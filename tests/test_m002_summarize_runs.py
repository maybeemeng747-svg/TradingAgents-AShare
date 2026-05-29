"""Tests for scripts/summarize_auto_dev_runs.py — [M-002] auto_dev_report_index"""  # noqa: E501

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "summarize_auto_dev_runs.py"
REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_summarize(
    tmp_path: Path,
    date: str = "2026-05-28",
    dry_run: bool = True,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "python3",
            str(SCRIPT),
            "--date", date,
            *(["--dry-run"] if dry_run else []),
            "--repo-dir", str(tmp_path),
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=30,
    )


def _create_fixture_task_run(
    base: Path,
    run_id: str,
    task_line: str,
    priority: str = "P1",
    status: str = "CLAIMED",
    started_at: str = "2026-05-28_20:03:20",
    git_head: str = "abc1234",
) -> Path:
    run_dir = base / "docs" / "task_runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "task.md").write_text(
        textwrap.dedent(f"""\
        # Auto Dev Task Run

        - Task: {task_line}
        - Priority: {priority}
        - Status: {status}
        - Started at: {started_at}
        - Git HEAD: {git_head}
        - Test commands: pytest tests/ -q --tb=short
        - Runner: scripts/auto_dev_loop.sh
        """),
        encoding="utf-8",
    )
    return run_dir


def _create_fixture_summary(
    run_dir: Path,
    final_status: str = "PASS",
    rounds: int = 1,
    reason: str = "",
    finished_at: str = "2026-05-28_20:10:00",
) -> None:
    lines = [
        "# Auto Dev Summary",
        "",
        f"- Task: test task",
        f"- Priority: P1",
        f"- Final status: {final_status}",
        f"- Rounds: {rounds}",
    ]
    if final_status == "PASS":
        lines.append("- Tests: PASS")
        lines.append("- Codex review: no P0/P1 findings")
        lines.append("- Review file: docs/reviews/TEST-20260528-round1.txt")
    if reason:
        lines.append(f"- Reason: {reason}")
    lines.append(f"- Run directory: {run_dir.name}")
    lines.append(f"- Finished at: {finished_at}")
    (run_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestRedaction:
    def test_redact_api_key(self):
        from scripts.summarize_auto_dev_runs import redact
        assert "sk-abc123def456ghi789jkl012mno345" not in redact(
            "key=sk-abc123def456ghi789jkl012mno345pqr678"
        )

    def test_redact_authorization_header(self):
        from scripts.summarize_auto_dev_runs import redact
        result = redact("Authorization: Bearer secret-token-12345")
        assert "secret-token-12345" not in result
        assert "REDACTED" in result

    def test_redact_preserves_normal_text(self):
        from scripts.summarize_auto_dev_runs import redact
        text = "Task PASS, tests passed, commit abc1234"
        assert redact(text) == text


class TestScanTaskRuns:
    def test_scan_empty_dir(self, tmp_path):
        from scripts.summarize_auto_dev_runs import scan_task_runs
        runs = scan_task_runs(tmp_path / "nonexistent")
        assert runs == []

    def test_scan_single_pass_run(self, tmp_path):
        from scripts.summarize_auto_dev_runs import scan_task_runs
        base = tmp_path
        run_dir = _create_fixture_task_run(
            base, "TEST-001-20260528-200320",
            "TEST-001 — Test task title",
        )
        _create_fixture_summary(run_dir, final_status="PASS")

        runs = scan_task_runs(base / "docs" / "task_runs", target_date="2026-05-28")
        assert len(runs) == 1
        assert runs[0].task_id == "TEST-001"
        assert runs[0].task_title == "Test task title"
        assert runs[0].status == "PASS"
        assert runs[0].rounds == 1
        assert runs[0].has_summary

    def test_scan_filters_by_date(self, tmp_path):
        from scripts.summarize_auto_dev_runs import scan_task_runs
        base = tmp_path
        _create_fixture_task_run(
            base, "A-001-20260527-100000", "A-001 — Yesterday"
        )
        _create_fixture_task_run(
            base, "B-001-20260528-200000", "B-001 — Today"
        )

        runs = scan_task_runs(base / "docs" / "task_runs", target_date="2026-05-28")
        assert len(runs) == 1
        assert runs[0].task_id == "B-001"

    def test_scan_no_date_filter(self, tmp_path):
        from scripts.summarize_auto_dev_runs import scan_task_runs
        base = tmp_path
        _create_fixture_task_run(
            base, "A-001-20260527-100000", "A-001 — Yesterday"
        )
        _create_fixture_task_run(
            base, "B-001-20260528-200000", "B-001 — Today"
        )

        runs = scan_task_runs(base / "docs" / "task_runs", target_date=None)
        assert len(runs) == 2

    def test_scan_needs_human_run(self, tmp_path):
        from scripts.summarize_auto_dev_runs import scan_task_runs
        base = tmp_path
        run_dir = _create_fixture_task_run(
            base, "FAIL-001-20260528-210000", "FAIL-001 — Failing task",
        )
        _create_fixture_summary(
            run_dir, final_status="NEEDS_HUMAN", rounds=2,
            reason="Test failed: timeout",
        )

        runs = scan_task_runs(base / "docs" / "task_runs", target_date="2026-05-28")
        assert len(runs) == 1
        assert runs[0].status == "NEEDS_HUMAN"
        assert runs[0].rounds == 2
        assert "timeout" in runs[0].reason

    def test_trace_files_collected(self, tmp_path):
        from scripts.summarize_auto_dev_runs import scan_task_runs
        base = tmp_path
        run_dir = _create_fixture_task_run(
            base, "T-001-20260528-200000", "T-001 — Trace test",
        )
        (run_dir / "opencode-round1.txt").write_text("log", encoding="utf-8")
        (run_dir / "tests-round1.txt").write_text("test output", encoding="utf-8")

        runs = scan_task_runs(base / "docs" / "task_runs", target_date="2026-05-28")
        assert len(runs) == 1
        assert "opencode-round1.txt" in runs[0].trace_files
        assert "tests-round1.txt" in runs[0].trace_files
        assert "task.md" not in runs[0].trace_files


class TestGenerateReport:
    def test_report_contains_header_and_table(self):
        from scripts.summarize_auto_dev_runs import TaskRun, generate_report
        from pathlib import Path

        runs = [
            TaskRun(
                run_id="TEST-001-20260528-200000",
                path=Path("/tmp"),
                task_id="TEST-001",
                task_title="Test task",
                priority="P1",
                status="PASS",
                rounds=1,
                has_summary=True,
                summary_content="summary content here",
            ),
        ]
        report = generate_report(runs, [], [], "2026-05-28")
        assert "# 自动开发日报 — 2026-05-28" in report
        assert "TEST-001" in report
        assert "PASS" in report
        assert "总览" in report

    def test_report_no_runs(self):
        from scripts.summarize_auto_dev_runs import generate_report
        report = generate_report([], [], [], "2026-05-28")
        assert "运行档案数: 0" in report

    def test_report_includes_commits(self):
        from scripts.summarize_auto_dev_runs import generate_report
        commits = [
            {"hash": "abc1234", "message": "fix: something", "author": "bot", "date": "2026-05-28 20:05:00 +0800"},
        ]
        report = generate_report([], commits, [], "2026-05-28")
        assert "abc1234" in report
        assert "fix: something" in report

    def test_report_includes_risks_for_needs_human(self):
        from scripts.summarize_auto_dev_runs import TaskRun, generate_report
        from pathlib import Path

        runs = [
            TaskRun(
                run_id="FAIL-001-20260528-200000",
                path=Path("/tmp"),
                task_id="FAIL-001",
                task_title="Failed task",
                priority="P0",
                status="NEEDS_HUMAN",
                reason="Test timeout",
                rounds=2,
            ),
        ]
        report = generate_report(runs, [], [], "2026-05-28")
        assert "需要人工介入" in report

    def test_report_no_api_keys(self):
        from scripts.summarize_auto_dev_runs import TaskRun, generate_report
        from pathlib import Path

        runs = [
            TaskRun(
                run_id="LEAK-001-20260528-200000",
                path=Path("/tmp"),
                task_id="LEAK-001",
                task_title="Leaked key",
                priority="P0",
                status="PASS",
                reason="used sk-AbCdEfGhIjKlMnOpQrStUvWxYz012345678 key",
                rounds=1,
                has_summary=True,
                summary_content="key=sk-AbCdEfGhIjKlMnOpQrStUvWxYz012345678",
            ),
        ]
        report = generate_report(runs, [], [], "2026-05-28")
        assert "sk-AbCdEfGhIjKlMnOpQrStUvWxYz012345678" not in report
        assert "REDACTED" in report


class TestDryRun:
    def test_dry_run_outputs_report(self, tmp_path):
        base = tmp_path
        _create_fixture_task_run(
            base, "DRY-001-20260528-200000", "DRY-001 — Dry run test",
        )
        result = _run_summarize(base, date="2026-05-28", dry_run=True)
        assert result.returncode == 0
        assert "自动开发日报" in result.stdout
        assert not (base / "docs" / "auto_dev_reports").exists() or not list(
            (base / "docs" / "auto_dev_reports").iterdir()
        )

    def test_write_creates_file(self, tmp_path):
        base = tmp_path
        run_dir = _create_fixture_task_run(
            base, "WRITE-001-20260528-200000", "WRITE-001 — Write test",
        )
        _create_fixture_summary(run_dir, final_status="PASS")
        result = _run_summarize(base, date="2026-05-28", dry_run=False)
        assert result.returncode == 0
        report_path = base / "docs" / "auto_dev_reports" / "2026-05-28.md"
        assert report_path.exists()
        content = report_path.read_text(encoding="utf-8")
        assert "WRITE-001" in content

    def test_no_fixture_still_runs(self, tmp_path):
        (tmp_path / "docs" / "task_runs").mkdir(parents=True)
        result = _run_summarize(tmp_path, date="2026-05-28", dry_run=True)
        assert result.returncode == 0
        assert "运行档案数: 0" in result.stdout


class TestReviews:
    def test_collect_reviews(self, tmp_path):
        from scripts.summarize_auto_dev_runs import collect_reviews
        rev_dir = tmp_path / "reviews"
        rev_dir.mkdir()
        (rev_dir / "TEST-20260528-round1.txt").write_text(
            "Task: TEST-001\nDate: 2026-05-28\n---\nNo issues found.",
            encoding="utf-8",
        )
        reviews = collect_reviews(rev_dir, target_date="2026-05-28")
        assert len(reviews) == 1
        assert reviews[0]["file"] == "TEST-20260528-round1.txt"

    def test_collect_reviews_filters_by_date(self, tmp_path):
        from scripts.summarize_auto_dev_runs import collect_reviews
        rev_dir = tmp_path / "reviews"
        rev_dir.mkdir()
        (rev_dir / "OLD-20260527-round1.txt").write_text("old", encoding="utf-8")
        (rev_dir / "NEW-20260528-round1.txt").write_text("new", encoding="utf-8")
        reviews = collect_reviews(rev_dir, target_date="2026-05-28")
        assert len(reviews) == 1
        assert "NEW" in reviews[0]["file"]

    def test_reviews_redacted_in_report(self, tmp_path):
        from scripts.summarize_auto_dev_runs import collect_reviews, generate_report
        rev_dir = tmp_path / "reviews"
        rev_dir.mkdir()
        (rev_dir / "LEAK-20260528-round1.txt").write_text(
            "Authorization: Bearer sk-secret-key-1234567890abcdef",
            encoding="utf-8",
        )
        reviews = collect_reviews(rev_dir, target_date="2026-05-28")
        report = generate_report([], [], reviews, "2026-05-28")
        assert "sk-secret-key" not in report
        assert "REDACTED" in report


class TestCollectTraceFiles:
    def test_collects_non_meta_files(self, tmp_path):
        from scripts.summarize_auto_dev_runs import collect_trace_files
        d = tmp_path / "run"
        d.mkdir()
        (d / "task.md").write_text("t", encoding="utf-8")
        (d / "summary.md").write_text("s", encoding="utf-8")
        (d / "opencode-round1.txt").write_text("log", encoding="utf-8")
        (d / "tests-round1.txt").write_text("test", encoding="utf-8")

        traces = collect_trace_files(d)
        assert "opencode-round1.txt" in traces
        assert "tests-round1.txt" in traces
        assert "task.md" not in traces
        assert "summary.md" not in traces
