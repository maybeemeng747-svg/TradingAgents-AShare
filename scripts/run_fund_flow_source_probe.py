#!/usr/bin/env python3
"""Fund flow source probe CLI — [DATA-024] fund_flow_source_probe.

Runs the main-force capital (主力资金) provider probe. Defaults to a
fixture dry-run (no network calls). Live smoke requires BOTH
``--live-smoke`` and ``TA_LIVE_DATA_SMOKE=1``.

Usage:
    # fixture dry-run (default; always safe; no network)
    python scripts/run_fund_flow_source_probe.py
    python scripts/run_fund_flow_source_probe.py --dry-run

    # live smoke (gated by env var)
    TA_LIVE_DATA_SMOKE=1 python scripts/run_fund_flow_source_probe.py \\
        --live-smoke --symbols 600519.SH,000001.SZ

Output:
    docs/data_source_reports/fund-flow-probe-YYYY-MM-DD.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.dataflows.fund_flow_source_probe import (
    DEFAULT_PROBE_SYMBOLS,
    build_capability_matrix_overlay,
    is_live_probe_enabled,
    render_fund_flow_probe_report,
    run_fund_flow_probe,
    save_fund_flow_probe_report,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Main-force fund flow provider probe with error attribution "
            "[DATA-024] fund_flow_source_probe"
        ),
    )
    parser.add_argument(
        "--symbols",
        default=None,
        help=(
            f"Comma-separated sample symbols for live-smoke "
            f"(default: {','.join(DEFAULT_PROBE_SYMBOLS[:3])}). "
            f"Ignored in fixture dry-run mode."
        ),
    )
    parser.add_argument(
        "--live-smoke",
        action="store_true",
        help=(
            "Run live network calls against route_to_vendor. "
            "Still requires TA_LIVE_DATA_SMOKE=1 to actually hit the network; "
            "otherwise SKIPPED rows are emitted."
        ),
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output file path (default: docs/data_source_reports/fund-flow-probe-YYYY-MM-DD.md).",
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
    if wants_live and not is_live_probe_enabled():
        print(
            "[DATA-024] --live-smoke requested but TA_LIVE_DATA_SMOKE!=1; "
            "no network calls will be made (results will be SKIPPED).",
            file=sys.stderr,
        )
    if not wants_live:
        print(
            "[DATA-024] fixture dry-run mode (default). "
            "Pass --live-smoke + TA_LIVE_DATA_SMOKE=1 for real network sampling.",
            file=sys.stderr,
        )

    report = run_fund_flow_probe(symbols=symbols, live_smoke=wants_live)
    md = render_fund_flow_probe_report(report)
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
                print(f"[DATA-024] Report written to {args.output}", file=sys.stderr)
            else:
                path = save_fund_flow_probe_report(report)
                print(f"[DATA-024] Report written to {path}", file=sys.stderr)

    s = report.summary
    missing = s.get("required_classes_missing", [])
    if missing:
        print(
            f"[DATA-024] WARNING: required 5-class attribution missing: {missing}",
            file=sys.stderr,
        )

    # 退出码约定:
    #   - fixture dry-run: 任一 fixture 归因错误 (all_passed=False) → exit 1
    #   - live mode 且 env 未 gate: 出现 NETWORK_ERROR/RATE_LIMITED/FIELD_CHANGE
    #     /UNKNOWN_UNIT/UNKNOWN → exit 1
    #   - live mode 且 env gated: 不算失败 (SKIPPED), exit 0
    if report.mode == "fixture" and not s.get("all_passed"):
        print(
            "[DATA-024] WARNING: fixture dry-run did not match expected_error_type "
            "for every fixture",
            file=sys.stderr,
        )
        sys.exit(1)
    if report.mode == "live" and not report.env_gated and s.get("has_failures"):
        print(
            "[DATA-024] WARNING: live-smoke observed provider failures",
            file=sys.stderr,
        )
        sys.exit(1)

    if report.mode == "live" and report.env_gated:
        print(
            f"[DATA-024] SKIPPED={s.get('by_status', {}).get('SKIPPED', 0)} "
            "(set TA_LIVE_DATA_SMOKE=1 to run live)",
            file=sys.stderr,
        )
    else:
        by_et = s.get("by_error_type", {})
        print(
            "[DATA-024] mode={} total={} OK={} NETWORK_ERROR={} "
            "RATE_LIMITED={} FIELD_CHANGE={} UNKNOWN_UNIT={} NO_DATA={}".format(
                report.mode,
                s.get("total", 0),
                by_et.get("ok", 0),
                by_et.get("network_error", 0),
                by_et.get("rate_limited", 0),
                by_et.get("field_change", 0),
                by_et.get("unknown_unit", 0),
                by_et.get("no_data", 0),
            ),
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
