"""Tests for the DATA-026 DB hygiene health check.

# [DATA-026] db_hygiene_check

Covers:
- polluted fixture DB -> non-zero counts, all_green False
- clean fixture DB -> all_green True
- get_pending_tasks no longer excludes @test.com -> P0 risk surfaced
- scheduler startup helper never touches the production DB (file size hash)
- CLI entry point returns non-zero when pollution is present
- source-level guard: hygiene service is read-only (no DB writes observed)
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Callable

import pytest

from api.services.db_hygiene_service import (
    DEFAULT_EMAIL_PATTERN,
    DbHygieneReport,
    HygieneRisk,
    _verify_pending_tasks_filter,
    log_startup_hygiene_warning,
    run_db_hygiene_check,
)
from scripts.cleanup_test_db_pollution import (
    USER_ID_TABLES,
    EMAIL_TABLES,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROD_DB = (PROJECT_ROOT / "tradingagents.db").resolve()


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
            INSERT INTO reports VALUES ('r-test', 'test-user');
            INSERT INTO user_tokens VALUES ('tok-test', 'test-user');
            INSERT INTO watchlist_items VALUES ('w-test', 'test-user');
            INSERT INTO feedbacks VALUES ('f-test', 'test-user');
            INSERT INTO imported_portfolio_positions VALUES ('p-test', 'test-user');
            INSERT INTO user_llm_provider_keys VALUES ('k-test', 'test-user');
            INSERT INTO email_verification_codes VALUES ('e-test', 'apitest@test.com');
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


def _file_signature(path: Path) -> int:
    """Cheap mutation guard: file size + mtime_ns."""
    stat = path.stat()
    return hash((stat.st_size, stat.st_mtime_ns))


# ── Acceptance: fixture pollution returns counts ────────────────────────────


def test_polluted_fixture_returns_nonzero_counts(tmp_path: Path):
    db_path = tmp_path / "tradingagents.db"
    _create_polluted_db(db_path)

    report = run_db_hygiene_check(
        db_path,
        skip_pending_tasks_check=True,  # fixture has no real SQLAlchemy schema
    )

    assert isinstance(report, DbHygieneReport)
    assert report.email_pattern == DEFAULT_EMAIL_PATTERN
    assert report.counts["users"] == 1
    assert report.counts["scheduled_analyses"] == 1
    assert report.counts["reports"] == 1
    assert report.counts["watchlist_items"] == 1
    assert report.counts["imported_portfolio_positions"] == 1
    assert report.counts["email_verification_codes"] == 1
    assert report.total_pollution > 0
    assert not report.all_green
    assert any(r.code == "test_pollution_present" and r.severity == "P1" for r in report.risks)


def test_clean_fixture_is_all_green(tmp_path: Path):
    db_path = tmp_path / "tradingagents.db"
    _create_clean_db(db_path)

    report = run_db_hygiene_check(db_path, skip_pending_tasks_check=True)

    assert report.total_pollution == 0
    assert all(count == 0 for count in report.counts.values())
    assert report.all_green is True
    assert report.has_p0_risk is False


def test_missing_db_is_info_not_p0(tmp_path: Path):
    missing = tmp_path / "does-not-exist.db"

    report = run_db_hygiene_check(missing, skip_pending_tasks_check=True)

    assert report.total_pollution == 0
    assert report.all_green is True  # fresh deploy = not a pollution event
    assert any(r.code == "db_not_found" and r.severity == "info" for r in report.risks)


# ── Acceptance: scheduler filter check (P0 when broken) ─────────────────────


def test_pending_tasks_filter_verification_passes_for_current_impl():
    ok, detail = _verify_pending_tasks_filter()
    assert ok is True, detail
    assert "@test.com" in detail or "excludes" in detail


def test_pending_tasks_filter_detection_flags_p0_when_filter_missing(
    monkeypatch: pytest.MonkeyPatch,
):
    """Simulate someone removing the test-user filter from get_pending_tasks."""

    from api.services import scheduled_service

    def _broken_get_pending_tasks(db, today, current_hhmm):
        # Returns ALL active tasks, ignoring test-user filter (the regression).
        from api.database import ScheduledAnalysisDB

        rows = (
            db.query(ScheduledAnalysisDB)
            .filter(ScheduledAnalysisDB.is_active == True)  # noqa: E712
            .all()
        )
        return [r for r in rows if (r.trigger_time or "20:00") <= current_hhmm]

    monkeypatch.setattr(scheduled_service, "get_pending_tasks", _broken_get_pending_tasks)

    ok, detail = _verify_pending_tasks_filter()
    assert ok is False
    assert "test-user" in detail or "test" in detail.lower()


def test_run_db_hygiene_check_surfaces_p0_when_filter_broken(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    from api.services import scheduled_service

    def _broken_get_pending_tasks(db, today, current_hhmm):
        from api.database import ScheduledAnalysisDB

        return (
            db.query(ScheduledAnalysisDB)
            .filter(ScheduledAnalysisDB.is_active == True)  # noqa: E712
            .all()
        )

    monkeypatch.setattr(scheduled_service, "get_pending_tasks", _broken_get_pending_tasks)

    db_path = tmp_path / "tradingagents.db"
    _create_clean_db(db_path)

    report = run_db_hygiene_check(db_path)

    assert report.total_pollution == 0
    # Even with 0 pollution, a broken filter must keep us out of all-green.
    assert report.all_green is False
    assert report.has_p0_risk is True
    assert report.pending_tasks_filter_ok is False
    assert any(r.severity == "P0" for r in report.risks)


# ── Acceptance: read-only guarantee (no DB writes) ──────────────────────────


def test_run_check_does_not_mutate_db(tmp_path: Path):
    db_path = tmp_path / "tradingagents.db"
    _create_polluted_db(db_path)

    before = _file_signature(db_path)
    run_db_hygiene_check(db_path, skip_pending_tasks_check=True)
    after = _file_signature(db_path)

    assert before == after, "hygiene check must not mutate the inspected DB"


def test_check_never_touches_production_db():
    """Static guard: the service must always resolve to a non-prod DB in tests.

    The pytest conftest isolates DATABASE_URL; ensure the resolver picks that
    up and not the project's tradingagents.db.
    """
    from api.database import DATABASE_URL

    report = run_db_hygiene_check(skip_pending_tasks_check=True)

    if DATABASE_URL.startswith("sqlite"):
        resolved_path = Path(report.db_path).resolve()
        assert resolved_path != PROD_DB, (
            "hygiene check resolved to the production DB under pytest - "
            "conftest isolation must keep it on a temp file"
        )


# ── Scheduler startup helper ────────────────────────────────────────────────


def test_log_startup_hygiene_warning_all_green_logs_info(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
):
    db_path = tmp_path / "tradingagents.db"
    _create_clean_db(db_path)
    report = run_db_hygiene_check(db_path, skip_pending_tasks_check=True)

    logger = logging.getLogger("test_scheduler_hygiene")
    with caplog.at_level(logging.INFO, logger="test_scheduler_hygiene"):
        log_startup_hygiene_warning(report, logger=logger)

    joined = "\n".join(rec.message for rec in caplog.records)
    assert "all green" in joined
    # No ERROR / WARNING when green
    assert not any(rec.levelno >= logging.WARNING for rec in caplog.records)


def test_log_startup_hygiene_warning_polluted_emits_warning_not_error(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
):
    """Constraint: pollution without filter regression must NOT block scheduler.

    Only a P0 (filter broken) is allowed to log at ERROR. Plain pollution is a
    WARNING so real-user scheduled tasks keep running.
    """
    db_path = tmp_path / "tradingagents.db"
    _create_polluted_db(db_path)
    report = run_db_hygiene_check(db_path, skip_pending_tasks_check=True)

    logger = logging.getLogger("test_scheduler_hygiene_warn")
    with caplog.at_level(logging.INFO, logger="test_scheduler_hygiene_warn"):
        log_startup_hygiene_warning(report, logger=logger)

    levels = {rec.levelno for rec in caplog.records}
    assert logging.WARNING in levels
    assert logging.ERROR not in levels
    joined = "\n".join(rec.message for rec in caplog.records)
    assert "test_pollution" in joined.lower() or report.total_pollution > 0


def test_log_startup_hygiene_warning_p0_emits_error(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
):
    from api.services import scheduled_service

    def _broken_get_pending_tasks(db, today, current_hhmm):
        from api.database import ScheduledAnalysisDB

        return db.query(ScheduledAnalysisDB).all()

    monkeypatch.setattr(scheduled_service, "get_pending_tasks", _broken_get_pending_tasks)

    db_path = tmp_path / "tradingagents.db"
    _create_clean_db(db_path)
    report = run_db_hygiene_check(db_path)

    logger = logging.getLogger("test_scheduler_hygiene_p0")
    with caplog.at_level(logging.INFO, logger="test_scheduler_hygiene_p0"):
        log_startup_hygiene_warning(report, logger=logger)

    joined = "\n".join(rec.message for rec in caplog.records)
    assert "P0" in joined
    assert any(rec.levelno == logging.ERROR for rec in caplog.records)


def test_scheduler_startup_wrapper_never_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    """The scheduler's _warn_db_hygiene_on_startup must never break startup.

    Even if the underlying service blows up (DB locked, import error, ...),
    the wrapper catches and logs a warning instead of crashing the loop.
    """
    import scheduler.main as scheduler_main

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated service failure")

    monkeypatch.setattr(
        "api.services.db_hygiene_service.run_db_hygiene_check", _boom
    )

    with caplog.at_level(logging.INFO):
        # Must not raise.
        scheduler_main._warn_db_hygiene_on_startup()

    joined = "\n".join(rec.message for rec in caplog.records)
    assert "non-blocking" in joined


# ── CLI entry point ─────────────────────────────────────────────────────────


def test_cli_returns_nonzero_for_polluted_db(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
):
    import importlib

    db_path = tmp_path / "tradingagents.db"
    _create_polluted_db(db_path)

    monkeypatch.setattr("sys.argv", [
        "run_db_hygiene_check.py",
        "--db",
        str(db_path),
        "--skip-pending-tasks-check",
    ])

    cli = importlib.import_module("scripts.run_db_hygiene_check")
    exit_code = cli.main()

    captured = capsys.readouterr().out
    assert "DB hygiene check" in captured
    assert exit_code == 1  # pollution present -> not all green


def test_cli_returns_zero_for_clean_db(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
):
    import importlib

    db_path = tmp_path / "tradingagents.db"
    _create_clean_db(db_path)

    monkeypatch.setattr("sys.argv", [
        "run_db_hygiene_check.py",
        "--db",
        str(db_path),
        "--skip-pending-tasks-check",
    ])

    cli = importlib.import_module("scripts.run_db_hygiene_check")
    exit_code = cli.main()

    captured = capsys.readouterr().out
    assert "all_green: True" in captured
    assert exit_code == 0


def test_cli_json_payload_shape(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
):
    import importlib
    import json

    db_path = tmp_path / "tradingagents.db"
    _create_polluted_db(db_path)

    monkeypatch.setattr("sys.argv", [
        "run_db_hygiene_check.py",
        "--db",
        str(db_path),
        "--skip-pending-tasks-check",
        "--json",
    ])

    cli = importlib.import_module("scripts.run_db_hygiene_check")
    cli.main()

    payload = json.loads(capsys.readouterr().out)
    for key in (
        "db_path",
        "email_pattern",
        "counts",
        "total_pollution",
        "all_green",
        "has_p0_risk",
        "pending_tasks_filter_ok",
        "pending_tasks_filter_detail",
        "risks",
    ):
        assert key in payload
    assert payload["total_pollution"] > 0
    assert payload["all_green"] is False


# ── to_dict / dataclass shape ───────────────────────────────────────────────


def test_to_dict_serializes_risks(tmp_path: Path):
    db_path = tmp_path / "tradingagents.db"
    _create_polluted_db(db_path)
    report = run_db_hygiene_check(db_path, skip_pending_tasks_check=True)

    payload = report.to_dict()
    assert payload["risks"], "expected at least one risk for polluted fixture"
    first = payload["risks"][0]
    assert {"severity", "code", "message"} <= set(first.keys())


# ── Backwards-compat: cleanup script tables stay in sync ────────────────────


def test_hygiene_service_covers_same_table_set_as_cleanup_script():
    """Lock the schema coverage: any new table added to the cleanup script
    must show up in hygiene counts (otherwise the health check would silently
    miss a pollution source)."""
    db_path = Path("/tmp/does-not-matter")
    # Use the same collect_counts source to avoid duplicating the table list.
    from scripts.cleanup_test_db_pollution import collect_counts

    expected_tables = {"users", *USER_ID_TABLES, *EMAIL_TABLES}
    # Build an empty in-memory DB with all expected tables so we can probe
    # which keys collect_counts actually returns.
    with sqlite3.connect(":memory:") as conn:
        conn.executescript(
            "CREATE TABLE users (id TEXT, email TEXT);\n"
            + "\n".join(
                f"CREATE TABLE {t} (id TEXT, user_id TEXT);" for t in USER_ID_TABLES
            )
            + "\n"
            + "\n".join(
                f"CREATE TABLE {t} (id TEXT, email TEXT);" for t in EMAIL_TABLES
            )
        )
        counts = collect_counts(conn, DEFAULT_EMAIL_PATTERN)

    missing = expected_tables - set(counts.keys())
    assert not missing, f"hygiene check lost coverage for: {sorted(missing)}"
