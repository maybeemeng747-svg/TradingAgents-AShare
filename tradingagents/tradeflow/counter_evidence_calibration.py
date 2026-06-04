# [H-009] mandate_counter_evidence_calibration
"""
昊天候选反证/过热降权校准。

把 H-006 回放中的反证信号沉淀为评分校准规则，重点降低"抄在半山腰"的候选：
过热、政策弱兑现、公司路径伪、资金不认、风险事件未消化。

6 个反证规则：
  1. overheated_price_position — 高位放量但无新证据
  2. policy_signal_decay — 政策信号衰减/消退
  3. weak_company_benefit_path — 公司受益路径弱/伪
  4. funding_not_confirmed — 资金不认可
  5. negative_event_unresolved — 负面风险事件未消化
  6. crowded_consensus_risk — 拥挤共识风险

POLICY_AMBUSH 降权约束：
  - 高位连续放量但政策/公司证据不新增 → 降级
  - 仅媒体热度、无原始政策/公告 → 不得高分
  - CONCEPT_ONLY/UNKNOWN 公司路径 → 不得进入 A 层

Constraints:
  - No LLM calls.
  - No buy/sell suggestions.
  - Does not equate short-term drawdown with thesis failure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


COUNTER_RULE_OVERHEATED_PRICE = "overheated_price_position"
COUNTER_RULE_POLICY_DECAY = "policy_signal_decay"
COUNTER_RULE_WEAK_PATH = "weak_company_benefit_path"
COUNTER_RULE_FUNDING = "funding_not_confirmed"
COUNTER_RULE_NEGATIVE = "negative_event_unresolved"
COUNTER_RULE_CROWDED = "crowded_consensus_risk"

ALL_COUNTER_RULES = [
    COUNTER_RULE_OVERHEATED_PRICE,
    COUNTER_RULE_POLICY_DECAY,
    COUNTER_RULE_WEAK_PATH,
    COUNTER_RULE_FUNDING,
    COUNTER_RULE_NEGATIVE,
    COUNTER_RULE_CROWDED,
]

COUNTER_RULE_LABELS = {
    COUNTER_RULE_OVERHEATED_PRICE: "高位过热",
    COUNTER_RULE_POLICY_DECAY: "政策信号衰减",
    COUNTER_RULE_WEAK_PATH: "受益路径弱",
    COUNTER_RULE_FUNDING: "资金不认可",
    COUNTER_RULE_NEGATIVE: "负面风险未消化",
    COUNTER_RULE_CROWDED: "拥挤共识",
}


@dataclass
class CounterEvidenceFlag:
    rule: str = ""
    triggered: bool = False
    severity: float = 0.0
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "rule": self.rule,
            "triggered": self.triggered,
            "severity": round(self.severity, 2),
            "description": self.description,
        }


@dataclass
class CounterEvidenceCalibrationResult:
    counter_evidence: list[dict] = field(default_factory=list)
    overheat_flags: list[str] = field(default_factory=list)
    downgrade_reasons: list[str] = field(default_factory=list)
    what_would_change_mind: list[str] = field(default_factory=list)
    counter_flags: list[CounterEvidenceFlag] = field(default_factory=list)
    max_downgrade_tier: str = ""
    ambush_score_cap: float = 100.0

    def to_dict(self) -> dict:
        return {
            "counter_evidence": self.counter_evidence,
            "overheat_flags": self.overheat_flags,
            "downgrade_reasons": self.downgrade_reasons,
            "what_would_change_mind": self.what_would_change_mind,
            "counter_flags": [f.to_dict() for f in self.counter_flags],
            "max_downgrade_tier": self.max_downgrade_tier,
            "ambush_score_cap": self.ambush_score_cap,
        }


def _rule_overheated_price_position(
    overheat_penalty: float = 0.0,
    narrative_score: float = 0.0,
    game_balance: str = "",
    has_new_policy_evidence: bool = False,
    has_new_company_evidence: bool = False,
    risk_flags: Optional[list[str]] = None,
) -> CounterEvidenceFlag:
    triggered = False
    severity = 0.0
    desc = ""

    high_overheat = overheat_penalty >= 25.0
    high_narrative = narrative_score > 50.0
    bad_game = game_balance in ("crowded", "fragile")

    if high_overheat and not has_new_policy_evidence:
        triggered = True
        severity = min(overheat_penalty / 100.0, 1.0)
        desc = f"过热惩罚={overheat_penalty:.0f}但无新政策/公司证据"
    elif high_narrative and not has_new_policy_evidence and bad_game:
        triggered = True
        severity = 0.6
        desc = f"叙事热度={narrative_score:.0f}但博弈{game_balance}且无新证据"
    elif bad_game and overheat_penalty >= 15.0:
        triggered = True
        severity = 0.5
        desc = f"博弈{game_balance}且过热惩罚={overheat_penalty:.0f}"

    return CounterEvidenceFlag(
        rule=COUNTER_RULE_OVERHEATED_PRICE,
        triggered=triggered,
        severity=round(severity, 2),
        description=desc,
    )


def _rule_policy_signal_decay(
    policy_tags: Optional[list[str]] = None,
    version_score: float = 0.0,
    has_policy_document: bool = False,
    unique_dates: int = 0,
    is_noise: bool = False,
) -> CounterEvidenceFlag:
    triggered = False
    severity = 0.0
    desc = ""

    no_policy_doc = not has_policy_document
    single_date = unique_dates <= 1
    low_version = version_score < 20.0

    if is_noise:
        triggered = True
        severity = 0.8
        desc = "政策信号为噪音"
    elif no_policy_doc and single_date and low_version:
        triggered = True
        severity = 0.7
        desc = "无政策原文且仅单日信号且版本分<20"
    elif no_policy_doc and single_date:
        triggered = True
        severity = 0.5
        desc = "无政策原文且仅单日信号"
    elif no_policy_doc and low_version:
        triggered = True
        severity = 0.4
        desc = "无政策原文且版本分低"

    return CounterEvidenceFlag(
        rule=COUNTER_RULE_POLICY_DECAY,
        triggered=triggered,
        severity=round(severity, 2),
        description=desc,
    )


def _rule_weak_company_benefit_path(
    company_role: str = "",
    beneficiary_path: Optional[list[str]] = None,
    has_company_evidence: bool = False,
) -> CounterEvidenceFlag:
    triggered = False
    severity = 0.0
    desc = ""

    no_role = company_role in ("", "UNKNOWN")
    concept_only = company_role == "CONCEPT_ONLY"
    no_path = not beneficiary_path or len(beneficiary_path) == 0
    no_evidence = not has_company_evidence

    if concept_only and no_evidence:
        triggered = True
        severity = 0.9
        desc = "CONCEPT_ONLY角色且无公司层证据"
    elif no_role:
        triggered = True
        severity = 0.8
        desc = "无公司角色定位"
    elif concept_only:
        triggered = True
        severity = 0.7
        desc = "CONCEPT_ONLY角色，受益路径弱"
    elif no_path and no_evidence:
        triggered = True
        severity = 0.6
        desc = "无受益路径且无公司层证据"
    elif no_path:
        triggered = True
        severity = 0.4
        desc = "无受益路径"

    return CounterEvidenceFlag(
        rule=COUNTER_RULE_WEAK_PATH,
        triggered=triggered,
        severity=round(severity, 2),
        description=desc,
    )


def _rule_funding_not_confirmed(
    fund_flow_anomaly_score: float = 0.0,
    fund_flow_anomaly_tags: Optional[list[str]] = None,
    fund_flow_unit_verified: bool = False,
) -> CounterEvidenceFlag:
    triggered = False
    severity = 0.0
    desc = ""

    ff_tags = set(fund_flow_anomaly_tags or [])
    net_outflow = "NET_OUTFLOW_DOMINANT" in ff_tags

    if net_outflow and not fund_flow_unit_verified:
        triggered = True
        severity = 0.7
        desc = "主力净流出且资金流单位未校验"
    elif net_outflow:
        triggered = True
        severity = 0.6
        desc = "主力持续净流出"
    elif fund_flow_anomaly_score <= 0 and not fund_flow_unit_verified:
        triggered = True
        severity = 0.3
        desc = "无资金流信号且单位未校验"

    return CounterEvidenceFlag(
        rule=COUNTER_RULE_FUNDING,
        triggered=triggered,
        severity=round(severity, 2),
        description=desc,
    )


def _rule_negative_event_unresolved(
    risk_flags: Optional[list[str]] = None,
    risk_penalty: float = 0.0,
) -> CounterEvidenceFlag:
    triggered = False
    severity = 0.0
    desc = ""

    r_flags = set(risk_flags or [])
    high_risk = {"INQUIRY_RISK", "FINANCIAL_QUALITY_RISK", "REGULATORY_RISK"}
    has_high = bool(r_flags & high_risk)

    if has_high:
        triggered = True
        severity = 0.9
        desc = f"存在高风险标签: {', '.join(sorted(r_flags & high_risk))}"
    elif risk_penalty < -10:
        triggered = True
        severity = min(abs(risk_penalty) / 20.0, 0.8)
        desc = f"风险惩罚={risk_penalty:.0f}"

    return CounterEvidenceFlag(
        rule=COUNTER_RULE_NEGATIVE,
        triggered=triggered,
        severity=round(severity, 2),
        description=desc,
    )


def _rule_crowded_consensus(
    game_balance: str = "",
    resonance_count: int = 0,
    narrative_score: float = 0.0,
) -> CounterEvidenceFlag:
    triggered = False
    severity = 0.0
    desc = ""

    if game_balance in ("crowded", "fragile") and resonance_count >= 3:
        triggered = True
        severity = 0.8
        desc = f"博弈{game_balance}且共振因子={resonance_count}"
    elif game_balance in ("crowded", "fragile"):
        triggered = True
        severity = 0.6
        desc = f"博弈{game_balance}"
    elif narrative_score > 60 and resonance_count >= 4:
        triggered = True
        severity = 0.5
        desc = f"叙事热度={narrative_score:.0f}且共振因子={resonance_count}，拥挤共识"

    return CounterEvidenceFlag(
        rule=COUNTER_RULE_CROWDED,
        triggered=triggered,
        severity=round(severity, 2),
        description=desc,
    )


def _worse_tier(current: str, new_tier: str) -> str:
    order = {"A": 0, "B": 1, "C": 2}
    if not current:
        return new_tier
    if not new_tier:
        return current
    return max(current, new_tier, key=lambda t: order.get(t, 1))


def evaluate_counter_evidence(
    candidate_type: str = "",
    overheat_penalty: float = 0.0,
    narrative_score: float = 0.0,
    game_balance: str = "",
    risk_flags: Optional[list[str]] = None,
    risk_penalty: float = 0.0,
    policy_tags: Optional[list[str]] = None,
    version_score: float = 0.0,
    has_policy_document: bool = False,
    unique_dates: int = 0,
    is_noise: bool = False,
    company_role: str = "",
    beneficiary_path: Optional[list[str]] = None,
    has_company_evidence: bool = False,
    has_new_policy_evidence: bool = False,
    has_new_company_evidence: bool = False,
    fund_flow_anomaly_score: float = 0.0,
    fund_flow_anomaly_tags: Optional[list[str]] = None,
    fund_flow_unit_verified: bool = False,
    resonance_count: int = 0,
) -> CounterEvidenceCalibrationResult:
    flags = [
        _rule_overheated_price_position(
            overheat_penalty=overheat_penalty,
            narrative_score=narrative_score,
            game_balance=game_balance,
            has_new_policy_evidence=has_new_policy_evidence,
            has_new_company_evidence=has_new_company_evidence,
            risk_flags=risk_flags,
        ),
        _rule_policy_signal_decay(
            policy_tags=policy_tags,
            version_score=version_score,
            has_policy_document=has_policy_document,
            unique_dates=unique_dates,
            is_noise=is_noise,
        ),
        _rule_weak_company_benefit_path(
            company_role=company_role,
            beneficiary_path=beneficiary_path,
            has_company_evidence=has_company_evidence,
        ),
        _rule_funding_not_confirmed(
            fund_flow_anomaly_score=fund_flow_anomaly_score,
            fund_flow_anomaly_tags=fund_flow_anomaly_tags,
            fund_flow_unit_verified=fund_flow_unit_verified,
        ),
        _rule_negative_event_unresolved(
            risk_flags=risk_flags,
            risk_penalty=risk_penalty,
        ),
        _rule_crowded_consensus(
            game_balance=game_balance,
            resonance_count=resonance_count,
            narrative_score=narrative_score,
        ),
    ]

    counter_evidence = [f.to_dict() for f in flags if f.triggered]
    overheat_flags: list[str] = []
    downgrade_reasons: list[str] = []
    what_would_change_mind: list[str] = []
    max_downgrade_tier = ""
    ambush_score_cap = 100.0

    for f in flags:
        if not f.triggered:
            continue

        label = COUNTER_RULE_LABELS.get(f.rule, f.rule)

        if f.rule == COUNTER_RULE_OVERHEATED_PRICE:
            overheat_flags.append(label)
            downgrade_reasons.append(f"过热: {f.description}")
            if candidate_type == "POLICY_AMBUSH":
                ambush_score_cap = min(ambush_score_cap, 50.0)
                max_downgrade_tier = _worse_tier(max_downgrade_tier, "B")

        elif f.rule == COUNTER_RULE_POLICY_DECAY:
            downgrade_reasons.append(f"政策衰减: {f.description}")
            if candidate_type == "POLICY_AMBUSH":
                if f.severity >= 0.7:
                    ambush_score_cap = min(ambush_score_cap, 25.0)
                    max_downgrade_tier = _worse_tier(max_downgrade_tier, "C")
                else:
                    ambush_score_cap = min(ambush_score_cap, 40.0)
                    max_downgrade_tier = _worse_tier(max_downgrade_tier, "B")
            what_would_change_mind.append("新增政策原文或二次确认")

        elif f.rule == COUNTER_RULE_WEAK_PATH:
            downgrade_reasons.append(f"路径弱: {f.description}")
            if candidate_type == "POLICY_AMBUSH":
                if company_role in ("CONCEPT_ONLY", "UNKNOWN", ""):
                    max_downgrade_tier = _worse_tier(max_downgrade_tier, "B")
                    ambush_score_cap = min(ambush_score_cap, 35.0)
            what_would_change_mind.append("公司公告实质订单/营收/合同")

        elif f.rule == COUNTER_RULE_FUNDING:
            downgrade_reasons.append(f"资金不认: {f.description}")
            what_would_change_mind.append("主力资金连续3日净流入")

        elif f.rule == COUNTER_RULE_NEGATIVE:
            downgrade_reasons.append(f"风险未消化: {f.description}")
            if candidate_type == "POLICY_AMBUSH":
                ambush_score_cap = min(ambush_score_cap, 30.0)
                max_downgrade_tier = _worse_tier(max_downgrade_tier, "C")
            what_would_change_mind.append("风险事件澄清/公告回应")

        elif f.rule == COUNTER_RULE_CROWDED:
            overheat_flags.append(label)
            downgrade_reasons.append(f"拥挤: {f.description}")
            if candidate_type == "POLICY_AMBUSH":
                ambush_score_cap = min(ambush_score_cap, 40.0)
                max_downgrade_tier = _worse_tier(max_downgrade_tier, "B")
            what_would_change_mind.append("博弈结构改善/共振减弱")

    if candidate_type == "POLICY_AMBUSH":
        if not has_policy_document and policy_tags and len(policy_tags) > 0:
            ambush_score_cap = min(ambush_score_cap, 45.0)
            downgrade_reasons.append("仅媒体热度，无原始政策/公告，不得高分")
            what_would_change_mind.append("政策原文/部委会议/国务院文件确认")

        if company_role in ("CONCEPT_ONLY", "UNKNOWN", ""):
            max_downgrade_tier = _worse_tier(max_downgrade_tier, "B")
            ambush_score_cap = min(ambush_score_cap, 35.0)
            downgrade_reasons.append(f"公司角色={company_role}，不得进入A层")
            what_would_change_mind.append("明确公司受益路径和实质订单")

    downgrade_reasons = list(dict.fromkeys(downgrade_reasons))
    what_would_change_mind = list(dict.fromkeys(what_would_change_mind))

    return CounterEvidenceCalibrationResult(
        counter_evidence=counter_evidence,
        overheat_flags=overheat_flags,
        downgrade_reasons=downgrade_reasons,
        what_would_change_mind=what_would_change_mind,
        counter_flags=flags,
        max_downgrade_tier=max_downgrade_tier,
        ambush_score_cap=round(ambush_score_cap, 2),
    )


def render_counter_evidence_summary(result: CounterEvidenceCalibrationResult) -> str:
    if not result.downgrade_reasons and not result.overheat_flags:
        return "无明显反证信号"
    lines = []
    if result.overheat_flags:
        lines.append(f"过热标记: {', '.join(result.overheat_flags)}")
    if result.downgrade_reasons:
        lines.append("降权原因:")
        for r in result.downgrade_reasons:
            lines.append(f"  - {r}")
    if result.what_would_change_mind:
        lines.append("什么能改变判断:")
        for w in result.what_would_change_mind:
            lines.append(f"  + {w}")
    if result.max_downgrade_tier:
        lines.append(f"降级至: {result.max_downgrade_tier}层")
    if result.ambush_score_cap < 100.0:
        lines.append(f"埋伏分上限: {result.ambush_score_cap:.0f}")
    return "\n".join(lines)
