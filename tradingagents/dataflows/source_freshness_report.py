# [DATA-018] source_freshness_report
"""A-share key data source freshness & fallback visualization daily report.

Extends DATA-017 fund_flow/LHB health inspection to ALL key sources
(quote, ohlcv, fund_flow, lhb, margin_trading, notice, report, rating,
news, global_news, financials, insider, zt_pool, hot_stocks, buyback).

Provides 6-state classification:

  - HAS_DATA          — successful retrieval with verified data
  - NORMAL_NO_DATA    — queried successfully, stock has no data (normal)
  - STALE             — data older than threshold (e.g. > 7 days)
  - FAILED            — API failure / parse error / timeout
  - RATE_LIMITED      — 429 / rate-limit triggered
  - UNIT_UNVERIFIED   — data present but unit not verified

Usage (library):
    from tradingagents.dataflows.source_freshness_report import (
        SourceFreshnessStatus,
        SourceFreshnessEntry,
        run_source_freshness_report,
        render_source_freshness_report,
        save_source_freshness_report,
    )
    report = run_source_freshness_report(raw_evidence)
    print(render_source_freshness_report(report))
    save_source_freshness_report(report, "docs/data_source_reports")
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .source_catalog import (
    DataType,
    Freshness,
    get_all_data_types,
    get_fallback_chain,
    get_primary_source,
    get_sources_for_type,
)


# ── 6-State Health Classification ─────────────────────────────────────

class SourceFreshnessStatus:
    HAS_DATA = "HAS_DATA"
    NORMAL_NO_DATA = "NORMAL_NO_DATA"
    STALE = "STALE"
    FAILED = "FAILED"
    RATE_LIMITED = "RATE_LIMITED"
    UNIT_UNVERIFIED = "UNIT_UNVERIFIED"

    ALL = [HAS_DATA, NORMAL_NO_DATA, STALE, FAILED, RATE_LIMITED, UNIT_UNVERIFIED]

    # Traffic-light mapping for visualization
    TRAFFIC_LIGHT = {
        HAS_DATA: "green",
        NORMAL_NO_DATA: "green",
        STALE: "yellow",
        FAILED: "red",
        RATE_LIMITED: "red",
        UNIT_UNVERIFIED: "yellow",
    }

    # Chinese labels for display
    LABEL_CN = {
        HAS_DATA: "正常",
        NORMAL_NO_DATA: "正常无数据",
        STALE: "过期",
        FAILED: "故障",
        RATE_LIMITED: "限流",
        UNIT_UNVERIFIED: "单位未校验",
    }


# ── Rate-limit detection patterns ─────────────────────────────────────

_RATE_LIMIT_PATTERNS = [
    "429", "rate limit", "rate_limit", "too many requests",
    "请求过于频繁", "限流", "频繁访问",
]

_FAILURE_PATTERNS = [
    "获取失败", "ProxyError", "ConnectionError", "不可用",
    "TimeoutError", "Max retries exceeded", "Unable to connect",
    "SSLError", "Connection refused", "Read timed out",
    "HTTPError", "JSONDecodeError",
]

_NORMAL_NO_DATA_PATTERNS = [
    "NORMAL_NO_DATA", "无数据", "空", "暂无", "未查询到",
    "NOT_QUERIED", "查询未触发",
]

_RATE_LIMIT_RISK_TO_NOTE = {
    "low": "低风险",
    "medium": "中风险",
    "high": "高风险",
}


# ── Data Models ────────────────────────────────────────────────────────

@dataclass
class SourceFreshnessEntry:
    """Per-data-type source freshness entry."""
    data_type: str = ""
    label: str = ""
    status: str = SourceFreshnessStatus.FAILED
    primary_vendor: str = ""
    fallback_vendor: str = ""
    fallback_chain: List[str] = field(default_factory=list)
    actual_vendor: str = ""
    endpoint: str = ""
    as_of: str = ""
    latest_data_date: str = ""
    unit: str = ""
    unit_verified: bool = False
    is_fallback: bool = False
    record_count: int = 0
    error: str = ""
    rate_limit_risk: str = ""
    diagnosis: str = ""
    freshness_catalog: str = ""

    @property
    def traffic_light(self) -> str:
        return SourceFreshnessStatus.TRAFFIC_LIGHT.get(self.status, "red")

    @property
    def label_cn(self) -> str:
        return SourceFreshnessStatus.LABEL_CN.get(self.status, "未知")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "data_type": self.data_type,
            "label": self.label,
            "status": self.status,
            "status_label_cn": self.label_cn,
            "traffic_light": self.traffic_light,
            "primary_vendor": self.primary_vendor,
            "fallback_vendor": self.fallback_vendor,
            "fallback_chain": self.fallback_chain,
            "actual_vendor": self.actual_vendor,
            "endpoint": self.endpoint,
            "as_of": self.as_of,
            "latest_data_date": self.latest_data_date,
            "unit": self.unit,
            "unit_verified": self.unit_verified,
            "is_fallback": self.is_fallback,
            "record_count": self.record_count,
            "error": self.error,
            "rate_limit_risk": self.rate_limit_risk,
            "diagnosis": self.diagnosis,
            "freshness_catalog": self.freshness_catalog,
        }


@dataclass
class SourceFreshnessReport:
    """Top-level source freshness report."""
    report_date: str = ""
    generated_at: str = ""
    symbol: str = ""
    entries: List[SourceFreshnessEntry] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "report_date": self.report_date,
            "generated_at": self.generated_at,
            "symbol": self.symbol,
            "entries": [e.to_dict() for e in self.entries],
            "summary": self.summary,
        }


# ── Data type labels ──────────────────────────────────────────────────

_DATA_TYPE_LABELS: Dict[str, str] = {
    "ohlcv": "行情/OHLCV",
    "quote": "实时行情",
    "realtime_quotes": "实时补丁",
    "fund_flow": "个股资金流",
    "board_fund_flow": "板块资金流",
    "lhb": "龙虎榜",
    "margin_trading": "融资融券",
    "notice": "公告",
    "report": "研报",
    "rating": "评级",
    "news": "新闻",
    "global_news": "全球新闻",
    "financials": "财务数据",
    "insider": "内部交易",
    "zt_pool": "涨停池",
    "hot_stocks": "热门股票",
    "buyback": "回购",
}


# ── Evidence key → data_type mapping (reverse of evidence_contract) ───

_EVIDENCE_KEY_TO_DATA_TYPE: Dict[str, str] = {
    "stock_data": "ohlcv",
    "indicators": "ohlcv",
    "vpa_indicators": "ohlcv",
    "news": "news",
    "global_news": "global_news",
    "fund_flow_board": "board_fund_flow",
    "fund_flow_individual": "fund_flow",
    "lhb": "lhb",
    "fundamentals": "financials",
    "balance_sheet": "financials",
    "cashflow": "financials",
    "income_statement": "financials",
    "insider_transactions": "insider",
    "zt_pool": "zt_pool",
    "hot_stocks": "hot_stocks",
    "announcements": "notice",
    "margin_trading": "margin_trading",
    "research_report": "report",
    "ratings": "rating",
    "buybacks": "buyback",
}


# ── Classification helpers ────────────────────────────────────────────

def _is_stale_date(as_of: str, max_age_days: int = 7) -> bool:
    """Check if an as_of date is stale."""
    if not as_of:
        return False
    try:
        parsed = datetime.strptime(as_of[:10], "%Y-%m-%d")
        age = (datetime.now() - parsed).days
        return age > max_age_days
    except (ValueError, TypeError):
        return False


def _detect_rate_limited(error_str: str) -> bool:
    """Check if error string indicates rate limiting."""
    if not error_str:
        return False
    lower = error_str.lower()
    return any(p.lower() in lower for p in _RATE_LIMIT_PATTERNS)


def _detect_failed(error_str: str) -> bool:
    """Check if error/raw string indicates failure."""
    if not error_str:
        return False
    return any(p in error_str for p in _FAILURE_PATTERNS)


def _detect_normal_no_data(value: str) -> bool:
    """Check if value string indicates normal no-data."""
    if not value:
        return False
    return any(p in value for p in _NORMAL_NO_DATA_PATTERNS)


def classify_source_status(
    raw_value: Any = None,
    status: str = "",
    error: str = "",
    as_of: str = "",
    unit_verified: Optional[bool] = None,
    is_dict_entry: bool = False,
) -> str:
    """Classify a source's health into one of 6 states.

    Priority order:
      1. RATE_LIMITED — error contains rate-limit patterns
      2. FAILED — status is FAILED or error contains failure patterns
      3. STALE — data exists but as_of is too old
      4. UNIT_UNVERIFIED — data exists but unit not verified
      5. NORMAL_NO_DATA — queried successfully, no data
      6. HAS_DATA — valid data present
    """
    # 1. Rate-limited check first (most specific failure)
    if error and _detect_rate_limited(error):
        return SourceFreshnessStatus.RATE_LIMITED

    # 2. Explicit FAILED status
    if status == "FAILED":
        # Check if failure is actually rate limiting
        if error and _detect_rate_limited(error):
            return SourceFreshnessStatus.RATE_LIMITED
        return SourceFreshnessStatus.FAILED

    # 2b. Check failure patterns in raw value or error
    if isinstance(raw_value, str) and _detect_failed(raw_value):
        return SourceFreshnessStatus.FAILED
    if error and _detect_failed(error):
        return SourceFreshnessStatus.FAILED

    # 3. NOT_QUERIED maps to NORMAL_NO_DATA
    if status == "NOT_QUERIED":
        return SourceFreshnessStatus.NORMAL_NO_DATA

    # 4. NORMAL_NO_DATA
    if status == "NORMAL_NO_DATA":
        return SourceFreshnessStatus.NORMAL_NO_DATA
    if isinstance(raw_value, str) and _detect_normal_no_data(raw_value):
        return SourceFreshnessStatus.NORMAL_NO_DATA

    # 5. HAS_DATA with sub-classifications
    if status == "HAS_DATA" or (raw_value is not None and str(raw_value).strip()):
        # 5a. Stale check
        if _is_stale_date(as_of):
            return SourceFreshnessStatus.STALE

        # 5b. Unit unverified check (only for numeric data types)
        if unit_verified is False:
            return SourceFreshnessStatus.UNIT_UNVERIFIED

        # 5c. All good
        return SourceFreshnessStatus.HAS_DATA

    # 6. Empty value with no status
    if not raw_value and not status:
        return SourceFreshnessStatus.NORMAL_NO_DATA

    return SourceFreshnessStatus.FAILED


# ── Inspection from raw_evidence ──────────────────────────────────────

def _inspect_entry(
    evidence_key: str,
    raw_entry: Any,
    data_type: str,
) -> SourceFreshnessEntry:
    """Inspect a single raw_evidence entry for freshness.

    Args:
        evidence_key: the key in raw_evidence (e.g. "fund_flow_individual")
        raw_entry: the value from raw_evidence (dict or string)
        data_type: the data_type string (e.g. "fund_flow")
    """
    entry = SourceFreshnessEntry(
        data_type=data_type,
        label=_DATA_TYPE_LABELS.get(data_type, data_type),
    )

    # Get catalog info
    primary = get_primary_source(data_type)
    if primary:
        entry.primary_vendor = primary.vendor
        entry.fallback_chain = get_fallback_chain(data_type)
        entry.fallback_vendor = entry.fallback_chain[1] if len(entry.fallback_chain) > 1 else ""
        entry.unit = primary.unit or ""
        entry.freshness_catalog = primary.freshness.value if hasattr(primary.freshness, 'value') else str(primary.freshness)
        entry.rate_limit_risk = primary.rate_limit_risk.value if hasattr(primary.rate_limit_risk, 'value') else str(primary.rate_limit_risk)

    if raw_entry is None:
        entry.status = SourceFreshnessStatus.NORMAL_NO_DATA
        entry.diagnosis = f"{evidence_key} 未在 raw_evidence 中找到"
        return entry

    if isinstance(raw_entry, dict):
        entry.actual_vendor = raw_entry.get("vendor", "")
        entry.endpoint = raw_entry.get("endpoint", "")
        entry.as_of = raw_entry.get("as_of", "")
        entry.latest_data_date = raw_entry.get("as_of", "")
        entry.unit = raw_entry.get("unit") or entry.unit
        entry.unit_verified = raw_entry.get("unit_verified") is True
        entry.is_fallback = bool(raw_entry.get("fallback_from"))
        entry.record_count = raw_entry.get("record_count", 0)
        entry.error = raw_entry.get("error") or ""
        raw_value = raw_entry.get("raw")
        status = raw_entry.get("status", "NOT_QUERIED")
    else:
        raw_value = raw_entry
        status = "NOT_QUERIED"
        if isinstance(raw_value, str):
            s = raw_value.strip()
            if not s:
                status = "NOT_QUERIED"
            elif _detect_failed(s):
                status = "FAILED"
                entry.error = s[:200]
            elif _detect_normal_no_data(s):
                status = "NORMAL_NO_DATA"
            elif len(s) > 20:
                status = "HAS_DATA"
            else:
                status = "NORMAL_NO_DATA"
        # For string-based evidence, unit_verified is not applicable
        entry.unit_verified = True

    entry.status = classify_source_status(
        raw_value=raw_value,
        status=status,
        error=entry.error,
        as_of=entry.as_of,
        unit_verified=entry.unit_verified if entry.unit_verified is not None else None,
    )

    # Build diagnosis text
    entry.diagnosis = _build_diagnosis(entry)

    return entry


def _build_diagnosis(entry: SourceFreshnessEntry) -> str:
    """Build human-readable diagnosis text."""
    fb_note = ""
    if entry.is_fallback:
        fb_note = f"，fallback from {entry.fallback_chain[0] if entry.fallback_chain else 'unknown'}"

    status = entry.status
    if status == SourceFreshnessStatus.HAS_DATA:
        return (
            f"{entry.label}正常（vendor={entry.actual_vendor or entry.primary_vendor or '未知'}"
            f"，records={entry.record_count}{fb_note}）"
        )
    elif status == SourceFreshnessStatus.NORMAL_NO_DATA:
        return f"{entry.label}查询成功但无数据（正常情况）"
    elif status == SourceFreshnessStatus.STALE:
        return f"{entry.label}数据过期（as_of={entry.as_of or '未知'}），超过 7 天未更新"
    elif status == SourceFreshnessStatus.FAILED:
        return f"{entry.label}接口失败: {entry.error or '未知错误'}"
    elif status == SourceFreshnessStatus.RATE_LIMITED:
        return f"{entry.label}触发限流（{entry.rate_limit_risk or '高风险'}），需降低请求频率"
    elif status == SourceFreshnessStatus.UNIT_UNVERIFIED:
        return (
            f"{entry.label}有数据但单位未校验（unit={entry.unit or '未知'}），"
            "不能作为强证据使用"
        )
    return f"{entry.label}状态未知"


# ── Report generation ─────────────────────────────────────────────────

def run_source_freshness_report(
    raw_evidence: Optional[Dict[str, Any]] = None,
    symbol: str = "",
    data_types: Optional[List[str]] = None,
) -> SourceFreshnessReport:
    """Build a comprehensive source freshness report.

    Args:
        raw_evidence: raw_evidence dict from data_collector
        symbol: stock symbol (for reporting purposes)
        data_types: optional list of data_types to inspect; if None,
                     uses all types from source_catalog + evidence keys

    Returns:
        SourceFreshnessReport with entries for each data source
    """
    now = datetime.now()
    report = SourceFreshnessReport(
        report_date=now.strftime("%Y-%m-%d"),
        generated_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        symbol=symbol,
    )

    raw = raw_evidence or {}

    # Determine which data types to cover
    if data_types is None:
        # All types from catalog + any extra evidence keys
        all_types = get_all_data_types()
        # Also include data types from evidence keys
        for ev_key in raw:
            dt = _EVIDENCE_KEY_TO_DATA_TYPE.get(ev_key, "")
            if dt and dt not in all_types:
                all_types.append(dt)
        data_types = all_types

    # Track which evidence keys we've consumed
    consumed_keys: set = set()

    for dt in sorted(data_types):
        # Find evidence key(s) for this data_type
        ev_keys = [k for k, v in _EVIDENCE_KEY_TO_DATA_TYPE.items() if v == dt]
        ev_keys = [k for k in ev_keys if k not in consumed_keys]

        if ev_keys:
            # Use the first matching evidence key
            ev_key = ev_keys[0]
            consumed_keys.add(ev_key)
            raw_entry = raw.get(ev_key)

            if raw_entry is None and len(ev_keys) > 1:
                # Try other keys
                for alt_key in ev_keys[1:]:
                    if alt_key in raw:
                        ev_key = alt_key
                        consumed_keys.add(ev_key)
                        raw_entry = raw[ev_key]
                        break

            entry = _inspect_entry(ev_key, raw_entry, dt)
            report.entries.append(entry)
        else:
            # No evidence key for this data_type — catalog-only entry
            entry = _catalog_only_entry(dt)
            report.entries.append(entry)

    # Build summary
    report.summary = _build_summary(report.entries)

    return report


def _catalog_only_entry(data_type: str) -> SourceFreshnessEntry:
    """Create an entry for a data_type that has no raw_evidence."""
    entry = SourceFreshnessEntry(
        data_type=data_type,
        label=_DATA_TYPE_LABELS.get(data_type, data_type),
        status=SourceFreshnessStatus.NORMAL_NO_DATA,
    )
    primary = get_primary_source(data_type)
    if primary:
        entry.primary_vendor = primary.vendor
        entry.fallback_chain = get_fallback_chain(data_type)
        entry.fallback_vendor = entry.fallback_chain[1] if len(entry.fallback_chain) > 1 else ""
        entry.unit = primary.unit or ""
        entry.freshness_catalog = primary.freshness.value if hasattr(primary.freshness, 'value') else str(primary.freshness)
        entry.rate_limit_risk = primary.rate_limit_risk.value if hasattr(primary.rate_limit_risk, 'value') else str(primary.rate_limit_risk)
    entry.diagnosis = f"{entry.label}未查询（本次分析未触发该数据源）"
    return entry


def _build_summary(entries: List[SourceFreshnessEntry]) -> Dict[str, Any]:
    """Build summary statistics for the report."""
    status_counts: Dict[str, int] = {s: 0 for s in SourceFreshnessStatus.ALL}
    for e in entries:
        status_counts[e.status] = status_counts.get(e.status, 0) + 1

    total = len(entries)
    green = status_counts.get(SourceFreshnessStatus.HAS_DATA, 0) + status_counts.get(SourceFreshnessStatus.NORMAL_NO_DATA, 0)
    yellow = status_counts.get(SourceFreshnessStatus.STALE, 0) + status_counts.get(SourceFreshnessStatus.UNIT_UNVERIFIED, 0)
    red = status_counts.get(SourceFreshnessStatus.FAILED, 0) + status_counts.get(SourceFreshnessStatus.RATE_LIMITED, 0)

    fallback_count = sum(1 for e in entries if e.is_fallback)
    high_risk_sources = [e.data_type for e in entries if e.rate_limit_risk == "high"]

    return {
        "total_sources": total,
        "status_counts": status_counts,
        "green_count": green,
        "yellow_count": yellow,
        "red_count": red,
        "fallback_triggered_count": fallback_count,
        "all_green": red == 0 and yellow == 0,
        "has_failures": red > 0,
        "has_warnings": yellow > 0,
        "high_rate_limit_risk_sources": high_risk_sources,
        "overall_status": "all_green" if red == 0 and yellow == 0 else ("has_failures" if red > 0 else "has_warnings"),
    }


# ── Markdown Rendering ────────────────────────────────────────────────

def render_source_freshness_report(report: SourceFreshnessReport) -> str:
    """Render source freshness report as Markdown."""
    lines: List[str] = []
    lines.append("# A-Share Source Freshness & Fallback Report")
    lines.append("")
    lines.append(f"- **Date**: {report.report_date}")
    lines.append(f"- **Generated at**: {report.generated_at}")
    if report.symbol:
        lines.append(f"- **Symbol**: {report.symbol}")
    lines.append("")

    s = report.summary
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- **Total sources**: {s.get('total_sources', 0)}")
    lines.append(f"- **Green (OK)**: {s.get('green_count', 0)}")
    lines.append(f"- **Yellow (Warning)**: {s.get('yellow_count', 0)}")
    lines.append(f"- **Red (Failed)**: {s.get('red_count', 0)}")
    lines.append(f"- **Fallback triggered**: {s.get('fallback_triggered_count', 0)}")
    if s.get("high_rate_limit_risk_sources"):
        risk_sources = ", ".join(s["high_rate_limit_risk_sources"])
        lines.append(f"- **High rate-limit risk sources**: {risk_sources}")
    lines.append("")

    # Main table
    lines.append("## Source Status")
    lines.append("")
    lines.append("| Data Type | Status | Primary Vendor | Actual Vendor | Fallback Chain | As Of | Unit | Records | Error |")
    lines.append("|-----------|--------|----------------|---------------|----------------|--------|------|---------|-------|")
    for e in report.entries:
        fb_chain = " -> ".join(e.fallback_chain) if e.fallback_chain else "-"
        unit_str = f"{e.unit}" if e.unit else "-"
        if e.unit_verified:
            unit_str += " (verified)"
        as_of_str = e.as_of or e.latest_data_date or "-"
        error_str = (e.error[:60] + "...") if len(e.error) > 60 else (e.error or "-")
        lines.append(
            f"| {e.label} | {e.label_cn} ({e.status}) | {e.primary_vendor or '-'} | "
            f"{e.actual_vendor or '-'} | {fb_chain} | {as_of_str} | "
            f"{unit_str} | {e.record_count} | {error_str} |"
        )
    lines.append("")

    # Failed/Warning details
    problem_entries = [
        e for e in report.entries
        if e.status not in (SourceFreshnessStatus.HAS_DATA, SourceFreshnessStatus.NORMAL_NO_DATA)
    ]
    if problem_entries:
        lines.append("## Issues & Warnings")
        lines.append("")
        for e in problem_entries:
            icon = "RED" if e.traffic_light == "red" else "YELLOW"
            lines.append(f"### {icon}: {e.label} — {e.label_cn}")
            lines.append(f"- **Status**: `{e.status}`")
            lines.append(f"- **Primary vendor**: {e.primary_vendor or 'N/A'}")
            lines.append(f"- **Actual vendor**: {e.actual_vendor or 'N/A'}")
            if e.fallback_chain:
                lines.append(f"- **Fallback chain**: {' -> '.join(e.fallback_chain)}")
            if e.is_fallback:
                lines.append(f"- **Fallback triggered**: Yes")
            if e.error:
                lines.append(f"- **Error**: {e.error}")
            lines.append(f"- **Rate-limit risk**: {e.rate_limit_risk or 'unknown'}")
            lines.append(f"- **Diagnosis**: {e.diagnosis}")
            lines.append("")

    # Fallback summary
    fallback_entries = [e for e in report.entries if e.is_fallback]
    if fallback_entries:
        lines.append("## Fallback Details")
        lines.append("")
        lines.append("| Data Type | Primary | Actual | Fallback Chain |")
        lines.append("|-----------|---------|--------|----------------|")
        for e in fallback_entries:
            lines.append(
                f"| {e.label} | {e.primary_vendor or '-'} | "
                f"{e.actual_vendor or '-'} | {' -> '.join(e.fallback_chain)} |"
            )
        lines.append("")

    lines.append("---")
    lines.append("*Generated by source_freshness_report.py — [DATA-018] source_freshness_report*")
    lines.append("")

    return "\n".join(lines)


# ── File I/O ──────────────────────────────────────────────────────────

def save_source_freshness_report(
    report: SourceFreshnessReport,
    output_dir: str = "docs/data_source_reports",
    filename: Optional[str] = None,
) -> str:
    """Save report as Markdown file. Returns the file path.

    Args:
        report: the SourceFreshnessReport to save
        output_dir: directory to save into (created if not exists)
        filename: optional filename; defaults to freshness-YYYY-MM-DD.md
    """
    if not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)

    if filename is None:
        suffix = f"-{report.symbol}" if report.symbol else ""
        filename = f"freshness-{report.report_date}{suffix}.md"

    filepath = os.path.join(output_dir, filename)
    content = render_source_freshness_report(report)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    return filepath


def find_latest_freshness_report(
    reports_dir: str = "docs/data_source_reports",
    symbol: Optional[str] = None,
) -> Optional[str]:
    """Find the latest freshness report file."""
    if not os.path.isdir(reports_dir):
        return None

    files = []
    for f in os.listdir(reports_dir):
        if not f.startswith("freshness-") or not f.endswith(".md"):
            continue
        if symbol and symbol not in f:
            continue
        # Extract date from filename: freshness-YYYY-MM-DD[-SYMBOL].md
        parts = f.replace("freshness-", "").replace(".md", "")
        date_part = parts[:10]  # YYYY-MM-DD
        files.append((date_part, os.path.join(reports_dir, f)))

    if not files:
        return None

    files.sort(key=lambda x: x[0], reverse=True)
    return files[0][1]


# ── Nightly report integration ────────────────────────────────────────

def build_freshness_section_for_nightly_report(
    reports_dir: str = "docs/data_source_reports",
    raw_evidence: Optional[Dict[str, Any]] = None,
) -> str:
    """Build a freshness section for the nightly auto-dev report.

    If raw_evidence is provided, generates a fresh report.
    Otherwise, tries to find the latest saved report.
    """
    if raw_evidence is not None:
        report = run_source_freshness_report(raw_evidence)
        return _render_section(report)

    report_path = find_latest_freshness_report(reports_dir)
    if report_path:
        with open(report_path, encoding="utf-8") as f:
            return f.read()

    # No report available
    report = run_source_freshness_report({})
    return _render_section(report)


def _render_section(report: SourceFreshnessReport) -> str:
    """Render a compact section for the nightly report."""
    lines: List[str] = []
    lines.append("## 数据源新鲜度报告")
    lines.append("")
    lines.append(f"- **日期**: {report.report_date}")
    lines.append("")

    s = report.summary
    green = s.get("green_count", 0)
    yellow = s.get("yellow_count", 0)
    red = s.get("red_count", 0)

    status_icon = "ALL GREEN" if s.get("all_green") else ("HAS FAILURES" if red > 0 else "HAS WARNINGS")
    lines.append(f"- **状态**: {status_icon}")
    lines.append(f"- **绿**: {green} / **黄**: {yellow} / **红**: {red}")
    lines.append("")

    # Compact table
    lines.append("| 数据源 | 状态 | 主源 | 实际源 | 最新日期 |")
    lines.append("|--------|------|------|--------|----------|")
    for e in report.entries:
        lines.append(
            f"| {e.label} | {e.label_cn} | "
            f"{e.primary_vendor or '-'} | {e.actual_vendor or '-'} | "
            f"{e.as_of or e.latest_data_date or '-'} |"
        )
    lines.append("")

    # Issues
    issues = [
        e for e in report.entries
        if e.status in (SourceFreshnessStatus.FAILED, SourceFreshnessStatus.RATE_LIMITED)
    ]
    if issues:
        lines.append("### 需关注")
        lines.append("")
        for e in issues:
            lines.append(f"- **{e.label}**: {e.diagnosis}")
        lines.append("")

    lines.append("---")
    lines.append("*Generated by source_freshness_report.py — [DATA-018]*")
    lines.append("")

    return "\n".join(lines)


# ── Sample Fixtures ───────────────────────────────────────────────────
# Representative fixtures for testing: AKShare fail, cn_astock fallback success,
# LHB normal no-data, stale data, rate-limited, unit unverified.

def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _old_date_str(days: int = 30) -> str:
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")


SAMPLE_ALL_HEALTHY = {
    "stock_data": {
        "raw": "600519.SH 2026-06-14 OHLCV: open=1680.0 high=1700.0 low=1675.0 close=1695.0 volume=12345600",
        "status": "HAS_DATA",
        "unit": "元/股, 股",
        "unit_verified": True,
        "vendor": "cn_akshare",
        "endpoint": "stock_zh_a_hist",
        "as_of": _today_str(),
        "fetched_at": datetime.now().isoformat(),
        "record_count": 250,
    },
    "fund_flow_individual": {
        "raw": "600519.SH 近20日主力资金净流向",
        "status": "HAS_DATA",
        "unit": "万元",
        "unit_verified": True,
        "vendor": "cn_akshare",
        "endpoint": "stock_individual_fund_flow",
        "as_of": _today_str(),
        "fetched_at": datetime.now().isoformat(),
        "record_count": 20,
    },
    "lhb": {
        "raw": "LHB_NORMAL_NO_DATA: 非异动日无龙虎榜数据",
        "status": "NORMAL_NO_DATA",
        "vendor": "cn_akshare",
        "endpoint": "stock_lhb_detail_em",
        "query_mode": "forced",
        "as_of": _today_str(),
        "record_count": 0,
    },
    "news": {
        "raw": "相关新闻5条",
        "status": "HAS_DATA",
        "vendor": "cn_akshare",
        "endpoint": "stock_news_em",
        "as_of": _today_str(),
        "record_count": 5,
    },
    "announcements": {
        "raw": "公告2条",
        "status": "HAS_DATA",
        "vendor": "cn_astock",
        "endpoint": "cninfo.com.cn/hisAnnouncement",
        "as_of": _today_str(),
        "record_count": 2,
    },
}

SAMPLE_AKSHARE_FAIL_FALLBACK = {
    "stock_data": {
        "raw": "600519.SH OHLCV data...",
        "status": "HAS_DATA",
        "unit": "元/股, 股",
        "unit_verified": True,
        "vendor": "cn_akshare",
        "endpoint": "stock_zh_a_hist",
        "as_of": _today_str(),
        "record_count": 250,
    },
    "fund_flow_individual": {
        "raw": "600519.SH 近20日主力资金净流向",
        "status": "HAS_DATA",
        "unit": "万元",
        "unit_verified": True,
        "vendor": "cn_astock",
        "endpoint": "push2his.eastmoney.com/fflow",
        "fallback_from": "cn_akshare",
        "as_of": _today_str(),
        "fetched_at": datetime.now().isoformat(),
        "record_count": 20,
    },
    "lhb": {
        "raw": "LHB_NORMAL_NO_DATA: 在今日无龙虎榜数据（非异动日属正常）",
        "status": "NORMAL_NO_DATA",
        "vendor": "cn_akshare",
        "endpoint": "stock_lhb_detail_em",
        "query_mode": "forced",
        "force_reason": "fund_flow_anomaly",
        "as_of": _today_str(),
        "record_count": 0,
    },
}

SAMPLE_RATE_LIMITED = {
    "stock_data": {
        "raw": "行情数据获取失败",
        "status": "FAILED",
        "vendor": "",
        "error": "HTTPError 429: Too Many Requests",
        "as_of": "",
        "record_count": 0,
    },
    "fund_flow_individual": {
        "raw": "资金流获取失败",
        "status": "FAILED",
        "vendor": "",
        "error": "429: 请求过于频繁，请稍后重试",
        "as_of": "",
        "record_count": 0,
    },
    "lhb": {
        "raw": "LHB_NORMAL_NO_DATA: 非异动日",
        "status": "NORMAL_NO_DATA",
        "vendor": "cn_akshare",
        "as_of": _today_str(),
        "record_count": 0,
    },
}

SAMPLE_STALE_DATA = {
    "stock_data": {
        "raw": "600519.SH OHLCV...",
        "status": "HAS_DATA",
        "unit": "元/股, 股",
        "unit_verified": True,
        "vendor": "cn_akshare",
        "as_of": _old_date_str(30),
        "record_count": 250,
    },
    "fund_flow_individual": {
        "raw": "600519.SH 资金流...",
        "status": "HAS_DATA",
        "unit": "万元",
        "unit_verified": True,
        "vendor": "cn_astock",
        "fallback_from": "cn_akshare",
        "as_of": _old_date_str(15),
        "record_count": 10,
    },
}

SAMPLE_UNIT_UNVERIFIED = {
    "stock_data": {
        "raw": "600519.SH OHLCV...",
        "status": "HAS_DATA",
        "unit": "",
        "unit_verified": False,
        "vendor": "cn_astock",
        "endpoint": "finance.pae.baidu.com/kline",
        "as_of": _today_str(),
        "record_count": 200,
    },
    "fund_flow_individual": {
        "raw": "600519.SH 资金流...",
        "status": "HAS_DATA",
        "unit": None,
        "unit_verified": False,
        "vendor": "cn_astock",
        "endpoint": "push2his.eastmoney.com/fflow",
        "as_of": _today_str(),
        "record_count": 20,
    },
}

SAMPLE_INTERFACE_FAILED = {
    "stock_data": {
        "raw": "行情数据获取失败：ProxyError('Cannot connect to proxy.')",
        "status": "FAILED",
        "vendor": "",
        "error": "ProxyError: Cannot connect to proxy.",
        "as_of": "",
        "record_count": 0,
    },
    "fund_flow_individual": {
        "raw": "个股资金流向数据获取失败：ConnectionError",
        "status": "FAILED",
        "vendor": "",
        "error": "ConnectionError",
        "as_of": "",
        "record_count": 0,
    },
    "lhb": {
        "raw": "LHB_FAILED: 龙虎榜数据获取失败（ConnectionError）",
        "status": "FAILED",
        "vendor": "",
        "error": "ConnectionError",
        "as_of": "",
        "record_count": 0,
    },
}

ALL_FRESHNESS_SAMPLES = {
    "ALL_HEALTHY": SAMPLE_ALL_HEALTHY,
    "AKSHARE_FAIL_FALLBACK": SAMPLE_AKSHARE_FAIL_FALLBACK,
    "RATE_LIMITED": SAMPLE_RATE_LIMITED,
    "STALE_DATA": SAMPLE_STALE_DATA,
    "UNIT_UNVERIFIED": SAMPLE_UNIT_UNVERIFIED,
    "INTERFACE_FAILED": SAMPLE_INTERFACE_FAILED,
}
