# [H-007] mandate_ta_queue_router
"""Research Queue Router — maps CandidateType to research queue and intent.

Routes H-004 candidate types into dedicated research queues so that:
- POLICY_AMBUSH candidates go into midline policy research (not short-term TA).
- POLICY_CONFIRM candidates go into TA deep analysis queue.
- TECH_TRADE candidates are flagged as short-term / T+0 only.
- OVERHEATED_AVOID / PSEUDO_POLICY candidates are demoted to WATCH_ONLY or REJECTED.

Components:
- ResearchQueue enum: five queue destinations.
- ResearchIntent enum: three research intent categories.
- QueueRouteResult: routing output per candidate.
- route_to_research_queue(): main routing function.
- compute_queue_statistics(): aggregate stats for API/daily report.
- render_queue_report(): Markdown rendering.

Constraints:
- No auto TA/LLM trigger.
- No strong buy/sell words.
- Does not override G-001 analysis_intent/position_context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ResearchQueue(Enum):
    MIDLINE_POLICY = "MIDLINE_POLICY"
    TA_CONFIRM = "TA_CONFIRM"
    SHORT_TERM_TRADE = "SHORT_TERM_TRADE"
    WATCH_ONLY = "WATCH_ONLY"
    REJECTED = "REJECTED"


class ResearchIntent(Enum):
    POLICY_VALIDATION = "policy_validation"
    TREND_CONFIRMATION = "trend_confirmation"
    RISK_REVIEW = "risk_review"


RESEARCH_QUEUE_LABELS = {
    ResearchQueue.MIDLINE_POLICY: "中线政策研究",
    ResearchQueue.TA_CONFIRM: "TA深度确认",
    ResearchQueue.SHORT_TERM_TRADE: "短线交易",
    ResearchQueue.WATCH_ONLY: "仅观察",
    ResearchQueue.REJECTED: "已拒绝",
}

RESEARCH_INTENT_LABELS = {
    ResearchIntent.POLICY_VALIDATION: "政策验证",
    ResearchIntent.TREND_CONFIRMATION: "趋势确认",
    ResearchIntent.RISK_REVIEW: "风控审查",
}

_QUEUE_PRIORITY_ORDER = {
    ResearchQueue.MIDLINE_POLICY: 1,
    ResearchQueue.TA_CONFIRM: 2,
    ResearchQueue.SHORT_TERM_TRADE: 3,
    ResearchQueue.WATCH_ONLY: 4,
    ResearchQueue.REJECTED: 5,
}

_PSEUDO_POLICY_REJECT_THRESHOLD = 0.0
_OVERHEATED_REJECT_THRESHOLD = 0.0


@dataclass
class QueueRouteResult:
    research_queue: str = ResearchQueue.WATCH_ONLY.value
    research_intent: str = ResearchIntent.RISK_REVIEW.value
    route_reason: str = ""
    queue_priority: int = 4

    def to_dict(self) -> dict:
        return {
            "research_queue": self.research_queue,
            "research_intent": self.research_intent,
            "route_reason": self.route_reason,
            "queue_priority": self.queue_priority,
        }


@dataclass
class QueueStatistics:
    total: int = 0
    by_queue: dict[str, int] = field(default_factory=dict)
    by_intent: dict[str, int] = field(default_factory=dict)
    midline_policy_symbols: list[str] = field(default_factory=list)
    ta_confirm_symbols: list[str] = field(default_factory=list)
    short_term_symbols: list[str] = field(default_factory=list)
    watch_only_symbols: list[str] = field(default_factory=list)
    rejected_symbols: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "by_queue": self.by_queue,
            "by_intent": self.by_intent,
            "midline_policy_symbols": self.midline_policy_symbols,
            "ta_confirm_symbols": self.ta_confirm_symbols,
            "short_term_symbols": self.short_term_symbols,
            "watch_only_symbols": self.watch_only_symbols,
            "rejected_symbols": self.rejected_symbols,
        }


def route_to_research_queue(
    candidate_type: str = "",
    ambush_score: float = 0.0,
    mandate_score_component: float = 0.0,
    beneficiary_score_component: float = 0.0,
    overheat_penalty: float = 0.0,
    risk_flags: Optional[list[str]] = None,
    game_balance: str = "",
    has_beneficiary_path: bool = False,
    has_policy: bool = False,
) -> QueueRouteResult:
    r_flags = set(risk_flags or [])

    if not candidate_type:
        return QueueRouteResult(
            research_queue=ResearchQueue.WATCH_ONLY.value,
            research_intent=ResearchIntent.RISK_REVIEW.value,
            route_reason="无候选类型，默认仅观察",
            queue_priority=_QUEUE_PRIORITY_ORDER[ResearchQueue.WATCH_ONLY],
        )

    if candidate_type == "POLICY_AMBUSH":
        intent = ResearchIntent.POLICY_VALIDATION
        if has_beneficiary_path and mandate_score_component >= 40:
            reason = f"政策左侧埋伏→中线政策研究: mandate={mandate_score_component:.0f}, 受益路径明确, 埋伏分={ambush_score:.0f}"
        elif has_policy:
            reason = f"政策左侧埋伏→中线政策研究: mandate={mandate_score_component:.0f}, 待验证受益路径"
        else:
            reason = f"政策左侧埋伏→中线政策研究: 埋伏分={ambush_score:.0f}"
        return QueueRouteResult(
            research_queue=ResearchQueue.MIDLINE_POLICY.value,
            research_intent=intent.value,
            route_reason=reason,
            queue_priority=_QUEUE_PRIORITY_ORDER[ResearchQueue.MIDLINE_POLICY],
        )

    if candidate_type == "POLICY_CONFIRM":
        intent = ResearchIntent.TREND_CONFIRMATION
        if has_beneficiary_path:
            reason = f"政策右侧确认→TA深度确认: mandate={mandate_score_component:.0f}, 受益路径+技术确认"
        else:
            reason = f"政策右侧确认→TA深度确认: mandate={mandate_score_component:.0f}, 需趋势确认"
        return QueueRouteResult(
            research_queue=ResearchQueue.TA_CONFIRM.value,
            research_intent=intent.value,
            route_reason=reason,
            queue_priority=_QUEUE_PRIORITY_ORDER[ResearchQueue.TA_CONFIRM],
        )

    if candidate_type == "TECH_TRADE":
        intent = ResearchIntent.TREND_CONFIRMATION
        reason = "纯技术交易→短线/做T队列，不混入主池"
        return QueueRouteResult(
            research_queue=ResearchQueue.SHORT_TERM_TRADE.value,
            research_intent=intent.value,
            route_reason=reason,
            queue_priority=_QUEUE_PRIORITY_ORDER[ResearchQueue.SHORT_TERM_TRADE],
        )

    if candidate_type == "EVENT_WATCH":
        if ambush_score >= 30 and has_policy:
            return QueueRouteResult(
                research_queue=ResearchQueue.WATCH_ONLY.value,
                research_intent=ResearchIntent.POLICY_VALIDATION.value,
                route_reason=f"事件观察→仅观察: 有政策信号(mandate={mandate_score_component:.0f}), 等待更多证据",
                queue_priority=_QUEUE_PRIORITY_ORDER[ResearchQueue.WATCH_ONLY],
            )
        return QueueRouteResult(
            research_queue=ResearchQueue.WATCH_ONLY.value,
            research_intent=ResearchIntent.RISK_REVIEW.value,
            route_reason="事件观察→仅观察: 信号不足，暂不入研究队列",
            queue_priority=_QUEUE_PRIORITY_ORDER[ResearchQueue.WATCH_ONLY],
        )

    if candidate_type == "PSEUDO_POLICY":
        high_risk = {"INQUIRY_RISK", "FINANCIAL_QUALITY_RISK"}
        if bool(r_flags & high_risk) or game_balance == "fragile":
            reason = f"伪政策题材→已拒绝: 高风险({', '.join(r_flags & high_risk) or game_balance})"
            return QueueRouteResult(
                research_queue=ResearchQueue.REJECTED.value,
                research_intent=ResearchIntent.RISK_REVIEW.value,
                route_reason=reason,
                queue_priority=_QUEUE_PRIORITY_ORDER[ResearchQueue.REJECTED],
            )
        reason = "伪政策题材→仅观察: 证据不足/蹭概念，不消耗研究预算"
        return QueueRouteResult(
            research_queue=ResearchQueue.WATCH_ONLY.value,
            research_intent=ResearchIntent.RISK_REVIEW.value,
            route_reason=reason,
            queue_priority=_QUEUE_PRIORITY_ORDER[ResearchQueue.WATCH_ONLY],
        )

    if candidate_type == "OVERHEATED_AVOID":
        reason = f"过热规避→已拒绝: overheat_penalty={overheat_penalty:.0f}"
        if r_flags:
            reason += f", 风险标签({', '.join(sorted(r_flags)[:3])})"
        return QueueRouteResult(
            research_queue=ResearchQueue.REJECTED.value,
            research_intent=ResearchIntent.RISK_REVIEW.value,
            route_reason=reason,
            queue_priority=_QUEUE_PRIORITY_ORDER[ResearchQueue.REJECTED],
        )

    return QueueRouteResult(
        research_queue=ResearchQueue.WATCH_ONLY.value,
        research_intent=ResearchIntent.RISK_REVIEW.value,
        route_reason=f"未知类型({candidate_type})→仅观察",
        queue_priority=_QUEUE_PRIORITY_ORDER[ResearchQueue.WATCH_ONLY],
    )


def compute_queue_statistics(
    routes: list[dict],
) -> QueueStatistics:
    stats = QueueStatistics(total=len(routes))
    by_queue: dict[str, int] = {}
    by_intent: dict[str, int] = {}

    queue_symbols = {
        "MIDLINE_POLICY": [],
        "TA_CONFIRM": [],
        "SHORT_TERM_TRADE": [],
        "WATCH_ONLY": [],
        "REJECTED": [],
    }

    for r in routes:
        queue = r.get("research_queue", "WATCH_ONLY")
        intent = r.get("research_intent", "risk_review")
        symbol = r.get("symbol", "")

        by_queue[queue] = by_queue.get(queue, 0) + 1
        by_intent[intent] = by_intent.get(intent, 0) + 1

        if queue in queue_symbols and symbol:
            queue_symbols[queue].append(symbol)

    stats.by_queue = by_queue
    stats.by_intent = by_intent
    stats.midline_policy_symbols = queue_symbols["MIDLINE_POLICY"]
    stats.ta_confirm_symbols = queue_symbols["TA_CONFIRM"]
    stats.short_term_symbols = queue_symbols["SHORT_TERM_TRADE"]
    stats.watch_only_symbols = queue_symbols["WATCH_ONLY"]
    stats.rejected_symbols = queue_symbols["REJECTED"]

    return stats


def render_queue_report(stats: QueueStatistics) -> str:
    lines = [
        "研究队列分流报告",
        "=" * 40,
        f"总计: {stats.total} 个候选",
        "",
    ]

    for queue_name, label in [
        (ResearchQueue.MIDLINE_POLICY, "中线政策研究"),
        (ResearchQueue.TA_CONFIRM, "TA深度确认"),
        (ResearchQueue.SHORT_TERM_TRADE, "短线交易"),
        (ResearchQueue.WATCH_ONLY, "仅观察"),
        (ResearchQueue.REJECTED, "已拒绝"),
    ]:
        count = stats.by_queue.get(queue_name.value, 0)
        symbols_map = {
            ResearchQueue.MIDLINE_POLICY: stats.midline_policy_symbols,
            ResearchQueue.TA_CONFIRM: stats.ta_confirm_symbols,
            ResearchQueue.SHORT_TERM_TRADE: stats.short_term_symbols,
            ResearchQueue.WATCH_ONLY: stats.watch_only_symbols,
            ResearchQueue.REJECTED: stats.rejected_symbols,
        }
        syms = symbols_map[queue_name]
        sym_str = ", ".join(syms[:10]) if syms else "无"
        lines.append(f"  {label}: {count}")
        if syms:
            lines.append(f"    标的: {sym_str}")
        lines.append("")

    lines.append("研究意图分布:")
    for intent_val, label in [
        (ResearchIntent.POLICY_VALIDATION, "政策验证"),
        (ResearchIntent.TREND_CONFIRMATION, "趋势确认"),
        (ResearchIntent.RISK_REVIEW, "风控审查"),
    ]:
        count = stats.by_intent.get(intent_val.value, 0)
        lines.append(f"  {label}: {count}")

    return "\n".join(lines)
