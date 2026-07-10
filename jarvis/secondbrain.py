"""Second brain: an Obsidian vault distilled from Claude + Codex session logs.

Design (Karpathy LLM-wiki + OpenClaw memory pattern):
- Raw transcripts stay immutable in ~/.claude/projects and ~/.codex/sessions.
- Each session is distilled ONCE (via `claude -p`, haiku) into a permanent
  note: TL;DR, decisions, problems & solutions, questions, follow-ups.
- Mechanical compilation layers on top: per-project wiki pages, daily
  digests, and INDEX.md for progressive disclosure by agents.
- Sync is incremental (state in .brain/state.json), batched, and commits
  locally to git. It NEVER pushes — the user controls GitHub.

Call flow:
    sync()
      ├─ ensure_vault()          # scaffold + git init, idempotent
      ├─ discover()              # new session files vs state.json
      ├─ parse_claude/_codex()   # transcript -> (role, text) turns
      ├─ distill()               # claude -p haiku -> markdown note
      └─ compile_()              # project pages, dailies, INDEX, git commit
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import Config

CLAUDE_PROJECTS = Path.home() / ".claude/projects"
CODEX_SESSIONS = Path.home() / ".codex/sessions"

DISTILL_PROMPT = """You are distilling a coding-assistant session transcript into a permanent \
note for a personal knowledge vault (a "second brain"). It will be read later by the user \
and his assistant to recall context, so be specific: names, paths, versions, numbers.

Output EXACTLY this format, plain text, no preamble:
TITLE: <concise 4-8 word title>
TAGS: <2-4 lowercase comma-separated tags>
TLDR: <1-2 sentences: what was done and the outcome>

## Decisions
- <each technical/architectural decision made, and WHY; write '- none' if none>

## Problems & Solutions
- <each specific problem hit and how it was (or must be) solved; '- none' if none>

## Questions Asked
- <notable questions the user asked — his interests and unknowns; '- none' if none>

## Follow-ups
- <open threads, unfinished work, things to revisit; '- none' if none>

Omit pleasantries and routine tool chatter. The transcript follows."""

HOME_MD = """# Second Brain

Distilled knowledge from every Claude Code and Codex session on this machine.
Maintained automatically by `jarvis sync` (raw transcripts stay in
`~/.claude/projects` and `~/.codex/sessions`; only insights live here).

- [[INDEX]] — flat index of every note (agents start here)
- `wiki/projects/` — one rollup page per project: decisions, open threads, sessions
- `wiki/decisions/`, `wiki/topics/` — curated pages, filed by Jarvis or by hand
- `sessions/` — one distilled note per session
- `daily/` — digest per day, OpenClaw-style
"""


@dataclass
class SessionDoc:
    source: str  # claude | codex
    sid: str
    path: Path
    cwd: str
    project: str
    date: str  # YYYY-MM-DD
    turns: list[tuple[str, str]] = field(default_factory=list)


# ---------------------------------------------------------------- vault --
class SecondBrain:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.vault = cfg.second_brain.vault
        self.state_path = self.vault / ".brain/state.json"

    def ensure_vault(self) -> None:
        for sub in ("sessions", "daily", "wiki/projects", "wiki/decisions",
                    "wiki/topics", ".brain"):
            (self.vault / sub).mkdir(parents=True, exist_ok=True)
        home = self.vault / "HOME.md"
        if not home.exists():
            home.write_text(HOME_MD)
        gitignore = self.vault / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text(".brain/\n.obsidian/workspace*\n.DS_Store\n")
        if not (self.vault / ".git").exists():
            subprocess.run(["git", "init"], cwd=self.vault, capture_output=True)

    def _state(self) -> dict:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text())
        return {"processed": {}}

    def _save_state(self, state: dict) -> None:
        self.state_path.write_text(json.dumps(state, indent=1))

    # ------------------------------------------------------------- sync --
    def sync(self, max_sessions: int = 12, log=print) -> str:
        self.ensure_vault()
        state = self._state()
        candidates = self.discover(state)
        log(f"{len(candidates)} unprocessed session file(s); distilling up to {max_sessions}.")

        done, skipped, touched_projects, touched_dates = 0, 0, set(), set()
        for path, source in candidates:
            if done >= max_sessions:
                break
            key = str(path)
            doc = parse_claude(path) if source == "claude" else parse_codex(path)
            if doc is None or self._should_skip(doc):
                state["processed"][key] = {"status": "skipped"}
                skipped += 1
                continue
            log(f"  distilling {source}:{doc.project} {doc.date} ({doc.sid[:8]})")
            note = self.distill(doc)
            if note is None:
                state["processed"][key] = {"status": "failed"}
                skipped += 1
                continue
            rel = self._write_note(doc, note)
            state["processed"][key] = {"status": "done", "note": str(rel)}
            touched_projects.add(doc.project)
            touched_dates.add(doc.date)
            done += 1
            self._save_state(state)  # survive interruption mid-batch

        for project in touched_projects:
            self.compile_project(project)
        for date in touched_dates:
            self.compile_daily(date)
        if done:
            self.compile_index()
        self._save_state(state)
        if done and self.cfg.second_brain.commit:
            self._commit(done)

        remaining = len(candidates) - done - skipped
        return (
            f"Distilled {done} session(s), skipped {skipped} trivial/failed, "
            f"{max(remaining, 0)} still pending. Vault: {self.vault}"
        )

    def discover(self, state: dict) -> list[tuple[Path, str]]:
        found: list[tuple[float, Path, str]] = []
        def unprocessed(f: Path) -> bool:
            # "failed" stays eligible for retry on a later run; newest-first
            # ordering keeps permanent failures from crowding out new sessions.
            return state["processed"].get(str(f), {}).get("status") not in ("done", "skipped")

        if CLAUDE_PROJECTS.is_dir():
            for f in CLAUDE_PROJECTS.glob("*/*.jsonl"):
                if "second-brain" in f.parent.name:
                    continue  # distillation runs themselves
                if unprocessed(f):
                    found.append((f.stat().st_mtime, f, "claude"))
        if CODEX_SESSIONS.is_dir():
            for f in CODEX_SESSIONS.rglob("rollout-*.jsonl"):
                if unprocessed(f):
                    found.append((f.stat().st_mtime, f, "codex"))
        found.sort(reverse=True)  # newest first
        return [(f, source) for _, f, source in found]

    def _should_skip(self, doc: SessionDoc) -> bool:
        if "second-brain" in doc.cwd or "jarvis-test-sandbox" in doc.cwd:
            return True
        user_turns = [t for role, t in doc.turns if role == "user"]
        total = sum(len(t) for _, t in doc.turns)
        return len(user_turns) < 2 or total < 400

    # ---------------------------------------------------------- distill --
    def distill(self, doc: SessionDoc) -> dict | None:
        transcript = _condense(doc.turns)
        try:
            out = subprocess.run(
                ["claude", "-p", "--model", "haiku", DISTILL_PROMPT],
                input=transcript,
                capture_output=True,
                text=True,
                timeout=240,
                cwd=self.vault,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return None
        if out.returncode != 0 or not out.stdout.strip():
            return None
        return _parse_distilled(out.stdout)

    def _write_note(self, doc: SessionDoc, note: dict) -> Path:
        year, month, day = doc.date.split("-")
        directory = self.vault / "sessions" / year / month
        directory.mkdir(parents=True, exist_ok=True)
        fname = f"{day}-{doc.project}-{doc.source}-{doc.sid[:8]}.md"
        tags = ", ".join(["session"] + note["tags"])
        content = (
            f"---\n"
            f"project: {doc.project}\n"
            f"source: {doc.source}\n"
            f"date: {doc.date}\n"
            f"session: {doc.sid}\n"
            f"tags: [{tags}]\n"
            f"---\n\n"
            f"# {note['title']}\n\n"
            f"**TL;DR:** {note['tldr']}\n\n"
            f"{note['body']}\n\n"
            f"Project: [[{doc.project}]] · Daily: [[{doc.date}]]\n"
        )
        (directory / fname).write_text(content)
        return (directory / fname).relative_to(self.vault)

    # ---------------------------------------------------------- compile --
    def _session_notes(self) -> list[tuple[dict, Path, str]]:
        notes = []
        for f in sorted((self.vault / "sessions").rglob("*.md"), reverse=True):
            text = f.read_text()
            meta = dict(re.findall(r"^(\w+): (.+)$", text.split("---")[1], re.M)) \
                if text.startswith("---") else {}
            notes.append((meta, f, text))
        return notes

    def compile_project(self, project: str) -> None:
        decisions, followups, links = [], [], []
        for meta, f, text in self._session_notes():
            if meta.get("project") != project:
                continue
            rel = f.relative_to(self.vault)
            title = _first_heading(text)
            links.append(f"- [[{f.stem}|{meta.get('date', '?')} ({meta.get('source', '?')}): {title}]]")
            decisions += [f"- ({meta.get('date', '?')}) {b}" for b in _section(text, "Decisions")]
            followups += [f"- ({meta.get('date', '?')}) {b}" for b in _section(text, "Follow-ups")]
        page = (
            f"---\ntype: project\n---\n\n# {project}\n\n"
            f"_Auto-compiled from {len(links)} session(s); "
            f"last sync {datetime.now():%Y-%m-%d}._\n\n"
            f"## Decisions\n" + ("\n".join(decisions[:40]) or "- none yet") + "\n\n"
            f"## Open threads\n" + ("\n".join(followups[:20]) or "- none") + "\n\n"
            f"## Sessions\n" + "\n".join(links) + "\n"
        )
        (self.vault / "wiki/projects" / f"{project}.md").write_text(page)

    def compile_daily(self, date: str) -> None:
        entries = []
        for meta, f, text in self._session_notes():
            if meta.get("date") != date:
                continue
            tldr = re.search(r"\*\*TL;DR:\*\* (.+)", text)
            entries.append(
                f"## [[{f.stem}|{meta.get('project')}: {_first_heading(text)}]] "
                f"({meta.get('source')})\n{tldr.group(1) if tldr else ''}"
            )
        if entries:
            (self.vault / "daily" / f"{date}.md").write_text(
                f"# {date}\n\n" + "\n\n".join(entries) + "\n"
            )

    def compile_index(self) -> None:
        lines = ["# INDEX", "", "One line per note — search here first, then read the note.", ""]
        for page in sorted((self.vault / "wiki/projects").glob("*.md")):
            lines.append(f"- wiki/projects/{page.name} — project rollup: {page.stem}")
        for meta, f, text in self._session_notes():
            tldr = re.search(r"\*\*TL;DR:\*\* (.+)", text)
            snippet = (tldr.group(1)[:100] if tldr else "")
            lines.append(f"- {f.relative_to(self.vault)} — {_first_heading(text)} — {snippet}")
        (self.vault / "INDEX.md").write_text("\n".join(lines) + "\n")

    def _commit(self, count: int) -> None:
        subprocess.run(["git", "add", "-A"], cwd=self.vault, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", f"brain sync: {count} session(s), {datetime.now():%Y-%m-%d %H:%M}"],
            cwd=self.vault,
            capture_output=True,
        )


# ---------------------------------------------------------------- parse --
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
