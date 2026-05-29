# [S-002] narrative_quality_score
"""Narrative Quality Score — assess event quality vs propagation potential.

Outputs:
- narrative_score: 0-40 bonus score reflecting how "investable" and
  "consensus-forming" the event narrative is.
- narrative_reasons: list of reason strings for daily plan display.
- narrative_evidence_refs: list of {dimension, matched_text, source} dicts.

Five dimensions (each 0-10, capped at 40 total):
  1. POLICY_BACKING    — policy endorsement / government tailwind
  2. INDUSTRY_LANDING  — real industry implementation, order/revenue/bidding
  3. CORPORATE_ACTION  — buyback, major order, winning bid, M&A,
                         earnings preview, rating change
  4. PROPAGATION_CLARITY — easy to understand, single clear thesis
  5. CROWDING_DEDUCTION — overheating / crowded / excessive hype → deduction

Rules:
- No external LLM calls. Keyword / regex only.
- narrative_score is 0 when no event texts are provided.
- Repeated / duplicate events do not stack — each dimension scored once.
- Score does NOT replace VCP/Pullback/Event strategies; it is a bonus layer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class NarrativeQualityResult:
    narrative_score: float = 0.0
    narrative_reasons: list[str] = field(default_factory=list)
    narrative_evidence_refs: list[dict] = field(default_factory=list)


MAX_NARRATIVE_SCORE = 40.0

# ── Dimension 1: POLICY_BACKING (0-10) ──
_POLICY_BACKING_KW = [
    (re.compile(r"国务院|发改委|工信部|证监会|商务部|科技部|财政部|中央"), 8),
    (re.compile(r"政策支持|重点扶持|产业政策|国家战略|重点支持"), 6),
    (re.compile(r"补贴|税收优惠|专项资金|财政支持"), 4),
    (re.compile(r"规划|行动计划|实施方案|指导意见"), 3),
]

# ── Dimension 2: INDUSTRY_LANDING (0-10) ──
_INDUSTRY_LANDING_KW = [
    (re.compile(r"中标|签约|落地|投产|量产|产能|产线"), 8),
    (re.compile(r"订单|大单|合同金额|框架协议"), 7),
    (re.compile(r"收入增长|营收|业绩|利润|净利润|毛利率"), 5),
    (re.compile(r"市场份额|市占率|渗透率"), 4),
    (re.compile(r"商业化|应用场景|试点|示范"), 3),
]

# ── Dimension 3: CORPORATE_ACTION (0-10) ──
# Each corporate action type has a distinct base score per the task spec.
_CORPORATE_ACTION_RULES: list[tuple[re.Pattern, str, int]] = [
    (re.compile(r"回购(?:计划|方案|进展|实施)"), "回购", 8),
    (re.compile(r"回购"), "回购(一般)", 6),
    (re.compile(r"重大合同|重大订单|大额订单|超级订单"), "重大订单", 8),
    (re.compile(r"中标|竞标成功"), "中标", 7),
    (re.compile(r"并购|重组|吸收合并|战略重组|重大资产重组"), "并购重组", 9),
    (re.compile(r"业绩预告.*(?:增长|大幅上升|扭亏)"), "业绩预告(利好)", 7),
    (re.compile(r"业绩预告"), "业绩预告", 4),
    (re.compile(r"评级上调|目标价上调|首次覆盖|维持买入|维持增持"), "评级变化(利好)", 7),
    (re.compile(r"评级下调|目标价下调"), "评级变化(利空)", -3),
    (re.compile(r"增持(?:计划|方案)?"), "增持", 6),
    (re.compile(r"股权激励"), "股权激励", 4),
    (re.compile(r"分红|派息|特别分红"), "分红", 3),
]

# ── Dimension 4: PROPAGATION_CLARITY (0-10) ──
# Clear single-theme events score higher than ambiguous multi-theme noise.
_PROPAGATION_CLARITY_PATTERNS = [
    (re.compile(r"龙头|标杆|核心|头部"), 3),
    (re.compile(r"唯一|首家|独家|首个|第一"), 3),
    (re.compile(r"突破|创新|首创|颠覆"), 2),
    (re.compile(r"确定性|高景气|高增长|持续增长"), 2),
]
_PROPAGATION_NOISE_KW = re.compile(
    r"可能|或许|传闻|不确定|待定|暂无|尚需|未知|预计.*不确定"
)

# ── Dimension 5: CROWDING_DEDUCTION (0 to -10) ──
_CROWDING_DEDUCTION_KW = [
    (re.compile(r"过热|暴涨|疯涨|狂飙|连续涨停|一字涨停"), 8),
    (re.compile(r"游资炒作|庄家|操纵|投机|击鼓传花"), 7),
    (re.compile(r"概念炒作|题材炒作|纯概念|蹭热点"), 6),
    (re.compile(r"高位放量|天量|换手率.*超|异动"), 4),
    (re.compile(r"风险提示|异常波动|监管关注|问询函"), 3),
]


def _score_dimension(
    texts: list[str],
    rules: list[tuple[re.Pattern, int]],
    max_dim: int,
) -> tuple[float, list[str], list[dict]]:
    score = 0.0
    reasons: list[str] = []
    refs: list[dict] = []
    for kw, weight in rules:
        for text in texts:
            if kw.search(text):
                score = max(score, weight)
                reasons.append(kw.pattern[:20])
                refs.append({"matched_text": text[:100], "weight": weight})
                break
    return min(score, max_dim), reasons, refs


def score_narrative_quality(
    event_texts: list[str] | None = None,
) -> NarrativeQualityResult:
    if not event_texts:
        return NarrativeQualityResult()

    texts = [t for t in event_texts if t]
    if not texts:
        return NarrativeQualityResult()

    combined = "\n".join(texts)
    all_reasons: list[str] = []
    all_refs: list[dict] = []
    total_score = 0.0

    # ── Dimension 1: POLICY_BACKING ──
    d1_score, d1_reasons, d1_refs = _score_dimension(texts, _POLICY_BACKING_KW, 10)
    if d1_score > 0:
        all_reasons.append(f"政策背书(+) ")
        for r in d1_refs:
            r["dimension"] = "POLICY_BACKING"
        all_refs.extend(d1_refs)
        total_score += d1_score

    # ── Dimension 2: INDUSTRY_LANDING ──
    d2_score, d2_reasons, d2_refs = _score_dimension(texts, _INDUSTRY_LANDING_KW, 10)
    if d2_score > 0:
        all_reasons.append(f"产业落地(+) ")
        for r in d2_refs:
            r["dimension"] = "INDUSTRY_LANDING"
        all_refs.extend(d2_refs)
        total_score += d2_score

    # ── Dimension 3: CORPORATE_ACTION ──
    best_ca_score = 0.0
    best_ca_label = ""
    ca_ref = None
    for kw, label, base in _CORPORATE_ACTION_RULES:
        for text in texts:
            if kw.search(text):
                if abs(base) > abs(best_ca_score):
                    best_ca_score = base
                    best_ca_label = label
                    ca_ref = {"matched_text": text[:100], "label": label, "base_score": base}
                break
    if best_ca_score > 0:
        all_reasons.append(f"公司动作: {best_ca_label}(+{best_ca_score})")
        if ca_ref:
            ca_ref["dimension"] = "CORPORATE_ACTION"
            all_refs.append(ca_ref)
        total_score += min(best_ca_score, 10)
    elif best_ca_score < 0:
        all_reasons.append(f"公司动作: {best_ca_label}({best_ca_score})")
        if ca_ref:
            ca_ref["dimension"] = "CORPORATE_ACTION"
            all_refs.append(ca_ref)

    # ── Dimension 4: PROPAGATION_CLARITY ──
    clarity_score = 0.0
    clarity_refs: list[dict] = []
    for kw, weight in _PROPAGATION_CLARITY_PATTERNS:
        for text in texts:
            if kw.search(text):
                clarity_score += weight
                clarity_refs.append({"matched_text": text[:100], "weight": weight})
                break
    noise_match = _PROPAGATION_NOISE_KW.search(combined)
    if noise_match:
        clarity_score = max(0, clarity_score - 3)
    clarity_score = min(clarity_score, 10)
    if clarity_score > 0:
        all_reasons.append(f"传播清晰度(+{clarity_score:.0f})")
        for r in clarity_refs:
            r["dimension"] = "PROPAGATION_CLARITY"
        all_refs.extend(clarity_refs)
        total_score += clarity_score

    # ── Dimension 5: CROWDING_DEDUCTION ──
    crowd_deduction = 0.0
    crowd_refs: list[dict] = []
    for kw, weight in _CROWDING_DEDUCTION_KW:
        for text in texts:
            if kw.search(text):
                crowd_deduction += weight
                crowd_refs.append({"matched_text": text[:100], "weight": weight})
                break
    crowd_deduction = min(crowd_deduction, 10)
    if crowd_deduction > 0:
        all_reasons.append(f"拥挤过热(-{crowd_deduction:.0f})")
        for r in crowd_refs:
            r["dimension"] = "CROWDING_DEDUCTION"
        all_refs.extend(crowd_refs)
        total_score -= crowd_deduction

    total_score = max(0.0, min(total_score, MAX_NARRATIVE_SCORE))
    total_score = round(total_score, 2)

    if total_score <= 0 and not all_refs:
        return NarrativeQualityResult()

    return NarrativeQualityResult(
        narrative_score=total_score,
        narrative_reasons=all_reasons,
        narrative_evidence_refs=all_refs,
    )
