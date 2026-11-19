"""Polite HTTP client for the support search API.

Principles: identify ourselves, honour robots.txt, rate-limit, cache, retry only
transient failures, and never try to get around an auth wall or block. A 4xx is
reported to the operator, not retried or worked around.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

from .robots import RobotsRules

DEFAULT_BASE_URL = "https://my.opentext.com"
SEARCH_PATH = "/api/web/support/search-results"


class SourceAccessError(RuntimeError):
    """Source refused us (4xx, robots, non-JSON/login page). Not retried, not bypassed."""


class SourceUnavailableError(RuntimeError):
    """Transient failure persisted after all retries."""


def _default_ua() -> str:
    contact = os.environ.get("OTA_CONTACT", "contact-not-set")
    return f"ota-assessment-agent/0.1 (take-home assessment; {contact})"


@dataclass
class ClientConfig:
    base_url: str = DEFAULT_BASE_URL
    user_agent: str = field(default_factory=_default_ua)
    min_interval_s: float = 1.0
    timeout_s: float = 15.0
    max_retries: int = 4
    backoff_base_s: float = 0.5
    backoff_max_s: float = 20.0
    retry_after_cap_s: float = 60.0
    cache_dir: Path | None = None
    cache_ttl_s: float = 3600.0
    enforce_robots: bool = True
    robots_txt: str | None = None  # inject for tests/offline; else fetched once


@dataclass
class FetchResult:
    url: str
    payload: Any
    from_cache: bool


class SearchClient:
    def __init__(
        self,
        config: ClientConfig | None = None,
        session: Any = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        jitter: Callable[[], float] = lambda: random.random() * 0.1,
    ) -> None:
        self.config = config or ClientConfig()
        self.session = session or requests.Session()
        self._sleep = sleep
        self._clock = clock
        self._wall = wall_clock
        self._jitter = jitter
        self._last_request_at: float | None = None
        self._robots: RobotsRules | None = None

    # -- public -----------------------------------------------------------
    def fetch_page(self, start: int, max_results: int, text: str = "*") -> FetchResult:
        path_q = f"{SEARCH_PATH}?start={start}&maxResults={max_results}&text={quote(text, safe='*')}"
        url = self.config.base_url.rstrip("/") + path_q
        self._check_robots(path_q)
        cached = self._cache_get(url)
        if cached is not None:
            return FetchResult(url, cached, True)
        resp = self._send(url)
        try:
            payload = resp.json()
        except ValueError as exc:
            raise SourceAccessError(
                "response was not JSON (possibly a login or error page); not retrying"
            ) from exc
        self._cache_put(url, payload)
        return FetchResult(url, payload, False)

    # -- robots -----------------------------------------------------------
    def _check_robots(self, path_q: str) -> None:
        if not self.config.enforce_robots:
            return
        if self._robots is None:
            self._robots = self._load_robots()
        if not self._robots.allowed(path_q):
            raise SourceAccessError(f"blocked by robots.txt: {path_q}")

    def _load_robots(self) -> RobotsRules:
        if self.config.robots_txt is not None:
            return RobotsRules.parse(self.config.robots_txt, self.config.user_agent)
        resp = self._send(self.config.base_url.rstrip("/") + "/robots.txt", allow_404=True)
        if resp.status_code == 404:
            return RobotsRules.allow_all()
        return RobotsRules.parse(resp.text, self.config.user_agent)

    # -- transport --------------------------------------------------------
    def _throttle(self) -> None:
        now = self._clock()
        if self._last_request_at is not None:
            wait = self.config.min_interval_s - (now - self._last_request_at)
            if wait > 0:
                self._sleep(wait)
        self._last_request_at = self._clock()

    def _backoff(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return min(retry_after, self.config.retry_after_cap_s)
        return min(self.config.backoff_max_s, self.config.backoff_base_s * (2**attempt)) + self._jitter()

    def _send(self, url: str, *, allow_404: bool = False) -> Any:
        cfg = self.config
        last_error = "unknown"
        for attempt in range(cfg.max_retries + 1):
            self._throttle()
            retry_after: float | None = None
            try:
                resp = self.session.get(
                    url,
                    headers={"User-Agent": cfg.user_agent, "Accept": "application/json"},
                    timeout=cfg.timeout_s,
                )
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                last_error = f"{type(exc).__name__}"
            else:
                code = resp.status_code
                if code == 429 or code >= 500:
                    last_error = f"HTTP {code}"
                    raw = getattr(resp, "headers", {}).get("Retry-After")
                    try:
                        retry_after = float(raw) if raw is not None else None
                    except ValueError:
                        retry_after = None
                elif code == 404 and allow_404:
                    return resp
                elif 400 <= code < 500:
                    raise SourceAccessError(
                        f"HTTP {code} from source. Possible causes: login required, missing "
                        "headers/cookies, blocked automation, or bad parameters. Not retrying and "
                        "not working around it; use fixtures or request sanctioned access."
                    )
                else:
                    return resp
            if attempt < cfg.max_retries:
                self._sleep(self._backoff(attempt, retry_after))
        raise SourceUnavailableError(f"gave up after {cfg.max_retries + 1} attempts: {last_error}")

    # -- cache ------------------------------------------------------------
    def _cache_path(self, url: str) -> Path | None:
        if self.config.cache_dir is None:
            return None
        return self.config.cache_dir / (hashlib.sha256(url.encode()).hexdigest()[:32] + ".json")

    def _cache_get(self, url: str) -> Any | None:
        p = self._cache_path(url)
        if p is None or not p.exists():
            return None
        try:
            blob = json.loads(p.read_text())
        except (OSError, ValueError):
            return None
        if self._wall() - float(blob.get("cached_at", 0)) > self.config.cache_ttl_s:
            return None
        return blob.get("payload")

    def _cache_put(self, url: str, payload: Any) -> None:
        p = self._cache_path(url)
        if p is None:
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"cached_at": self._wall(), "payload": payload}))


class FixtureClient:
    """Offline stand-in that serves recorded pages listed in fixtures/manifest.json."""

    def __init__(self, directory: Path) -> None:
        self.dir = Path(directory)
        self.manifest = json.loads((self.dir / "manifest.json").read_text())

    def fetch_page(self, start: int, max_results: int, text: str = "*") -> FetchResult:
        name = self.manifest["pages"].get(str(start))
        if name is None:
            return FetchResult(f"fixture://empty?start={start}", {"total": 0, "results": []}, True)
        payload = json.loads((self.dir / name).read_text())
        return FetchResult(f"fixture://{name}?start={start}", payload, True)
