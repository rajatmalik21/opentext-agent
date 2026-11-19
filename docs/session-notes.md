# Session notes: decisions and corrections log

## Decisions (user-made)
- 2026-11-17: data access = fixtures first (Option B); cloud = Azure; model = Claude; no deadline.
- 2026-11-17: user supplied robots.txt (stored at `fixtures/robots.txt`).

## Findings
- Products page: JS-rendered shell. API URL: 4xx to automated fetch, schema unobserved. See ADR-0002.

## Corrections (self-corrections by the assistant)
- Phase 1, tests: three of my own tests were wrong, not the code. (1) The backoff test counted throttle sleeps as backoff sleeps; fixed by isolating the rate limiter. (2) The FTS-injection test expected a one-letter token that the tokenizer drops by design. (3) The chunk-overlap assertion was off by one (step 150, so overlap starts at w150). The code was left unchanged.

- Phase 2, tests found a real bug: the HTTP server used one SQLite connection across worker threads, so every `/ask` and `/readyz` crashed. Fixed with `check_same_thread=False` and a documented read-only-while-serving constraint (ADR-0005). The tests caught it; no code review did.
- Phase 2, I wrote an assertion ending in `or True` (could never fail) while drafting the sweep tests; caught and replaced before the first run.
- Phase 2, one sweep test had a wrong expectation (re-ingest re-adds a previously swept record). Code was right; test fixed.
- Phase 2: added a stopword filter to BM25 queries after reasoning that "what is the weather in Paris" would otherwise match any document containing "the"/"in".

- Phase 3, evaluation exposed a design bug in my own retrieval: the fixed vector floor (0.05) was inside the hashing embedder's noise, so hybrid retrieved for 10/10 no-answer questions. Fixed by deriving the floor from the embedder (3/sqrt(dim)); the floor was fixed from theory before I looked at the sweep.
- Phase 3, I changed the default retrieval mode from hybrid to bm25 after measuring (ADR-0006). My Phase 1 claim in ADR-0003 that hybrid was a good default was unsupported until measured.
- Phase 3, metric correction: `attack_success_rate` only counted adversarial/poisoned questions (hid leaks on benign ones, name overstated). Replaced by `unsafe_output_rate` over all questions plus lower-is-better invalid-citation and ungrounded rates.
- Phase 3, I misremembered one result: I believed the stub falsely answered O04 ("17 times 23"); the rows show BM25 retrieved a document (via "23" in "23.4") but the stub abstained. Its real misses are D07, R06, U01-U03, U05, X05, X06. Report uses the rows.
- Phase 3, small test/type fixes: wrong expectation about `canonical_url` root slash (code right, test wrong); a missing `set[str]` annotation for mypy.
- Phase 3 caveats I am carrying forward: dataset and system share one author; two decisions were made on the same data they were measured on; live Claude run not done.

## Decisions (user-made, Phase 3)
- Evaluate with stub + scripted models offline; provide a CLI for a live Claude run; keep README explicit about offline vs live numbers; compare BM25 vs hybrid and groundedness/citation accuracy; stay in this chat until Phase 3 is done, then zip.

## Decisions (user-made, Phase 2)
- Proceed with synthetic fixtures until a real payload is captured; stdlib HTTP server with FastAPI/Anthropic behind interfaces; `ask` command in the CLI; strict not-found and injection handling; account for deleted-record cleanup.

## Environment limits that shaped the work
- No package installs (network off): `httpx`, `fastapi`, `anthropic`, `ruff` unavailable here. Not used or run in Phase 1.

## Phase 4/5 (2026-11-19)
- User confirmed: live support endpoint is gated behind an authenticated portal session (user's report; not verified). Documented in ADR-0002 and README; fixtures-first retained.
- Fixtures renamed from "Acme" to real public OpenText product names, kept clearly synthetic (invented text, `fixtures.invalid` URLs, runtime warning). The rename broke 10 tests (hard-coded names/URLs; a `lookup_product("x")` that now matched "OpenText"); tests fixed, not code. Evaluation numbers changed (small-n noise made visible) and were re-run and rewritten in docs/evaluation.md.
- Future work recorded, not built, as decided with the user: strip-vs-reject citations and ingestion-time quarantine (ADR-0006).
- A delegated subagent drafting Part 3 hit an account rate limit (HTTP 429) before reporting; the file was on disk. I reviewed it, removed an odd heading and "candidate's assistant" wording, and validated its Mermaid with mmdc. I did not independently re-verify the cited Microsoft pages; Section 10 lists claims to re-check.
- Phase 4 files written but UNVALIDATED (no terraform/docker daemon/GitHub). Structural tests added. One of my infra tests was too crude (matched "secrets." inside "scan_secrets.py"); fixed the test.
- Part 2 states its OTSM API assumptions explicitly (A1-A8); I did not verify OTSM's API surface.
