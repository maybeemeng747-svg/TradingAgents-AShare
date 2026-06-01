"""Data source health check and fallback observability.  # [M-008] data_source_health

Runs small-sample smoke tests against every configured data-source endpoint,
records success / failure / latency / fallback chain, and generates a
human-readable Markdown report.

Usage (library):
    from tradingagents.dataflows.health_check import run_health_check, render_report
    report = run_health_check()
    print(render_report(report))

Usage (CLI):
    python scripts/run_health_check.py [--dry-run] [--output docs/data_source_health/YYYY-MM-DD.md]
"""

from __future__ import annotations

import time
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from .interface import route_to_vendor, _registry, get_vendor, _resolve_vendor_chain, TOOLS_CATEGORIES

# ── Constants ──────────────────────────────────────────────────────────

STATUS_OK = "OK"
STATUS_FAILED = "FAILED"
STATUS_STALE = "STALE"
STATUS_NOT_QUERIED = "NOT_QUERIED"

DEFAULT_SAMPLE_SYMBOLS = ["600519.SH", "000001.SZ"]
DEFAULT_HISTORY_DAYS = 30

# ── Data models ────────────────────────────────────────────────────────


@dataclass
class EndpointCheck:
    method: str
    category: str
    sample_symbol: str
    status: str = STATUS_NOT_QUERIED
    vendor: str = ""
    fallback_from: str = ""
    latency_ms: float = 0.0
    error: str = ""
    chain: List[str] = field(default_factory=list)
    record_count: int = 0
    is_stale: bool = False
    stale_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "method": self.method,
            "category": self.category,
            "sample_symbol": self.sample_symbol,
            "status": self.status,
            "vendor": self.vendor,
            "fallback_from": self.fallback_from,
            "latency_ms": round(self.latency_ms, 1),
            "error": self.error,
            "chain": self.chain,
            "record_count": self.record_count,
            "is_stale": self.is_stale,
            "stale_reason": self.stale_reason,
        }


@dataclass
class HealthReport:
    run_at: str
    date: str
    checks: List[EndpointCheck] = field(default_factory=list)
    summary: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_at": self.run_at,
            "date": self.date,
            "checks": [c.to_dict() for c in self.checks],
            "summary": self.summary,
        }


# ── Endpoint definitions ───────────────────────────────────────────────

def _default_endpoints() -> List[Dict[str, Any]]:
    today = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=DEFAULT_HISTORY_DAYS)).strftime("%Y%m%d")
    today_dashed = datetime.now().strftime("%Y-%m-%d")
    endpoints = [
        {
            "method": "get_stock_data",
            "args": ("_SYMBOL_", start, today),
            "kwargs": {},
        },
        {
            "method": "get_indicators",
            "args": ("_SYMBOL_", "rsi", today, 14),
            "kwargs": {},
        },
        {
            "method": "get_fundamentals",
            "args": ("_SYMBOL_",),
            "kwargs": {"curr_date": today},
        },
        {
            "method": "get_balance_sheet",
            "args": ("_SYMBOL_",),
            "kwargs": {"freq": "quarterly", "curr_date": today},
        },
        {
            "method": "get_cashflow",
            "args": ("_SYMBOL_",),
            "kwargs": {"freq": "quarterly", "curr_date": today},
        },
        {
            "method": "get_income_statement",
            "args": ("_SYMBOL_",),
            "kwargs": {"freq": "quarterly", "curr_date": today},
        },
        {
            "method": "get_news",
            "args": ("_SYMBOL_", start, today),
            "kwargs": {},
        },
        {
            "method": "get_realtime_quotes",
            "args": (["_SYMBOL_"],),
            "kwargs": {},
        },
    ]

    # [DATA-P0-603629] astock_source_fallback: add cn_market_data endpoints
    endpoints.extend([
        {
            "method": "get_individual_fund_flow",
            "args": ("_SYMBOL_",),
            "kwargs": {},
        },
        {
            "method": "get_lhb_detail",
            "args": ("_SYMBOL_", today_dashed),
            "kwargs": {"force": True},
        },
        {
            "method": "get_announcements",
            "args": ("_SYMBOL_",),
            "kwargs": {},
        },
    ])

    return endpoints


# ── Single-endpoint probe ──────────────────────────────────────────────

def _probe_endpoint(
    method: str,
    args: tuple,
    kwargs: dict,
    symbol: str,
) -> EndpointCheck:
    category = ""
    for cat, info in TOOLS_CATEGORIES.items():
        if method in info["tools"]:
            category = cat
            break

    chain: List[str] = []
    try:
        chain = _resolve_vendor_chain(method, get_vendor(category, method))
    except Exception:
        chain = []

    check = EndpointCheck(
        method=method,
        category=category,
        sample_symbol=symbol,
        chain=chain,
    )

    def _replace_symbol(v):
        if v == "_SYMBOL_":
            return symbol
        if isinstance(v, list):
            return [_replace_symbol(item) for item in v]
        if isinstance(v, tuple):
            return tuple(_replace_symbol(item) for item in v)
        return v

    real_args = tuple(_replace_symbol(a) for a in args)
    real_kwargs = {k: _replace_symbol(v) for k, v in kwargs.items()}

    t0 = time.monotonic()
    try:
        result = route_to_vendor(method, *real_args, **real_kwargs)
        elapsed = (time.monotonic() - t0) * 1000

        check.latency_ms = elapsed
        check.vendor = chain[0] if chain else ""

        if result is None or (isinstance(result, str) and len(result.strip()) < 10):
            check.status = STATUS_STALE
            check.is_stale = True
            check.stale_reason = "empty_or_minimal_response"
        elif isinstance(result, str):
            lines = [l for l in result.strip().split("\n") if l.strip() and not l.startswith("#")]
            check.record_count = max(0, len(lines) - 1)
            check.status = STATUS_OK
            try:
                from .interface import get_last_hit_vendor
                actual_vendor = get_last_hit_vendor(method)
                if actual_vendor:
                    check.vendor = actual_vendor
            except Exception:
                pass
        else:
            check.status = STATUS_OK
    except Exception as exc:
        elapsed = (time.monotonic() - t0) * 1000
        check.latency_ms = elapsed
        check.status = STATUS_FAILED
        check.error = f"{type(exc).__name__}: {exc}"
        if chain:
            check.vendor = chain[0]

    return check


# ── Main health-check runner ───────────────────────────────────────────

def run_health_check(
    symbols: Optional[List[str]] = None,
    endpoints: Optional[List[Dict[str, Any]]] = None,
    probe_fn: Optional[Callable] = None,
) -> HealthReport:
    now = datetime.now()
    report = HealthReport(
        run_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        date=now.strftime("%Y-%m-%d"),
    )

    if symbols is None:
        symbols = DEFAULT_SAMPLE_SYMBOLS
    if endpoints is None:
        endpoints = _default_endpoints()
    if probe_fn is None:
        probe_fn = _probe_endpoint

    symbol = symbols[0]

    for ep in endpoints:
        check = probe_fn(
            method=ep["method"],
            args=ep.get("args", ()),
            kwargs=ep.get("kwargs", {}),
            symbol=symbol,
        )
        report.checks.append(check)

    # Detect fallback: when the first vendor in chain differs from actual vendor
    for check in report.checks:
        if check.status == STATUS_OK and check.chain:
            configured_first = check.chain[0]
            if check.vendor and check.vendor != configured_first:
                check.fallback_from = configured_first

    summary: Dict[str, int] = {
        STATUS_OK: 0,
        STATUS_FAILED: 0,
        STATUS_STALE: 0,
        STATUS_NOT_QUERIED: 0,
        "fallback_count": 0,
        "total": len(report.checks),
    }
    for check in report.checks:
        if check.status in summary:
            summary[check.status] += 1
        if check.fallback_from:
            summary["fallback_count"] += 1
    report.summary = summary

    return report


# ── Markdown rendering ─────────────────────────────────────────────────

def render_report(report: HealthReport) -> str:
    lines: List[str] = []
    lines.append(f"# Data Source Health Report")
    lines.append("")
    lines.append(f"- **Date**: {report.date}")
    lines.append(f"- **Run at**: {report.run_at}")
    lines.append("")

    s = report.summary
    lines.append("## Summary")
    lines.append("")
    lines.append(f"| Metric | Count |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Total endpoints checked | {s.get('total', 0)} |")
    lines.append(f"| OK | {s.get(STATUS_OK, 0)} |")
    lines.append(f"| FAILED | {s.get(STATUS_FAILED, 0)} |")
    lines.append(f"| STALE | {s.get(STATUS_STALE, 0)} |")
    lines.append(f"| NOT_QUERIED | {s.get(STATUS_NOT_QUERIED, 0)} |")
    lines.append(f"| Fallback triggered | {s.get('fallback_count', 0)} |")
    lines.append("")

    lines.append("## Endpoint Details")
    lines.append("")
    lines.append("| Method | Category | Symbol | Status | Vendor | Fallback From | Latency (ms) | Records | Error |")
    lines.append("|--------|----------|--------|--------|--------|---------------|-------------|---------|-------|")

    for c in report.checks:
        error_short = c.error[:60] + "..." if len(c.error) > 60 else c.error
        lines.append(
            f"| {c.method} | {c.category} | {c.sample_symbol} | {c.status} | {c.vendor} "
            f"| {c.fallback_from or '-'} | {c.latency_ms:.0f} | {c.record_count} | {error_short or '-'} |"
        )
    lines.append("")

    fallback_checks = [c for c in report.checks if c.fallback_from]
    if fallback_checks:
        lines.append("## Fallback Events")
        lines.append("")
        for c in fallback_checks:
            lines.append(f"- **{c.method}**: {c.fallback_from} → {c.vendor}")
        lines.append("")

    failed_checks = [c for c in report.checks if c.status == STATUS_FAILED]
    if failed_checks:
        lines.append("## Failed Endpoints")
        lines.append("")
        for c in failed_checks:
            lines.append(f"- **{c.method}** ({c.category}): {c.error}")
        lines.append("")

    stale_checks = [c for c in report.checks if c.status == STATUS_STALE]
    if stale_checks:
        lines.append("## Stale Responses")
        lines.append("")
        for c in stale_checks:
            lines.append(f"- **{c.method}** ({c.category}): {c.stale_reason}")
        lines.append("")

    lines.append("---")
    lines.append(f"*Generated by health_check.py — [M-008] data_source_health*")
    lines.append("")

    return "\n".join(lines)


# ── File output ────────────────────────────────────────────────────────

def save_report(
    report: HealthReport,
    output_dir: str = "docs/data_source_health",
) -> str:
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"{report.date}.md")
    md = render_report(report)
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    return path
