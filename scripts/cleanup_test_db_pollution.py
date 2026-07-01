#!/usr/bin/env python3
"""Clean test-account pollution from the local TradingAgents SQLite DB.

The script is intentionally conservative:
- dry-run by default;
- only permits @test.com patterns unless explicitly overridden;
- backs up the SQLite DB before destructive cleanup;
- deletes only rows owned by matching test users or matching verification emails.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = PROJECT_ROOT / "tradingagents.db"
DEFAULT_BACKUP_DIR = PROJECT_ROOT / "var" / "db_backups"
DEFAULT_PATTERN = "%@test.com"

USER_ID_TABLES = (
    "reports",
    "user_llm_configs",
    "user_tokens",
    "watchlist_items",
    "feedbacks",
    "imported_portfolio_positions",
    "scheduled_analyses",
    "user_llm_provider_keys",
)
EMAIL_TABLES = (
    "email_verification_codes",
)


@dataclass(frozen=True)
class CleanupReport:
    db_path: str
    email_pattern: str
    executed: bool
    backup_path: str | None
    before: dict[str, int]
    after: dict[str, int]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    if not _table_exists(conn, table):
        return False
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def _count(conn: sqlite3.Connection, sql: str, params: Iterable[str]) -> int:
    row = conn.execute(sql, tuple(params)).fetchone()
    return int(row[0] or 0)


def collect_counts(conn: sqlite3.Connection, email_pattern: str) -> dict[str, int]:
    """Collect pollution counts without mutating the DB."""
    counts: dict[str, int] = {}
    has_users = _table_exists(conn, "users") and _column_exists(conn, "users", "email")
    if has_users:
        counts["users"] = _count(
            conn,
            "SELECT COUNT(*) FROM users WHERE email LIKE ?",
            (email_pattern,),
        )
    else:
        counts["users"] = 0

    for table in USER_ID_TABLES:
        if has_users and _column_exists(conn, table, "user_id"):
            counts[table] = _count(
                conn,
                f"""
                SELECT COUNT(*) FROM {table}
                WHERE user_id IN (SELECT id FROM users WHERE email LIKE ?)
                """,
                (email_pattern,),
            )
        else:
            counts[table] = 0

    for table in EMAIL_TABLES:
        if _column_exists(conn, table, "email"):
            counts[table] = _count(
                conn,
                f"SELECT COUNT(*) FROM {table} WHERE email LIKE ?",
                (email_pattern,),
            )
        else:
            counts[table] = 0

    return counts


def validate_email_pattern(email_pattern: str, allow_non_test_pattern: bool = False) -> None:
    """Reject broad cleanup patterns unless the caller explicitly overrides."""
    normalized = email_pattern.lower()
    if allow_non_test_pattern:
        return
    if "@test.com" not in normalized:
        raise ValueError(
            "Refusing to clean non-test email pattern. "
            "Use --allow-non-test-pattern only for an explicit manual recovery."
        )


def backup_database(db_path: Path, backup_dir: Path) -> Path:
    """Create a SQLite backup using sqlite3's online backup API."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"{db_path.stem}.pre-test-cleanup-{timestamp}.db"
    with sqlite3.connect(str(db_path)) as source, sqlite3.connect(str(backup_path)) as target:
        source.backup(target)
    return backup_path


def cleanup_test_pollution(
    db_path: Path,
    email_pattern: str = DEFAULT_PATTERN,
    *,
    execute: bool = False,
    backup_dir: Path = DEFAULT_BACKUP_DIR,
    allow_non_test_pattern: bool = False,
) -> CleanupReport:
    validate_email_pattern(email_pattern, allow_non_test_pattern)
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    with sqlite3.connect(str(db_path)) as conn:
        before = collect_counts(conn, email_pattern)

    backup_path: Path | None = None
    if execute and any(before.values()):
        backup_path = backup_database(db_path, backup_dir)
        with sqlite3.connect(str(db_path)) as conn:
            has_users = _table_exists(conn, "users") and _column_exists(conn, "users", "email")
            with conn:
                if has_users:
                    for table in USER_ID_TABLES:
                        if _column_exists(conn, table, "user_id"):
                            conn.execute(
                                f"""
                                DELETE FROM {table}
                                WHERE user_id IN (
                                    SELECT id FROM users WHERE email LIKE ?
                                )
                                """,
                                (email_pattern,),
                            )
                for table in EMAIL_TABLES:
                    if _column_exists(conn, table, "email"):
                        conn.execute(
                            f"DELETE FROM {table} WHERE email LIKE ?",
                            (email_pattern,),
                        )
                if has_users:
                    conn.execute("DELETE FROM users WHERE email LIKE ?", (email_pattern,))
            conn.execute("PRAGMA optimize")

    with sqlite3.connect(str(db_path)) as conn:
        after = collect_counts(conn, email_pattern)

    return CleanupReport(
        db_path=str(db_path),
        email_pattern=email_pattern,
        executed=execute,
        backup_path=str(backup_path) if backup_path else None,
        before=before,
        after=after,
    )


def _render_report(report: CleanupReport) -> str:
    lines = [
        "DB test pollution cleanup",
        f"- db_path: {report.db_path}",
        f"- email_pattern: {report.email_pattern}",
        f"- mode: {'execute' if report.executed else 'dry-run'}",
        f"- backup_path: {report.backup_path or '-'}",
        "",
        "| table | before | after | delta |",
        "|---|---:|---:|---:|",
    ]
    for table in sorted(report.before):
        before = report.before[table]
        after = report.after.get(table, 0)
        lines.append(f"| {table} | {before} | {after} | {before - after} |")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite DB path")
    parser.add_argument(
        "--email-pattern",
        default=DEFAULT_PATTERN,
        help="SQL LIKE pattern for test users; defaults to %%@test.com",
    )
    parser.add_argument("--execute", action="store_true", help="Actually delete matching rows")
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP_DIR)
    parser.add_argument(
        "--allow-non-test-pattern",
        action="store_true",
        help="Allow cleanup patterns that do not contain @test.com",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of markdown")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = cleanup_test_pollution(
        args.db,
        args.email_pattern,
        execute=args.execute,
        backup_dir=args.backup_dir,
        allow_non_test_pattern=args.allow_non_test_pattern,
    )
    if args.json:
        print(json.dumps(report.__dict__, ensure_ascii=False, indent=2))
    else:
        print(_render_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
