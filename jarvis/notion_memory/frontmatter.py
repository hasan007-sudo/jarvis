"""Parse Obsidian-style YAML frontmatter from a markdown string."""
from __future__ import annotations

import re

import yaml

_FRONTMATTER = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)


def parse_frontmatter(content: str) -> tuple[dict, str]:
    match = _FRONTMATTER.match(content)
    if not match:
        return {}, content
    try:
        data = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    return data, content[match.end():].strip()
