# Notion Memory (`jarvis notes`) Implementation Plan

> **For agentic workers:** Implement task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Per project owner's standing rule, **no automated tests** are written; each task ends with a manual run-to-verify step.

**Goal:** Add a `jarvis notes` command that ingests Notion pages into a separate Obsidian-style vault and answers queries over them with local hybrid retrieval (BM25 + tags + wiki-graph, RRF-fused, Ebbinghaus decay-weighted), optionally routed through Claude/Codex for prose answers.

**Architecture:** A new isolated package `jarvis/notion_memory/` ports the OpenCode Orchestrator retrieval core to pure Python. Notion pages are pulled incrementally via the free Notion API and written as markdown-with-frontmatter into a dedicated vault. Retrieval is 100% local (no embeddings, no vector DB). The existing `second_brain` config and `sync` command are untouched.

**Tech Stack:** Python 3.11+, `pyyaml` (already a dep), `httpx` (new optional dep for Notion API), stdlib `subprocess` for one-shot Claude/Codex calls.

## Phase 1 Scope (current build)

**Build now:** Tasks 1, 2, 3, 4, 5, 7, 8, 9 — the local retrieval engine plus `query` and `ask`.

**Deferred:** Task 6 (Notion API client) and the `notes_sync` entrypoint. Phase 1 therefore does **not** add `httpx`, does **not** create `notion.py`, and does **not** register a `notes sync` subcommand. `command.py` must not import from `.notion`.

Until ingestion lands, point `notion_memory.vault` at any existing Obsidian-style vault (the second-brain vault already carries `tags:` frontmatter and `[[wikilinks]]`) to exercise retrieval end to end.

## Global Constraints

- Python `>=3.11`; follow existing jarvis style (argparse subcommands, lazy imports inside dispatch, `Config.load()`, dataclass config sections).
- **Do NOT modify** `second_brain` config, `SecondBrainConfig`, `secondbrain.py`, or the `sync` command.
- No secrets in yaml: the Notion token is read only from the `NOTION_TOKEN` environment variable.
- Retrieval must stay local and free — no external calls except the Notion API (`sync`) and the existing Claude/Codex CLIs (`ask`).
- New vault default: `~/Github/Personal/notion-brain`.
- Ported math must stay faithful to the source constants: `RRF_K=60`, `BM25_K1=1.2`, `BM25_B=0.75`, `GRAPH_HOP=2`, `MIN_STRENGTH=0.05`, `EXPIRED_MULTIPLIER=0.35`, `DEFAULT_DECAY_LAMBDA=0.03`, and the `KIND_DECAY` table.
- No tests. Verify each task by importing the module and/or running the actual command.

---

## File Structure

```
jarvis/notion_memory/
  __init__.py        # package marker
  frontmatter.py     # parse_frontmatter(content) -> (dict, body)
  scoring.py         # memory_strength / is_prompt_safe / decay lambdas   (ports memory-scoring.ts + memory-kind.ts)
  vault.py           # Vault loader: tag index + wiki/md link graph + content map (ports tag-indexer.ts + graph-parser.ts)
  retrieval.py       # HybridSearch: BM25 ⊕ tag ⊕ graph → RRF → × decay   (ports hybrid-search.ts)
  notion.py          # NotionClient (httpx) + page→markdown conversion
  llm.py             # run_llm(cfg, prompt, stdin) one-shot Claude/Codex
  command.py         # notes_sync / notes_query / notes_ask entrypoints
```
Modified: `jarvis/config.py` (add `notion_memory` section), `jarvis/cli.py` (add `notes` subcommand), `pyproject.toml` (add `httpx` extra).

---

## Task 1: Config section (isolated)

**Files:**
- Modify: `jarvis/config.py`

**Interfaces:**
- Produces: `Config.notion_memory: NotionMemoryConfig` with fields `vault: Path`, `token_env: str`, `batch: int`.

- [ ] **Step 1: Add default block.** In `DEFAULT_CONFIG`, after the `second_brain` block, add:

```python
    "notion_memory": {
        "vault": "~/Github/Personal/notion-brain",
        "token_env": "NOTION_TOKEN",
        "batch": 100,
    },
```

- [ ] **Step 2: Add dataclass.** After `SecondBrainConfig`:

```python
@dataclass
class NotionMemoryConfig:
    vault: Path = Path.home() / "Github/Personal/notion-brain"
    token_env: str = "NOTION_TOKEN"
    batch: int = 100
```

- [ ] **Step 3: Add field on `Config`.** After the `second_brain: SecondBrainConfig = ...` line:

```python
    notion_memory: NotionMemoryConfig = field(default_factory=NotionMemoryConfig)
```

- [ ] **Step 4: Populate in `Config.load()`.** After the `sb = {...}` merge line add:

```python
        nm = {**DEFAULT_CONFIG["notion_memory"], **(merged.get("notion_memory") or {})}
```

and add this keyword to the `return cls(...)` call (next to `second_brain=...`):

```python
            notion_memory=NotionMemoryConfig(
                vault=Path(nm["vault"]).expanduser(),
                token_env=str(nm["token_env"]),
                batch=int(nm["batch"]),
            ),
```

- [ ] **Step 5: Verify.**

Run: `cd /Users/mohammedhasan/Github/Personal/jarvis && python -c "from jarvis.config import Config; c=Config.load(); print(c.notion_memory)"`
Expected: prints `NotionMemoryConfig(vault=PosixPath('.../notion-brain'), token_env='NOTION_TOKEN', batch=100)` and existing config still loads (no exception).

- [ ] **Step 6: Commit.**

```bash
git add jarvis/config.py
git commit -m "feat(notes): add isolated notion_memory config section"
```

---

## Task 2: Frontmatter parser

**Files:**
- Create: `jarvis/notion_memory/__init__.py` (empty)
- Create: `jarvis/notion_memory/frontmatter.py`

**Interfaces:**
- Produces: `parse_frontmatter(content: str) -> tuple[dict, str]` — returns `(frontmatter_dict, body_without_frontmatter)`; `({}, content)` when no frontmatter.

- [ ] **Step 1: Create package marker.** `jarvis/notion_memory/__init__.py` = empty file.

- [ ] **Step 2: Implement.** `jarvis/notion_memory/frontmatter.py`:

```python
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
```

- [ ] **Step 3: Verify.**

Run:
```bash
cd /Users/mohammedhasan/Github/Personal/jarvis && python -c "
from jarvis.notion_memory.frontmatter import parse_frontmatter
d,b = parse_frontmatter('---\ntitle: X\ntags: [a, b]\n---\n\nHello body')
print(d, repr(b))
print(parse_frontmatter('no frontmatter here'))
"
```
Expected: `{'title': 'X', 'tags': ['a', 'b']} 'Hello body'` then `({}, 'no frontmatter here')`.

- [ ] **Step 4: Commit.**

```bash
git add jarvis/notion_memory/__init__.py jarvis/notion_memory/frontmatter.py
git commit -m "feat(notes): frontmatter parser"
```

---

## Task 3: Decay scoring (port of memory-scoring.ts + memory-kind.ts)

**Files:**
- Create: `jarvis/notion_memory/scoring.py`

**Interfaces:**
- Produces:
  - `memory_strength(meta: dict | None, now: float | None = None) -> float` — Ebbinghaus strength in `[0.05, 1.0]`; `1.0` for no-metadata / `keep`; `0.0` for archived.
  - `is_prompt_safe(meta: dict | None) -> bool` — excludes sensitive / malicious / tombstoned.

- [ ] **Step 1: Implement.** `jarvis/notion_memory/scoring.py`:

```python
"""Local Ebbinghaus memory-strength math (port of memory-scoring.ts + memory-kind.ts)."""
from __future__ import annotations

import math
from datetime import datetime, timezone

DEFAULT_DECAY_LAMBDA = 0.03
KIND_DECAY: dict[str, float] = {
    "procedural": 0.006, "semantic": 0.018, "episodic": 0.07,
    "sop": 0.006, "workflow": 0.01, "fact": 0.018,
    "preference": 0.02, "gotcha": 0.03, "episode": 0.07,
}
MIN_STRENGTH = 0.05
EXPIRED_MULTIPLIER = 0.35

_MEMORY_KEYS = (
    "event_time", "ingestion_time", "last_accessed", "valid_to", "decay_lambda",
    "access_count", "access_ema", "importance", "confidence", "memory_kind",
    "memory_layer", "valid_from", "supersedes",
)


def _now_ms() -> float:
    return datetime.now(timezone.utc).timestamp() * 1000


def _ts_ms(value) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00")).timestamp() * 1000
    except ValueError:
        return None


def _num_or(value, fallback: float) -> float:
    return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else fallback


def decay_lambda(meta: dict) -> float:
    explicit = meta.get("decay_lambda")
    if isinstance(explicit, (int, float)) and math.isfinite(explicit):
        return float(explicit)
    return KIND_DECAY.get(meta.get("memory_kind", "fact"), DEFAULT_DECAY_LAMBDA)


def _has_memory_metadata(meta: dict) -> bool:
    return any(meta.get(k) is not None for k in _MEMORY_KEYS)


def _is_expired(valid_to, now: float) -> bool:
    exp = _ts_ms(valid_to)
    return exp is not None and exp < now


def memory_strength(meta: dict | None, now: float | None = None) -> float:
    if now is None:
        now = _now_ms()
    if not meta or meta.get("keep") is True:
        return 1.0
    if meta.get("memory_layer") == "archive":
        return 0.0
    if not _has_memory_metadata(meta):
        return 1.0
    last_seen = _ts_ms(meta.get("last_accessed") or meta.get("ingestion_time")) or now
    age_days = max(0.0, (now - last_seen) / 86_400_000)
    lam = decay_lambda(meta)
    access = _num_or(meta.get("access_ema"), _num_or(meta.get("access_count"), 0))
    quality = max(0.1, _num_or(meta.get("importance"), 1) * _num_or(meta.get("confidence"), 1))
    reinforcement = 1 + math.log1p(access) / 4
    expiry = EXPIRED_MULTIPLIER if _is_expired(meta.get("valid_to"), now) else 1
    raw = quality * reinforcement * math.exp(-lam * age_days) * expiry
    return min(1.0, max(MIN_STRENGTH, raw))


def is_prompt_safe(meta: dict | None) -> bool:
    if not meta:
        return True
    return (
        meta.get("privacy_class") != "sensitive"
        and meta.get("memory_layer") != "malicious"
        and meta.get("tombstone") is not True
    )
```

- [ ] **Step 2: Verify.**

Run:
```bash
cd /Users/mohammedhasan/Github/Personal/jarvis && python -c "
from jarvis.notion_memory.scoring import memory_strength, is_prompt_safe
print('plain', memory_strength({}))            # 1.0
print('archive', memory_strength({'memory_layer':'archive'}))  # 0.0
print('fresh', round(memory_strength({'memory_kind':'semantic','ingestion_time':'2026-07-24T00:00:00+00:00'}),3))
print('old', round(memory_strength({'memory_kind':'episodic','ingestion_time':'2025-01-01T00:00:00+00:00'}),3))
print('safe', is_prompt_safe({'privacy_class':'sensitive'}))   # False
"
```
Expected: `plain 1.0`, `archive 0.0`, `fresh` near 1.0, `old` a small value ≥ 0.05, `safe False`.

- [ ] **Step 3: Commit.**

```bash
git add jarvis/notion_memory/scoring.py
git commit -m "feat(notes): port Ebbinghaus decay scoring"
```

---

## Task 4: Vault loader (port of tag-indexer.ts + graph-parser.ts)

**Files:**
- Create: `jarvis/notion_memory/vault.py`

**Interfaces:**
- Consumes: `parse_frontmatter` from Task 2.
- Produces: `class Vault` with attributes `meta: dict[str,dict]`, `content: dict[str,str]` (lowercased body), `path: dict[str,str]`; methods `index_file(file_path, raw)`, `files_with_tag(tag) -> set[str]`, `forward_links(name) -> list[str]`, `backlinks(name) -> list[str]`, classmethod `load(vault_dir: Path) -> Vault`. Note keys are basenames without extension (`note_name`).

- [ ] **Step 1: Implement.** `jarvis/notion_memory/vault.py`:

```python
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
```

- [ ] **Step 2: Verify.**

Run:
```bash
cd /Users/mohammedhasan/Github/Personal/jarvis && python -c "
from jarvis.notion_memory.vault import Vault, parse_links, note_name
v = Vault()
v.index_file('/x/auth.md', '---\ntags: [security, auth]\n---\n\nAuth links to [[login]].')
v.index_file('/x/login.md', '---\ntags: [auth]\n---\n\nLogin note.')
print(note_name('/x/auth.md'))                 # auth
print(sorted(v.files_with_tag('auth')))        # ['auth', 'login']
print(v.forward_links('auth'))                 # ['login']
print(v.backlinks('login'))                    # ['auth']
print(parse_links('see [[A]] and [B](./b.md) and [ext](http://x.com)'))  # ['A', 'b']
"
```
Expected: matches inline comments.

- [ ] **Step 3: Commit.**

```bash
git add jarvis/notion_memory/vault.py
git commit -m "feat(notes): vault tag index + link graph"
```

---

## Task 5: Hybrid search (port of hybrid-search.ts)

**Files:**
- Create: `jarvis/notion_memory/retrieval.py`

**Interfaces:**
- Consumes: `Vault` (Task 4); `memory_strength`, `is_prompt_safe` (Task 3).
- Produces: `@dataclass SearchResult(note: str, score: float, match_type: str, path: str)`; `class HybridSearch(vault: Vault)` with `search(query: str, max_results: int = 20) -> list[SearchResult]`.

- [ ] **Step 1: Implement.** `jarvis/notion_memory/retrieval.py`:

```python
"""HybridSearch: BM25 (lexical) + tag + 2-hop graph, RRF-fused, decay-weighted.

Port of hybrid-search.ts with NEUTRAL engine weights (all 1.0).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .scoring import is_prompt_safe, memory_strength
from .vault import Vault

RRF_K = 60
DEFAULT_MAX = 20
BM25_K1 = 1.2
BM25_B = 0.75
GRAPH_HOP = 2


@dataclass
class SearchResult:
    note: str
    score: float
    match_type: str
    path: str


class HybridSearch:
    def __init__(self, vault: Vault) -> None:
        self.v = vault
        self._avg_len = sum(len(c) for c in vault.content.values()) / max(len(vault.content), 1)

    def search(self, query: str, max_results: int = DEFAULT_MAX) -> list[SearchResult]:
        terms = [t for t in query.lower().split() if t]
        if not terms:
            return []
        return self._fuse(self._lexical(terms), self._tag(terms), self._graph(terms), max_results)

    def _lexical(self, terms: list[str]) -> list[str]:
        scores: dict[str, float] = {}
        n = len(self.v.content)
        if n == 0:
            return []
        for term in terms:
            df = sum(1 for c in self.v.content.values() if term in c)
            idf = math.log((n - df + 0.5) / (df + 0.5) + 1)
            for name, content in self.v.content.items():
                tf = _count(content, term)
                if tf == 0:
                    continue
                doc_len = len(content)
                tf_norm = (tf * (BM25_K1 + 1)) / (
                    tf + BM25_K1 * (1 - BM25_B + BM25_B * (doc_len / self._avg_len))
                )
                scores[name] = scores.get(name, 0.0) + idf * tf_norm
        return _sorted(scores)

    def _tag(self, terms: list[str]) -> list[str]:
        scores: dict[str, float] = {}
        for term in terms:
            for name in self.v.files_with_tag(term):
                scores[name] = scores.get(name, 0.0) + 1
        return _sorted(scores)

    def _graph(self, terms: list[str]) -> list[str]:
        seeds = set(self._tag(terms))
        visited: set[str] = set()
        scores: dict[str, float] = {}
        for seed in seeds:
            self._traverse(seed, GRAPH_HOP, visited, scores)
        return _sorted(scores)

    def _traverse(self, note: str, depth: int, visited: set[str], scores: dict[str, float]) -> None:
        if depth <= 0 or note in visited:
            return
        visited.add(note)
        for nb in self.v.forward_links(note) + self.v.backlinks(note):
            scores[nb] = scores.get(nb, 0.0) + depth
            self._traverse(nb, depth - 1, visited, scores)

    def _fuse(self, lexical, tags, graph, limit: int) -> list[SearchResult]:
        fused: dict[str, list] = {}
        self._rrf(fused, lexical, "lexical")
        self._rrf(fused, tags, "tag")
        self._rrf(fused, graph, "graph")
        results: list[SearchResult] = []
        for name, (score, match_type) in fused.items():
            meta = self.v.meta.get(name)
            if not is_prompt_safe(meta):
                continue
            final = score * memory_strength(meta)
            if final <= 0:
                continue
            results.append(SearchResult(name, final, match_type, self.v.path.get(name, "")))
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

    def _rrf(self, fused: dict[str, list], ranked: list[str], match_type: str) -> None:
        for i, name in enumerate(ranked):
            rrf = 1 / (RRF_K + i + 1)
            if name in fused:
                fused[name][0] += rrf
                if rrf > 1 / (RRF_K + 1):
                    fused[name][1] = match_type
            else:
                fused[name] = [rrf, match_type]


def _count(text: str, term: str) -> int:
    count = pos = 0
    while (pos := text.find(term, pos)) != -1:
        count += 1
        pos += len(term)
    return count


def _sorted(scores: dict[str, float]) -> list[str]:
    return [k for k, _ in sorted(scores.items(), key=lambda x: x[1], reverse=True)]
```

- [ ] **Step 2: Verify.**

Run:
```bash
cd /Users/mohammedhasan/Github/Personal/jarvis && python -c "
from jarvis.notion_memory.vault import Vault
from jarvis.notion_memory.retrieval import HybridSearch
v = Vault()
v.index_file('/x/auth.md', '---\ntags: [auth]\n---\n\nAuthentication and login token flow. [[session]]')
v.index_file('/x/session.md', '---\ntags: [session]\n---\n\nSession cookies.')
v.index_file('/x/pizza.md', '---\ntags: [food]\n---\n\nPizza recipe.')
for r in HybridSearch(v).search('login token'):
    print(round(r.score,4), r.match_type, r.note)
"
```
Expected: `auth` ranks first (lexical+graph); `pizza` absent; `session` may appear via graph. No exception.

- [ ] **Step 3: Commit.**

```bash
git add jarvis/notion_memory/retrieval.py
git commit -m "feat(notes): port BM25+RRF hybrid search"
```

---

## Task 6: Notion client + page→markdown

**Files:**
- Create: `jarvis/notion_memory/notion.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `class NotionClient(token: str)` with `search_pages() -> Iterator[dict]` (pages, `last_edited_time` descending) and `blocks(block_id: str) -> list[dict]`; module functions `page_title(page) -> str`, `page_tags(page) -> list[str]`, `slugify(title) -> str`, `blocks_to_md(blocks, id2slug: dict) -> str`.

- [ ] **Step 1: Add dependency.** In `pyproject.toml`, under `[project.optional-dependencies]`, add:

```toml
notion = ["httpx>=0.27"]
```

- [ ] **Step 2: Install it.**

Run: `cd /Users/mohammedhasan/Github/Personal/jarvis && pip install 'httpx>=0.27'`
Expected: installs without error.

- [ ] **Step 3: Implement.** `jarvis/notion_memory/notion.py`:

```python
"""Minimal Notion API client (free tier) + page-to-markdown conversion."""
from __future__ import annotations

import re
import time
from typing import Iterator

import httpx

API = "https://api.notion.com/v1"
VERSION = "2022-06-28"
THROTTLE_SECONDS = 0.34  # ~3 req/sec average limit


class NotionClient:
    def __init__(self, token: str) -> None:
        self._client = httpx.Client(
            timeout=30,
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": VERSION,
                "Content-Type": "application/json",
            },
        )

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        for _ in range(5):
            resp = self._client.request(method, f"{API}{path}", json=body)
            if resp.status_code == 429:
                time.sleep(float(resp.headers.get("Retry-After", 1)))
                continue
            resp.raise_for_status()
            time.sleep(THROTTLE_SECONDS)
            return resp.json()
        resp.raise_for_status()
        return {}

    def search_pages(self) -> Iterator[dict]:
        cursor = None
        while True:
            body = {
                "filter": {"property": "object", "value": "page"},
                "sort": {"direction": "descending", "timestamp": "last_edited_time"},
                "page_size": 100,
            }
            if cursor:
                body["start_cursor"] = cursor
            data = self._request("POST", "/search", body)
            for page in data.get("results", []):
                yield page
            if not data.get("has_more"):
                return
            cursor = data.get("next_cursor")

    def blocks(self, block_id: str) -> list[dict]:
        out: list[dict] = []
        cursor = None
        while True:
            path = f"/blocks/{block_id}/children?page_size=100"
            if cursor:
                path += f"&start_cursor={cursor}"
            data = self._request("GET", path)
            out.extend(data.get("results", []))
            if not data.get("has_more"):
                return out
            cursor = data.get("next_cursor")


def page_title(page: dict) -> str:
    for prop in page.get("properties", {}).values():
        if prop.get("type") == "title":
            text = "".join(t.get("plain_text", "") for t in prop.get("title", []))
            return text or "Untitled"
    return "Untitled"


def page_tags(page: dict) -> list[str]:
    tags: list[str] = []
    for prop in page.get("properties", {}).values():
        kind = prop.get("type")
        if kind == "select" and prop.get("select"):
            tags.append(prop["select"]["name"])
        elif kind == "multi_select":
            tags.extend(o["name"] for o in prop.get("multi_select", []))
    return tags


def slugify(title: str) -> str:
    s = re.sub(r"[^\w\s-]", "", title.lower()).strip()
    return re.sub(r"[\s_]+", "-", s) or "untitled"


_BLOCK_PREFIX = {
    "heading_1": "# ", "heading_2": "## ", "heading_3": "### ",
    "bulleted_list_item": "- ", "numbered_list_item": "1. ",
    "quote": "> ", "to_do": "- [ ] ", "callout": "> ",
}


def _rich_text(spans: list[dict], id2slug: dict) -> str:
    parts: list[str] = []
    for s in spans:
        mention = s.get("mention") if s.get("type") == "mention" else None
        if mention and mention.get("type") == "page":
            slug = id2slug.get(mention["page"]["id"])
            parts.append(f"[[{slug}]]" if slug else s.get("plain_text", ""))
        else:
            parts.append(s.get("plain_text", ""))
    return "".join(parts)


def blocks_to_md(blocks: list[dict], id2slug: dict) -> str:
    lines: list[str] = []
    for b in blocks:
        t = b.get("type", "")
        payload = b.get(t, {})
        text = _rich_text(payload.get("rich_text", []), id2slug)
        if t == "code":
            lines.append(f"```{payload.get('language', '')}\n{text}\n```")
        elif t in _BLOCK_PREFIX:
            lines.append(_BLOCK_PREFIX[t] + text)
        elif text:
            lines.append(text)
    return "\n\n".join(lines)
```

- [ ] **Step 4: Verify (offline — no token needed).**

Run:
```bash
cd /Users/mohammedhasan/Github/Personal/jarvis && python -c "
from jarvis.notion_memory.notion import page_title, page_tags, slugify, blocks_to_md
page = {'properties': {'Name': {'type':'title','title':[{'plain_text':'My Auth Notes'}]},
                       'Area': {'type':'multi_select','multi_select':[{'name':'security'},{'name':'auth'}]}}}
print(page_title(page))                  # My Auth Notes
print(page_tags(page))                   # ['security', 'auth']
print(slugify('My Auth Notes!'))         # my-auth-notes
blocks = [{'type':'heading_1','heading_1':{'rich_text':[{'plain_text':'Title'}]}},
          {'type':'paragraph','paragraph':{'rich_text':[{'plain_text':'See '},{'type':'mention','mention':{'type':'page','page':{'id':'abc'}},'plain_text':'Login'}]}}]
print(blocks_to_md(blocks, {'abc':'login'}))
"
```
Expected: `My Auth Notes`, `['security', 'auth']`, `my-auth-notes`, then `# Title` and a paragraph `See [[login]]`.

- [ ] **Step 5: Commit.**

```bash
git add jarvis/notion_memory/notion.py pyproject.toml
git commit -m "feat(notes): Notion API client and markdown conversion"
```

---

## Task 7: One-shot LLM helper

**Files:**
- Create: `jarvis/notion_memory/llm.py`

**Interfaces:**
- Consumes: `Config` (uses `cfg.models.sync` role + `cfg.codex_bin`).
- Produces: `run_llm(cfg, prompt: str, stdin: str) -> str`.

- [ ] **Step 1: Implement.** `jarvis/notion_memory/llm.py`:

```python
"""One-shot Claude/Codex call reusing jarvis's cheap read-only `sync` model role."""
from __future__ import annotations

import subprocess


def run_llm(cfg, prompt: str, stdin: str) -> str:
    role = cfg.models.sync
    if role.provider == "claude":
        cmd = ["claude", "-p", "--model", role.claude.model, prompt]
    else:
        c = role.codex
        cmd = [cfg.codex_bin, "exec", "--model", c.model,
               "--sandbox", "read-only", "--skip-git-repo-check"]
        if c.ephemeral:
            cmd.append("--ephemeral")
        cmd.append(prompt)
    try:
        out = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=240)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return f"[notes ask] LLM call failed: {exc}"
    return out.stdout.strip() or "[notes ask] empty response."
```

- [ ] **Step 2: Verify (import only — avoids spending an LLM call).**

Run: `cd /Users/mohammedhasan/Github/Personal/jarvis && python -c "from jarvis.notion_memory.llm import run_llm; print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Commit.**

```bash
git add jarvis/notion_memory/llm.py
git commit -m "feat(notes): one-shot LLM helper for ask"
```

---

## Task 8: Command entrypoints (sync / query / ask)

**Files:**
- Create: `jarvis/notion_memory/command.py`

**Interfaces:**
- Consumes: `Vault`, `HybridSearch`, `NotionClient` + helpers, `run_llm`, `Config`.
- Produces: `notes_sync(cfg) -> str`, `notes_query(cfg, query: str, as_json: bool, k: int) -> str`, `notes_ask(cfg, question: str, k: int) -> str`.

- [ ] **Step 1: Implement.** `jarvis/notion_memory/command.py`:

```python
"""jarvis notes: sync (Notion -> vault), query (ranked search), ask (RAG answer)."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .llm import run_llm
from .notion import (
    NotionClient, blocks_to_md, page_tags, page_title, slugify,
)
from .retrieval import HybridSearch
from .vault import Vault


def notes_sync(cfg) -> str:
    token = os.environ.get(cfg.notion_memory.token_env)
    if not token:
        return (f"Set ${cfg.notion_memory.token_env} to your Notion integration token "
                "(https://www.notion.so/my-integrations) and share pages with the integration.")
    vault: Path = cfg.notion_memory.vault
    vault.mkdir(parents=True, exist_ok=True)
    state_path = vault / ".notion/state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    last_sync = state.get("last_sync")
    id2slug: dict = state.get("id2slug", {})

    client = NotionClient(token)
    changed: list[dict] = []
    newest = last_sync
    for page in client.search_pages():
        edited = page.get("last_edited_time", "")
        if last_sync and edited <= last_sync:
            break  # results are last_edited descending; the rest are older
        changed.append(page)
        id2slug[page["id"]] = slugify(page_title(page))
        if newest is None or edited > newest:
            newest = edited

    written = 0
    for page in changed:
        title = page_title(page)
        slug = id2slug[page["id"]]
        body = blocks_to_md(client.blocks(page["id"]), id2slug)
        frontmatter = {
            "title": title,
            "tags": ["notion", *page_tags(page)],
            "memory_kind": "semantic",
            "event_time": page.get("created_time"),
            "ingestion_time": datetime.now(timezone.utc).isoformat(),
            "notion_id": page["id"],
            "source": "notion",
        }
        md = ("---\n" + yaml.safe_dump(frontmatter, sort_keys=False) + "---\n\n"
              + f"# {title}\n\n" + body + "\n")
        (vault / f"{slug}.md").write_text(md)
        written += 1

    state.update({"last_sync": newest or last_sync, "id2slug": id2slug})
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2))
    return f"Synced {written} Notion page(s). Vault: {vault}"


def _snippet(path: str, terms: list[str]) -> str:
    try:
        for line in Path(path).read_text().splitlines():
            low = line.lower()
            if line.strip() and any(t in low for t in terms):
                return line.strip()[:160]
    except OSError:
        pass
    return ""


def notes_query(cfg, query: str, as_json: bool, k: int) -> str:
    vault = Vault.load(cfg.notion_memory.vault)
    hits = HybridSearch(vault).search(query, k)
    if as_json:
        return json.dumps(
            [{"note": h.note, "score": round(h.score, 4), "match": h.match_type, "path": h.path}
             for h in hits],
            indent=2,
        )
    if not hits:
        return "No matches."
    terms = [t for t in query.lower().split() if t]
    blocks = []
    for h in hits:
        snippet = _snippet(h.path, terms)
        blocks.append(f"[{h.score:.3f} {h.match_type}] {h.note}\n  {h.path}\n  {snippet}")
    return "\n\n".join(blocks)


def notes_ask(cfg, question: str, k: int) -> str:
    vault = Vault.load(cfg.notion_memory.vault)
    hits = HybridSearch(vault).search(question, k)
    if not hits:
        return "No relevant notes found. Run `jarvis notes sync` first."
    context = "\n\n---\n\n".join(_safe_read(h.path) for h in hits)
    prompt = (
        "Answer the question using ONLY the notes provided on stdin. "
        "Cite the note titles you used. If the notes do not contain the answer, say so plainly.\n\n"
        f"Question: {question}"
    )
    return run_llm(cfg, prompt, context)


def _safe_read(path: str) -> str:
    try:
        return Path(path).read_text()
    except OSError:
        return ""
```

- [ ] **Step 2: Verify (query path, offline).**

Run:
```bash
cd /Users/mohammedhasan/Github/Personal/jarvis && python -c "
import tempfile, pathlib
from jarvis.config import Config
from jarvis.notion_memory import command as nm
cfg = Config.load()
d = pathlib.Path(tempfile.mkdtemp())
(d/'auth.md').write_text('---\ntags: [auth]\nmemory_kind: semantic\ningestion_time: 2026-07-24T00:00:00+00:00\n---\n\n# Auth\n\nLogin token flow.')
cfg.notion_memory.vault = d
print(nm.notes_query(cfg, 'login token', False, 5))
print(nm.notes_query(cfg, 'login token', True, 5))
"
```
Expected: plaintext block naming `auth` with a snippet, then a JSON array with one object. No exception.

- [ ] **Step 3: Commit.**

```bash
git add jarvis/notion_memory/command.py
git commit -m "feat(notes): sync/query/ask entrypoints"
```

---

## Task 9: CLI wiring + agent-facing docs

**Files:**
- Modify: `jarvis/cli.py`

**Interfaces:**
- Consumes: `notes_sync`, `notes_query`, `notes_ask` from Task 8.

- [ ] **Step 1: Register the subcommand.** In `main()` in `jarvis/cli.py`, after the `project` subparser block (around line 45), add:

```python
    notes = sub.add_parser("notes", help="Notion second brain: sync, query, ask")
    notes_sub = notes.add_subparsers(dest="notes_action")
    notes_sub.add_parser("sync", help="pull Notion pages into the notion vault (incremental)")
    nq = notes_sub.add_parser("query", help="ranked hybrid search over your notes")
    nq.add_argument("text")
    nq.add_argument("--json", action="store_true", help="machine-readable output for agents")
    nq.add_argument("-k", type=int, default=8, help="max results")
    na = notes_sub.add_parser("ask", help="answer a question from your notes via Claude/Codex")
    na.add_argument("text")
    na.add_argument("-k", type=int, default=8, help="notes to retrieve as context")
```

- [ ] **Step 2: Dispatch.** In the `if command in (...)` dispatch chain, add before the final `ui` branch:

```python
    elif command == "notes":
        _notes(args)
```

- [ ] **Step 3: Handler.** Add this function (near `_sync`):

```python
def _notes(args) -> None:
    from .notion_memory import command as nm

    cfg = Config.load()
    action = getattr(args, "notes_action", None) or "query"
    if action == "sync":
        print(nm.notes_sync(cfg))
    elif action == "query":
        print(nm.notes_query(cfg, args.text, args.json, args.k))
    elif action == "ask":
        print(nm.notes_ask(cfg, args.text, args.k))
    else:
        print("usage: jarvis notes [sync|query <text>|ask <text>]")
```

- [ ] **Step 4: Verify CLI parses.**

Run:
```bash
cd /Users/mohammedhasan/Github/Personal/jarvis && python -m jarvis.cli notes query "hello world" --json
```
Expected: prints `[]` (empty JSON — no vault yet) or results if a vault exists; no traceback. Also run `python -m jarvis.cli notes --help` and confirm `sync`, `query`, `ask` are listed.

- [ ] **Step 5: Live end-to-end (manual, needs token).** With `export NOTION_TOKEN=secret_...` and at least one page shared to the integration:

```bash
jarvis notes sync
jarvis notes query "some topic in your notes"
jarvis notes ask "what did I note about some topic?"
```
Expected: `sync` reports N pages and writes `.md` files under `~/Github/Personal/notion-brain`; `query` returns ranked hits; `ask` returns a prose answer citing note titles.

- [ ] **Step 6: Commit.**

```bash
git add jarvis/cli.py
git commit -m "feat(notes): wire jarvis notes CLI subcommand"
```

---

## How Claude/Codex query this

Both agents call the CLI via their shell tool:

```bash
jarvis notes query "auth token expiry" --json     # structured hits for tool use
jarvis notes ask "how did I set up auth?"          # prose answer
```

Add a one-line pointer to your Claude/Codex project instructions (e.g. `CLAUDE.md`): *"For personal Notion knowledge, run `jarvis notes query \"...\" --json`."* (Do this manually — outside this plan's scope.)

## Deferred to v2 (out of scope)

- **Access-reinforcement**: bump `access_count` in frontmatter on each retrieval so used notes resist decay. Needs a write path on the read command; skipped for v1.
- **Role-aware retrieval weights** (planner/worker/reviewer engine bias): v1 uses neutral weights.
- **Deletion/tombstoning** of Notion pages removed upstream: v1 only adds/updates.
- **Nested block children** (toggles, sub-lists): v1 converts top-level blocks only.
