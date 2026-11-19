"""Tolerant normalization of search-API payloads into SourceRecord.

ASSUMPTION: the real response schema has not been observed (the endpoint returned a
client error to an automated fetch). Field-name candidates below are guesses that
make the parser tolerant to drift; replace/confirm them once a real response is
captured. Everything here treats upstream text as untrusted data.
"""

from __future__ import annotations

import hashlib
import html
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from ..models import SourceRecord

BASE = "https://my.opentext.com"

LIST_KEYS = ("results", "items", "hits", "documents", "data")
TOTAL_KEYS = ("total", "totalresults", "totalcount", "count", "numfound")
TITLE_KEYS = ("title", "name", "heading")
URL_KEYS = ("url", "link", "href", "permalink")
SUMMARY_KEYS = ("summary", "description", "snippet", "abstract", "excerpt")
TYPE_KEYS = ("type", "contenttype", "resourcetype", "category", "kind")
PRODUCT_KEYS = ("product", "productname", "products")
DATE_KEYS = ("lastmodified", "lastupdated", "modified", "updated", "date")
ID_KEYS = ("id", "docid", "documentid", "uid")

_TRACKING = {"referrer", "fbclid", "gclid"}
_BLOCKS = re.compile(r"<(script|style)\b.*?</\1\s*>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


class SchemaDriftError(ValueError):
    """Payload shape is not recognizable."""


def clean_text(s: str) -> str:
    s = _BLOCKS.sub(" ", s)
    s = _TAG.sub(" ", s)
    return _WS.sub(" ", html.unescape(s)).strip()


def canonical_url(raw: str) -> str | None:
    raw = raw.strip()
    if not raw:
        return None
    u = urlsplit(urljoin(BASE + "/", raw))
    if u.scheme not in ("http", "https") or not u.netloc:
        return None
    q = [
        (k, v)
        for k, v in parse_qsl(u.query, keep_blank_values=True)
        if k.lower() not in _TRACKING and not k.lower().startswith("utm_")
    ]
    path = u.path.rstrip("/") or "/"
    return urlunsplit(("https", u.netloc.lower(), path, urlencode(q), ""))


def _lower_map(d: dict[str, Any]) -> dict[str, Any]:
    return {str(k).lower(): v for k, v in d.items()}


def _as_text(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, str):
        return clean_text(v) or None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return str(v)
    if isinstance(v, dict):
        lm = _lower_map(v)
        for k in ("name", "title", "label", "value"):
            t = _as_text(lm.get(k))
            if t:
                return t
        return None
    if isinstance(v, list):
        parts = [t for t in (_as_text(x) for x in v) if t]
        return ", ".join(parts) or None
    return None


def _first(item: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    lm = _lower_map(item)
    for k in keys:
        if k in lm:
            t = _as_text(lm[k])
            if t:
                return t
    return None


def extract_items(payload: Any, _depth: int = 0) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [i for i in payload if isinstance(i, dict)]
    if not isinstance(payload, dict) or _depth > 2:
        raise SchemaDriftError("payload is not a list or object")
    lm = _lower_map(payload)
    for key in LIST_KEYS:
        v = lm.get(key)
        if isinstance(v, list):
            return [i for i in v if isinstance(i, dict)]
        if isinstance(v, dict):
            try:
                return extract_items(v, _depth + 1)
            except SchemaDriftError:
                continue
    if extract_total(payload) == 0:
        return []
    raise SchemaDriftError(f"no result list found; top-level keys: {sorted(lm)[:10]}")


def extract_total(payload: Any, _depth: int = 0) -> int | None:
    if not isinstance(payload, dict) or _depth > 2:
        return None
    lm = _lower_map(payload)
    for key in TOTAL_KEYS:
        v = lm.get(key)
        if isinstance(v, bool):
            continue
        if isinstance(v, int):
            return v
        if isinstance(v, str) and v.isdigit():
            return int(v)
    nested = lm.get("data")
    return extract_total(nested, _depth + 1) if isinstance(nested, dict) else None


def normalize_item(
    item: dict[str, Any], *, source: str, source_url: str, fetched_at: str
) -> SourceRecord | None:
    title = _first(item, TITLE_KEYS)
    raw_url = _first(item, URL_KEYS)
    url = canonical_url(raw_url) if raw_url else None
    if not title or not url:
        return None
    summary = _first(item, SUMMARY_KEYS) or ""
    product = _first(item, PRODUCT_KEYS)
    rtype = _first(item, TYPE_KEYS)
    updated = _first(item, DATE_KEYS)
    source_id = _first(item, ID_KEYS) or hashlib.sha1(url.encode()).hexdigest()[:16]
    digest = hashlib.sha256(
        "\x1f".join([title, url, summary, product or "", rtype or "", updated or ""]).encode()
    ).hexdigest()
    return SourceRecord(
        source_id=source_id,
        product=product,
        title=title,
        url=url,
        summary=summary,
        resource_type=rtype,
        last_updated=updated,
        source=source,
        source_url=source_url,
        fetched_at=fetched_at,
        content_hash=digest,
    )
