"""Provider-neutral types shared by the agent, tools and model clients.

Internal message formats (a list of dicts):
  {"role": "user", "content": str}
  {"role": "assistant", "text": str, "tool_calls": list[ToolCall]}
  {"role": "tool", "tool_call_id": str, "name": str, "content": str}
Each model client converts these to its provider's wire format.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

Message = dict[str, Any]


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, other: Usage) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens


@dataclass
class ModelTurn:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    stop_reason: str = ""


@dataclass
class Citation:
    id: str
    url: str
    title: str
    product: str | None
    resource_type: str | None
    last_updated: str | None
    fetched_at: str
    stale: bool


@dataclass
class AskResult:
    """status: answered | not_found | refused | rejected | invalid | error"""

    request_id: str
    status: str
    answer: str = ""
    reason: str = ""
    citations: list[Citation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    tool_calls: int = 0
    sources_retrieved: int = 0
    latency_ms: int = 0
    model: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
