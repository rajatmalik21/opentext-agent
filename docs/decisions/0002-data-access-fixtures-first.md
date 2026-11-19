# ADR-0002: Fixtures-first data access, polite live client

**Status:** Accepted (user chose Option B)

## Context
Observed 2026-11-17: the products page is a JS shell (product list not in server HTML); the
search API returned a 4xx to an automated request, cause unknown (auth, headers, wildcard?).
`robots.txt` (supplied by the user) allows `/` and disallows only URLs containing a `referrer`
query parameter. Terms of use were not reviewed.

## Decision
- The pipeline runs against recorded fixtures by default (`FixtureClient`). A live `SearchClient` exists
  and is exercised by tests with fake sessions only.
- The live client identifies itself, enforces robots.txt (own matcher, because the stdlib one ignores `*`),
  rate-limits (1 req/s), caches, retries only 429/5xx/timeouts with backoff and Retry-After, and treats any other 4xx
  or non-JSON reply as an access error that is reported, never retried or bypassed.
- Fixtures in the repo are SYNTHETIC: real public OpenText product names, invented generic articles, URLs on the reserved `fixtures.invalid` host, and a runtime
  "synthetic fixture" warning on any answer that cites them. They test mechanics, not OpenText facts.

## Access boundary (confirmed by the candidate, 2026-11-19)
The candidate confirmed that the live `my.opentext.com` support search endpoint is **gated behind an authenticated customer-portal session**. (This is the candidate's report; the assistant only observed a 4xx
to an unauthenticated automated request and could not verify the cause.) Consequences:
- The agent cannot, and must not, ingest live content without sanctioned credentials. No cookies, session tokens or scraping workarounds are used or documented; `SearchClient` stops on any 4xx.
- A production version needs one of: (a) a documented partner/service API with a service identity, (b) an OpenText-provided content export, or (c) end-user delegated access (OAuth on behalf of the signed-in customer) so that answers respect each user's entitlements.
  This choice drives the identity design in `docs/architecture-service-management.md`.
- Entitlement matters: support content is plausibly customer-specific; any shared index must enforce per-user/per-tenant security trimming (ASSUMPTION: not verified).
- The live ingestion path (`ingest --source live`) is implemented and unit-tested with fake sessions only; it has never run against the real service.

## Alternatives considered
- Scrape with a headless browser: rejected; circumvents the access model and is brittle.
- Reuse a browser session cookie: rejected; bypasses authentication.
- Ask OpenText for sanctioned/API access: best long-term option (Option C); noted as a production gap.

## Consequences
- Agent answers in the PoC are only as real as the fixtures. Real data requires the user to capture a legitimate
  response (secrets removed) and drop it in as fixtures. Evaluation (Phase 3) must say which corpus was used.
- ASSUMPTIONS flagged in code: response field names, `start` is 1-based, pagination by `start + maxResults`.
