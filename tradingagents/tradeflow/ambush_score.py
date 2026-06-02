# [H-004] mandate_ambush_score
"""Ambush Score & Candidate Type Classification — core of 昊天候选池.

Introduces the 昊天 candidate pool classification:
- POLICY_AMBUSH: policy left-side ambush — strong mandate, clear beneficiary path,
  price has NOT yet broken out. Requires policy continuity, company evidence,
  manageable risk, not overheated.
- POLICY_CONFIRM: policy right-side confirmation — mandate + technical/fund confirmation.
- TECH_TRADE: pure technical pattern without policy evidence. Short-term / T+0.
- EVENT_WATCH: event-driven observation without full mandate chain.
- PSEUDO_POLICY: pseudo-policy theme — weak evidence, concept-only, media noise.
- OVERHEATED_AVOID: overheated / high-position / risk-crowded, avoid chasing.

Components:
- CandidateType enum: six candidate types.
- AmbushScoreResult: output of ambush scoring.
- compute_ambush_score(): main scoring function.
- classify_candidate_type(): determine candidate type from score components.
- route_deep_ta(): determine deep TA routing based on candidate type.

Constraints:
- No buy/sell strong suggestions.
- No auto TA trigger.
- No mixing pure-tech into policy ambush pool.
- All high scores must have evidence refs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .strategy_config import StrategyConfig, DEFAULT_STRATEGY_CONFIG  # [M-004]


class CandidateType(Enum):
    POLICY_AMBUSH = "POLICY_AMBUSH"
    POLICY_CONFIRM = "POLICY_CONFIRM"
    TECH_TRADE = "TECH_TRADE"
    EVENT_WATCH = "EVENT_WATCH"
    PSEUDO_POLICY = "PSEUDO_POLICY"
    OVERHEATED_AVOID = "OVERHEATED_AVOID"
    UNCLASSIFIED_DATA_GAP = "UNCLASSIFIED_DATA_GAP"  # [TF-P0-002] tradeflow_pool_split


CANDIDATE_TYPE_LABELS = {
    CandidateType.POLICY_AMBUSH: "政策左侧埋伏",
    CandidateType.POLICY_CONFIRM: "政策右侧确认",
    CandidateType.TECH_TRADE: "技术交易",
    CandidateType.EVENT_WATCH: "事件观察",
    CandidateType.UNCLASSIFIED_DATA_GAP: "证据缺口",  # [TF-P0-002]
    CandidateType.PSEUDO_POLICY: "伪政策题材",
    CandidateType.OVERHEATED_AVOID: "过热规避",
}

_AMBUSH_SCORE_MAX = 100.0
_MANDATE_SCORE_WEIGHT = 0.35
_BENEFICIARY_SCORE_WEIGHT = 0.30
_PRICING_GAP_SCORE_WEIGHT = 0.20
_OVERHEAT_PENALTY_WEIGHT = 0.15


@dataclass
class AmbushScoreResult:
    ambush_score: float = 0.0
    mandate_score_component: float = 0.0
    beneficiary_score_component: float = 0.0
    pricing_gap_score: float = 0.0
    overheat_penalty: float = 0.0
    candidate_type: str = CandidateType.TECH_TRADE.value
    candidate_type_reason: str = ""
    ambush_reasons: list[str] = field(default_factory=list)
    ambush_evidence_refs: list[dict] = field(default_factory=list)
    deep_ta_route: str = ""
    deep_ta_route_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "ambush_score": round(self.ambush_score, 2),
            "mandate_score_component": round(self.mandate_score_component, 2),
            "beneficiary_score_component": round(self.beneficiary_score_component, 2),
            "pricing_gap_score": round(self.pricing_gap_score, 2),
            "overheat_penalty": round(self.overheat_penalty, 2),
            "candidate_type": self.candidate_type,
            "candidate_type_reason": self.candidate_type_reason,
            "ambush_reasons": self.ambush_reasons,
            "ambush_evidence_refs": self.ambush_evidence_refs,
            "deep_ta_route": self.deep_ta_route,
            "deep_ta_route_reason": self.deep_ta_route_reason,
        }


def _compute_mandate_score_component(
    mandate_score: float = 0.0,
    version_score: float = 0.0,
    policy_tags: Optional[list[str]] = None,
    has_policy_document: bool = False,
    has_high_authority: bool = False,
    unique_dates: int = 0,
    is_noise: bool = False,
) -> tuple[float, list[str]]:
    reasons: list[str] = []

    effective_mandate = max(mandate_score, version_score * 1.5)

    if is_noise:
        effective_mandate *= 0.3
        reasons.append("mandate_noise:降权")

    if not policy_tags and mandate_score == 0 and version_score == 0:
        reasons.append("no_policy_evidence")
        return 0.0, reasons

    if has_policy_document:
        effective_mandate = min(effective_mandate * 1.1, 100.0)
        reasons.append("has_policy_document:加成")

    if has_high_authority:
        effective_mandate = min(effective_mandate * 1.05, 100.0)
        reasons.append("has_high_authority:加成")

    if unique_dates >= 3:
        effective_mandate = min(effective_mandate * 1.05, 100.0)
        reasons.append(f"multi_day_continuity:{unique_dates}days")

    normalized = min(effective_mandate / 100.0, 1.0) * 100.0
    return round(normalized, 2), reasons


def _compute_beneficiary_score_component(
    company_role: str = "",
    beneficiary_path: Optional[list[str]] = None,
    has_company_evidence: bool = False,
    path_confidence: float = 0.0,
) -> tuple[float, list[str]]:
    reasons: list[str] = []

    if not company_role or company_role in ("UNKNOWN", ""):
        reasons.append("no_company_role")
        return 0.0, reasons

    if not beneficiary_path:
        reasons.append("no_beneficiary_path")
        return 0.0, reasons

    role_score_map = {
        "LEADER": 100.0,
        "CORE_SUPPLIER": 85.0,
        "INFRA_PROVIDER": 70.0,
        "APPLICATION_SCENE": 60.0,
        "PERIPHERAL": 35.0,
        "CONCEPT_ONLY": 15.0,
    }
    base_score = role_score_map.get(company_role, 0.0)

    if not has_company_evidence:
        base_score *= 0.4
        reasons.append("no_company_evidence:降权")
    else:
        reasons.append("has_company_evidence")

    path_bonus = min(len(beneficiary_path) * 8, 20.0)
    base_score += path_bonus

    if path_confidence > 0:
        conf_factor = min(path_confidence, 1.0)
        base_score *= (0.5 + 0.5 * conf_factor)
        reasons.append(f"path_confidence:{path_confidence:.2f}")

    reasons.append(f"role:{company_role}")
    reasons.append(f"path_segments:{len(beneficiary_path)}")

    return round(min(base_score, 100.0), 2), reasons


def _compute_pricing_gap_score(
    has_tech_breakout: bool = False,
    trigger_price: Optional[float] = None,
    current_price: Optional[float] = None,
    strategy_tags: Optional[list[str]] = None,
    score: float = 0.0,
) -> tuple[float, list[str]]:
    reasons: list[str] = []
    tags = set(strategy_tags or [])

    has_vcp = "VCP" in tags
    has_pullback = "PULLBACK_SUPPORT" in tags

    if has_tech_breakout or (trigger_price and current_price and current_price >= trigger_price):
        gap_score = 80.0
        reasons.append("breakout_confirmed")
    elif has_vcp or has_pullback:
        if trigger_price and current_price:
            distance = abs(trigger_price - current_price) / max(current_price, 0.01)
            if distance <= 0.03:
                gap_score = 65.0
                reasons.append("near_trigger:<=3%")
            elif distance <= 0.08:
                gap_score = 50.0
                reasons.append("approaching_trigger:3-8%")
            else:
                gap_score = 30.0
                reasons.append("far_from_trigger:>8%")
        else:
            gap_score = 40.0
            reasons.append("tech_pattern_no_price_context")
    else:
        gap_score = 0.0
        reasons.append("no_tech_signal")

    if score > 0:
        tech_bonus = min(score * 0.3, 15.0)
        gap_score += tech_bonus
        reasons.append(f"tech_score_bonus:{tech_bonus:.1f}")

    return round(min(gap_score, 100.0), 2), reasons


def _compute_overheat_penalty(
    risk_flags: Optional[list[str]] = None,
    risk_penalty: float = 0.0,
    game_balance: str = "",
    fund_flow_anomaly_tags: Optional[list[str]] = None,
    narrative_score: float = 0.0,
) -> tuple[float, list[str]]:
    reasons: list[str] = []
    r_flags = set(risk_flags or [])
    ff_tags = set(fund_flow_anomaly_tags or [])

    penalty = 0.0

    if "LHB_OVERHEAT_RISK" in r_flags:
        penalty += 25.0
        reasons.append("LHB_OVERHEAT_RISK:+25")
    if "MARGIN_CROWDING_RISK" in r_flags:
        penalty += 20.0
        reasons.append("MARGIN_CROWDING_RISK:+20")
    if "NET_OUTFLOW_DOMINANT" in ff_tags:
        penalty += 15.0
        reasons.append("NET_OUTFLOW_DOMINANT:+15")

    if risk_penalty < -15:
        penalty += min(abs(risk_penalty) * 0.8, 20.0)
        reasons.append(f"heavy_risk_penalty:{risk_penalty}")

    if game_balance == "crowded":
        penalty += 20.0
        reasons.append("game_balance_crowded:+20")
    elif game_balance == "fragile":
        penalty += 25.0
        reasons.append("game_balance_fragile:+25")

    if narrative_score > 40:
        penalty += min((narrative_score - 40) * 0.3, 10.0)
        reasons.append(f"high_narrative_possible_overheat:{narrative_score}")

    if len(r_flags) >= 3:
        penalty += 10.0
        reasons.append("too_many_risk_flags:+10")

    return round(min(penalty, 100.0), 2), reasons


def compute_ambush_score(
    mandate_score: float = 0.0,
    version_score: float = 0.0,
    policy_tags: Optional[list[str]] = None,
    has_policy_document: bool = False,
    has_high_authority: bool = False,
    unique_dates: int = 0,
    is_noise: bool = False,
    company_role: str = "",
    beneficiary_path: Optional[list[str]] = None,
    has_company_evidence: bool = False,
    path_confidence: float = 0.0,
    has_tech_breakout: bool = False,
    trigger_price: Optional[float] = None,
    current_price: Optional[float] = None,
    strategy_tags: Optional[list[str]] = None,
    tech_score: float = 0.0,
    risk_flags: Optional[list[str]] = None,
    risk_penalty: float = 0.0,
    game_balance: str = "",
    fund_flow_anomaly_tags: Optional[list[str]] = None,
    narrative_score: float = 0.0,
    mandate_evidence_refs: Optional[list[dict]] = None,
    mandate_reasons: Optional[list[str]] = None,
    data_completeness: float = 1.0,  # [TF-P0-002] tradeflow_pool_split
    cfg: Optional[StrategyConfig] = None,
) -> AmbushScoreResult:
    if cfg is None:
        cfg = DEFAULT_STRATEGY_CONFIG

    mandate_comp, mandate_reasons_list = _compute_mandate_score_component(
        mandate_score=mandate_score,
        version_score=version_score,
        policy_tags=policy_tags,
        has_policy_document=has_policy_document,
        has_high_authority=has_high_authority,
        unique_dates=unique_dates,
        is_noise=is_noise,
    )

    beneficiary_comp, beneficiary_reasons_list = _compute_beneficiary_score_component(
        company_role=company_role,
        beneficiary_path=beneficiary_path,
        has_company_evidence=has_company_evidence,
        path_confidence=path_confidence,
    )

    pricing_comp, pricing_reasons_list = _compute_pricing_gap_score(
        has_tech_breakout=has_tech_breakout,
        trigger_price=trigger_price,
        current_price=current_price,
        strategy_tags=strategy_tags,
        score=tech_score,
    )

    overheat_pen, overheat_reasons_list = _compute_overheat_penalty(
        risk_flags=risk_flags,
        risk_penalty=risk_penalty,
        game_balance=game_balance,
        fund_flow_anomaly_tags=fund_flow_anomaly_tags,
        narrative_score=narrative_score,
    )

    raw_ambush = (
        mandate_comp * _MANDATE_SCORE_WEIGHT
        + beneficiary_comp * _BENEFICIARY_SCORE_WEIGHT
        + pricing_comp * _PRICING_GAP_SCORE_WEIGHT
        - overheat_pen * _OVERHEAT_PENALTY_WEIGHT
    )
    ambush_score = round(max(0.0, min(raw_ambush, _AMBUSH_SCORE_MAX)), 2)

    all_reasons = mandate_reasons_list + beneficiary_reasons_list + pricing_reasons_list + overheat_reasons_list

    evidence_refs = list(mandate_evidence_refs or [])

    candidate_type, type_reason = classify_candidate_type(
        ambush_score=ambush_score,
        mandate_score_component=mandate_comp,
        beneficiary_score_component=beneficiary_comp,
        pricing_gap_score=pricing_comp,
        overheat_penalty=overheat_pen,
        has_policy=bool(policy_tags or mandate_score > 0 or version_score > 0),
        has_beneficiary_path=bool(beneficiary_path and company_role not in ("", "UNKNOWN", "CONCEPT_ONLY")),
        has_tech_breakout=has_tech_breakout or pricing_comp >= 65.0,
        is_overheated=overheat_pen >= 30.0,
        is_noise=is_noise,
        risk_flags=risk_flags,
        game_balance=game_balance,
        data_completeness=data_completeness,  # [TF-P0-002]
    )

    deep_ta_route, deep_ta_route_reason = route_deep_ta(
        candidate_type=candidate_type,
        ambush_score=ambush_score,
        has_policy=bool(policy_tags or mandate_score > 0),
        has_beneficiary_path=bool(beneficiary_path and company_role not in ("", "UNKNOWN", "CONCEPT_ONLY")),
    )

    return AmbushScoreResult(
        ambush_score=ambush_score,
        mandate_score_component=round(mandate_comp, 2),
        beneficiary_score_component=round(beneficiary_comp, 2),
        pricing_gap_score=round(pricing_comp, 2),
        overheat_penalty=round(overheat_pen, 2),
        candidate_type=candidate_type.value,
        candidate_type_reason=type_reason,
        ambush_reasons=all_reasons,
        ambush_evidence_refs=evidence_refs,
        deep_ta_route=deep_ta_route,
        deep_ta_route_reason=deep_ta_route_reason,
    )


def classify_candidate_type(
    ambush_score: float = 0.0,
    mandate_score_component: float = 0.0,
    beneficiary_score_component: float = 0.0,
    pricing_gap_score: float = 0.0,
    overheat_penalty: float = 0.0,
    has_policy: bool = False,
    has_beneficiary_path: bool = False,
    has_tech_breakout: bool = False,
    is_overheated: bool = False,
    is_noise: bool = False,
    risk_flags: Optional[list[str]] = None,
    game_balance: str = "",
    data_completeness: float = 1.0,  # [TF-P0-002] tradeflow_pool_split
) -> tuple[CandidateType, str]:
    r_flags = set(risk_flags or [])

    if data_completeness < 0.3 and not has_policy and not has_tech_breakout:  # [TF-P0-002]
        return CandidateType.UNCLASSIFIED_DATA_GAP, f"证据缺口: 数据完整度{data_completeness:.0%}<30%, 无法分类"

    if is_overheated:
        return CandidateType.OVERHEATED_AVOID, "过热/高位/风险拥挤，规避追涨"

    if game_balance == "fragile":
        return CandidateType.OVERHEATED_AVOID, "博弈fragile，结构脆弱"

    high_risk = {"INQUIRY_RISK", "FINANCIAL_QUALITY_RISK"}
    if bool(r_flags & high_risk):
        return CandidateType.OVERHEATED_AVOID, f"高风险标签({', '.join(r_flags & high_risk)})"

    if has_policy and has_beneficiary_path and mandate_score_component >= 40.0:
        if beneficiary_score_component >= 35.0:
            if not has_tech_breakout:
                return CandidateType.POLICY_AMBUSH, f"政策左侧埋伏: mandate={mandate_score_component:.0f}, beneficiary={beneficiary_score_component:.0f}, 未突破"
            else:
                return CandidateType.POLICY_CONFIRM, f"政策右侧确认: mandate={mandate_score_component:.0f}, beneficiary={beneficiary_score_component:.0f}, 已突破"

    if has_policy and mandate_score_component >= 20.0 and not has_beneficiary_path:
        if is_noise:
            return CandidateType.PSEUDO_POLICY, f"伪政策题材: mandate弱({mandate_score_component:.0f}), noise=True, 无受益路径"
        if mandate_score_component < 30.0:
            return CandidateType.EVENT_WATCH, f"事件观察: mandate偏低({mandate_score_component:.0f}), 无受益路径"
        return CandidateType.PSEUDO_POLICY, f"伪政策题材: mandate={mandate_score_component:.0f}, 有政策信号但无公司受益路径"

    if has_policy and mandate_score_component < 20.0:
        if has_tech_breakout:
            return CandidateType.TECH_TRADE, f"技术交易: 有弱政策({mandate_score_component:.0f})但技术主导"
        return CandidateType.EVENT_WATCH, f"事件观察: 政策弱({mandate_score_component:.0f}), 无技术确认"

    if not has_policy:
        if has_tech_breakout or pricing_gap_score >= 50.0:
            return CandidateType.TECH_TRADE, f"技术交易: 无政策证据, pricing_gap={pricing_gap_score:.0f}"
        return CandidateType.EVENT_WATCH, "事件观察: 无政策证据, 无技术形态"

    return CandidateType.EVENT_WATCH, "事件观察: 默认分类"


def route_deep_ta(
    candidate_type: CandidateType = CandidateType.TECH_TRADE,
    ambush_score: float = 0.0,
    has_policy: bool = False,
    has_beneficiary_path: bool = False,
) -> tuple[str, str]:
    if candidate_type == CandidateType.POLICY_AMBUSH:
        return "policy_verify", "中线研究/政策验证队列：优先验证政策连续性和受益路径"
    elif candidate_type == CandidateType.POLICY_CONFIRM:
        if has_beneficiary_path:
            return "deep_ta", "可进入TA深度分析：政策+受益路径+技术确认"
        return "policy_verify", "政策确认但受益路径不明确，先验证受益链"
    elif candidate_type == CandidateType.TECH_TRADE:
        return "short_term", "短线/做T，不混入主池"
    elif candidate_type == CandidateType.EVENT_WATCH:
        if ambush_score >= 30:
            return "observe", "事件观察，等待更多证据"
        return "skip", "事件信号不足，暂不深挖"
    elif candidate_type == CandidateType.PSEUDO_POLICY:
        return "skip", "伪政策题材，不消耗TA预算"
    elif candidate_type == CandidateType.OVERHEATED_AVOID:
        return "skip", "过热规避，不追涨"
    return "skip", "默认不深挖"
