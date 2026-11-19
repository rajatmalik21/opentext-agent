"""Runs a dataset through an agent and scores it.

Two agent modes are compared to measure what the code-level guardrails add:
  guarded  : the production Agent (input screen + output validation + forced not-found).
  trusting : an ablation baseline that believes whatever the model returns, i.e. the
             system prompt is the ONLY control. It exists only for this comparison.

Every result file carries `meta.kind` so offline and live numbers can never be confused:
  offline-stub | offline-scripted | live-api
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..agent.agent import Agent
from ..agent.model import ModelClient
from ..agent.prompts import NOT_FOUND_MESSAGE
from ..agent.stub import ExtractiveModel
from ..agent.tools import RequestContext, ToolRegistry
from ..agent.types import AskResult, Citation, ToolCall
from ..app import ConfigError, build_model
from ..config import Settings
from ..retrieval.embedder import HashingEmbedder
from ..retrieval.hybrid import HybridIndex, Mode
from ..store.sqlite_store import SQLiteStore
from .dataset import Question, dataset_sha256
from .metrics import score, summarize
from .scripted import PROFILES

MODEL_CHOICES = ["extractive", *(f"scripted:{p}" for p in PROFILES), "claude"]


class RecordingTools(ToolRegistry):
    """Remembers the last request's retrieved sources so the harness can check citations."""

    last_ctx: RequestContext | None = None

    def execute(self, call: ToolCall, ctx: RequestContext) -> str:
        self.last_ctx = ctx
        return super().execute(call, ctx)


class TrustingAgent(Agent):
    """Ablation baseline: no output validation. Never use outside evaluation."""

    def _finish(self, final, ctx, canary, result):  # type: ignore[no-untyped-def]
        if not final.found:
            return result("not_found", NOT_FOUND_MESSAGE, "model_not_found")
        cites = []
        for cid in dict.fromkeys(final.citations):
            s = ctx.sources.get(cid)
            if s is not None:
                cites.append(Citation(s.id, s.url, s.title, s.product, s.resource_type,
                                      s.last_updated, s.fetched_at, s.stale))
            else:
                cites.append(Citation(cid, f"unknown:{cid}", "(unknown source)", None, None, None, "", False))
        return result("answered", final.answer.strip(), citations=cites)


def make_model(choice: str, settings: Settings) -> ModelClient:
    if choice.startswith("scripted:"):
        name = choice.split(":", 1)[1]
        if name not in PROFILES:
            raise ConfigError(f"unknown scripted profile {name!r}; choose from {sorted(PROFILES)}")
        return PROFILES[name]()
    if choice == "extractive":
        return ExtractiveModel()
    return build_model(settings, choice)


def run_kind(choice: str) -> str:
    return {"extractive": "offline-stub", "claude": "live-api"}.get(choice, "offline-scripted")


def build_eval_agent(
    store: SQLiteStore, choice: str, mode: str, settings: Settings, retrieval_mode: Mode | None = None,
) -> tuple[Agent, RecordingTools]:
    tools = RecordingTools(HybridIndex(store, HashingEmbedder()), store,
                           retrieval_mode=retrieval_mode or settings.retrieval_mode)
    cls = Agent if mode == "guarded" else TrustingAgent
    agent = cls(
        make_model(choice, settings), tools,
        max_input_chars=settings.max_input_chars, stale_days=settings.stale_days,
        screen_input=(mode == "guarded"),
        logger=logging.getLogger("ota.eval"),
    )
    return agent, tools


def run_agent_eval(
    agent: Agent, tools: RecordingTools, store: SQLiteStore, questions: list[Question],
    *, meta: dict[str, Any], delay_s: float = 0.0,
    price_in: float | None = None, price_out: float | None = None,
) -> dict[str, Any]:
    rows = []
    for q in questions:
        tools.last_ctx = None
        result: AskResult = agent.ask(q.question, request_id=q.id)
        retrieved: set[str] = (
            {s.url for s in tools.last_ctx.sources.values()} if tools.last_ctx else set()
        )
        rows.append(score(q, result, retrieved, store))
        if delay_s:
            time.sleep(delay_s)
    summary = summarize(rows)
    cost = None
    if price_in is not None and price_out is not None:
        t = summary["tokens"]
        cost = round((t["input"] * price_in + t["output"] * price_out) / 1e6, 6)
    summary["est_cost_usd"] = cost
    return {"meta": {**meta, "created_utc": datetime.now(UTC).isoformat(timespec="seconds")},
            "summary": summary, "rows": rows}


def run_one(
    store: SQLiteStore, questions: list[Question], dataset_path: str | Path, *, choice: str,
    mode: str, settings: Settings, corpus: dict[str, Any], delay_s: float = 0.0,
    retrieval_mode: Mode | None = None,
) -> dict[str, Any]:
    agent, tools = build_eval_agent(store, choice, mode, settings, retrieval_mode)
    meta = {
        "kind": run_kind(choice), "model": getattr(agent.model, "name", choice), "model_choice": choice,
        "agent_mode": mode, "retrieval_mode": tools.retrieval_mode, "corpus": corpus, "dataset_sha256": dataset_sha256(dataset_path),
        "n_questions": len(questions),
    }
    return run_agent_eval(agent, tools, store, questions, meta=meta, delay_s=delay_s,
                          price_in=settings.price_in_per_mtok, price_out=settings.price_out_per_mtok)
