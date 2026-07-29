"""jarvis notes: query (ranked search), ask (RAG answer)."""
from __future__ import annotations

import json
from pathlib import Path

from .llm import run_llm
from .retrieval import HybridSearch
from .vault import Vault


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
        return f"No relevant notes found in {cfg.notion_memory.vault}."
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
