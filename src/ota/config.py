"""Settings from environment variables. Secrets are read here and never logged."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .retrieval.hybrid import Mode

_MODES = ("bm25", "hybrid", "vector")


def _mode(raw: str) -> Mode:
    if raw not in _MODES:
        raise ValueError(f"OTA_RETRIEVAL_MODE must be one of {_MODES}, got {raw!r}")
    return raw  # type: ignore[return-value]


def _float(name: str) -> float | None:
    raw = os.environ.get(name)
    return float(raw) if raw else None


@dataclass
class Settings:
    db_path: str = "data/index.db"
    model: str = "claude-sonnet-5-5"
    anthropic_api_key: str = field(default="", repr=False)
    api_key: str = field(default="", repr=False)  # optional shared secret for POST /ask
    stale_days: int = 30
    max_input_chars: int = 500
    price_in_per_mtok: float | None = None  # set from your own pricing; never assumed
    price_out_per_mtok: float | None = None
    log_level: str = "INFO"
    # Default is bm25: Phase 3 found no measurable gain from hybrid with the lexical hashing
    # embedder (docs/evaluation.md, ADR-0003). Switch to "hybrid" once a semantic embedder exists.
    retrieval_mode: Mode = "bm25"

    @classmethod
    def from_env(cls) -> Settings:
        e = os.environ
        return cls(
            db_path=e.get("OTA_DB", "data/index.db"),
            model=e.get("OTA_MODEL", "claude-sonnet-5-5"),
            anthropic_api_key=e.get("ANTHROPIC_API_KEY", ""),
            api_key=e.get("OTA_API_KEY", ""),
            stale_days=int(e.get("OTA_STALE_DAYS", "30")),
            max_input_chars=int(e.get("OTA_MAX_INPUT_CHARS", "500")),
            price_in_per_mtok=_float("OTA_PRICE_IN_PER_MTOK"),
            price_out_per_mtok=_float("OTA_PRICE_OUT_PER_MTOK"),
            log_level=e.get("OTA_LOG_LEVEL", "INFO"),
            retrieval_mode=_mode(e.get("OTA_RETRIEVAL_MODE", "bm25")),
        )
