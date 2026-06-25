# [DATA-019] live_source_sampling
"""Small-sample live data source sampling health daily report.

Builds on DATA-018's freshness report by adding **live small-sample probing**:
each day we pick 3-5 representative stocks (one large-cap, one small/mid-cap,
one recent TradeFlow candidate, one user watchlist) and verify that every key
data type (quote, fund_flow, lhb, notice, rating, buyback, report) can
actually return data for *real* stocks — not just "the interface looks alive".

This catches the failure mode where an endpoint returns 200 OK with empty
results, or a vendor is registered but silently drops a particular stock.

6-state classification (reused from DATA-018 ``SourceFreshnessStatus``):

  - HAS_DATA          — successful retrieval with verified data
  - NORMAL_NO_DATA    — queried successfully, stock has no data (normal)
  - STALE             — data older than threshold (e.g. > 7 days)
  - FAILED            — API failure / parse error / timeout
  - RATE_LIMITED      — 429 / rate-limit triggered
  - UNIT_UNVERIFIED   — data present but unit not verified

**Gating** — all live network calls require ``TA_LIVE_DATA_SMOKE=1`` in the
environment.  Without it, results are marked ``SKIPPED`` and the report still
renders (useful for CI / fixture replay).

Usage (library):
    from tradingagents.dataflows.live_source_sampling import (
        run_live_sampling,
        render_live_sampling_report,
        save_live_sampling_report,
    )
    report = run_live_sampling()
    print(render_live_sampling_report(report))
    save_live_sampling_report(report)

Usage (CLI):
    TA_LIVE_DATA_SMOKE=1 python scripts/run_live_sampling.py
    TA_LIVE_DATA_SMOKE=1 python scripts/run_live_sampling.py --symbols 600519.SH,002415.SZ

Output:
    docs/data_source_reports/YYYY-MM-DD-live-smoke.md
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from .source_freshness_report import (
    SourceFreshnessStatus,
    _detect_failed,
    _detect_normal_no_data,
    _detect_rate_limited,
    _is_stale_date,
)
from .source_catalog import get_fallback_chain, get_primary_source


# ── Config ────────────────────────────────────────────────────────────

_LIVE_ENV = "TA_LIVE_DATA_SMOKE"
_MIN_ENDPOINT_INTERVAL = 0.6  # seconds between endpoint calls (rate-limit safety)
_MAX_SYMBOLS = 5

# Default sample universe: one large-cap, one small/mid-cap,
# one recent TradeFlow candidate, one user watchlist.
# These are stable, liquid names that should always have data.
DEFAULT_SAMPLE_UNIVERSE: List[Dict[str, str]] = [
    {
        "symbol": "600519.SH",
        "name": "贵州茅台",
        "category": "large_cap",
        "category_cn": "大票",
    },
    {
        "symbol": "002415.SZ",
        "name": "海康威视",
        "category": "small_mid_cap",
        "category_cn": "中小票",
    },
    {
        "symbol": "603629.SH",
        "name": "苏利股份",
        "category": "recent_candidate",
        "category_cn": "近期候选",
    },
    {
        "symbol": "000001.SZ",
        "name": "平安银行",
        "category": "watchlist",
        "category_cn": "自选",
    },
]


# ── Data Models ───────────────────────────────────────────────────────


@dataclass
class SampleStock:
    """A stock in the sample universe."""

    symbol: str = ""
    name: str = ""
    category: str = ""  # large_cap / small_mid_cap / recent_candidate / watchlist
    category_cn: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "category": self.category,
            "category_cn": self.category_cn,
        }


@dataclass
class SampleResult:
    """Result of probing one (data_type, symbol) pair."""

    data_type: str = ""
    data_type_label: str = ""
    symbol: str = ""
    symbol_name: str = ""
    category: str = ""
    status: str = SourceFreshnessStatus.FAILED
    actual_vendor: str = ""
    primary_vendor: str = ""
    fallback_vendor: str = ""
    is_fallback: bool = False
    latency_ms: float = 0.0
    record_count: int = 0
    unit: str = ""
    unit_verified: bool = False
    as_of: str = ""
    error: str = ""
    sample_snippet: str = ""
    diagnosis: str = ""
    rate_limit_risk: str = ""

    @property
    def traffic_light(self) -> str:
        return SourceFreshnessStatus.TRAFFIC_LIGHT.get(self.status, "red")

    @property
    def label_cn(self) -> str:
        return SourceFreshnessStatus.LABEL_CN.get(self.status, "未知")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "data_type": self.data_type,
            "data_type_label": self.data_type_label,
            "symbol": self.symbol,
            "symbol_name": self.symbol_name,
            "category": self.category,
            "status": self.status,
            "status_label_cn": self.label_cn,
            "traffic_light": self.traffic_light,
            "actual_vendor": self.actual_vendor,
            "primary_vendor": self.primary_vendor,
            "fallback_vendor": self.fallback_vendor,
            "is_fallback": self.is_fallback,
            "latency_ms": round(self.latency_ms, 1),
            "record_count": self.record_count,
            "unit": self.unit,
            "unit_verified": self.unit_verified,
            "as_of": self.as_of,
            "error": self.error[:200] if self.error else "",
            "sample_snippet": self.sample_snippet[:150],
            "diagnosis": self.diagnosis,
            "rate_limit_risk": self.rate_limit_risk,
        }


@dataclass
class LiveSamplingReport:
    """Top-level live sampling report."""

    report_date: str = ""
    generated_at: str = ""
    samples: List[SampleStock] = field(default_factory=list)
    results: List[SampleResult] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)
    env_gated: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "report_date": self.report_date,
            "generated_at": self.generated_at,
            "samples": [s.to_dict() for s in self.samples],
            "results": [r.to_dict() for r in self.results],
            "summary": self.summary,
            "env_gated": self.env_gated,
        }


# ── Data type label mapping (subset of DATA-018 labels) ───────────────

_DATA_TYPE_LABELS: Dict[str, str] = {
    "quote": "实时行情",
    "fund_flow": "个股资金流",
    "lhb": "龙虎榜",
    "notice": "公告",
    "rating": "评级",
    "buyback": "回购",
    "report": "研报",
}


# ── Data type → provider method definitions ───────────────────────────
#
# Each definition maps a data_type to:
#   - method: the CnAstockProvider method name
#   - args_template: how to build call args from (symbol, date)
#   - kwargs: extra kwargs
#   - expected_unit: the unit we expect to see in a healthy response
#   - special_note: optional note for fund_flow/lhb semantics


def _make_data_type_definitions() -> List[Dict[str, Any]]:
    return [
        {
            "data_type": "quote",
            "method": "get_realtime_quotes",
            "args_template": (["{symbol}"],),
            "kwargs": {},
            "expected_unit": "元",
            "special_note": "",
        },
        {
            "data_type": "fund_flow",
            "method": "get_individual_fund_flow",
            "args_template": ("{symbol}",),
            "kwargs": {},
            "expected_unit": "万元",
            "special_note": "主力资金一般应可取；失败需 fallback 到 cn_astock/Eastmoney push2his",
        },
        {
            "data_type": "lhb",
            "method": "get_lhb_detail",
            "args_template": ("{symbol}", "{date}"),
            "kwargs": {"force": True},
            "expected_unit": "万元",
            "special_note": "龙虎榜无触发是 NORMAL_NO_DATA（非异动日属正常），不是失败",
        },
        {
            "data_type": "notice",
            "method": "get_announcements",
            "args_template": ("{symbol}",),
            "kwargs": {},
            "expected_unit": "条",
            "special_note": "",
        },
        {
            "data_type": "rating",
            "method": "get_ratings",
            "args_template": ("{symbol}",),
            "kwargs": {},
            "expected_unit": "条",
            "special_note": "",
        },
        {
            "data_type": "buyback",
            "method": "get_buybacks",
            "args_template": ("{symbol}",),
            "kwargs": {},
            "expected_unit": "万元",
            "special_note": "无回购计划是 NORMAL_NO_DATA，不是失败",
        },
        {
            "data_type": "report",
            "method": "get_research_report",
            "args_template": ("{symbol}",),
            "kwargs": {},
            "expected_unit": "条",
            "special_note": "",
        },
    ]


def _resolve_args(template: tuple, symbol: str, date: str) -> tuple:
    """Replace {symbol} / {date} placeholders in an args template."""
    result = []
    for arg in template:
        if isinstance(arg, str):
            arg = arg.replace("{symbol}", symbol).replace("{date}", date)
            result.append(arg)
        elif isinstance(arg, list):
            result.append(
                [a.replace("{symbol}", symbol).replace("{date}", date) for a in arg]
            )
        else:
            result.append(arg)
    return tuple(result)


# ── Fetch function type ───────────────────────────────────────────────

# A fetch_fn takes (method_name, args_tuple, kwargs_dict) and returns
# a string result (or raises an exception).
FetchFn = Callable[..., str]


def _default_fetch_fn(method: str, args: tuple, kwargs: dict) -> str:
    """Default fetch using route_to_vendor (full fallback chain).

    Only called when TA_LIVE_DATA_SMOKE=1 is set.
    """
    from .interface import route_to_vendor  # local import to avoid cycle

    return route_to_vendor(method, *args, **kwargs)


def _get_last_hit_vendor(method: str) -> str:
    """Get the vendor that actually served the last call for this method."""
    try:
        from .interface import get_last_hit_vendor

        return get_last_hit_vendor(method) or ""
    except Exception:
        return ""


# ── Classification ────────────────────────────────────────────────────


def classify_sample_result(
    raw: Any,
    error: str = "",
    as_of: str = "",
    unit_verified: Optional[bool] = None,
    data_type: str = "",
    today: Optional[str] = None,
) -> str:
    """Classify a sample probe into one of the 6 states.

    Delegates to DATA-018's ``classify_source_status`` but adds data-type
    specific overrides (e.g. LHB NORMAL_NO_DATA patterns).
    """
    from .source_freshness_report import classify_source_status

    # If we have an explicit LHB normal-no-data marker, honor it.
    if isinstance(raw, str):
        s = raw.strip()
        if data_type == "lhb" and "LHB_NORMAL_NO_DATA" in s:
            return SourceFreshnessStatus.NORMAL_NO_DATA
        if data_type == "buyback" and "BUYBACK_NORMAL_NO_DATA" in s:
            return SourceFreshnessStatus.NORMAL_NO_DATA

    status_hint = ""
    if isinstance(raw, str) and "LHB_NORMAL_NO_DATA" in raw:
        status_hint = "NORMAL_NO_DATA"
    if isinstance(raw, str) and "BUYBACK_NORMAL_NO_DATA" in raw:
        status_hint = "NORMAL_NO_DATA"

    return classify_source_status(
        raw_value=raw,
        status=status_hint,
        error=error,
        as_of=as_of,
        unit_verified=unit_verified,
        today=today,
    )


# ── Unit detection helpers ────────────────────────────────────────────

_WAN_YUAN_MARKERS = ["单位：万元", "单位:万元", "（万元）", "(万元)"]
_YUAN_MARKERS = ["单位：元", "单位:元"]
_TIAO_MARKERS = ["条", "total"]


def _detect_unit_from_text(text: str) -> Tuple[str, bool]:
    """Detect unit from response text. Returns (unit, unit_verified)."""
    if not text:
        return ("", False)
    for marker in _WAN_YUAN_MARKERS:
        if marker in text:
            return ("万元", True)
    for marker in _YUAN_MARKERS:
        if marker in text:
            return ("元", True)
    return ("", False)


def _count_data_lines(text: str) -> int:
    """Count non-header data lines in a text response."""
    if not text:
        return 0
    lines = [
        l
        for l in text.strip().split("\n")
        if l.strip()
        and not l.strip().startswith("#")
        and not l.strip().startswith("##")
        and not l.strip().startswith("---")
    ]
    # Subtract 1-2 for header rows
    return max(0, len(lines) - 2) if len(lines) > 2 else max(0, len(lines) - 1)


def _extract_as_of(text: str) -> str:
    """Try to extract a date string (YYYY-MM-DD) from response text."""
    if not text:
        return ""
    import re

    match = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    return match.group(1) if match else ""


# ── Core sampling logic ───────────────────────────────────────────────


def _sample_single(
    fetch_fn: FetchFn,
    dt_def: Dict[str, Any],
    stock: SampleStock,
    date_str: str,
) -> SampleResult:
    """Probe a single (data_type, symbol) pair.

    Args:
        fetch_fn: callable that takes (method, args, kwargs) → str
        dt_def: data type definition dict
        stock: the sample stock
        date_str: today's date string
    """
    data_type = dt_def["data_type"]
    result = SampleResult(
        data_type=data_type,
        data_type_label=_DATA_TYPE_LABELS.get(data_type, data_type),
        symbol=stock.symbol,
        symbol_name=stock.name,
        category=stock.category,
    )

    # Fill catalog info
    primary = get_primary_source(data_type)
    if primary:
        result.primary_vendor = primary.vendor
        chain = get_fallback_chain(data_type)
        result.fallback_vendor = chain[1] if len(chain) > 1 else ""
        result.unit = primary.unit or ""
        result.rate_limit_risk = (
            primary.rate_limit_risk.value
            if hasattr(primary.rate_limit_risk, "value")
            else str(primary.rate_limit_risk)
        )

    # Rate-limit safety pause
    time.sleep(_MIN_ENDPOINT_INTERVAL)

    method = dt_def["method"]
    args = _resolve_args(dt_def["args_template"], stock.symbol, date_str)
    kwargs = dict(dt_def.get("kwargs", {}))

    t0 = time.monotonic()
    try:
        raw = fetch_fn(method, args, kwargs)
        elapsed = (time.monotonic() - t0) * 1000
        result.latency_ms = elapsed

        if raw is None:
            result.status = SourceFreshnessStatus.FAILED
            result.error = "null_response"
            result.diagnosis = _build_sample_diagnosis(result, dt_def)
            return result

        raw_str = str(raw)
        result.sample_snippet = raw_str[:150]

        # Detect unit from text
        unit, unit_verified = _detect_unit_from_text(raw_str)
        if unit:
            result.unit = unit
            result.unit_verified = unit_verified
        else:
            # For dict/JSON responses (e.g. quote), unit verification is N/A
            result.unit_verified = True

        # Extract as_of date
        as_of = _extract_as_of(raw_str)
        result.as_of = as_of

        # Count records
        result.record_count = _count_data_lines(raw_str)

        # Classify
        result.status = classify_sample_result(
            raw=raw_str,
            error="",
            as_of=as_of,
            unit_verified=result.unit_verified if result.unit_verified else None,
            data_type=data_type,
            today=date_str,
        )

        # Determine actual vendor (only meaningful for live calls)
        result.actual_vendor = _get_last_hit_vendor(method)
        if result.actual_vendor and result.primary_vendor and result.actual_vendor != result.primary_vendor:
            result.is_fallback = True

        # If classified as FAILED but we have failure patterns in text, set error
        if result.status == SourceFreshnessStatus.FAILED and not result.error:
            result.error = "failure_string_detected"

    except Exception as exc:
        elapsed = (time.monotonic() - t0) * 1000
        result.latency_ms = elapsed
        err_str = f"{type(exc).__name__}: {exc}"
        result.error = err_str[:200]

        if _detect_rate_limited(err_str):
            result.status = SourceFreshnessStatus.RATE_LIMITED
        else:
            result.status = SourceFreshnessStatus.FAILED

    result.diagnosis = _build_sample_diagnosis(result, dt_def)
    return result


def _build_sample_diagnosis(result: SampleResult, dt_def: Dict[str, Any]) -> str:
    """Build human-readable diagnosis for a sample result."""
    special_note = dt_def.get("special_note", "")
    note_suffix = f"（注意：{special_note}）" if special_note else ""

    status = result.status
    if status == SourceFreshnessStatus.HAS_DATA:
        vendor_info = result.actual_vendor or result.primary_vendor or "未知"
        fb_info = "，fallback" if result.is_fallback else ""
        return (
            f"{result.data_type_label}正常（vendor={vendor_info}"
            f"，records={result.record_count}{fb_info}）{note_suffix}"
        )
    elif status == SourceFreshnessStatus.NORMAL_NO_DATA:
        return f"{result.data_type_label}查询成功但无数据（正常情况）{note_suffix}"
    elif status == SourceFreshnessStatus.STALE:
        return f"{result.data_type_label}数据过期（as_of={result.as_of or '未知'}）{note_suffix}"
    elif status == SourceFreshnessStatus.FAILED:
        return f"{result.data_type_label}接口失败: {result.error or '未知错误'}{note_suffix}"
    elif status == SourceFreshnessStatus.RATE_LIMITED:
        return f"{result.data_type_label}触发限流{note_suffix}"
    elif status == SourceFreshnessStatus.UNIT_UNVERIFIED:
        return f"{result.data_type_label}有数据但单位未校验{note_suffix}"
    return f"{result.data_type_label}状态未知{note_suffix}"


# ── Report generation ─────────────────────────────────────────────────


def build_sample_universe(
    custom_stocks: Optional[List[Dict[str, str]]] = None,
) -> List[SampleStock]:
    """Build the sample universe from defaults or custom overrides.

    Args:
        custom_stocks: optional list of dicts with keys symbol/name/category/category_cn.
                       If provided, replaces the default universe (capped at _MAX_SYMBOLS).
    """
    source = custom_stocks if custom_stocks is not None else DEFAULT_SAMPLE_UNIVERSE
    stocks = []
    for s in source[:_MAX_SYMBOLS]:
        stocks.append(
            SampleStock(
                symbol=s.get("symbol", ""),
                name=s.get("name", ""),
                category=s.get("category", ""),
                category_cn=s.get("category_cn", ""),
            )
        )
    return stocks


def run_live_sampling(
    samples: Optional[List[SampleStock]] = None,
    data_types: Optional[List[Dict[str, Any]]] = None,
    fetch_fn: Optional[FetchFn] = None,
    date_str: Optional[str] = None,
) -> LiveSamplingReport:
    """Run live sampling across the sample universe.

    Args:
        samples: list of SampleStock; defaults to DEFAULT_SAMPLE_UNIVERSE
        data_types: list of data_type definitions; defaults to all key types
        fetch_fn: callable (method, args, kwargs) → str; defaults to route_to_vendor.
                  Only invoked when TA_LIVE_DATA_SMOKE=1.
        date_str: override the date string; defaults to today

    Returns:
        LiveSamplingReport with results for each (stock, data_type) pair.
    """
    now = datetime.now()
    if date_str is None:
        date_str = now.strftime("%Y-%m-%d")

    report = LiveSamplingReport(
        report_date=date_str,
        generated_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        env_gated=False,
    )

    if samples is None:
        samples = build_sample_universe()
    report.samples = samples

    if data_types is None:
        data_types = _make_data_type_definitions()

    # Env gate check
    if os.getenv(_LIVE_ENV, "").strip() != "1":
        report.env_gated = True
        for stock in samples:
            for dt_def in data_types:
                result = SampleResult(
                    data_type=dt_def["data_type"],
                    data_type_label=_DATA_TYPE_LABELS.get(dt_def["data_type"], dt_def["data_type"]),
                    symbol=stock.symbol,
                    symbol_name=stock.name,
                    category=stock.category,
                    status="SKIPPED",
                )
                result.diagnosis = "未开启 live smoke（设置 TA_LIVE_DATA_SMOKE=1 启用）"
                report.results.append(result)
        report.summary = _compute_summary(report.results)
        return report

    # Live mode
    report.env_gated = False
    if fetch_fn is None:
        fetch_fn = _default_fetch_fn

    for stock in samples:
        for dt_def in data_types:
            result = _sample_single(fetch_fn, dt_def, stock, date_str)
            report.results.append(result)

    report.summary = _compute_summary(report.results)
    return report


def _compute_summary(results: List[SampleResult]) -> Dict[str, Any]:
    """Compute summary statistics for the report."""
    status_counts: Dict[str, int] = {s: 0 for s in SourceFreshnessStatus.ALL}
    status_counts["SKIPPED"] = 0

    for r in results:
        status_counts[r.status] = status_counts.get(r.status, 0) + 1

    total = len(results)
    green = status_counts.get(SourceFreshnessStatus.HAS_DATA, 0) + status_counts.get(
        SourceFreshnessStatus.NORMAL_NO_DATA, 0
    )
    yellow = status_counts.get(SourceFreshnessStatus.STALE, 0) + status_counts.get(
        SourceFreshnessStatus.UNIT_UNVERIFIED, 0
    )
    red = status_counts.get(SourceFreshnessStatus.FAILED, 0) + status_counts.get(
        SourceFreshnessStatus.RATE_LIMITED, 0
    )
    skipped = status_counts.get("SKIPPED", 0)

    # [DATA-019A] live_source_sampling_skip_status
    # skipped-only scenario: every check was SKIPPED (no actual PASS/FAIL).
    # In this case the report must NOT claim ALL GREEN — nothing was executed.
    skipped_only = total > 0 and skipped == total

    fallback_count = sum(1 for r in results if r.is_fallback)

    # Per-data-type summary
    by_data_type: Dict[str, Dict[str, Any]] = {}
    for r in results:
        dt = r.data_type
        dt_stat = by_data_type.setdefault(
            dt,
            {
                "total": 0,
                "green": 0,
                "yellow": 0,
                "red": 0,
                "skipped": 0,
                "label": r.data_type_label,
            },
        )
        dt_stat["total"] += 1
        if r.status in (SourceFreshnessStatus.HAS_DATA, SourceFreshnessStatus.NORMAL_NO_DATA):
            dt_stat["green"] += 1
        elif r.status in (SourceFreshnessStatus.STALE, SourceFreshnessStatus.UNIT_UNVERIFIED):
            dt_stat["yellow"] += 1
        elif r.status in (SourceFreshnessStatus.FAILED, SourceFreshnessStatus.RATE_LIMITED):
            dt_stat["red"] += 1
        elif r.status == "SKIPPED":
            dt_stat["skipped"] += 1

    return {
        "total_checks": total,
        "status_counts": status_counts,
        "green_count": green,
        "yellow_count": yellow,
        "red_count": red,
        "skipped_count": skipped,
        # [DATA-019A] live_source_sampling_skip_status
        "skipped_only": skipped_only,
        "fallback_triggered_count": fallback_count,
        # all_green requires at least one actually-executed check
        # (i.e. not skipped-only) AND no red/yellow.
        "all_green": (not skipped_only) and red == 0 and yellow == 0,
        "has_failures": red > 0,
        "has_warnings": yellow > 0,
        "by_data_type": by_data_type,
        "overall_status": (
            # [DATA-019A] live_source_sampling_skip_status
            "skipped"
            if skipped_only
            else (
                "all_green"
                if red == 0 and yellow == 0
                else ("has_failures" if red > 0 else "has_warnings")
            )
        ),
    }


# ── Markdown rendering ────────────────────────────────────────────────


def render_live_sampling_report(report: LiveSamplingReport) -> str:
    """Render the live sampling report as Markdown."""
    lines: List[str] = []
    lines.append("# Live Source Sampling Health Report")
    lines.append("")
    lines.append(f"- **Date**: {report.report_date}")
    lines.append(f"- **Generated at**: {report.generated_at}")
    lines.append(
        f"- **Env gated**: {'Yes (TA_LIVE_DATA_SMOKE not set)' if report.env_gated else 'No (live calls executed)'}"
    )
    lines.append("")

    # Sample universe
    lines.append("## Sample Universe")
    lines.append("")
    lines.append("| Symbol | Name | Category |")
    lines.append("|--------|------|----------|")
    for s in report.samples:
        lines.append(f"| {s.symbol} | {s.name} | {s.category_cn or s.category} |")
    lines.append("")

    # Summary
    s = report.summary
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- **Total checks**: {s.get('total_checks', 0)}")
    lines.append(f"- **Green (OK)**: {s.get('green_count', 0)}")
    lines.append(f"- **Yellow (Warning)**: {s.get('yellow_count', 0)}")
    lines.append(f"- **Red (Failed)**: {s.get('red_count', 0)}")
    lines.append(f"- **Skipped**: {s.get('skipped_count', 0)}")
    lines.append(f"- **Fallback triggered**: {s.get('fallback_triggered_count', 0)}")
    overall = s.get("overall_status", "unknown")
    overall_label = {
        "all_green": "ALL GREEN",
        "has_failures": "HAS FAILURES",
        "has_warnings": "HAS WARNINGS",
        # [DATA-019A] live_source_sampling_skip_status
        "skipped": "未执行实盘抽样（等待启用 live smoke）",
    }.get(overall, overall)
    lines.append(f"- **Overall**: {overall_label}")
    # [DATA-019A] live_source_sampling_skip_status — explicit skipped-only banner
    if s.get("skipped_only"):
        lines.append("")
        lines.append(
            "> ⚠️ 本次报告所有检查均为 SKIPPED（未开启 `TA_LIVE_DATA_SMOKE=1`），"
            "并未实际执行实盘抽样，不能视为全部通过/全绿。"
        )
    lines.append("")

    # Per-data-type summary
    by_dt = s.get("by_data_type", {})
    if by_dt:
        lines.append("## Per-Data-Type Summary")
        lines.append("")
        lines.append("| Data Type | Total | Green | Yellow | Red | Skipped |")
        lines.append("|-----------|-------|-------|--------|-----|---------|")
        for dt, dt_stat in by_dt.items():
            lines.append(
                f"| {dt_stat.get('label', dt)} | {dt_stat['total']} | "
                f"{dt_stat['green']} | {dt_stat['yellow']} | "
                f"{dt_stat['red']} | {dt_stat['skipped']} |"
            )
        lines.append("")

    # Main results table
    lines.append("## Sampling Results")
    lines.append("")
    lines.append(
        "| Data Type | Symbol | Status | Vendor | Fallback | Latency (ms) | "
        "Records | Unit | As Of | Error |"
    )
    lines.append(
        "|-----------|--------|--------|--------|----------|-------------|"
        "---------|------|-------|-------|"
    )
    for r in report.results:
        vendor_info = r.actual_vendor or r.primary_vendor or "-"
        fb_info = "Yes" if r.is_fallback else "-"
        unit_str = r.unit if r.unit else "-"
        if r.unit_verified:
            unit_str += " ✓"
        as_of_str = r.as_of or "-"
        error_str = (r.error[:40] + "...") if len(r.error) > 40 else (r.error or "-")
        lines.append(
            f"| {r.data_type_label} | {r.symbol} | {r.label_cn} ({r.status}) | "
            f"{vendor_info} | {fb_info} | {r.latency_ms:.0f} | "
            f"{r.record_count} | {unit_str} | {as_of_str} | {error_str} |"
        )
    lines.append("")

    # Issues & warnings
    problem_results = [
        r
        for r in report.results
        if r.status
        not in (
            SourceFreshnessStatus.HAS_DATA,
            SourceFreshnessStatus.NORMAL_NO_DATA,
            "SKIPPED",
        )
    ]
    if problem_results:
        lines.append("## Issues & Warnings")
        lines.append("")
        for r in problem_results:
            icon = "RED" if r.traffic_light == "red" else "YELLOW"
            lines.append(f"### {icon}: {r.data_type_label} — {r.symbol} ({r.symbol_name})")
            lines.append(f"- **Status**: `{r.status}`")
            lines.append(f"- **Primary vendor**: {r.primary_vendor or 'N/A'}")
            lines.append(f"- **Actual vendor**: {r.actual_vendor or 'N/A'}")
            if r.error:
                lines.append(f"- **Error**: {r.error}")
            lines.append(f"- **Diagnosis**: {r.diagnosis}")
            lines.append("")

    # Fund flow & LHB special notes
    lines.append("## Special Notes: Fund Flow & LHB")
    lines.append("")
    lines.append(
        "- **主力资金 (fund_flow)**: 一般应可取。如果 FAILED，说明 fallback 链路断裂，"
        "需检查 cn_astock/Eastmoney push2his 是否可用。"
    )
    lines.append(
        "- **龙虎榜 (lhb)**: 非异动日无龙虎榜是 NORMAL_NO_DATA（正常），"
        "不是接口失败。只有 FAILED 才需要告警。"
    )
    lines.append("")

    lines.append("---")
    lines.append("*Generated by live_source_sampling.py — [DATA-019] live_source_sampling*")
    lines.append("")

    return "\n".join(lines)


# ── File I/O ──────────────────────────────────────────────────────────


def save_live_sampling_report(
    report: LiveSamplingReport,
    output_dir: str = "docs/data_source_reports",
    filename: Optional[str] = None,
) -> str:
    """Save report as Markdown. Returns the file path.

    Output filename format: ``YYYY-MM-DD-live-smoke.md``
    (per DATA-019 task spec).

    Also writes a JSON sidecar (``YYYY-MM-DD-live-smoke.json``) carrying the
    structured report so that the DATA-020 frontend/API can render
    skipped / failed / fallback layers without re-parsing Markdown.
    """
    os.makedirs(output_dir, exist_ok=True)

    if filename is None:
        filename = f"{report.report_date}-live-smoke.md"

    filepath = os.path.join(output_dir, filename)
    content = render_live_sampling_report(report)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    # [DATA-020] live_sampling_health_ui — JSON sidecar for structured access.
    json_filename = filename
    if json_filename.endswith(".md"):
        json_filename = json_filename[:-3] + ".json"
    else:
        json_filename = f"{report.report_date}-live-smoke.json"
    json_filepath = os.path.join(output_dir, json_filename)
    try:
        with open(json_filepath, "w", encoding="utf-8") as f:
            json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
    except Exception:
        # JSON sidecar is best-effort; never break Markdown save.
        pass

    return filepath


def find_latest_live_sampling_report(
    reports_dir: str = "docs/data_source_reports",
) -> Optional[str]:
    """Find the latest live-sampling report file."""
    if not os.path.isdir(reports_dir):
        return None

    files = []
    for f in os.listdir(reports_dir):
        if not f.endswith("-live-smoke.md"):
            continue
        date_part = f.replace("-live-smoke.md", "")
        files.append((date_part, os.path.join(reports_dir, f)))

    if not files:
        return None

    files.sort(key=lambda x: x[0], reverse=True)
    return files[0][1]


# [DATA-020] live_sampling_health_ui
def find_latest_live_sampling_json(
    reports_dir: str = "docs/data_source_reports",
) -> Optional[str]:
    """Find the latest live-sampling JSON sidecar file."""
    if not os.path.isdir(reports_dir):
        return None

    files = []
    for f in os.listdir(reports_dir):
        if not f.endswith("-live-smoke.json"):
            continue
        date_part = f.replace("-live-smoke.json", "")
        files.append((date_part, os.path.join(reports_dir, f)))

    if not files:
        return None

    files.sort(key=lambda x: x[0], reverse=True)
    return files[0][1]


def load_latest_live_sampling_report(
    reports_dir: str = "docs/data_source_reports",
) -> Optional[Dict[str, Any]]:
    """Load the latest live-sampling report as a structured dict.

    Reads the JSON sidecar produced by :func:`save_live_sampling_report`.
    Returns ``None`` when no report exists or the sidecar is unreadable.
    Never raises — callers (API/UI) rely on a graceful ``no_data`` response.
    """
    json_path = find_latest_live_sampling_json(reports_dir)
    if not json_path:
        return None
    try:
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        return data
    except Exception:
        return None


# ── Nightly report integration ────────────────────────────────────────


def build_sampling_section_for_nightly_report(
    reports_dir: str = "docs/data_source_reports",
) -> str:
    """Build a sampling section for the nightly auto-dev report.

    Tries to find the latest saved report; if none exists, returns a stub.
    """
    report_path = find_latest_live_sampling_report(reports_dir)
    if report_path:
        try:
            with open(report_path, encoding="utf-8") as f:
                return f.read()
        except Exception:
            pass

    # No report available — return stub
    lines = [
        "## 数据源实盘抽样健康日报",
        "",
        "- **状态**: 未生成（请运行 `TA_LIVE_DATA_SMOKE=1 python scripts/run_live_sampling.py`）",
        "",
        "---",
        "*Generated by live_source_sampling.py — [DATA-019]*",
        "",
    ]
    return "\n".join(lines)


# ── Env gate helper ───────────────────────────────────────────────────


def is_live_sampling_enabled() -> bool:
    """Check if live sampling is enabled (TA_LIVE_DATA_SMOKE=1)."""
    return os.getenv(_LIVE_ENV, "").strip() == "1"


# ── Sample fixtures (for testing) ─────────────────────────────────────
# Representative fixture responses for testing classification logic.
# These mirror real provider output formats without hitting live APIs.

_SAMPLE_RESPONSES: Dict[str, Dict[str, str]] = {
    # quote responses (JSON-like)
    "quote_ok": '{"600519.SH": {"price": 1800.0, "open": 1790.0, "volume": 12345678, "amount": 22222222222, "source": "tencent"}}',
    "quote_empty": "{}",
    # fund_flow responses
    "fund_flow_ok": (
        "600519.SH 近20日主力资金净流向（Eastmoney push2his，单位：万元）：\n"
        "日期 | 主力净流入 | 小单净流入 | 中单净流入 | 大单净流入 | 超大单净流入\n"
        "2026-06-17 | 1234.56 | -500.00 | 200.00 | 600.00 | 434.56\n"
        "2026-06-16 | -800.00 | 300.00 | -100.00 | -400.00 | -300.00\n"
    ),
    "fund_flow_akshare_fail": "个股资金流向数据获取失败：ProxyError: Cannot connect to proxy.",
    "fund_flow_astock_fallback": (
        "600519.SH 近20日主力资金净流向（Eastmoney push2his，单位：万元）：\n"
        "日期 | 主力净流入 | 小单净流入\n"
        "2026-06-17 | 1234.56 | -500.00\n"
    ),
    # lhb responses
    "lhb_normal_no_data": (
        "600519.SH [G-007] LHB_NORMAL_NO_DATA: 在 2026-06-18 无龙虎榜数据（非异动日属正常）。"
    ),
    "lhb_has_data": (
        "600519.SH [G-007] LHB_HAS_DATA: 龙虎榜明细（2026-06-18，Eastmoney datacenter）：\n"
        "- 涨幅偏离 | 净买 1234.5万 | 买入 2000.0万 | 卖出 765.5万\n"
    ),
    "lhb_failed": "龙虎榜数据获取失败：ConnectionError",
    # notice responses
    "notice_ok": (
        "## Announcements for 600519.SH (3 total)\n\n"
        "- 2026-06-17 | 定期报告 | 贵州茅台2025年年度报告\n"
        "- 2026-06-15 | 股东大会 | 关于召开2025年年度股东大会的通知\n"
    ),
    "notice_empty": "## Announcements for 002415.SZ (0 total)",
    # rating responses
    "rating_ok": (
        "## Ratings for 600519.SH (2 total)\n"
        "- 2026-06-17 | 买入 | 目标价 2000.0 | 中信证券\n"
        "- 2026-06-10 | 增持 | 目标价 1950.0 | 国泰君安\n"
    ),
    # buyback responses
    "buyback_normal_no_data": "002415.SZ [DATA-013] BUYBACK_NORMAL_NO_DATA: 该股无回购计划或进展数据。",
    "buyback_has_data": (
        "002415.SZ [DATA-013] BUYBACK_HAS_DATA: 回购数据（Eastmoney datacenter）：\n"
        "- 2026-06-01 | 回购金额 5000.0万 | 进度 实施中\n"
    ),
    # report responses
    "report_ok": (
        "## Research Reports for 600519.SH (5 total)\n"
        "- 2026-06-17 | 中信证券 | 买入 | 茅台量价齐升\n"
        "- 2026-06-15 | 国泰君安 | 增持 | 高端酒持续发力\n"
    ),
    "report_empty": "## Research Reports for 603629.SH (0 total)",
    # stale
    "fund_flow_stale": (
        "600519.SH 近20日主力资金净流向（Eastmoney push2his，单位：万元）：\n"
        "日期 | 主力净流入 | 小单净流入\n"
        "2026-05-01 | 1234.56 | -500.00\n"
    ),
    # rate limited
    "rate_limited": "HTTPError 429: Too Many Requests — 请求过于频繁",
}

ALL_SAMPLE_RESPONSES = _SAMPLE_RESPONSES
