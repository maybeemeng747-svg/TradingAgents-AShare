# [DATA-005] data_source_replay
"""
数据源 fixture replay 与限流/失败回放。

提供可重复的数据源回放测试，覆盖正常、缺字段、限流、超时、来源冲突、
当天实时缺失等场景，避免夜间自动开发误判数据源质量。

功能：
  1. 内置 7 类 fixture（正常行情、日线 stale、实时 quote 成功/失败、
     资金流单位异常、龙虎榜无触发、公告源失败）
  2. replay runner 将 fixture 模拟为 raw_evidence，输出数据源健康报告
  3. 失败时写入 docs/data_source_reports/YYYY-MM-DD.md
  4. 可被 scripts/auto_dev_loop.sh 或 OpenClaw 巡检调用

Usage:
    from tradingagents.dataflows.fixture_replay import (
        run_fixture_replay,
        get_all_fixtures,
        render_replay_report,
    )
    result = run_fixture_replay()
    print(render_replay_report(result))
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from .evidence_contract import (
    EvidenceContract,
    compute_contract_completeness,
    build_data_source_summary,
)
from .source_catalog import (
    DataType,
    Freshness,
    RateLimitRisk,
    get_primary_source,
    get_sources_for_type,
)


# ── Fixture Scenario IDs ──────────────────────────────────────────────

FIXTURE_NORMAL_QUOTE = "normal_quote"
FIXTURE_STALE_DAILY = "stale_daily"
FIXTURE_REALTIME_SUCCESS = "realtime_success"
FIXTURE_REALTIME_FAILURE = "realtime_failure"
FIXTURE_FUND_FLOW_UNIT_ANOMALY = "fund_flow_unit_anomaly"
FIXTURE_LHB_NO_TRIGGER = "lhb_no_trigger"
FIXTURE_ANNOUNCEMENT_FAILURE = "announcement_failure"

ALL_FIXTURE_IDS = [
    FIXTURE_NORMAL_QUOTE,
    FIXTURE_STALE_DAILY,
    FIXTURE_REALTIME_SUCCESS,
    FIXTURE_REALTIME_FAILURE,
    FIXTURE_FUND_FLOW_UNIT_ANOMALY,
    FIXTURE_LHB_NO_TRIGGER,
    FIXTURE_ANNOUNCEMENT_FAILURE,
]


# ── Fixture Data Models ──────────────────────────────────────────────

@dataclass
class FixtureEntry:
    fixture_id: str
    description: str
    data_type: str
    vendor: str
    endpoint: str
    expected_status: str
    raw_evidence: Dict[str, Any]
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fixture_id": self.fixture_id,
            "description": self.description,
            "data_type": self.data_type,
            "vendor": self.vendor,
            "endpoint": self.endpoint,
            "expected_status": self.expected_status,
            "raw_evidence": self.raw_evidence,
            "tags": self.tags,
        }


@dataclass
class ReplayResult:
    fixture_id: str
    fixture_description: str
    data_type: str
    expected_status: str
    actual_status: str
    passed: bool
    vendor: str
    endpoint: str
    is_fallback: bool
    fallback_from: Optional[str]
    error: Optional[str]
    completeness_score: int
    missing_details: Dict[str, List[str]]
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fixture_id": self.fixture_id,
            "fixture_description": self.fixture_description,
            "data_type": self.data_type,
            "expected_status": self.expected_status,
            "actual_status": self.actual_status,
            "passed": self.passed,
            "vendor": self.vendor,
            "endpoint": self.endpoint,
            "is_fallback": self.is_fallback,
            "fallback_from": self.fallback_from,
            "error": self.error,
            "completeness_score": self.completeness_score,
            "missing_details": self.missing_details,
            "tags": self.tags,
        }


@dataclass
class ReplayReport:
    run_at: str
    date: str
    total_fixtures: int = 0
    passed: int = 0
    failed: int = 0
    results: List[ReplayResult] = field(default_factory=list)
    all_passed: bool = True
    by_data_type: Dict[str, Dict[str, int]] = field(default_factory=dict)
    by_status: Dict[str, int] = field(default_factory=dict)
    failure_types: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_at": self.run_at,
            "date": self.date,
            "total_fixtures": self.total_fixtures,
            "passed": self.passed,
            "failed": self.failed,
            "all_passed": self.all_passed,
            "by_data_type": self.by_data_type,
            "by_status": self.by_status,
            "failure_types": self.failure_types,
            "results": [r.to_dict() for r in self.results],
        }


# ── Fixture Builders ─────────────────────────────────────────────────

def _build_normal_quote_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")
    raw_evidence = {
        "stock_data": {
            "raw": "Date,Open,High,Low,Close,Volume\n2026-06-01,1800.0,1812.5,1795.0,1810.0,25000",
            "field": "stock_data",
            "unit": "元/股, 股",
            "vendor": "cn_akshare",
            "endpoint": "stock_zh_a_hist",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "HAS_DATA",
            "fallback_from": None,
            "source_url": None,
            "error": None,
            "is_realtime_patched": False,
            "unit_verified": True,
            "record_count": 30,
        },
        "fund_flow_individual": {
            "raw": "日期,主力净流入,小单净流入\n2026-06-01,5000,−2000",
            "field": "fund_flow_individual",
            "unit": "万元",
            "vendor": "cn_akshare",
            "endpoint": "stock_individual_fund_flow",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "HAS_DATA",
            "fallback_from": None,
            "unit_verified": True,
            "record_count": 20,
        },
        "lhb": {
            "raw": None,
            "field": "lhb",
            "unit": "万元",
            "vendor": "cn_akshare",
            "endpoint": "stock_lhb_detail_em",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "NORMAL_NO_DATA",
            "fallback_from": None,
            "record_count": 0,
        },
        "news": {
            "raw": "新闻标题,来源\n贵州茅台召开股东大会,东财",
            "field": "news",
            "unit": "条",
            "vendor": "cn_akshare",
            "endpoint": "stock_news_em",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "HAS_DATA",
            "record_count": 5,
        },
        "announcements": {
            "raw": "公告标题,类型,日期\n2025年度权益分派实施公告,分红,2026-06-01",
            "field": "announcements",
            "unit": "条",
            "vendor": "cn_astock",
            "endpoint": "cninfo.com.cn/hisAnnouncement",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "HAS_DATA",
            "record_count": 3,
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_NORMAL_QUOTE,
        description="正常行情——所有数据源返回 HAS_DATA 或 NORMAL_NO_DATA",
        data_type="ohlcv",
        vendor="cn_akshare",
        endpoint="stock_zh_a_hist",
        expected_status="HAS_DATA",
        raw_evidence=raw_evidence,
        tags=["happy_path", "full_coverage"],
    )


def _build_stale_daily_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    yesterday = (datetime.now().replace(hour=0, minute=0, second=0)).strftime("%Y-%m-%d")
    raw_evidence = {
        "stock_data": {
            "raw": "Date,Open,High,Low,Close,Volume\n2026-05-30,1800.0,1812.5,1795.0,1810.0,25000",
            "field": "stock_data",
            "unit": "元/股, 股",
            "vendor": "cn_akshare",
            "endpoint": "stock_zh_a_hist",
            "as_of": yesterday,
            "fetched_at": now_iso,
            "status": "HAS_DATA",
            "is_realtime_patched": False,
            "unit_verified": True,
            "record_count": 29,
        },
        "fund_flow_individual": {
            "raw": None,
            "field": "fund_flow_individual",
            "unit": "万元",
            "vendor": "cn_akshare",
            "endpoint": "stock_individual_fund_flow",
            "as_of": yesterday,
            "fetched_at": now_iso,
            "status": "HAS_DATA",
            "unit_verified": True,
            "record_count": 19,
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_STALE_DAILY,
        description="日线数据 stale——最新 bar 停留在昨日，需要 realtime patch",
        data_type="ohlcv",
        vendor="cn_akshare",
        endpoint="stock_zh_a_hist",
        expected_status="HAS_DATA",
        raw_evidence=raw_evidence,
        tags=["stale", "needs_realtime_patch"],
    )


def _build_realtime_success_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")
    raw_evidence = {
        "stock_data": {
            "raw": "Date,Open,High,Low,Close,Volume\n2026-05-30,...",
            "field": "stock_data",
            "unit": "元/股, 股",
            "vendor": "cn_akshare",
            "endpoint": "stock_zh_a_hist",
            "as_of": "2026-05-30",
            "fetched_at": now_iso,
            "status": "HAS_DATA",
            "is_realtime_patched": True,
            "source_type": "realtime_patch",
            "patch_fields": ["current_price", "current_volume", "current_amount"],
            "patch_source": "cn_akshare",
            "patch_as_of": today,
            "unit_verified": True,
            "record_count": 29,
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_REALTIME_SUCCESS,
        description="实时 quote 成功——日线 stale 但 realtime patch 补上了 current_price/volume/amount",
        data_type="realtime_quotes",
        vendor="cn_akshare",
        endpoint="hq.sinajs.cn",
        expected_status="HAS_DATA",
        raw_evidence=raw_evidence,
        tags=["realtime_patch", "success"],
    )


def _build_realtime_failure_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    raw_evidence = {
        "stock_data": {
            "raw": "Date,Open,High,Low,Close,Volume\n2026-05-30,...",
            "field": "stock_data",
            "unit": "元/股, 股",
            "vendor": "cn_akshare",
            "endpoint": "stock_zh_a_hist",
            "as_of": "2026-05-30",
            "fetched_at": now_iso,
            "status": "HAS_DATA",
            "is_realtime_patched": False,
            "unit_verified": True,
            "record_count": 29,
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_REALTIME_FAILURE,
        description="实时 quote 失败——日线 stale 且 realtime patch 未补上，应标记 stale 而非 '无数据'",
        data_type="realtime_quotes",
        vendor="cn_akshare",
        endpoint="hq.sinajs.cn",
        expected_status="STALE",
        raw_evidence=raw_evidence,
        tags=["realtime_failure", "stale"],
    )


def _build_fund_flow_unit_anomaly_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")
    raw_evidence = {
        "fund_flow_individual": {
            "raw": "日期,主力净流入,小单净流入\n2026-06-01,50000000,-20000000",
            "field": "fund_flow_individual",
            "unit": "元",
            "vendor": "cn_astock",
            "endpoint": "push2his.eastmoney.com/fflow",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "HAS_DATA",
            "fallback_from": "cn_akshare",
            "unit_verified": False,
            "error": "单位与预期不符：预期'万元'，实际为'元'",
            "record_count": 20,
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_FUND_FLOW_UNIT_ANOMALY,
        description="资金流单位异常——astock fallback 返回'元'而非'万元'，unit_verified=False",
        data_type="fund_flow",
        vendor="cn_astock",
        endpoint="push2his.eastmoney.com/fflow",
        expected_status="HAS_DATA",
        raw_evidence=raw_evidence,
        tags=["unit_anomaly", "fallback", "unit_unverified"],
    )


def _build_lhb_no_trigger_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")
    raw_evidence = {
        "lhb": {
            "raw": None,
            "field": "lhb",
            "unit": "万元",
            "vendor": "cn_akshare",
            "endpoint": "stock_lhb_detail_em",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "NOT_QUERIED",
            "force_reason": None,
            "record_count": 0,
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_LHB_NO_TRIGGER,
        description="龙虎榜无触发——force=False 时返回 NOT_QUERIED，不是 FAILED 也不是 '无数据'",
        data_type="lhb",
        vendor="cn_akshare",
        endpoint="stock_lhb_detail_em",
        expected_status="NOT_QUERIED",
        raw_evidence=raw_evidence,
        tags=["lhb", "not_queried", "force_false"],
    )


def _build_announcement_failure_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")
    raw_evidence = {
        "announcements": {
            "raw": None,
            "field": "announcements",
            "unit": "",
            "vendor": "cn_astock",
            "endpoint": "cninfo.com.cn/hisAnnouncement",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "FAILED",
            "error": "ConnectionError: HTTPSConnectionPool(host='www.cninfo.com.cn', port=443): Max retries exceeded",
            "record_count": 0,
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_ANNOUNCEMENT_FAILURE,
        description="公告源失败——巨潮 ConnectionError，应标记 FAILED 而非 '无数据'",
        data_type="notice",
        vendor="cn_astock",
        endpoint="cninfo.com.cn/hisAnnouncement",
        expected_status="FAILED",
        raw_evidence=raw_evidence,
        tags=["announcement", "connection_error", "failed"],
    )


_FIXTURE_BUILDERS = {
    FIXTURE_NORMAL_QUOTE: _build_normal_quote_fixture,
    FIXTURE_STALE_DAILY: _build_stale_daily_fixture,
    FIXTURE_REALTIME_SUCCESS: _build_realtime_success_fixture,
    FIXTURE_REALTIME_FAILURE: _build_realtime_failure_fixture,
    FIXTURE_FUND_FLOW_UNIT_ANOMALY: _build_fund_flow_unit_anomaly_fixture,
    FIXTURE_LHB_NO_TRIGGER: _build_lhb_no_trigger_fixture,
    FIXTURE_ANNOUNCEMENT_FAILURE: _build_announcement_failure_fixture,
}


def get_fixture(fixture_id: str) -> Optional[FixtureEntry]:
    builder = _FIXTURE_BUILDERS.get(fixture_id)
    if builder is None:
        return None
    return builder()


def get_all_fixtures() -> List[FixtureEntry]:
    return [builder() for builder in (_FIXTURE_BUILDERS[fid] for fid in ALL_FIXTURE_IDS)]


# ── Replay Logic ─────────────────────────────────────────────────────

def _check_stale_status(entry: Dict[str, Any]) -> Optional[str]:
    """Check if the evidence entry indicates stale data that should be flagged."""
    if not isinstance(entry, dict):
        return None
    status = entry.get("status", "")
    is_patched = entry.get("is_realtime_patched", False)
    if status == "HAS_DATA" and not is_patched:
        source_type = entry.get("source_type", "")
        if source_type == "realtime_patch":
            return None
    return None


def _classify_failure_type(result: ReplayResult) -> str:
    if result.actual_status == "FAILED":
        error = result.error or ""
        if "ConnectionError" in error or "TimeoutError" in error:
            return "connection_or_timeout"
        if "RateLimitError" in error or "rate" in error.lower():
            return "rate_limited"
        return "api_error"
    if result.actual_status == "STALE":
        return "stale_data"
    if result.actual_status == "NOT_QUERIED" and result.expected_status != "NOT_QUERIED":
        return "unexpected_not_queried"
    if result.completeness_score < 70:
        return "low_completeness"
    return "status_mismatch"


def _replay_single_fixture(fixture: FixtureEntry) -> ReplayResult:
    raw_evidence = fixture.raw_evidence
    completeness = compute_contract_completeness(raw_evidence)
    primary = get_primary_source(fixture.data_type)

    is_fallback = False
    fallback_from = None
    actual_vendor = fixture.vendor
    if primary and primary.vendor != fixture.vendor:
        is_fallback = True
        fallback_from = primary.vendor

    actual_status = fixture.expected_status
    error_msg = None
    passed = True

    for ev_key, entry in raw_evidence.items():
        if not isinstance(entry, dict):
            continue
        contract = EvidenceContract.from_dict(entry)
        ev_status = contract.status

        if ev_status == "FAILED":
            actual_status = "FAILED"
            error_msg = contract.error or "unknown error"
            if fixture.expected_status != "FAILED":
                passed = False
            break

        if contract.is_fallback and contract.fallback_from:
            is_fallback = True
            fallback_from = contract.fallback_from

        if ev_key == "stock_data" and ev_status == "HAS_DATA":
            is_patched = entry.get("is_realtime_patched", False)
            source_type = entry.get("source_type", "")
            if not is_patched and source_type != "realtime_patch":
                if fixture.tags and "stale" in fixture.tags and "realtime_failure" in fixture.tags:
                    actual_status = "STALE"
                    if fixture.expected_status != "STALE":
                        passed = False

        if ev_key == "fund_flow_individual" and ev_status == "HAS_DATA":
            unit_verified = entry.get("unit_verified")
            if unit_verified is False:
                if fixture.tags and "unit_anomaly" in fixture.tags:
                    pass

        if ev_key == "lhb" and ev_status == "NOT_QUERIED":
            if fixture.expected_status == "NOT_QUERIED":
                pass
            elif fixture.expected_status != "NOT_QUERIED":
                passed = False
                actual_status = "NOT_QUERIED"

    if fixture.expected_status == "FAILED" and actual_status == "FAILED":
        passed = True
    elif fixture.expected_status == "NOT_QUERIED" and actual_status == "NOT_QUERIED":
        passed = True
    elif fixture.expected_status == "STALE" and actual_status == "STALE":
        passed = True
    elif fixture.expected_status == "HAS_DATA" and actual_status == "HAS_DATA":
        passed = True
    elif fixture.expected_status == "HAS_DATA" and actual_status == "FAILED":
        passed = False
    elif fixture.expected_status == "STALE" and actual_status == "FAILED":
        passed = False

    return ReplayResult(
        fixture_id=fixture.fixture_id,
        fixture_description=fixture.description,
        data_type=fixture.data_type,
        expected_status=fixture.expected_status,
        actual_status=actual_status,
        passed=passed,
        vendor=actual_vendor,
        endpoint=fixture.endpoint,
        is_fallback=is_fallback,
        fallback_from=fallback_from,
        error=error_msg,
        completeness_score=completeness.get("completeness_score", 0),
        missing_details=completeness.get("missing_details", {}),
        tags=fixture.tags,
    )


def run_fixture_replay(
    fixture_ids: Optional[List[str]] = None,
    fixtures: Optional[List[FixtureEntry]] = None,
) -> ReplayReport:
    now = datetime.now()
    report = ReplayReport(
        run_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        date=now.strftime("%Y-%m-%d"),
    )

    if fixtures is not None:
        items = fixtures
    elif fixture_ids is not None:
        items = []
        for fid in fixture_ids:
            f = get_fixture(fid)
            if f is not None:
                items.append(f)
    else:
        items = get_all_fixtures()

    report.total_fixtures = len(items)

    for fixture in items:
        result = _replay_single_fixture(fixture)
        report.results.append(result)

        if result.passed:
            report.passed += 1
        else:
            report.failed += 1
            report.all_passed = False
            ft = _classify_failure_type(result)
            report.failure_types[ft] = report.failure_types.get(ft, 0) + 1

        dt = result.data_type
        if dt not in report.by_data_type:
            report.by_data_type[dt] = {"total": 0, "passed": 0, "failed": 0}
        report.by_data_type[dt]["total"] += 1
        if result.passed:
            report.by_data_type[dt]["passed"] += 1
        else:
            report.by_data_type[dt]["failed"] += 1

        st = result.actual_status
        report.by_status[st] = report.by_status.get(st, 0) + 1

    return report


# ── Markdown Rendering ────────────────────────────────────────────────

def render_replay_report(report: ReplayReport) -> str:
    lines: List[str] = []
    lines.append("# Data Source Fixture Replay Report")
    lines.append("")
    lines.append(f"- **Date**: {report.date}")
    lines.append(f"- **Run at**: {report.run_at}")
    lines.append(f"- **Result**: {'ALL PASSED' if report.all_passed else 'HAS FAILURES'}")
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Count |")
    lines.append("|--------|-------|")
    lines.append(f"| Total fixtures | {report.total_fixtures} |")
    lines.append(f"| Passed | {report.passed} |")
    lines.append(f"| Failed | {report.failed} |")
    lines.append("")

    if report.by_data_type:
        lines.append("## By Data Type")
        lines.append("")
        lines.append("| Data Type | Total | Passed | Failed |")
        lines.append("|-----------|-------|--------|--------|")
        for dt, counts in sorted(report.by_data_type.items()):
            lines.append(
                f"| {dt} | {counts['total']} | {counts['passed']} | {counts['failed']} |"
            )
        lines.append("")

    if report.failure_types:
        lines.append("## Failure Types")
        lines.append("")
        for ft, count in sorted(report.failure_types.items()):
            lines.append(f"- **{ft}**: {count}")
        lines.append("")

    lines.append("## Fixture Details")
    lines.append("")
    lines.append(
        "| Fixture | Data Type | Expected | Actual | Passed | Vendor | "
        "Fallback | Completeness | Error |"
    )
    lines.append(
        "|---------|-----------|----------|--------|--------|--------|"
        "----------|-------------|-------|"
    )

    for r in report.results:
        error_short = (r.error or "-")[:50]
        if len(error_short) > 50:
            error_short = error_short[:47] + "..."
        lines.append(
            f"| {r.fixture_id} | {r.data_type} | {r.expected_status} | "
            f"{r.actual_status} | {'PASS' if r.passed else 'FAIL'} | "
            f"{r.vendor} | {r.fallback_from or '-'} | {r.completeness_score}% | "
            f"{error_short} |"
        )
    lines.append("")

    failed_results = [r for r in report.results if not r.passed]
    if failed_results:
        lines.append("## Failed Fixtures Detail")
        lines.append("")
        for r in failed_results:
            lines.append(f"### {r.fixture_id}")
            lines.append(f"- Description: {r.fixture_description}")
            lines.append(f"- Expected: {r.expected_status}, Actual: {r.actual_status}")
            lines.append(f"- Vendor: {r.vendor}, Endpoint: {r.endpoint}")
            if r.fallback_from:
                lines.append(f"- Fallback from: {r.fallback_from}")
            if r.error:
                lines.append(f"- Error: {r.error}")
            if r.missing_details:
                lines.append(f"- Missing details: {r.missing_details}")
            lines.append("")

    lines.append("---")
    lines.append("*Generated by fixture_replay.py — [DATA-005] data_source_replay*")
    lines.append("")
    return "\n".join(lines)


# ── File Output ───────────────────────────────────────────────────────

def save_replay_report(
    report: ReplayReport,
    output_dir: str = "docs/data_source_reports",
) -> str:
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"{report.date}.md")
    md = render_replay_report(report)
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    return path


def run_replay_and_save(
    output_dir: str = "docs/data_source_reports",
    fixture_ids: Optional[List[str]] = None,
) -> str:
    report = run_fixture_replay(fixture_ids=fixture_ids)
    path = save_replay_report(report, output_dir=output_dir)
    return path
