# ADR-0001: Stack and hosting

**Status:** Accepted (user confirmed: Azure, Claude as model provider)

## Context
Take-home PoC for an AI agent over support content, with IaC and CI, judged on architecture
judgment and operability. The target role is Microsoft 365 / Copilot oriented.

## Decision
- Python 3.12+, Pydantic for typed records, SQLite for storage. FastAPI and the Anthropic SDK arrive in Phase 2.
- Model: Claude via API behind a `ModelClient` interface (provider swappable).
- Hosting: Azure Container Apps, with managed identity and Key Vault. IaC in Terraform (Phase 4).
- CI: GitHub Actions with OIDC to Azure (no long-lived keys).

## Alternatives considered
- AWS App Runner / GCP Cloud Run: equally viable technically; Azure chosen for Entra ID / M365 alignment, which Parts 2 and 3 build on.
- TypeScript/Node: fine, but Python has the stronger eval and data tooling.
- Azure OpenAI as the model: possible behind the same interface; Claude requested.

## Consequences
- Entra-based identity story carries straight into Part 2.
- Claude is an external provider call from Azure: data-egress and provider terms must be covered in the security section.
- Build sandbox had no package-install access, so Phase 1 uses only `requests`, `pydantic`, `numpy`, stdlib `sqlite3`.
  `httpx`, `fastapi`, `anthropic`, `ruff` are declared under optional extras but NOT exercised yet.
