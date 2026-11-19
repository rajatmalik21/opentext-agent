"""The grounded support agent.

Enforcement lives in code, not only in the prompt:
  * no retrieved source  -> NOT_FOUND, whatever the model says
  * citations must be ids retrieved in THIS request; URLs come from the index, not the model
  * any URL in the answer that is not a retrieved source -> rejected
  * leak markers (canary, delimiter nonce, delimiter text) in the answer -> rejected
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import urlsplit

from ..obs import log_event
from .guard import (
    looks_like_attack, neutralize, parse_final, redact, validate_answer, validate_input, FinalAnswer,
)
from .model import ModelClient, ModelError
from .prompts import (
    ERROR_MESSAGE, NOT_FOUND_MESSAGE, REFUSED_MESSAGE, REJECTED_MESSAGE, build_system_prompt,
)
from .tools import RequestContext, ToolRegistry
from .types import AskResult, Citation, Message, Usage

def _is_synthetic(url: str) -> bool:
    """Fixture URLs live on the reserved `.invalid` TLD (RFC 2606): never a real page."""
    host = urlsplit(url).hostname or ""
    return host == "invalid" or host.endswith(".invalid")


REPAIR_PROMPT = (
    "Your last reply was not a valid JSON object of the required form. Reply with ONLY the JSON "
    'object: {"found": ..., "answer": ..., "citations": [...]}.'
)


class Agent:
    def __init__(
        self,
        model: ModelClient,
        tools: ToolRegistry,
        *,
        max_input_chars: int = 500,
        max_turns: int = 5,
        max_tokens: int = 800,
        stale_days: int = 30,
        screen_input: bool = True,
        price_in_per_mtok: float | None = None,
        price_out_per_mtok: float | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        logger: logging.Logger | None = None,
    ) -> None:
        self.model = model
        self.tools = tools
        self.max_input_chars = max_input_chars
        self.max_turns = max_turns
        self.max_tokens = max_tokens
        self.stale_days = stale_days
        self.screen_input = screen_input
        self.price_in = price_in_per_mtok
        self.price_out = price_out_per_mtok
        self.now = now
        self.logger = logger or logging.getLogger("ota")

    # -- public -----------------------------------------------------------
    def ask(self, question: object, request_id: str | None = None) -> AskResult:
        rid = request_id or uuid.uuid4().hex[:12]
        t0 = time.perf_counter()
        result = self._ask(rid, question)
        result.latency_ms = int((time.perf_counter() - t0) * 1000)
        result.model = getattr(self.model, "name", "unknown")
        self._log(result, question)
        return result

    # -- core -------------------------------------------------------------
    def _ask(self, rid: str, question: object) -> AskResult:
        cleaned, err = validate_input(question, self.max_input_chars)
        if cleaned is None:
            return AskResult(rid, "invalid", f"Invalid question ({err}).", reason=err)
        if self.screen_input and looks_like_attack(cleaned):
            return AskResult(rid, "refused", REFUSED_MESSAGE, reason="policy_screen")

        canary = f"CANARY-{secrets.token_hex(6)}"
        ctx = RequestContext(nonce=secrets.token_hex(6), now=self.now(), stale_days=self.stale_days)
        system = build_system_prompt(canary)
        messages: list[Message] = [
            {"role": "user", "content": f"<<USER-{ctx.nonce}>>\n{neutralize(cleaned)}\n<<END-USER-{ctx.nonce}>>"}
        ]
        usage = Usage()
        specs = self.tools.specs()
        repaired = False

        def result(status: str, answer: str, reason: str = "", **kw: object) -> AskResult:
            return AskResult(
                rid, status, answer, reason=reason, usage=usage, tool_calls=ctx.tool_calls,
                sources_retrieved=len(ctx.sources), **kw,  # type: ignore[arg-type]
            )

        for turn_no in range(self.max_turns):
            try:
                turn = self.model.generate(system, messages, specs, self.max_tokens)
            except ModelError as exc:
                log_event(self.logger, "model_error", logging.WARNING, request_id=rid, error=str(exc)[:200])
                return result("error", ERROR_MESSAGE, "model_error")
            usage.add(turn.usage)

            if turn.tool_calls:
                messages.append({"role": "assistant", "text": turn.text, "tool_calls": turn.tool_calls})
                for call in turn.tool_calls:
                    messages.append({
                        "role": "tool", "tool_call_id": call.id, "name": call.name,
                        "content": self.tools.execute(call, ctx),
                    })
                continue

            final = parse_final(turn.text)
            if final is None:
                if repaired or turn_no == self.max_turns - 1:
                    return result("error", ERROR_MESSAGE, "unparseable_model_output")
                repaired = True
                messages.append({"role": "assistant", "text": turn.text, "tool_calls": []})
                messages.append({"role": "user", "content": REPAIR_PROMPT})
                continue
            return self._finish(final, ctx, canary, result)
        return result("error", ERROR_MESSAGE, "max_turns_exceeded")

    def _finish(
        self, final: FinalAnswer, ctx: RequestContext, canary: str,
        result: Callable[..., AskResult],
    ) -> AskResult:
        if not final.found:
            return result("not_found", NOT_FOUND_MESSAGE, "model_not_found")
        if not ctx.sources:
            return result("not_found", NOT_FOUND_MESSAGE, "ungrounded_claim")  # said found, retrieved nothing
        verdict = validate_answer(
            final,
            allowed_ids=set(ctx.sources),
            allowed_urls={s.url for s in ctx.sources.values()},
            forbidden=[canary, ctx.nonce],
        )
        if not verdict.ok:
            return result("rejected", REJECTED_MESSAGE, verdict.reason)

        citations: list[Citation] = []
        warnings: list[str] = []
        for cid in dict.fromkeys(final.citations):
            s = ctx.sources[cid]
            citations.append(Citation(s.id, s.url, s.title, s.product, s.resource_type,
                                      s.last_updated, s.fetched_at, s.stale))
            if _is_synthetic(s.url):
                warnings.append(
                    f"Source {s.id} is a synthetic fixture, not real OpenText support content."
                )
            if s.stale:
                warnings.append(
                    f"Source {s.id} was last refreshed over {ctx.stale_days} days ago and may be out of date."
                )
        return result("answered", final.answer.strip(), citations=citations, warnings=warnings)

    # -- observability ----------------------------------------------------
    def _log(self, r: AskResult, question: object) -> None:
        q = question if isinstance(question, str) else ""
        cost = None
        if self.price_in is not None and self.price_out is not None:
            cost = round((r.usage.input_tokens * self.price_in + r.usage.output_tokens * self.price_out) / 1e6, 6)
        log_event(
            self.logger, "ask", request_id=r.request_id, status=r.status, reason=r.reason,
            latency_ms=r.latency_ms, model=r.model, input_tokens=r.usage.input_tokens,
            output_tokens=r.usage.output_tokens, est_cost_usd=cost, tool_calls=r.tool_calls,
            sources_retrieved=r.sources_retrieved, cited=[c.id for c in r.citations],
            question_sha256=hashlib.sha256(q.encode()).hexdigest()[:12],
            question_redacted=redact(q)[:200], answer_chars=len(r.answer),
        )
