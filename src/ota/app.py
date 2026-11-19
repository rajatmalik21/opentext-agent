"""Wiring: settings + store -> Agent."""

from __future__ import annotations

from .agent.agent import Agent
from .agent.claude import ClaudeClient
from .agent.model import ModelClient
from .agent.stub import ExtractiveModel
from .agent.tools import ToolRegistry
from .config import Settings
from .retrieval.embedder import HashingEmbedder
from .retrieval.hybrid import HybridIndex
from .store.sqlite_store import SQLiteStore


class ConfigError(RuntimeError):
    pass


def build_model(settings: Settings, choice: str) -> ModelClient:
    if choice == "claude":
        if not settings.anthropic_api_key:
            raise ConfigError("ANTHROPIC_API_KEY is not set; use --model extractive for offline mode")
        return ClaudeClient(settings.anthropic_api_key, settings.model)
    if choice == "extractive":
        return ExtractiveModel()
    raise ConfigError(f"unknown model choice: {choice}")


def build_agent(settings: Settings, store: SQLiteStore, choice: str = "extractive") -> Agent:
    index = HybridIndex(store, HashingEmbedder())
    return Agent(
        build_model(settings, choice),
        ToolRegistry(index, store, retrieval_mode=settings.retrieval_mode),
        max_input_chars=settings.max_input_chars,
        stale_days=settings.stale_days,
        price_in_per_mtok=settings.price_in_per_mtok,
        price_out_per_mtok=settings.price_out_per_mtok,
    )
