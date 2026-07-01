#!/usr/bin/env python3
"""Research report sources smoke CLI — [DATA-025] free_research_report_sources.

Runs the free research report source smoke against AKShare
``stock_research_report_em`` (东方财富研报中心). Defaults to a fixture dry-run
(no network calls). Live smoke requires BOTH ``--live-smoke`` and
``TA_LIVE_DATA_SMOKE=1``.

Usage:
    # fixture dry-run (default; always safe; no network)
    python scripts/run_research_report_smoke.py
    python scripts/run_research_report_smoke.py --dry-run

    # live smoke (gated by env var)
    TA_LIVE_DATA_SMOKE=1 python scripts/run_research_report_smoke.py \\
        --live-smoke --symbols 600519.SH,000001.SZ

Output:
    docs/data_source_reports/research_report_sources-YYYY-MM-DD.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.dataflows.research_report_sources import (
    DEFAULT_SMOKE_SYMBOLS,
    is_live_smoke_enabled,
    render_research_report_smoke_report,
    run_research_report_smoke,
    save_research_report_smoke_report,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Free research report sources smoke "
            "[DATA-025] free_research_report_sources"
        ),
    )
    parser.add_argument(
        "--symbols",
        default=None,
        help=(
            f"Comma-separated sample symbols for live-smoke "
            f"(default: {','.join(DEFAULT_SMOKE_SYMBOLS[:3])}). "
            f"Ignored in fixture dry-run mode."
        ),
    )
    parser.add_argument(
        "--live-smoke",
        action="store_true",
        help=(
            "Run live network calls against AKShare stock_research_report_em. "
            "Still requires TA_LIVE_DATA_SMOKE=1 to actually hit the network; "
            "otherwise SKIPPED rows are emitted."
        ),
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output file path (default: docs/data_source_reports/research_report_sources-YYYY-MM-DD.md).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print report to stdout without writing a file.",
    )
    parser.add_argument(
        "--stdout-json",
        action="store_true",
        help="Print the full report as JSON to stdout (human logs go to stderr).",
    )
    args = parser.parse_args()

    symbols = None
    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    wants_live = bool(args.live_smoke)
    if wants_live and not is_live_smoke_enabled():
        print(
            "[DATA-025] --live-smoke requested but TA_LIVE_DATA_SMOKE!=1; "
            "no network calls will be made (results will be SKIPPED).",
            file=sys.stderr,
        )
    if not wants_live:
        print(
            "[DATA-025] fixture dry-run mode (default). "
            "Pass --live-smoke + TA_LIVE_DATA_SMOKE=1 for real network sampling.",
            file=sys.stderr,
        )

    report = run_research_report_smoke(symbols=symbols, live_smoke=wants_live)
    md = render_research_report_smoke_report(report)

    if args.stdout_json:
        payload = {
            "report": report.to_dict(),
            "markdown": md,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if args.dry_run:
            print(md)
        else:
            if args.output:
                os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
                with open(args.output, "w", encoding="utf-8") as f:
                    f.write(md)
                print(f"[DATA-025] Report written to {args.output}", file=sys.stderr)
            else:
                path = save_research_report_smoke_report(report)
                print(f"[DATA-025] Report written to {path}", file=sys.stderr)

    s = report.summary
    # 退出码约定 (对齐 fund_flow_source_probe):
    #   - fixture dry-run: 任一 fixture 状态与 expected 不符 (all_passed=False) → exit 1
    #   - live mode 且 env 未 gate: 出现 FAILED → exit 1
    #   - live mode 且 env gated: 不算失败 (SKIPPED), exit 0
    if report.mode == "fixture" and not s.get("all_passed"):
        print(
            "[DATA-025] WARNING: fixture dry-run status mismatch",
            file=sys.stderr,
        )
        sys.exit(1)
    if report.mode == "live" and not report.env_gated and s.get("has_failures"):
        print(
            "[DATA-025] WARNING: live-smoke observed provider failures",
            file=sys.stderr,
        )
        sys.exit(1)

    if report.mode == "live" and report.env_gated:
        print(
            f"[DATA-025] SKIPPED={s.get('by_status', {}).get('SKIPPED', 0)} "
            "(set TA_LIVE_DATA_SMOKE=1 to run live)",
            file=sys.stderr,
        )
    else:
        print(
            "[DATA-025] mode={} total={} HAS_DATA={} NORMAL_NO_DATA={} FAILED={}".format(
                report.mode,
                s.get("total", 0),
                s.get("HAS_DATA", 0),
                s.get("NORMAL_NO_DATA", 0),
                s.get("FAILED", 0),
            ),
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
