"""Codex worker: runs a task via `codex exec --json` in the project directory.

Codex's exec mode is non-interactive — it cannot ask per-action approval the
way the Claude worker can. Containment comes from Codex's own sandbox
(workspace-write, scoped to the project). If the task wasn't granted
auto-edits up front, Jarvis asks the user once before starting.

The JSONL event parser is deliberately tolerant: Codex event names have
drifted across releases, so we extract text/commands from several shapes and
fall back to raw lines.
"""

from __future__ import annotations

import asyncio
import json

from ..config import Config
from . import TaskResult


class CodexWorker:
    def __init__(self, cfg: Config):
        self.bin = cfg.codex_bin
        self.options = cfg.models.brain.codex

    async def run(self, task, on_event, gate) -> TaskResult:
        if not task.auto_edits:
            approved = await gate(
                f"start a Codex agent with write access to {task.project} "
                "(Codex self-approves actions inside its sandbox)"
            )
            if not approved:
                return TaskResult(ok=False, summary="User declined Codex execution.")

        cmd = [
            self.bin,
            "exec",
            "--json",
            "--model",
            self.options.model,
            "--cd",
            str(task.project),
            "--sandbox",
            self.options.sandbox,
        ]
        if self.options.ephemeral:
            cmd.append("--ephemeral")
        if self.options.skip_git_repo_check:
            cmd.append("--skip-git-repo-check")
        if self.options.ignore_user_config:
            cmd.append("--ignore-user-config")
        if self.options.ignore_rules:
            cmd.append("--ignore-rules")
        cmd.append(task.instructions)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            return TaskResult(
                ok=False,
                summary=f"Codex binary not found at {self.bin!r}. "
                "Fix the install or set codex_bin in ~/.jarvis/config.yaml.",
            )

        last_text = ""
        thread_id: str | None = None
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                on_event("text", line)
                last_text = line
                continue
            text, kind, tid = _extract(event)
            if tid:
                thread_id = tid
            if text:
                on_event(kind, text)
                if kind == "text":
                    last_text = text

        rc = await proc.wait()
        stderr = (await proc.stderr.read()).decode(errors="replace") if proc.stderr else ""
        if rc != 0:
            detail = stderr.strip().splitlines()[-1] if stderr.strip() else f"exit code {rc}"
            return TaskResult(ok=False, summary=f"Codex failed: {detail}", session_id=thread_id)
        return TaskResult(
            ok=True,
            summary=last_text or "Codex finished (no final message).",
            session_id=thread_id,
        )


def _extract(event: dict) -> tuple[str, str, str | None]:
    """Pull (text, kind, thread_id) out of a Codex JSONL event, tolerantly."""
    etype = str(event.get("type", ""))
    tid = event.get("thread_id") or event.get("session_id")

    item = event.get("item") or {}
    if isinstance(item, dict) and item:
        itype = item.get("type", "")
        if itype == "agent_message" and item.get("text"):
            return str(item["text"]), "text", tid
        if itype == "command_execution" and item.get("command"):
            return f"$ {item['command']}", "tool", tid
        if itype == "file_change":
            return "file change", "tool", tid
        if itype == "error" and item.get("message"):
            return f"codex warning: {item['message']}", "status", tid

    msg = event.get("msg") or {}
    if isinstance(msg, dict):
        if msg.get("type") == "agent_message" and msg.get("message"):
            return str(msg["message"]), "text", tid
        if msg.get("type") in ("exec_command_begin",) and msg.get("command"):
            command = msg["command"]
            if isinstance(command, list):
                command = " ".join(map(str, command))
            return f"$ {command}", "tool", tid

    if etype in ("thread.started", "session.created"):
        return "", "status", tid
    return "", "status", tid
