"""The Jarvis orchestrator — a long-lived, delegate-only conversation session.

Jarvis itself can only talk, look things up (WebSearch/WebFetch), and call its
own control tools (spawn_task, task_status, ...). It has no file or shell
access: every implementation task becomes a separate worker agent. A
can_use_tool allowlist enforces this in code, not just in the prompt.

Call flow:
    Orchestrator.run()
      └─ ClaudeSDKClient (persistent session, resumed across restarts)
           ├─ _loop()                    # user turns in/out
           │    └─ mcp__jarvis__spawn_task
           │         └─ TaskManager.spawn() ─▶ worker.run() (background)
           │              └─ gate() ─▶ confirm_queue ─▶ user says yes/no
           └─ _confirm_watcher()         # surfaces approval requests
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    TextBlock,
    create_sdk_mcp_server,
    tool,
)

from .brains import get_worker
from .config import JARVIS_HOME, Config
from .memory import Memory
from .projects import find_project
from .tasks import ConfirmRequest, TaskManager

JARVIS_SYSTEM = """You are Jarvis, {user}'s personal voice assistant on their Mac, \
in the spirit of Iron Man's JARVIS: capable, dry, loyal, brief.

HARD RULES
- You are an orchestrator ONLY. You never read, write, edit, or execute anything \
yourself — you have no file or shell tools.
- Any coding task, feature, fix, refactor, or file operation MUST be delegated \
with spawn_task. Workers are full agents (Claude Code with {user}'s skills, \
agents and CLAUDE.md, or Codex) running in the background in the project folder.
- Answer questions from knowledge; use WebSearch/WebFetch for lookups.
- Tasks run concurrently — after spawning one, you are free for the next request. \
Use task_status to report progress, cancel_task to stop one.
- Default worker platform: {brain}. When the user says "switch to codex" or \
"use claude", call switch_brain.
- For spawn_task's project argument, pass whatever name the user said; the \
resolver searches under {home}. If it returns NOT_FOUND or AMBIGUOUS, relay the \
situation and ASK the user — never guess a path.
- auto_edits=true lets a worker change files in its project without per-action \
approval. Set it when the user clearly grants it ("just do it", "auto-approve", \
"don't ask"); otherwise leave false and actions will be confirmed one by one.
- Use remember for durable facts {user} shares (preferences, decisions, \
project context). Use register_project when a project resolution is confirmed \
so it sticks.

SECOND BRAIN
A knowledge vault distilled from all past Claude/Codex sessions lives at \
{vault}. To recall past work, decisions, or project context: brain_search a \
keyword first, then brain_read the promising notes. wiki/projects/<name>.md \
is the rollup for a project; INDEX.md lists every note. Fetch progressively — \
search, then read only what you need.

SPEECH STYLE
Your replies may be read aloud. Default to 1-3 short, plain sentences. \
No markdown, no bullet lists, no code blocks in replies.

MEMORY
{memory}

Registered projects: {projects}
"""

YES_WORDS = {"yes", "y", "yeah", "yep", "sure", "ok", "okay", "approve", "approved",
             "confirm", "go", "go ahead", "do it", "proceed", "allow"}
NO_WORDS = {"no", "n", "nope", "deny", "denied", "stop", "don't", "dont", "reject",
            "cancel that", "not now"}


def _text_result(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


class Orchestrator:
    def __init__(self, cfg: Config, io):
        self.cfg = cfg
        self.io = io
        self.memory = Memory()
        self.confirm_queue: asyncio.Queue = asyncio.Queue()
        self.pending: ConfirmRequest | None = None
        self.tasks = TaskManager(self.memory, self.confirm_queue, announce=io.notify)
        self._tool_defs = self._build_tools()
        self._allowed = {"WebSearch", "WebFetch", "TodoWrite"} | {
            f"mcp__jarvis__{t.name}" for t in self._tool_defs
        }

    # ------------------------------------------------------------- tools --
    def _build_tools(self) -> list:
        orch = self

        @tool(
            "spawn_task",
            "Delegate a coding/implementation task to a background worker agent. "
            "brain: 'claude', 'codex', or 'default'. project: the name the user "
            "said, or a full path. auto_edits: true only if the user granted it.",
            {"title": str, "instructions": str, "project": str, "brain": str,
             "auto_edits": bool},
        )
        async def spawn_task(args: dict) -> dict:
            brain = (args.get("brain") or "default").strip().lower()
            if brain in ("", "default"):
                brain = orch.cfg.brain
            if brain not in ("claude", "codex"):
                return _text_result(f"Unknown brain {brain!r}; use claude or codex.")

            query = str(args.get("project", "")).strip()
            candidates = find_project(query, orch.cfg)
            if not candidates:
                return _text_result(
                    f"PROJECT_NOT_FOUND: no folder matching '{query}' under "
                    f"{Path.home()}. Ask the user for the exact folder name or "
                    "full path — do not guess."
                )
            if len(candidates) > 1:
                listing = "\n".join(str(c) for c in candidates[:5])
                return _text_result(
                    f"PROJECT_AMBIGUOUS: several folders match '{query}':\n"
                    f"{listing}\nAsk the user which one, then call spawn_task "
                    "again with the full path."
                )

            project = candidates[0]
            task = orch.tasks.new_task(
                title=str(args.get("title", "task")),
                instructions=str(args.get("instructions", "")),
                project=project,
                brain=brain,
                auto_edits=bool(args.get("auto_edits", False)),
            )
            orch.tasks.spawn(task, get_worker(brain, orch.cfg))
            return _text_result(
                f"Task {task.id} '{task.title}' started on {brain} in {project}. "
                "It runs in the background; the user will hear when it finishes."
            )

        @tool(
            "task_status",
            "Status of background tasks. Pass a task id like 't1', or '' for all.",
            {"task_id": str},
        )
        async def task_status(args: dict) -> dict:
            tid = str(args.get("task_id", "")).strip() or None
            return _text_result(orch.tasks.status_text(tid))

        @tool("cancel_task", "Cancel a running background task by id.", {"task_id": str})
        async def cancel_task(args: dict) -> dict:
            tid = str(args.get("task_id", "")).strip()
            ok = orch.tasks.cancel(tid)
            return _text_result(f"Task {tid} cancelled." if ok else f"No running task {tid}.")

        @tool(
            "switch_brain",
            "Switch the default worker platform. brain: 'claude' or 'codex'. "
            "Persists across restarts.",
            {"brain": str},
        )
        async def switch_brain(args: dict) -> dict:
            brain = str(args.get("brain", "")).strip().lower()
            try:
                orch.cfg.set_brain(brain)
            except ValueError as exc:
                return _text_result(str(exc))
            return _text_result(f"Default worker platform is now {brain}.")

        @tool(
            "remember",
            "Save a durable fact to long-term memory (survives restarts). "
            "name: short kebab-case title. content: the fact in 1-3 sentences.",
            {"name": str, "content": str},
        )
        async def remember(args: dict) -> dict:
            path = orch.memory.remember(str(args["name"]), str(args["content"]))
            return _text_result(f"Remembered in {path.name}.")

        @tool(
            "register_project",
            "Pin a project name to a folder path so future resolution is instant.",
            {"name": str, "path": str},
        )
        async def register_project(args: dict) -> dict:
            path = Path(str(args["path"])).expanduser()
            if not path.is_dir():
                return _text_result(f"{path} is not a directory.")
            orch.cfg.register_project(str(args["name"]), path)
            return _text_result(f"Registered '{args['name']}' -> {path}.")

        @tool(
            "brain_search",
            "Search the second-brain vault (distilled past sessions, decisions, "
            "project knowledge). Returns matching notes with snippets.",
            {"query": str},
        )
        async def brain_search(args: dict) -> dict:
            import subprocess

            vault = orch.cfg.second_brain.vault
            if not vault.is_dir():
                return _text_result("Second brain vault doesn't exist yet — run `jarvis sync`.")
            query = str(args.get("query", "")).strip()
            try:
                # Any folder works as a second brain: search every text file.
                out = subprocess.run(
                    ["rg", "-i", "-n", "--max-count", "3", query],
                    cwd=vault, capture_output=True, text=True, timeout=15,
                )
                hits = out.stdout.strip()
            except FileNotFoundError:
                hits = "\n".join(
                    f"{f.relative_to(vault)}: {line.strip()}"
                    for f in vault.rglob("*")
                    if f.is_file() and f.suffix in (".md", ".txt")
                    for line in f.read_text(errors="ignore").splitlines()
                    if query.lower() in line.lower()
                )[:3000]
            if not hits:
                return _text_result(f"No notes match '{query}'. Try INDEX.md via brain_read.")
            return _text_result(hits[:3000])

        @tool(
            "brain_read",
            "Read one note from the second-brain vault by relative path "
            "(e.g. 'INDEX.md', 'wiki/projects/jarvis.md', a path from brain_search).",
            {"path": str},
        )
        async def brain_read(args: dict) -> dict:
            vault = orch.cfg.second_brain.vault
            target = (vault / str(args.get("path", ""))).resolve()
            if not target.is_relative_to(vault.resolve()) or not target.is_file():
                return _text_result("Not a valid note path inside the vault.")
            return _text_result(target.read_text()[:8000])

        return [spawn_task, task_status, cancel_task, switch_brain, remember,
                register_project, brain_search, brain_read]

    async def _gate(self, tool_name: str, tool_input: dict, context):
        if tool_name in self._allowed:
            return PermissionResultAllow()
        return PermissionResultDeny(
            message="Jarvis is orchestrator-only. Delegate this via spawn_task."
        )

    def _options(self, resume: str | None) -> ClaudeAgentOptions:
        server = create_sdk_mcp_server(name="jarvis", tools=self._tool_defs)
        return ClaudeAgentOptions(
            # Pin the session store to one directory so `resume` works no
            # matter where jarvis was launched from.
            cwd=str(JARVIS_HOME),
            system_prompt=JARVIS_SYSTEM.format(
                user=self.cfg.user_name,
                brain=self.cfg.brain,
                home=Path.home(),
                vault=self.cfg.second_brain.vault,
                memory=self.memory.context_block(),
                projects=", ".join(self.cfg.projects) or "(none)",
            ),
            mcp_servers={"jarvis": server},
            allowed_tools=sorted(self._allowed),
            disallowed_tools=[
                "Bash", "Write", "Edit", "NotebookEdit",
                "Read", "Glob", "Grep", "Task", "Skill",
            ],
            setting_sources=[],  # Jarvis persona stays clean; workers load user config
            permission_mode="default",
            can_use_tool=self._gate,
            resume=resume or None,
        )

    # -------------------------------------------------------------- loop --
    async def run(self) -> None:
        watcher = asyncio.create_task(self._confirm_watcher())
        resume = self.memory.get("orchestrator_session")
        try:
            try:
                async with ClaudeSDKClient(self._options(resume)) as client:
                    await self._loop(client)
            except Exception:
                if not resume:
                    raise
                # Stale/invalid stored session — start fresh once.
                self.memory.set("orchestrator_session", "")
                async with ClaudeSDKClient(self._options(None)) as client:
                    await self._loop(client)
        finally:
            watcher.cancel()

    async def _loop(self, client: ClaudeSDKClient) -> None:
        brain = self.cfg.brain
        self.io.say(f"Jarvis online. Default worker: {brain}. What do you need?")
        turns = 0
        while True:
            user = (await self.io.ask()).strip()
            if not user:
                continue
            if user.lower().strip(" .!") in {"exit", "quit", "bye", "goodbye", "shut down"}:
                await self._wrap_up(client, turns)
                return
            if self.pending is not None and self._resolve_confirm(user):
                continue
            turns += 1
            await client.query(user)
            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock) and block.text.strip():
                            self.io.say(block.text.strip())
                elif isinstance(msg, ResultMessage) and msg.session_id:
                    self.memory.set("orchestrator_session", msg.session_id)
            if self.pending is not None:
                self.io.notify(
                    f"Still waiting on approval: {self.pending.description} — yes or no?"
                )

    def _resolve_confirm(self, user: str) -> bool:
        normalized = user.lower().strip(" .!?,")
        first = normalized.split()[0] if normalized.split() else ""
        req = self.pending
        assert req is not None
        if normalized in YES_WORDS or first in YES_WORDS:
            answer = True
        elif normalized in NO_WORDS or first in NO_WORDS:
            answer = False
        else:
            return False  # not an answer — treat as normal conversation
        self.pending = None
        if not req.future.done():
            req.future.set_result(answer)
        self.io.say(
            f"{'Approved' if answer else 'Denied'} for task {req.task_id}."
        )
        return True

    async def _confirm_watcher(self) -> None:
        while True:
            req: ConfirmRequest = await self.confirm_queue.get()
            self.pending = req
            self.io.notify(
                f"Task {req.task_id} '{req.task_title}' wants to "
                f"{req.description}. Approve? (yes/no)"
            )
            try:
                await asyncio.shield(req.future)
            except (asyncio.CancelledError, Exception):
                pass
            finally:
                if self.pending is req:
                    self.pending = None

    async def _wrap_up(self, client: ClaudeSDKClient, turns: int) -> None:
        if self.tasks.running:
            names = ", ".join(t.id for t in self.tasks.running)
            self.io.notify(f"Warning: tasks still running ({names}); they stop when Jarvis exits.")
        if turns:
            await client.query(
                "Session ending. In one or two plain sentences, summarize this "
                "conversation and any open threads for your future self. Reply "
                "with only the summary."
            )
            summary = ""
            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            summary = block.text.strip()
            if summary:
                self.memory.save_summary(summary)
        self.io.say("Goodbye.")
