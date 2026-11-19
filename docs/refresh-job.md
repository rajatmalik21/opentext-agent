# Scheduled refresh job (design; manual in the PoC)

**Command:** `python -m ota.cli --db <path> ingest --source live` (or `fixtures`).

**Cadence:** daily off-peak for a PoC; the cache TTL (1h) prevents repeated hits inside one run.

**Behaviour**
- Idempotent: unchanged records are only "touched" (fetched_at); changed ones replace their chunks atomically.
- Partial failure keeps what was fetched and reports `complete=false` plus errors. A first-page failure exits non-zero (exit 2 on access errors).
- Records that disappear upstream are NOT deleted yet (gap: needs a tombstone/last-seen sweep after a *complete* run).

**Production shape (Phase 4/5):** Azure Container Apps Job on a cron trigger, managed identity, writes to the shared index; emits the JSON report as a structured log; alert on `complete=false`, `rejected` spikes (schema drift) or 2 consecutive failures.
