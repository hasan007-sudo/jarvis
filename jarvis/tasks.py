"""Task manager: runs worker agents concurrently and routes confirmations.

The orchestrator never executes anything itself — every task becomes a worker
agent (Claude or Codex) running as an asyncio task. Confirmation requests from
workers are queued; the orchestrator surfaces them to the user (voice/text)
and resolves the future, unblocking the worker.
"""

from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from .memory import Memory


@dataclass
class ConfirmRequest:
    task_id: str
    task_title: str
    description: str
    future: asyncio.Future


@dataclass
class Task:
    id: str
    title: str
    instructions: str
    project: Path
    brain: str
    auto_edits: bool = False
    status: str = "queued"  # queued | running | done | failed | cancelled
    events: list[str] = field(default_factory=list)
    result: str = ""
    session_id: str | None = None
    created_at: datetime = field(default_factory=datetime.now)

    def log(self, kind: str, text: str) -> None:
        line = f"[{kind}] {text.strip()}"
        self.events.append(line[:300])
        if len(self.events) > 200:
            del self.events[: len(self.events) - 200]


class TaskManager:
    def __init__(
        self,
        memory: Memory,
        confirm_queue: asyncio.Queue,
        announce: Callable[[str], None],
    ):
        self.memory = memory
        self.confirm_queue = confirm_queue
        self.announce = announce
        self.tasks: dict[str, Task] = {}
        self._handles: dict[str, asyncio.Task] = {}
        self._ids = itertools.count(1)

    def new_task(
        self, title: str, instructions: str, project: Path, brain: str, auto_edits: bool
    ) -> Task:
        task = Task(
            id=f"t{next(self._ids)}",
            title=title,
            instructions=instructions,
            project=project,
            brain=brain,
            auto_edits=auto_edits,
        )
        self.tasks[task.id] = task
        return task

    def spawn(self, task: Task, worker) -> None:
        task.status = "running"
        self.memory.record_task(task.id, task.title, str(task.project), task.brain)
        self._handles[task.id] = asyncio.create_task(self._run(task, worker))

    async def _run(self, task: Task, worker) -> None:
        async def gate(description: str) -> bool:
            future: asyncio.Future = asyncio.get_running_loop().create_future()
            await self.confirm_queue.put(
                ConfirmRequest(task.id, task.title, description, future)
            )
            return await future

        def on_event(kind: str, text: str) -> None:
            task.log(kind, text)

        try:
            result = await worker.run(task, on_event, gate)
            task.status = "done" if result.ok else "failed"
            task.result = result.summary
            task.session_id = result.session_id
        except asyncio.CancelledError:
            task.status = "cancelled"
            task.result = "Cancelled by user."
            raise
        except Exception as exc:  # worker crashed — report, don't kill the daemon
            task.status = "failed"
            task.result = f"Worker error: {exc}"
        finally:
            self.memory.finish_task(task.id, task.status, task.result)
            self.announce(f"Task {task.id} '{task.title}' {task.status}. {task.result[:200]}")

    def cancel(self, task_id: str) -> bool:
        handle = self._handles.get(task_id)
        if handle and not handle.done():
            handle.cancel()
            return True
        return False

    def status_text(self, task_id: str | None = None) -> str:
        if task_id and task_id in self.tasks:
            t = self.tasks[task_id]
            recent = "\n".join(t.events[-10:]) or "(no events yet)"
            return (
                f"{t.id} '{t.title}' — {t.status} (brain={t.brain}, "
                f"project={t.project}, auto_edits={t.auto_edits})\n"
                f"Recent activity:\n{recent}\n"
                f"Result: {t.result or '(pending)'}"
            )
        if not self.tasks:
            return "No tasks this session."
        return "\n".join(
            f"{t.id} [{t.status}] {t.title} ({t.brain}, {t.project.name})"
            for t in self.tasks.values()
        )

    @property
    def running(self) -> list[Task]:
        return [t for t in self.tasks.values() if t.status == "running"]
