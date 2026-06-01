# [H-008] mandate_watchlist_note
"""Watchlist Note Summary — compresses mandate topic, beneficiary path,
benefit score, consensus score, expected window and evidence gaps into
a short, stable note for the watchlist and frontend observation list.

Output format: `topic｜利好X.X｜共识N｜窗口W｜缺口:a/b`
- When no evidence: show `缺证据`, never fake benefit scores.
- When user note already exists: preserve, only append suggestion.

Constraints:
- No buy/sell strong suggestions.
- No overwriting user notes — only append or generate suggestion.
- No fabricating benefit scores from thin air.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


_WINDOW_MAP = {
    "POLICY_AMBUSH": "中线",
    "POLICY_CONFIRM": "短线",
    "TECH_TRADE": "短线",
    "EVENT_WATCH": "观察",
    "PSEUDO_POLICY": "观察",
    "OVERHEATED_AVOID": "规避",
}

_CONSENSUS_THRESHOLD_HIGH = 70.0
_CONSENSUS_THRESHOLD_LOW = 30.0
_BENEFIT_THRESHOLD_HIGH = 60.0
_BENEFIT_THRESHOLD_LOW = 20.0

_FORBIDDEN_NOTE_WORDS = {"买入", "卖出", "清仓", "满仓", "梭哈", "加仓", "减仓"}


@dataclass
class WatchlistNoteResult:
    topic: str = ""
    benefit_score: float = 0.0
    consensus_score: float = 0.0
    expected_window: str = ""
    evidence_gap: list[str] = field(default_factory=list)
    note_summary: str = ""
    has_evidence: bool = False
    suggested_note: str = ""

    def to_dict(self) -> dict:
        return {
            "topic": self.topic,
            "benefit_score": round(self.benefit_score, 1),
            "consensus_score": round(self.consensus_score, 1),
            "expected_window": self.expected_window,
            "evidence_gap": self.evidence_gap,
            "note_summary": self.note_summary,
            "has_evidence": self.has_evidence,
            "suggested_note": self.suggested_note,
        }


def _compute_benefit_score(
    mandate_score_component: float = 0.0,
    beneficiary_score_component: float = 0.0,
    ambush_score: float = 0.0,
) -> float:
    raw = (
        mandate_score_component * 0.35
        + beneficiary_score_component * 0.35
        + ambush_score * 0.30
    )
    return round(min(max(raw, 0.0), 100.0), 1)


def _compute_consensus_score(
    mandate_score_component: float = 0.0,
    narrative_score: float = 0.0,
    composite_score: float = 0.0,
    positive_category_count: int = 0,
    resonance_count: int = 0,
) -> float:
    parts: list[float] = []
    if mandate_score_component > 0:
        parts.append(min(mandate_score_component, 100.0))
    if narrative_score > 0:
        parts.append(min(narrative_score, 100.0))
    if composite_score > 0:
        parts.append(min(composite_score, 100.0))
    if not parts:
        if resonance_count >= 3:
            return 55.0
        elif resonance_count >= 2:
            return 40.0
        elif resonance_count >= 1:
            return 25.0
        return 0.0
    base = sum(parts) / len(parts)
    if positive_category_count >= 3:
        base = min(base * 1.1, 100.0)
    elif positive_category_count >= 2:
        base = min(base * 1.05, 100.0)
    if resonance_count >= 2:
        base = min(base + 5.0 * resonance_count, 100.0)
    return round(min(max(base, 0.0), 100.0), 1)


def _compute_evidence_gap(
    has_policy_tags: bool = False,
    has_beneficiary_path: bool = False,
    has_company_role: bool = False,
    has_fund_flow: bool = False,
    has_narrative: bool = False,
    has_tech_signal: bool = False,
    data_completeness: float = 0.0,
    missing_evidence: Optional[list[str]] = None,
) -> list[str]:
    gaps: list[str] = []
    if not has_policy_tags:
        gaps.append("政策证据")
    if not has_beneficiary_path:
        gaps.append("受益路径")
    if not has_company_role:
        gaps.append("公司定位")
    if not has_fund_flow:
        gaps.append("资金验证")
    if not has_narrative:
        gaps.append("叙事质量")
    if not has_tech_signal:
        gaps.append("技术确认")
    if data_completeness < 0.5:
        gaps.append("数据完整度低")
    for ev in (missing_evidence or []):
        if ev and ev not in gaps:
            gaps.append(ev)
            if len(gaps) >= 6:
                break
    return gaps[:6]


def _format_window(candidate_type: str = "", expected_window: str = "") -> str:
    if expected_window:
        return expected_window
    return _WINDOW_MAP.get(candidate_type, "观察")


def _sanitize_note(text: str) -> str:
    for w in _FORBIDDEN_NOTE_WORDS:
        text = text.replace(w, "***")
    return text


def generate_watchlist_note(
    mandate_topic: str = "",
    candidate_type: str = "",
    mandate_score_component: float = 0.0,
    beneficiary_score_component: float = 0.0,
    ambush_score: float = 0.0,
    narrative_score: float = 0.0,
    composite_score: float = 0.0,
    positive_category_count: int = 0,
    resonance_count: int = 0,
    beneficiary_path: Optional[list[str]] = None,
    company_role: str = "",
    policy_tags: Optional[list[str]] = None,
    fund_flow_anomaly_tags: Optional[list[str]] = None,
    strategy_tags: Optional[list[str]] = None,
    data_completeness: float = 0.0,
    missing_evidence: Optional[list[str]] = None,
    expected_window: str = "",
    existing_note: str = "",
) -> WatchlistNoteResult:
    has_policy_tags = bool(policy_tags)
    has_beneficiary_path = bool(beneficiary_path)
    has_company_role = bool(company_role and company_role not in ("", "UNKNOWN"))
    has_fund_flow = bool(fund_flow_anomaly_tags)
    has_narrative = narrative_score > 0
    tags = set(strategy_tags or [])
    has_tech_signal = "VCP" in tags or "PULLBACK_SUPPORT" in tags

    benefit_score = _compute_benefit_score(
        mandate_score_component=mandate_score_component,
        beneficiary_score_component=beneficiary_score_component,
        ambush_score=ambush_score,
    )
    consensus_score = _compute_consensus_score(
        mandate_score_component=mandate_score_component,
        narrative_score=narrative_score,
        composite_score=composite_score,
        positive_category_count=positive_category_count,
        resonance_count=resonance_count,
    )
    evidence_gap = _compute_evidence_gap(
        has_policy_tags=has_policy_tags,
        has_beneficiary_path=has_beneficiary_path,
        has_company_role=has_company_role,
        has_fund_flow=has_fund_flow,
        has_narrative=has_narrative,
        has_tech_signal=has_tech_signal,
        data_completeness=data_completeness,
        missing_evidence=missing_evidence,
    )
    window = _format_window(candidate_type, expected_window)

    has_any_evidence = has_policy_tags or has_beneficiary_path or has_company_role or has_fund_flow or has_narrative or has_tech_signal

    topic = mandate_topic or (policy_tags[0] if policy_tags else "")
    if not topic:
        if candidate_type == "TECH_TRADE":
            topic = "技术形态"
        elif candidate_type == "OVERHEATED_AVOID":
            topic = "过热规避"
        else:
            topic = "未定主题"

    if not has_any_evidence:
        note_summary = f"{topic}｜缺证据"
    else:
        parts = [topic]
        if benefit_score > 0:
            parts.append(f"利好{benefit_score:.1f}")
        else:
            parts.append("利好N/A")
        if consensus_score > 0:
            parts.append(f"共识{consensus_score:.0f}")
        else:
            parts.append("共识N/A")
        parts.append(f"窗口{window}")
        if evidence_gap:
            parts.append(f"缺口:{'/'.join(evidence_gap[:3])}")
        note_summary = _sanitize_note("｜".join(parts))

    suggested_note = note_summary
    if existing_note and existing_note.strip():
        suggested_note = existing_note.strip()

    return WatchlistNoteResult(
        topic=topic,
        benefit_score=benefit_score,
        consensus_score=consensus_score,
        expected_window=window,
        evidence_gap=evidence_gap,
        note_summary=note_summary,
        has_evidence=has_any_evidence,
        suggested_note=suggested_note,
    )


def generate_watchlist_note_from_candidate(candidate: "Candidate") -> WatchlistNoteResult:
    from .schemas import Candidate
    return generate_watchlist_note(
        mandate_topic=candidate.mandate_topic,
        candidate_type=candidate.candidate_type,
        mandate_score_component=candidate.mandate_score_component,
        beneficiary_score_component=candidate.beneficiary_score_component,
        ambush_score=candidate.ambush_score,
        narrative_score=candidate.narrative_score,
        composite_score=candidate.composite_score,
        positive_category_count=candidate.positive_category_count,
        resonance_count=candidate.resonance_count,
        beneficiary_path=candidate.beneficiary_path,
        company_role=candidate.company_role,
        policy_tags=candidate.policy_tags,
        fund_flow_anomaly_tags=candidate.fund_flow_anomaly_tags,
        strategy_tags=candidate.strategy_tags,
        data_completeness=candidate.data_completeness,
        missing_evidence=candidate.missing_evidence,
    )
