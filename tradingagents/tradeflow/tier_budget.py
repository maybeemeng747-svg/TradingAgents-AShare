# [S-007] candidate_tier_budget
"""Candidate Tier Classification & TA Token Budget Allocation.

Responsibilities:
1. Classify candidates into A (priority deep dive) / B (observe, wait for
   evidence) / C (eliminate / deprioritize) tiers based on the composite
   score, risk flags, data completeness, and strategy resonance already
   computed by the selection_priority_gate (S-005).
2. Assign a ``ta_budget_priority`` token budget suggestion per tier.
3. Explain ``missing_evidence_for_upgrade`` so the Daily Plan can show
   "what evidence is needed to promote this candidate".
4. Enforce a hard cap on the number of A-tier candidates per day so that
   TA token consumption stays bounded.

Design constraints:
- No external LLM calls.
- No strong buy/sell words.
- Does not auto-invoke TA.
- Does not connect to real billing APIs; only outputs budget *suggestions*.
- Single weak signal alone must NOT enter A tier.
- Two or more strong evidence categories with manageable risk CAN enter A.
- High-risk / low-completeness candidates at most B or C.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .strategy_config import StrategyConfig, DEFAULT_STRATEGY_CONFIG  # [M-004]


_STRONG_BUY_SELL_WORDS = {
    "立即买入", "重仓买入", "立即清仓", "满仓", "梭哈",
    "强力买入", "强烈推荐", "必买", "抄底", "追涨", "杀跌",
}

TIER_A = "A"
TIER_B = "B"
TIER_C = "C"
ALL_TIERS = {TIER_A, TIER_B, TIER_C}

TIER_BUDGET_MAP = {
    TIER_A: 100,
    TIER_B: 30,
    TIER_C: 0,
}

TIER_LABELS = {
    TIER_A: "优先深挖",
    TIER_B: "观察等待证据",
    TIER_C: "淘汰/暂不关注",
}

A_TIER_HARD_CAP = 5

_SEVERITY_HIGH_RISK_FLAGS = {"INQUIRY_RISK", "FINANCIAL_QUALITY_RISK"}


@dataclass
class TierBudgetResult:
    tier: str = TIER_C
    ta_budget_priority: int = 0
    tier_reason: str = ""
    missing_evidence_for_upgrade: list[str] = field(default_factory=list)
    why_not_deep_ta: str = ""


@dataclass
class TierBudgetAllocation:
    tier_a_count: int = 0
    tier_b_count: int = 0
    tier_c_count: int = 0
    total_budget: int = 0
    a_tier_cap_applied: bool = False
    demoted_symbols: list[str] = field(default_factory=list)


def _sanitize(text: str) -> str:
    for w in _STRONG_BUY_SELL_WORDS:
        if w in text:
            text = text.replace(w, "***")
    return text


def classify_candidate_tier(
    priority_rank: str = "",
    composite_score: float = 0.0,
    positive_category_count: int = 0,
    data_completeness: float = 0.0,
    missing_evidence: Optional[list[str]] = None,
    risk_flags: Optional[list[str]] = None,
    risk_penalty: float = 0.0,
    game_balance: str = "",
    gate_passed: bool = False,
    why_not_deep_ta: str = "",
    cfg: Optional[StrategyConfig] = None,  # [M-004]
) -> TierBudgetResult:
    """Classify a single candidate into A / B / C tier.

    The classification builds on the S-005 ``priority_rank`` but adds
    stricter guardrails:

    * **Tier A** requires:
      - ``positive_category_count >= 3`` **or**
        (``positive_category_count >= 2`` **and** ``composite_score >= 50``)
      - ``data_completeness >= 0.6``
      - No high-severity risk flags
      - Fewer than 3 risk flags in total
      - ``game_balance`` is ``favorable`` or ``neutral``
      - ``gate_passed`` is True
    * **Tier C** is assigned when:
      - High-severity risk flags are present **or**
      - 3 or more risk flags **or**
      - ``data_completeness < 0.3`` **or**
      - ``game_balance`` is ``fragile`` **or**
      - ``positive_category_count < 1``
    * **Tier B** covers everything else.

    Returns:
        ``TierBudgetResult`` with tier, budget, reason, and upgrade hints.
    """
    if cfg is None:
        cfg = DEFAULT_STRATEGY_CONFIG

    r_flags = set(risk_flags or [])
    missing = list(missing_evidence or [])

    has_high_risk = bool(r_flags & set(cfg.risk_high_severity_flags))  # [M-004]
    many_risks = len(r_flags) >= cfg.risk_many_flags_threshold
    heavy_penalty = risk_penalty <= cfg.risk_heavy_penalty_threshold
    fragile = game_balance == "fragile"
    favorable_or_neutral = game_balance in {"favorable", "neutral"}

    missing_for_upgrade: list[str] = []
    tier = TIER_C
    reason = ""

    # --- Tier A conditions ---
    meets_a_signals = (
        positive_category_count >= cfg.gate_rank_a_min_categories  # [M-004]
        or (positive_category_count >= cfg.gate_rank_b_min_categories and composite_score >= cfg.tier_a_min_composite_for_2cat)
    )
    meets_a_completeness = data_completeness >= cfg.tier_a_min_completeness
    meets_a_risk = not has_high_risk and not many_risks and not heavy_penalty
    meets_a_game = favorable_or_neutral
    meets_a_gate = gate_passed

    if meets_a_signals and meets_a_completeness and meets_a_risk and meets_a_game and meets_a_gate:
        tier = TIER_A
        cat_desc = f"{positive_category_count}类信号共振"
        reason = f"{cat_desc}，综合分{composite_score:.0f}，风险可控，建议优先深度分析"
    else:
        # Build upgrade hints
        if not meets_a_signals:
            if positive_category_count < 2:
                missing_for_upgrade.append(f"需至少2类正向信号(当前{positive_category_count}类)")
            else:
                missing_for_upgrade.append(f"信号数{positive_category_count}类+综合分需≥50(当前{composite_score:.0f})")
        if not meets_a_completeness:
            missing_for_upgrade.append(f"数据完整度需≥60%(当前{data_completeness:.0%})")
        if not meets_a_risk:
            if has_high_risk:
                missing_for_upgrade.append(f"高风险标签需解除({', '.join(r_flags & _SEVERITY_HIGH_RISK_FLAGS)})")
            if many_risks:
                missing_for_upgrade.append(f"风险标签过多({len(r_flags)}个，需<3)")
            if heavy_penalty:
                missing_for_upgrade.append(f"风险罚分过重({risk_penalty}，需>-15)")
        if not meets_a_game:
            missing_for_upgrade.append(f"博弈平衡需改善(当前{game_balance or '未评估'})")
        if not meets_a_gate:
            missing_for_upgrade.append("S-005门控未通过")

        # --- Tier B or C ---
        meets_b_signals = positive_category_count >= 1
        meets_b_completeness = data_completeness >= cfg.tier_b_min_completeness  # [M-004]
        meets_b_risk = not has_high_risk and not many_risks and not heavy_penalty
        not_fragile = not fragile

        if (
            meets_b_signals
            and meets_b_completeness
            and meets_b_risk
            and not_fragile
            and positive_category_count >= 1
        ):
            tier = TIER_B
            reason_parts = []
            if positive_category_count < 2:
                reason_parts.append(f"信号不足({positive_category_count}类)")
            elif not meets_a_completeness:
                reason_parts.append(f"完整度不足({data_completeness:.0%})")
            elif not favorable_or_neutral:
                reason_parts.append(f"博弈不佳({game_balance})")
            elif not gate_passed:
                reason_parts.append("门控未通过")
            else:
                reason_parts.append("综合条件接近A层但未完全满足")
            if missing:
                reason_parts.append(f"缺: {', '.join(missing[:3])}")
            reason = "；".join(reason_parts) if reason_parts else "观察等待更多证据"
        else:
            tier = TIER_C
            c_reasons = []
            if has_high_risk:
                c_reasons.append(f"高风险({', '.join(r_flags & _SEVERITY_HIGH_RISK_FLAGS)})")
            if many_risks:
                c_reasons.append(f"风险标签多({len(r_flags)}个)")
            if fragile:
                c_reasons.append("博弈fragile")
            if positive_category_count < 1:
                c_reasons.append("无正向信号")
            if data_completeness < 0.3:
                c_reasons.append(f"数据严重不足({data_completeness:.0%})")
            elif data_completeness < 0.5:
                c_reasons.append(f"数据不足({data_completeness:.0%})")
            if heavy_penalty:
                c_reasons.append(f"风险罚分重({risk_penalty})")
            reason = "；".join(c_reasons) if c_reasons else "综合条件不足"

    budget = cfg.tier_a_budget if tier == TIER_A else (cfg.tier_b_budget if tier == TIER_B else cfg.tier_c_budget)  # [M-004]
    not_deep = why_not_deep_ta if tier != TIER_A else ""

    reason = _sanitize(reason)
    not_deep = _sanitize(not_deep)
    for i, hint in enumerate(missing_for_upgrade):
        missing_for_upgrade[i] = _sanitize(hint)

    return TierBudgetResult(
        tier=tier,
        ta_budget_priority=budget,
        tier_reason=reason,
        missing_evidence_for_upgrade=missing_for_upgrade,
        why_not_deep_ta=not_deep,
    )


def allocate_tier_budget(
    results: list[TierBudgetResult],
    a_tier_cap: int = 0,  # [M-004] default 0 means use StrategyConfig default
    cfg: Optional[StrategyConfig] = None,
) -> TierBudgetAllocation:
    """Enforce the A-tier cap and compute aggregate budget.

    When more candidates qualify for tier A than ``a_tier_cap``, the
    lowest-scoring A-tier candidates are demoted to B.  The caller
    should pre-sort ``results`` so that index 0 is the strongest
    candidate.

    Args:
        results: Pre-sorted list of TierBudgetResult (strongest first).
        a_tier_cap: Maximum number of A-tier candidates allowed.

    Returns:
        TierBudgetAllocation with aggregate stats and demotion info.
    """
    if cfg is None:
        cfg = DEFAULT_STRATEGY_CONFIG
    if a_tier_cap <= 0:
        a_tier_cap = cfg.tier_a_hard_cap

    tier_budget_map = {TIER_A: cfg.tier_a_budget, TIER_B: cfg.tier_b_budget, TIER_C: cfg.tier_c_budget}  # [M-004]

    a_indices = [i for i, r in enumerate(results) if r.tier == TIER_A]

    cap_applied = False
    demoted: list[str] = []

    if len(a_indices) > a_tier_cap:
        cap_applied = True
        for idx in a_indices[a_tier_cap:]:
            old = results[idx]
            old.tier = TIER_B
            old.ta_budget_priority = tier_budget_map[TIER_B]
            old.tier_reason = f"A层已满(上限{a_tier_cap})，降为B层观察；{old.tier_reason}"
            old.why_not_deep_ta = f"A层名额已满({a_tier_cap})，降为观察"
            demoted.append(f"slot_{idx}")

    counts = {TIER_A: 0, TIER_B: 0, TIER_C: 0}
    total = 0
    for r in results:
        counts[r.tier] = counts.get(r.tier, 0) + 1
        total += r.ta_budget_priority

    return TierBudgetAllocation(
        tier_a_count=counts.get(TIER_A, 0),
        tier_b_count=counts.get(TIER_B, 0),
        tier_c_count=counts.get(TIER_C, 0),
        total_budget=total,
        a_tier_cap_applied=cap_applied,
        demoted_symbols=demoted,
    )


def render_tier_budget_summary(allocation: TierBudgetAllocation) -> str:
    """Render a human-readable tier budget summary for Daily Plan."""
    lines = [
        f"📊 TA Token 预算分配",
        f"  A层(优先深挖): {allocation.tier_a_count}只 × 100 tokens",
        f"  B层(观察等待): {allocation.tier_b_count}只 × 30 tokens",
        f"  C层(暂不关注): {allocation.tier_c_count}只 × 0 tokens",
        f"  总预算建议: {allocation.total_budget} tokens",
    ]
    if allocation.a_tier_cap_applied:
        lines.append(f"  ⚠️ A层上限{A_TIER_HARD_CAP}已触发，{len(allocation.demoted_symbols)}只降为B层")
    return "\n".join(lines)
