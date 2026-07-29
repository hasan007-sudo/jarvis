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
