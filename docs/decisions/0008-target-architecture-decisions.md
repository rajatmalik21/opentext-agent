# ADR-0008: Target architecture decisions (Part 2)

**Status:** Proposed. Design only; nothing here is built. Full context in `docs/architecture-service-management.md`. Assumptions A1-A8 there apply.

| # | Decision | Alternatives | Consequence / revisit when |
|---|---|---|---|
| D1 | Safety rules enforced in an agent runtime in code (delimiters, canary, id-only citations, forced not-found, output validation) | Prompt-only | Measured offline in `docs/evaluation.md`; re-measure live |
| D2 | Act on behalf of the signed-in user (delegated token); tenant and user from validated claims only | Shared service account | Needs OTSM delegation support (A2); otherwise reduce scope |
| D3 | Security-trimmed index with ACLs in the query; federate at query time for tightly entitled or fast-changing data | Index everything and trim after retrieval | Trim-after leaks via counts, ranking and latency; revisit if ACLs cannot be read (A3) |
| D4 | BM25 first; hybrid only with a semantic embedder and evidence | Hybrid default | ADR-0006 |
| D5 | Writes are schema-validated proposals executed with the approver's identity after human approval | Autonomous writes | Tiered autonomy only after measured accuracy |
| D6 | Model gateway with swappable providers; Claude first; retrieval-only fallback | Single provider | Residency customers may need an in-region or private model |
| D7 | Per-tenant index, per-region stamp | Shared multi-tenant index | Cost vs isolation; revisit at scale |
| D8 | Compare against OpenText Aviator and Microsoft Copilot options before building | Build by default | If Aviator meets the evaluated bar, extend it and build only gaps |
| D9 | Evaluation is a release gate (offline on every change, live on a schedule) plus production sampling | Manual testing | Dataset needs SME-written, held-out cases |

Open items carried from ADR-0006 (documented, not built): strip vs reject invalid citations; quarantine at ingestion.
