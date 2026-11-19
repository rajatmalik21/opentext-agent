"""Typed tools. Results are rendered as delimited, per-request-nonce SOURCE blocks.

The model cites by short ids (S1..); the agent maps ids back to URLs, so the model can
never introduce a URL of its own that survives validation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..retrieval.hybrid import HybridIndex, Mode
from ..store.sqlite_store import SQLiteStore
from .guard import neutralize
from .types import ToolCall, ToolSpec


class SearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=300)
    k: int = Field(default=5, ge=1, le=8)
    product: str | None = Field(default=None, max_length=100)


class LookupArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)


@dataclass
class SourceView:
    id: str
    url: str
    title: str
    product: str | None
    resource_type: str | None
    last_updated: str | None
    fetched_at: str
    stale: bool


@dataclass
class RequestContext:
    nonce: str
    now: datetime
    stale_days: int
    sources: dict[str, SourceView] = field(default_factory=dict)
    by_url: dict[str, str] = field(default_factory=dict)
    tool_calls: int = 0


def _attr(v: str | None) -> str:
    return re.sub(r'[\s"<>]+', " ", v or "").strip()[:80]


def _is_stale(fetched_at: str, now: datetime, stale_days: int) -> bool:
    try:
        fetched = datetime.fromisoformat(fetched_at)
    except ValueError:
        return True  # unknown freshness is treated as stale
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=now.tzinfo)
    return (now - fetched).days > stale_days


class ToolRegistry:
    def __init__(
        self,
        index: HybridIndex,
        store: SQLiteStore,
        *,
        max_tool_calls: int = 4,
        char_limit: int = 1200,
        retrieval_mode: Mode = "bm25",
    ) -> None:
        self.index = index
        self.store = store
        self.retrieval_mode = retrieval_mode
        self.max_tool_calls = max_tool_calls
        self.char_limit = char_limit

    def specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                "search_support_content",
                "Search the indexed support content. Returns source blocks you may cite by id.",
                {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search text (max 300 chars)."},
                        "k": {"type": "integer", "minimum": 1, "maximum": 8},
                        "product": {"type": "string", "description": "Optional product name filter."},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                "lookup_product",
                "List indexed resources for one product name. Returns source blocks you may cite.",
                {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                    "additionalProperties": False,
                },
            ),
        ]

    # -- execution --------------------------------------------------------
    def execute(self, call: ToolCall, ctx: RequestContext) -> str:
        ctx.tool_calls += 1
        if ctx.tool_calls > self.max_tool_calls:
            return "ERROR: tool call limit reached. Answer now from what you have, or set found=false."
        try:
            if call.name == "search_support_content":
                return self._search(SearchArgs.model_validate(call.arguments), ctx)
            if call.name == "lookup_product":
                return self._lookup(LookupArgs.model_validate(call.arguments), ctx)
        except ValidationError as exc:
            fields = ", ".join(sorted({".".join(map(str, e["loc"])) or "?" for e in exc.errors()}))
            return f"ERROR: invalid arguments (fields: {fields})."
        return "ERROR: unknown tool."

    def _search(self, args: SearchArgs, ctx: RequestContext) -> str:
        fetch = args.k * 3 if args.product else args.k
        hits = self.index.search(args.query, k=fetch, fetch_k=30, mode=self.retrieval_mode)
        if args.product:
            needle = args.product.lower()
            hits = [h for h in hits if h.product and needle in h.product.lower()]
        hits = hits[: args.k]
        blocks = []
        for h in hits:
            rec = self.store.get_record(h.url)
            if rec is not None:
                blocks.append(self._render(rec, h.text, ctx))
        return "\n".join(blocks) if blocks else "NO_RESULTS"

    def _lookup(self, args: LookupArgs, ctx: RequestContext) -> str:
        recs = self.store.records_by_product(args.name, limit=8)
        blocks = []
        for rec in recs:
            header = " | ".join(x for x in (rec["product"], rec["title"], rec["resource_type"]) if x)
            blocks.append(self._render(rec, f"{header}\n{rec['summary']}", ctx))
        return "\n".join(blocks) if blocks else "NO_RESULTS"

    def _render(self, rec: dict[str, Any], text: str, ctx: RequestContext) -> str:
        sid = ctx.by_url.get(rec["url"])
        if sid is None:
            sid = f"S{len(ctx.sources) + 1}"
            ctx.by_url[rec["url"]] = sid
            ctx.sources[sid] = SourceView(
                id=sid, url=rec["url"], title=rec["title"], product=rec["product"],
                resource_type=rec["resource_type"], last_updated=rec["last_updated"],
                fetched_at=rec["fetched_at"],
                stale=_is_stale(rec["fetched_at"], ctx.now, ctx.stale_days),
            )
        v = ctx.sources[sid]
        body = neutralize(text)[: self.char_limit]
        head = (
            f'<<SOURCE-{ctx.nonce} id={sid} product="{_attr(v.product)}" '
            f'type="{_attr(v.resource_type)}" last_updated="{_attr(v.last_updated)}" '
            f'stale="{str(v.stale).lower()}">>'
        )
        return f"{head}\n{body}\n<<END-{ctx.nonce}>>"
