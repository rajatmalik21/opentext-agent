"""Typed records shared by ingestion, storage and retrieval."""

from __future__ import annotations

from pydantic import BaseModel


class SourceRecord(BaseModel):
    """One normalized support resource, with provenance.

    `url` (canonicalized) is the identity key and the citation target.
    `last_updated` is kept as the source's own string: we do not guess its format.
    `content_hash` covers content fields only (not fetched_at), so re-ingesting
    unchanged content is a no-op.
    """

    source_id: str
    product: str | None = None
    title: str
    url: str
    summary: str = ""
    resource_type: str | None = None
    last_updated: str | None = None
    source: str
    source_url: str
    fetched_at: str
    content_hash: str
