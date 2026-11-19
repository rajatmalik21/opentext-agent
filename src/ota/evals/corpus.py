"""Evaluation corpora: the fixtures, optionally plus synthetic DISTRACTOR documents.

The fixture corpus has only ~10 documents, so almost any retriever looks perfect on it. The
distractor generator adds N deterministic synthetic documents that reuse the same generic
vocabulary (configure, upgrade, patch, identity provider, mailbox connector...) but NOT the
discriminating terms of the gold documents (saml, ldap, 24.2, 25.1, 22.x...), to make ranking
non-trivial. They are NOT real support content and only exist to stress retrieval.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

from ..ingest.client import FetchResult, FixtureClient
from ..ingest.pipeline import ingest
from ..retrieval.embedder import Embedder, HashingEmbedder
from ..store.sqlite_store import SQLiteStore

PRODUCTS = ["OpenText Content Cloud", "OpenText Service Management", "OpenText Extended ECM",
            "Acme Analytics", "Acme Archive", "Acme Workflow"]  # last three are invented
VERBS = ["Configure", "Upgrade", "Troubleshoot", "Monitor", "Tune", "Secure", "Migrate",
         "Integrate", "Review", "Plan"]
TOPICS = ["portal access", "report templates", "index service", "ticket routing", "license usage",
          "release schedule", "patch process", "repository storage", "service accounts", "user roles",
          "audit logs", "notification rules", "identity provider settings", "mailbox connector",
          "migration checklist", "performance counters", "software updates", "backup schedule"]
TYPES = ["Documentation", "Knowledge Base", "FAQ", "Release Notes", "Lifecycle"]
SENTENCES = [
    "Describes the recommended approach for {t}.",
    "Covers common causes and how to diagnose problems with {t}.",
    "Includes checks to complete before changing {t}.",
    "Explains how {t} behaves after an upgrade or patch.",
    "Lists settings that affect {t} and their defaults.",
    "Review this guide when planning changes to {t}.",
]
# Terms that identify gold documents; distractors must never contain them.
RESERVED_TERMS = ("saml", "ldap", "kerberos", "24.2", "25.1", "22.x", "scim")


def distractor_items(n: int, seed: int = 7) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    items: list[dict[str, Any]] = []
    for i in range(n):
        verb, topic, product = rng.choice(VERBS), rng.choice(TOPICS), rng.choice(PRODUCTS)
        s1, s2 = rng.sample(SENTENCES, 2)
        items.append({
            "id": f"dis-{i:04d}",
            "title": f"{verb} {topic} for {product}",
            "description": f"{s1.format(t=topic)} {s2.format(t=rng.choice(TOPICS))}",
            "type": rng.choice(TYPES), "product": product, "lastModified": "2026-03-01",
            "url": f"https://fixtures.invalid/synthetic/dis-{i:04d}",
        })
    return items


class ListClient:
    """In-memory PageSource over a list of search-API-shaped items."""

    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.items = items

    def fetch_page(self, start: int, max_results: int, text: str = "*") -> FetchResult:
        chunk = self.items[start - 1 : start - 1 + max_results]
        return FetchResult(f"list://{start}", {"total": len(self.items), "results": chunk}, True)


def build_store(
    fixtures_dir: str | Path, n_distractors: int = 0, seed: int = 7, embedder: Embedder | None = None,
) -> SQLiteStore:
    emb = embedder or HashingEmbedder()
    store = SQLiteStore()  # in-memory: evaluation never touches the serving index
    ingest(FixtureClient(Path(fixtures_dir)), store, emb)
    if n_distractors:
        ingest(ListClient(distractor_items(n_distractors, seed)), store, emb,
               page_size=50, max_pages=10_000, source="synthetic-distractors", sweep=False)
    return store
