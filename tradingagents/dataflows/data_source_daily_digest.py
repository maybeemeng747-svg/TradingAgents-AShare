# [DATA-006] data_source_report_daily
"""
数据源质量报告接入夜间日报。

读取 DATA-005 fixture replay 结果或 docs/data_source_reports/ 下的已存报告，
按数据类型聚合为 OK/PARTIAL/FAILED 摘要，评估对 TradeFlow 候选池和 TA readiness 的影响。

功能：
  1. 从 ReplayReport 或文件系统读取 replay 结果
  2. 按数据类型聚合：行情、实时补丁、资金流、龙虎榜、公告/研报、raw_evidence contract
  3. 对每类输出：OK/PARTIAL/FAILED、失败样本、是否影响 TradeFlow 候选、是否影响 TA readiness
  4. 当 replay 不存在时明确显示 NOT_RUN，不能假装通过

Usage:
    from tradingagents.dataflows.data_source_daily_digest import (
        build_daily_digest,
        render_daily_digest,
        read_replay_report_from_file,
    )
    digest = build_daily_digest(report)
    print(render_daily_digest(digest))
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from .fixture_replay import (
    ReplayReport,
    ReplayResult,
    render_replay_report,
    run_fixture_replay,
)


# ── Data Type Categories ─────────────────────────────────────────────

_DATA_TYPE_CATEGORIES: Dict[str, str] = {
    "ohlcv": "行情/OHLCV",
    "realtime_quotes": "实时补丁",
    "fund_flow": "资金流",
    "board_fund_flow": "板块资金流",
    "lhb": "龙虎榜",
    "news": "新闻",
    "global_news": "全球新闻",
    "notice": "公告/研报",
    "report": "研报/评级",
    "financials": "财务数据",
    "insider": "内部交易",
    "zt_pool": "涨停池",
    "hot_stocks": "热门股票",
}


# ── Impact Assessment ────────────────────────────────────────────────

_IMPACT_ON_TRADEFLOW: Dict[str, str] = {
    "ohlcv": "high",
    "realtime_quotes": "medium",
    "fund_flow": "high",
    "board_fund_flow": "medium",
    "lhb": "medium",
    "news": "low",
    "global_news": "low",
    "notice": "medium",
    "report": "low",
    "financials": "low",
    "insider": "low",
    "zt_pool": "low",
    "hot_stocks": "low",
}

_IMPACT_ON_TA_READINESS: Dict[str, str] = {
    "ohlcv": "high",
    "realtime_quotes": "medium",
    "fund_flow": "high",
    "board_fund_flow": "low",
    "lhb": "medium",
    "news": "medium",
    "global_news": "low",
    "notice": "medium",
    "report": "medium",
    "financials": "high",
    "insider": "low",
    "zt_pool": "low",
    "hot_stocks": "low",
}


# ── Data Models ──────────────────────────────────────────────────────

@dataclass
class DataTypeHealth:
    data_type: str
    label: str
    status: str = "NOT_RUN"
    total_fixtures: int = 0
    passed: int = 0
    failed: int = 0
    failure_samples: List[str] = field(default_factory=list)
    completeness_avg: float = 0.0
    has_fallback: bool = False
    fallback_samples: List[str] = field(default_factory=list)
    impact_tradeflow: str = "unknown"
    impact_ta_readiness: str = "unknown"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "data_type": self.data_type,
            "label": self.label,
            "status": self.status,
            "total_fixtures": self.total_fixtures,
            "passed": self.passed,
            "failed": self.failed,
            "failure_samples": self.failure_samples,
            "completeness_avg": round(self.completeness_avg, 1),
            "has_fallback": self.has_fallback,
            "fallback_samples": self.fallback_samples,
            "impact_tradeflow": self.impact_tradeflow,
            "impact_ta_readiness": self.impact_ta_readiness,
        }


@dataclass
class DailyDigest:
    date: str
    generated_at: str
    replay_status: str = "NOT_RUN"
    replay_report_available: bool = False
    replay_report_path: Optional[str] = None
    total_fixtures: int = 0
    total_passed: int = 0
    total_failed: int = 0
    all_passed: bool = True
    data_type_healths: List[DataTypeHealth] = field(default_factory=list)
    failure_types: Dict[str, int] = field(default_factory=dict)
    overall_tradeflow_impact: str = "none"
    overall_ta_impact: str = "none"
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "date": self.date,
            "generated_at": self.generated_at,
            "replay_status": self.replay_status,
            "replay_report_available": self.replay_report_available,
            "replay_report_path": self.replay_report_path,
            "total_fixtures": self.total_fixtures,
            "total_passed": self.total_passed,
            "total_failed": self.total_failed,
            "all_passed": self.all_passed,
            "data_type_healths": [h.to_dict() for h in self.data_type_healths],
            "failure_types": self.failure_types,
            "overall_tradeflow_impact": self.overall_tradeflow_impact,
            "overall_ta_impact": self.overall_ta_impact,
            "warnings": self.warnings,
        }


# ── Classification Helpers ───────────────────────────────────────────

def _classify_status(passed: int, total: int) -> str:
    if total == 0:
        return "NOT_RUN"
    if passed == total:
        return "OK"
    if passed == 0:
        return "FAILED"
    return "PARTIAL"


def _assess_overall_impact(healths: List[DataTypeHealth], impact_field: str) -> str:
    impacts = set()
    for h in healths:
        if h.status == "FAILED":
            impacts.add(getattr(h, impact_field))
        elif h.status == "PARTIAL":
            val = getattr(h, impact_field)
            if val in ("high", "medium"):
                impacts.add(val)
    if "high" in impacts:
        return "high"
    if "medium" in impacts:
        return "medium"
    if "low" in impacts:
        return "low"
    return "none"


# ── Core Logic ───────────────────────────────────────────────────────

def build_daily_digest(
    report: Optional[ReplayReport] = None,
    report_path: Optional[str] = None,
) -> DailyDigest:
    now = datetime.now()
    digest = DailyDigest(
        date=now.strftime("%Y-%m-%d"),
        generated_at=now.strftime("%Y-%m-%d %H:%M:%S"),
    )

    if report_path is not None:
        digest.replay_report_path = report_path

    if report is None and report_path is not None:
        report = read_replay_report_from_file(report_path)

    if report is None:
        digest.replay_status = "NOT_RUN"
        digest.replay_report_available = False
        digest.all_passed = False
        digest.warnings.append(
            "DATA-005 replay 尚未运行，数据源健康状况未知。"
            "请先运行 run_fixture_replay() 或 run_replay_and_save()。"
        )
        return digest

    digest.replay_status = "COMPLETED"
    digest.replay_report_available = True
    digest.total_fixtures = report.total_fixtures
    digest.total_passed = report.passed
    digest.total_failed = report.failed
    digest.all_passed = report.all_passed
    digest.failure_types = dict(report.failure_types)

    by_type: Dict[str, List[ReplayResult]] = {}
    for r in report.results:
        by_type.setdefault(r.data_type, []).append(r)

    for dt, results in sorted(by_type.items()):
        passed_count = sum(1 for r in results if r.passed)
        failed_count = len(results) - passed_count
        status = _classify_status(passed_count, len(results))

        failure_samples = []
        fallback_samples = []
        completeness_scores = []
        has_fallback = False

        for r in results:
            completeness_scores.append(r.completeness_score)
            if not r.passed:
                sample = f"{r.fixture_id}: expected={r.expected_status} actual={r.actual_status}"
                if r.error:
                    sample += f" error={r.error[:80]}"
                failure_samples.append(sample)
            if r.is_fallback:
                has_fallback = True
                fb = f"{r.fixture_id}: fallback from {r.fallback_from}"
                fallback_samples.append(fb)

        avg_completeness = (
            sum(completeness_scores) / len(completeness_scores)
            if completeness_scores else 0.0
        )

        health = DataTypeHealth(
            data_type=dt,
            label=_DATA_TYPE_CATEGORIES.get(dt, dt),
            status=status,
            total_fixtures=len(results),
            passed=passed_count,
            failed=failed_count,
            failure_samples=failure_samples[:5],
            completeness_avg=avg_completeness,
            has_fallback=has_fallback,
            fallback_samples=fallback_samples[:3],
            impact_tradeflow=_IMPACT_ON_TRADEFLOW.get(dt, "unknown"),
            impact_ta_readiness=_IMPACT_ON_TA_READINESS.get(dt, "unknown"),
        )
        digest.data_type_healths.append(health)

    digest.overall_tradeflow_impact = _assess_overall_impact(
        digest.data_type_healths, "impact_tradeflow"
    )
    digest.overall_ta_impact = _assess_overall_impact(
        digest.data_type_healths, "impact_ta_readiness"
    )

    if not digest.all_passed:
        high_impact = [
            h for h in digest.data_type_healths
            if h.status in ("FAILED", "PARTIAL")
            and h.impact_tradeflow == "high"
        ]
        if high_impact:
            types = ", ".join(h.label for h in high_impact)
            digest.warnings.append(
                f"高影响数据源异常: {types} — 可能影响 TradeFlow 候选池质量。"
            )

        ta_impact = [
            h for h in digest.data_type_healths
            if h.status in ("FAILED", "PARTIAL")
            and h.impact_ta_readiness == "high"
        ]
        if ta_impact:
            types = ", ".join(h.label for h in ta_impact)
            digest.warnings.append(
                f"TA readiness 受影响: {types} — 深度分析报告可信度可能下降。"
            )

    return digest


def read_replay_report_from_file(path: str) -> Optional[ReplayReport]:
    if not os.path.exists(path):
        return None
    try:
        report = _parse_report_file(path)
        return report
    except Exception:
        return None


def find_latest_replay_report(
    reports_dir: str = "docs/data_source_reports",
    target_date: Optional[str] = None,
) -> Optional[str]:
    if not os.path.isdir(reports_dir):
        return None
    if target_date:
        candidate = os.path.join(reports_dir, f"{target_date}.md")
        if os.path.exists(candidate):
            return candidate
        return None

    files = []
    for f in os.listdir(reports_dir):
        if f.endswith(".md"):
            match = re.match(r"(\d{4}-\d{2}-\d{2})\.md", f)
            if match:
                files.append((match.group(1), os.path.join(reports_dir, f)))
    if not files:
        return None
    files.sort(key=lambda x: x[0], reverse=True)
    return files[0][1]


def _parse_report_file(path: str) -> ReplayReport:
    with open(path, encoding="utf-8") as f:
        content = f.read()

    date_match = re.search(r"\*\*Date\*\*:\s*(\d{4}-\d{2}-\d{2})", content)
    run_at_match = re.search(r"\*\*Run at\*\*:\s*(.+)", content)
    result_match = re.search(r"\*\*Result\*\*:\s*(.+)", content)

    report = ReplayReport(
        run_at=run_at_match.group(1).strip() if run_at_match else "",
        date=date_match.group(1) if date_match else "",
        all_passed="ALL PASSED" in (result_match.group(1) if result_match else ""),
    )

    summary_match = re.search(
        r"\| Total fixtures \| (\d+) \|.*?"
        r"\| Passed \| (\d+) \|.*?"
        r"\| Failed \| (\d+) \|",
        content,
        re.DOTALL,
    )
    if summary_match:
        report.total_fixtures = int(summary_match.group(1))
        report.passed = int(summary_match.group(2))
        report.failed = int(summary_match.group(3))

    fixture_rows = re.findall(
        r"\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|",
        content,
    )
    for row in fixture_rows:
        fixture_id = row[0]
        data_type = row[1]
        expected = row[2]
        actual = row[3]
        passed_str = row[4]
        report.results.append(ReplayResult(
            fixture_id=fixture_id,
            fixture_description="",
            data_type=data_type,
            expected_status=expected,
            actual_status=actual,
            passed=passed_str == "PASS",
            vendor="",
            endpoint="",
            is_fallback=False,
            fallback_from=None,
            error=None,
            completeness_score=0,
            missing_details={},
        ))

    return report


# ── Markdown Rendering ────────────────────────────────────────────────

def render_daily_digest(digest: DailyDigest) -> str:
    lines: List[str] = []
    lines.append("## 数据源健康日报")
    lines.append("")

    if digest.replay_status == "NOT_RUN":
        lines.append("> **NOT_RUN**: DATA-005 fixture replay 未执行，数据源健康状况未知。")
        lines.append(">")
        for w in digest.warnings:
            lines.append(f"> {w}")
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("*Generated by data_source_daily_digest.py — [DATA-006] data_source_report_daily*")
        lines.append("")
        return "\n".join(lines)

    status_icon = "OK" if digest.all_passed else "HAS ISSUES"
    lines.append(f"- **日期**: {digest.date}")
    lines.append(f"- **Replay 状态**: {digest.replay_status}")
    lines.append(f"- **总体结果**: {status_icon}")
    lines.append(f"- **Fixtures**: {digest.total_passed} passed / {digest.total_failed} failed / {digest.total_fixtures} total")
    lines.append("")

    if digest.data_type_healths:
        lines.append("### 按数据类型")
        lines.append("")
        lines.append("| 数据类型 | 状态 | 通过/总数 | 完整度 | 影响TradeFlow | 影响TA |")
        lines.append("|----------|------|-----------|--------|---------------|--------|")
        for h in digest.data_type_healths:
            status_mark = _status_mark(h.status)
            tf_mark = _impact_mark(h.impact_tradeflow) if h.status != "OK" else "-"
            ta_mark = _impact_mark(h.impact_ta_readiness) if h.status != "OK" else "-"
            lines.append(
                f"| {h.label} | {status_mark} | {h.passed}/{h.total_fixtures} | "
                f"{h.completeness_avg:.0f}% | {tf_mark} | {ta_mark} |"
            )
        lines.append("")

    if digest.failure_types:
        lines.append("### 失败类型")
        lines.append("")
        for ft, count in sorted(digest.failure_types.items()):
            lines.append(f"- **{ft}**: {count}")
        lines.append("")

    failed_healths = [h for h in digest.data_type_healths if h.status in ("FAILED", "PARTIAL")]
    if failed_healths:
        lines.append("### 失败/部分失败详情")
        lines.append("")
        for h in failed_healths:
            lines.append(f"#### {h.label} ({h.status})")
            if h.failure_samples:
                for s in h.failure_samples:
                    lines.append(f"- {s}")
            if h.has_fallback:
                lines.append(f"- Fallback: {'; '.join(h.fallback_samples)}")
            lines.append("")

    if digest.warnings:
        lines.append("### 警告")
        lines.append("")
        for w in digest.warnings:
            lines.append(f"- {w}")
        lines.append("")

    if digest.replay_report_path:
        lines.append(f"> 完整 replay 报告: `{digest.replay_report_path}`")
        lines.append("")

    lines.append("---")
    lines.append("*Generated by data_source_daily_digest.py — [DATA-006] data_source_report_daily*")
    lines.append("")
    return "\n".join(lines)


def _status_mark(status: str) -> str:
    if status == "OK":
        return "OK"
    if status == "PARTIAL":
        return "PARTIAL"
    if status == "FAILED":
        return "FAILED"
    return "NOT_RUN"


def _impact_mark(impact: str) -> str:
    if impact == "high":
        return "HIGH"
    if impact == "medium":
        return "MEDIUM"
    if impact == "low":
        return "LOW"
    return "-"


def build_digest_section_for_nightly_report(
    reports_dir: str = "docs/data_source_reports",
    target_date: Optional[str] = None,
) -> str:
    report_path = find_latest_replay_report(reports_dir, target_date=target_date)
    if report_path:
        report = read_replay_report_from_file(report_path)
        digest = build_daily_digest(report=report, report_path=report_path)
    else:
        digest = build_daily_digest(report=None)
    return render_daily_digest(digest)


def run_digest_and_append_to_report(
    nightly_report_path: str,
    reports_dir: str = "docs/data_source_reports",
    target_date: Optional[str] = None,
) -> bool:
    section = build_digest_section_for_nightly_report(reports_dir, target_date=target_date)
    if not nightly_report_path or not os.path.exists(nightly_report_path):
        return False
    with open(nightly_report_path, encoding="utf-8") as f:
        content = f.read()
    if "数据源健康日报" in content:
        return False
    with open(nightly_report_path, "a", encoding="utf-8") as f:
        f.write("\n")
        f.write(section)
    return True
