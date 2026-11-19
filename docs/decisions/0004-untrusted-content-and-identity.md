# ADR-0004: Treat upstream text as untrusted; canonical URL is identity

**Status:** Accepted

## Decision
- Everything from the source is data, never instructions. At ingestion: strip `<script>/<style>` blocks and tags, unescape entities, collapse whitespace, reject non-http(s) URLs.
  Plain-text injection phrases are NOT removed (cannot be reliably filtered); they are handled at the agent layer in Phase 2 (delimited context, instruction hierarchy, output validation, tool allow-list).
- Identity key and citation target is the canonical URL (https, lower-case host, tracking params and fragment dropped, trailing slash trimmed). Re-ingestion is idempotent via a content hash over content fields only.
- Provenance kept per record: source, source URL, fetch time, content hash.

## Alternatives considered
- Upstream `id` as key: not confirmed stable or present (schema unobserved). Kept as an attribute only.

## Consequences
- Two upstream items with different ids but the same canonical URL collapse into one record (newer `last_updated` wins). Acceptable for citations; revisit if the real data disagrees.
- A fixture containing a deliberate injection string ensures Phase 2/3 tests exercise this path.
