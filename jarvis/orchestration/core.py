"""Shared Jarvis orchestration state, tools, and approval handling."""

from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from ..brains import get_worker
from ..config import Config
from ..memory import Memory
from ..projects import find_project
from ..tasks import ConfirmRequest, TaskManager

JARVIS_SYSTEM = """You are Jarvis, {user}'s personal voice assistant on their Mac, \
in the spirit of Iron Man's JARVIS: capable, dry, loyal, brief.

HARD RULES
- You are an orchestrator ONLY. Never read, write, edit, or execute files or shell \
commands yourself. Use only the Jarvis control tools supplied to you.
- Any coding task, feature, fix, refactor, or file operation MUST be delegated \
with spawn_task.
- Tasks run concurrently. Use task_status to report progress and cancel_task to stop one.
- Default worker platform: {brain}. Use switch_brain when the user asks to change it.
- For spawn_task's project argument, pass the name the user said. If resolution returns \
NOT_FOUND or AMBIGUOUS, ask the user; never guess a path.
- Set auto_edits=true only when the user explicitly permits edits without confirmation.
- Use remember for durable facts and register_project for confirmed project mappings.

SECOND BRAIN
The knowledge vault is {vault}. Use brain_search first and brain_read only for promising notes.

SPEECH STYLE
Replies may be read aloud. Default to 1-3 short plain sentences. No markdown or code blocks.

MEMORY
{memory}

Registered projects: {projects}
"""

YES_WORDS = {"yes", "y", "yeah", "yep", "sure", "ok", "okay", "approve", "approved",
             "confirm", "go", "go ahead", "do it", "proceed", "allow"}
NO_WORDS = {"no", "n", "nope", "deny", "denied", "stop", "don't", "dont", "reject",
            "cancel that", "not now"}


@dataclass
class ToolSpec:
    name: str
    description: str
    properties: dict[str, dict]
    handler: Callable[[dict], Awaitable[str]]

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": self.properties,
            "required": list(self.properties),
            "additionalProperties": False,
        }


class OrchestratorCore:
    """Own shared task state and execute provider-independent Jarvis tools."""

    def __init__(self, cfg: Config, io):
        self.cfg = cfg
        self.io = io
        self.memory = Memory()
        self.confirm_queue: asyncio.Queue = asyncio.Queue()
        self.pending: ConfirmRequest | None = None
        self.tasks = TaskManager(self.memory, self.confirm_queue, announce=io.notify)
        self.tools = self._build_tools()

    @property
    def system_prompt(self) -> str:
        return JARVIS_SYSTEM.format(
            user=self.cfg.user_name,
            brain=self.cfg.brain,
            vault=self.cfg.second_brain.vault,
            memory=self.memory.context_block(),
            projects=", ".join(self.cfg.projects) or "(none)",
        )

    async def execute_tool(self, name: str, args: dict) -> str:
        spec = next((item for item in self.tools if item.name == name), None)
        if spec is None:
            return f"Unknown Jarvis tool: {name}"
        try:
            return await spec.handler(args)
        except Exception as exc:
            return f"Jarvis tool {name} failed: {exc}"

    def _build_tools(self) -> list[ToolSpec]:
        text = {"type": "string"}
        boolean = {"type": "boolean"}

        async def spawn_task(args: dict) -> str:
            brain = str(args.get("brain") or "default").strip().lower()
            if brain in ("", "default"):
                brain = self.cfg.brain
            if brain not in ("claude", "codex"):
                return f"Unknown brain {brain!r}; use claude or codex."
            candidates = find_project(str(args.get("project", "")).strip(), self.cfg)
            if not candidates:
                return "PROJECT_NOT_FOUND: ask the user for the exact project folder."
            if len(candidates) > 1:
                listing = "\n".join(str(path) for path in candidates[:5])
                return f"PROJECT_AMBIGUOUS:\n{listing}\nAsk the user which folder to use."
            task = self.tasks.new_task(
                title=str(args.get("title", "task")),
                instructions=str(args.get("instructions", "")),
                project=candidates[0],
                brain=brain,
                auto_edits=bool(args.get("auto_edits", False)),
            )
            self.tasks.spawn(task, get_worker(brain, self.cfg))
            return f"Task {task.id} '{task.title}' started on {brain} in {task.project}."

        async def task_status(args: dict) -> str:
            task_id = str(args.get("task_id", "")).strip() or None
            return self.tasks.status_text(task_id)

        async def cancel_task(args: dict) -> str:
            task_id = str(args.get("task_id", "")).strip()
            return f"Task {task_id} cancelled." if self.tasks.cancel(task_id) else f"No running task {task_id}."

        async def switch_brain(args: dict) -> str:
            brain = str(args.get("brain", "")).strip().lower()
            self.cfg.set_brain(brain)
            return f"Default worker platform is now {brain}."

        async def remember(args: dict) -> str:
            path = self.memory.remember(str(args["name"]), str(args["content"]))
            return f"Remembered in {path.name}."

        async def register_project(args: dict) -> str:
            path = Path(str(args["path"])).expanduser()
            if not path.is_dir():
                return f"{path} is not a directory."
            self.cfg.register_project(str(args["name"]), path)
            return f"Registered '{args['name']}' -> {path}."

        async def brain_search(args: dict) -> str:
            vault = self.cfg.second_brain.vault
            if not vault.is_dir():
                return "Second brain vault doesn't exist yet; run `jarvis sync`."
            query = str(args.get("query", "")).strip()
            try:
                out = subprocess.run(
                    ["rg", "-i", "-n", "--max-count", "3", query],
                    cwd=vault, capture_output=True, text=True, timeout=15,
                )
                hits = out.stdout.strip()
            except FileNotFoundError:
                hits = "\n".join(
                    f"{path.relative_to(vault)}: {line.strip()}"
                    for path in vault.rglob("*")
                    if path.is_file() and path.suffix in (".md", ".txt")
                    for line in path.read_text(errors="ignore").splitlines()
                    if query.lower() in line.lower()
                )[:3000]
            return hits[:3000] if hits else f"No notes match '{query}'."

        async def brain_read(args: dict) -> str:
            vault = self.cfg.second_brain.vault
            target = (vault / str(args.get("path", ""))).resolve()
            if not target.is_relative_to(vault.resolve()) or not target.is_file():
                return "Not a valid note path inside the vault."
            return target.read_text()[:8000]

        return [
            ToolSpec("spawn_task", "Delegate a task to a background Claude or Codex worker.",
                     {"title": text, "instructions": text, "project": text,
                      "brain": text, "auto_edits": boolean}, spawn_task),
            ToolSpec("task_status", "Get background task status; task_id may be empty.",
                     {"task_id": text}, task_status),
            ToolSpec("cancel_task", "Cancel a running background task.",
                     {"task_id": text}, cancel_task),
            ToolSpec("switch_brain", "Switch the default worker to claude or codex.",
                     {"brain": text}, switch_brain),
            ToolSpec("remember", "Save a durable fact to Jarvis memory.",
                     {"name": text, "content": text}, remember),
            ToolSpec("register_project", "Pin a project name to a folder path.",
                     {"name": text, "path": text}, register_project),
            ToolSpec("brain_search", "Search the second-brain knowledge vault.",
                     {"query": text}, brain_search),
            ToolSpec("brain_read", "Read a note using its vault-relative path.",
                     {"path": text}, brain_read),
        ]

    def resolve_confirmation(self, user: str) -> bool:
        normalized = user.lower().strip(" .!?,")
        first = normalized.split()[0] if normalized.split() else ""
        request = self.pending
        if request is None:
            return False
        if normalized in YES_WORDS or first in YES_WORDS:
            answer = True
        elif normalized in NO_WORDS or first in NO_WORDS:
            answer = False
        else:
            return False
        self.pending = None
        if not request.future.done():
            request.future.set_result(answer)
        self.io.say(f"{'Approved' if answer else 'Denied'} for task {request.task_id}.")
        return True

    async def confirm_watcher(self) -> None:
        while True:
            request: ConfirmRequest = await self.confirm_queue.get()
            self.pending = request
            self.io.notify(
                f"Task {request.task_id} '{request.task_title}' wants to "
                f"{request.description}. Approve? (yes/no)"
            )
            try:
                await asyncio.shield(request.future)
            except (asyncio.CancelledError, Exception):
                pass
            finally:
                if self.pending is request:
                    self.pending = None

    def warn_running_tasks(self) -> None:
        if self.tasks.running:
            names = ", ".join(task.id for task in self.tasks.running)
            self.io.notify(f"Warning: tasks still running ({names}); they stop when Jarvis exits.")

