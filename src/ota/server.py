"""Stdlib HTTP API (FastAPI is not installable in the build sandbox; see ADR-0001/0005).

  GET  /healthz  liveness (no dependencies checked)
  GET  /readyz   readiness: index has records and a model is configured
  POST /ask      {"question": "..."}  ->  AskResult JSON

Optional shared-secret auth for /ask via OTA_API_KEY (header X-API-Key). This is a PoC control,
not a substitute for real identity (see the Part 2 architecture).
"""

from __future__ import annotations

import hmac
import json
import logging
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .agent.agent import Agent
from .obs import log_event
from .store.sqlite_store import SQLiteStore

MAX_BODY = 8 * 1024
STATUS_TO_HTTP = {"answered": 200, "not_found": 200, "refused": 200, "rejected": 200,
                  "invalid": 400, "error": 502}


def make_server(
    agent: Agent, store: SQLiteStore, *, host: str = "127.0.0.1", port: int = 8080, api_key: str = ""
) -> ThreadingHTTPServer:
    logger = logging.getLogger("ota")

    class Handler(BaseHTTPRequestHandler):
        server_version = "ota/0.1"
        timeout = 30  # slow-client guard

        def log_message(self, format: str, *args: Any) -> None:  # silence default stderr noise
            pass

        def _send(self, code: int, obj: dict[str, Any], rid: str = "") -> None:
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if rid:
                self.send_header("X-Request-Id", rid)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/healthz":
                self._send(200, {"status": "ok"})
            elif self.path == "/readyz":
                problems = []
                try:
                    if store.count_records() == 0:
                        problems.append("index is empty")
                except Exception:  # noqa: BLE001 - readiness must not crash
                    problems.append("index unavailable")
                if getattr(agent, "model", None) is None:
                    problems.append("model not configured")
                self._send(200 if not problems else 503,
                           {"status": "ready" if not problems else "not_ready", "problems": problems})
            else:
                self._send(404, {"error": "not_found"})

        def do_POST(self) -> None:
            rid = uuid.uuid4().hex[:12]
            if self.path != "/ask":
                return self._send(404, {"error": "not_found"}, rid)
            if api_key and not hmac.compare_digest(self.headers.get("X-API-Key", ""), api_key):
                log_event(logger, "auth_failed", logging.WARNING, request_id=rid)
                return self._send(401, {"error": "unauthorized"}, rid)
            if "application/json" not in self.headers.get("Content-Type", ""):
                return self._send(415, {"error": "content-type must be application/json"}, rid)
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                return self._send(411, {"error": "content-length required"}, rid)
            if length < 0 or length > MAX_BODY:
                return self._send(413, {"error": f"body exceeds {MAX_BODY} bytes"}, rid)
            try:
                payload = json.loads(self.rfile.read(length))
                question = payload["question"] if isinstance(payload, dict) else None
            except (ValueError, KeyError):
                return self._send(400, {"error": "body must be JSON with a 'question' field"}, rid)
            result = agent.ask(question, request_id=rid)
            self._send(STATUS_TO_HTTP.get(result.status, 500), result.to_dict(), rid)

    return ThreadingHTTPServer((host, port), Handler)
