# [H-015] mandate_daily_report
"""Mandate daily report for the Haotian / policy-left-side radar.

This module turns the topic heatmap into a daily, auditable summary. It does
not call LLMs and does not emit trade actions; it only explains topic focus,
candidate entry/exit reasons and evidence gaps.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


_DEFAULT_REPORT_DIR = "docs/mandate_daily_reports"


@dataclass
class MandateDailyCandidate:
    symbol: str = ""
    name: str = ""
    topic: str = ""
    company_role: str = ""
    candidate_type: str = ""
    tier: str = ""
    mandate_score: float = 0.0
    latest_date: str = ""
    entry_reason: str = ""
    evidence_gaps: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "topic": self.topic,
            "company_role": self.company_role,
            "candidate_type": self.candidate_type,
            "tier": self.tier,
            "mandate_score": self.mandate_score,
            "latest_date": self.latest_date,
            "entry_reason": self.entry_reason,
            "evidence_gaps": list(self.evidence_gaps),
        }


@dataclass
class MandateDailyReport:
    as_of: str = ""
    generated_at: str = ""
    rising_topics: List[dict] = field(default_factory=list)
    cooling_topics: List[dict] = field(default_factory=list)
    main_candidates: List[MandateDailyCandidate] = field(default_factory=list)
    observation_candidates: List[MandateDailyCandidate] = field(default_factory=list)
    entry_reasons: List[dict] = field(default_factory=list)
    exit_reasons: List[dict] = field(default_factory=list)
    evidence_gaps: List[dict] = field(default_factory=list)
    markdown: str = ""
    source: str = "generated"

    def to_dict(self) -> dict:
        return {
            "as_of": self.as_of,
            "generated_at": self.generated_at,
            "rising_topics": self.rising_topics,
            "cooling_topics": self.cooling_topics,
            "main_candidates": [c.to_dict() for c in self.main_candidates],
            "observation_candidates": [c.to_dict() for c in self.observation_candidates],
            "entry_reasons": self.entry_reasons,
            "exit_reasons": self.exit_reasons,
            "evidence_gaps": self.evidence_gaps,
            "markdown": self.markdown,
            "source": self.source,
        }


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _topic_window(topic: dict, key: str = "7") -> dict:
    windows = topic.get("windows") or {}
    return windows.get(key) or windows.get(int(key)) or {}


def _topic_score(topic: dict) -> float:
    w7 = _topic_window(topic, "7")
    return float(topic.get("peak_heat") or 0) + float(w7.get("evidence_count") or 0) * 2


def _candidate_score(candidate: dict) -> float:
    return float(candidate.get("mandate_score") or candidate.get("composite_score") or 0)


def _entry_reason(topic: dict, candidate: dict) -> str:
    parts = [
        f"主题{topic.get('heat_trend_label') or '未知'}",
        f"状态{topic.get('topic_status_label') or '未知'}",
    ]
    role = candidate.get("company_role") or ""
    if role:
        parts.append(f"角色：{role}")
    score = _candidate_score(candidate)
    if score:
        parts.append(f"昊天分 {score:.1f}")
    w7 = _topic_window(topic, "7")
    evidence = w7.get("evidence_count", 0)
    if evidence:
        parts.append(f"近7天证据 {evidence}")
    return "；".join(parts)


def _exit_reason(topic: dict) -> str:
    gaps = topic.get("counter_evidence_gaps") or []
    flags = topic.get("overheat_flags") or []
    if topic.get("heat_trend") == "COOLING":
        return "主题热度降温，候选需要降为观察或等待新证据。"
    if topic.get("topic_status") == "RECEDING":
        return "主题进入退潮状态，旧候选需要复核是否继续留池。"
    if flags:
        return f"存在过热标记：{'、'.join(flags[:3])}。"
    if gaps:
        return f"存在反证缺口：{'、'.join(gaps[:3])}。"
    return "最新窗口未见有效候选或新增证据，暂不作为主线跟踪。"


def _topic_summary(topic: dict) -> dict:
    return {
        "topic": topic.get("topic", ""),
        "status": topic.get("topic_status", ""),
        "status_label": topic.get("topic_status_label", ""),
        "heat_trend": topic.get("heat_trend", ""),
        "heat_trend_label": topic.get("heat_trend_label", ""),
        "state_change_label": topic.get("state_change_label", ""),
        "latest_date": topic.get("latest_date", ""),
        "peak_heat": topic.get("peak_heat", 0),
        "candidate_count": len(topic.get("candidates") or []),
        "evidence_summary": topic.get("evidence_summary", ""),
    }


def build_mandate_daily_report(heatmap: dict, *, as_of: str = "") -> MandateDailyReport:
    """Build a daily mandate report from a topic heatmap response."""
    as_of = as_of or heatmap.get("as_of") or datetime.now().strftime("%Y-%m-%d")
    topics = list(heatmap.get("topics") or [])
    active_topics = [t for t in topics if t.get("candidates") or t.get("heat_curve")]

    rising = [
        t for t in active_topics
        if t.get("heat_trend") == "RISING" or t.get("state_change_positive")
    ]
    cooling = [
        t for t in active_topics
        if t.get("heat_trend") == "COOLING" or t.get("topic_status") == "RECEDING"
    ]
    rising = sorted(rising, key=_topic_score, reverse=True)[:5]
    cooling = sorted(cooling, key=_topic_score, reverse=True)[:5]

    main_candidates: List[MandateDailyCandidate] = []
    observation_candidates: List[MandateDailyCandidate] = []
    entry_reasons: List[dict] = []
    evidence_gaps: List[dict] = []

    for topic in active_topics:
        topic_name = topic.get("topic", "")
        gaps = list(topic.get("counter_evidence_gaps") or [])
        if gaps:
            evidence_gaps.append({"topic": topic_name, "gaps": gaps[:5]})

        for c in topic.get("candidates") or []:
            item = MandateDailyCandidate(
                symbol=c.get("symbol", ""),
                name=c.get("name", ""),
                topic=topic_name,
                company_role=c.get("company_role", ""),
                candidate_type=c.get("candidate_type", ""),
                tier=c.get("tier", ""),
                mandate_score=_candidate_score(c),
                latest_date=c.get("latest_date", ""),
                entry_reason=_entry_reason(topic, c),
                evidence_gaps=gaps[:5],
            )
            target = main_candidates if (
                topic.get("is_left_side") or topic.get("is_confirmed")
            ) and len(main_candidates) < 8 else observation_candidates
            target.append(item)
            entry_reasons.append({
                "symbol": item.symbol,
                "name": item.name,
                "topic": item.topic,
                "reason": item.entry_reason,
            })

    main_candidates.sort(key=lambda c: c.mandate_score, reverse=True)
    observation_candidates.sort(key=lambda c: c.mandate_score, reverse=True)

    exit_reasons = [
        {"topic": t.get("topic", ""), "reason": _exit_reason(t)}
        for t in cooling
    ]
    # Include inactive predefined topics only when they had a prior signal.
    for topic in active_topics:
        if topic in cooling:
            continue
        if not topic.get("candidates") and topic.get("latest_date"):
            exit_reasons.append({"topic": topic.get("topic", ""), "reason": _exit_reason(topic)})
        if len(exit_reasons) >= 8:
            break

    report = MandateDailyReport(
        as_of=as_of,
        generated_at=_now_iso(),
        rising_topics=[_topic_summary(t) for t in rising],
        cooling_topics=[_topic_summary(t) for t in cooling],
        main_candidates=main_candidates[:8],
        observation_candidates=observation_candidates[:12],
        entry_reasons=entry_reasons[:20],
        exit_reasons=exit_reasons[:8],
        evidence_gaps=evidence_gaps[:12],
    )
    report.markdown = render_mandate_daily_report_markdown(report)
    return report


def render_mandate_daily_report_markdown(report: MandateDailyReport) -> str:
    """Render the mandate daily report as Markdown without trade commands."""
    lines: List[str] = []
    lines.append(f"# 昊天主题日报 {report.as_of}")
    lines.append("")
    lines.append(
        f"> 升温主题 {len(report.rising_topics)} | 降温主题 {len(report.cooling_topics)} | "
        f"主候选 {len(report.main_candidates)} | 观察候选 {len(report.observation_candidates)}"
    )
    lines.append("")
    lines.append("## 升温主题")
    lines.append("")
    if report.rising_topics:
        for t in report.rising_topics:
            lines.append(
                f"- **{t['topic']}**：{t.get('heat_trend_label') or '未知'} / "
                f"{t.get('status_label') or '未知'}，{t.get('state_change_label') or '持平'}；"
                f"候选 {t.get('candidate_count', 0)}，峰值 {t.get('peak_heat', 0)}。"
            )
    else:
        lines.append("- 暂无升温主题。")
    lines.append("")
    lines.append("## 降温主题")
    lines.append("")
    if report.cooling_topics:
        for t in report.cooling_topics:
            lines.append(
                f"- **{t['topic']}**：{t.get('heat_trend_label') or '未知'} / "
                f"{t.get('status_label') or '未知'}；{t.get('state_change_label') or '持平'}。"
            )
    else:
        lines.append("- 暂无降温主题。")
    lines.append("")
    lines.append("## 主候选")
    lines.append("")
    if report.main_candidates:
        lines.append("| 代码 | 名称 | 主题 | 角色 | 昊天分 | 入池原因 |")
        lines.append("|------|------|------|------|--------|----------|")
        for c in report.main_candidates:
            lines.append(
                f"| {c.symbol} | {c.name} | {c.topic} | {c.company_role or '-'} | "
                f"{c.mandate_score:.1f} | {c.entry_reason} |"
            )
    else:
        lines.append("- 暂无主候选。")
    lines.append("")
    lines.append("## 观察候选")
    lines.append("")
    if report.observation_candidates:
        lines.append("| 代码 | 名称 | 主题 | 昊天分 | 观察原因 |")
        lines.append("|------|------|------|--------|----------|")
        for c in report.observation_candidates[:10]:
            lines.append(
                f"| {c.symbol} | {c.name} | {c.topic} | {c.mandate_score:.1f} | {c.entry_reason} |"
            )
    else:
        lines.append("- 暂无观察候选。")
    lines.append("")
    lines.append("## 出池/降级原因")
    lines.append("")
    if report.exit_reasons:
        for item in report.exit_reasons:
            lines.append(f"- **{item.get('topic', '')}**：{item.get('reason', '')}")
    else:
        lines.append("- 暂无需要降级或出池的主题。")
    lines.append("")
    lines.append("## 证据缺口")
    lines.append("")
    if report.evidence_gaps:
        for item in report.evidence_gaps:
            lines.append(f"- **{item.get('topic', '')}**：{'、'.join(item.get('gaps', [])[:5])}")
    else:
        lines.append("- 暂无显式反证缺口。")
    lines.append("")
    lines.append("> 本日报只做主题雷达摘要，不构成交易建议；后续动作仍需 TA 中线/短线分析确认。")
    return "\n".join(lines)


def save_mandate_daily_report(
    report: MandateDailyReport,
    output_dir: str = _DEFAULT_REPORT_DIR,
) -> tuple[str, str]:
    os.makedirs(output_dir, exist_ok=True)
    md_path = os.path.join(output_dir, f"mandate-{report.as_of}.md")
    json_path = os.path.join(output_dir, f"mandate-{report.as_of}.json")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(report.markdown)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
    return md_path, json_path


def find_latest_mandate_daily_report(
    reports_dir: str = _DEFAULT_REPORT_DIR,
) -> Optional[str]:
    if not os.path.isdir(reports_dir):
        return None
    files = [
        os.path.join(reports_dir, f)
        for f in os.listdir(reports_dir)
        if f.startswith("mandate-") and f.endswith(".json")
    ]
    if not files:
        return None
    files.sort(reverse=True)
    return files[0]


def load_latest_mandate_daily_report(
    reports_dir: str = _DEFAULT_REPORT_DIR,
) -> Optional[dict]:
    path = find_latest_mandate_daily_report(reports_dir)
    if not path:
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    data["source"] = "file"
    data["path"] = path
    return data
