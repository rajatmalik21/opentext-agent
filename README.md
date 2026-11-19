# OpenText support-content agent (assessment PoC)

> **Status:** Phases 0-5 are written. Parts 1-3 of the assessment are in this repo. Deployment IaC and CI are **UNVALIDATED** (never run: no network, Docker daemon, Terraform or GitHub where this was built). No live Claude result exists.

## Assessment deliverables

| Part | What | Where |
|---|---|---|
| **1** | Working agent: ingestion, retrieval, guardrails, evaluation, Dockerfile, Terraform, CI | this repo (`src/`, `evals/`, `infra/`, `.github/`) |
| **2** | **Target architecture for OpenText Service Management** | [`docs/architecture-service-management.md`](docs/architecture-service-management.md) |
| **3** | **Microsoft 365 modernization and Copilot readiness assessment** | [`docs/m365-copilot-readiness.md`](docs/m365-copilot-readiness.md) |
| | Evaluation report (offline vs live kept separate) | [`docs/evaluation.md`](docs/evaluation.md) |
| | Decisions (ADR-0001 to 0008) and session log | [`docs/decisions/`](docs/decisions/), [`docs/session-notes.md`](docs/session-notes.md) |

A small grounded Q&A agent over support-resource content: ingests a support search API, indexes it (SQLite FTS5 BM25, optional hashed vectors), and answers with **citations**, an explicit **"not found"** path, and **prompt-injection defences** enforced in code. Claude is the model provider behind a swappable interface.

## Read this before trusting any number

- **The corpus is synthetic.** The real my.opentext.com search API returned a client error to an automated request and the products page is JavaScript-rendered, so no real OpenText content has been ingested. Fixtures use real public OpenText product names (Content Cloud, Service Management, Extended ECM) with **invented** content and URLs on the reserved `fixtures.invalid` host; answers citing them carry a "synthetic fixture" warning.
- **Access boundary.** Rajat reports that the live my.opentext.com support search is gated behind an authenticated customer-portal session (not verified by the assistant). The agent therefore stays fixtures-first and uses no cookies, tokens or scraping workarounds. Production needs a sanctioned route (service API, export, or delegated user access). See `docs/decisions/0002-data-access-fixtures-first.md`.
- **Offline numbers are not live numbers.** `offline-stub` and `offline-scripted` results come from deterministic stand-ins, not from Claude. **No live Claude result exists yet**; `ClaudeClient` has never called the real API (only a faked-HTTP test). `docs/evaluation.md` explains exactly what each number means, and result files carry `meta.kind` (`offline-stub` | `offline-scripted` | `offline-retrieval` | `live-api`).

## Quickstart (offline, no network, no key)

Needs Python 3.12+ with `requests`, `pydantic`, `numpy` (and `pytest`, `mypy` for checks). Package installs were unavailable where this was built.

```
export PYTHONPATH=src
python -m pytest -q                                   # 126 tests
python -m mypy src
python -m ota.cli ingest                              # index the synthetic fixtures into data/index.db
python -m ota.cli ask "How do I configure SAML single sign-on?"
python -m ota.cli serve --port 8080                   # POST /ask, GET /healthz, GET /readyz
```
`ask`/`serve` default to the offline extractive stub. For Claude: `export ANTHROPIC_API_KEY=...` and add `--model claude` (unverified, see above).

## Evaluation

```
python -m ota.evals retrieval                         # BM25 vs vector vs hybrid; stopword and floor ablations
python -m ota.evals matrix                            # offline: 4 models x {guarded, trusting} agents
# LIVE (costs money; sends question text + retrieved content to the provider):
python -m ota.evals agent --model claude --agent guarded --confirm-live --delay 1
```
Headline offline findings (details, tables, limits in [`docs/evaluation.md`](docs/evaluation.md)):
- Hybrid retrieval did **not** beat BM25 with the lexical hashing embedder, so the default is `bm25` (ADR-0006).
- A vector floor inside embedder noise made hybrid retrieve for nearly every no-answer question; fixed (spurious retrieval on no-answer questions 9 to 5 of 10 on the small corpus).
- Against simulated misbehaving models, the guarded agent delivered 0 unsafe outputs and 0 invalid citations where the prompt-only baseline delivered up to 11 unsafe outputs and 16 invalid citations in the worst simulated cases. It cannot catch **wrong-but-sourced** answers; only the model's judgment can, which the live run must measure.

## Layout
```
src/ota/ingest      client (polite, robots-aware), normalize, pipeline (idempotent + safe sweep)
src/ota/retrieval   embedder, hybrid BM25/vector/RRF
src/ota/store       SQLite store
src/ota/agent       agent loop, guardrails, tools, Claude REST client, offline stub
src/ota/evals       dataset, metrics, scripted models, runner, retrieval ablations, CLI
evals/              dataset.jsonl, results/*.json
fixtures/           robots.txt (real) + SYNTHETIC search fixtures
docs/               evaluation.md, architecture-service-management.md, m365-copilot-readiness.md, decisions/ (ADR-0001..0008), refresh-job.md, session-notes.md
infra/terraform/    Azure Container Apps IaC (UNVALIDATED)
.github/            ci.yml, deploy.yml, dependabot.yml (UNVALIDATED)
Dockerfile, Makefile, requirements*.txt
```

## Deployment and CI (UNVALIDATED)

`Dockerfile` (multi-stage, non-root, synthetic index built in), `infra/terraform/` (Container Apps, ACR, Key Vault, managed identity, Log Analytics; see its README for the two-step first deploy) and `.github/workflows/ci.yml` (**pytest and mypy** on 3.12/3.13, offline eval smoke, pip-audit, secret scan, terraform validate, trivy, docker smoke test). `deploy.yml` is manual, OIDC, environment-approved. None of it has run; `tests/test_infra_files.py` only checks structure. Decisions and gaps: ADR-0007. Local equivalents: `make test typecheck scan`.

## Known limitations (so far)
Synthetic corpus; unverified Claude client; small author-written eval set without held-out data; stdlib HTTP server without TLS, rate limiting or per-user identity; no retrieval-score abstention gate; injection mitigated, not solved (ADR-0005); linter not run (`ruff` unavailable offline).
