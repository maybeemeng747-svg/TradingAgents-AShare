# [DATA-017] fund_lhb_health
"""主力资金 / 龙虎榜数据源健康巡检与 fallback 验收。

针对 TA 报告中主力资金仍失败的问题，提供可复现的数据源健康巡检，
区分以下五种状态：

  - HAS_DATA        — 接口成功返回有效数据
  - NORMAL_NO_DATA  — 接口成功但正常无数据（如非异动日无龙虎榜）
  - FAILED          — 接口失败 / 解析失败 / 超时
  - STALE           — 数据过期（日期不符或长期未更新）
  - UNIT_UNVERIFIED — 有数据但单位未校验，不能作为强证据

Usage (library):
    from tradingagents.dataflows.fund_lhb_health import (
        FundLhbHealthStatus,
        inspect_fund_flow_health,
        inspect_lhb_health,
        run_fund_lhb_health_check,
        render_fund_lhb_health_report,
    )
    result = run_fund_lhb_health_check(raw_evidence)
    print(render_fund_lhb_health_report(result))
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional


# ── Health Status ──────────────────────────────────────────────────────

class FundLhbHealthStatus:
    HAS_DATA = "HAS_DATA"
    NORMAL_NO_DATA = "NORMAL_NO_DATA"
    FAILED = "FAILED"
    STALE = "STALE"
    UNIT_UNVERIFIED = "UNIT_UNVERIFIED"

    ALL = [HAS_DATA, NORMAL_NO_DATA, FAILED, STALE, UNIT_UNVERIFIED]


# ── Data Models ────────────────────────────────────────────────────────

@dataclass
class FundFlowHealth:
    """Individual fund flow health inspection result."""
    status: str = FundLhbHealthStatus.FAILED
    vendor: str = ""
    endpoint: str = ""
    fallback_from: str = ""
    unit: str = ""
    unit_verified: bool = False
    as_of: str = ""
    fetched_at: str = ""
    record_count: int = 0
    is_fallback: bool = False
    error: str = ""
    diagnosis: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "vendor": self.vendor,
            "endpoint": self.endpoint,
            "fallback_from": self.fallback_from,
            "unit": self.unit,
            "unit_verified": self.unit_verified,
            "as_of": self.as_of,
            "fetched_at": self.fetched_at,
            "record_count": self.record_count,
            "is_fallback": self.is_fallback,
            "error": self.error,
            "diagnosis": self.diagnosis,
        }


@dataclass
class SectorFundFlowHealth:
    """Sector/board fund flow health inspection result."""
    status: str = FundLhbHealthStatus.FAILED
    vendor: str = ""
    endpoint: str = ""
    fallback_from: str = ""
    record_count: int = 0
    is_fallback: bool = False
    error: str = ""
    diagnosis: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "vendor": self.vendor,
            "endpoint": self.endpoint,
            "fallback_from": self.fallback_from,
            "record_count": self.record_count,
            "is_fallback": self.is_fallback,
            "error": self.error,
            "diagnosis": self.diagnosis,
        }


@dataclass
class LhbHealth:
    """LHB health inspection result."""
    status: str = FundLhbHealthStatus.FAILED
    vendor: str = ""
    endpoint: str = ""
    fallback_from: str = ""
    query_mode: str = "not_queried"
    force_reason: str = ""
    as_of: str = ""
    record_count: int = 0
    is_fallback: bool = False
    error: str = ""
    diagnosis: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "vendor": self.vendor,
            "endpoint": self.endpoint,
            "fallback_from": self.fallback_from,
            "query_mode": self.query_mode,
            "force_reason": self.force_reason,
            "as_of": self.as_of,
            "record_count": self.record_count,
            "is_fallback": self.is_fallback,
            "error": self.error,
            "diagnosis": self.diagnosis,
        }


@dataclass
class FundLhbHealthReport:
    """Combined health report for fund flow and LHB."""
    run_at: str = ""
    symbol: str = ""
    fund_flow_individual: FundFlowHealth = field(default_factory=FundFlowHealth)
    fund_flow_sector: SectorFundFlowHealth = field(default_factory=SectorFundFlowHealth)
    lhb: LhbHealth = field(default_factory=LhbHealth)
    summary: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_at": self.run_at,
            "symbol": self.symbol,
            "fund_flow_individual": self.fund_flow_individual.to_dict(),
            "fund_flow_sector": self.fund_flow_sector.to_dict(),
            "lhb": self.lhb.to_dict(),
            "summary": self.summary,
        }


# ── Inspection Logic ───────────────────────────────────────────────────

def _is_stale_date(as_of: str, max_age_days: int = 7) -> bool:
    """Check if an as_of date is stale (older than max_age_days from now)."""
    if not as_of:
        return False
    try:
        parsed = datetime.strptime(as_of[:10], "%Y-%m-%d")
        age = (datetime.now() - parsed).days
        return age > max_age_days
    except (ValueError, TypeError):
        return False


def inspect_fund_flow_health(
    raw_evidence: Optional[Dict[str, Any]] = None,
    symbol: str = "",
) -> FundFlowHealth:
    """Inspect individual fund flow health from raw_evidence.

    Classifies into:
      - HAS_DATA: successful retrieval with verified unit
      - UNIT_UNVERIFIED: data present but unit not verified
      - FAILED: API failure
      - STALE: data is too old
      - NORMAL_NO_DATA: queried but no data
    """
    raw = raw_evidence or {}
    entry = raw.get("fund_flow_individual")

    health = FundFlowHealth()

    if entry is None:
        health.status = FundLhbHealthStatus.FAILED
        health.error = "fund_flow_individual not found in raw_evidence"
        health.diagnosis = "个股资金流未在 raw_evidence 中找到，可能是数据采集阶段未执行或被跳过"
        return health

    if isinstance(entry, dict):
        health.vendor = entry.get("vendor", "")
        health.endpoint = entry.get("endpoint", "")
        health.fallback_from = entry.get("fallback_from") or ""
        health.unit = entry.get("unit") or ""
        health.unit_verified = entry.get("unit_verified") is True
        health.as_of = entry.get("as_of", "")
        health.fetched_at = entry.get("fetched_at", "")
        health.record_count = entry.get("record_count", 0)
        health.is_fallback = bool(health.fallback_from)
        raw_value = entry.get("raw")
        status = entry.get("status", "NOT_QUERIED")
        health.error = entry.get("error") or ""
    else:
        raw_value = entry
        status = "NOT_QUERIED"
        if isinstance(raw_value, str):
            s = raw_value.strip()
            if not s:
                status = "NOT_QUERIED"
            elif any(p in s for p in ("获取失败", "ProxyError", "ConnectionError", "不可用", "TimeoutError")):
                status = "FAILED"
                health.error = s[:200]
            elif len(s) > 20:
                status = "HAS_DATA"
                health.unit = "万元"
                health.unit_verified = True
            else:
                status = "NORMAL_NO_DATA"

    # Classify into one of 5 health states
    if status == "FAILED":
        health.status = FundLhbHealthStatus.FAILED
        if not health.diagnosis:
            health.diagnosis = f"个股资金流接口失败: {health.error or '未知错误'}"
            if health.vendor:
                health.diagnosis += f"（vendor={health.vendor}）"
    elif status == "NOT_QUERIED":
        health.status = FundLhbHealthStatus.FAILED
        health.diagnosis = "个股资金流未查询（NOT_QUERIED），数据采集阶段可能跳过或路由未触发"
    elif status == "NORMAL_NO_DATA":
        health.status = FundLhbHealthStatus.NORMAL_NO_DATA
        health.diagnosis = "个股资金流查询成功但无数据返回（可能为新股或停牌）"
    elif status == "HAS_DATA":
        if _is_stale_date(health.as_of):
            health.status = FundLhbHealthStatus.STALE
            health.diagnosis = f"个股资金流数据过期（as_of={health.as_of}），超过 7 天未更新"
        elif not health.unit_verified:
            health.status = FundLhbHealthStatus.UNIT_UNVERIFIED
            health.diagnosis = (
                f"个股资金流有数据但单位未校验（unit={health.unit or '未知'}），"
                "不能作为强证据使用"
            )
        else:
            health.status = FundLhbHealthStatus.HAS_DATA
            fb_note = ""
            if health.is_fallback:
                fb_note = f"，fallback from {health.fallback_from}"
            health.diagnosis = (
                f"个股资金流正常（vendor={health.vendor or '未知'}"
                f"，unit={health.unit or '未知'}，records={health.record_count}{fb_note}）"
            )
    else:
        health.status = FundLhbHealthStatus.FAILED
        health.diagnosis = f"个股资金流状态未知: {status}"

    return health


def inspect_sector_fund_flow_health(
    raw_evidence: Optional[Dict[str, Any]] = None,
    symbol: str = "",
) -> SectorFundFlowHealth:
    """Inspect sector/board fund flow health from raw_evidence."""
    raw = raw_evidence or {}
    entry = raw.get("fund_flow_board")

    health = SectorFundFlowHealth()

    if entry is None:
        health.status = FundLhbHealthStatus.NORMAL_NO_DATA
        health.diagnosis = "板块资金流未在 raw_evidence 中（非所有报告必查项）"
        return health

    if isinstance(entry, dict):
        health.vendor = entry.get("vendor", "")
        health.endpoint = entry.get("endpoint", "")
        health.fallback_from = entry.get("fallback_from") or ""
        health.record_count = entry.get("record_count", 0)
        health.is_fallback = bool(health.fallback_from)
        health.error = entry.get("error") or ""
        status = entry.get("status", "NOT_QUERIED")
    else:
        raw_value = entry
        status = "NOT_QUERIED"
        if isinstance(raw_value, str):
            s = raw_value.strip()
            if not s:
                status = "NOT_QUERIED"
            elif any(p in s for p in ("获取失败", "ProxyError", "ConnectionError", "不可用")):
                status = "FAILED"
                health.error = s[:200]
            elif len(s) > 20:
                status = "HAS_DATA"
            else:
                status = "NORMAL_NO_DATA"

    if status == "FAILED":
        health.status = FundLhbHealthStatus.FAILED
        health.diagnosis = f"板块资金流接口失败: {health.error or '未知错误'}"
    elif status in ("NOT_QUERIED",):
        health.status = FundLhbHealthStatus.NORMAL_NO_DATA
        health.diagnosis = "板块资金流未查询（非关键路径）"
    elif status == "NORMAL_NO_DATA":
        health.status = FundLhbHealthStatus.NORMAL_NO_DATA
        health.diagnosis = "板块资金流查询正常但无数据"
    else:
        health.status = FundLhbHealthStatus.HAS_DATA
        health.diagnosis = f"板块资金流正常（vendor={health.vendor or '未知'}）"

    return health


def inspect_lhb_health(
    raw_evidence: Optional[Dict[str, Any]] = None,
    symbol: str = "",
) -> LhbHealth:
    """Inspect LHB health from raw_evidence.

    Critical distinction:
      - NORMAL_NO_DATA: force=True, queried successfully, stock not on LHB (normal)
      - FAILED: API error / parse error / timeout
      - NOT_QUERIED: force=False, not triggered (not a failure)
      - HAS_DATA: actual LHB records found
    """
    raw = raw_evidence or {}
    entry = raw.get("lhb")

    health = LhbHealth()

    if entry is None:
        health.status = FundLhbHealthStatus.NORMAL_NO_DATA
        health.query_mode = "not_queried"
        health.diagnosis = "龙虎榜未在 raw_evidence 中（可能未触发查询）"
        return health

    if isinstance(entry, dict):
        health.vendor = entry.get("vendor", "")
        health.endpoint = entry.get("endpoint", "")
        health.fallback_from = entry.get("fallback_from") or ""
        health.query_mode = entry.get("query_mode", "not_queried") or "not_queried"
        health.force_reason = entry.get("force_reason", "") or ""
        health.as_of = entry.get("as_of", "")
        health.record_count = entry.get("record_count", 0)
        health.is_fallback = bool(health.fallback_from)
        health.error = entry.get("error") or ""
        status = entry.get("status", "NOT_QUERIED")
    else:
        raw_value = entry
        status = "NOT_QUERIED"
        health.query_mode = "not_queried"
        if isinstance(entry, dict) and "raw" in entry:
            raw_value = entry["raw"]
        if raw_value is None:
            status = "NOT_QUERIED"
        elif isinstance(raw_value, str):
            val = raw_value.strip()
            if not val:
                status = "NOT_QUERIED"
            elif "获取失败" in val or "LHB_FAILED" in val:
                status = "FAILED"
                health.error = val[:200]
            elif "查询未触发" in val or "LHB_NOT_QUERIED" in val:
                status = "NOT_QUERIED"
                health.query_mode = "not_queried"
            elif "无龙虎榜数据" in val or "非异动日" in val or "LHB_NORMAL_NO_DATA" in val:
                status = "NORMAL_NO_DATA"
                health.query_mode = "forced"
            elif "龙虎榜明细" in val or "LHB_HAS_DATA" in val:
                status = "HAS_DATA"
                health.query_mode = "forced"
            else:
                status = "NOT_QUERIED"
        else:
            status = "HAS_DATA" if raw_value else "NOT_QUERIED"

    if status == "FAILED":
        health.status = FundLhbHealthStatus.FAILED
        if not health.diagnosis:
            health.diagnosis = (
                f"龙虎榜查询失败: {health.error or '接口异常'}"
                f"（vendor={health.vendor or '未知'}）"
            )
    elif status == "NOT_QUERIED":
        health.status = FundLhbHealthStatus.NORMAL_NO_DATA
        health.diagnosis = (
            "龙虎榜未触发查询（force=False），非异常日不查询属正常行为，"
            "不应显示为失败"
        )
    elif status == "NORMAL_NO_DATA":
        health.status = FundLhbHealthStatus.NORMAL_NO_DATA
        health.diagnosis = (
            "龙虎榜已查询（force=True），当日无上榜记录，"
            "属于非异动日正常情况，不应降低完整度"
        )
    elif status == "HAS_DATA":
        if _is_stale_date(health.as_of):
            health.status = FundLhbHealthStatus.STALE
            health.diagnosis = f"龙虎榜数据过期（as_of={health.as_of}）"
        else:
            health.status = FundLhbHealthStatus.HAS_DATA
            health.diagnosis = (
                f"龙虎榜有数据（vendor={health.vendor or '未知'}，"
                f"records={health.record_count}）"
            )
    else:
        health.status = FundLhbHealthStatus.FAILED
        health.diagnosis = f"龙虎榜状态未知: {status}"

    return health


# ── Combined Health Check ──────────────────────────────────────────────

def run_fund_lhb_health_check(
    raw_evidence: Optional[Dict[str, Any]] = None,
    symbol: str = "",
) -> FundLhbHealthReport:
    """Run combined fund flow and LHB health check.

    Returns a FundLhbHealthReport with individual inspection results
    and an aggregated summary.
    """
    now = datetime.now()
    report = FundLhbHealthReport(
        run_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        symbol=symbol,
    )

    report.fund_flow_individual = inspect_fund_flow_health(raw_evidence, symbol)
    report.fund_flow_sector = inspect_sector_fund_flow_health(raw_evidence, symbol)
    report.lhb = inspect_lhb_health(raw_evidence, symbol)

    # Build summary
    statuses = [
        report.fund_flow_individual.status,
        report.fund_flow_sector.status,
        report.lhb.status,
    ]
    summary: Dict[str, Any] = {
        "fund_flow_individual_status": report.fund_flow_individual.status,
        "fund_flow_sector_status": report.fund_flow_sector.status,
        "lhb_status": report.lhb.status,
        "fund_flow_unit_verified": report.fund_flow_individual.unit_verified,
        "fund_flow_is_fallback": report.fund_flow_individual.is_fallback,
        "lhb_is_fallback": report.lhb.is_fallback,
        "has_any_failed": FundLhbHealthStatus.FAILED in statuses,
        "has_any_stale": FundLhbHealthStatus.STALE in statuses,
        "has_unit_unverified": FundLhbHealthStatus.UNIT_UNVERIFIED in statuses,
        "all_critical_ok": (
            report.fund_flow_individual.status == FundLhbHealthStatus.HAS_DATA
            and report.lhb.status in (
                FundLhbHealthStatus.HAS_DATA,
                FundLhbHealthStatus.NORMAL_NO_DATA,
            )
        ),
        "status_counts": {s: statuses.count(s) for s in FundLhbHealthStatus.ALL if s in statuses},
    }
    report.summary = summary

    return report


# ── Markdown Rendering ─────────────────────────────────────────────────

def render_fund_lhb_health_report(report: FundLhbHealthReport) -> str:
    """Render fund flow / LHB health report as Markdown."""
    lines: List[str] = []
    lines.append("# Fund Flow & LHB Health Report")
    lines.append("")
    lines.append(f"- **Symbol**: {report.symbol or 'N/A'}")
    lines.append(f"- **Run at**: {report.run_at}")
    lines.append("")

    s = report.summary
    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Fund Flow (Individual) | {s.get('fund_flow_individual_status', 'N/A')} |")
    lines.append(f"| Fund Flow (Sector) | {s.get('fund_flow_sector_status', 'N/A')} |")
    lines.append(f"| LHB | {s.get('lhb_status', 'N/A')} |")
    lines.append(f"| Unit Verified | {'Yes' if s.get('fund_flow_unit_verified') else 'No'} |")
    lines.append(f"| Fund Flow Fallback | {'Yes' if s.get('fund_flow_is_fallback') else 'No'} |")
    lines.append(f"| LHB Fallback | {'Yes' if s.get('lhb_is_fallback') else 'No'} |")
    lines.append(f"| All Critical OK | {'Yes' if s.get('all_critical_ok') else 'No'} |")
    lines.append("")

    # Individual Fund Flow
    ff = report.fund_flow_individual
    lines.append("## Individual Fund Flow")
    lines.append("")
    lines.append(f"- **Status**: `{ff.status}`")
    lines.append(f"- **Vendor**: {ff.vendor or 'N/A'}")
    lines.append(f"- **Endpoint**: {ff.endpoint or 'N/A'}")
    lines.append(f"- **Fallback From**: {ff.fallback_from or 'None'}")
    lines.append(f"- **Unit**: {ff.unit or 'N/A'} (verified: {ff.unit_verified})")
    lines.append(f"- **As Of**: {ff.as_of or 'N/A'}")
    lines.append(f"- **Records**: {ff.record_count}")
    if ff.error:
        lines.append(f"- **Error**: {ff.error}")
    lines.append(f"- **Diagnosis**: {ff.diagnosis}")
    lines.append("")

    # Sector Fund Flow
    sf = report.fund_flow_sector
    lines.append("## Sector Fund Flow")
    lines.append("")
    lines.append(f"- **Status**: `{sf.status}`")
    lines.append(f"- **Vendor**: {sf.vendor or 'N/A'}")
    lines.append(f"- **Endpoint**: {sf.endpoint or 'N/A'}")
    if sf.error:
        lines.append(f"- **Error**: {sf.error}")
    lines.append(f"- **Diagnosis**: {sf.diagnosis}")
    lines.append("")

    # LHB
    lhb = report.lhb
    lines.append("## LHB (Dragon-Tiger Board)")
    lines.append("")
    lines.append(f"- **Status**: `{lhb.status}`")
    lines.append(f"- **Vendor**: {lhb.vendor or 'N/A'}")
    lines.append(f"- **Endpoint**: {lhb.endpoint or 'N/A'}")
    lines.append(f"- **Query Mode**: {lhb.query_mode}")
    if lhb.force_reason:
        lines.append(f"- **Force Reason**: {lhb.force_reason}")
    lines.append(f"- **Records**: {lhb.record_count}")
    if lhb.error:
        lines.append(f"- **Error**: {lhb.error}")
    lines.append(f"- **Diagnosis**: {lhb.diagnosis}")
    lines.append("")

    lines.append("---")
    lines.append("*Generated by fund_lhb_health.py — [DATA-017] fund_lhb_health*")
    lines.append("")

    return "\n".join(lines)


# ── Sample Fixtures ────────────────────────────────────────────────────
# Three representative samples as required by the task:
#   1. HAS_DATA: fund flow with verified unit, no LHB (normal)
#   2. NORMAL_NO_DATA: no LHB records on non-anomaly day
#   3. FAILED: fund flow API failure fixture

SAMPLE_HAS_FUND_DATA = {
    "fund_flow_individual": {
        "raw": "600519.SH 近20日主力资金净流向：\n日期 主力净流入(万元)\n2026-06-10 12345.6",
        "status": "HAS_DATA",
        "unit": "万元",
        "unit_verified": True,
        "vendor": "cn_akshare",
        "endpoint": "stock_individual_fund_flow",
        "as_of": datetime.now().strftime("%Y-%m-%d"),
        "fetched_at": datetime.now().isoformat(),
        "record_count": 20,
    },
    "fund_flow_board": {
        "raw": "板块资金流...",
        "status": "HAS_DATA",
        "vendor": "cn_akshare",
        "endpoint": "stock_sector_fund_flow",
    },
    "lhb": {
        "raw": "[G-007] LHB_NORMAL_NO_DATA: 在 2026-06-10 无龙虎榜数据（非异动日属正常）",
        "status": "NORMAL_NO_DATA",
        "vendor": "cn_akshare",
        "endpoint": "stock_lhb_detail_em",
        "query_mode": "forced",
        "force_reason": "fund_flow_anomaly",
        "as_of": datetime.now().strftime("%Y-%m-%d"),
        "record_count": 0,
    },
}

SAMPLE_NO_LHB_NORMAL = {
    "fund_flow_individual": {
        "raw": "000001.SZ 近20日主力资金净流向：\n日期 主力净流入(万元)\n2026-06-10 -5000",
        "status": "HAS_DATA",
        "unit": "万元",
        "unit_verified": True,
        "vendor": "cn_astock",
        "endpoint": "push2his.eastmoney.com/fflow",
        "fallback_from": "cn_akshare",
        "as_of": datetime.now().strftime("%Y-%m-%d"),
        "fetched_at": datetime.now().isoformat(),
        "record_count": 20,
    },
    "fund_flow_board": {
        "raw": "",
        "status": "NORMAL_NO_DATA",
        "vendor": "",
    },
    "lhb": {
        "raw": "[G-007] LHB_NORMAL_NO_DATA: 在 2026-06-10 无龙虎榜数据（非异动日属正常）",
        "status": "NORMAL_NO_DATA",
        "vendor": "cn_akshare",
        "endpoint": "stock_lhb_detail_em",
        "query_mode": "forced",
        "force_reason": "anomaly_condition",
        "as_of": datetime.now().strftime("%Y-%m-%d"),
        "record_count": 0,
    },
}

SAMPLE_INTERFACE_FAILED = {
    "fund_flow_individual": {
        "raw": "个股资金流向数据获取失败：ProxyError('Cannot connect to proxy.')",
        "status": "FAILED",
        "unit": None,
        "unit_verified": False,
        "vendor": "",
        "endpoint": "",
        "error": "ProxyError: Cannot connect to proxy.",
        "as_of": "",
        "fetched_at": "",
        "record_count": 0,
    },
    "fund_flow_board": {
        "raw": "",
        "status": "NOT_QUERIED",
        "vendor": "",
    },
    "lhb": {
        "raw": "[G-007] LHB_FAILED: 龙虎榜数据获取失败（ConnectionError）",
        "status": "FAILED",
        "vendor": "",
        "endpoint": "",
        "query_mode": "forced",
        "force_reason": "fund_flow_anomaly_and_anomaly_condition",
        "error": "ConnectionError",
        "as_of": "",
        "record_count": 0,
    },
}

ALL_SAMPLES = {
    "HAS_FUND_DATA": SAMPLE_HAS_FUND_DATA,
    "NO_LHB_NORMAL": SAMPLE_NO_LHB_NORMAL,
    "INTERFACE_FAILED": SAMPLE_INTERFACE_FAILED,
}
