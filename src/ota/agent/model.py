"""Model-provider interface. The agent only depends on this."""

from __future__ import annotations

from typing import Protocol

from .types import Message, ModelTurn, ToolSpec


class ModelError(RuntimeError):
    """Provider call failed (after retries). Never contains credentials."""


class ModelClient(Protocol):
    name: str

    def generate(
        self, system: str, messages: list[Message], tools: list[ToolSpec], max_tokens: int = 800
    ) -> ModelTurn: ...
