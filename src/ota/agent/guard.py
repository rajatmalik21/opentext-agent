"""Deterministic guardrails: input checks, output validation, log redaction.

These are defence in depth. They reduce risk; they do not make injection impossible.
Known limits are listed in docs/decisions/0005-agent-guardrails.md.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, ValidationError

from ..ingest.normalize import canonical_url

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_WS = re.compile(r"[ \t]+")

# Narrow on purpose: these catch the common override / extraction phrasings. Anything
# subtler is handled structurally (untrusted-data framing, validation of the output).
_ATTACK_PATTERNS = [
    re.compile(
        r"\b(ignore|disregard|forget|override)\b.{0,40}\b(previous|prior|above|earlier|all|your)\b"
        r".{0,40}\b(instructions?|prompts?|rules?|guidelines?)\b", re.I | re.S),
    re.compile(
        r"\b(reveal|show|print|repeat|output|leak|display|tell me)\b.{0,50}"
        r"\b(system|developer|hidden|initial|original)\b.{0,20}\b(prompts?|instructions?|messages?|rules?)\b",
        re.I | re.S),
    re.compile(r"\b(api[ _-]?keys?|secret keys?|credentials?|passwords?)\b.{0,30}\b(you|your|system)\b", re.I | re.S),
    re.compile(r"\b(jailbreak|DAN mode|developer mode)\b", re.I),
]

_URL = re.compile(r"https?://[^\s<>\"'\])]+", re.I)

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_SECRET = re.compile(r"\b(?:sk|key|tok|pat)[-_][A-Za-z0-9_-]{10,}\b", re.I)
_NUMBER = re.compile(r"(?<!\w)\+?\d[\d\s().-]{7,}\d(?!\w)")


def neutralize(text: str) -> str:
    """Stop untrusted text from forging our block delimiters."""
    return text.replace("<<", "< <").replace(">>", "> >")


def validate_input(question: object, max_chars: int) -> tuple[str | None, str]:
    """Returns (cleaned_question, error_code). error_code is '' when valid."""
    if not isinstance(question, str):
        return None, "not_a_string"
    cleaned = _WS.sub(" ", _CONTROL.sub("", question)).strip()
    if not cleaned:
        return None, "empty"
    if len(cleaned) > max_chars:
        return None, "too_long"
    return cleaned, ""


def looks_like_attack(question: str) -> bool:
    return any(p.search(question) for p in _ATTACK_PATTERNS)


def redact(text: str) -> str:
    text = _EMAIL.sub("[email]", text)
    text = _SECRET.sub("[secret]", text)
    return _NUMBER.sub("[number]", text)


class FinalAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")
    found: bool
    answer: str = ""
    citations: list[str] = []


def parse_final(text: str) -> FinalAnswer | None:
    candidates = [text.strip()]
    stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
    candidates.append(stripped)
    lo, hi = text.find("{"), text.rfind("}")
    if 0 <= lo < hi:
        candidates.append(text[lo : hi + 1])
    for c in candidates:
        try:
            return FinalAnswer.model_validate(json.loads(c))
        except (ValueError, ValidationError):
            continue
    return None


@dataclass
class Verdict:
    ok: bool
    reason: str = ""


def validate_answer(
    final: FinalAnswer,
    allowed_ids: set[str],
    allowed_urls: set[str],
    forbidden: list[str],
    max_chars: int = 2000,
) -> Verdict:
    """Checks a found=true answer. `forbidden` = canary, nonce and similar leak markers."""
    if not final.answer.strip():
        return Verdict(False, "empty_answer")
    if len(final.answer) > max_chars:
        return Verdict(False, "answer_too_long")
    if not final.citations:
        return Verdict(False, "no_citations")
    unknown = [c for c in final.citations if c not in allowed_ids]
    if unknown:
        return Verdict(False, "unknown_citation")
    low = final.answer.lower()
    for marker in forbidden:
        if marker and marker.lower() in low:
            return Verdict(False, "leak_marker")
    if "<<source" in low or "<<end" in low or "<<user" in low:
        return Verdict(False, "delimiter_echo")
    for raw in _URL.findall(final.answer):
        canon = canonical_url(raw.rstrip(".,;:!?"))
        if canon is None or canon not in allowed_urls:
            return Verdict(False, "foreign_url")
    return Verdict(True)
