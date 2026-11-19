"""Hybrid retrieval: BM25 (FTS5) + cosine over embeddings, fused with Reciprocal Rank Fusion.

RRF is rank-based, so it needs no score calibration between BM25 and cosine.
Results are collapsed to one hit (best chunk) per source URL.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

import numpy as np

from ..store.sqlite_store import SQLiteStore
from .embedder import Embedder

Mode = Literal["hybrid", "bm25", "vector"]
_WORD = re.compile(r"\w+", re.UNICODE)


@dataclass
class Hit:
    chunk_id: int
    url: str
    title: str
    product: str | None
    resource_type: str | None
    text: str
    score: float
    bm25_rank: int | None
    vector_rank: int | None


STOPWORDS = frozenset(
    "a an and are as at be by can do does for from has have how i if in is it its me my of on or "
    "our so than that the their then there these this to us was we what when where which who why "
    "will with would you your".split()
)


def fts_query(query: str) -> str | None:
    # Stopwords are dropped so a question like "what is the weather in Paris" cannot match
    # every document that merely contains "the" / "in". 1-char tokens are dropped too.
    toks = [t for t in _WORD.findall(query.lower()) if len(t) > 1 and t not in STOPWORDS]
    if not toks:
        return None
    return " OR ".join(f'"{t}"' for t in dict.fromkeys(toks))  # quoted: no FTS operator injection


class HybridIndex:
    def __init__(
        self,
        store: SQLiteStore,
        embedder: Embedder,
        rrf_k: int = 60,
        min_vector_score: float | None = None,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.rrf_k = rrf_k
        # Default: the embedder's own noise floor (a real embedding model would define its own).
        # Phase 3 history: the first version used a fixed 0.05, which sat inside the hashing
        # embedder's collision noise and made every question return vector hits.
        self.min_vector_score = (
            float(getattr(embedder, "noise_floor", 0.0)) if min_vector_score is None else min_vector_score
        )

    def _bm25(self, query: str, n: int) -> list[int]:
        q = fts_query(query)
        return [cid for cid, _ in self.store.fts_search(q, n)] if q else []

    def _vector(self, query: str, n: int) -> list[int]:
        ids, mat = self.store.load_vectors()
        if ids.size == 0:
            return []
        qv = self.embedder.embed([query])[0]
        sims = mat @ qv
        order = np.argsort(-sims)[:n]
        return [int(ids[i]) for i in order if sims[i] >= self.min_vector_score]

    def search(self, query: str, k: int = 5, fetch_k: int = 20, mode: Mode = "hybrid") -> list[Hit]:
        bm = self._bm25(query, fetch_k) if mode in ("hybrid", "bm25") else []
        ve = self._vector(query, fetch_k) if mode in ("hybrid", "vector") else []
        bm_rank = {cid: r for r, cid in enumerate(bm, start=1)}
        ve_rank = {cid: r for r, cid in enumerate(ve, start=1)}
        scores: dict[int, float] = {}
        for ranks in (bm_rank, ve_rank):
            for cid, r in ranks.items():
                scores[cid] = scores.get(cid, 0.0) + 1.0 / (self.rrf_k + r)
        ordered = sorted(scores, key=lambda c: scores[c], reverse=True)
        chunks = self.store.get_chunks(ordered)
        hits: list[Hit] = []
        seen: set[str] = set()
        for cid in ordered:
            c = chunks.get(cid)
            if c is None or c["url"] in seen:
                continue
            seen.add(c["url"])
            hits.append(
                Hit(cid, c["url"], c["title"], c["product"], c["resource_type"], c["text"],
                    scores[cid], bm_rank.get(cid), ve_rank.get(cid))
            )
            if len(hits) >= k:
                break
        return hits
