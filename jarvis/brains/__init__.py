"""Worker brains. Each runs one task end-to-end in a separate agent session."""

from __future__ import annotations

from dataclasses import dataclass

from ..config import Config


@dataclass
class TaskResult:
    ok: bool
    summary: str
    session_id: str | None = None


def get_worker(brain: str, cfg: Config):
    if brain == "claude":
        from .claude import ClaudeWorker

        return ClaudeWorker(cfg)
    if brain == "codex":
        from .codex import CodexWorker

        return CodexWorker(cfg)
    raise ValueError(f"Unknown brain: {brain!r}")
