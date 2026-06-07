# [V-005] nightly_mandate_quality_report
"""夜间昊天候选质量日报与样本回放。

把 DATA-006、H-006、V-004 的结果合并成夜间昊天候选质量日报，第二天可以直接看到
候选池有没有选对方向、哪些是数据问题、哪些是策略问题。

功能：
  1. 从 H-006 回放结果读取候选池评估摘要
  2. 从 DATA-006 日报读取数据源健康状况
  3. 从 DATA-007 证据覆盖率读取候选可信度
  4. 合并生成统一日报，包含候选数量/类型/主题/等级分布、反证 TopN、数据覆盖率
  5. 候选为空时区分：无事件源/事件源失败/全部过滤/策略门槛过严

Usage:
    from tradingagents.tradeflow.mandate_quality_report import (
        build_mandate_quality_report,
        render_mandate_quality_report,
        run_mandate_quality_report,
    )
    report = build_mandate_quality_report()
    print(render_mandate_quality_report(report))
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from .mandate_replay_eval import (
    ALL_COUNTER_TYPES,
    COUNTER_TYPE_LABELS,
    ReplayReport,
    run_replay_evaluation,
    render_replay_report,
)


# ── Report Input Fixture ─────────────────────────────────────────────

@dataclass
class CandidateSummary:
    symbol: str = ""
    name: str = ""
    candidate_type: str = ""
    tier: str = ""
    mandate_topic: str = ""
    company_role: str = ""
    ambush_score: float = 0.0
    mandate_score_component: float = 0.0
    research_queue: str = ""
    evidence_coverage: float = 0.0
    evidence_quality_level: str = "UNKNOWN"
    overheat_flags: List[str] = field(default_factory=list)
    downgrade_reasons: List[str] = field(default_factory=list)
    counter_evidence_types: List[str] = field(default_factory=list)
    what_would_change_mind: List[str] = field(default_factory=list)
    topic_lifecycle_state: str = "UNKNOWN"
    need_deep_ta: bool = False
    watchlist_note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "candidate_type": self.candidate_type,
            "tier": self.tier,
            "mandate_topic": self.mandate_topic,
            "company_role": self.company_role,
            "ambush_score": round(self.ambush_score, 2),
            "mandate_score_component": round(self.mandate_score_component, 2),
            "research_queue": self.research_queue,
            "evidence_coverage": round(self.evidence_coverage, 3),
            "evidence_quality_level": self.evidence_quality_level,
            "overheat_flags": self.overheat_flags,
            "downgrade_reasons": self.downgrade_reasons,
            "counter_evidence_types": self.counter_evidence_types,
            "what_would_change_mind": self.what_would_change_mind,
            "topic_lifecycle_state": self.topic_lifecycle_state,
            "need_deep_ta": self.need_deep_ta,
            "watchlist_note": self.watchlist_note,
        }


@dataclass
class MandateQualityReport:
    date: str = ""
    generated_at: str = ""
    total_candidates: int = 0
    candidates_by_type: Dict[str, int] = field(default_factory=dict)
    candidates_by_tier: Dict[str, int] = field(default_factory=dict)
    candidates_by_topic: Dict[str, int] = field(default_factory=dict)
    candidates_by_lifecycle: Dict[str, int] = field(default_factory=dict)
    candidates_by_queue: Dict[str, int] = field(default_factory=dict)
    avg_evidence_coverage: float = 0.0
    evidence_quality_distribution: Dict[str, int] = field(default_factory=dict)
    overheat_count: int = 0
    downgrade_count: int = 0
    counter_evidence_top: List[Dict[str, Any]] = field(default_factory=list)
    need_deep_ta_count: int = 0
    candidates_needing_research: List[CandidateSummary] = field(default_factory=list)
    data_source_status: str = "NOT_RUN"
    data_source_summary: str = ""
    replay_status: str = "NOT_RUN"
    replay_summary: str = ""
    empty_reason: str = ""
    candidates: List[CandidateSummary] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "date": self.date,
            "generated_at": self.generated_at,
            "total_candidates": self.total_candidates,
            "candidates_by_type": self.candidates_by_type,
            "candidates_by_tier": self.candidates_by_tier,
            "candidates_by_topic": self.candidates_by_topic,
            "candidates_by_lifecycle": self.candidates_by_lifecycle,
            "candidates_by_queue": self.candidates_by_queue,
            "avg_evidence_coverage": round(self.avg_evidence_coverage, 3),
            "evidence_quality_distribution": self.evidence_quality_distribution,
            "overheat_count": self.overheat_count,
            "downgrade_count": self.downgrade_count,
            "counter_evidence_top": self.counter_evidence_top,
            "need_deep_ta_count": self.need_deep_ta_count,
            "candidates_needing_research": [c.to_dict() for c in self.candidates_needing_research],
            "data_source_status": self.data_source_status,
            "data_source_summary": self.data_source_summary,
            "replay_status": self.replay_status,
            "replay_summary": self.replay_summary,
            "empty_reason": self.empty_reason,
            "candidates": [c.to_dict() for c in self.candidates],
        }


# ── Distribution Helpers ─────────────────────────────────────────────

def _count_distribution(items: List[str]) -> Dict[str, int]:
    dist: Dict[str, int] = {}
    for item in items:
        dist[item] = dist.get(item, 0) + 1
    return dist


def _top_counter_evidences(
    candidates: List[CandidateSummary],
    limit: int = 5,
) -> List[Dict[str, Any]]:
    counter_counts: Dict[str, int] = {}
    counter_examples: Dict[str, str] = {}
    for c in candidates:
        for ct in c.counter_evidence_types:
            counter_counts[ct] = counter_counts.get(ct, 0) + 1
            if ct not in counter_examples:
                counter_examples[ct] = c.symbol
    sorted_items = sorted(counter_counts.items(), key=lambda x: x[1], reverse=True)
    result = []
    for ct, count in sorted_items[:limit]:
        label = COUNTER_TYPE_LABELS.get(ct, ct)
        result.append({
            "counter_type": ct,
            "label": label,
            "count": count,
            "example_symbol": counter_examples.get(ct, ""),
        })
    return result


# ── Empty Reason Classification ──────────────────────────────────────

EMPTY_NO_EVENT_SOURCE = "no_event_source"
EMPTY_EVENT_SOURCE_FAILED = "event_source_failed"
EMPTY_ALL_FILTERED = "all_filtered"
EMPTY_STRATEGY_TOO_STRICT = "strategy_too_strict"
EMPTY_UNKNOWN = "unknown"

EMPTY_REASON_LABELS = {
    EMPTY_NO_EVENT_SOURCE: "无事件源",
    EMPTY_EVENT_SOURCE_FAILED: "事件源失败",
    EMPTY_ALL_FILTERED: "全部被过滤",
    EMPTY_STRATEGY_TOO_STRICT: "策略门槛过严",
    EMPTY_UNKNOWN: "未知原因",
}


def classify_empty_reason(
    event_source_status: str = "",
    total_universe: int = 0,
    total_filtered: int = 0,
) -> str:
    if event_source_status == "FAILED":
        return EMPTY_EVENT_SOURCE_FAILED
    if event_source_status in ("OK", "PARTIAL") and total_universe == 0:
        return EMPTY_NO_EVENT_SOURCE
    if total_universe > 0 and total_filtered == total_universe:
        return EMPTY_ALL_FILTERED
    if total_universe > 0 and total_filtered < total_universe:
        return EMPTY_STRATEGY_TOO_STRICT
    return EMPTY_UNKNOWN


# ── Core Report Builder ──────────────────────────────────────────────

def build_mandate_quality_report(
    candidates: Optional[List[CandidateSummary]] = None,
    replay_report: Optional[ReplayReport] = None,
    data_source_status: str = "NOT_RUN",
    data_source_summary: str = "",
    event_source_status: str = "",
    total_universe: int = 0,
    total_filtered: int = 0,
    date: Optional[str] = None,
) -> MandateQualityReport:
    now = datetime.now()
    report = MandateQualityReport(
        date=date or now.strftime("%Y-%m-%d"),
        generated_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        data_source_status=data_source_status,
        data_source_summary=data_source_summary,
    )

    if candidates is None:
        candidates = []

    report.candidates = candidates
    report.total_candidates = len(candidates)

    if replay_report is not None:
        report.replay_status = "COMPLETED"
        report.replay_summary = (
            f"fixtures={replay_report.total_fixtures} "
            f"passed={replay_report.passed} "
            f"failed={replay_report.failed} "
            f"avg_score={replay_report.avg_verdict_score:.1f}"
        )

    if report.total_candidates == 0:
        report.empty_reason = classify_empty_reason(
            event_source_status=event_source_status,
            total_universe=total_universe,
            total_filtered=total_filtered,
        )
        return report

    types = [c.candidate_type or "UNKNOWN" for c in candidates]
    tiers = [c.tier or "UNKNOWN" for c in candidates]
    topics = [c.mandate_topic or "无主题" for c in candidates]
    lifecycles = [c.topic_lifecycle_state or "UNKNOWN" for c in candidates]
    queues = [c.research_queue or "UNKNOWN" for c in candidates]
    quality_levels = [c.evidence_quality_level or "UNKNOWN" for c in candidates]

    report.candidates_by_type = _count_distribution(types)
    report.candidates_by_tier = _count_distribution(tiers)
    report.candidates_by_topic = _count_distribution(topics)
    report.candidates_by_lifecycle = _count_distribution(lifecycles)
    report.candidates_by_queue = _count_distribution(queues)
    report.evidence_quality_distribution = _count_distribution(quality_levels)

    coverages = [c.evidence_coverage for c in candidates if c.evidence_coverage > 0]
    if coverages:
        report.avg_evidence_coverage = round(sum(coverages) / len(coverages), 3)

    report.overheat_count = sum(1 for c in candidates if c.overheat_flags)
    report.downgrade_count = sum(1 for c in candidates if c.downgrade_reasons)
    report.need_deep_ta_count = sum(1 for c in candidates if c.need_deep_ta)
    report.counter_evidence_top = _top_counter_evidences(candidates)

    report.candidates_needing_research = [
        c for c in candidates
        if c.need_deep_ta and c.research_queue in ("MIDLINE_POLICY", "TA_CONFIRM")
    ]

    return report


# ── Build from Replay ────────────────────────────────────────────────

def build_report_from_replay(
    fixture_ids: Optional[List[str]] = None,
    date: Optional[str] = None,
) -> MandateQualityReport:
    replay_report = run_replay_evaluation(fixture_ids=fixture_ids)

    candidates: List[CandidateSummary] = []
    for r in replay_report.results:
        cs = CandidateSummary(
            symbol=r.symbol,
            candidate_type=r.candidate_type,
            ambush_score=r.verdict_score,
            counter_evidence_types=[ce.counter_type for ce in r.counter_evidences],
        )
        candidates.append(cs)

    return build_mandate_quality_report(
        candidates=candidates,
        replay_report=replay_report,
        date=date or replay_report.date,
    )


# ── Markdown Rendering ───────────────────────────────────────────────

def render_mandate_quality_report(report: MandateQualityReport) -> str:
    lines: List[str] = []
    lines.append("# 昊天候选质量日报")
    lines.append("")
    lines.append(f"- **日期**: {report.date}")
    lines.append(f"- **生成时间**: {report.generated_at}")
    lines.append(f"- **候选总数**: {report.total_candidates}")
    lines.append("")

    # ── Empty Reason ──
    if report.total_candidates == 0:
        lines.append("## 无候选原因")
        lines.append("")
        label = EMPTY_REASON_LABELS.get(report.empty_reason, report.empty_reason)
        lines.append(f"**{label}**")
        lines.append("")
        if report.data_source_summary:
            lines.append(f"数据源状态: {report.data_source_summary}")
            lines.append("")
        if report.replay_summary:
            lines.append(f"回放状态: {report.replay_summary}")
            lines.append("")
        lines.append("---")
        lines.append("*Generated by mandate_quality_report.py — [V-005] nightly_mandate_quality_report*")
        lines.append("")
        return "\n".join(lines)

    # ── Candidate Distribution ──
    lines.append("## 候选分布")
    lines.append("")

    if report.candidates_by_type:
        lines.append("### 按候选类型")
        lines.append("")
        lines.append("| 类型 | 数量 |")
        lines.append("|------|------|")
        for ct, count in sorted(report.candidates_by_type.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"| {ct} | {count} |")
        lines.append("")

    if report.candidates_by_tier:
        lines.append("### 按等级")
        lines.append("")
        lines.append("| 等级 | 数量 |")
        lines.append("|------|------|")
        for tier, count in sorted(report.candidates_by_tier.items()):
            lines.append(f"| {tier} | {count} |")
        lines.append("")

    if report.candidates_by_topic:
        lines.append("### 按政策主题")
        lines.append("")
        lines.append("| 主题 | 数量 |")
        lines.append("|------|------|")
        for topic, count in sorted(report.candidates_by_topic.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"| {topic} | {count} |")
        lines.append("")

    if report.candidates_by_lifecycle:
        lines.append("### 按主题生命周期")
        lines.append("")
        lines.append("| 状态 | 数量 |")
        lines.append("|------|------|")
        for lc, count in sorted(report.candidates_by_lifecycle.items()):
            lines.append(f"| {lc} | {count} |")
        lines.append("")

    if report.candidates_by_queue:
        lines.append("### 按研究队列")
        lines.append("")
        lines.append("| 队列 | 数量 |")
        lines.append("|------|------|")
        for q, count in sorted(report.candidates_by_queue.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"| {q} | {count} |")
        lines.append("")

    # ── Evidence Coverage ──
    lines.append("## 数据覆盖率")
    lines.append("")
    lines.append(f"- **平均覆盖率**: {report.avg_evidence_coverage:.1%}")
    if report.evidence_quality_distribution:
        parts = []
        for ql in ("HIGH", "MEDIUM", "LOW", "CRITICAL", "UNKNOWN"):
            cnt = report.evidence_quality_distribution.get(ql, 0)
            if cnt > 0:
                parts.append(f"{ql}={cnt}")
        lines.append(f"- **质量分布**: {' | '.join(parts)}")
    lines.append("")

    # ── Counter Evidence / Overheat ──
    lines.append("## 反证与过热")
    lines.append("")
    lines.append(f"- **过热候选**: {report.overheat_count}")
    lines.append(f"- **降权候选**: {report.downgrade_count}")
    lines.append(f"- **需深研候选**: {report.need_deep_ta_count}")
    lines.append("")

    if report.counter_evidence_top:
        lines.append("### 反证 TopN")
        lines.append("")
        lines.append("| 反证类型 | 数量 | 样本标的 |")
        lines.append("|----------|------|----------|")
        for ce in report.counter_evidence_top:
            lines.append(f"| {ce['label']} | {ce['count']} | {ce['example_symbol']} |")
        lines.append("")

    # ── Candidates Needing Research ──
    if report.candidates_needing_research:
        lines.append("## 需人工研究候选")
        lines.append("")
        lines.append("| 标的 | 类型 | 主题 | ambush分 | 覆盖率 | 队列 |")
        lines.append("|------|------|------|---------|--------|------|")
        for c in report.candidates_needing_research:
            lines.append(
                f"| {c.symbol} | {c.candidate_type} | {c.mandate_topic or '--'} "
                f"| {c.ambush_score:.1f} | {c.evidence_coverage:.0%} "
                f"| {c.research_queue} |"
            )
        lines.append("")

    # ── Data Source ──
    if report.data_source_status != "NOT_RUN":
        lines.append("## 数据源状态")
        lines.append("")
        lines.append(f"- **状态**: {report.data_source_status}")
        if report.data_source_summary:
            lines.append(f"- **摘要**: {report.data_source_summary}")
        lines.append("")

    # ── Replay ──
    if report.replay_status != "NOT_RUN":
        lines.append("## 回放评估")
        lines.append("")
        lines.append(f"- **状态**: {report.replay_status}")
        if report.replay_summary:
            lines.append(f"- **摘要**: {report.replay_summary}")
        lines.append("")

    lines.append("---")
    lines.append("*Generated by mandate_quality_report.py — [V-005] nightly_mandate_quality_report*")
    lines.append("")
    return "\n".join(lines)


# ── File Output ──────────────────────────────────────────────────────

def save_mandate_quality_report(
    report: MandateQualityReport,
    output_dir: str = "docs/mandate_reports",
) -> str:
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"{report.date}.md")
    md = render_mandate_quality_report(report)
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    return path


def run_mandate_quality_report(
    candidates: Optional[List[CandidateSummary]] = None,
    replay_report: Optional[ReplayReport] = None,
    data_source_status: str = "NOT_RUN",
    data_source_summary: str = "",
    event_source_status: str = "",
    total_universe: int = 0,
    total_filtered: int = 0,
    output_dir: str = "docs/mandate_reports",
    date: Optional[str] = None,
) -> str:
    report = build_mandate_quality_report(
        candidates=candidates,
        replay_report=replay_report,
        data_source_status=data_source_status,
        data_source_summary=data_source_summary,
        event_source_status=event_source_status,
        total_universe=total_universe,
        total_filtered=total_filtered,
        date=date,
    )
    return save_mandate_quality_report(report, output_dir=output_dir)
