import sqlite3
from pathlib import Path

import pytest

from scripts.cleanup_test_db_pollution import cleanup_test_pollution


def _create_polluted_db(db_path: Path) -> None:
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
            INSERT INTO users VALUES ('test-user', 'portfolio-import@test.com');
            INSERT INTO users VALUES ('real-user', 'meng@example.com');
            INSERT INTO scheduled_analyses VALUES ('s-test', 'test-user');
            INSERT INTO scheduled_analyses VALUES ('s-real', 'real-user');
            INSERT INTO reports VALUES ('r-test', 'test-user');
            INSERT INTO reports VALUES ('r-real', 'real-user');
            INSERT INTO user_llm_configs VALUES ('test-user');
            INSERT INTO user_llm_configs VALUES ('real-user');
            INSERT INTO user_tokens VALUES ('tok-test', 'test-user');
            INSERT INTO user_tokens VALUES ('tok-real', 'real-user');
            INSERT INTO watchlist_items VALUES ('w-test', 'test-user');
            INSERT INTO watchlist_items VALUES ('w-real', 'real-user');
            INSERT INTO feedbacks VALUES ('f-test', 'test-user');
            INSERT INTO feedbacks VALUES ('f-real', 'real-user');
            INSERT INTO imported_portfolio_positions VALUES ('p-test', 'test-user');
            INSERT INTO imported_portfolio_positions VALUES ('p-real', 'real-user');
            INSERT INTO user_llm_provider_keys VALUES ('k-test', 'test-user');
            INSERT INTO user_llm_provider_keys VALUES ('k-real', 'real-user');
            INSERT INTO email_verification_codes VALUES ('e-test', 'portfolio-import@test.com');
            INSERT INTO email_verification_codes VALUES ('e-real', 'meng@example.com');
            """
        )


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def test_dry_run_reports_pollution_without_deleting(tmp_path):
    db_path = tmp_path / "tradingagents.db"
    _create_polluted_db(db_path)

    report = cleanup_test_pollution(db_path, execute=False, backup_dir=tmp_path / "backups")

    assert report.executed is False
    assert report.backup_path is None
    assert report.before["users"] == 1
    assert report.before["scheduled_analyses"] == 1
    assert report.after == report.before
    with sqlite3.connect(db_path) as conn:
        assert _count(conn, "users") == 2
        assert _count(conn, "scheduled_analyses") == 2


def test_execute_deletes_only_test_domain_rows_and_keeps_backup(tmp_path):
    db_path = tmp_path / "tradingagents.db"
    backup_dir = tmp_path / "backups"
    _create_polluted_db(db_path)

    report = cleanup_test_pollution(db_path, execute=True, backup_dir=backup_dir)

    assert report.executed is True
    assert report.backup_path is not None
    assert Path(report.backup_path).exists()
    assert report.after["users"] == 0
    assert report.after["scheduled_analyses"] == 0
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT email FROM users").fetchall() == [("meng@example.com",)]
        assert conn.execute("SELECT id, user_id FROM scheduled_analyses").fetchall() == [
            ("s-real", "real-user")
        ]
        assert conn.execute("SELECT id, email FROM email_verification_codes").fetchall() == [
            ("e-real", "meng@example.com")
        ]
        for table in (
            "reports",
            "user_llm_configs",
            "user_tokens",
            "watchlist_items",
            "feedbacks",
            "imported_portfolio_positions",
            "user_llm_provider_keys",
        ):
            assert _count(conn, table) == 1

    with sqlite3.connect(report.backup_path) as backup:
        assert _count(backup, "users") == 2
        assert _count(backup, "scheduled_analyses") == 2


def test_rejects_non_test_pattern_by_default(tmp_path):
    db_path = tmp_path / "tradingagents.db"
    _create_polluted_db(db_path)

    with pytest.raises(ValueError, match="non-test email pattern"):
        cleanup_test_pollution(db_path, email_pattern="%@example.com", execute=True)


def test_allows_partial_schema_for_older_databases(tmp_path):
    db_path = tmp_path / "tradingagents.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT NOT NULL);
            CREATE TABLE scheduled_analyses (id TEXT PRIMARY KEY, user_id TEXT NOT NULL);
            INSERT INTO users VALUES ('test-user', 'apitest@test.com');
            INSERT INTO scheduled_analyses VALUES ('s-test', 'test-user');
            """
        )

    report = cleanup_test_pollution(db_path, execute=True, backup_dir=tmp_path / "backups")

    assert report.before["users"] == 1
    assert report.before["scheduled_analyses"] == 1
    assert report.after["users"] == 0
    assert report.after["scheduled_analyses"] == 0
