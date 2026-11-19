# ADR-0005: Agent design, enforcement in code, stdlib HTTP

**Status:** Accepted

## Decisions
1. **Provider-neutral `ModelClient`.** `ClaudeClient` (REST via `requests`) and `ExtractiveModel` (offline stub) implement it. The agent never imports a vendor SDK.
2. **"Not found" is enforced by code, not just the prompt.** The system prompt demands `found=false` when sources lack the answer, but the agent also forces `not_found` when the model claims an answer without any retrieved source, and replaces the model's text with a fixed message.
3. **Citations are ids, URLs come from the index.** The model cites `S1..Sn`. Ids must have been retrieved in this request. Any URL appearing in answer text must equal a retrieved source URL, otherwise the answer is rejected.
4. **Untrusted content is framed, not trusted.** Retrieved text and the user's question sit inside per-request random-nonce delimiters; delimiter look-alikes in content are neutralized; the prompt states these blocks are data. Leak markers (per-request canary, delimiter nonce, delimiter text) in an answer cause rejection.
5. **Cheap pre-screen** for obvious override/extraction phrasing in the user's question (no model call, status `refused`).
6. **Tool surface is minimal and typed:** `search_support_content`, `lookup_product`; pydantic validation with `extra=forbid`; per-request call cap; read-only.
7. **Stale index entries:** each source carries `stale` (fetched more than N days ago; unknown date counts as stale). Stale sources produce a warning in the response. Records deleted upstream are removed by the ingestion sweep (complete runs only, source-scoped, mass-removal guard of 50% on indexes of 10+ records).
8. **HTTP API on the stdlib** (`http.server`) because FastAPI cannot be installed in the build sandbox. Body-size cap, content-type check, optional `X-API-Key` (constant-time compare), no-store headers, `/healthz` (liveness) separate from `/readyz` (index + model).

## Known limits (be honest in the interview)
- Prompt injection is **mitigated, not solved.** Regex pre-screens are trivially evadable (paraphrase, other languages). The real controls are structural: read-only tools, no URL/egress from model text, id-only citations, output validation. A compromised model can still produce a *misleading but well-formed* answer citing a real source; groundedness checking of claims against text is not implemented (Phase 3 measures it).
- Validation checks that cited ids exist, not that each claim is supported by the cited text.
- `ClaudeClient` is **unverified against the live API** (no key/network here). Wire format was taken from the public docs (2023-06-01 version header); `temperature` is omitted; `tool_result.is_error` is unused (not confirmed in docs). Needs one real call before any claim of "works with Claude".
- `http.server` is a PoC server: no TLS, no rate limiting, no per-user identity, thread-per-request. SQLite connection is shared across threads and must stay read-only while serving.
- Model price env vars are optional; no default prices are assumed, so cost estimates are `null` unless you supply your own.

## Alternatives considered
- FastAPI now: better ergonomics, blocked by sandbox; interface keeps the swap trivial.
- LLM-based injection classifier: adds cost/latency and its own attack surface; deferred.
- Letting the model emit URLs: rejected; id-based citation removes a whole exfiltration/hallucination class.
