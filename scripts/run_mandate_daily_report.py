#!/usr/bin/env python3
# [H-016] mandate_daily_cli
"""H-016 — CLI entry point for the Haotian / policy-left-side daily report.

Wraps the H-015 ``build_mandate_daily_report`` pipeline so OpenClaw or a cron
job can regenerate ``docs/mandate_daily_reports/mandate-YYYY-MM-DD.{md,json}``
overnight or on demand, and rotate the older reports out.

Hard rules (also enforced in the underlying module):

* Never calls an LLM.
* Never scans the full market — only reads the existing TradeFlow candidate
  pool (``tradeflow.db``) and the topic heatmap built from it.
* Never writes to ``tradeflow.db`` / ``tradingagents.db``. The only side
  effects are the mandate report files inside ``--output-dir`` (and only when
  ``--dry-run`` is *not* set).
* Retention only deletes files matching ``mandate-YYYY-MM-DD.(md|json)`` in
  ``--output-dir``; sibling files are left untouched.

Usage:

    # Generate today's report with the default 60-day heatmap window.
    python scripts/run_mandate_daily_report.py

    # Backfill a specific trading day.
    python scripts/run_mandate_daily_report.py --as-of 2026-06-24

    # Preview without writing (OpenClaw dry-run / cron pre-check).
    python scripts/run_mandate_daily_report.py --dry-run

    # Custom output dir + retention.
    python scripts/run_mandate_daily_report.py \\
        --output-dir docs/mandate_daily_reports \\
        --retention-days 120

    # Point at a different tradeflow.db (read-only).
    python scripts/run_mandate_daily_report.py --tf-db-path /srv/data/tradeflow.db

OpenClaw / cron example (no LLM, no live market scan, no DB write):

    30 22 * * 1-5  cd /path/to/TradingAgents-AShare && \\
        .venv/bin/python scripts/run_mandate_daily_report.py \\
        --output-dir docs/mandate_daily_reports \\
        --retention-days 90 >> logs/mandate_daily.log 2>&1
"""

from __future__ import annotations

import argparse
import os
import sys

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from tradingagents.tradeflow.mandate_daily_report import (  # noqa: E402
    _DEFAULT_REPORT_DIR,
    _DEFAULT_RETENTION_DAYS,
    build_mandate_daily_report,
    build_mandate_daily_summary,
    purge_old_mandate_daily_reports,
    save_mandate_daily_report,
)


def _resolve_tf_db_path(arg_path: str | None) -> str:
    """Resolve the read-only tradeflow DB path the same way the API does."""
    if arg_path:
        return arg_path
    env_path = os.environ.get("TRADEFLOW_DB_PATH", "")
    if env_path:
        return env_path
    return os.path.join(project_root, "tradeflow.db")


def _load_topic_heatmap(as_of: str, window_days: int, tf_db_path: str) -> dict:
    """Build the topic heatmap from the read-only TradeFlow DB.

    Imported lazily so the CLI's ``--help`` and dry-run path stay cheap and so
    we never accidentally spin up the wider API service.

    ``read_only=True`` is mandatory here: the report CLI must NEVER mutate
    ``tradeflow.db`` (no schema migration, no writes), even when the file is
    missing columns or tables. See H-016 / Codex round-1 review.
    """
    from api.services.tradeflow_service import get_topic_heatmap

    return get_topic_heatmap(
        as_of=as_of,
        window_days=window_days,
        tf_db_path=tf_db_path,
        read_only=True,
    )


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="[H-016] Haotian mandate daily report generator (no LLM, no market scan)",
    )
    parser.add_argument(
        "--as-of",
        default="",
        help="Report date (YYYY-MM-DD). Defaults to the latest tradeflow_candidates date.",
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=60,
        help="Heatmap lookback window in days (default 60).",
    )
    parser.add_argument(
        "--output-dir",
        default=_DEFAULT_REPORT_DIR,
        help=f"Directory for mandate-YYYY-MM-DD.{{md,json}} (default {_DEFAULT_REPORT_DIR}).",
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=_DEFAULT_RETENTION_DAYS,
        help=(
            f"Rotate out mandate report files older than N days (default "
            f"{_DEFAULT_RETENTION_DAYS}). Only deletes mandate-* files in "
            f"--output-dir. Set to 0 to disable retention."
        ),
    )
    parser.add_argument(
        "--tf-db-path",
        default=None,
        help=(
            "Read-only TradeFlow DB path. Defaults to $TRADEFLOW_DB_PATH or "
            "tradeflow.db in the project root."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the summary only; do not write files or run retention.",
    )
    args = parser.parse_args(argv)

    tf_db_path = _resolve_tf_db_path(args.tf_db_path)

    if not os.path.exists(tf_db_path):
        print(
            f"[H-016] no tradeflow DB at {tf_db_path}; "
            "falling back to an empty heatmap (no market scan, no LLM).",
            file=sys.stderr,
        )

    heatmap = _load_topic_heatmap(args.as_of, args.window_days, tf_db_path)
    status = "ok" if heatmap.get("status") != "no_data" else "no_data"

    report = build_mandate_daily_report(heatmap, as_of=heatmap.get("as_of") or args.as_of)

    if args.dry_run:
        # [H-016] mandate_daily_cli — dry-run: print summary, never write.
        print(build_mandate_daily_summary(report, source="dry_run", status=status))
        if status == "no_data":
            print(
                "[H-016] dry-run produced no_data (empty heatmap); "
                "run TradeFlow discovery/plan first.",
                file=sys.stderr,
            )
        return 0

    os.makedirs(args.output_dir, exist_ok=True)
    md_path, json_path = save_mandate_daily_report(report, output_dir=args.output_dir)

    purge_summary: dict[str, list[str]] = {"deleted": [], "kept": [], "skipped": []}
    if args.retention_days > 0:
        purge_summary = purge_old_mandate_daily_reports(
            args.output_dir,
            args.retention_days,
            as_of=report.as_of,
        )

    print(build_mandate_daily_summary(report, source="generated_file", status=status))
    print(f"[H-016] wrote {md_path}")
    print(f"[H-016] wrote {json_path}")
    print(
        f"[H-016] retention({args.retention_days}d): "
        f"deleted={len(purge_summary['deleted'])} "
        f"kept={len(purge_summary['kept'])} "
        f"skipped={len(purge_summary['skipped'])}"
    )
    if purge_summary["deleted"]:
        for name in purge_summary["deleted"]:
            print(f"  - purged {name}")
    return 0


def main() -> None:
    raise SystemExit(_cli())


if __name__ == "__main__":
    main()
