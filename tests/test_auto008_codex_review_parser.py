from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scripts.parse_codex_review import classify_review, extract_final_review


REPO_ROOT = Path(__file__).resolve().parents[1]
PARSER = REPO_ROOT / "scripts" / "parse_codex_review.py"
LOOP = REPO_ROOT / "scripts" / "auto_dev_loop.sh"


def test_extracts_only_last_codex_answer() -> None:
    text = "\n".join(
        [
            "codex",
            "- [P1] an earlier draft finding",
            "tool output",
            "codex",
            "I did not identify any introduced correctness issue.",
        ]
    )
    assert extract_final_review(text) == (
        "I did not identify any introduced correctness issue."
    )
    assert classify_review(text)[0] == "CLEAN"


def test_large_review_with_p1_is_not_lost_to_sigpipe() -> None:
    text = "x" * 2_000_000 + "\ncodex\n- [P1] Unsafe path write — file.py:12\n"
    assert classify_review(text)[0] == "FINDINGS"


def test_p2_finding_fails_review() -> None:
    text = "codex\nFull review comments:\n\n- [P2] Stale state — ui.tsx:44\n"
    assert classify_review(text)[0] == "FINDINGS"


def test_p3_only_review_is_non_blocking() -> None:
    text = (
        "codex\nThe patch is correct, with one low-priority suggestion.\n\n"
        "- [P3] Simplify a test helper — tests/test_ui.py:44\n"
    )
    assert classify_review(text)[0] == "CLEAN"


def test_p3_does_not_hide_blocking_finding() -> None:
    text = (
        "codex\nFull review comments:\n\n"
        "- [P3] Simplify a test helper — tests/test_ui.py:44\n"
        "- [P1] Unsafe path write — api/main.py:12\n"
    )
    assert classify_review(text)[0] == "FINDINGS"


def test_context_priority_before_final_answer_is_ignored() -> None:
    text = (
        "task says stop on P0/P1/P2\n"
        "diff contains - [P1] example text\n"
        "codex\nNo actionable correctness issues were found in the reviewed changes.\n"
    )
    assert classify_review(text)[0] == "CLEAN"


def test_common_clean_codex_verdicts_are_recognized() -> None:
    verdicts = (
        "No discrete correctness issues were found in the current changes.",
        (
            "I did not find any actionable correctness, security, performance, "
            "or maintainability issue introduced by these changes."
        ),
        (
            "The code change is correct. I did not find a correctness issue "
            "introduced by the current changes."
        ),
        (
            "The current diff only marks task state, and I did not find a "
            "discrete correctness issue in these changes."
        ),
    )
    for verdict in verdicts:
        assert classify_review(f"codex\n{verdict}\n")[0] == "CLEAN"


def test_ambiguous_positive_sounding_text_fails_closed() -> None:
    text = "codex\nLooks generally reasonable and the tests pass.\n"
    assert classify_review(text)[0] == "UNKNOWN"


def test_missing_codex_marker_fails_closed() -> None:
    assert classify_review("No actionable correctness issues were found.")[0] == "UNKNOWN"


def test_cli_exit_codes(tmp_path: Path) -> None:
    cases = (
        ("clean.txt", "codex\nNo correctness issues were identified.\n", 0),
        ("finding.txt", "codex\n- [P0] Production corruption — db.py:1\n", 10),
        ("unknown.txt", "codex\nTests pass.\n", 20),
    )
    for name, content, expected in cases:
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(PARSER), str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == expected


def test_auto_loop_uses_fail_closed_parser() -> None:
    text = LOOP.read_text(encoding="utf-8")
    assert 'REVIEW_PARSER="$SCRIPT_DIR/parse_codex_review.py"' in text
    assert 'REVIEW_DECISION="FINDINGS"' in text
    assert 'REVIEW_DECISION" != "CLEAN"' in text
    assert "echo \"$REVIEW_CONTENT\" | grep -qiE" not in text
    assert "local _partial_bytes" not in text
    assert "Do not update task status or release downstream tasks" in text
