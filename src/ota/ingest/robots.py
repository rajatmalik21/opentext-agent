"""Minimal robots.txt matcher supporting `*` and `$` (the stdlib parser does not)."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class _Rule:
    allow: bool
    pattern: str
    regex: re.Pattern[str]


def _compile(pattern: str) -> re.Pattern[str]:
    anchored = pattern.endswith("$")
    if anchored:
        pattern = pattern[:-1]
    body = ".*".join(re.escape(part) for part in pattern.split("*"))
    return re.compile("^" + body + ("$" if anchored else ""))


class RobotsRules:
    def __init__(self, rules: list[_Rule]) -> None:
        self._rules = rules

    @classmethod
    def allow_all(cls) -> RobotsRules:
        return cls([])

    @classmethod
    def parse(cls, text: str, agent: str = "*") -> RobotsRules:
        groups: list[tuple[list[str], list[tuple[str, str]]]] = []
        agents: list[str] = []
        rules: list[tuple[str, str]] = []
        in_rules = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            field, _, value = line.partition(":")
            field = field.strip().lower()
            value = value.strip()
            if field == "user-agent":
                if in_rules:
                    groups.append((agents, rules))
                    agents, rules, in_rules = [], [], False
                agents.append(value.lower())
            elif field in ("allow", "disallow"):
                in_rules = True
                rules.append((field, value))
        if agents or rules:
            groups.append((agents, rules))

        agent_l = agent.lower()
        chosen: list[tuple[str, str]] | None = None
        for ags, rs in groups:
            if any(a != "*" and a in agent_l for a in ags):
                chosen = rs
                break
        if chosen is None:
            for ags, rs in groups:
                if "*" in ags:
                    chosen = rs
                    break
        compiled = [
            _Rule(kind == "allow", pat, _compile(pat))
            for kind, pat in (chosen or [])
            if pat  # empty Disallow means "allow everything"
        ]
        return cls(compiled)

    def allowed(self, path_with_query: str) -> bool:
        best: _Rule | None = None
        for rule in self._rules:
            if not rule.regex.match(path_with_query):
                continue
            if (
                best is None
                or len(rule.pattern) > len(best.pattern)
                or (len(rule.pattern) == len(best.pattern) and rule.allow)
            ):
                best = rule
        return True if best is None else best.allow
