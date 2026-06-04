#!/usr/bin/env python3
"""Live smoke CLI — [DATA-P1-ASTOCK-LIVE-SMOKE] astock_live_smoke.

Runs low-frequency live smoke against cn_astock/Eastmoney critical endpoints.

Usage:
    TA_LIVE_DATA_SMOKE=1 python scripts/run_live_smoke.py
    TA_LIVE_DATA_SMOKE=1 python scripts/run_live_smoke.py --symbols 600519.SH,603629.SH
    python scripts/run_live_smoke.py  # prints SKIPPED report, no live calls
"""

from __future__ import annotations

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.dataflows.live_smoke import (
    run_live_smoke,
    render_live_smoke_report,
    save_live_smoke_report,
    is_live_smoke_enabled,
    DEFAULT_LIVE_SMOKE_SYMBOLS,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Live smoke for cn_astock/Eastmoney critical endpoints [DATA-P1-ASTOCK-LIVE-SMOKE]",
    )
    parser.add_argument(
        "--symbols",
        default=None,
        help=f"Comma-separated sample symbols (default: {','.join(DEFAULT_LIVE_SMOKE_SYMBOLS[:3])}).",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output file path (default: docs/data_source_reports/live-smoke-YYYY-MM-DD.md).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print report to stdout without writing a file.",
    )
    args = parser.parse_args()

    symbols = None
    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    if not is_live_smoke_enabled():
        print(
            f"[DATA-P1-ASTOCK-LIVE-SMOKE] TA_LIVE_DATA_SMOKE not set — "
            f"running in SKIP mode (no live network calls).",
            file=sys.stderr,
        )

    report = run_live_smoke(symbols=symbols)
    md = render_live_smoke_report(report)

    if args.dry_run:
        print(md)
    else:
        if args.output:
            os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(md)
            print(f"[DATA-P1-ASTOCK-LIVE-SMOKE] Report written to {args.output}", file=sys.stderr)
        else:
            path = save_live_smoke_report(report)
            print(f"[DATA-P1-ASTOCK-LIVE-SMOKE] Report written to {path}", file=sys.stderr)

    s = report.summary
    failed = s.get("FAILED", 0)
    if failed > 0:
        print(f"[DATA-P1-ASTOCK-LIVE-SMOKE] WARNING: {failed} endpoint(s) FAILED", file=sys.stderr)
        sys.exit(1)

    if report.env_gated:
        print(
            f"[DATA-P1-ASTOCK-LIVE-SMOKE] SKIPPED={s.get('SKIPPED', 0)} "
            f"(set TA_LIVE_DATA_SMOKE=1 to run live)",
            file=sys.stderr,
        )
    else:
        print(
            f"[DATA-P1-ASTOCK-LIVE-SMOKE] OK={s.get('OK', 0)} FAILED={failed} "
            f"NORMAL_NO_DATA={s.get('NORMAL_NO_DATA', 0)}",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
