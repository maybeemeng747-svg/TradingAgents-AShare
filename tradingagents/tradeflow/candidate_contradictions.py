# [H-011] candidate_contradiction_explainer
"""
Candidate contradiction aggregator — "why this might be wrong".

Cross-examines policy logic, company benefit path, fund flow, technicals,
data completeness and risk flags to surface contradictions, rather than
only showing the bullish narrative.

Output fields on Candidate:
  - contradiction_level  (none / low / medium / high)
  - contradiction_items   (list of dicts with type / dimension_a / dimension_b / description)
  - blocking_evidence_gaps (list of strings — what must be resolved before confidence)
  - next_verification_steps (list of strings — suggested next steps)

Constraints:
  - No LLM calls.
  - No buy/sell suggestions.
  - Does not treat unknown items as confirmed negatives.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


CONTRADICTION_NONE = "none"
CONTRADICTION_LOW = "low"
CONTRADICTION_MEDIUM = "medium"
CONTRADICTION_HIGH = "high"

ALL_CONTRADICTION_LEVELS = [CONTRADICTION_NONE, CONTRADICTION_LOW, CONTRADICTION_MEDIUM, CONTRADICTION_HIGH]

CONTRADICTION_TYPE_POLICY_COMPANY = "policy_strong_company_weak"
CONTRADICTION_TYPE_THEME_FUNDING = "theme_hot_funding_cold"
CONTRADICTION_TYPE_FUNDAMENTAL_THEME = "fundamental_weak_theme_strong"
CONTRADICTION_TYPE_TECH_THESIS = "tech_broken_thesis_intact"
CONTRADICTION_TYPE_DATA_SCORE = "data_sparse_score_high"
CONTRADICTION_TYPE_RISK_NARRATIVE = "risk_high_narrative_bullish"

ALL_CONTRADICTION_TYPES = [
    CONTRADICTION_TYPE_POLICY_COMPANY,
    CONTRADICTION_TYPE_THEME_FUNDING,
    CONTRADICTION_TYPE_FUNDAMENTAL_THEME,
    CONTRADICTION_TYPE_TECH_THESIS,
    CONTRADICTION_TYPE_DATA_SCORE,
    CONTRADICTION_TYPE_RISK_NARRATIVE,
]

CONTRADICTION_LABELS = {
    CONTRADICTION_TYPE_POLICY_COMPANY: "政策强但公司路径弱",
    CONTRADICTION_TYPE_THEME_FUNDING: "主题热但资金不认",
    CONTRADICTION_TYPE_FUNDAMENTAL_THEME: "基本面差但题材强",
    CONTRADICTION_TYPE_TECH_THESIS: "技术破位但中线逻辑未破",
    CONTRADICTION_TYPE_DATA_SCORE: "数据完整度低但分数高",
    CONTRADICTION_TYPE_RISK_NARRATIVE: "风险高但叙事偏乐观",
}


@dataclass
class ContradictionItem:
    contradiction_type: str = ""
    dimension_a: str = ""
    dimension_b: str = ""
    description: str = ""
    severity: float = 0.0

    def to_dict(self) -> dict:
        return {
            "contradiction_type": self.contradiction_type,
            "dimension_a": self.dimension_a,
            "dimension_b": self.dimension_b,
            "description": self.description,
            "severity": round(self.severity, 2),
        }


@dataclass
class ContradictionResult:
    contradiction_level: str = CONTRADICTION_NONE
    contradiction_items: list[dict] = field(default_factory=list)
    blocking_evidence_gaps: list[str] = field(default_factory=list)
    next_verification_steps: list[str] = field(default_factory=list)
    _items_raw: list[ContradictionItem] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "contradiction_level": self.contradiction_level,
            "contradiction_items": self.contradiction_items,
            "blocking_evidence_gaps": self.blocking_evidence_gaps,
            "next_verification_steps": self.next_verification_steps,
        }


def _check_policy_vs_company(
    policy_tags: Optional[list[str]] = None,
    version_score: float = 0.0,
    mandate_score_component: float = 0.0,
    has_policy_document: bool = False,
    company_role: str = "",
    beneficiary_path: Optional[list[str]] = None,
    has_company_evidence: bool = False,
) -> Optional[ContradictionItem]:
    has_policy = bool(policy_tags) or version_score >= 20 or mandate_score_component >= 20
    weak_company = company_role in ("", "UNKNOWN", "CONCEPT_ONLY") or not beneficiary_path or not has_company_evidence

    if not has_policy or not weak_company:
        return None

    severity = 0.0
    if has_policy_document and company_role in ("", "UNKNOWN"):
        severity = 0.9
    elif has_policy_document and company_role == "CONCEPT_ONLY":
        severity = 0.8
    elif has_policy and not has_company_evidence:
        severity = 0.7
    else:
        severity = 0.5

    policy_desc = "政策信号强"
    if has_policy_document:
        policy_desc += "(有政策原文)"
    company_desc = f"公司角色={company_role or '未知'}"
    if not beneficiary_path:
        company_desc += ", 无受益路径"

    return ContradictionItem(
        contradiction_type=CONTRADICTION_TYPE_POLICY_COMPANY,
        dimension_a=policy_desc,
        dimension_b=company_desc,
        description=f"{policy_desc}，但{company_desc}",
        severity=round(severity, 2),
    )


def _check_theme_vs_funding(
    narrative_score: float = 0.0,
    mandate_score_component: float = 0.0,
    fund_flow_anomaly_score: float = 0.0,
    fund_flow_anomaly_tags: Optional[list[str]] = None,
    fund_flow_unit_verified: bool = False,
) -> Optional[ContradictionItem]:
    theme_hot = narrative_score >= 30 or mandate_score_component >= 30
    ff_tags = set(fund_flow_anomaly_tags or [])
    funding_cold = fund_flow_anomaly_score <= 0 or "NET_OUTFLOW_DOMINANT" in ff_tags

    if not theme_hot or not funding_cold:
        return None

    severity = 0.0
    if "NET_OUTFLOW_DOMINANT" in ff_tags:
        severity = 0.8
    elif fund_flow_anomaly_score <= 0 and not fund_flow_unit_verified:
        severity = 0.7
    else:
        severity = 0.5

    theme_desc = f"叙事分={narrative_score:.0f}/政策分={mandate_score_component:.0f}"
    funding_desc = f"资金分={fund_flow_anomaly_score:.0f}"
    if "NET_OUTFLOW_DOMINANT" in ff_tags:
        funding_desc += "(主力净流出)"

    return ContradictionItem(
        contradiction_type=CONTRADICTION_TYPE_THEME_FUNDING,
        dimension_a=theme_desc,
        dimension_b=funding_desc,
        description=f"主题热度高({theme_desc})，但资金不认可({funding_desc})",
        severity=round(severity, 2),
    )


def _check_fundamental_vs_theme(
    risk_penalty: float = 0.0,
    risk_flags: Optional[list[str]] = None,
    narrative_score: float = 0.0,
    mandate_score_component: float = 0.0,
) -> Optional[ContradictionItem]:
    r_flags = set(risk_flags or [])
    fundamental_weak = risk_penalty <= -15 or bool(
        r_flags & {"FINANCIAL_QUALITY_RISK", "INQUIRY_RISK"}
    )
    theme_strong = narrative_score >= 40 or mandate_score_component >= 40

    if not fundamental_weak or not theme_strong:
        return None

    severity = 0.0
    if r_flags & {"FINANCIAL_QUALITY_RISK", "INQUIRY_RISK"}:
        severity = 0.9
    elif risk_penalty <= -25:
        severity = 0.8
    else:
        severity = 0.6

    fund_desc = f"风险惩罚={risk_penalty:.0f}"
    if r_flags & {"FINANCIAL_QUALITY_RISK", "INQUIRY_RISK"}:
        fund_desc += f"({', '.join(sorted(r_flags & {'FINANCIAL_QUALITY_RISK', 'INQUIRY_RISK'}))})"
    theme_desc = f"叙事分={narrative_score:.0f}/政策分={mandate_score_component:.0f}"

    return ContradictionItem(
        contradiction_type=CONTRADICTION_TYPE_FUNDAMENTAL_THEME,
        dimension_a=fund_desc,
        dimension_b=theme_desc,
        description=f"基本面偏弱({fund_desc})，但题材叙事偏强({theme_desc})",
        severity=round(severity, 2),
    )


def _check_tech_vs_thesis(
    game_balance: str = "",
    overheat_penalty: float = 0.0,
    candidate_type: str = "",
    mandate_score_component: float = 0.0,
) -> Optional[ContradictionItem]:
    tech_broken = game_balance in ("crowded", "fragile") or overheat_penalty >= 25
    thesis_intact = candidate_type in ("POLICY_AMBUSH", "POLICY_CONFIRM") and mandate_score_component >= 25

    if not tech_broken or not thesis_intact:
        return None

    severity = 0.0
    if game_balance in ("crowded", "fragile") and overheat_penalty >= 25:
        severity = 0.8
    elif game_balance in ("crowded", "fragile"):
        severity = 0.7
    else:
        severity = 0.5

    tech_desc = f"博弈={game_balance or '未评估'}, 过热惩罚={overheat_penalty:.0f}"
    thesis_desc = f"类型={candidate_type}, 政策分={mandate_score_component:.0f}"

    return ContradictionItem(
        contradiction_type=CONTRADICTION_TYPE_TECH_THESIS,
        dimension_a=tech_desc,
        dimension_b=thesis_desc,
        description=f"技术/博弈偏弱({tech_desc})，但中线政策逻辑仍完整({thesis_desc})",
        severity=round(severity, 2),
    )


def _check_data_vs_score(
    data_completeness: float = 0.0,
    tradeflow_data_completeness: float = 0.0,
    ambush_score: float = 0.0,
    composite_score: float = 0.0,
    missing_evidence: Optional[list[str]] = None,
) -> Optional[ContradictionItem]:
    comp = max(data_completeness, tradeflow_data_completeness)
    score_high = ambush_score >= 50 or composite_score >= 50
    data_sparse = comp < 0.4

    if not score_high or not data_sparse:
        return None

    severity = 0.0
    if comp < 0.2:
        severity = 0.9
    elif comp < 0.3:
        severity = 0.7
    else:
        severity = 0.5

    missing = missing_evidence or []
    data_desc = f"完整度={comp:.0%}"
    if missing:
        data_desc += f", 缺{len(missing)}项"
    score_desc = f"埋伏分={ambush_score:.0f}/综合分={composite_score:.0f}"

    return ContradictionItem(
        contradiction_type=CONTRADICTION_TYPE_DATA_SCORE,
        dimension_a=data_desc,
        dimension_b=score_desc,
        description=f"数据完整度低({data_desc})，但评分偏高({score_desc})",
        severity=round(severity, 2),
    )


def _check_risk_vs_narrative(
    risk_flags: Optional[list[str]] = None,
    risk_penalty: float = 0.0,
    narrative_score: float = 0.0,
    bull_case: str = "",
) -> Optional[ContradictionItem]:
    r_flags = set(risk_flags or [])
    high_risk = bool(
        r_flags & {"INQUIRY_RISK", "FINANCIAL_QUALITY_RISK", "LOCKUP_RISK", "REDUCE_HOLDING_RISK"}
    )
    narrative_bullish = narrative_score >= 30 or bool(bull_case)

    if not high_risk or not narrative_bullish:
        return None

    severity = 0.0
    high_risk_flags = r_flags & {"INQUIRY_RISK", "FINANCIAL_QUALITY_RISK", "LOCKUP_RISK", "REDUCE_HOLDING_RISK"}
    if r_flags & {"INQUIRY_RISK", "FINANCIAL_QUALITY_RISK"}:
        severity = 0.9
    elif len(high_risk_flags) >= 2:
        severity = 0.8
    else:
        severity = 0.6

    risk_desc = f"风险标签: {', '.join(sorted(high_risk_flags))}"
    narr_desc = f"叙事分={narrative_score:.0f}"
    if bull_case:
        narr_desc += f", 多头逻辑={bull_case[:30]}"

    return ContradictionItem(
        contradiction_type=CONTRADICTION_TYPE_RISK_NARRATIVE,
        dimension_a=risk_desc,
        dimension_b=narr_desc,
        description=f"存在高风险({risk_desc})，但叙事偏乐观({narr_desc})",
        severity=round(severity, 2),
    )


def _classify_contradiction_level(items: list[ContradictionItem]) -> str:
    if not items:
        return CONTRADICTION_NONE
    max_severity = max(item.severity for item in items)
    count = len(items)
    if max_severity >= 0.8 or count >= 4:
        return CONTRADICTION_HIGH
    if max_severity >= 0.6 or count >= 3:
        return CONTRADICTION_MEDIUM
    return CONTRADICTION_LOW


def _build_blocking_gaps(
    items: list[ContradictionItem],
    company_role: str = "",
    beneficiary_path: Optional[list[str]] = None,
    data_completeness: float = 0.0,
    tradeflow_data_completeness: float = 0.0,
    fund_flow_unit_verified: bool = False,
) -> list[str]:
    gaps: list[str] = []
    types = {item.contradiction_type for item in items}

    if CONTRADICTION_TYPE_POLICY_COMPANY in types:
        if company_role in ("", "UNKNOWN"):
            gaps.append("公司受益角色未明确")
        if not beneficiary_path:
            gaps.append("受益路径缺失")
        gaps.append("公司层证据(订单/合同/营收)缺失")

    if CONTRADICTION_TYPE_THEME_FUNDING in types:
        if not fund_flow_unit_verified:
            gaps.append("资金流数据单位未校验")
        gaps.append("主力资金连续3日净流入确认")

    if CONTRADICTION_TYPE_FUNDAMENTAL_THEME in types:
        gaps.append("基本面/财务指标核实")
        gaps.append("问询函/风险公告澄清状态")

    if CONTRADICTION_TYPE_TECH_THESIS in types:
        gaps.append("技术破位是否改变中线逻辑判断")

    if CONTRADICTION_TYPE_DATA_SCORE in types:
        comp = max(data_completeness, tradeflow_data_completeness)
        if comp < 0.3:
            gaps.append("核心数据完整度不足30%，评分不可信")
        else:
            gaps.append("关键数据字段补全")

    if CONTRADICTION_TYPE_RISK_NARRATIVE in types:
        gaps.append("风险事件进展核实")

    return list(dict.fromkeys(gaps))


def _build_verification_steps(
    items: list[ContradictionItem],
    candidate_type: str = "",
    company_role: str = "",
    mandate_topic: str = "",
) -> list[str]:
    steps: list[str] = []
    types = {item.contradiction_type for item in items}

    if CONTRADICTION_TYPE_POLICY_COMPANY in types:
        steps.append(f"核实{mandate_topic or '该主题'}下{company_role or '公司'}的实质订单/合同/营收关联")
        steps.append("查看最近公告是否披露具体业务进展")

    if CONTRADICTION_TYPE_THEME_FUNDING in types:
        steps.append("观察未来3个交易日主力资金流向是否转正")
        steps.append("检查龙虎榜是否有机构买入")

    if CONTRADICTION_TYPE_FUNDAMENTAL_THEME in types:
        steps.append("阅读最新财报/问询函回复，判断基本面是否实质恶化")
        steps.append("区分短期风险与长期逻辑是否冲突")

    if CONTRADICTION_TYPE_TECH_THESIS in types:
        if candidate_type in ("POLICY_AMBUSH", "POLICY_CONFIRM"):
            steps.append("确认技术破位是洗盘还是逻辑失败")
            steps.append("若政策逻辑未变，技术破位可能提供更好入场点，但需风控确认")
        else:
            steps.append("技术破位时不宜加仓")

    if CONTRADICTION_TYPE_DATA_SCORE in types:
        steps.append("补全缺失数据后重新评估评分")
        steps.append("低完整度高评分不做决策依据")

    if CONTRADICTION_TYPE_RISK_NARRATIVE in types:
        steps.append("跟踪风险事件后续公告")
        steps.append("评估风险是否已充分定价")

    return list(dict.fromkeys(steps))


def evaluate_contradictions(
    candidate_type: str = "",
    policy_tags: Optional[list[str]] = None,
    version_score: float = 0.0,
    mandate_score_component: float = 0.0,
    has_policy_document: bool = False,
    company_role: str = "",
    beneficiary_path: Optional[list[str]] = None,
    has_company_evidence: bool = False,
    narrative_score: float = 0.0,
    fund_flow_anomaly_score: float = 0.0,
    fund_flow_anomaly_tags: Optional[list[str]] = None,
    fund_flow_unit_verified: bool = False,
    risk_penalty: float = 0.0,
    risk_flags: Optional[list[str]] = None,
    game_balance: str = "",
    overheat_penalty: float = 0.0,
    ambush_score: float = 0.0,
    composite_score: float = 0.0,
    data_completeness: float = 0.0,
    tradeflow_data_completeness: float = 0.0,
    missing_evidence: Optional[list[str]] = None,
    bull_case: str = "",
    mandate_topic: str = "",
) -> ContradictionResult:
    items: list[ContradictionItem] = []

    c1 = _check_policy_vs_company(
        policy_tags=policy_tags,
        version_score=version_score,
        mandate_score_component=mandate_score_component,
        has_policy_document=has_policy_document,
        company_role=company_role,
        beneficiary_path=beneficiary_path,
        has_company_evidence=has_company_evidence,
    )
    if c1:
        items.append(c1)

    c2 = _check_theme_vs_funding(
        narrative_score=narrative_score,
        mandate_score_component=mandate_score_component,
        fund_flow_anomaly_score=fund_flow_anomaly_score,
        fund_flow_anomaly_tags=fund_flow_anomaly_tags,
        fund_flow_unit_verified=fund_flow_unit_verified,
    )
    if c2:
        items.append(c2)

    c3 = _check_fundamental_vs_theme(
        risk_penalty=risk_penalty,
        risk_flags=risk_flags,
        narrative_score=narrative_score,
        mandate_score_component=mandate_score_component,
    )
    if c3:
        items.append(c3)

    c4 = _check_tech_vs_thesis(
        game_balance=game_balance,
        overheat_penalty=overheat_penalty,
        candidate_type=candidate_type,
        mandate_score_component=mandate_score_component,
    )
    if c4:
        items.append(c4)

    c5 = _check_data_vs_score(
        data_completeness=data_completeness,
        tradeflow_data_completeness=tradeflow_data_completeness,
        ambush_score=ambush_score,
        composite_score=composite_score,
        missing_evidence=missing_evidence,
    )
    if c5:
        items.append(c5)

    c6 = _check_risk_vs_narrative(
        risk_flags=risk_flags,
        risk_penalty=risk_penalty,
        narrative_score=narrative_score,
        bull_case=bull_case,
    )
    if c6:
        items.append(c6)

    level = _classify_contradiction_level(items)
    blocking_gaps = _build_blocking_gaps(
        items,
        company_role=company_role,
        beneficiary_path=beneficiary_path,
        data_completeness=data_completeness,
        tradeflow_data_completeness=tradeflow_data_completeness,
        fund_flow_unit_verified=fund_flow_unit_verified,
    )
    verification_steps = _build_verification_steps(
        items,
        candidate_type=candidate_type,
        company_role=company_role,
        mandate_topic=mandate_topic,
    )

    return ContradictionResult(
        contradiction_level=level,
        contradiction_items=[item.to_dict() for item in items],
        blocking_evidence_gaps=blocking_gaps,
        next_verification_steps=verification_steps,
        _items_raw=items,
    )


def render_contradiction_summary(result: ContradictionResult) -> str:
    if result.contradiction_level == CONTRADICTION_NONE:
        return "无明显矛盾"
    lines = []
    level_labels = {
        CONTRADICTION_LOW: "低",
        CONTRADICTION_MEDIUM: "中",
        CONTRADICTION_HIGH: "高",
    }
    lines.append(f"矛盾等级: {level_labels.get(result.contradiction_level, result.contradiction_level)}")
    if result.contradiction_items:
        lines.append("矛盾项:")
        for item in result.contradiction_items:
            label = CONTRADICTION_LABELS.get(item.get("contradiction_type", ""), item.get("contradiction_type", ""))
            lines.append(f"  - [{label}] {item.get('description', '')}")
    if result.blocking_evidence_gaps:
        lines.append("阻断性证据缺口:")
        for g in result.blocking_evidence_gaps:
            lines.append(f"  ! {g}")
    if result.next_verification_steps:
        lines.append("建议验证步骤:")
        for s in result.next_verification_steps:
            lines.append(f"  > {s}")
    return "\n".join(lines)
