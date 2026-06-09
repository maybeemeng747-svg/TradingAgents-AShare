# [V-002] nightly_acceptance_report
"""Tests for V-002: nightly acceptance report with candidate sample replay."""

import sys
import os
import tempfile
import textwrap

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.summarize_auto_dev_runs import (
    redact,
    parse_ready_queue,
    parse_test_summary_from_logs,
    run_sample_replay,
    format_sample_replay,
    generate_report,
    TaskRun,
)
from pathlib import Path


class TestRedact:
    def test_redact_sk_key(self):
        assert "sk-" not in redact("key=sk-abc123def456ghi789jkl012mno345")
        assert "[REDACTED_API_KEY]" in redact("key=sk-abc123def456ghi789jkl012mno345")

    def test_redact_api_key(self):
        result = redact("api_key=mysecretkey123")
        assert "mysecretkey123" not in result
        assert "[REDACTED]" in result

    def test_redact_bearer(self):
        result = redact("Authorization: Bearer tok_abcdef123456")
        assert "tok_abcdef123456" not in result

    def test_redact_token_equals(self):
        result = redact("token=secret_value_here")
        assert "secret_value_here" not in result

    def test_redact_password(self):
        result = redact("password=my_pass_123")
        assert "my_pass_123" not in result

    def test_redact_secret(self):
        result = redact("secret=abc123xyz")
        assert "abc123xyz" not in result

    def test_redact_preserves_normal_text(self):
        text = "This is a normal report with no secrets"
        assert redact(text) == text

    def test_redact_env_var_style(self):
        result = redact("API_KEY=sk-proj-abc123def456ghi789")
        assert "sk-proj-abc123def456ghi789" not in result


class TestParseReadyQueue:
    def _write_tasks_md(self, tmpdir: Path, content: str) -> Path:
        p = tmpdir / "TASKS.md"
        p.write_text(content, encoding="utf-8")
        return p

    def test_parse_single_ready(self, tmp_path):
        content = textwrap.dedent("""\
        ### T-006: Some task title（P1）
        - **优先级**: P1
        - **状态**: ready
        """)
        p = self._write_tasks_md(tmp_path, content)
        result = parse_ready_queue(p)
        assert len(result) == 1
        assert result[0]["task_id"] == "T-006"
        assert "P1" in result[0]["priority"]

    def test_parse_multi_segment_ready_id(self, tmp_path):
        content = textwrap.dedent("""\
        ### TF-QUALITY-001: TradeFlow 候选池严格收敛门禁（P0）
        - **优先级**: P0
        - **状态**: ready
        """)
        p = self._write_tasks_md(tmp_path, content)
        result = parse_ready_queue(p)
        assert len(result) == 1
        assert result[0]["task_id"] == "TF-QUALITY-001"
        assert "P0" in result[0]["priority"]

    def test_parse_multiple_ready(self, tmp_path):
        content = textwrap.dedent("""\
        ### T-006: First task（P1）
        - **优先级**: P1
        - **状态**: ready

        ### M-005: Second task（P2）
        - **优先级**: P2
        - **状态**: ready

        ### S-001: Done task（P1）
        - **优先级**: P1
        - **状态**: done
        """)
        p = self._write_tasks_md(tmp_path, content)
        result = parse_ready_queue(p)
        assert len(result) == 2
        ids = [r["task_id"] for r in result]
        assert "T-006" in ids
        assert "M-005" in ids
        assert "S-001" not in ids

    def test_parse_empty_ready(self, tmp_path):
        content = textwrap.dedent("""\
        ### S-001: Done task（P1）
        - **优先级**: P1
        - **状态**: done
        """)
        p = self._write_tasks_md(tmp_path, content)
        result = parse_ready_queue(p)
        assert len(result) == 0

    def test_parse_no_file(self):
        result = parse_ready_queue(Path("/nonexistent/TASKS.md"))
        assert result == []

    def test_parse_blocked_and_proposed(self, tmp_path):
        content = textwrap.dedent("""\
        ### V-001: Blocked task（P1）
        - **优先级**: P1
        - **状态**: blocked

        ### B-001: Proposed task（高）
        - **优先级**: 高
        - **状态**: proposed

        ### T-006: Ready task（P1）
        - **优先级**: P1
        - **状态**: ready
        """)
        p = self._write_tasks_md(tmp_path, content)
        result = parse_ready_queue(p)
        assert len(result) == 1
        assert result[0]["task_id"] == "T-006"


class TestParseTestSummaryFromLogs:
    def test_parse_standard_pytest_output(self, tmp_path):
        run_dir = tmp_path / "T-001-20260530-120000"
        run_dir.mkdir()
        (run_dir / "tests-round1.txt").write_text(
            "=== short test summary ===\n1451 passed, 15 skipped, 0 failed\n",
            encoding="utf-8",
        )
        run = TaskRun(run_id="T-001-20260530-120000", path=run_dir, trace_files=["tests-round1.txt"])
        result = parse_test_summary_from_logs(run)
        assert result["passed"] == 1451
        assert result["failed"] == 0
        assert result["skipped"] == 15

    def test_parse_with_failures(self, tmp_path):
        run_dir = tmp_path / "M-003-20260530-120000"
        run_dir.mkdir()
        (run_dir / "tests-round1.txt").write_text(
            "120 passed, 3 failed, 5 skipped\n",
            encoding="utf-8",
        )
        run = TaskRun(run_id="M-003-20260530-120000", path=run_dir, trace_files=["tests-round1.txt"])
        result = parse_test_summary_from_logs(run)
        assert result["passed"] == 120
        assert result["failed"] == 3
        assert result["skipped"] == 5

    def test_parse_no_test_files(self, tmp_path):
        run_dir = tmp_path / "X-001-20260530-120000"
        run_dir.mkdir()
        run = TaskRun(run_id="X-001-20260530-120000", path=run_dir, trace_files=["opencode-round1.txt"])
        result = parse_test_summary_from_logs(run)
        assert result["passed"] == 0
        assert result["failed"] == 0

    def test_parse_errors(self, tmp_path):
        run_dir = tmp_path / "E-001-20260530-120000"
        run_dir.mkdir()
        (run_dir / "tests-round1.txt").write_text(
            "100 passed, 2 errors\n",
            encoding="utf-8",
        )
        run = TaskRun(run_id="E-001-20260530-120000", path=run_dir, trace_files=["tests-round1.txt"])
        result = parse_test_summary_from_logs(run)
        assert result["passed"] == 100
        assert result["errors"] == 2

    def test_parse_latest_round(self, tmp_path):
        run_dir = tmp_path / "R-001-20260530-120000"
        run_dir.mkdir()
        (run_dir / "tests-round1.txt").write_text(
            "3 failed, 120 passed\n",
            encoding="utf-8",
        )
        (run_dir / "tests-round2.txt").write_text(
            "1495 passed, 17 skipped, 0 failed\n",
            encoding="utf-8",
        )
        run = TaskRun(
            run_id="R-001-20260530-120000",
            path=run_dir,
            trace_files=["tests-round1.txt", "tests-round2.txt"],
        )
        result = parse_test_summary_from_logs(run)
        assert result["passed"] == 1495
        assert result["failed"] == 0


class TestSampleReplay:
    def test_run_sample_replay_returns_results(self):
        results = run_sample_replay()
        assert isinstance(results, list)
        assert len(results) >= 6

    def test_replay_covers_required_scenarios(self):
        results = run_sample_replay()
        names = [r.get("fixture_name", "") for r in results]
        required = ["vcp_hit", "liquidity_filter", "no_strategy_hit",
                     "event_catalyst", "fund_flow_anomaly", "risk_demoted"]
        for req in required:
            assert req in names, f"Missing fixture: {req}"

    def test_replay_all_pass(self):
        results = run_sample_replay()
        for r in results:
            cat_match = r.get("category_match", r.get("subcategory_match", True))
            fp_match = r.get("fp_match", r.get("fn_match", True))
            if cat_match is not None and fp_match is not None:
                assert cat_match and fp_match, (
                    f"Fixture {r.get('fixture_name')} failed: "
                    f"cat_match={cat_match}, fp_match={fp_match}"
                )

    def test_format_sample_replay_produces_markdown(self):
        results = run_sample_replay()
        md = format_sample_replay(results)
        assert "## 候选样本回放" in md
        assert "vcp_hit" in md
        assert "liquidity_filter" in md
        assert "risk_demoted" in md
        assert "PASS" in md

    def test_format_empty_replay(self):
        md = format_sample_replay([])
        assert md == ""


class TestGenerateReportV002:
    def test_report_with_ready_queue_nonempty(self):
        runs = []
        commits = []
        reviews = []
        ready_queue = [
            {"task_id": "T-006", "title": "Some task", "priority": "P1"},
            {"task_id": "S-006", "title": "Another task", "priority": "P1"},
        ]
        report = generate_report(runs, commits, reviews, "2026-05-30",
                                 ready_queue=ready_queue, replay_results=None)
        assert "Ready 队列" in report
        assert "T-006" in report
        assert "2 个任务" in report
        assert "任务池不足" not in report

    def test_report_with_empty_ready_queue(self):
        runs = []
        commits = []
        reviews = []
        report = generate_report(runs, commits, reviews, "2026-05-30",
                                 ready_queue=[], replay_results=None)
        assert "任务池不足" in report
        assert "夜间 cron 不应空转" in report

    def test_report_without_ready_queue(self):
        runs = []
        commits = []
        reviews = []
        report = generate_report(runs, commits, reviews, "2026-05-30",
                                 ready_queue=None, replay_results=None)
        assert "Ready 队列" not in report

    def test_report_with_sample_replay(self):
        runs = []
        commits = []
        reviews = []
        replay_results = run_sample_replay()
        report = generate_report(runs, commits, reviews, "2026-05-30",
                                 ready_queue=[], replay_results=replay_results)
        assert "候选样本回放" in report
        assert "vcp_hit" in report

    def test_report_with_test_results(self, tmp_path):
        run_dir = tmp_path / "M-004-20260530-120000"
        run_dir.mkdir()
        (run_dir / "tests-round1.txt").write_text(
            "1451 passed, 15 skipped, 0 failed\n",
            encoding="utf-8",
        )
        runs = [TaskRun(
            run_id="M-004-20260530-120000",
            path=run_dir,
            task_id="M-004",
            task_title="Test task",
            status="PASS",
            trace_files=["tests-round1.txt"],
        )]
        report = generate_report(runs, [], [], "2026-05-30",
                                 ready_queue=[], replay_results=None)
        assert "测试结果汇总" in report
        assert "1451 passed" in report

    def test_report_no_secrets(self):
        runs = [TaskRun(
            run_id="X-001",
            path=Path("/tmp"),
            task_id="X-001",
            status="PASS",
            reason="Used api_key=sk-abc123def456ghi789jkl012 for testing",
        )]
        report = generate_report(runs, [], [], "2026-05-30",
                                 ready_queue=None, replay_results=None)
        assert "sk-abc123" not in report
        assert "[REDACTED]" in report or "[REDACTED_API_KEY]" in report

    def test_full_report_dry_run(self):
        runs = [TaskRun(
            run_id="M-004-20260530-120000",
            path=Path("/tmp"),
            task_id="M-004",
            task_title="Config task",
            priority="P1",
            status="PASS",
            git_head="abc1234",
            rounds=1,
        )]
        commits = [{"hash": "abc1234", "message": "feat: M-004", "author": "bot", "date": "2026-05-30T12:00:00"}]
        ready_queue = [{"task_id": "T-006", "title": "Next task", "priority": "P1"}]
        replay_results = run_sample_replay()

        report = generate_report(runs, commits, [], "2026-05-30",
                                 ready_queue=ready_queue, replay_results=replay_results)

        assert "自动开发日报" in report
        assert "总览" in report
        assert "M-004" in report
        assert "提交记录" in report
        assert "Ready 队列" in report
        assert "候选样本回放" in report
        assert "T-006" in report
        assert "vcp_hit" in report

    def test_report_contains_ready_queue_count_in_header(self):
        ready_queue = [{"task_id": "T-006", "title": "Task", "priority": "P1"}]
        report = generate_report([], [], [], "2026-05-30",
                                 ready_queue=ready_queue, replay_results=None)
        assert "Ready 队列: 1 个任务" in report


class TestDryRunIntegration:
    def test_dry_run_with_fixtures(self, tmp_path):
        import subprocess
        result = subprocess.run(
            [sys.executable, "scripts/summarize_auto_dev_runs.py",
             "--dry-run", "--with-sample-replay",
             "--date", "2026-05-30",
             "--repo-dir", str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(Path(__file__).parent.parent),
        )
        assert result.returncode == 0
        output = result.stdout
        assert "自动开发日报" in output
        assert "Ready 队列" in output
        assert "任务池不足" in output

    def test_dry_run_without_replay(self, tmp_path):
        import subprocess
        result = subprocess.run(
            [sys.executable, "scripts/summarize_auto_dev_runs.py",
             "--dry-run",
             "--date", "2026-05-30",
             "--repo-dir", str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(Path(__file__).parent.parent),
        )
        assert result.returncode == 0
        output = result.stdout
        assert "自动开发日报" in output
        assert "候选样本回放" not in output
