# [S-003] underwater_risk_flags
"""Underwater Risk Flags — identify hidden structural risks for TradeFlow candidates.

Outputs:
- risk_flags: list of risk flag strings (LOCKUP_RISK, REDUCE_HOLDING_RISK, etc.)
- risk_penalty: 0 to -30 deduction score
- risk_evidence_refs: list of {flag, matched_text, source} for audit trail

Risk flag categories:
  1. LOCKUP_RISK          — lockup expiry / massive unlock coming
  2. REDUCE_HOLDING_RISK  — insider / major shareholder reduction
  3. INQUIRY_RISK         — regulatory inquiry / supervision letter
  4. FINANCIAL_QUALITY_RISK — earnings quality / audit / going concern
  5. MARGIN_CROWDING_RISK — margin financing crowded / excessive leverage
  6. LHB_OVERHEAT_RISK    — dragon-tiger board overheating / hot money crowded

Rules:
- All risk flags MUST be backed by raw event text or explicit data fields.
- No risk flags from speculation; no LLM calls.
- LHB and margin risks MUST distinguish HAS_DATA / NOT_QUERIED / FAILED.
  NOT_QUERIED is NOT the same as "no risk".
- Risk flags translate to score deduction and stricter need_deep_ta gate,
  never to direct buy/sell/liquidate actions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class UnderwaterRiskResult:
    risk_flags: list[str] = field(default_factory=list)
    risk_penalty: float = 0.0
    risk_evidence_refs: list[dict] = field(default_factory=list)
    risk_reasons: list[str] = field(default_factory=list)


MAX_RISK_PENALTY = 30.0

FLAG_LOCKUP_RISK = "LOCKUP_RISK"
FLAG_REDUCE_HOLDING_RISK = "REDUCE_HOLDING_RISK"
FLAG_INQUIRY_RISK = "INQUIRY_RISK"
FLAG_FINANCIAL_QUALITY_RISK = "FINANCIAL_QUALITY_RISK"
FLAG_MARGIN_CROWDING_RISK = "MARGIN_CROWDING_RISK"
FLAG_LHB_OVERHEAT_RISK = "LHB_OVERHEAT_RISK"

ALL_RISK_FLAGS = {
    FLAG_LOCKUP_RISK,
    FLAG_REDUCE_HOLDING_RISK,
    FLAG_INQUIRY_RISK,
    FLAG_FINANCIAL_QUALITY_RISK,
    FLAG_MARGIN_CROWDING_RISK,
    FLAG_LHB_OVERHEAT_RISK,
}

_LOCKUP_KW = [
    (re.compile(r"解禁|限售股.*上市|首发原股东.*股份.*上市|定增.*解禁"), 10),
    (re.compile(r"限售股|锁定期.*满|限售期满|解禁市值|待解禁"), 7),
    (re.compile(r"大宗交易.*减持|协议转让.*减持"), 5),
]

_REDUCE_HOLDING_KW = [
    (re.compile(r"减持计划|减持股份|大股东.*减持|董监高.*减持|持股比例.*下降"), 10),
    (re.compile(r"减持进展|减持完成|实际减持"), 7),
    (re.compile(r"被动减持|司法划转|司法拍卖"), 5),
]

_INQUIRY_KW = [
    (re.compile(r"问询函|关注函|监管函|警示函|整改函|约谈"), 10),
    (re.compile(r"证监会.*调查|立案调查|行政处罚|自律监管"), 9),
    (re.compile(r"交易所.*关注|监管关注|异常波动.*说明"), 6),
]

_FINANCIAL_QUALITY_KW = [
    (re.compile(r"审计.*非标|保留意见|否定意见|无法表示意见|带强调事项段"), 10),
    (re.compile(r"商誉减值|资产减值.*重大|坏账准备.*大幅|存货跌价"), 7),
    (re.compile(r"持续经营.*不确定|退市风险|ST|\\*ST|被实施.*退市"), 9),
    (re.compile(r"财务造假|虚增收入|虚增利润|会计差错更正"), 10),
    (re.compile(r"经营性现金流.*为负|连续.*亏损|业绩.*大幅下滑"), 5),
]

_MARGIN_CROWDING_KW = [
    (re.compile(r"融资余额.*新高|融资买入.*激增|融资客.*扎堆"), 8),
    (re.compile(r"融券余量.*激增|做空.*压力|融券余额.*飙升"), 7),
    (re.compile(r"融资融券.*过热|杠杆资金.*涌入|两融.*过亿"), 6),
]

_LHB_OVERHEAT_KW = [
    (re.compile(r"龙虎榜.*机构.*买入|龙虎榜.*游资|龙虎榜.*活跃|上榜.*资金"), 8),
    (re.compile(r"席位.*净买入.*亿|知名游资.*介入|一线游资"), 7),
    (re.compile(r"换手率.*超.*%|天量.*成交|成交额.*历史|连续.*涨停"), 6),
    (re.compile(r"龙虎榜.*净卖出|机构.*大额.*卖出|席位.*净卖出"), 8),
]


def _match_keywords(
    texts: list[str],
    rules: list[tuple[re.Pattern, int]],
    flag_name: str,
    source_hint: str = "event_text",
) -> tuple[list[str], list[dict], float]:
    matched_snippets: list[str] = []
    refs: list[dict] = []
    best_weight = 0.0

    for kw, weight in rules:
        for text in texts:
            if kw.search(text):
                if weight > best_weight:
                    best_weight = weight
                snippet = text[:120]
                matched_snippets.append(snippet)
                refs.append({
                    "flag": flag_name,
                    "matched_text": snippet,
                    "source": source_hint,
                    "weight": weight,
                })
                break

    return matched_snippets, refs, best_weight


def detect_underwater_risks(
    event_texts: list[str] | None = None,
    lhb_status: str = "NOT_QUERIED",
    lhb_texts: list[str] | None = None,
    margin_status: str = "NOT_QUERIED",
    margin_texts: list[str] | None = None,
) -> UnderwaterRiskResult:
    """Detect underwater risk flags from event texts and data status.

    Args:
        event_texts: List of news/announcement title strings for this symbol.
        lhb_status: Dragon-tiger board query status.
            "HAS_DATA" — queried and has data; check lhb_texts for overheating.
            "NOT_QUERIED" — not queried; must NOT generate LHB_OVERHEAT_RISK.
            "FAILED" — query failed; must NOT generate LHB_OVERHEAT_RISK.
            "NORMAL_NO_DATA" — queried but no data; no overheating risk.
        lhb_texts: LHB-related text snippets (only used when lhb_status="HAS_DATA").
        margin_status: Margin financing query status. Same semantics as lhb_status.
        margin_texts: Margin-related text snippets (only used when margin_status="HAS_DATA").

    Returns:
        UnderwaterRiskResult with flags, penalty, evidence refs, and reasons.
        All flags require raw text evidence; no speculation.
    """
    texts = [t for t in (event_texts or []) if t]
    if not texts and lhb_status == "NOT_QUERIED" and margin_status == "NOT_QUERIED":
        return UnderwaterRiskResult()

    all_flags: list[str] = []
    all_refs: list[dict] = []
    all_reasons: list[str] = []
    total_penalty = 0.0

    # ── 1. LOCKUP_RISK ──
    snippets, refs, weight = _match_keywords(texts, _LOCKUP_KW, FLAG_LOCKUP_RISK)
    if weight > 0:
        all_flags.append(FLAG_LOCKUP_RISK)
        all_refs.extend(refs)
        total_penalty += weight
        all_reasons.append(f"解禁风险(-{weight:.0f})")

    # ── 2. REDUCE_HOLDING_RISK ──
    snippets, refs, weight = _match_keywords(texts, _REDUCE_HOLDING_KW, FLAG_REDUCE_HOLDING_RISK)
    if weight > 0:
        all_flags.append(FLAG_REDUCE_HOLDING_RISK)
        all_refs.extend(refs)
        total_penalty += weight
        all_reasons.append(f"减持风险(-{weight:.0f})")

    # ── 3. INQUIRY_RISK ──
    snippets, refs, weight = _match_keywords(texts, _INQUIRY_KW, FLAG_INQUIRY_RISK)
    if weight > 0:
        all_flags.append(FLAG_INQUIRY_RISK)
        all_refs.extend(refs)
        total_penalty += weight
        all_reasons.append(f"问询风险(-{weight:.0f})")

    # ── 4. FINANCIAL_QUALITY_RISK ──
    snippets, refs, weight = _match_keywords(texts, _FINANCIAL_QUALITY_KW, FLAG_FINANCIAL_QUALITY_RISK)
    if weight > 0:
        all_flags.append(FLAG_FINANCIAL_QUALITY_RISK)
        all_refs.extend(refs)
        total_penalty += weight
        all_reasons.append(f"财务质量风险(-{weight:.0f})")

    # ── 5. MARGIN_CROWDING_RISK ──
    # Must distinguish HAS_DATA / NOT_QUERIED / FAILED.
    # Only generate risk when status is HAS_DATA and texts indicate crowding.
    if margin_status == "HAS_DATA":
        margin_input = list(texts)
        if margin_texts:
            margin_input.extend([t for t in margin_texts if t])
        snippets, refs, weight = _match_keywords(
            margin_input, _MARGIN_CROWDING_KW, FLAG_MARGIN_CROWDING_RISK,
        )
        if weight > 0:
            all_flags.append(FLAG_MARGIN_CROWDING_RISK)
            all_refs.extend(refs)
            total_penalty += weight
            all_reasons.append(f"融资拥挤风险(-{weight:.0f})")
    # NOT_QUERIED / FAILED / NORMAL_NO_DATA → do NOT generate margin risk

    # ── 6. LHB_OVERHEAT_RISK ──
    # Must distinguish HAS_DATA / NOT_QUERIED / FAILED.
    if lhb_status == "HAS_DATA":
        lhb_input = list(texts)
        if lhb_texts:
            lhb_input.extend([t for t in lhb_texts if t])
        snippets, refs, weight = _match_keywords(
            lhb_input, _LHB_OVERHEAT_KW, FLAG_LHB_OVERHEAT_RISK,
        )
        if weight > 0:
            all_flags.append(FLAG_LHB_OVERHEAT_RISK)
            all_refs.extend(refs)
            total_penalty += weight
            all_reasons.append(f"龙虎榜过热风险(-{weight:.0f})")
    # NOT_QUERIED / FAILED / NORMAL_NO_DATA → do NOT generate LHB risk

    total_penalty = min(total_penalty, MAX_RISK_PENALTY)

    if not all_flags:
        return UnderwaterRiskResult()

    return UnderwaterRiskResult(
        risk_flags=sorted(set(all_flags)),
        risk_penalty=round(-total_penalty, 2),
        risk_evidence_refs=all_refs,
        risk_reasons=all_reasons,
    )
