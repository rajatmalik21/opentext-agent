# ADR-0007: Deployment and CI

**Status:** Accepted. **Everything it describes is UNVALIDATED**: the Dockerfile, Terraform and workflows were written in a sandbox with no network, no Docker daemon, no Terraform and no GitHub.
Only `tests/test_infra_files.py` (structural checks) and `tools/scan_secrets.py` run here.

## Decisions
- **Azure Container Apps**, one user-assigned managed identity (AcrPull on ACR, Key Vault Secrets User on the vault). No registry admin user, no stored cloud credentials.
- **Secrets:** the Anthropic key lives in Key Vault, is set out of band, and is injected as a Container Apps secret reference. Terraform never holds its value (it would persist in state).
- **Default is the offline extractive model.** `enable_claude=false` means a reviewer can deploy without any key. Claude is an explicit switch.
- **Internal ingress by default** (`external_ingress=false`). The PoC server has no TLS of its own, no rate limit and no per-user identity; exposing it needs a front door with Entra authentication first.
- **Two-step first deploy** (`deploy_app=false`, push image, `deploy_app=true`) because the app needs an image that does not exist until ACR does.
- **Image:** multi-stage, non-root (uid 10001), pinned minor tag, healthcheck, no secrets, index built at image-build time from SYNTHETIC fixtures. The index is read-only at runtime, so `readOnlyRootFilesystem` is compatible.
- **CI (`ci.yml`)**: `pytest` and `mypy` on Python 3.12 and 3.13; offline eval smoke; pip-audit; regex secret scan; `terraform fmt/validate`; trivy config and image scan; docker build with a container smoke test. Permissions are `contents: read`; no cloud secrets.
- **Deploy (`deploy.yml`)** is manual only, uses OIDC federation, and relies on environment required-reviewers for approval.
- ruff is **advisory** (`continue-on-error`) because it has never run on this code. Remove that once it is clean.

## Known gaps (production-readiness)
- Public ACR and Key Vault endpoints; no VNet, private endpoints, WAF or API gateway.
- Basic ACR (no geo-replication, no content trust); base image not pinned by digest; actions pinned by tag, not SHA.
- No remote-state bootstrap, no plan-on-PR job, no drift detection, no per-environment promotion.
- Alerts cover restarts and budget only; no SLO-based alerting (see `docs/architecture-service-management.md`).
- Claude calls leave Azure to the provider API. Data egress, residency and provider terms need a decision before real content is used.
- azurerm 4.x attribute names were written from memory of the provider schema; `terraform validate` is the check.
- The container image has not been built; the build-time ingest step is untested.

## Alternatives considered
- AKS: more control, much more to operate for a PoC.
- Azure OpenAI instead of Claude: possible behind the same `ModelClient` interface (ADR-0001).
- Bicep: native Azure, but Terraform is more portable across the OpenText-style multi-cloud estates.

## Future work
Digest-pinned images, SHA-pinned actions, SBOM and image signing, plan-on-PR with a comment, private networking, and a staging environment that runs the live eval gate before promotion.
