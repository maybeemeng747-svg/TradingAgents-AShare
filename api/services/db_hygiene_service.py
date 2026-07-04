"""Read-only DB hygiene health check.

# [DATA-026] db_hygiene_check

This service detects test-account pollution (`@test.com` users, scheduled
tasks, reports, watchlist items, imported positions) accumulated in the
production SQLite DB. It is intentionally **read-only**:

- never mutates the production DB;
- never deletes rows;
- never triggers TA / LLM calls;
- never prints key material.

It also verifies that ``scheduled_service.get_pending_tasks`` still excludes
``@test.com`` users from the scheduler's pending queue. The verification uses
an isolated in-memory SQLite fixture so the production DB is untouched. When
the filter regresses, the report surfaces a P0 risk that callers (scheduler
startup, auto-dev preflight, API endpoint) can surface to a human.

Cleanup is delegated to ``scripts/cleanup_test_db_pollution.py`` (dry-run by
default, backup before destructive operations).
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_EMAIL_PATTERN = "%@test.com"

# Severity codes used in the report. P0 = scheduler may execute test users
# (must hard-stop or warn loudly). P1 = pollution present but filter still
# works (warn + suggest cleanup). info = informational (e.g. fresh DB).
SEVERITY_P0 = "P0"
SEVERITY_P1 = "P1"
SEVERITY_INFO = "info"


@dataclass(frozen=True)
class HygieneRisk:
    """A single risk surfaced by the hygiene check."""

    severity: str
    code: str
    message: str


@dataclass(frozen=True)
class DbHygieneReport:
    """Read-only snapshot of test-account pollution + scheduler filter state."""

    db_path: str
    email_pattern: str
    counts: dict[str, int]
    total_pollution: int
    pending_tasks_filter_ok: bool
    pending_tasks_filter_detail: str
    risks: list[HygieneRisk] = field(default_factory=list)

    @property
    def all_green(self) -> bool:
        """True only when there is no pollution AND the filter still works."""
        return (
            self.total_pollution == 0
            and self.pending_tasks_filter_ok
            and not any(r.severity == SEVERITY_P0 for r in self.risks)
        )

    @property
    def has_p0_risk(self) -> bool:
        return any(r.severity == SEVERITY_P0 for r in self.risks) or not self.pending_tasks_filter_ok

    def to_dict(self) -> dict:
        return {
            "db_path": self.db_path,
            "email_pattern": self.email_pattern,
            "counts": dict(self.counts),
            "total_pollution": self.total_pollution,
            "all_green": self.all_green,
            "has_p0_risk": self.has_p0_risk,
            "pending_tasks_filter_ok": self.pending_tasks_filter_ok,
            "pending_tasks_filter_detail": self.pending_tasks_filter_detail,
            "risks": [asdict(r) for r in self.risks],
        }


def _resolve_db_path(db_path: str | Path | None) -> Path:
    """Resolve the SQLite file path backing the configured database URL."""
    if db_path is not None:
        return Path(db_path)
    from api.database import DATABASE_URL

    if DATABASE_URL.startswith("sqlite"):
        raw = DATABASE_URL.replace("sqlite:///", "").replace("sqlite://", "")
        if raw in ("", ":memory:"):
            return Path("tradingagents.db")
        return Path(raw)
    # Non-SQLite backends: fall back to project default for reporting only.
    return Path("tradingagents.db")


def run_db_hygiene_check(
    db_path: str | Path | None = None,
    *,
    email_pattern: str = DEFAULT_EMAIL_PATTERN,
    skip_pending_tasks_check: bool = False,
) -> DbHygieneReport:
    """Run a read-only hygiene check; never mutates the DB.

    Args:
        db_path: SQLite file path. Defaults to the project DB inferred from
            ``DATABASE_URL``.
        email_pattern: SQL LIKE pattern identifying test accounts. Defaults to
            the conservative ``%@test.com`` used by the cleanup script.
        skip_pending_tasks_check: skip the runtime verification of
            ``get_pending_tasks`` (e.g. for cheap dry runs or test fixtures).
    """
    # Local import keeps api.services.db_hygiene_service importable even if
    # the scripts package is missing on a stripped deployment.
    from scripts.cleanup_test_db_pollution import collect_counts

    resolved = _resolve_db_path(db_path)
    risks: list[HygieneRisk] = []

    counts: dict[str, int] = {}
    if resolved.exists():
        try:
            with sqlite3.connect(str(resolved)) as conn:
                counts = dict(collect_counts(conn, email_pattern))
        except sqlite3.Error as exc:
            risks.append(
                HygieneRisk(
                    severity=SEVERITY_P0,
                    code="db_unreadable",
                    message=f"Could not read DB at {resolved}: {exc}",
                )
            )
            counts = {}
    else:
        # Brand-new deployments will not have a DB file yet. That's not a
        # pollution event, but flag it so callers know the check ran against
        # an absent DB.
        risks.append(
            HygieneRisk(
                severity=SEVERITY_INFO,
                code="db_not_found",
                message=f"DB file does not exist yet: {resolved}",
            )
        )

    total = sum(counts.values()) if counts else 0
    if total > 0:
        risks.append(
            HygieneRisk(
                severity=SEVERITY_P1,
                code="test_pollution_present",
                message=(
                    f"Found {total} test-account row(s) matching {email_pattern}; "
                    "run `python scripts/cleanup_test_db_pollution.py --execute` "
                    "(dry-run by default; backs up to var/db_backups/)."
                ),
            )
        )

    pending_ok = True
    pending_detail = "skipped"
    if not skip_pending_tasks_check:
        try:
            pending_ok, pending_detail = _verify_pending_tasks_filter()
            if not pending_ok:
                risks.append(
                    HygieneRisk(
                        severity=SEVERITY_P0,
                        code="pending_tasks_includes_test_users",
                        message=pending_detail,
                    )
                )
        except Exception as exc:  # pragma: no cover - defensive
            pending_ok = False
            pending_detail = f"pending-tasks filter verification failed: {exc}"
            risks.append(
                HygieneRisk(
                    severity=SEVERITY_P0,
                    code="pending_tasks_filter_unknown",
                    message=pending_detail,
                )
            )

    return DbHygieneReport(
        db_path=str(resolved),
        email_pattern=email_pattern,
        counts=counts,
        total_pollution=total,
        pending_tasks_filter_ok=pending_ok,
        pending_tasks_filter_detail=pending_detail,
        risks=risks,
    )


def _verify_pending_tasks_filter() -> tuple[bool, str]:
    """Verify ``get_pending_tasks`` still excludes ``@test.com`` users.

    Builds an isolated in-memory SQLite fixture (1 test user + 1 real user,
    each with one active scheduled task) and asks ``get_pending_tasks`` for
    pending work. The production DB is never touched.

    Returns ``(ok, detail)``. ``ok`` is False if the real-user task is
    missing (fixture broken) or any test-user task leaks through.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from api.database import Base, ScheduledAnalysisDB, UserDB
    from api.services.scheduled_service import get_pending_tasks

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    # Build every mapped table so future get_pending_tasks joins don't break
    # the verification. Tables that already exist are skipped.
    Base.metadata.create_all(bind=engine, checkfirst=True)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    today = "2026-01-01"
    test_user_id = "hygiene-test-user"
    real_user_id = "hygiene-real-user"

    with Session() as db:
        db.add(UserDB(id=test_user_id, email="hygiene-test@test.com"))
        db.add(UserDB(id=real_user_id, email="hygiene-real@example.com"))
        db.add(
            ScheduledAnalysisDB(
                id="sched-hygiene-test",
                user_id=test_user_id,
                symbol="000001",
                horizon="short",
                trigger_time="09:00",
                is_active=True,
            )
        )
        db.add(
            ScheduledAnalysisDB(
                id="sched-hygiene-real",
                user_id=real_user_id,
                symbol="600000",
                horizon="short",
                trigger_time="09:00",
                is_active=True,
            )
        )
        db.commit()

        pending = get_pending_tasks(db, today, "23:59")
        test_user_pending = [t for t in pending if t.user_id == test_user_id]
        real_user_pending = [t for t in pending if t.user_id == real_user_id]

        if not real_user_pending:
            return (
                False,
                "real-user task missing from get_pending_tasks — fixture broken",
            )
        if test_user_pending:
            return (
                False,
                (
                    f"get_pending_tasks returned {len(test_user_pending)} test-user "
                    "task(s) — filter no longer excludes @test.com users"
                ),
            )
        return True, "get_pending_tasks correctly excludes @test.com users"


def log_startup_hygiene_warning(
    report: DbHygieneReport,
    *,
    logger: logging.Logger,
) -> None:
    """Emit a single consolidated warning block for scheduler / preflight use.

    Never raises. Never triggers TA / LLM. P0 risks are logged at ERROR so
    operators can alert on them; the caller decides whether to hard-stop.
    """
    if report.all_green:
        logger.info(
            "[DB-Hygiene] all green: no test pollution, pending-task filter OK (db=%s)",
            report.db_path,
        )
        return

    if not report.pending_tasks_filter_ok:
        logger.error(
            "[DB-Hygiene] [P0] scheduler may execute test-user tasks: %s",
            report.pending_tasks_filter_detail,
        )

    if report.total_pollution > 0:
        summary = ", ".join(
            f"{table}={count}"
            for table, count in sorted(report.counts.items())
            if count
        )
        logger.warning(
            "[DB-Hygiene] found %d test-account row(s) (%s). "
            "Cleanup: `python scripts/cleanup_test_db_pollution.py --execute` "
            "(dry-run by default; backs up to var/db_backups/).",
            report.total_pollution,
            summary or "none",
        )

    for risk in report.risks:
        if risk.severity == SEVERITY_INFO:
            logger.info("[DB-Hygiene] %s", risk.message)
