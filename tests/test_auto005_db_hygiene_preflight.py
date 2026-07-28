# [AUTO-005] db_hygiene_preflight
"""Tests for AUTO-005: preflight DB hygiene wiring + 续航门禁.

Acceptance covered (from docs/TASKS.md):
  - 污染 fixture 下 preflight 返回 warning/needs-human，不执行 OpenCode.
  - 干净库下自动开发 dry-run 可继续选任务.
  - ``AUTO_DEV_MAX_TASKS`` 与失败即停逻辑不回归.
  - preflight 默认只读；不自动执行 DB 清理；只输出建议命令.
  - 增加静态测试，防止 preflight 写生产 DB.

The tests are split into two layers:
  1. Static source guards on ``scripts/preflight_check.sh`` and
     ``scripts/auto_dev_loop.sh`` — verify AUTO-005 is wired in, the cleanup
        ``--execute`` flag is never invoked, and no SQL write statements are
        present in the preflight script itself.
  2. Functional tests on the report formatter — ``format_db_hygiene_section``
     renders all-green / polluted / unavailable snapshots correctly, and the
     CLI dry-run includes the section.
  3. Acceptance tests using fixture DBs — polluted fixture -> preflight
     surfaces warning; clean fixture -> dry-run continues.
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT_SCRIPT = REPO_ROOT / "scripts" / "preflight_check.sh"
AUTO_DEV_SCRIPT = REPO_ROOT / "scripts" / "auto_dev_loop.sh"
PRIMARY_WORKTREE_GUARD = REPO_ROOT / "scripts" / "primary_worktree_guard.sh"
SUMMARIZE_SCRIPT = REPO_ROOT / "scripts" / "summarize_auto_dev_runs.py"
HYGIENE_CLI = REPO_ROOT / "scripts" / "run_db_hygiene_check.py"

sys.path.insert(0, str(REPO_ROOT))

from scripts.summarize_auto_dev_runs import (  # noqa: E402
    collect_db_hygiene_snapshot,
    format_db_hygiene_section,
    generate_report,
)


# ── helpers ────────────────────────────────────────────────────────────────


def _create_polluted_db(db_path: Path) -> None:
    """Build a minimal schema + a single @test.com user with related rows."""
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT NOT NULL);
            CREATE TABLE scheduled_analyses (id TEXT PRIMARY KEY, user_id TEXT NOT NULL);
            CREATE TABLE reports (id TEXT PRIMARY KEY, user_id TEXT);
            CREATE TABLE user_llm_configs (user_id TEXT PRIMARY KEY);
            CREATE TABLE user_tokens (id TEXT PRIMARY KEY, user_id TEXT NOT NULL);
            CREATE TABLE watchlist_items (id TEXT PRIMARY KEY, user_id TEXT NOT NULL);
            CREATE TABLE feedbacks (id TEXT PRIMARY KEY, user_id TEXT NOT NULL);
            CREATE TABLE imported_portfolio_positions (id TEXT PRIMARY KEY, user_id TEXT NOT NULL);
            CREATE TABLE user_llm_provider_keys (id TEXT PRIMARY KEY, user_id TEXT NOT NULL);
            CREATE TABLE email_verification_codes (id TEXT PRIMARY KEY, email TEXT NOT NULL);
            INSERT INTO users VALUES ('test-user', 'apitest@test.com');
            INSERT INTO users VALUES ('real-user', 'meng@example.com');
            INSERT INTO scheduled_analyses VALUES ('s-test', 'test-user');
            """
        )


def _create_clean_db(db_path: Path) -> None:
    """Build the schema with only a real (non-test) user."""
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT NOT NULL);
            CREATE TABLE scheduled_analyses (id TEXT PRIMARY KEY, user_id TEXT NOT NULL);
            CREATE TABLE reports (id TEXT PRIMARY KEY, user_id TEXT);
            CREATE TABLE user_llm_configs (user_id TEXT PRIMARY KEY);
            CREATE TABLE user_tokens (id TEXT PRIMARY KEY, user_id TEXT NOT NULL);
            CREATE TABLE watchlist_items (id TEXT PRIMARY KEY, user_id TEXT NOT NULL);
            CREATE TABLE feedbacks (id TEXT PRIMARY KEY, user_id TEXT NOT NULL);
            CREATE TABLE imported_portfolio_positions (id TEXT PRIMARY KEY, user_id TEXT NOT NULL);
            CREATE TABLE user_llm_provider_keys (id TEXT PRIMARY KEY, user_id TEXT NOT NULL);
            CREATE TABLE email_verification_codes (id TEXT PRIMARY KEY, email TEXT NOT NULL);
            INSERT INTO users VALUES ('real-user', 'meng@example.com');
            INSERT INTO scheduled_analyses VALUES ('s-real', 'real-user');
            """
        )


# ── Static guards on preflight_check.sh ────────────────────────────────────


class TestPreflightStaticGuards:
    """Lock in the AUTO-005 wiring without spawning bash.

    These guards are deliberately source-text based so they catch a future
    regression even when Python / venv is unavailable in CI.
    """

    def test_preflight_has_auto005_marker(self):
        text = PREFLIGHT_SCRIPT.read_text(encoding="utf-8")
        assert "[AUTO-005] db_hygiene_preflight" in text, (
            "preflight_check.sh must carry the AUTO-005 marker"
        )

    def test_preflight_invokes_hygiene_cli(self):
        text = PREFLIGHT_SCRIPT.read_text(encoding="utf-8")
        assert "run_db_hygiene_check.py" in text, (
            "preflight must shell out to scripts/run_db_hygiene_check.py"
        )
        assert "--json" in text, "preflight must request JSON output for parsing"

    def test_preflight_has_skip_db_hygiene_flag(self):
        text = PREFLIGHT_SCRIPT.read_text(encoding="utf-8")
        assert "--skip-db-hygiene" in text, (
            "preflight must expose --skip-db-hygiene for environments without Python"
        )

    def test_preflight_never_invokes_cleanup_execute(self):
        """The cleanup script's --execute flag mutates the DB. Preflight must
        only print the suggested command as text, never run it."""
        text = PREFLIGHT_SCRIPT.read_text(encoding="utf-8")
        # The string "cleanup_test_db_pollution.py --execute" is allowed inside
        # log lines (as a suggested command). What is forbidden is actually
        # running it. Look for any active invocation pattern.
        forbidden_patterns = [
            # `python ... cleanup_test_db_pollution.py --execute` as a real
            # command (not inside a quoted log message). We approximate by
            # checking it is never on a line that does NOT start with log/warn.
            r"^\s*python[^\n]*cleanup_test_db_pollution[^\n]*--execute",
            r"^\s*bash[^\n]*cleanup_test_db_pollution[^\n]*--execute",
            r"subprocess\.run[^\n]*cleanup_test_db_pollution[^\n]*--execute",
        ]
        for pat in forbidden_patterns:
            assert not re.search(pat, text, re.MULTILINE), (
                f"preflight must not directly execute cleanup script: matched {pat}"
            )

    def test_preflight_has_no_sql_write_statements(self):
        """Static guard: preflight itself must never write to the DB. The
        hygiene CLI is the only thing allowed to touch the DB, and it is
        read-only by design."""
        text = PREFLIGHT_SCRIPT.read_text(encoding="utf-8")
        write_patterns = [
            r"\bINSERT\s+INTO",
            r"\bUPDATE\s+\w+\s+SET",
            r"\bDELETE\s+FROM",
            r"\bDROP\s+TABLE",
            r"\bCREATE\s+TABLE",
            r"\bALTER\s+TABLE",
            r"\bREPLACE\s+INTO",
            r"\bPRAGMA\s+\w+\s*=\s*\w",  # writable PRAGMA like journal_mode=X
        ]
        for pat in write_patterns:
            matches = re.findall(pat, text, re.IGNORECASE)
            assert not matches, (
                f"preflight must not contain SQL write statements: found {matches}"
            )

    def test_preflight_bash_syntax(self):
        result = subprocess.run(
            ["bash", "-n", str(PREFLIGHT_SCRIPT)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr

    def test_preflight_outputs_suggested_cleanup_command_only(self):
        """Spec: 不自动执行 DB 清理；只输出建议命令."""
        text = PREFLIGHT_SCRIPT.read_text(encoding="utf-8")
        # The suggested command may appear in log lines (good) but never as a
        # direct invocation. The string should appear at least once so operators
        # know what to run.
        assert "cleanup_test_db_pollution.py" in text, (
            "preflight should surface the suggested cleanup command for operators"
        )


# ── Static guards on auto_dev_loop.sh ──────────────────────────────────────


class TestAutoDevLoopRegressionGuards:
    """AUTO-005 acceptance: AUTO_DEV_MAX_TASKS and fail-stop logic must not regress."""

    def test_auto_dev_loop_still_calls_preflight(self):
        text = AUTO_DEV_SCRIPT.read_text(encoding="utf-8")
        assert "preflight_check.sh" in text, (
            "auto_dev_loop.sh must still invoke preflight_check.sh"
        )

    def test_auto_dev_max_tasks_unchanged(self):
        text = AUTO_DEV_SCRIPT.read_text(encoding="utf-8")
        assert 'AUTO_DEV_MAX_TASKS="${AUTO_DEV_MAX_TASKS:-0}"' in text
        assert "AUTO_DEV_MAX_TASKS" in text and "0 = no explicit cap" in text

    def test_fail_stop_break_guard_present(self):
        text = AUTO_DEV_SCRIPT.read_text(encoding="utf-8")
        assert re.search(r"FAILED_TASKS[^\n]*-gt\s*0[^\n]*\n\s*break", text, re.MULTILINE), (
            "fail-stop guard (FAILED_TASKS>0 break) must remain"
        )

    def test_quota_exhausted_branch_present(self):
        text = AUTO_DEV_SCRIPT.read_text(encoding="utf-8")
        assert "QUOTA_EXHAUSTED" in text

    def test_dirty_tree_guard_present(self):
        text = AUTO_DEV_SCRIPT.read_text(encoding="utf-8")
        assert "Working tree dirty" in text


# ── format_db_hygiene_section unit tests ──────────────────────────────────


class TestFormatDbHygieneSection:
    def test_all_green_snapshot(self):
        snapshot = {
            "db_path": "/tmp/x.db",
            "email_pattern": "%@test.com",
            "counts": {"users": 0, "reports": 0},
            "total_pollution": 0,
            "all_green": True,
            "has_p0_risk": False,
            "pending_tasks_filter_ok": True,
            "pending_tasks_filter_detail": "ok",
            "risks": [],
        }
        out = format_db_hygiene_section(snapshot)
        assert "## DB hygiene 与续航门禁（AUTO-005）" in out
        assert "all green" in out
        assert "P0" not in out
        assert "P1" not in out

    def test_polluted_snapshot_emits_p1_warning_and_suggested_command(self):
        snapshot = {
            "db_path": "/tmp/x.db",
            "email_pattern": "%@test.com",
            "counts": {"users": 1, "scheduled_analyses": 2},
            "total_pollution": 3,
            "all_green": False,
            "has_p0_risk": False,
            "pending_tasks_filter_ok": True,
            "pending_tasks_filter_detail": "ok",
            "risks": [
                {"severity": "P1", "code": "test_pollution_present",
                 "message": "Found 3 test-account row(s)"},
            ],
        }
        out = format_db_hygiene_section(snapshot)
        assert "**P1**" in out
        assert "3 行" in out
        # Must surface suggested command, never execute it
        assert "cleanup_test_db_pollution.py" in out
        # Must NOT include the destructive --execute flag
        assert "--execute" not in out, (
            "report section must not echo --execute; operators must read dry-run docs first"
        )

    def test_p0_snapshot_emits_p0_warning(self):
        snapshot = {
            "db_path": "/tmp/x.db",
            "email_pattern": "%@test.com",
            "counts": {},
            "total_pollution": 0,
            "all_green": False,
            "has_p0_risk": True,
            "pending_tasks_filter_ok": False,
            "pending_tasks_filter_detail": "filter broken",
            "risks": [
                {"severity": "P0", "code": "pending_tasks_includes_test_users",
                 "message": "filter broken"},
            ],
        }
        out = format_db_hygiene_section(snapshot)
        assert "**P0**" in out
        assert "失效 (P0)" in out

    def test_unavailable_snapshot(self):
        out = format_db_hygiene_section(None)
        assert "## DB hygiene 与续航门禁（AUTO-005）" in out
        assert "hygiene 服务不可用" in out

    def test_section_redacts_secrets(self):
        """Spec: never leak keys in the report."""
        snapshot = {
            "db_path": "/tmp/x.db",
            "email_pattern": "%@test.com",
            "counts": {},
            "total_pollution": 0,
            "all_green": False,
            "has_p0_risk": True,
            "pending_tasks_filter_ok": False,
            "pending_tasks_filter_detail": "ok",
            "risks": [
                {"severity": "P0", "code": "x",
                 "message": "api_key=sk-secret123abc456def789ghi012 was logged"},
            ],
        }
        out = format_db_hygiene_section(snapshot)
        assert "sk-secret123" not in out
        assert "[REDACTED]" in out


# ── generate_report integration ────────────────────────────────────────────


class TestGenerateReportAuto005:
    def test_report_includes_db_hygiene_section(self):
        section = "## DB hygiene 与续航门禁（AUTO-005）\n\n| all_green | True |\n"
        report = generate_report(
            [], [], [], "2026-07-07",
            ready_queue=[],
            db_hygiene_section=section,
        )
        assert "## DB hygiene 与续航门禁（AUTO-005）" in report

    def test_report_without_db_hygiene_section(self):
        report = generate_report(
            [], [], [], "2026-07-07",
            ready_queue=[],
            db_hygiene_section=None,
        )
        assert "DB hygiene 与续航门禁" not in report


# ── collect_db_hygiene_snapshot against fixture DBs ────────────────────────


def _point_service_at_db(monkeypatch: pytest.MonkeyPatch, db_path: Path) -> None:
    """Point the hygiene service at a fixture DB.

    ``api.database.DATABASE_URL`` is read at module import time and cached, so
    ``monkeypatch.setenv`` alone does not affect already-imported code. We also
    patch the cached attribute on the module so the resolver picks up the
    fixture path. The fixture file itself is never mutated.
    """
    import api.database as api_db  # noqa: WPS433
    monkeypatch.setattr(api_db, "DATABASE_URL", f"sqlite:///{db_path}")


class TestCollectDbHygieneSnapshotFixtures:
    """End-to-end against fixture DBs to confirm the section reflects reality."""

    def test_clean_fixture_returns_all_green_dict(self, tmp_path: Path, monkeypatch):
        db = tmp_path / "clean.db"
        _create_clean_db(db)
        _point_service_at_db(monkeypatch, db)
        snapshot = collect_db_hygiene_snapshot(skip_pending_tasks_check=True)
        assert snapshot is not None
        assert snapshot["total_pollution"] == 0
        assert snapshot["all_green"] is True
        assert snapshot["has_p0_risk"] is False

    def test_polluted_fixture_returns_nonzero_counts(self, tmp_path: Path, monkeypatch):
        db = tmp_path / "polluted.db"
        _create_polluted_db(db)
        _point_service_at_db(monkeypatch, db)
        snapshot = collect_db_hygiene_snapshot(skip_pending_tasks_check=True)
        assert snapshot is not None
        assert snapshot["total_pollution"] > 0
        assert snapshot["all_green"] is False
        assert snapshot["counts"]["users"] == 1
        assert snapshot["counts"]["scheduled_analyses"] == 1

    def test_clean_fixture_does_not_mutate_db(self, tmp_path: Path, monkeypatch):
        """Spec: preflight 默认只读. The snapshot must not write to the DB."""
        db = tmp_path / "clean.db"
        _create_clean_db(db)
        _point_service_at_db(monkeypatch, db)
        before = db.stat().st_size
        collect_db_hygiene_snapshot(skip_pending_tasks_check=True)
        after = db.stat().st_size
        assert before == after, "collect_db_hygiene_snapshot must not mutate the DB"

    def test_polluted_fixture_does_not_mutate_db(self, tmp_path: Path, monkeypatch):
        db = tmp_path / "polluted.db"
        _create_polluted_db(db)
        _point_service_at_db(monkeypatch, db)
        before = db.stat().st_size
        collect_db_hygiene_snapshot(skip_pending_tasks_check=True)
        after = db.stat().st_size
        assert before == after, "snapshot must remain read-only against polluted DB"


# ── Acceptance: preflight_check.sh against fixture DBs ─────────────────────
#
# These tests spawn preflight_check.sh as a subprocess so we exercise the
# actual bash wiring. We use DATABASE_URL to point the hygiene CLI at a
# fixture DB and the project venv for python.


@pytest.fixture
def bash_env(tmp_path: Path, monkeypatch):
    """Return a copy of os.environ with DATABASE_URL pointed at a fixture."""
    env = dict(os.environ)
    env["DATABASE_URL"] = f"sqlite:///{tmp_path / 'fixture.db'}"
    return env


class TestPreflightAcceptance:
    """AUTO-005 acceptance: preflight behaviour against fixture DBs."""

    @staticmethod
    def _make_primary_preflight_repo(tmp_path: Path) -> Path:
        """Build a disposable primary worktree for preflight subprocess tests.

        The production guard intentionally blocks linked worktrees, including
        Codex review worktrees.  Acceptance tests therefore run the real
        scripts from a fresh single-worktree repository instead of weakening
        the guard with a test bypass.
        """
        repo = tmp_path / "preflight-repo"
        scripts_dir = repo / "scripts"
        docs_dir = repo / "docs"
        scripts_dir.mkdir(parents=True)
        docs_dir.mkdir()
        for source in (
            PREFLIGHT_SCRIPT,
            PRIMARY_WORKTREE_GUARD,
            REPO_ROOT / "scripts" / "run_db_hygiene_check.py",
        ):
            target = scripts_dir / source.name
            shutil.copy2(source, target)
            target.chmod(target.stat().st_mode | 0o111)
        project_venv = REPO_ROOT / ".venv"
        if project_venv.is_dir():
            (repo / ".venv").symlink_to(project_venv, target_is_directory=True)
        (docs_dir / "TASKS.md").write_text("# Tasks\n", encoding="utf-8")
        (docs_dir / "DEVLOG.md").write_text("# Devlog\n", encoding="utf-8")
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=T",
                "-c",
                "user.email=t@t.com",
                "commit",
                "-m",
                "init",
            ],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        return repo

    @pytest.mark.parametrize("create_fn", ["polluted", "clean"])
    def test_preflight_db_hygiene_section_status(self, tmp_path: Path, create_fn: str):
        db = tmp_path / "fixture.db"
        if create_fn == "polluted":
            _create_polluted_db(db)
        else:
            _create_clean_db(db)

        env = dict(os.environ)
        env["DATABASE_URL"] = f"sqlite:///{db}"
        env["PYTHONPATH"] = str(REPO_ROOT)
        repo = self._make_primary_preflight_repo(tmp_path)

        # Run preflight with --skip-tests so only static + hygiene checks fire.
        # The fresh python subprocess reads DATABASE_URL at module load time,
        # so the fixture DB is honoured.
        result = subprocess.run(
            ["bash", "scripts/preflight_check.sh", "--skip-tests"],
            env=env,
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=60,
        )

        # Combine stdout + stderr for assertions (preflight uses both).
        combined = result.stdout + result.stderr

        if create_fn == "polluted":
            # The hygiene check must surface the pollution; section must NOT
            # report all_green. The overall preflight exit may be 0/1/2
            # depending on other warnings (dirty tree, scheduler running);
            # what matters is the hygiene section flagged the pollution.
            assert "DB hygiene [P1]" in combined, (
                "polluted fixture must surface a P1 hygiene warning"
            )
            # Must not silently claim all_green
            assert "DB hygiene all green" not in combined, (
                "polluted fixture must not be reported as all green"
            )
        else:
            # Clean fixture: hygiene section reports all_green and never flags
            # P0/P1. We assert the specific hygiene markers, not generic
            # phrases like "检测到 " (which collides with scheduler warnings).
            assert "DB hygiene all green" in combined, (
                "clean fixture should report DB hygiene all green"
            )
            assert "DB hygiene [P1]" not in combined
            assert "DB hygiene [P0]" not in combined

    def test_skip_db_hygiene_flag_bypasses_check(self, tmp_path: Path):
        """--skip-db-hygiene must skip the check entirely (no JSON output)."""
        db = tmp_path / "fixture.db"
        _create_polluted_db(db)
        env = dict(os.environ)
        env["DATABASE_URL"] = f"sqlite:///{db}"
        env["PYTHONPATH"] = str(REPO_ROOT)
        repo = self._make_primary_preflight_repo(tmp_path)

        # Run WITHOUT --quiet so the skip log line is visible.
        result = subprocess.run(
            [
                "bash",
                "scripts/preflight_check.sh",
                "--skip-tests",
                "--skip-db-hygiene",
            ],
            env=env,
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=60,
        )
        combined = result.stdout + result.stderr
        # The skip log line must appear
        assert "DB hygiene 跳过 (--skip-db-hygiene)" in combined
        # The JSON-derived status lines must NOT appear (no check ran)
        assert "total_pollution:" not in combined
        assert "DB hygiene [P1]" not in combined
        assert "DB hygiene all green" not in combined


# ── Acceptance: summarize_auto_dev_runs.py CLI ─────────────────────────────


class TestSummarizeCliAuto005:
    """AUTO-005 acceptance: daily report surfaces ready count + endurance + hygiene."""

    @staticmethod
    def _make_minimal_repo(tmp_path: Path) -> Path:
        """Build a minimal repo with TASKS.md + auto_dev_loop.sh + reports dir."""
        (tmp_path / "docs" / "task_runs").mkdir(parents=True)
        (tmp_path / "docs" / "reviews").mkdir(parents=True)
        (tmp_path / "scripts").mkdir(parents=True)
        (tmp_path / "docs" / "TASKS.md").write_text(
            "### T-001: sample task（P1）\n- **状态**: ready\n- **优先级**: P1\n",
            encoding="utf-8",
        )
        (tmp_path / "docs" / "DEVLOG.md").write_text("# Devlog\n", encoding="utf-8")
        (tmp_path / "scripts" / "auto_dev_loop.sh").write_text(
            textwrap.dedent("""\
                #!/usr/bin/env bash
                if [[ -n "$DIRTY" ]]; then
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
                """),
            encoding="utf-8",
        )
        return tmp_path

    def test_dry_run_includes_db_hygiene_section(self, tmp_path: Path):
        repo = self._make_minimal_repo(tmp_path)
        result = subprocess.run(
            [sys.executable, str(SUMMARIZE_SCRIPT), "--dry-run",
             "--date", "2026-07-07",
             "--no-runtime-budget", "--no-endurance",
             "--repo-dir", str(repo)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        assert "## DB hygiene 与续航门禁（AUTO-005）" in result.stdout

    def test_dry_run_no_db_hygiene_flag_skips_section(self, tmp_path: Path):
        repo = self._make_minimal_repo(tmp_path)
        result = subprocess.run(
            [sys.executable, str(SUMMARIZE_SCRIPT), "--dry-run",
             "--date", "2026-07-07",
             "--no-runtime-budget", "--no-endurance", "--no-db-hygiene",
             "--repo-dir", str(repo)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        assert "DB hygiene 与续航门禁" not in result.stdout

    def test_dry_run_does_not_modify_tasks_md(self, tmp_path: Path):
        repo = self._make_minimal_repo(tmp_path)
        tasks_md = repo / "docs" / "TASKS.md"
        before = tasks_md.read_text(encoding="utf-8")
        subprocess.run(
            [sys.executable, str(SUMMARIZE_SCRIPT), "--dry-run",
             "--date", "2026-07-07", "--repo-dir", str(repo)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )
        after = tasks_md.read_text(encoding="utf-8")
        assert before == after

    def test_dry_run_includes_ready_count_and_endurance(self, tmp_path: Path):
        """AUTO-005: report must record ready count + estimated endurance."""
        repo = self._make_minimal_repo(tmp_path)
        result = subprocess.run(
            [sys.executable, str(SUMMARIZE_SCRIPT), "--dry-run",
             "--date", "2026-07-07",
             "--no-endurance",  # use static endurance only
             "--repo-dir", str(repo)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        # Ready count appears in the report header
        assert "Ready 队列" in result.stdout
        # AUTO-004 endurance section appears by default
        assert "续航预算与失败即停回归" in result.stdout


# ── Acceptance: auto_dev_loop dry-run can still select tasks on clean DB ────


class TestAutoDevLoopDryRunAcceptance:
    """AUTO-005: 干净库下自动开发 dry-run 可继续选任务.

    This is a smoke regression: invoking auto_dev_loop.sh --dry-run against a
    clean repo must still surface the AUTO-002 DRY-RUN block (proving the
    fail-stop / endurance gates did not break task selection).
    """

    def test_dry_run_still_selects_task(self, tmp_path: Path):
        repo = tmp_path / "repo"
        (repo / "scripts").mkdir(parents=True)
        (repo / "docs").mkdir()
        shutil.copy2(AUTO_DEV_SCRIPT, repo / "scripts" / "auto_dev_loop.sh")
        # Also copy preflight so auto_dev_loop can invoke it
        shutil.copy2(PREFLIGHT_SCRIPT, repo / "scripts" / "preflight_check.sh")
        shutil.copy2(
            PRIMARY_WORKTREE_GUARD,
            repo / "scripts" / "primary_worktree_guard.sh",
        )

        tasks = """# Tasks

### AUTO-005-TEST: sample selectable task (P1)
- **优先级**：P1
- **状态**：ready
- **验证方式**：
  - `bash -n scripts/auto_dev_loop.sh` 通过.
"""
        (repo / "docs" / "TASKS.md").write_text(tasks, encoding="utf-8")
        (repo / "docs" / "DEVLOG.md").write_text("# Devlog\n", encoding="utf-8")

        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.name=T", "-c", "user.email=t@t.com",
             "commit", "-m", "init"],
            cwd=repo, check=True, capture_output=True,
        )

        result = subprocess.run(
            ["bash", "scripts/auto_dev_loop.sh", "--dry-run"],
            cwd=repo, text=True, capture_output=True, timeout=60, check=False,
        )

        assert result.returncode == 0, result.stderr
        assert "AUTO-002 DRY-RUN" in result.stdout
        assert "AUTO-005-TEST" in result.stdout
