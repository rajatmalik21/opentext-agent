"""Offline stand-in for an LLM: deterministic, extractive, NOT intelligent.

Used for the CLI demo, CI smoke tests and local development without an API key. It calls the
search tool once, then answers with the first sentence of the best source, only if the question
shares content words with it. Metrics measured with this model say nothing about Claude.
"""

from __future__ import annotations

import json
import re

from ..retrieval.hybrid import STOPWORDS
from .types import Message, ModelTurn, ToolCall, ToolSpec, Usage

_USER = re.compile(r"<<USER-[0-9a-f]+>>\n(.*?)\n<<END-USER-[0-9a-f]+>>", re.S)
_SOURCE = re.compile(
    r"<<SOURCE-(?P<n>[0-9a-f]+) id=(?P<id>S\d+)(?P<attrs>[^>]*)>>\n(?P<body>.*?)\n<<END-(?P=n)>>", re.S
)
_WORD = re.compile(r"[a-z0-9]+")


def _content_words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if len(w) > 2 and w not in STOPWORDS}


class ExtractiveModel:
    name = "extractive-stub"

    def generate(
        self, system: str, messages: list[Message], tools: list[ToolSpec], max_tokens: int = 800
    ) -> ModelTurn:
        usage = Usage(0, 0)
        tool_msgs = [m for m in messages if m["role"] == "tool"]
        question = ""
        for m in messages:
            if m["role"] == "user":
                hit = _USER.search(m["content"])
                if hit:
                    question = hit.group(1)
                    break
        if not tool_msgs:
            return ModelTurn(
                tool_calls=[ToolCall("stub-1", "search_support_content", {"query": question or "?", "k": 3})],
                usage=usage, stop_reason="tool_use",
            )
        q_words = _content_words(question)
        for m in tool_msgs:
            for s in _SOURCE.finditer(m["content"]):
                lines = s.group("body").split("\n", 1)
                header, body = lines[0], (lines[1] if len(lines) > 1 else "")
                if not q_words & _content_words(header + " " + body):
                    continue
                parts = header.split(" | ")
                title = parts[1] if len(parts) > 1 else header
                first = re.split(r"(?<=[.!?])\s", body.strip(), maxsplit=1)[0]
                answer = f"{title}: {first}".strip()
                return ModelTurn(
                    text=json.dumps({"found": True, "answer": answer, "citations": [s.group("id")]}),
                    usage=usage, stop_reason="end_turn",
                )
        return ModelTurn(
            text=json.dumps({"found": False, "answer": "", "citations": []}),
            usage=usage, stop_reason="end_turn",
        )
