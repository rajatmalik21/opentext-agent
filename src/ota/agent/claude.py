"""Claude via the Messages REST API, using `requests` (no SDK needed).

NOT verified against the live API in this build (no key, no network). Wire format was checked
against the public docs for API version 2023-06-01: POST /v1/messages, headers x-api-key,
anthropic-version, content-type; tool_use / tool_result content blocks.
`temperature` is deliberately NOT sent: newer models accept only the default.
`is_error` on tool_result is NOT used: it was not documented on the page consulted.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

import requests

from .model import ModelError
from .types import Message, ModelTurn, ToolCall, ToolSpec, Usage

API_VERSION = "2023-06-01"


class ClaudeClient:
    name = "claude"

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-5-5",
        *,
        base_url: str = "https://api.anthropic.com",
        session: Any = None,
        timeout_s: float = 60.0,
        max_retries: int = 2,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self.model = model
        self._key = api_key
        self._url = base_url.rstrip("/") + "/v1/messages"
        self._session = session or requests.Session()
        self._timeout = timeout_s
        self._max_retries = max_retries
        self._sleep = sleep

    def __repr__(self) -> str:  # never expose the key
        return f"ClaudeClient(model={self.model!r})"

    # -- conversion -------------------------------------------------------
    @staticmethod
    def convert_messages(messages: list[Message]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        pending: list[dict[str, Any]] = []  # tool_result blocks awaiting their user message

        def push_user(blocks: list[dict[str, Any]]) -> None:
            if out and out[-1]["role"] == "user":
                prev = out[-1]["content"]
                prev_blocks = prev if isinstance(prev, list) else [{"type": "text", "text": prev}]
                out[-1]["content"] = prev_blocks + blocks
            else:
                out.append({"role": "user", "content": blocks})

        for m in messages:
            role = m["role"]
            if role == "tool":
                pending.append(
                    {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
                )
                continue
            if pending:
                push_user(pending)
                pending = []
            if role == "user":
                push_user([{"type": "text", "text": m["content"]}])
            elif role == "assistant":
                blocks: list[dict[str, Any]] = []
                if m.get("text"):
                    blocks.append({"type": "text", "text": m["text"]})
                for c in m.get("tool_calls", []):
                    blocks.append(
                        {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
                    )
                out.append({"role": "assistant", "content": blocks})
        if pending:
            push_user(pending)
        return out

    # -- call -------------------------------------------------------------
    def generate(
        self, system: str, messages: list[Message], tools: list[ToolSpec], max_tokens: int = 800
    ) -> ModelTurn:
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": self.convert_messages(messages),
            "tools": [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in tools
            ],
        }
        headers = {
            "x-api-key": self._key,
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }
        last = "unknown"
        for attempt in range(self._max_retries + 1):
            try:
                resp = self._session.post(
                    self._url, headers=headers, data=json.dumps(body), timeout=self._timeout
                )
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                last = type(exc).__name__
            else:
                code = resp.status_code
                if code == 200:
                    return self._parse(resp.json())
                last = f"HTTP {code}"
                if not (code == 429 or code >= 500):
                    raise ModelError(f"model API rejected the request: {last} {self._brief(resp)}")
            if attempt < self._max_retries:
                self._sleep(float(2**attempt))
        raise ModelError(f"model API unavailable after {self._max_retries + 1} attempts: {last}")

    @staticmethod
    def _brief(resp: Any) -> str:
        try:
            msg = resp.json().get("error", {}).get("message", "")
        except (ValueError, AttributeError):
            msg = ""
        return str(msg)[:200]

    @staticmethod
    def _parse(data: dict[str, Any]) -> ModelTurn:
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in data.get("content", []):
            kind = block.get("type")
            if kind == "text":
                text_parts.append(str(block.get("text", "")))
            elif kind == "tool_use":
                args = block.get("input")
                calls.append(
                    ToolCall(str(block["id"]), str(block["name"]), args if isinstance(args, dict) else {})
                )
        u = data.get("usage", {}) or {}
        return ModelTurn(
            text="".join(text_parts),
            tool_calls=calls,
            usage=Usage(int(u.get("input_tokens", 0)), int(u.get("output_tokens", 0))),
            stop_reason=str(data.get("stop_reason", "")),
        )
