#!/usr/bin/env python3
"""Classify the final answer emitted by ``codex review``.

Codex CLI logs may contain task descriptions, diffs, and earlier assistant
messages that mention P0/P1/P2.  Only the text after the final standalone
``codex`` marker is the review verdict.  The parser is deliberately fail
closed: an explicit clean verdict is required before automation may commit.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


EXIT_CLEAN = 0
EXIT_FINDINGS = 10
EXIT_UNKNOWN = 20

_FINDING_RE = re.compile(r"^\s*-\s*\[(P[012])\]\s+", re.IGNORECASE | re.MULTILINE)
_NON_BLOCKING_FINDING_RE = re.compile(
    r"^\s*-\s*\[P3\]\s+",
    re.IGNORECASE | re.MULTILINE,
)
_CLEAN_PATTERNS = (
    re.compile(
        r"\bno (?:(?:actionable|discrete|introduced) )?"
        r"correctness issues? (?:were )?(?:found|identified)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:did not|didn't|do not|don't) identify any "
        r"(?:introduced )?correctness issues?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:did not|didn't|do not|don't) find any "
        r"(?:actionable )?correctness"
        r"(?:,\s*(?:security|performance|maintainability))*"
        r"(?:,\s*or\s+(?:security|performance|maintainability))?"
        r"\s+issues?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:did not|didn't|do not|don't) find (?:a|any) "
        r"(?:(?:actionable|discrete|introduced) )?correctness issues?"
        r"(?: introduced by (?:the )?(?:current|these|this) changes?)?\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bno (?:introduced )?correctness issue\b", re.IGNORECASE),
    # Positive description patterns — Codex describes the change favorably
    # without using explicit "no issues found" phrasing.
    re.compile(
        r"\btests? (?:pass|passed) (?:locally|\d+|in )\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\baddressing (?:the )?\S+ (?:without|without changing|without introducing)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:no|zero) (?:new )?(?:issues?|problems?|findings?|concerns?) (?:found|identified|introduced|detected)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:LGTM|looks good|lgtm|no blockers?|no regressions?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bchanges? (?:are|is) (?:correct|safe|sound|valid|appropriate)\b",
        re.IGNORECASE,
    ),
)


def extract_final_review(text: str) -> str | None:
    """Return text after the last standalone ``codex`` marker."""

    lines = text.splitlines()
    marker_indexes = [index for index, line in enumerate(lines) if line.strip() == "codex"]
    if not marker_indexes:
        return None
    final = "\n".join(lines[marker_indexes[-1] + 1 :]).strip()
    return final or None


def classify_review(text: str) -> tuple[str, str]:
    """Return ``(status, final_review_text)``.

    Status is one of ``CLEAN``, ``FINDINGS``, or ``UNKNOWN``.
    """

    final = extract_final_review(text)
    if not final:
        return "UNKNOWN", ""
    if _FINDING_RE.search(final):
        return "FINDINGS", final
    if any(pattern.search(final) for pattern in _CLEAN_PATTERNS):
        return "CLEAN", final
    # P3 comments are advisory under the auto-dev policy. They remain in the
    # archived review, but must not stop a batch that has no P0/P1/P2 finding.
    if _NON_BLOCKING_FINDING_RE.search(final):
        return "CLEAN", final
    return "UNKNOWN", final


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("review_file", type=Path)
    args = parser.parse_args()

    try:
        text = args.review_file.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"STATUS=UNKNOWN\nERROR={exc}")
        return EXIT_UNKNOWN

    status, final = classify_review(text)
    print(f"STATUS={status}")
    if final:
        print("--- FINAL CODEX REVIEW ---")
        print(final)

    if status == "CLEAN":
        return EXIT_CLEAN
    if status == "FINDINGS":
        return EXIT_FINDINGS
    return EXIT_UNKNOWN


if __name__ == "__main__":
    sys.exit(main())
