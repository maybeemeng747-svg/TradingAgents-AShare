#!/usr/bin/env python3
"""Data source health check CLI — [M-008] data_source_health.

Runs small-sample smoke tests against every configured data-source endpoint
and writes a Markdown health report.

Usage:
    python scripts/run_health_check.py                     # live smoke
    python scripts/run_health_check.py --dry-run           # print report, don't write file
    python scripts/run_health_check.py --output /tmp/health.md
    python scripts/run_health_check.py --symbols 600519.SH,000001.SZ
"""

from __future__ import annotations

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.dataflows.health_check import (
    run_health_check,
    render_report,
    save_report,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Data source health check and fallback observability [M-008]",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the report to stdout without writing a file.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output file path (default: docs/data_source_health/YYYY-MM-DD.md).",
    )
    parser.add_argument(
        "--symbols",
        default=None,
        help="Comma-separated sample symbols (default: 600519.SH,000001.SZ).",
    )
    args = parser.parse_args()

    symbols = None
    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    print(f"[M-008] Running data source health check...", file=sys.stderr)
    report = run_health_check(symbols=symbols)

    md = render_report(report)

    if args.dry_run:
        print(md)
    else:
        if args.output:
            os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(md)
            print(f"[M-008] Report written to {args.output}", file=sys.stderr)
        else:
            path = save_report(report)
            print(f"[M-008] Report written to {path}", file=sys.stderr)

    s = report.summary
    failed = s.get("FAILED", 0)
    if failed > 0:
        print(f"[M-008] WARNING: {failed} endpoint(s) FAILED", file=sys.stderr)
        sys.exit(1)

    print(
        f"[M-008] OK={s.get('OK', 0)} FAILED={failed} "
        f"STALE={s.get('STALE', 0)} fallback={s.get('fallback_count', 0)}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
