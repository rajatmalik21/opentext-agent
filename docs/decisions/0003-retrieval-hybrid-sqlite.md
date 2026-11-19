# ADR-0003: Hybrid retrieval on SQLite (FTS5 BM25 + hashed vectors, RRF)

**Status:** Accepted, **amended by ADR-0006 (Phase 3):** measured results did not support hybrid as the default with the hashing embedder; default mode is now `bm25` and the vector floor is derived from the embedder's noise floor. The text below records the original reasoning.

## Context
Small corpus (support listings), need grounded citations, no required external services in CI.

## Decision
- Store records, chunks, FTS5 index and float32 embeddings in one SQLite file.
- Rank with BM25 (FTS5, porter stemming) and cosine similarity, fused with Reciprocal Rank Fusion (k=60); collapse to one hit per URL.
- Default embedder is feature hashing over uni+bigrams: deterministic, offline, but LEXICAL not semantic.
- Embedder name/dim recorded in the index; mismatch is refused (forces a rebuild, avoids silent garbage).
- User queries are tokenized and each token double-quoted before reaching FTS5, so FTS operators cannot be injected.

## Alternatives considered
- Azure AI Search / pgvector: right for production scale and security trimming; overkill for the PoC. Swap path: implement the same `search()` contract.
- Real embedding model now: better semantics, but needs a key/network in CI and adds cost; defer behind the `Embedder` interface.

## Consequences
- Brute-force vector scan is O(chunks); fine to ~10^4-10^5 chunks, then move to an ANN index.
- Hashing embedder adds little over BM25 on paraphrased queries; Phase 3 will measure bm25 vs vector vs hybrid before claiming a benefit.
- No per-user security trimming yet; required before any non-public content (Part 2).
