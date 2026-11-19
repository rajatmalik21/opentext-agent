"""Minimal secret scan (regex). A backstop, NOT a replacement for a real scanner (gitleaks/trufflehog).

Exit 1 if a likely credential is found. Usage: python tools/scan_secrets.py [root]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PATTERNS = {
    "anthropic-key": re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    "openai-style-key": re.compile(r"\bsk-[A-Za-z0-9]{32,}\b"),
    "github-token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    "aws-access-key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "azure-storage-key": re.compile(r"AccountKey=[A-Za-z0-9+/=]{40,}"),
    "bearer-literal": re.compile(r"Bearer\s+[A-Za-z0-9._\-]{30,}"),
}
SKIP_DIRS = {".git", "__pycache__", ".mypy_cache", ".pytest_cache", ".terraform", "data", ".ruff_cache"}
SKIP_FILES = {"scan_secrets.py", "test_infra_files.py"}  # contain the patterns/examples themselves


def scan(root: Path) -> list[str]:
    hits: list[str] = []
    for p in root.rglob("*"):
        if not p.is_file() or SKIP_DIRS & set(p.relative_to(root).parts) or p.name in SKIP_FILES:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for name, rx in PATTERNS.items():
            if rx.search(text):
                hits.append(f"{p.relative_to(root)}: {name}")
    return hits


if __name__ == "__main__":
    found = scan(Path(sys.argv[1] if len(sys.argv) > 1 else "."))
    for h in found:
        print("POSSIBLE SECRET:", h)
    sys.exit(1 if found else 0)
