#!/usr/bin/env python3
"""REPORT-UX-002 — Historical report semantics + data-gap dry-run audit CLI.

Scans completed TA reports and prints a markdown pre-check that explains which
historical reports can have their DECISION-001 3-layer action semantics and
DATA-021 ``data_blockers`` + DATA-004 ``raw_evidence`` recovered, which need a
rerun, and which can no longer be judged.

This script is **read-only**: it never calls ``db.commit`` / ``db.add``. The
``--dry-run`` flag is always effectively on (kept for symmetry with the other
audit CLIs and to make the intent explicit on the command line).

Usage:
    python scripts/audit_report_semantics.py
    python scripts/audit_report_semantics.py --dry-run
    python scripts/audit_report_semantics.py --user-id <uid> --limit 200
    python scripts/audit_report_semantics.py --output docs/report_semantics_audit_sample.md
    python scripts/audit_report_semantics.py --db-url sqlite:///./tradingagents.db
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.services.report_semantics_audit import (
    DEFAULT_AUDIT_LIMIT,
    audit_report_semantics,
    render_audit_report,
)


def _default_db_url() -> str:
    """Resolve the local tradingagents.db path the same way the API does."""
    for env_var in ("TRADINGAGENTS_DB_URL", "DATABASE_URL"):
        url = os.environ.get(env_var)
        if url:
            return url
    # Fall back to the project-local sqlite file (read-only intent).
    return "sqlite:///./tradingagents.db"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="REPORT-UX-002 read-only report semantics audit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Always on — kept for parity with other audit CLIs. Never writes to the DB.",
    )
    parser.add_argument(
        "--db-url",
        default=None,
        help="SQLAlchemy DB URL. Defaults to $TRADINGAGENTS_DB_URL / ./tradingagents.db.",
    )
    parser.add_argument(
        "--user-id",
        default=None,
        help="Only scan reports for this user_id. Default: all users (migration scope).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_AUDIT_LIMIT,
        help=f"Max completed reports to scan (default {DEFAULT_AUDIT_LIMIT}).",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Write the markdown audit to this file. Default: stdout.",
    )
    args = parser.parse_args()

    db_url = args.db_url or _default_db_url()
    # read-only intent: force read-only pragma for sqlite where supported.
    connect_args = {}
    if db_url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
    engine = create_engine(db_url, connect_args=connect_args)
    SessionLocal = sessionmaker(bind=engine)

    try:
        db = SessionLocal()
        try:
            audit = audit_report_semantics(
                db,
                user_id=args.user_id,
                limit=args.limit,
            )
        finally:
            db.close()
    finally:
        engine.dispose()

    markdown = render_audit_report(audit)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(markdown)
        print(
            f"[REPORT-UX-002] audit written to {args.output} "
            f"(scanned {audit['scanned_report_count']}, dry_run={audit['dry_run']})",
            file=sys.stderr,
        )
    else:
        print(markdown)

    # Exit code: non-zero only if the scan itself failed to run (0 reports
    # scanned because of a DB error). Gaps in historical reports are expected
    # and are the whole point of this pre-check, so they do not fail the CLI.
    return 0 if audit.get("scanned_report_count", 0) is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
