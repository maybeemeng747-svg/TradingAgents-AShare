#!/usr/bin/env python3
"""Research source smoke CLI — [DATA-027] research_source_smoke.

Runs the extended free research/announcement/half-year-report source smoke
with failure attribution. Defaults to a fixture dry-run (no network calls).
Live smoke requires BOTH ``--live-smoke`` and ``TA_LIVE_DATA_SMOKE=1``.

Usage:
    # fixture dry-run (default; always safe; no network)
    python scripts/run_research_source_smoke.py
    python scripts/run_research_source_smoke.py --dry-run

    # live smoke (gated by env var)
    TA_LIVE_DATA_SMOKE=1 python scripts/run_research_source_smoke.py \\
        --live-smoke --symbols 600519.SH,000001.SZ

Output:
    docs/data_source_reports/research-source-smoke-YYYY-MM-DD.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.dataflows.research_source_smoke import (
    DEFAULT_SMOKE_SYMBOLS,
    build_capability_matrix_overlay,
    is_live_smoke_enabled,
    render_research_source_smoke_report,
    run_research_source_smoke,
    save_research_source_smoke_report,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Free research/announcement/half-year-report source smoke "
            "with failure attribution [DATA-027] research_source_smoke"
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
        help="Output file path (default: docs/data_source_reports/research-source-smoke-YYYY-MM-DD.md).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print report to stdout without writing a file.",
    )
    parser.add_argument(
        "--stdout-json",
        action="store_true",
        help="Print the full report + overlay as JSON to stdout (human logs go to stderr).",
    )
    args = parser.parse_args()

    symbols = None
    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    wants_live = bool(args.live_smoke)
    if wants_live and not is_live_smoke_enabled():
        print(
            "[DATA-027] --live-smoke requested but TA_LIVE_DATA_SMOKE!=1; "
            "no network calls will be made (results will be SKIPPED).",
            file=sys.stderr,
        )
    if not wants_live:
        print(
            "[DATA-027] fixture dry-run mode (default). "
            "Pass --live-smoke + TA_LIVE_DATA_SMOKE=1 for real network sampling.",
            file=sys.stderr,
        )

    report = run_research_source_smoke(symbols=symbols, live_smoke=wants_live)
    md = render_research_source_smoke_report(report)
    overlay = build_capability_matrix_overlay(report)

    if args.stdout_json:
        payload = {
            "report": report.to_dict(),
            "overlay": overlay,
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
                print(f"[DATA-027] Report written to {args.output}", file=sys.stderr)
            else:
                path = save_research_source_smoke_report(report)
                print(f"[DATA-027] Report written to {path}", file=sys.stderr)

    s = report.summary
    missing = s.get("required_classes_missing", [])
    if missing:
        print(
            f"[DATA-027] WARNING: required attribution missing: {missing}",
            file=sys.stderr,
        )

    # 退出码约定 (对齐 fund_flow_source_probe / research_report_sources):
    #   - fixture dry-run: 任一 fixture 归因错误 (all_passed=False) → exit 1
    #   - live mode 且 env 未 gate: 出现 NETWORK_ERROR/RATE_LIMITED/UNKNOWN → exit 1
    #   - live mode 且 env gated: 不算失败 (SKIPPED), exit 0
    if report.mode == "fixture" and not s.get("all_passed"):
        print(
            "[DATA-027] WARNING: fixture dry-run did not match expected_error_type "
            "for every fixture",
            file=sys.stderr,
        )
        sys.exit(1)
    if report.mode == "live" and not report.env_gated and s.get("has_failures"):
        print(
            "[DATA-027] WARNING: live-smoke observed provider failures",
            file=sys.stderr,
        )
        sys.exit(1)

    if report.mode == "live" and report.env_gated:
        print(
            f"[DATA-027] SKIPPED={s.get('by_status', {}).get('SKIPPED', 0)} "
            "(set TA_LIVE_DATA_SMOKE=1 to run live)",
            file=sys.stderr,
        )
    else:
        by_et = s.get("by_error_type", {})
        print(
            "[DATA-027] mode={} total={} ok={} network_error={} rate_limited={} "
            "field_missing={} schema_change={} no_data={}".format(
                report.mode,
                s.get("total", 0),
                by_et.get("ok", 0),
                by_et.get("network_error", 0),
                by_et.get("rate_limited", 0),
                by_et.get("field_missing", 0),
                by_et.get("schema_change", 0),
                by_et.get("no_data", 0),
            ),
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
