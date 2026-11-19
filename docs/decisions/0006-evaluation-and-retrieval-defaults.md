# ADR-0006: Evaluation design and retrieval defaults

**Status:** Accepted. Amends ADR-0003 (retrieval) and extends ADR-0005 (guardrails).

## Context
ADR-0003 chose hybrid retrieval (BM25 + hashed vectors, RRF) before any measurement. Phase 3 built a 39-question
dataset and harness (`docs/evaluation.md`) and measured it.

## Decisions
1. **Default retrieval mode is `bm25`** (`OTA_RETRIEVAL_MODE`, `--retrieval-mode`). On this dataset hybrid never beat BM25
   (tie at hit@1 on 10 docs; 17/22 vs 18/22 on 310 docs, MRR 0.8106 vs 0.8409) and was equal or worse on spurious retrieval; the hashing embedder is lexical, so the vector side adds noise, not meaning.
   (One-question differences are noise; the point is that no evidence supports the extra complexity.)
   Hybrid/vector remain selectable. Revisit with a semantic embedder.
2. **The vector similarity floor belongs to the embedder** (`noise_floor = 3/sqrt(dim)`), replacing a fixed 0.05 that sat inside collision noise.
   Chosen from theory before viewing the sweep, then reported with the full sweep.
3. **Stopword filtering stays**: ranking unchanged, BM25 spurious retrieval on no-answer questions 9/10 to 5/10.
4. **Evaluation is deterministic and offline by default.** Result files carry `meta.kind` (`offline-stub`, `offline-scripted`, `offline-retrieval`, `live-api`);
   a live run requires `--confirm-live` and `ANTHROPIC_API_KEY`.
5. **A `trusting` ablation agent** (no output validation, no input screen) exists in the evaluation package only, to quantify what the guardrails add.
   It is never wired into the application.
6. **Metrics are rules, not an LLM judge**, so every number can be explained and challenged. Their limits are stated in the report.

## Alternatives considered
- Keep hybrid because it is "best practice": rejected, no supporting evidence here.
- Tune the floor on the dataset to maximise MRR: rejected as overfitting a 39-question set; the sweep is published instead.
- LLM-as-judge for groundedness: deferred; adds cost and a second source of error. Needed if lexical grounding proves too blunt on live outputs.

## Future work (decided with the candidate: documented, deliberately NOT built)
- **Strict reject vs strip bad citations.** Rejecting any answer with an invalid citation is safe but cost recall (17/22 to 12/22 for the sloppy-citer simulation).
  Option: strip unknown citation ids when at least one valid citation remains, and keep the answer. Not adopted: it should be decided with live-model evidence (real models may rarely emit bad ids, or may do so in ways that signal fabrication).
  Acceptance test when built: recall recovers on the simulation with `unsafe_output` still 0/39.
- **Quarantine at ingestion.** A single poisoned document reached 6 benign questions plus its own 2 and forced rejections (recall 12/22). Option: flag/quarantine records matching injection patterns for human review
  (would refine ADR-0004's "do not filter at ingestion", for obvious payloads only). Not built. Acceptance test when built: benign-question recall restored under the injectable simulation, quarantined records listed in the ingestion report.
- **No retrieval-score abstention gate yet.** In-domain unanswerable questions (U01-U03, U05) still retrieve real documents; only model judgment stops them. Live run decides whether a gate is needed.
- Results are small-n and author-written; see limitations in `docs/evaluation.md`.
