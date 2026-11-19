"""Retrieval evaluation: BM25 vs vector vs hybrid (RRF), and the stopword-filter ablation.

Offline and deterministic. The vector side uses the HASHING embedder (lexical, not semantic),
so "vector" here measures feature-hashed word overlap, not a neural embedding model.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..retrieval import hybrid as hybrid_mod
from ..retrieval.embedder import HashingEmbedder
from ..retrieval.hybrid import HybridIndex, Mode
from ..store.sqlite_store import SQLiteStore
from .corpus import build_store
from .dataset import Question

MODES: tuple[Mode, ...] = ("bm25", "vector", "hybrid")


@contextmanager
def no_stopwords() -> Iterator[None]:
    """Temporarily disable the BM25 stopword filter (to measure its effect)."""
    saved = hybrid_mod.STOPWORDS
    hybrid_mod.STOPWORDS = frozenset()
    try:
        yield
    finally:
        hybrid_mod.STOPWORDS = saved


def _metrics(ranks: list[int | None]) -> dict[str, Any]:
    n = len(ranks)

    def at(k: int) -> int:
        return sum(1 for r in ranks if r is not None and r <= k)

    return {"n": n, "hit@1": at(1), "hit@3": at(3), "hit@5": at(5),
            "mrr": round(sum(1 / r for r in ranks if r) / n, 4) if n else None}


def evaluate_modes(questions: list[Question], store: SQLiteStore, k: int = 10) -> dict[str, Any]:
    idx = HybridIndex(store, HashingEmbedder())
    gold_qs = [q for q in questions if q.gold_urls]
    out: dict[str, Any] = {}
    for mode in MODES:
        ranks: dict[str, int | None] = {}
        for q in gold_qs:
            hits = idx.search(q.question, k=k, fetch_k=60, mode=mode)
            ranks[q.id] = next((i for i, h in enumerate(hits, 1) if h.url in q.gold_urls), None)
        direct = [ranks[q.id] for q in gold_qs if not q.paraphrase]
        para = [ranks[q.id] for q in gold_qs if q.paraphrase]
        out[mode] = {
            "all": _metrics(list(ranks.values())), "direct": _metrics(direct), "paraphrase": _metrics(para),
            "missed_at_5": sorted(i for i, r in ranks.items() if r is None or r > 5),
        }
    return out


def spurious_retrieval(questions: list[Question], store: SQLiteStore) -> dict[str, Any]:
    """Share of unanswerable/out-of-scope questions for which retrieval still returns results."""
    idx = HybridIndex(store, HashingEmbedder())
    qs = [q for q in questions if q.category in ("unanswerable", "out_of_scope")]
    out: dict[str, Any] = {}
    for mode in ("bm25", "hybrid"):
        hit_ids = [q.id for q in qs if idx.search(q.question, k=5, fetch_k=60, mode=mode)]
        out[mode] = {"n": len(hit_ids), "of": len(qs), "ids": hit_ids}
    return out


FLOORS = (0.0, 0.05, 0.10, 0.1325, 0.20, 0.30)


def vector_floor_sweep(questions: list[Question], store: SQLiteStore) -> dict[str, Any]:
    """Hybrid retrieval as the vector similarity floor varies. 0.05 = the original setting."""
    gold_qs = [q for q in questions if q.gold_urls]
    abst = [q for q in questions if q.category in ("unanswerable", "out_of_scope")]
    out: dict[str, Any] = {}
    for floor in FLOORS:
        idx = HybridIndex(store, HashingEmbedder(), min_vector_score=floor)
        ranks = []
        for q in gold_qs:
            hits = idx.search(q.question, k=10, fetch_k=60, mode="hybrid")
            ranks.append(next((i for i, h in enumerate(hits, 1) if h.url in q.gold_urls), None))
        spurious = sum(1 for q in abst if idx.search(q.question, k=5, fetch_k=60, mode="hybrid"))
        out[str(floor)] = {**_metrics(ranks), "spurious": spurious, "spurious_of": len(abst)}
    return out


def run_retrieval_suite(
    fixtures_dir: str | Path, questions: list[Question], distractor_sizes: list[int],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for n in distractor_sizes:
        store = build_store(fixtures_dir, n)
        entry: dict[str, Any] = {
            "documents": store.count_records(), "modes": evaluate_modes(questions, store),
            "spurious_with_stopwords": spurious_retrieval(questions, store),
            "vector_floor_sweep": vector_floor_sweep(questions, store),
        }
        with no_stopwords():
            entry["spurious_without_stopwords"] = spurious_retrieval(questions, store)
            entry["modes_without_stopwords"] = evaluate_modes(questions, store)
        result[f"distractors_{n}"] = entry
    return result
