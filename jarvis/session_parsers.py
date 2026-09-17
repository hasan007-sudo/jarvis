"""Session transcript parsing and text helpers for the second brain."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class SessionDoc:
    source: str  # claude | codex | omp
    sid: str
    path: Path
    cwd: str
    project: str
    date: str  # YYYY-MM-DD
    turns: list[tuple[str, str]] = field(default_factory=list)


def parse_claude(path: Path) -> SessionDoc | None:
    turns, cwd, first_ts = [], "", None
    for obj in _jsonl(path):
        cwd = obj.get("cwd") or cwd
        first_ts = first_ts or obj.get("timestamp")
        if obj.get("type") not in ("user", "assistant"):
            continue
        message = obj.get("message") or {}
        role, content = message.get("role"), message.get("content")
        for text in _texts(content):
            turns.append((role, text))
    if not turns:
        return None
    return SessionDoc(
        source="claude",
        sid=path.stem,
        path=path,
        cwd=cwd,
        project=_project_name(cwd),
        date=_date(first_ts, path),
        turns=turns,
    )


def parse_codex(path: Path) -> SessionDoc | None:
    turns, cwd, sid, first_ts = [], "", path.stem.split("-")[-1], None
    for obj in _jsonl(path):
        first_ts = first_ts or obj.get("timestamp")
        payload = obj.get("payload") or {}
        if obj.get("type") == "session_meta":
            cwd = payload.get("cwd") or cwd
            sid = payload.get("id") or sid
        if payload.get("type") == "message" and payload.get("role") in ("user", "assistant"):
            for text in _texts(payload.get("content")):
                turns.append((payload["role"], text))
    if not turns:
        return None
    return SessionDoc(
        source="codex",
        sid=str(sid),
        path=path,
        cwd=cwd,
        project=_project_name(cwd),
        date=_date(first_ts, path),
        turns=turns,
    )


def parse_omp(path: Path) -> SessionDoc | None:
    turns, cwd, sid, first_ts = [], "", path.stem.split("_")[-1], None
    for obj in _jsonl(path):
        first_ts = first_ts or obj.get("timestamp")
        if obj.get("type") == "session":
            cwd = obj.get("cwd") or cwd
            sid = obj.get("id") or sid
        elif obj.get("type") == "message":
            message = obj.get("message") or {}
            role, content = message.get("role"), message.get("content")
            if role in ("user", "assistant"):
                for text in _texts(content):
                    turns.append((role, text))
    if not turns:
        return None
    return SessionDoc(
        source="omp",
        sid=str(sid),
        path=path,
        cwd=cwd,
        project=_project_name(cwd),
        date=_date(first_ts, path),
        turns=turns,
    )

def _fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.name.encode())
    digest.update(b"\0")
    with path.open("rb") as transcript:
        for chunk in iter(lambda: transcript.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _jsonl(path: Path):
    try:
        with path.open() as f:
            for line in f:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


def _texts(content) -> list[str]:
    """Extract human-meaningful text blocks; drop tool noise and injected XML."""
    raw: list[str] = []
    if isinstance(content, str):
        raw = [content]
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") in (
                "text", "input_text", "output_text"
            ):
                raw.append(block.get("text", ""))
    out = []
    for t in raw:
        t = t.strip()
        if t and not t.startswith("<") and not t.startswith("Caveat:"):
            out.append(t)
    return out


def _project_name(cwd: str) -> str:
    if not cwd:
        return "unknown"
    name = Path(cwd).name or "home"
    return name if name != Path.home().name else "home"


def _date(ts: str | None, path: Path) -> str:
    if ts:
        match = re.match(r"(\d{4}-\d{2}-\d{2})", ts)
        if match:
            return match.group(1)
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")


def _condense(turns: list[tuple[str, str]], per_msg: int = 1200, cap: int = 60000) -> str:
    parts, total = [], 0
    for role, text in turns:
        if len(text) > per_msg:
            text = text[:per_msg] + " …[truncated]"
        line = f"{role.upper()}: {text}"
        if total + len(line) > cap:
            parts.append("…[transcript truncated]")
            break
        parts.append(line)
        total += len(line)
    return "\n\n".join(parts)


def _parse_distilled(raw: str) -> dict | None:
    title = re.search(r"^TITLE:\s*(.+)$", raw, re.M)
    tldr = re.search(r"^TLDR:\s*(.+)$", raw, re.M)
    tags = re.search(r"^TAGS:\s*(.+)$", raw, re.M)
    body_at = raw.find("## Decisions")
    if not title or body_at == -1:
        return None
    return {
        "title": title.group(1).strip(),
        "tldr": tldr.group(1).strip() if tldr else "",
        "tags": [t.strip() for t in tags.group(1).split(",")][:4] if tags else [],
        "body": raw[body_at:].strip(),
    }


def _first_heading(text: str) -> str:
    match = re.search(r"^# (.+)$", text, re.M)
    return match.group(1) if match else "untitled"


def _section(text: str, name: str) -> list[str]:
    match = re.search(rf"## {name}\n(.*?)(?=\n## |\nProject: |\Z)", text, re.S)
    if not match:
        return []
    bullets = [b.strip()[2:].strip() for b in match.group(1).strip().splitlines()
               if b.strip().startswith("- ")]
    return [b for b in bullets if b and b.lower() != "none"]
