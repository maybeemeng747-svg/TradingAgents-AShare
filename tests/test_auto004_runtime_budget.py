# [AUTO-004] auto_dev_runtime_budget
"""Tests for AUTO-004: nightly endurance budget + fail-stop regression.

Covers the AUTO-004 additions to scripts/summarize_auto_dev_runs.py:
  - compute_historical_runtimes: average elapsed time per priority
  - estimate_ready_endurance_from_history: hybrid budget projection
  - sort_ready_queue_for_budget: pickup-order mirroring
  - verify_fail_stop_strategy: read-only loop guard verification
  - generate_low_endurance_proposal: proposed supplement when ready < 2h
  - format_runtime_budget_section: report section rendering
  - integration with generate_report and the CLI dry-run
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.summarize_auto_dev_runs import (  # noqa: E402
    _extract_priority_token,
    _parse_run_timestamp,
    compute_historical_runtimes,
    estimate_ready_endurance_from_history,
    format_runtime_budget_section,
    generate_low_endurance_proposal,
    generate_report,
    sort_ready_queue_for_budget,
    verify_fail_stop_strategy,
    TaskRun,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "summarize_auto_dev_runs.py"


# ── helpers ──────────────────────────────────────────────────────────────

def _run(
    run_id: str,
    *,
    task_id: str,
    priority: str,
    started_at: str,
    finished_at: str,
    status: str = "PASS",
) -> TaskRun:
    return TaskRun(
        run_id=run_id,
        path=Path("/tmp") / run_id,
        task_id=task_id,
        task_title="test",
        priority=priority,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
    )


# ── _parse_run_timestamp ─────────────────────────────────────────────────

class TestParseRunTimestamp:
    def test_parse_underscore_format(self):
        ts = _parse_run_timestamp("2026-07-02_20:44:09")
        assert ts == datetime(2026, 7, 2, 20, 44, 9)

    def test_parse_iso_format(self):
        ts = _parse_run_timestamp("2026-07-02T20:44:09")
        assert ts == datetime(2026, 7, 2, 20, 44, 9)

    def test_parse_empty(self):
        assert _parse_run_timestamp("") is None

    def test_parse_none_like(self):
        assert _parse_run_timestamp("") is None

    def test_parse_garbage(self):
        assert _parse_run_timestamp("not-a-date") is None

    def test_parse_strips_whitespace(self):
        ts = _parse_run_timestamp("  2026-07-02_20:44:09  ")
        assert ts == datetime(2026, 7, 2, 20, 44, 9)


class TestExtractPriorityToken:
    def test_plain(self):
        assert _extract_priority_token("P1") == "P1"

    def test_with_chinese_suffix(self):
        assert _extract_priority_token("P1（高）") == "P1"

    def test_p0(self):
        assert _extract_priority_token("P0") == "P0"

    def test_unknown(self):
        assert _extract_priority_token("高") == ""

    def test_empty(self):
        assert _extract_priority_token("") == ""


# ── compute_historical_runtimes ──────────────────────────────────────────

class TestComputeHistoricalRuntimes:
    def test_basic_average(self):
        # 600s and 1200s -> avg 900s
        runs = [
            _run("A-001-20260701-100000", task_id="A-001", priority="P1",
                 started_at="2026-07-01_10:00:00", finished_at="2026-07-01_10:10:00"),
            _run("A-002-20260701-110000", task_id="A-002", priority="P1",
                 started_at="2026-07-01_11:00:00", finished_at="2026-07-01_11:20:00"),
        ]
        result = compute_historical_runtimes(runs)
        assert result["has_history"] is True
        assert result["overall"]["count"] == 2
        assert result["overall"]["avg_seconds"] == 900.0
        assert result["by_priority"]["P1"]["count"] == 2
        assert result["by_priority"]["P1"]["avg_seconds"] == 900.0

    def test_drops_below_minimum(self):
        # 10s elapsed -> below 30s floor, should be dropped
        runs = [
            _run("A-001-20260701-100000", task_id="A-001", priority="P1",
                 started_at="2026-07-01_10:00:00", finished_at="2026-07-01_10:00:10"),
        ]
        result = compute_historical_runtimes(runs)
        assert result["has_history"] is False
        assert result["overall"]["count"] == 0

    def test_drops_above_maximum(self):
        # 7h elapsed -> above 6h cap, should be dropped (clock skew)
        runs = [
            _run("A-001-20260701-100000", task_id="A-001", priority="P1",
                 started_at="2026-07-01_10:00:00", finished_at="2026-07-01_17:00:00"),
        ]
        result = compute_historical_runtimes(runs)
        assert result["has_history"] is False

    def test_skips_missing_timestamps(self):
        runs = [
            _run("A-001-20260701-100000", task_id="A-001", priority="P1",
                 started_at="", finished_at="2026-07-01_10:10:00"),
            _run("A-002-20260701-110000", task_id="A-002", priority="P1",
                 started_at="2026-07-01_11:00:00", finished_at=""),
        ]
        result = compute_historical_runtimes(runs)
        assert result["has_history"] is False

    def test_groups_by_priority(self):
        runs = [
            _run("A-001-20260701-100000", task_id="A-001", priority="P0",
                 started_at="2026-07-01_10:00:00", finished_at="2026-07-01_10:10:00"),
            _run("A-002-20260701-110000", task_id="A-002", priority="P2",
                 started_at="2026-07-01_11:00:00", finished_at="2026-07-01_11:30:00"),
        ]
        result = compute_historical_runtimes(runs)
        assert set(result["by_priority"].keys()) == {"P0", "P2"}
        assert result["by_priority"]["P0"]["avg_seconds"] == 600.0
        assert result["by_priority"]["P2"]["avg_seconds"] == 1800.0

    def test_empty_input(self):
        result = compute_historical_runtimes([])
        assert result["has_history"] is False
        assert result["overall"]["count"] == 0

    def test_negative_elapsed_dropped(self):
        # finished before started -> negative, dropped
        runs = [
            _run("A-001-20260701-100000", task_id="A-001", priority="P1",
                 started_at="2026-07-01_10:10:00", finished_at="2026-07-01_10:00:00"),
        ]
        result = compute_historical_runtimes(runs)
        assert result["has_history"] is False


# ── estimate_ready_endurance_from_history ────────────────────────────────

class TestEstimateEnduranceFromHistory:
    def test_empty_queue(self):
        result = estimate_ready_endurance_from_history(
            [], {"has_history": False, "by_priority": {}, "overall": {}}
        )
        assert result["empty"] is True
        assert result["count"] == 0
        assert result["low_endurance"] is True
        assert result["meets_target"] is False
        assert result["method"] == "static_fallback"

    def test_uses_historical_when_samples_sufficient(self):
        historical = {
            "has_history": True,
            "by_priority": {"P1": {"count": 3, "avg_seconds": 1200.0, "min": 600, "max": 1800}},
            "overall": {"count": 3, "avg_seconds": 1200.0, "min": 600, "max": 1800},
        }
        queue = [{"task_id": "T-001", "title": "t", "priority": "P1"}]
        result = estimate_ready_endurance_from_history(queue, historical)
        assert result["method"] == "historical"
        assert result["empty"] is False
        # Historical midpoint ~20min, so one task should not meet 3h target
        assert result["meets_target"] is False
        assert result["per_task"][0]["source"] == "historical"

    def test_falls_back_to_static_when_insufficient_samples(self):
        historical = {
            "has_history": False,
            "by_priority": {"P1": {"count": 1, "avg_seconds": 1200.0}},
            "overall": {"count": 0, "avg_seconds": 0},
        }
        queue = [{"task_id": "T-001", "title": "t", "priority": "P1"}]
        result = estimate_ready_endurance_from_history(queue, historical)
        # Only 1 sample (< _MIN_HISTORY_SAMPLES=2) -> static source
        assert result["per_task"][0]["source"] == "static"

    def test_low_endurance_flag(self):
        historical = {"has_history": False, "by_priority": {}, "overall": {}}
        queue = [{"task_id": "T-001", "title": "t", "priority": "P2"}]
        result = estimate_ready_endurance_from_history(
            queue, historical, target_hours=3.0, low_hours=2.0
        )
        # Static P2 budget is 10-30 min, well below 2h -> low_endurance True
        assert result["low_endurance"] is True

    def test_meets_target_with_many_tasks(self):
        historical = {"has_history": False, "by_priority": {}, "overall": {}}
        # 10 P1 tasks @ 20-40 min each = 200-400 min = 3.3-6.6h -> meets 3h
        queue = [
            {"task_id": f"T-{i:03d}", "title": "t", "priority": "P1"}
            for i in range(10)
        ]
        result = estimate_ready_endurance_from_history(
            queue, historical, target_hours=3.0
        )
        assert result["meets_target"] is True
        assert result["low_endurance"] is False

    def test_custom_thresholds(self):
        historical = {"has_history": False, "by_priority": {}, "overall": {}}
        queue = [{"task_id": "T-001", "title": "t", "priority": "P1"}]
        result = estimate_ready_endurance_from_history(
            queue, historical, target_hours=0.1, low_hours=0.05
        )
        # 1 P1 task @ 20-40 min easily exceeds 0.1h target
        assert result["meets_target"] is True

    def test_per_task_count_matches_queue(self):
        historical = {"has_history": False, "by_priority": {}, "overall": {}}
        queue = [
            {"task_id": "T-001", "title": "a", "priority": "P0"},
            {"task_id": "T-002", "title": "b", "priority": "P1"},
            {"task_id": "T-003", "title": "c", "priority": "P2"},
        ]
        result = estimate_ready_endurance_from_history(queue, historical)
        assert len(result["per_task"]) == 3
        ids = {t["task_id"] for t in result["per_task"]}
        assert ids == {"T-001", "T-002", "T-003"}


# ── sort_ready_queue_for_budget ──────────────────────────────────────────

class TestSortReadyQueueForBudget:
    def test_priority_ordering(self):
        queue = [
            {"task_id": "T-002", "title": "p2", "priority": "P2"},
            {"task_id": "T-001", "title": "p0", "priority": "P0"},
            {"task_id": "T-003", "title": "p1", "priority": "P1"},
        ]
        ordered = sort_ready_queue_for_budget(queue)
        ids = [t["task_id"] for t in ordered]
        assert ids == ["T-001", "T-003", "T-002"]

    def test_stable_within_same_priority(self):
        queue = [
            {"task_id": "T-001", "title": "a", "priority": "P1"},
            {"task_id": "T-002", "title": "b", "priority": "P1"},
            {"task_id": "T-003", "title": "c", "priority": "P1"},
        ]
        ordered = sort_ready_queue_for_budget(queue)
        ids = [t["task_id"] for t in ordered]
        assert ids == ["T-001", "T-002", "T-003"]

    def test_empty_queue(self):
        assert sort_ready_queue_for_budget([]) == []
        assert sort_ready_queue_for_budget(None) == []

    def test_unknown_priority_goes_last(self):
        queue = [
            {"task_id": "T-002", "title": "p1", "priority": "P1"},
            {"task_id": "T-001", "title": "weird", "priority": "高"},
        ]
        ordered = sort_ready_queue_for_budget(queue)
        assert ordered[0]["task_id"] == "T-002"
        assert ordered[1]["task_id"] == "T-001"


# ── verify_fail_stop_strategy ────────────────────────────────────────────

class TestVerifyFailStopStrategy:
    def test_real_auto_dev_loop_passes(self):
        result = verify_fail_stop_strategy(REPO_ROOT / "scripts" / "auto_dev_loop.sh")
        assert result["ok"] is True
        assert all(result["checks"].values())
        assert result["script"] == "auto_dev_loop.sh"

    def test_missing_script(self):
        result = verify_fail_stop_strategy(Path("/nonexistent/loop.sh"))
        assert result["ok"] is False
        assert not any(result["checks"].values())

    def test_valid_synthetic_script(self, tmp_path):
        script = tmp_path / "loop.sh"
        script.write_text(textwrap.dedent("""\
            #!/usr/bin/env bash
            if [[ -n "$DIRTY" ]]; then
                err "Working tree dirty, exiting to avoid overwriting user changes"
                exit 1
            fi
            if [ "$RESULT_STATUS" = "DONE" ]; then
                COMPLETED_TASKS=$((COMPLETED_TASKS + 1))
                continue
            elif [ "$RESULT_STATUS" = "QUOTA_EXHAUSTED" ]; then
                FAILED_TASKS=$((FAILED_TASKS + 1))
            else
                FAILED_TASKS=$((FAILED_TASKS + 1))
            fi
            if [ $FAILED_TASKS -gt 0 ]; then
                break
            fi
        """))
        result = verify_fail_stop_strategy(script)
        assert result["ok"] is True
        assert all(result["checks"].values())

    def test_broken_script_no_fail_stop(self, tmp_path):
        script = tmp_path / "loop.sh"
        script.write_text("#!/usr/bin/env bash\necho running\n")
        result = verify_fail_stop_strategy(script)
        assert result["ok"] is False
        assert result["checks"]["break_on_failed_tasks"] is False
        assert result["checks"]["stop_on_dirty_tree"] is False


# ── generate_low_endurance_proposal ─────────────────────────────────────

class TestGenerateLowEnduranceProposal:
    def test_empty_queue(self):
        endurance = {"empty": True, "total_min_minutes": 0, "total_max_minutes": 0}
        out = generate_low_endurance_proposal(endurance)
        assert "ready 队列为空" in out.lower() or "续航补充建议" in out
        assert "空转" in out

    def test_low_endurance_triggers_proposal(self):
        endurance = {
            "empty": False,
            "low_endurance": True,
            "total_min_minutes": 10,
            "total_max_minutes": 30,
        }
        out = generate_low_endurance_proposal(endurance, target_hours=3.0, low_hours=2.0)
        assert "续航补充建议" in out
        assert "建议补充" in out

    def test_sufficient_endurance_no_proposal(self):
        endurance = {
            "empty": False,
            "low_endurance": False,
            "total_min_minutes": 300,
            "total_max_minutes": 400,
        }
        out = generate_low_endurance_proposal(endurance, target_hours=3.0, low_hours=2.0)
        assert out == ""

    def test_proposal_does_not_auto_promote(self):
        endurance = {"empty": True, "total_min_minutes": 0, "total_max_minutes": 0}
        out = generate_low_endurance_proposal(endurance)
        # Must only suggest human review, never claim to flip status itself
        assert "proposed" in out.lower() or "ready" in out.lower()
        assert "人工" in out


# ── format_runtime_budget_section ────────────────────────────────────────

class TestFormatRuntimeBudgetSection:
    def test_renders_all_subsections(self):
        historical = {
            "has_history": True,
            "by_priority": {"P1": {"count": 2, "avg_seconds": 900.0, "min": 600, "max": 1200}},
            "overall": {"count": 2, "avg_seconds": 900.0, "min": 600, "max": 1200},
        }
        endurance = {
            "empty": False,
            "count": 1,
            "total_min_minutes": 15,
            "total_max_minutes": 30,
            "hours_range": "15m-30m",
            "by_priority": {"P1": 1},
            "summary": "test summary",
            "method": "historical",
            "meets_target": False,
            "low_endurance": True,
            "per_task": [
                {"task_id": "T-001", "priority": "P1",
                 "est_min_minutes": 15.0, "est_max_minutes": 30.0, "source": "historical"},
            ],
        }
        fail_stop = {
            "ok": True,
            "checks": {
                "break_on_failed_tasks": True,
                "break_on_quota_exhausted": True,
                "stop_on_dirty_tree": True,
                "continue_only_on_done": True,
            },
            "script": "auto_dev_loop.sh",
        }
        section = format_runtime_budget_section(endurance, historical, fail_stop)
        assert "续航预算与失败即停回归" in section
        assert "历史每任务耗时" in section
        assert "续航估计" in section
        assert "失败即停策略回归" in section
        assert "生效" in section
        assert "T-001" in section

    def test_no_history_message(self):
        historical = {"has_history": False, "by_priority": {}, "overall": {}}
        endurance = {
            "empty": True, "count": 0, "total_min_minutes": 0,
            "total_max_minutes": 0, "hours_range": "0h", "summary": "empty",
        }
        fail_stop = {"ok": False, "checks": {
            "break_on_failed_tasks": False, "break_on_quota_exhausted": False,
            "stop_on_dirty_tree": False, "continue_only_on_done": False,
        }, "script": ""}
        section = format_runtime_budget_section(endurance, historical, fail_stop)
        assert "无历史耗时样本" in section
        assert "缺失" in section

    def test_no_secrets(self):
        historical = {"has_history": False, "by_priority": {}, "overall": {}}
        endurance = {
            "empty": True, "count": 0, "total_min_minutes": 0,
            "total_max_minutes": 0, "hours_range": "0h",
            "summary": "api_key=sk-secret123abc456def789ghi012",
        }
        fail_stop = {"ok": True, "checks": {
            "break_on_failed_tasks": True, "break_on_quota_exhausted": True,
            "stop_on_dirty_tree": True, "continue_only_on_done": True,
        }, "script": ""}
        section = format_runtime_budget_section(endurance, historical, fail_stop)
        assert "sk-secret123" not in section


# ── generate_report integration ──────────────────────────────────────────

class TestGenerateReportAuto004:
    def test_report_includes_runtime_budget_section(self):
        section = "## 续航预算与失败即停回归\n\nAUTO-004 section present\n"
        proposal = "## 续航补充建议（ready 队列为空）\n\n> 空转提示\n"
        report = generate_report(
            [], [], [], "2026-07-02",
            ready_queue=[],
            runtime_budget_section=section,
            low_endurance_proposal=proposal,
        )
        assert "续航预算与失败即停回归" in report
        assert "续航补充建议" in report

    def test_report_without_auto004_sections(self):
        report = generate_report(
            [], [], [], "2026-07-02",
            ready_queue=[],
            runtime_budget_section=None,
            low_endurance_proposal=None,
        )
        assert "续航预算与失败即停回归" not in report


# ── CLI dry-run integration ──────────────────────────────────────────────

class TestCLIDryRun:
    def _make_fixture_repo(self, tmp_path: Path) -> Path:
        """Build a minimal repo with task_runs + TASKS.md + auto_dev_loop.sh."""
        runs_dir = tmp_path / "docs" / "task_runs"
        runs_dir.mkdir(parents=True)

        # Two historical runs with valid elapsed time
        for rid, prio, start, finish in [
            ("T-001-20260701-100000", "P1", "2026-07-01_10:00:00", "2026-07-01_10:15:00"),
            ("T-002-20260701-110000", "P1", "2026-07-01_11:00:00", "2026-07-01_11:20:00"),
        ]:
            rd = runs_dir / rid
            rd.mkdir()
            (rd / "task.md").write_text(textwrap.dedent(f"""\
                # Auto Dev Task Run
                - Task: {rid.split('-2026')[0]} - sample
                - Priority: {prio}
                - Status: CLAIMED
                - Started at: {start}
                - Git HEAD: abc1234
                - Test commands: pytest tests/ -q
                - Runner: scripts/auto_dev_loop.sh
                """), encoding="utf-8")
            (rd / "summary.md").write_text(textwrap.dedent(f"""\
                # Auto Dev Summary
                - Final status: PASS
                - Rounds: 1
                - Finished at: {finish}
                """), encoding="utf-8")

        # TASKS.md with one ready task
        (tmp_path / "docs" / "TASKS.md").write_text(textwrap.dedent("""\
            ### T-099: Future task（P1）
            - **优先级**: P1
            - **状态**: ready
            """), encoding="utf-8")

        # Minimal auto_dev_loop.sh with fail-stop guards
        (tmp_path / "scripts").mkdir()
        (tmp_path / "scripts" / "auto_dev_loop.sh").write_text(textwrap.dedent("""\
            #!/usr/bin/env bash
            DIRTY=$(git status --porcelain)
            if [ -n "$DIRTY" ]; then
                err "Working tree dirty, exiting"
                exit 1
            fi
            if [ "$RESULT_STATUS" = "DONE" ]; then
                COMPLETED_TASKS=$((COMPLETED_TASKS + 1))
                continue
            elif [ "$RESULT_STATUS" = "QUOTA_EXHAUSTED" ]; then
                FAILED_TASKS=$((FAILED_TASKS + 1))
            else
                FAILED_TASKS=$((FAILED_TASKS + 1))
            fi
            if [ $FAILED_TASKS -gt 0 ]; then
                break
            fi
            """), encoding="utf-8")
        return tmp_path

    def test_dry_run_includes_budget_section(self, tmp_path):
        self._make_fixture_repo(tmp_path)
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--dry-run",
             "--date", "2026-07-02", "--repo-dir", str(tmp_path)],
            capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, result.stderr
        out = result.stdout
        assert "续航预算与失败即停回归" in out
        assert "历史每任务耗时" in out
        assert "失败即停策略回归" in out
        assert "生效" in out
        # Ready queue has 1 P1 task
        assert "T-099" in out

    def test_dry_run_empty_ready_no_idlespin(self, tmp_path):
        runs_dir = tmp_path / "docs" / "task_runs"
        runs_dir.mkdir(parents=True)
        (tmp_path / "docs" / "TASKS.md").write_text(
            "### X-001: done task\n- **状态**: done\n", encoding="utf-8"
        )
        (tmp_path / "scripts").mkdir()
        (tmp_path / "scripts" / "auto_dev_loop.sh").write_text(
            "#!/usr/bin/env bash\n", encoding="utf-8"
        )
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--dry-run",
             "--date", "2026-07-02", "--repo-dir", str(tmp_path)],
            capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, result.stderr
        out = result.stdout
        # Empty ready queue must surface the no-idle-spin notice
        assert "空转" in out

    def test_dry_run_no_runtime_budget_flag(self, tmp_path):
        self._make_fixture_repo(tmp_path)
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--dry-run",
             "--date", "2026-07-02", "--no-runtime-budget",
             "--repo-dir", str(tmp_path)],
            capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, result.stderr
        out = result.stdout
        assert "续航预算与失败即停回归" not in out
        assert "历史每任务耗时" not in out

    def test_dry_run_does_not_modify_tasks_md(self, tmp_path):
        repo = self._make_fixture_repo(tmp_path)
        tasks_md = repo / "docs" / "TASKS.md"
        before = tasks_md.read_text(encoding="utf-8")
        subprocess.run(
            [sys.executable, str(SCRIPT), "--dry-run",
             "--date", "2026-07-02", "--repo-dir", str(repo)],
            capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
        )
        after = tasks_md.read_text(encoding="utf-8")
        assert before == after, "TASKS.md must not be modified by the dry-run"
