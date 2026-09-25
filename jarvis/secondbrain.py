"""Second brain: an Obsidian vault distilled from Claude + Codex session logs.

Design (Karpathy LLM-wiki + OpenClaw memory pattern):
- Raw transcripts stay immutable in ~/.claude/projects and ~/.codex/sessions.
- Each session is distilled ONCE (via the configured sync provider) into a permanent
  note with separate task sections for distinct user objectives.
- Mechanical compilation layers on top: per-project wiki pages, daily
  digests, and INDEX.md for progressive disclosure by agents.
- Sync is incremental (state in .brain/state.json), batched, and commits
  locally to git. It NEVER pushes — the user controls GitHub.

Call flow:
    sync()
      ├─ ensure_vault()          # scaffold + git init, idempotent
      ├─ discover()              # new session files vs state.json
      ├─ parse_claude/_codex()   # transcript -> (role, text) turns
      ├─ distill()               # configured Claude/Codex model -> markdown note
      └─ compile_()              # project pages, dailies, INDEX, git commit
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

from .config import Config
from .session_parsers import (
    SessionDoc,
    _condense,
    _fingerprint,
    _first_heading,
    _parse_distilled,
    _section,
    parse_claude,
    parse_codex,
    parse_omp,
)

CLAUDE_PROJECTS = Path.home() / ".claude/projects"
CODEX_SESSIONS = Path.home() / ".codex/sessions"
OMP_SESSIONS = Path.home() / ".omp/agent/sessions"
STATE_VERSION = 2

DISTILL_PROMPT = """You are distilling a coding-assistant session transcript into a permanent \
note for a personal knowledge vault (a "second brain"). It will be read later by the user \
and his assistant to recall context, so be specific: names, paths, versions, numbers.

Output EXACTLY this format, plain text, no preamble:
TITLE: <concise 4-8 word title>
TAGS: <2-4 lowercase comma-separated tags>
TLDR: <1-2 sentences: what was done and the outcome>

## Task 1: <concise description of the first distinct user objective>
Outcome: <success, partial, blocked, or informational>

### Preference signals
- <user constraints or corrections that should guide future work>

### Decisions
- <technical or architectural decisions and why they were made>

### Problems & Solutions
- <specific problems and how they were or must be solved>

### Questions Asked
- <notable questions the user asked, including unresolved unknowns>

### Key steps
- <important investigation or implementation steps>

### Validation
- <checks performed and their results, including what was not tested>

### Follow-ups
- <open threads, unfinished work, or things to revisit>

Repeat as `## Task 2: ...`, `## Task 3: ...`, and so on for every distinct user \
objective in the session. A task can span many turns; do not turn routine implementation \
steps or tool calls into separate tasks. Keep decisions, problems and solutions, questions, \
preferences, steps, validation, and follow-ups under the task they belong to. Omit any \
per-task subsection that has no transcript-supported content. Never invent missing metadata \
or validation. Omit pleasantries and routine tool chatter. The transcript follows."""

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
        return {"version": STATE_VERSION, "processed": {}}

    def _save_state(self, state: dict) -> None:
        self.state_path.write_text(json.dumps(state, indent=1))

    # ------------------------------------------------------------- sync --
    def sync(self, max_sessions: int = 12, log=print) -> str:
        self.ensure_vault()
        state = self._state()
        if state.get("version") != STATE_VERSION:
            self._reset_generated_notes()
            state = {"version": STATE_VERSION, "processed": {}}
            log("Reset legacy distillation state; all sessions will be re-distilled.")
        candidates = self.discover(state)
        log(f"{len(candidates)} unprocessed session file(s); distilling up to {max_sessions}.")

        done, skipped, touched_projects, touched_dates = 0, 0, set(), set()
        for path, source in candidates:
            if done >= max_sessions:
                break
            key = str(path)
            fingerprint = _fingerprint(path)
            if source == "claude":
                doc = parse_claude(path)
            elif source == "codex":
                doc = parse_codex(path)
            elif source == "omp":
                doc = parse_omp(path)
            else:
                doc = None
            if doc is None or self._should_skip(doc):
                state["processed"][key] = {
                    "status": "skipped",
                    "filename": path.name,
                    "fingerprint": fingerprint,
                }
                skipped += 1
                continue
            log(f"  distilling {source}:{doc.project} {doc.date} ({doc.sid[:8]})")
            note = self.distill(doc)
            if note is None:
                state["processed"][key] = {
                    "status": "failed",
                    "filename": path.name,
                    "fingerprint": fingerprint,
                }
                skipped += 1
                continue
            previous = state["processed"].get(key, {})
            rel = self._write_note(doc, note, fingerprint)
            previous_note = previous.get("note")
            if previous_note and previous_note != str(rel):
                (self.vault / previous_note).unlink(missing_ok=True)
            state["processed"][key] = {
                "status": "done",
                "filename": path.name,
                "fingerprint": fingerprint,
                "note": str(rel),
            }
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
            entry = state["processed"].get(str(f), {})
            return (
                entry.get("status") not in ("done", "skipped")
                or entry.get("fingerprint") != _fingerprint(f)
            )

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
        if OMP_SESSIONS.is_dir():
            for f in OMP_SESSIONS.glob("*/*.jsonl"):
                if "second-brain" in f.parent.name:
                    continue
                if unprocessed(f):
                    found.append((f.stat().st_mtime, f, "omp"))
        found.sort(reverse=True)  # newest first
        return [(f, source) for _, f, source in found]

    def _reset_generated_notes(self) -> None:
        for directory in (
            self.vault / "sessions",
            self.vault / "daily",
            self.vault / "wiki/projects",
        ):
            for note in directory.rglob("*.md"):
                note.unlink()
        (self.vault / "INDEX.md").unlink(missing_ok=True)

    def _should_skip(self, doc: SessionDoc) -> bool:
        if "second-brain" in doc.cwd or "jarvis-test-sandbox" in doc.cwd:
            return True
        user_turns = [t for role, t in doc.turns if role == "user"]
        total = sum(len(t) for _, t in doc.turns)
        return len(user_turns) < 2 or total < 400

    # ---------------------------------------------------------- distill --
    def distill(self, doc: SessionDoc) -> dict | None:
        transcript = _condense(doc.turns)
        sync = self.cfg.models.sync
        if sync.provider == "antigravity":
            model = sync.antigravity.model or "gemini-3.8-flash-high"
            cmd = [self.cfg.agy_bin, "--model", model, "-p", f"{DISTILL_PROMPT}\n\nTRANSCRIPT:\n{transcript}"]
            input_text = None
        elif sync.provider == "opencode":
            from .orchestration.external import run_sync

            try:
                return _parse_distilled(run_sync(self.cfg, DISTILL_PROMPT, transcript))
            except (RuntimeError, TimeoutError, OSError):
                return None
        elif sync.provider == "claude":
            cmd = ["claude", "-p", "--model", sync.claude.model, DISTILL_PROMPT]
            input_text = transcript
        elif sync.provider == "codex":
            codex = sync.codex
            cmd = [
                self.cfg.codex_bin,
                "exec",
                "--model",
                codex.model,
                "--sandbox",
                codex.sandbox,
                "--cd",
                str(self.vault),
            ]
            if codex.ephemeral:
                cmd.append("--ephemeral")
            if codex.skip_git_repo_check:
                cmd.append("--skip-git-repo-check")
            if codex.ignore_user_config:
                cmd.append("--ignore-user-config")
            if codex.ignore_rules:
                cmd.append("--ignore-rules")
            cmd.append(DISTILL_PROMPT)
            input_text = transcript
        else:
            raise ValueError(f"Unknown sync provider: {sync.provider!r}")
        try:
            out = subprocess.run(
                cmd,
                input=input_text,
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

    def _write_note(self, doc: SessionDoc, note: dict, fingerprint: str) -> Path:
        year, month, day = doc.date.split("-")
        directory = self.vault / "sessions" / year / month
        directory.mkdir(parents=True, exist_ok=True)
        fname = f"{day}-{doc.project}-{doc.source}-{doc.sid}-{fingerprint[:16]}.md"
        tags = ", ".join(t for t in note["tags"] if t.lower() != "session")
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
