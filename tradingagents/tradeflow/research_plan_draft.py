# [UI-009] candidate_ta_plan_draft
"""Research Plan Draft Generator — creates a TA research plan from candidate data.

Generates a lightweight research plan (queue record + analysis parameters)
without triggering any TA or LLM call. The plan is a draft that the user
must confirm before any deep analysis starts.

Components:
- ResearchPlanDraft: dataclass for the plan output.
- generate_research_plan(): main entry point.
- render_plan_markdown(): Markdown rendering.

Constraints:
- No auto TA/LLM trigger.
- No strong buy/sell words.
- Does not override G-001 analysis_intent/position_context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


_MIDLINE_MODULES = [
    "fundamentals_analyst",
    "news_analyst",
    "smart_money_analyst",
    "risk_manager",
    "volume_price_analyst",
]

_SHORT_TERM_MODULES = [
    "technical_analyst",
    "smart_money_analyst",
    "volume_price_analyst",
    "risk_manager",
]

_FULL_TA_MODULES = [
    "fundamentals_analyst",
    "sentiment_analyst",
    "news_analyst",
    "technical_analyst",
    "macro_analyst",
    "smart_money_analyst",
    "volume_price_analyst",
    "bull_researcher",
    "bear_researcher",
    "research_director",
    "risk_manager",
    "compliance_check",
    "trader",
]


@dataclass
class ResearchPlanDraft:
    symbol: str = ""
    trade_date: str = ""
    candidate_type: str = ""
    research_queue: str = ""
    research_intent: str = ""
    route_reason: str = ""
    horizon: str = ""
    analysis_intent: str = ""
    position_context: str = ""
    runtime_profile: str = ""
    enabled_modules: list[str] = field(default_factory=list)
    required_evidence: list[str] = field(default_factory=list)
    existing_evidence_coverage: float = 0.0
    can_generate: bool = True
    block_reason: str = ""
    plan_markdown: str = ""

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "trade_date": self.trade_date,
            "candidate_type": self.candidate_type,
            "research_queue": self.research_queue,
            "research_intent": self.research_intent,
            "route_reason": self.route_reason,
            "horizon": self.horizon,
            "analysis_intent": self.analysis_intent,
            "position_context": self.position_context,
            "runtime_profile": self.runtime_profile,
            "enabled_modules": self.enabled_modules,
            "required_evidence": self.required_evidence,
            "existing_evidence_coverage": self.existing_evidence_coverage,
            "can_generate": self.can_generate,
            "block_reason": self.block_reason,
            "plan_markdown": self.plan_markdown,
        }


_MIN_EVIDENCE_COVERAGE = 0.15


def _determine_horizon(candidate_type: str) -> str:
    if candidate_type in ("POLICY_AMBUSH", "POLICY_CONFIRM"):
        return "medium"
    return "short"


def _determine_analysis_intent(candidate_type: str) -> str:
    if candidate_type in ("POLICY_AMBUSH", "POLICY_CONFIRM"):
        return "entry"
    return "watch"


def _determine_position_context(candidate_type: str) -> str:
    return "false"


def _determine_runtime_profile(candidate_type: str) -> str:
    if candidate_type in ("POLICY_AMBUSH", "POLICY_CONFIRM"):
        return "MIDLINE_POLICY_LIGHT"
    if candidate_type == "TECH_TRADE":
        return "SHORT_TECH_LIGHT"
    return "FULL_TA"


def _determine_modules(candidate_type: str) -> list[str]:
    if candidate_type in ("POLICY_AMBUSH", "POLICY_CONFIRM"):
        return list(_MIDLINE_MODULES)
    if candidate_type == "TECH_TRADE":
        return list(_SHORT_TERM_MODULES)
    return list(_FULL_TA_MODULES)


def _determine_required_evidence(candidate_type: str) -> list[str]:
    base = ["stock_data", "fund_flow"]
    if candidate_type in ("POLICY_AMBUSH", "POLICY_CONFIRM"):
        return base + ["policy_evidence", "beneficiary_path", "announcements"]
    if candidate_type == "TECH_TRADE":
        return base + ["realtime_quote"]
    if candidate_type == "EVENT_WATCH":
        return base + ["event_source"]
    return base


def _check_can_generate(
    candidate_type: str,
    research_queue: str,
    evidence_coverage: float,
    missing_evidence: Optional[list[str]] = None,
) -> tuple[bool, str]:
    if research_queue == "REJECTED":
        return False, "候选已被研究队列拒绝，不适合生成研究预案"
    if candidate_type in ("OVERHEATED_AVOID", "PSEUDO_POLICY"):
        return False, f"候选类型为{candidate_type}，不适合生成研究预案"
    if evidence_coverage < _MIN_EVIDENCE_COVERAGE:
        return False, f"证据覆盖率过低({evidence_coverage:.0%})，无法生成有意义的研究预案"
    missing = missing_evidence or []
    critical_missing = []
    for m in missing:
        ml = m.lower()
        if any(k in ml for k in ("stock_data", "fund_flow", "realtime")):
            critical_missing.append(m)
    if critical_missing and evidence_coverage < 0.3:
        return False, f"关键证据缺失({', '.join(critical_missing[:3])})，覆盖率不足"
    return True, ""


def generate_research_plan(
    symbol: str,
    trade_date: str,
    candidate_type: str = "",
    research_queue: str = "",
    research_intent: str = "",
    route_reason: str = "",
    evidence_coverage: float = 0.0,
    missing_evidence: Optional[list[str]] = None,
) -> ResearchPlanDraft:
    horizon = _determine_horizon(candidate_type)
    analysis_intent = _determine_analysis_intent(candidate_type)
    position_context = _determine_position_context(candidate_type)
    runtime_profile = _determine_runtime_profile(candidate_type)
    enabled_modules = _determine_modules(candidate_type)
    required_evidence = _determine_required_evidence(candidate_type)

    can_generate, block_reason = _check_can_generate(
        candidate_type, research_queue, evidence_coverage, missing_evidence,
    )

    draft = ResearchPlanDraft(
        symbol=symbol,
        trade_date=trade_date,
        candidate_type=candidate_type,
        research_queue=research_queue,
        research_intent=research_intent,
        route_reason=route_reason,
        horizon=horizon,
        analysis_intent=analysis_intent,
        position_context=position_context,
        runtime_profile=runtime_profile,
        enabled_modules=enabled_modules,
        required_evidence=required_evidence,
        existing_evidence_coverage=evidence_coverage,
        can_generate=can_generate,
        block_reason=block_reason,
    )
    draft.plan_markdown = render_plan_markdown(draft)
    return draft


def render_plan_markdown(draft: ResearchPlanDraft) -> str:
    lines = [
        f"## TA 研究预案: {draft.symbol}",
        "",
        f"- **日期**: {draft.trade_date}",
        f"- **候选类型**: {draft.candidate_type or '未分类'}",
        f"- **研究队列**: {draft.research_queue or '未分配'}",
        f"- **研究意图**: {draft.research_intent or '未确定'}",
        f"- **分析周期**: {'中线' if draft.horizon == 'medium' else '短线'}",
        f"- **运行 Profile**: {draft.runtime_profile}",
        f"- **持仓上下文**: {'已持仓' if draft.position_context == 'true' else '未持仓'}",
        f"- **证据覆盖率**: {draft.existing_evidence_coverage:.0%}",
        "",
        "### 启用模块",
    ]
    for m in draft.enabled_modules:
        lines.append(f"- {m}")
    lines.append("")
    lines.append("### 必要证据")
    for e in draft.required_evidence:
        lines.append(f"- {e}")
    if draft.route_reason:
        lines.append("")
        lines.append(f"### 分流原因")
        lines.append(draft.route_reason)
    if not draft.can_generate:
        lines.append("")
        lines.append(f"### 无法生成")
        lines.append(draft.block_reason)
    return "\n".join(lines)
