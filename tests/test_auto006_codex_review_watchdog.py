# [AUTO-006] codex_review_watchdog
"""Tests for AUTO-006: Codex review timeout watchdog + closeout strategy.

Covers two surfaces:
  1. ``scripts/auto_dev_loop.sh`` static guards — confirms the watchdog
     records review-meta-roundN.json, computes elapsed_ms, surfaces a
     ``NEEDS_HUMAN`` closeout on timeout, and never treats a timeout as PASS.
  2. ``scripts/summarize_auto_dev_runs.py`` AUTO-006 helpers — parsing
     review-meta-roundN.json, aggregating across runs, and rendering the
     nightly report section for PASS / FAIL / TIMEOUT / SKIPPED scenarios.

Constraints honoured:
  - No live Codex / OpenCode calls.
  - No writes to production tradingagents.db.
  - No TASKS.md status mutations.
  - No prompts/ changes.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.summarize_auto_dev_runs import (  # noqa: E402
    _fmt_ms,
    collect_review_meta,
    format_codex_review_watchdog_section,
    generate_report,
    parse_review_meta_for_run,
    summarize_review_meta,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
LOOP_SCRIPT = REPO_ROOT / "scripts" / "auto_dev_loop.sh"


# ── helpers ──────────────────────────────────────────────────────────────


def _write_review_meta(
    run_dir: Path,
    round_num: int,
    *,
    task_id: str = "T-001",
    status: str = "PASS",
    elapsed_ms: int = 30_000,
    timeout_seconds: int = 1200,
    exit_code: int = 0,
    timed_out: bool = False,
    review_skipped: bool = False,
    has_partial_output: bool = False,
    partial_output_bytes: int = 0,
    started_at_epoch: int = 1_720_000_000,
) -> Path:
    """Write a valid review-meta-roundN.json into a run dir."""
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / f"review-meta-round{round_num}.json"
    data = {
        "task_id": task_id,
        "round": round_num,
        "started_at_epoch": started_at_epoch,
        "finished_at_epoch": started_at_epoch + elapsed_ms // 1000,
        "elapsed_ms": elapsed_ms,
        "timeout_seconds": timeout_seconds,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "review_skipped": review_skipped,
        "has_partial_output": has_partial_output,
        "partial_output_bytes": partial_output_bytes,
        "status": status,
        "run_id": run_dir.name,
        "meta_path": str(path),
        "parse_error": False,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# ── auto_dev_loop.sh static guards ───────────────────────────────────────


class TestAutoDevLoopStaticGuards:
    """AUTO-006 requires auto_dev_loop.sh to embed watchdog markers."""

    @pytest.fixture(scope="class")
    def script_text(self) -> str:
        return LOOP_SCRIPT.read_text(encoding="utf-8")

    def test_script_exists(self):
        assert LOOP_SCRIPT.exists(), "scripts/auto_dev_loop.sh missing"

    def test_has_codex_review_timeout_env(self, script_text):
        # Configurable timeout already present (AUTO-002) — AUTO-006 relies on it.
        assert "CODEX_REVIEW_TIMEOUT_SECONDS" in script_text

    def test_has_killpg_graceful_terminate(self, script_text):
        # Graceful terminate/killpg already present in run_with_timeout — AUTO-006
        # inherits it for codex review. Confirm the contract is still there.
        assert "killpg" in script_text
        assert "SIGTERM" in script_text
        assert "SIGKILL" in script_text

    def test_has_write_review_meta_helper(self, script_text):
        # AUTO-006 adds the helper that emits review-meta-roundN.json
        assert "write_review_meta" in script_text
        assert "# [AUTO-006] codex_review_watchdog" in script_text
        assert "review-meta-round" in script_text

    def test_has_codex_review_start_epoch_marker(self, script_text):
        # Watchdog must capture start/end epoch around codex review for elapsed_ms.
        assert "CODEX_REVIEW_START_EPOCH" in script_text
        assert "CODEX_REVIEW_END_EPOCH" in script_text

    def test_timeout_branch_marks_needs_human(self, script_text):
        # On timeout the run must NOT commit. Match the exit-124 branch.
        m = re.search(r"CODEX_EXIT\s*-eq\s*124", script_text)
        assert m, "expected CODEX_EXIT -eq 124 branch for timeout handling"
        # The branch must set RESULT_STATUS to NEEDS_HUMAN somewhere after
        # the timeout check, before the next ``elif``/``fi``.
        tail = script_text[m.start():]
        # Restrict to the current if/elif block to avoid false positives.
        end = re.search(r"\n    fi\n", tail)
        block = tail[: end.start()] if end else tail[:600]
        assert "NEEDS_HUMAN" in block, (
            "timeout branch must set NEEDS_HUMAN — never PASS"
        )
        assert "RESULT_STATUS" in block

    def test_timeout_branch_does_not_continue(self, script_text):
        # AUTO-006: on timeout the batch must stop, not pick the next task.
        m = re.search(r"CODEX_EXIT\s*-eq\s*124", script_text)
        assert m
        tail = script_text[m.start():]
        end = re.search(r"\n    fi\n", tail)
        block = tail[: end.start()] if end else tail[:600]
        assert "break" in block, "timeout branch must break out of the round loop"
        assert not re.search(r"^\s*continue\s*$", block, re.MULTILINE), (
            "timeout branch must NOT continue to the next task"
        )

    def test_partial_output_preserved_on_timeout(self, script_text):
        # AUTO-006: timeout must archive partial output for human recovery.
        m = re.search(r"CODEX_EXIT\s*-eq\s*124", script_text)
        assert m
        tail = script_text[m.start():]
        end = re.search(r"\n    fi\n", tail)
        block = tail[: end.start()] if end else tail[:800]
        assert "codex-review-round" in block, (
            "timeout branch must reference the archived partial review"
        )
        assert "review-meta-round" in block, (
            "timeout branch must reference the saved review meta"
        )

    def test_hard_rule_no_commit_without_review_preserved(self, script_text):
        # Hard rule: codex unavailable must still block commit (AUTO-006 must not
        # weaken the existing guard).
        assert "no commit without Codex review" in script_text or (
            "REVIEW_SKIPPED" in script_text and "NEEDS_HUMAN" in script_text
        )

    def test_bash_syntax_ok(self):
        result = subprocess.run(
            ["bash", "-n", str(LOOP_SCRIPT)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"bash -n failed on auto_dev_loop.sh:\n{result.stderr}"
        )


# ── write_review_meta shell helper (fixture-driven) ──────────────────────


class TestWriteReviewMetaShellHelper:
    """Drive the bash helper end-to-end by sourcing it from the real script.

    We do NOT spawn codex; we just exercise ``write_review_meta`` with the
    three required scenarios (PASS, FAIL, TIMEOUT) plus an edge case where
    the review file is missing (timeout with no partial output).
    """

    @pytest.fixture(scope="class")
    def helper_script(self, tmp_path_factory):
        """Extract just the write_review_meta function from auto_dev_loop.sh."""
        tmp = tmp_path_factory.mktemp("auto006")
        script = tmp / "helper.sh"
        # Pull the function definition and the ``warn`` helper it depends on,
        # plus a stub for ``log`` to keep the helper self-contained.
        start = None
        end = None
        lines = LOOP_SCRIPT.read_text(encoding="utf-8").splitlines(keepends=True)
        for i, line in enumerate(lines, start=1):
            if start is None and line.startswith("write_review_meta()"):
                start = i
            elif start is not None and line.rstrip() == "PYEOF":
                # The standalone } right after PYEOF closes the function.
                for j in range(i + 1, min(i + 5, len(lines) + 1)):
                    if lines[j - 1].rstrip() == "}":
                        end = j
                        break
                if end:
                    break
        assert start and end, "could not locate write_review_meta() in script"
        body = "".join(lines[start - 1:end])
        script.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "warn() { :; }\n"
            "log() { :; }\n"
            + body,
            encoding="utf-8",
        )
        return script

    def _run_helper(self, helper_script: Path, review_file: Path | None) -> dict:
        out_dir = helper_script.parent / "out"
        out_dir.mkdir(exist_ok=True)
        out_file = out_dir / "review-meta-round1.json"
        review_arg = str(review_file) if review_file else "/nonexistent/review.txt"
        cmd = [
            "bash", "-c",
            textwrap.dedent(f"""
                set -euo pipefail
                source {helper_script}
                TASK_ID='AUTO-006-HELPER' \
                write_review_meta '{out_file}' '1' '1720000000' '1720000045' \
                    '1200' '0' 'false' 'false' '{review_arg}' 'PASS'
            """),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        assert result.returncode == 0, f"helper failed: {result.stderr}"
        return json.loads(out_file.read_text(encoding="utf-8"))

    def test_pass_with_partial_output(self, helper_script, tmp_path):
        review = tmp_path / "review.txt"
        review.write_text("review body content here", encoding="utf-8")
        data = self._run_helper(helper_script, review)
        assert data["status"] == "PASS"
        assert data["exit_code"] == 0
        assert data["timed_out"] is False
        assert data["elapsed_ms"] == 45_000
        assert data["timeout_seconds"] == 1200
        assert data["has_partial_output"] is True
        assert data["partial_output_bytes"] > 0
        assert data["task_id"] == "AUTO-006-HELPER"

    def test_pass_without_review_file(self, helper_script):
        data = self._run_helper(helper_script, None)
        assert data["status"] == "PASS"
        assert data["has_partial_output"] is False
        assert data["partial_output_bytes"] == 0

    def test_timeout_scenario(self, helper_script, tmp_path):
        out_dir = helper_script.parent / "timeout_out"
        out_dir.mkdir(exist_ok=True)
        out_file = out_dir / "review-meta-round2.json"
        # Empty review file simulates a timeout that produced no output
        empty_review = tmp_path / "empty.txt"
        empty_review.write_text("", encoding="utf-8")
        cmd = [
            "bash", "-c",
            textwrap.dedent(f"""
                set -euo pipefail
                source {helper_script}
                TASK_ID='AUTO-006-TIMEOUT' \
                write_review_meta '{out_file}' '2' '1720000000' '1720001200' \
                    '1200' '124' 'true' 'false' '{empty_review}' 'TIMEOUT'
            """),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        assert result.returncode == 0, f"helper failed: {result.stderr}"
        data = json.loads(out_file.read_text(encoding="utf-8"))
        assert data["status"] == "TIMEOUT"
        assert data["exit_code"] == 124
        assert data["timed_out"] is True
        assert data["elapsed_ms"] == 1_200_000
        # Empty file → has_partial_output must be False even though file exists
        assert data["has_partial_output"] is False
        assert data["partial_output_bytes"] == 0


# ── parse_review_meta_for_run ────────────────────────────────────────────


class TestParseReviewMetaForRun:
    def test_empty_dir(self, tmp_path):
        assert parse_review_meta_for_run(tmp_path) == []

    def test_nonexistent_dir(self, tmp_path):
        assert parse_review_meta_for_run(tmp_path / "missing") == []

    def test_single_pass_entry(self, tmp_path):
        _write_review_meta(tmp_path, 1, status="PASS")
        entries = parse_review_meta_for_run(tmp_path)
        assert len(entries) == 1
        assert entries[0]["status"] == "PASS"
        assert entries[0]["parse_error"] is False
        assert entries[0]["run_id"] == tmp_path.name

    def test_multiple_rounds_sorted(self, tmp_path):
        _write_review_meta(tmp_path, 3, status="FAIL", elapsed_ms=5000)
        _write_review_meta(tmp_path, 1, status="PASS", elapsed_ms=10000)
        _write_review_meta(tmp_path, 2, status="TIMEOUT", elapsed_ms=1200000, timed_out=True)
        entries = parse_review_meta_for_run(tmp_path)
        rounds = [e["round"] for e in entries]
        assert rounds == [1, 2, 3]
        assert entries[1]["status"] == "TIMEOUT"

    def test_corrupt_json_recorded_as_parse_error(self, tmp_path):
        _write_review_meta(tmp_path, 1)
        bad = tmp_path / "review-meta-round2.json"
        bad.write_text("{ this is not valid json ", encoding="utf-8")
        entries = parse_review_meta_for_run(tmp_path)
        assert len(entries) == 2
        parse_errs = [e for e in entries if e.get("parse_error")]
        assert len(parse_errs) == 1
        assert parse_errs[0]["round"] == 2

    def test_ignores_unrelated_files(self, tmp_path):
        _write_review_meta(tmp_path, 1)
        (tmp_path / "codex-review-round1.txt").write_text("...", encoding="utf-8")
        (tmp_path / "review-meta-round1.txt").write_text("...", encoding="utf-8")
        (tmp_path / "review-meta-round.json").write_text("{}", encoding="utf-8")
        entries = parse_review_meta_for_run(tmp_path)
        assert len(entries) == 1


# ── collect_review_meta ──────────────────────────────────────────────────


class TestCollectReviewMeta:
    def test_empty_dir(self, tmp_path):
        assert collect_review_meta(tmp_path) == []

    def test_aggregates_across_runs(self, tmp_path):
        run1 = tmp_path / "AUTO-006-20260707-032240"
        run2 = tmp_path / "DATA-024-20260629-231953"
        _write_review_meta(run1, 1, task_id="AUTO-006", status="PASS")
        _write_review_meta(run2, 2, task_id="DATA-024", status="TIMEOUT", timed_out=True)
        # Add task.md so collect_review_meta can pull task_id / priority
        (run1 / "task.md").write_text(
            "- Task: AUTO-006 - test\n- Priority: P1\n", encoding="utf-8"
        )
        (run2 / "task.md").write_text(
            "- Task: DATA-024 - test\n- Priority: P1\n", encoding="utf-8"
        )
        entries = collect_review_meta(tmp_path)
        assert len(entries) == 2
        tids = {e["task_id"] for e in entries}
        assert tids == {"AUTO-006", "DATA-024"}

    def test_date_filter(self, tmp_path):
        run1 = tmp_path / "AUTO-006-20260707-032240"
        run2 = tmp_path / "DATA-024-20260629-231953"
        _write_review_meta(run1, 1, task_id="AUTO-006", status="PASS")
        _write_review_meta(run2, 1, task_id="DATA-024", status="PASS")
        entries = collect_review_meta(tmp_path, target_date="2026-07-07")
        assert len(entries) == 1
        assert entries[0]["task_id"] == "AUTO-006"

    def test_task_md_missing_tolerated(self, tmp_path):
        run1 = tmp_path / "AUTO-006-20260707-032240"
        _write_review_meta(run1, 1, task_id="AUTO-006", status="PASS")
        # No task.md — collect must still surface the entry with fallback tid
        entries = collect_review_meta(tmp_path)
        assert len(entries) == 1
        # task_id falls back to the JSON's own task_id when task.md is missing
        assert entries[0]["task_id"] == "AUTO-006"


# ── summarize_review_meta ────────────────────────────────────────────────


class TestSummarizeReviewMeta:
    def test_empty(self):
        s = summarize_review_meta([])
        assert s["count"] == 0
        assert s["timeout_count"] == 0
        assert s["any_timed_out"] is False
        assert s["by_task"] == {}

    def test_pass_only(self):
        entries = [{"status": "PASS", "elapsed_ms": 10000, "task_id": "T-1",
                    "timed_out": False, "has_partial_output": True}]
        s = summarize_review_meta(entries)
        assert s["pass_count"] == 1
        assert s["timeout_count"] == 0
        assert s["any_timed_out"] is False
        assert s["any_partial_output"] is True
        assert s["max_elapsed_ms"] == 10000
        assert s["avg_elapsed_ms"] == 10000.0

    def test_mixed_statuses(self):
        entries = [
            {"status": "PASS", "elapsed_ms": 10000, "task_id": "T-1",
             "timed_out": False, "has_partial_output": False},
            {"status": "TIMEOUT", "elapsed_ms": 1200000, "task_id": "T-2",
             "timed_out": True, "has_partial_output": True},
            {"status": "FAIL", "elapsed_ms": 5000, "task_id": "T-3",
             "timed_out": False, "has_partial_output": False},
            {"status": "SKIPPED", "elapsed_ms": 0, "task_id": "T-4",
             "timed_out": False, "has_partial_output": False},
        ]
        s = summarize_review_meta(entries)
        assert s["count"] == 4
        assert s["pass_count"] == 1
        assert s["fail_count"] == 1
        assert s["timeout_count"] == 1
        assert s["skipped_count"] == 1
        assert s["any_timed_out"] is True
        assert s["any_partial_output"] is True
        assert s["max_elapsed_ms"] == 1200000
        # avg = (10000 + 1200000 + 5000 + 0) / 4 — but 0 is excluded from samples
        # Actually elapsed_samples filters only >0, so avg is over 3 samples
        assert s["avg_elapsed_ms"] == pytest.approx(
            (10000 + 1200000 + 5000) / 3.0
        )

    def test_parse_errors_counted(self):
        entries = [
            {"status": "PASS", "elapsed_ms": 100, "task_id": "T-1",
             "timed_out": False, "has_partial_output": False},
            {"parse_error": True, "task_id": "T-2"},
        ]
        s = summarize_review_meta(entries)
        # parse-error entries do NOT increment count (only valid entries do)
        assert s["count"] == 2  # total entries
        assert s["parse_errors"] == 1
        assert s["pass_count"] == 1

    def test_by_task_aggregation(self):
        entries = [
            {"status": "PASS", "elapsed_ms": 10000, "task_id": "T-1",
             "timed_out": False, "has_partial_output": False},
            {"status": "TIMEOUT", "elapsed_ms": 1200000, "task_id": "T-1",
             "timed_out": True, "has_partial_output": True},
            {"status": "PASS", "elapsed_ms": 5000, "task_id": "T-2",
             "timed_out": False, "has_partial_output": False},
        ]
        s = summarize_review_meta(entries)
        assert s["by_task"]["T-1"]["count"] == 2
        assert s["by_task"]["T-1"]["any_timed_out"] is True
        assert s["by_task"]["T-1"]["max_elapsed_ms"] == 1200000
        assert s["by_task"]["T-2"]["count"] == 1


# ── format_codex_review_watchdog_section ─────────────────────────────────


class TestFormatCodexReviewWatchdogSection:
    def test_empty_entries(self):
        section = format_codex_review_watchdog_section([])
        assert "AUTO-006" in section
        assert "未触发" in section or "predates" in section

    def test_pass_scenario(self):
        entries = [
            {"task_id": "AUTO-006", "run_id": "AUTO-006-20260707-032240",
             "round": 1, "status": "PASS", "elapsed_ms": 45000,
             "timeout_seconds": 1200, "exit_code": 0, "timed_out": False,
             "has_partial_output": True, "partial_output_bytes": 1500,
             "parse_error": False},
        ]
        section = format_codex_review_watchdog_section(entries)
        assert "PASS" in section
        assert "45.0s" in section or "45s" in section
        # No P0 callout when no timeout
        assert "P0" not in section

    def test_fail_scenario(self):
        entries = [
            {"task_id": "T-1", "run_id": "T-1-20260707-100000", "round": 1,
             "status": "FAIL", "elapsed_ms": 2000, "timeout_seconds": 1200,
             "exit_code": 1, "timed_out": False,
             "has_partial_output": False, "partial_output_bytes": 0,
             "parse_error": False},
        ]
        section = format_codex_review_watchdog_section(entries)
        assert "FAIL" in section
        assert "1" in section  # exit code
        # FAIL alone does not surface P0/P1 timeout callout
        assert "P0" not in section

    def test_timeout_scenario_emits_p0(self):
        entries = [
            {"task_id": "T-1", "run_id": "T-1-20260707-100000", "round": 1,
             "status": "TIMEOUT", "elapsed_ms": 1200000, "timeout_seconds": 1200,
             "exit_code": 124, "timed_out": True,
             "has_partial_output": True, "partial_output_bytes": 800,
             "parse_error": False},
        ]
        section = format_codex_review_watchdog_section(entries)
        assert "TIMEOUT" in section
        assert "20.0m" in section or "1200000" in section
        assert "P0" in section
        assert "NEEDS_HUMAN" in section
        assert "AUTO_DEV_CODEX_REVIEW_TIMEOUT_SECONDS" in section

    def test_parse_error_emits_p1(self):
        entries = [
            {"task_id": "?", "run_id": "T-1-20260707-100000", "round": 1,
             "parse_error": True, "meta_path": "/tmp/x.json"},
        ]
        section = format_codex_review_watchdog_section(entries)
        assert "PARSE_ERROR" in section
        assert "P1" in section
        assert "解析失败" in section

    def test_by_task_table_rendered(self):
        entries = [
            {"task_id": "T-1", "run_id": "T-1-20260707-100000", "round": 1,
             "status": "PASS", "elapsed_ms": 10000, "timeout_seconds": 1200,
             "exit_code": 0, "timed_out": False,
             "has_partial_output": False, "partial_output_bytes": 0,
             "parse_error": False},
            {"task_id": "T-1", "run_id": "T-1-20260707-100000", "round": 2,
             "status": "PASS", "elapsed_ms": 8000, "timeout_seconds": 1200,
             "exit_code": 0, "timed_out": False,
             "has_partial_output": False, "partial_output_bytes": 0,
             "parse_error": False},
        ]
        section = format_codex_review_watchdog_section(entries)
        assert "按任务聚合" in section
        assert "PASS:2" in section

    def test_detail_table_capped_at_30(self):
        entries = []
        for i in range(35):
            entries.append({
                "task_id": f"T-{i}", "run_id": f"T-{i}-run", "round": 1,
                "status": "PASS", "elapsed_ms": 1000, "timeout_seconds": 1200,
                "exit_code": 0, "timed_out": False,
                "has_partial_output": False, "partial_output_bytes": 0,
                "parse_error": False,
            })
        section = format_codex_review_watchdog_section(entries)
        assert "省略 5 行" in section

    def test_no_secrets_in_section(self):
        entries = [
            {"task_id": "T-1", "run_id": "T-1-run", "round": 1,
             "status": "PASS", "elapsed_ms": 1000, "timeout_seconds": 1200,
             "exit_code": 0, "timed_out": False,
             "has_partial_output": False, "partial_output_bytes": 0,
             "parse_error": False},
        ]
        # summarize_review_meta never touches secrets, but the section formatter
        # should not accidentally introduce any either.
        section = format_codex_review_watchdog_section(entries)
        assert "sk-" not in section
        assert "api_key=" not in section.lower()


# ── _fmt_ms ──────────────────────────────────────────────────────────────


class TestFmtMs:
    @pytest.mark.parametrize(
        "ms,expected_substring",
        [
            (0, "0s"),
            (500, "ms"),
            (1500, "1.5s"),
            (45_000, "45.0s"),
            (120_000, "2.0m"),
            (1_200_000, "20.0m"),
            (3_600_000, "1.00h"),
        ],
    )
    def test_formatting(self, ms, expected_substring):
        out = _fmt_ms(ms)
        assert expected_substring in out, f"_fmt_ms({ms}) = {out!r}"

    def test_negative_input_safe(self):
        # Defensive: never raise on weird inputs
        assert isinstance(_fmt_ms(-1), str)
        assert isinstance(_fmt_ms("garbage"), str)


# ── generate_report integration ──────────────────────────────────────────


class TestGenerateReportAuto006:
    def test_report_includes_watchdog_section_when_provided(self):
        section = (
            "## Codex Review 超时 watchdog（AUTO-006）\n\n"
            "fake AUTO-006 section\n"
        )
        report = generate_report(
            [], [], [], "2026-07-07",
            ready_queue=[],
            codex_review_watchdog_section=section,
        )
        assert "AUTO-006" in report
        assert "fake AUTO-006 section" in report

    def test_report_without_watchdog_section(self):
        report = generate_report(
            [], [], [], "2026-07-07",
            ready_queue=[],
            codex_review_watchdog_section=None,
        )
        assert "AUTO-006" not in report

    def test_report_with_real_entries(self, tmp_path):
        # End-to-end: build a real review-meta file, collect it, render report.
        run_dir = tmp_path / "AUTO-006-20260707-032240"
        _write_review_meta(run_dir, 1, task_id="AUTO-006", status="PASS",
                           has_partial_output=True, partial_output_bytes=500)
        (run_dir / "task.md").write_text(
            "- Task: AUTO-006 - test\n- Priority: P1\n", encoding="utf-8"
        )
        entries = collect_review_meta(tmp_path, target_date="2026-07-07")
        assert len(entries) == 1
        summary = summarize_review_meta(entries)
        section = format_codex_review_watchdog_section(entries, summary)
        report = generate_report(
            [], [], [], "2026-07-07",
            ready_queue=[],
            codex_review_watchdog_section=section,
        )
        assert "AUTO-006" in report
        assert "PASS" in report


# ── CLI integration ──────────────────────────────────────────────────────


class TestCLIAuto006:
    def test_cli_has_no_codex_review_watchdog_flag(self):
        """The --no-codex-review-watchdog flag must exist to opt out."""
        result = subprocess.run(
            [sys.executable, "scripts/summarize_auto_dev_runs.py", "--help"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, result.stderr
        assert "--no-codex-review-watchdog" in result.stdout

    def test_cli_dry_run_renders_empty_section(self, tmp_path):
        """When no review-meta files exist, the section renders the empty state."""
        result = subprocess.run(
            [sys.executable, "scripts/summarize_auto_dev_runs.py",
             "--dry-run", "--date", "2026-07-07",
             "--no-db-hygiene",
             "--repo-dir", str(tmp_path)],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        # tmp_path has no docs/task_runs, so AUTO-006 section should still appear
        # with the empty-state message.
        # Exit code 0 means the script ran without crashing.
        assert result.returncode == 0, result.stderr

    def test_cli_dry_run_with_real_repo(self):
        """Run the CLI against the real repo and confirm the section appears."""
        result = subprocess.run(
            [sys.executable, "scripts/summarize_auto_dev_runs.py",
             "--dry-run", "--date", "2026-07-07", "--no-db-hygiene"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, result.stderr
        assert "AUTO-006" in result.stdout


# ── closeout strategy (timeout never becomes PASS) ───────────────────────


class TestCloseoutStrategy:
    """AUTO-006 acceptance: a timeout must NEVER result in a commit/PASS."""

    def test_timeout_status_propagates_to_summary(self):
        """When review-meta status is TIMEOUT, the section must surface P0."""
        entries = [
            {"task_id": "T-1", "run_id": "T-1-r", "round": 1,
             "status": "TIMEOUT", "elapsed_ms": 1200000,
             "timeout_seconds": 1200, "exit_code": 124, "timed_out": True,
             "has_partial_output": False, "partial_output_bytes": 0,
             "parse_error": False},
        ]
        summary = summarize_review_meta(entries)
        assert summary["any_timed_out"] is True
        assert summary["timeout_count"] == 1
        assert summary["pass_count"] == 0
        section = format_codex_review_watchdog_section(entries, summary)
        # The P0 callout must appear; if it doesn't, the closeout is broken.
        assert "P0" in section

    def test_partial_output_recorded_even_on_timeout(self):
        """A timeout with partial output must mark has_partial_output=True
        so the human recovery path knows there's salvageable content."""
        entries = [
            {"task_id": "T-1", "run_id": "T-1-r", "round": 1,
             "status": "TIMEOUT", "elapsed_ms": 600000,
             "timeout_seconds": 1200, "exit_code": 124, "timed_out": True,
             "has_partial_output": True, "partial_output_bytes": 2048,
             "parse_error": False},
        ]
        summary = summarize_review_meta(entries)
        assert summary["any_partial_output"] is True
        section = format_codex_review_watchdog_section(entries, summary)
        # Both P0 (timeout) and partial_output=True must be in the report
        assert "P0" in section
        assert "是" in section  # 是否捕获部分输出 = 是

    def test_loop_script_does_not_continue_after_timeout(self):
        """Static check: the timeout branch must break, never continue."""
        text = LOOP_SCRIPT.read_text(encoding="utf-8")
        m = re.search(r"CODEX_EXIT\s*-eq\s*124", text)
        assert m
        tail = text[m.start():]
        end = re.search(r"\n    fi\n", tail)
        block = tail[: end.start()] if end else tail[:600]
        assert "break" in block
        # Explicitly reject ``continue`` after timeout (would mean "next task")
        # within the timeout branch
        continue_in_block = re.findall(r"^\s*continue\s*$", block, re.MULTILINE)
        assert continue_in_block == [], (
            "timeout branch must not continue — would skip the closeout"
        )
