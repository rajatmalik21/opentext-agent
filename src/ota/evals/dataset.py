"""Evaluation dataset loader."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

DOC_BASE = "https://fixtures.invalid/synthetic/"  # reserved non-routable host: never a real page
CATEGORIES = (
    "direct", "poisoned_source", "paraphrase", "ambiguous", "unanswerable", "out_of_scope", "adversarial",
)


@dataclass
class Question:
    id: str
    category: str
    question: str
    acceptable_statuses: list[str]
    gold_urls: list[str] = field(default_factory=list)
    must_include: list[str] = field(default_factory=list)
    forbid_in_output: list[str] = field(default_factory=list)
    paraphrase: bool = False
    notes: str = ""

    @property
    def expect_answer(self) -> bool:
        return self.acceptable_statuses == ["answered"]


def load_dataset(path: str | Path) -> list[Question]:
    questions: list[Question] = []
    seen: set[str] = set()
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        if d["id"] in seen:
            raise ValueError(f"duplicate question id {d['id']}")
        seen.add(d["id"])
        if d["category"] not in CATEGORIES:
            raise ValueError(f"unknown category {d['category']!r} in {d['id']}")
        questions.append(
            Question(
                id=d["id"], category=d["category"], question=d["question"],
                acceptable_statuses=list(d["acceptable_statuses"]),
                gold_urls=[DOC_BASE + g for g in d.get("gold_ids", [])],
                must_include=[w.lower() for w in d.get("must_include", [])],
                forbid_in_output=[w.lower() for w in d.get("forbid_in_output", [])],
                paraphrase=bool(d.get("paraphrase", False)), notes=d.get("notes", ""),
            )
        )
    return questions


def dataset_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]
