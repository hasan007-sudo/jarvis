"""Resolve a spoken project name to a folder under the user's home directory.

Resolution order: explicit path -> registered projects in config -> search the
configured roots (and home, one level deep). Returns *all* candidates; the
orchestrator asks the user when there are zero or several.
"""

from __future__ import annotations

import re
from pathlib import Path

from .config import Config

SKIP_DIRS = {
    "node_modules",
    "Library",
    "Applications",
    "Movies",
    "Music",
    "Pictures",
    ".Trash",
    "venv",
    ".venv",
    "__pycache__",
    "dist",
    "build",
    "target",
}


def _norm(name: str) -> str:
    return re.sub(r"[\s_\-.]+", "", name).casefold()


def find_project(query: str, cfg: Config) -> list[Path]:
    query = query.strip()

    # Explicit path ("~/Github/foo" or "/Users/...")
    if "/" in query:
        p = Path(query).expanduser()
        return [p] if p.is_dir() else []

    target = _norm(query)

    # Registered projects win
    for name, path in cfg.projects.items():
        if _norm(name) == target and path.is_dir():
            return [path]

    # Search configured roots (depth 3) plus home itself (depth 1)
    roots: list[tuple[Path, int]] = [(r, 3) for r in cfg.search_roots if r.is_dir()]
    roots.append((Path.home(), 1))

    hits: list[Path] = []
    visited: set[Path] = set()
    for root, depth in roots:
        _walk(root, depth, target, hits, visited)

    # Prefer git repos, then shallower paths
    hits.sort(key=lambda p: (not (p / ".git").exists(), len(p.parts)))
    return hits


def _walk(root: Path, depth: int, target: str, hits: list[Path], visited: set[Path]) -> None:
    if root in visited:
        return
    visited.add(root)
    try:
        entries = [
            e
            for e in root.iterdir()
            if e.is_dir() and not e.name.startswith(".") and e.name not in SKIP_DIRS
        ]
    except (PermissionError, OSError):
        return
    for entry in entries:
        if _norm(entry.name) == target:
            if entry not in hits:
                hits.append(entry)
        elif depth > 1:
            _walk(entry, depth - 1, target, hits, visited)
