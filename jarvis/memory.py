"""Persistent memory: fact files under ~/.jarvis/memory plus a SQLite log.

Survives restarts. Facts are markdown files indexed by MEMORY.md (injected
into the orchestrator's system prompt each session). SQLite stores task
history, conversation summaries, and saved session IDs.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .config import JARVIS_HOME

MEMORY_DIR = JARVIS_HOME / "memory"
DB_PATH = JARVIS_HOME / "jarvis.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    title TEXT, project TEXT, brain TEXT, status TEXT, summary TEXT,
    created_at TEXT, finished_at TEXT
);
CREATE TABLE IF NOT EXISTS summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, text TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Memory:
    def __init__(self) -> None:
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        self.index_path = MEMORY_DIR / "MEMORY.md"
        if not self.index_path.exists():
            self.index_path.write_text("# Jarvis Memory Index\n")
        self.db = sqlite3.connect(DB_PATH)
        self.db.executescript(SCHEMA)
        self.db.commit()

    # -- key/value (session ids, current brain, etc.) --
    def get(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO kv(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.db.commit()

    # -- durable facts --
    def remember(self, name: str, content: str) -> Path:
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "fact"
        path = MEMORY_DIR / f"{slug}.md"
        is_new = not path.exists()
        path.write_text(content.rstrip() + "\n")
        if is_new:
            first_line = content.strip().splitlines()[0][:100] if content.strip() else name
            with self.index_path.open("a") as f:
                f.write(f"- [{name}]({slug}.md) — {first_line}\n")
        return path

    def memory_index(self) -> str:
        return self.index_path.read_text()

    # -- conversation summaries --
    def save_summary(self, text: str) -> None:
        self.db.execute(
            "INSERT INTO summaries(created_at, text) VALUES(?, ?)", (_now(), text)
        )
        self.db.commit()

    def last_summary(self) -> str | None:
        row = self.db.execute(
            "SELECT text FROM summaries ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else None

    # -- task history --
    def record_task(self, task_id: str, title: str, project: str, brain: str) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO tasks(id, title, project, brain, status, summary, created_at, finished_at) "
            "VALUES(?, ?, ?, ?, 'running', '', ?, NULL)",
            (task_id, title, project, brain, _now()),
        )
        self.db.commit()

    def finish_task(self, task_id: str, status: str, summary: str) -> None:
        self.db.execute(
            "UPDATE tasks SET status=?, summary=?, finished_at=? WHERE id=?",
            (status, summary[:2000], _now(), task_id),
        )
        self.db.commit()

    def recent_tasks(self, limit: int = 5) -> list[tuple]:
        return self.db.execute(
            "SELECT id, title, project, brain, status, summary FROM tasks "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()

    # -- context injected into the orchestrator's system prompt --
    def context_block(self) -> str:
        parts = ["## Long-term memory index", self.memory_index().strip()]
        if summary := self.last_summary():
            parts += ["## Last conversation summary", summary.strip()]
        if tasks := self.recent_tasks():
            lines = [
                f"- {tid} [{status}] {title} ({project}, {brain})"
                for tid, title, project, brain, status, _ in tasks
            ]
            parts += ["## Recent tasks", "\n".join(lines)]
        return "\n\n".join(parts)
