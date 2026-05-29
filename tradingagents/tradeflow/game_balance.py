# [S-004] candidate_game_balance
"""Game Balance Assessment — lightweight bull/bear/policy/fund perspective for candidates.

Outputs:
- game_balance: one of "favorable" / "neutral" / "crowded" / "fragile"
- bull_case: short text describing the bull thesis from existing signals
- bear_case: short text describing the bear thesis from risk flags and negative evidence
- policy_case: short text summarizing policy/regulatory stance
- fund_flow_case: short text summarizing fund flow / capital structure
- resonance_count: how many of {policy, narrative, tech, fund} categories resonate
- game_balance_refs: list of {field, matched_text, source} evidence refs

Logic:
- Count resonance across four signal categories: policy, narrative/event, tech, fund.
- When >= 2 categories resonate and risk is manageable → "favorable" or "neutral".
- When too many risk flags or high-severity risks → "fragile" or "crowded".
- All output MUST reference existing signals; no LLM calls; no strong buy/sell words.

Rules:
- No external LLM calls.
- No strong buy/sell words (禁止: 立即买入, 重仓买入, etc.).
- Only summarizes signals already present on the candidate; does NOT replace TA analysis.
- game_balance_refs must trace back to existing evidence fields.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


GAME_BALANCE_FAVORABLE = "favorable"
GAME_BALANCE_NEUTRAL = "neutral"
GAME_BALANCE_CROWDED = "crowded"
GAME_BALANCE_FRAGILE = "fragile"
ALL_GAME_BALANCES = {GAME_BALANCE_FAVORABLE, GAME_BALANCE_NEUTRAL, GAME_BALANCE_CROWDED, GAME_BALANCE_FRAGILE}

_STRONG_BUY_SELL_WORDS = {
    "立即买入", "重仓买入", "立即清仓", "满仓", "梭哈",
    "强力买入", "强烈推荐", "必买", "抄底", "追涨", "杀跌",
}

_SEVERITY_HIGH_RISK_FLAGS = {"INQUIRY_RISK", "FINANCIAL_QUALITY_RISK"}

_FUND_FLOW_POSITIVE_KW = [
    re.compile(r"主力.*净流入|资金.*净流入|大单.*净买入|北向.*净买入"),
    re.compile(r"融资.*增加|融资余额.*上升|机构.*增仓"),
    re.compile(r"龙虎榜.*机构.*买入|游资.*介入|知名席位.*买入"),
]

_FUND_FLOW_NEGATIVE_KW = [
    re.compile(r"主力.*净流出|资金.*净流出|大单.*净卖出|北向.*净卖出"),
    re.compile(r"融资.*减少|融资余额.*下降|机构.*减仓"),
    re.compile(r"龙虎榜.*净卖出|机构.*大额.*卖出"),
]


@dataclass
class GameBalanceResult:
    game_balance: str = GAME_BALANCE_NEUTRAL
    bull_case: str = ""
    bear_case: str = ""
    policy_case: str = ""
    fund_flow_case: str = ""
    resonance_count: int = 0
    game_balance_refs: list[dict] = field(default_factory=list)


def _has_fund_flow_positive(event_texts: list[str]) -> tuple[bool, list[str]]:
    snippets: list[str] = []
    for kw in _FUND_FLOW_POSITIVE_KW:
        for text in event_texts:
            if kw.search(text):
                snippets.append(text[:80])
                break
    return len(snippets) > 0, snippets


def _has_fund_flow_negative(event_texts: list[str]) -> tuple[bool, list[str]]:
    snippets: list[str] = []
    for kw in _FUND_FLOW_NEGATIVE_KW:
        for text in event_texts:
            if kw.search(text):
                snippets.append(text[:80])
                break
    return len(snippets) > 0, snippets


def _sanitize(text: str) -> str:
    for w in _STRONG_BUY_SELL_WORDS:
        if w in text:
            text = text.replace(w, "***")
    return text


def assess_game_balance(
    strategy_tags: Optional[list[str]] = None,
    score: float = 0.0,
    policy_tags: Optional[list[str]] = None,
    version_score: float = 0.0,
    policy_evidence_refs: Optional[list[dict]] = None,
    narrative_score: float = 0.0,
    narrative_reasons: Optional[list[str]] = None,
    risk_flags: Optional[list[str]] = None,
    risk_penalty: float = 0.0,
    risk_reasons: Optional[list[str]] = None,
    event_texts: Optional[list[str]] = None,
) -> GameBalanceResult:
    """Assess game balance from existing candidate signals.

    Args:
        strategy_tags: Strategy tags on the candidate (VCP, PULLBACK_SUPPORT, etc.).
        score: Aggregate candidate score.
        policy_tags: Policy version tags (from S-001).
        version_score: Policy version score.
        policy_evidence_refs: Policy evidence refs.
        narrative_score: Narrative quality score (from S-002).
        narrative_reasons: Narrative quality reasons.
        risk_flags: Risk flags (from S-003).
        risk_penalty: Risk penalty score (negative).
        risk_reasons: Risk penalty reasons.
        event_texts: Raw event texts for fund flow keyword extraction.

    Returns:
        GameBalanceResult with balance verdict and case summaries.
    """
    tags = set(strategy_tags or [])
    p_tags = list(policy_tags or [])
    r_flags = set(risk_flags or [])
    r_reasons = list(risk_reasons or [])
    n_reasons = list(narrative_reasons or [])
    p_refs = list(policy_evidence_refs or [])
    texts = [t for t in (event_texts or []) if t]

    refs: list[dict] = []

    has_policy = version_score > 0 or len(p_tags) > 0
    has_narrative = narrative_score > 0 or "EVENT_CATALYST" in tags
    has_tech = "VCP" in tags or "PULLBACK_SUPPORT" in tags
    has_fund_flow_pos, fund_pos_snippets = _has_fund_flow_positive(texts)
    has_fund_flow_neg, fund_neg_snippets = _has_fund_flow_negative(texts)
    has_fund = has_fund_flow_pos or "HOT_MONEY_LHB" in tags

    resonance = sum([has_policy, has_narrative, has_tech, has_fund])

    has_high_risk = bool(r_flags & _SEVERITY_HIGH_RISK_FLAGS)
    many_risks = len(r_flags) >= 3
    heavy_penalty = risk_penalty <= -15

    # ── Build policy_case ──
    policy_parts: list[str] = []
    if has_policy:
        policy_parts.append(f"政策版本: {', '.join(p_tags[:3])} (+{version_score}分)")
        for ref in p_refs[:2]:
            snippet = ref.get("matched_text", "")[:60]
            if snippet:
                policy_parts.append(f"证据: {snippet}")
                refs.append({"field": "policy_case", "matched_text": snippet, "source": "policy_evidence"})
    else:
        policy_parts.append("无明确政策版本信号")
    policy_case = "; ".join(policy_parts)

    # ── Build fund_flow_case ──
    fund_parts: list[str] = []
    if has_fund_flow_pos:
        fund_parts.append("资金面偏正面: 主力/北向/机构净流入信号")
        for s in fund_pos_snippets[:2]:
            refs.append({"field": "fund_flow_case", "matched_text": s, "source": "event_text_positive"})
    if has_fund_flow_neg:
        fund_parts.append("资金面偏负面: 主力/北向净流出信号")
        for s in fund_neg_snippets[:2]:
            refs.append({"field": "fund_flow_case", "matched_text": s, "source": "event_text_negative"})
    if "HOT_MONEY_LHB" in tags:
        fund_parts.append("龙虎榜有游资/机构活跃数据")
        refs.append({"field": "fund_flow_case", "matched_text": "HOT_MONEY_LHB", "source": "astock_tags"})
    if not fund_parts:
        fund_parts.append("无明确资金流信号")
    fund_flow_case = "; ".join(fund_parts)

    # ── Build bull_case ──
    bull_parts: list[str] = []
    if has_policy:
        bull_parts.append(f"政策方向支持({', '.join(p_tags[:2])})")
    if has_narrative:
        label = f"叙事质量+{narrative_score:.0f}" if narrative_score > 0 else "事件催化"
        bull_parts.append(label)
        for r in n_reasons[:2]:
            refs.append({"field": "bull_case", "matched_text": r, "source": "narrative_reasons"})
    if has_tech:
        tech_label = []
        if "VCP" in tags:
            tech_label.append("VCP形态")
        if "PULLBACK_SUPPORT" in tags:
            tech_label.append("回踩支撑")
        bull_parts.append(f"技术面: {', '.join(tech_label)}")
    if has_fund_flow_pos:
        bull_parts.append("资金流入")
    bull_case = "; ".join(bull_parts) if bull_parts else "缺乏明确看多信号"

    # ── Build bear_case ──
    bear_parts: list[str] = []
    if r_flags:
        bear_parts.append(f"风险标签: {', '.join(sorted(r_flags))}")
        for r in r_reasons[:2]:
            refs.append({"field": "bear_case", "matched_text": r, "source": "risk_reasons"})
    if heavy_penalty:
        bear_parts.append(f"风险罚分较高({risk_penalty})")
    if has_fund_flow_neg:
        bear_parts.append("资金流出压力")
    if not bear_parts:
        if not bull_parts:
            bear_case = "多空信号均不明显"
        else:
            bear_case = "无明显看空信号"
    else:
        bear_case = "; ".join(bear_parts)

    # ── Determine game_balance ──
    if has_high_risk:
        balance = GAME_BALANCE_FRAGILE
    elif many_risks or heavy_penalty:
        balance = GAME_BALANCE_FRAGILE
    elif has_fund_flow_neg and has_fund_flow_pos and len(r_flags) >= 2:
        balance = GAME_BALANCE_CROWDED
    elif resonance >= 2 and risk_penalty > -10:
        if resonance >= 3 and score > 0:
            balance = GAME_BALANCE_FAVORABLE
        else:
            balance = GAME_BALANCE_NEUTRAL
    elif resonance >= 1 and risk_penalty > -10:
        balance = GAME_BALANCE_NEUTRAL
    else:
        balance = GAME_BALANCE_NEUTRAL

    if r_flags and balance == GAME_BALANCE_FAVORABLE:
        if len(r_flags) >= 2 or risk_penalty <= -10:
            balance = GAME_BALANCE_NEUTRAL

    bull_case = _sanitize(bull_case)
    bear_case = _sanitize(bear_case)
    policy_case = _sanitize(policy_case)
    fund_flow_case = _sanitize(fund_flow_case)

    return GameBalanceResult(
        game_balance=balance,
        bull_case=bull_case,
        bear_case=bear_case,
        policy_case=policy_case,
        fund_flow_case=fund_flow_case,
        resonance_count=resonance,
        game_balance_refs=refs,
    )
