"""Live smoke test for cn_astock/Eastmoney critical endpoints.  # [DATA-P1-ASTOCK-LIVE-SMOKE] astock_live_smoke

Low-frequency live smoke that validates real endpoint availability, rate
limiting, field presence and unit correctness for the cn_astock provider
and its direct HTTP data sources (Eastmoney, Tencent, CNInfo).

**Gating** — all live network calls require ``TA_LIVE_DATA_SMOKE=1`` in the
environment.  Without it, tests are **skipped** and the smoke report shows
``SKIPPED``.

Usage (library):
    from tradingagents.dataflows.live_smoke import run_live_smoke, render_live_smoke_report
    report = run_live_smoke(symbols=["600519.SH", "000001.SZ"])
    print(render_live_smoke_report(report))

Usage (CLI):
    TA_LIVE_DATA_SMOKE=1 python scripts/run_live_smoke.py
    TA_LIVE_DATA_SMOKE=1 python scripts/run_live_smoke.py --symbols 600519.SH,603629.SH

Output:
    docs/data_source_reports/live-smoke-YYYY-MM-DD.md
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from .providers.cn_astock_provider import (
    CnAstockProvider,
    _rate_limit,
    _extract_code,
)

_LIVE_ENV = "TA_LIVE_DATA_SMOKE"
_MIN_ENDPOINT_INTERVAL = 0.5
_MAX_SYMBOLS = 3

DEFAULT_LIVE_SMOKE_SYMBOLS = ["600519.SH", "000001.SZ", "603629.SH"]


@dataclass
class EndpointSmokeResult:
    endpoint: str
    vendor: str
    symbol: str
    status: str = "NOT_RUN"
    latency_ms: float = 0.0
    field_count: int = 0
    unit: str = ""
    unit_verified: bool = False
    error: str = ""
    has_data: bool = False
    sample_snippet: str = ""
    rate_limit_ok: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "vendor": self.vendor,
            "symbol": self.symbol,
            "status": self.status,
            "latency_ms": round(self.latency_ms, 1),
            "field_count": self.field_count,
            "unit": self.unit,
            "unit_verified": self.unit_verified,
            "error": self.error[:200] if self.error else "",
            "has_data": self.has_data,
            "sample_snippet": self.sample_snippet[:120],
            "rate_limit_ok": self.rate_limit_ok,
        }


@dataclass
class LiveSmokeReport:
    run_at: str
    date: str
    symbols: List[str] = field(default_factory=list)
    results: List[EndpointSmokeResult] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)
    env_gated: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_at": self.run_at,
            "date": self.date,
            "symbols": self.symbols,
            "results": [r.to_dict() for r in self.results],
            "summary": self.summary,
            "env_gated": self.env_gated,
        }


_STATUS_OK = "OK"
_STATUS_FAILED = "FAILED"
_STATUS_SKIPPED = "SKIPPED"
_STATUS_NORMAL_NO_DATA = "NORMAL_NO_DATA"

_WAN_YUAN_PATTERN = re.compile(r"单位[：:]\s*万元")
_YUAN_PATTERN = re.compile(r"单位[：:]\s*元")


def _count_data_lines(text: str) -> int:
    if not text:
        return 0
    lines = [
        l for l in text.strip().split("\n")
        if l.strip() and not l.strip().startswith("#") and not l.strip().startswith("##")
    ]
    return max(0, len(lines) - 1)


def _detect_unit(text: str) -> str:
    if _WAN_YUAN_PATTERN.search(text):
        return "万元"
    if _YUAN_PATTERN.search(text):
        return "元"
    return ""


def _check_rate_limit(symbol: str, endpoint_key: str) -> bool:
    try:
        t0 = time.monotonic()
        _rate_limit(endpoint_key)
        elapsed = time.monotonic() - t0
        return elapsed < 2.0
    except Exception:
        return False


def _make_endpoint_definitions() -> List[Dict[str, Any]]:
    return [
        {
            "endpoint": "cn_astock/push2his_fund_flow",
            "method": "get_individual_fund_flow",
            "vendor": "cn_astock",
            "args_template": ("{symbol}",),
            "kwargs": {},
            "expected_unit": "万元",
        },
        {
            "endpoint": "cn_astock/datacenter_lhb",
            "method": "get_lhb_detail",
            "vendor": "cn_astock",
            "args_template": ("{symbol}", "{date}"),
            "kwargs": {"force": True},
            "expected_unit": "万元",
        },
        {
            "endpoint": "cn_astock/tencent_realtime_quote",
            "method": "get_realtime_quotes",
            "vendor": "cn_astock",
            "args_template": (["{symbol}"],),
            "kwargs": {},
            "expected_unit": "",
        },
        {
            "endpoint": "cn_astock/cninfo_announcements",
            "method": "get_announcements",
            "vendor": "cn_astock",
            "args_template": ("{symbol}",),
            "kwargs": {},
            "expected_unit": "",
        },
        {  # [DATA-014] policy_news_fixture_smoke
            "endpoint": "cn_astock/eastmoney_news",
            "method": "get_news",
            "vendor": "cn_astock",
            "args_template": ("{symbol}", "{date}", "{date}"),
            "kwargs": {},
            "expected_unit": "条",
        },
        {  # [DATA-014] policy_news_fixture_smoke
            "endpoint": "cn_astock/cls_global_news",
            "method": "get_global_news",
            "vendor": "cn_astock",
            "args_template": ("{date}",),
            "kwargs": {},
            "expected_unit": "条",
        },
        {  # [DATA-015] limit_up_pool_fallback
            "endpoint": "cn_astock/zt_pool",
            "method": "get_zt_pool",
            "vendor": "cn_astock",
            "args_template": ("{date}",),
            "kwargs": {},
            "expected_unit": "条",
        },
        {  # [DATA-016] hot_stock_fallback
            "endpoint": "cn_astock/hot_stocks",
            "method": "get_hot_stocks",
            "vendor": "cn_astock",
            "args_template": (),
            "kwargs": {},
            "expected_unit": "条",
        },
    ]


def _resolve_args(template: tuple, symbol: str, date: str) -> tuple:
    result = []
    for arg in template:
        if isinstance(arg, str):
            arg = arg.replace("{symbol}", symbol).replace("{date}", date)
            result.append(arg)
        elif isinstance(arg, list):
            result.append([a.replace("{symbol}", symbol).replace("{date}", date) for a in arg])
        else:
            result.append(arg)
    return tuple(result)


def _smoke_single_endpoint(
    provider: CnAstockProvider,
    ep_def: Dict[str, Any],
    symbol: str,
    date: str,
) -> EndpointSmokeResult:
    result = EndpointSmokeResult(
        endpoint=ep_def["endpoint"],
        vendor=ep_def["vendor"],
        symbol=symbol,
    )

    rate_key = f"live_smoke_{ep_def['endpoint']}"
    result.rate_limit_ok = _check_rate_limit(symbol, rate_key)

    time.sleep(_MIN_ENDPOINT_INTERVAL)

    args = _resolve_args(ep_def["args_template"], symbol, date)
    kwargs = ep_def.get("kwargs", {})

    method = getattr(provider, ep_def["method"], None)
    if method is None:
        result.status = _STATUS_FAILED
        result.error = f"Method {ep_def['method']} not found on provider"
        return result

    t0 = time.monotonic()
    try:
        raw = method(*args, **kwargs)
        elapsed = (time.monotonic() - t0) * 1000
        result.latency_ms = elapsed

        if raw is None or (isinstance(raw, str) and len(raw.strip()) < 10):
            result.status = _STATUS_FAILED
            result.error = "empty_or_minimal_response"
            return result

        if isinstance(raw, str):
            if "LHB_NORMAL_NO_DATA" in raw:
                result.status = _STATUS_NORMAL_NO_DATA
                result.has_data = False
                result.unit = _detect_unit(raw)
                result.field_count = _count_data_lines(raw)
                result.sample_snippet = raw[:120]
                return result

            if any(p in raw for p in ("数据获取失败", "暂不可用", "获取失败", "FAILED")):
                result.status = _STATUS_FAILED
                result.error = "failure_string_detected"
                result.sample_snippet = raw[:120]
                return result

            result.has_data = True
            result.field_count = _count_data_lines(raw)
            result.unit = _detect_unit(raw)
            result.unit_verified = result.unit == ep_def.get("expected_unit", "") if ep_def.get("expected_unit") else False
            result.sample_snippet = raw[:120]
            result.status = _STATUS_OK

        elif isinstance(raw, dict):
            result.has_data = bool(raw)
            result.field_count = len(raw)
            result.status = _STATUS_OK

        else:
            result.has_data = True
            result.status = _STATUS_OK

    except Exception as exc:
        elapsed = (time.monotonic() - t0) * 1000
        result.latency_ms = elapsed
        result.status = _STATUS_FAILED
        result.error = f"{type(exc).__name__}: {exc}"

    return result


def run_live_smoke(
    symbols: Optional[List[str]] = None,
    endpoints: Optional[List[Dict[str, Any]]] = None,
    smoke_fn: Optional[Any] = None,
) -> LiveSmokeReport:
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    report = LiveSmokeReport(
        run_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        date=date_str,
        env_gated=False,
    )

    if os.getenv(_LIVE_ENV, "").strip() != "1":
        report.env_gated = True
        if endpoints is None:
            endpoints = _make_endpoint_definitions()
        if symbols is None:
            symbols = DEFAULT_LIVE_SMOKE_SYMBOLS[:_MAX_SYMBOLS]
        report.symbols = symbols[:_MAX_SYMBOLS]
        for ep_def in endpoints:
            for sym in symbols[:_MAX_SYMBOLS]:
                report.results.append(EndpointSmokeResult(
                    endpoint=ep_def["endpoint"],
                    vendor=ep_def["vendor"],
                    symbol=sym,
                    status=_STATUS_SKIPPED,
                ))
        report.summary = _compute_summary(report.results)
        return report

    if symbols is None:
        symbols = DEFAULT_LIVE_SMOKE_SYMBOLS[:_MAX_SYMBOLS]
    if endpoints is None:
        endpoints = _make_endpoint_definitions()

    report.symbols = symbols[:_MAX_SYMBOLS]
    report.env_gated = False

    provider = CnAstockProvider()

    if smoke_fn is None:
        smoke_fn = _smoke_single_endpoint

    for ep_def in endpoints:
        for sym in symbols[:_MAX_SYMBOLS]:
            result = smoke_fn(provider, ep_def, sym, date_str)
            report.results.append(result)

    report.summary = _compute_summary(report.results)
    return report


def _compute_summary(results: List[EndpointSmokeResult]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "total": len(results),
        "OK": 0,
        "FAILED": 0,
        "SKIPPED": 0,
        "NORMAL_NO_DATA": 0,
        "NOT_RUN": 0,
        "all_passed": False,
        "has_failures": False,
        "by_endpoint": {},
    }

    for r in results:
        if r.status in summary:
            summary[r.status] += 1
        ep = summary["by_endpoint"].setdefault(r.endpoint, {
            "total": 0, "OK": 0, "FAILED": 0, "SKIPPED": 0,
            "NORMAL_NO_DATA": 0, "avg_latency_ms": 0.0,
        })
        ep["total"] += 1
        if r.status in ep:
            ep[r.status] += 1
        ep["avg_latency_ms"] += r.latency_ms

    for ep_data in summary["by_endpoint"].values():
        if ep_data["total"] > 0:
            ep_data["avg_latency_ms"] = round(
                ep_data["avg_latency_ms"] / ep_data["total"], 1
            )

    runnable = [r for r in results if r.status != _STATUS_SKIPPED]
    summary["all_passed"] = (
        len(runnable) > 0 and all(r.status in (_STATUS_OK, _STATUS_NORMAL_NO_DATA) for r in runnable)
    )
    summary["has_failures"] = any(r.status == _STATUS_FAILED for r in runnable)

    return summary


def render_live_smoke_report(report: LiveSmokeReport) -> str:
    lines: List[str] = []
    lines.append("# Live Smoke Report — cn_astock/Eastmoney Critical Endpoints")
    lines.append("")
    lines.append(f"- **Date**: {report.date}")
    lines.append(f"- **Run at**: {report.run_at}")
    lines.append(f"- **Symbols**: {', '.join(report.symbols)}")
    lines.append(f"- **Env gated**: {'Yes (TA_LIVE_DATA_SMOKE not set)' if report.env_gated else 'No (live calls executed)'}")
    lines.append("")

    s = report.summary
    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Count |")
    lines.append("|--------|-------|")
    lines.append(f"| Total checks | {s.get('total', 0)} |")
    lines.append(f"| OK | {s.get('OK', 0)} |")
    lines.append(f"| FAILED | {s.get('FAILED', 0)} |")
    lines.append(f"| SKIPPED (env gated) | {s.get('SKIPPED', 0)} |")
    lines.append(f"| NORMAL_NO_DATA | {s.get('NORMAL_NO_DATA', 0)} |")
    lines.append(f"| All runnable passed | {'Yes' if s.get('all_passed') else 'No'} |")
    lines.append(f"| Has failures | {'Yes' if s.get('has_failures') else 'No'} |")
    lines.append("")

    lines.append("## Endpoint Details")
    lines.append("")
    lines.append("| Endpoint | Vendor | Symbol | Status | Latency (ms) | Fields | Unit | Unit Verified | Error |")
    lines.append("|----------|--------|--------|--------|-------------|--------|------|---------------|-------|")

    for r in report.results:
        error_short = r.error[:50] + "..." if len(r.error) > 50 else r.error
        lines.append(
            f"| {r.endpoint} | {r.vendor} | {r.symbol} | {r.status} "
            f"| {r.latency_ms:.0f} | {r.field_count} | {r.unit or '-'} "
            f"| {'Yes' if r.unit_verified else '-'} | {error_short or '-'} |"
        )
    lines.append("")

    failed = [r for r in report.results if r.status == _STATUS_FAILED]
    if failed:
        lines.append("## Failed Endpoints")
        lines.append("")
        for r in failed:
            lines.append(f"- **{r.endpoint}** ({r.symbol}): {r.error}")
        lines.append("")

    lines.append("## Per-Endpoint Summary")
    lines.append("")
    by_ep = s.get("by_endpoint", {})
    if by_ep:
        lines.append("| Endpoint | Total | OK | FAILED | SKIPPED | Avg Latency (ms) |")
        lines.append("|----------|-------|----|--------|---------|------------------|")
        for ep_name, ep_data in by_ep.items():
            lines.append(
                f"| {ep_name} | {ep_data['total']} | {ep_data['OK']} "
                f"| {ep_data['FAILED']} | {ep_data['SKIPPED']} "
                f"| {ep_data['avg_latency_ms']} |"
            )
        lines.append("")

    lines.append("---")
    lines.append(f"*Generated by live_smoke.py — [DATA-P1-ASTOCK-LIVE-SMOKE] astock_live_smoke*")
    lines.append("")

    return "\n".join(lines)


def save_live_smoke_report(
    report: LiveSmokeReport,
    output_dir: str = "docs/data_source_reports",
) -> str:
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"live-smoke-{report.date}.md")
    md = render_live_smoke_report(report)
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    return path


def is_live_smoke_enabled() -> bool:
    return os.getenv(_LIVE_ENV, "").strip() == "1"
