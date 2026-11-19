"""Ingestion pipeline: page through the source, normalize, de-duplicate, index.

Failure policy: if the FIRST page cannot be fetched/parsed, raise (nothing useful to do).
If a LATER page fails, keep what we have, record the error, and report complete=False.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from ..chunking import build_chunks
from ..models import SourceRecord
from ..retrieval.embedder import Embedder
from ..store.sqlite_store import SQLiteStore
from .client import FetchResult, SourceAccessError, SourceUnavailableError
from .normalize import SchemaDriftError, extract_items, extract_total, normalize_item


class PageSource(Protocol):
    def fetch_page(self, start: int, max_results: int, text: str = "*") -> FetchResult: ...


@dataclass
class IngestReport:
    pages_fetched: int = 0
    items_seen: int = 0
    rejected: int = 0
    duplicates: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0
    sweep_note: str = ""
    complete: bool = False
    stop_reason: str = ""
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def ingest(
    client: PageSource,
    store: SQLiteStore,
    embedder: Embedder,
    *,
    page_size: int = 5,
    max_pages: int = 50,
    text: str = "*",
    start_base: int = 1,  # ASSUMPTION: `start` is 1-based; verify against the real API
    source: str = "support-search-api",
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    sweep: bool = True,
    max_removal_fraction: float = 0.5,
    guard_min_size: int = 10,
) -> IngestReport:
    store.check_embedder(embedder)
    report = IngestReport()
    found: dict[str, SourceRecord] = {}
    start = start_base
    report.stop_reason = "max_pages"

    for page_no in range(max_pages):
        try:
            res = client.fetch_page(start, page_size, text)
            items = extract_items(res.payload)
        except (SourceAccessError, SourceUnavailableError, SchemaDriftError) as exc:
            if page_no == 0:
                raise
            report.errors.append(f"start={start}: {type(exc).__name__}: {exc}")
            report.stop_reason = "error"
            break
        report.pages_fetched += 1
        if not items:
            report.stop_reason, report.complete = "empty_page", True
            break

        fetched_at = now().isoformat()
        for item in items:
            report.items_seen += 1
            rec = normalize_item(item, source=source, source_url=res.url, fetched_at=fetched_at)
            if rec is None:
                report.rejected += 1
            elif rec.url in found:
                report.duplicates += 1
                if (rec.last_updated or "") > (found[rec.url].last_updated or ""):
                    found[rec.url] = rec
            else:
                found[rec.url] = rec

        total = extract_total(res.payload)
        next_start = start + page_size
        if total is not None and next_start - start_base >= total:
            report.stop_reason, report.complete = "reached_total", True
            break
        if len(items) < page_size:
            report.stop_reason, report.complete = "short_page", True
            break
        start = next_start

    for rec in found.values():
        status = store.record_status(rec.url, rec.content_hash)
        if status == "same":
            store.touch(rec.url, rec.fetched_at)
            report.unchanged += 1
            continue
        chunks = build_chunks(rec)
        store.replace_record(rec, chunks, embedder.embed(chunks))
        if status == "missing":
            report.inserted += 1
        else:
            report.updated += 1

    # Deleted-upstream cleanup. Only after a COMPLETE run (a partial run proves nothing about
    # absence), only within this source, and never a mass deletion: if too much of the index
    # would vanish (empty/blocked upstream, schema drift) we keep it and say so.
    if sweep:
        if not report.complete:
            report.sweep_note = "skipped: run incomplete"
        else:
            existing = store.all_urls(source)
            missing = existing - found.keys()
            if not missing:
                report.sweep_note = "nothing to remove"
            elif len(existing) >= guard_min_size and len(missing) > max_removal_fraction * len(existing):
                report.sweep_note = (
                    f"skipped: would remove {len(missing)}/{len(existing)} records (over guard)"
                )
            else:
                report.removed = store.delete_records(missing)
                report.sweep_note = f"removed {report.removed} records absent upstream"
    return report
