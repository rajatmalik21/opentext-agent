"""Embedder interface plus a dependency-free default.

HashingEmbedder is feature hashing over word unigrams and bigrams. It is deterministic,
offline and fast, but it is lexical, NOT semantic: it captures word overlap, not meaning.
It exists so the pipeline, tests and CI run without a model download or API key. Swap in
a real embedding model (e.g. an Azure-hosted one) behind the same interface for production.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from typing import Protocol

import numpy as np

_TOKEN = re.compile(r"[a-z0-9]+")


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: Sequence[str]) -> np.ndarray: ...


class HashingEmbedder:
    def __init__(self, dim: int = 512) -> None:
        self.dim = dim
        self.name = f"hashing-v1-{dim}"
        # Cosine between UNRELATED signed-hash vectors is ~N(0, 1/dim). Similarities below
        # 3 standard deviations are indistinguishable from collision noise, so retrieval must
        # not treat them as matches. Derived from theory, then checked empirically
        # (docs/evaluation.md): measured sd ~0.034 vs 1/sqrt(512) = 0.044.
        self.noise_floor = 3.0 / dim**0.5

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, text in enumerate(texts):
            toks = _TOKEN.findall(text.lower())
            feats = toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:], strict=False)]
            for f in feats:
                n = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "big")
                out[i, n % self.dim] += 1.0 if (n >> 63) & 1 else -1.0
            norm = float(np.linalg.norm(out[i]))
            if norm > 0:
                out[i] /= norm
        return out
