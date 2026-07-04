#!/usr/bin/env python3
"""Read-only DB hygiene health check.

# [DATA-026] db_hygiene_check

Inspects the configured SQLite DB for ``@test.com`` test-account pollution
and verifies ``scheduled_service.get_pending_tasks`` still excludes test
users. Never mutates the DB. Never triggers TA / LLM. Never prints keys.

Exit code is 0 when the report is all-green, 1 otherwise (so it can be
wired into preflight gates such as AUTO-005).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Allow running as `python scripts/run_db_hygiene_check.py` from project root
# without pre-installing the package.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from api.services.db_hygiene_service import (  # noqa: E402  # [DATA-026] db_hygiene_check
    DEFAULT_EMAIL_PATTERN,
    run_db_hygiene_check,
)


def _render(report) -> str:
    lines = [
        "DB hygiene check",
        f"- db_path: {report.db_path}",
        f"- email_pattern: {report.email_pattern}",
        f"- all_green: {report.all_green}",
        f"- has_p0_risk: {report.has_p0_risk}",
        f"- total_pollution: {report.total_pollution}",
        f"- pending_tasks_filter_ok: {report.pending_tasks_filter_ok}",
        "",
        "| table | count |",
        "|---|---:|",
    ]
    for table in sorted(report.counts):
        lines.append(f"| {table} | {report.counts[table]} |")
    if report.risks:
        lines.append("")
        lines.append("Risks:")
        for risk in report.risks:
            lines.append(f"- [{risk.severity}] {risk.code}: {risk.message}")
    lines.append("")
    lines.append(
        "Cleanup (read first, then --execute after backup): "
        "`python scripts/cleanup_test_db_pollution.py`"
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="SQLite DB path. Defaults to the project DB inferred from DATABASE_URL.",
    )
    parser.add_argument(
        "--email-pattern",
        default=DEFAULT_EMAIL_PATTERN,
        help="SQL LIKE pattern identifying test accounts (default: %%@test.com).",
    )
    parser.add_argument(
        "--skip-pending-tasks-check",
        action="store_true",
        help="Skip the runtime verification of get_pending_tasks filter.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of markdown.",
    )
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(
        level=logging.WARNING,
        format="[%(asctime)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    args = parse_args()
    report = run_db_hygiene_check(
        args.db,
        email_pattern=args.email_pattern,
        skip_pending_tasks_check=args.skip_pending_tasks_check,
    )
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(_render(report))
    return 0 if report.all_green else 1


if __name__ == "__main__":
    raise SystemExit(main())
