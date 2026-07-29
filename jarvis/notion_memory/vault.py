"""Vault loader: tag index + wiki/markdown link graph + lexical content map.

Ports tag-indexer.ts (frontmatter tags) and graph-parser.ts (wiki + md links).
"""
from __future__ import annotations

import re
from pathlib import Path

from .frontmatter import parse_frontmatter

_WIKI = re.compile(r"\[\[([^\[\]|#]+)(?:\|[^\[\]]+)?(?:#[^\[\]]+)?\]\]")
_MDLINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def note_name(path: str) -> str:
    base = re.split(r"[/\\]", path)[-1]
    dot = base.rfind(".")
    return base[:dot] if dot != -1 else base


def parse_links(content: str) -> list[str]:
    targets: list[str] = []
    for m in _WIKI.finditer(content):
        t = m.group(1).strip()
        if t and t not in targets:
            targets.append(t)
    for m in _MDLINK.finditer(content):
        url = m.group(2).strip()
        if "://" not in url and (url.endswith(".md") or url.startswith(".") or url.startswith("/")):
            t = note_name(url)
            if t and t not in targets:
                targets.append(t)
    return targets


class Vault:
    def __init__(self) -> None:
        self.tag_map: dict[str, set[str]] = {}
        self.meta: dict[str, dict] = {}
        self.content: dict[str, str] = {}
        self.path: dict[str, str] = {}
        self.forward: dict[str, set[str]] = {}
        self.back: dict[str, set[str]] = {}

    def index_file(self, file_path: str, raw: str) -> None:
        name = note_name(file_path)
        data, body = parse_frontmatter(raw)
        self.meta[name] = data
        self.content[name] = body.lower()
        self.path[name] = file_path
        tags = data.get("tags")
        if isinstance(tags, list):
            for tag in tags:
                if isinstance(tag, str):
                    self.tag_map.setdefault(tag.lower(), set()).add(name)
        for target in parse_links(raw):
            self.forward.setdefault(name, set()).add(target)
            self.back.setdefault(target, set()).add(name)

    @classmethod
    def load(cls, vault_dir: Path) -> "Vault":
        v = cls()
        if not vault_dir.exists():
            return v
        for md in sorted(vault_dir.rglob("*.md")):
            try:
                v.index_file(str(md), md.read_text())
            except OSError:
                continue
        return v

    def files_with_tag(self, tag: str) -> set[str]:
        return self.tag_map.get(tag.lower(), set())

    def forward_links(self, name: str) -> list[str]:
        return sorted(self.forward.get(name, set()))

    def backlinks(self, name: str) -> list[str]:
        return sorted(self.back.get(name, set()))
