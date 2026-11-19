"""Chunking. Records are short, so most yield one chunk; long summaries are windowed.

Every chunk repeats the header (product | title | type) so a chunk is self-describing
at retrieval time.
"""

from __future__ import annotations

from .models import SourceRecord


def build_chunks(record: SourceRecord, max_words: int = 180, overlap: int = 30) -> list[str]:
    header = " | ".join(x for x in (record.product, record.title, record.resource_type) if x)
    words = record.summary.split()
    if len(words) <= max_words:
        return [f"{header}\n{record.summary}".strip()]
    step = max(1, max_words - overlap)
    chunks: list[str] = []
    for i in range(0, len(words), step):
        chunks.append(f"{header}\n{' '.join(words[i : i + max_words])}")
        if i + max_words >= len(words):
            break
    return chunks
