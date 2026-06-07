# [DATA-007] evidence_coverage_audit
"""
raw_evidence 覆盖率审计与候选可信度联动。

把 raw_evidence contract 的覆盖情况转成可读审计结果，并联动 TradeFlow 候选可信度，
避免"候选分数高但关键数据缺证据"的情况继续进入高优先级。

功能：
  1. audit_raw_evidence(): 审计 raw_evidence 字典，输出覆盖率、关键缺口、stale 字段、
     单位未知字段、fallback 字段和质量等级。
  2. audit_candidate_evidence(): 审计单个 TradeFlow 候选的证据完整度。
  3. apply_credibility_linkage(): 根据审计结果联动候选等级，关键缺失时限制升级。
  4. render_audit_summary(): 渲染审计摘要 Markdown。
  5. build_evidence_coverage_section(): 为夜间日报构建证据覆盖率 section。

使用示例：
    from tradingagents.dataflows.evidence_coverage_audit import (
        audit_raw_evidence,
        audit_candidate_evidence,
        apply_credibility_linkage,
    )
    result = audit_raw_evidence(raw_evidence)
    print(result.evidence_quality_level)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from .evidence_contract import (
    EvidenceContract,
    compute_contract_completeness,
)


_CRITICAL_FIELDS_BY_CANDIDATE_TYPE: Dict[str, List[str]] = {
    "POLICY_AMBUSH": ["fundamentals", "news", "announcements"],
    "POLICY_CONFIRM": ["fundamentals", "news", "announcements"],
    "TECH_TRADE": ["stock_data", "fund_flow_individual"],
    "EVENT_WATCH": ["news", "announcements"],
    "PSEUDO_POLICY": ["news"],
    "OVERHEATED_AVOID": ["stock_data", "fund_flow_individual"],
    "UNCLASSIFIED_DATA_GAP": [],
}

_EVIDENCE_FIELD_LABELS: Dict[str, str] = {
    "stock_data": "行情/OHLCV",
    "fund_flow_individual": "个股资金流",
    "fund_flow_board": "板块资金流",
    "lhb": "龙虎榜",
    "news": "新闻",
    "global_news": "全球新闻",
    "announcements": "公告/研报",
    "fundamentals": "财务基本面",
    "balance_sheet": "资产负债表",
    "cashflow": "现金流量表",
    "income_statement": "利润表",
    "insider_transactions": "内部人交易",
    "zt_pool": "涨停池",
    "hot_stocks": "热门股票",
    "indicators": "技术指标",
    "vpa_indicators": "量价分析指标",
    "margin_trading": "融资融券",  # [DATA-010] margin_trading_raw_evidence
    "research_report": "研报/评级",  # [DATA-011] research_report_raw_evidence
    "ratings": "分析师评级",  # [DATA-012] rating_raw_evidence
    "buybacks": "回购",  # [DATA-013] buyback_raw_evidence
    "zt_pool": "涨停池",  # [DATA-015] limit_up_pool_fallback
}

_EVIDENCE_FIELD_FAMILIES: Dict[str, str] = {
    "stock_data": "ohlcv",
    "indicators": "ohlcv",
    "vpa_indicators": "ohlcv",
    "fund_flow_individual": "fund_flow",
    "fund_flow_board": "fund_flow",
    "lhb": "lhb",
    "news": "news",
    "global_news": "news",
    "announcements": "notice",
    "fundamentals": "financials",
    "balance_sheet": "financials",
    "cashflow": "financials",
    "income_statement": "financials",
    "insider_transactions": "insider",
    "zt_pool": "zt_pool",  # [DATA-015] limit_up_pool_fallback
    "hot_stocks": "hot_stocks",
    "margin_trading": "margin_trading",  # [DATA-010] margin_trading_raw_evidence
    "research_report": "report",  # [DATA-011] research_report_raw_evidence
    "ratings": "rating",  # [DATA-012] rating_raw_evidence
    "buybacks": "buyback",  # [DATA-013] buyback_raw_evidence
}

_TIER_RANK = {"A": 1, "B": 2, "C": 3}


def _worse_tier(current: str, minimum: str) -> str:
    """Return the worse (higher rank number) of two tiers."""
    current_rank = _TIER_RANK.get(current, 3)
    minimum_rank = _TIER_RANK.get(minimum, 3)
    return "A" if current_rank <= minimum_rank and minimum_rank == 1 else (
        "B" if max(current_rank, minimum_rank) == 2 else "C"
    )


@dataclass
class EvidenceAuditResult:
    evidence_coverage: float = 0.0
    critical_missing_fields: List[str] = field(default_factory=list)
    stale_fields: List[str] = field(default_factory=list)
    unit_unknown_fields: List[str] = field(default_factory=list)
    fallback_fields: List[str] = field(default_factory=list)
    failed_fields: List[str] = field(default_factory=list)
    not_queried_fields: List[str] = field(default_factory=list)
    normal_no_data_fields: List[str] = field(default_factory=list)
    has_data_fields: List[str] = field(default_factory=list)
    evidence_quality_level: str = "UNKNOWN"
    total_fields: int = 0
    satisfied_fields: int = 0
    audit_refs: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evidence_coverage": round(self.evidence_coverage, 4),
            "critical_missing_fields": self.critical_missing_fields,
            "stale_fields": self.stale_fields,
            "unit_unknown_fields": self.unit_unknown_fields,
            "fallback_fields": self.fallback_fields,
            "failed_fields": self.failed_fields,
            "not_queried_fields": self.not_queried_fields,
            "normal_no_data_fields": self.normal_no_data_fields,
            "has_data_fields": self.has_data_fields,
            "evidence_quality_level": self.evidence_quality_level,
            "total_fields": self.total_fields,
            "satisfied_fields": self.satisfied_fields,
            "audit_refs_count": len(self.audit_refs),
        }


@dataclass
class CandidateCredibilityResult:
    symbol: str = ""
    candidate_type: str = ""
    evidence_audit: Optional[EvidenceAuditResult] = None
    tier_allowed: str = ""
    tier_restricted: bool = False
    need_deep_ta_allowed: bool = True
    credibility_level: str = "UNKNOWN"
    credibility_reasons: List[str] = field(default_factory=list)
    credibility_refs: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "candidate_type": self.candidate_type,
            "evidence_audit": self.evidence_audit.to_dict() if self.evidence_audit else None,
            "tier_allowed": self.tier_allowed,
            "tier_restricted": self.tier_restricted,
            "need_deep_ta_allowed": self.need_deep_ta_allowed,
            "credibility_level": self.credibility_level,
            "credibility_reasons": self.credibility_reasons,
            "credibility_refs_count": len(self.credibility_refs),
        }


def _classify_quality_level(
    coverage: float,
    critical_missing: List[str],
    failed_count: int,
    total: int,
) -> str:
    if total == 0:
        return "UNKNOWN"
    if coverage >= 0.8 and not critical_missing and failed_count == 0:
        return "HIGH"
    if coverage >= 0.5 and len(critical_missing) <= 1:
        return "MEDIUM"
    if coverage >= 0.3:
        return "LOW"
    return "CRITICAL"


def audit_raw_evidence(raw_evidence: Dict[str, Any]) -> EvidenceAuditResult:
    """Audit raw_evidence contract coverage.

    Scans every evidence key, classifies fields by status, detects stale/
    unit-unknown/fallback fields, and produces an overall quality level.

    Args:
        raw_evidence: Dict mapping evidence key -> EvidenceContract dict.

    Returns:
        EvidenceAuditResult with full audit breakdown.
    """
    result = EvidenceAuditResult()

    if not raw_evidence:
        result.evidence_quality_level = "UNKNOWN"
        result.audit_refs.append({
            "field": "raw_evidence_audit",
            "status": "NO_EVIDENCE",
            "reason": "raw_evidence 为空",
        })
        return result

    contract_completeness = compute_contract_completeness(raw_evidence)
    result.total_fields = contract_completeness["total_checks"]
    result.satisfied_fields = contract_completeness["satisfied_checks"]
    result.evidence_coverage = (
        round(result.satisfied_fields / result.total_fields, 4)
        if result.total_fields > 0
        else 0.0
    )

    for ev_key, entry in raw_evidence.items():
        if not isinstance(entry, dict):
            result.not_queried_fields.append(ev_key)
            continue

        contract = EvidenceContract.from_dict(entry)
        label = _EVIDENCE_FIELD_LABELS.get(ev_key, ev_key)

        if contract.has_data:
            result.has_data_fields.append(ev_key)
        elif contract.is_failed:
            result.failed_fields.append(ev_key)
        elif contract.status == "NORMAL_NO_DATA":
            result.normal_no_data_fields.append(ev_key)
        else:
            result.not_queried_fields.append(ev_key)

        if contract.is_realtime_patched:
            result.stale_fields.append(ev_key)

        if not contract.unit_known and contract.has_data:
            result.unit_unknown_fields.append(ev_key)

        if contract.is_fallback:
            result.fallback_fields.append(ev_key)

    missing_from_completeness = contract_completeness.get("missing_details", {})
    for ev_key, missing_reqs in missing_from_completeness.items():
        if ev_key not in result.failed_fields and ev_key not in result.not_queried_fields:
            result.critical_missing_fields.append(ev_key)

    result.evidence_quality_level = _classify_quality_level(
        coverage=result.evidence_coverage,
        critical_missing=result.critical_missing_fields,
        failed_count=len(result.failed_fields),
        total=result.total_fields,
    )

    result.audit_refs.append({
        "field": "raw_evidence_audit",
        "evidence_coverage": result.evidence_coverage,
        "quality_level": result.evidence_quality_level,
        "has_data_count": len(result.has_data_fields),
        "failed_count": len(result.failed_fields),
        "stale_count": len(result.stale_fields),
        "unit_unknown_count": len(result.unit_unknown_fields),
        "fallback_count": len(result.fallback_fields),
    })

    return result


def audit_candidate_evidence(
    candidate_data_completeness: float = 0.0,
    candidate_tradeflow_completeness: float = 0.0,
    candidate_type: str = "",
    missing_data_fields: Optional[List[str]] = None,
    evidence_gate_applied: bool = False,
    fund_flow_unit_verified: bool = False,
    raw_evidence: Optional[Dict[str, Any]] = None,
    has_policy_evidence: bool = False,
    has_tech_signal: bool = False,
    has_realtime_quote: bool = False,
) -> CandidateCredibilityResult:
    """Audit a TradeFlow candidate's evidence completeness and credibility.

    Combines the raw_evidence contract audit with candidate-level metadata
    to determine credibility.

    Args:
        candidate_data_completeness: S-005 data completeness (0-1).
        candidate_tradeflow_completeness: S-008 tradeflow completeness (0-1).
        candidate_type: Candidate type string (POLICY_AMBUSH, TECH_TRADE, etc).
        missing_data_fields: S-008 missing dimension labels.
        evidence_gate_applied: Whether evidence gate was triggered.
        fund_flow_unit_verified: Fund flow unit verification status.
        raw_evidence: Optional raw_evidence dict for deep audit.
        has_policy_evidence: Whether policy evidence exists.
        has_tech_signal: Whether tech signal exists.
        has_realtime_quote: Whether realtime quote data is available.

    Returns:
        CandidateCredibilityResult with credibility assessment.
    """
    result = CandidateCredibilityResult(
        candidate_type=candidate_type,
    )

    if raw_evidence:
        result.evidence_audit = audit_raw_evidence(raw_evidence)
    else:
        coverage = max(candidate_data_completeness, candidate_tradeflow_completeness)
        result.evidence_audit = EvidenceAuditResult(
            evidence_coverage=coverage,
            total_fields=10,
            satisfied_fields=int(coverage * 10),
            evidence_quality_level=_classify_quality_level(
                coverage=coverage,
                critical_missing=[],
                failed_count=0,
                total=10,
            ),
        )

    audit = result.evidence_audit
    reasons: List[str] = []
    refs: List[Dict[str, Any]] = []
    tier_allowed = "A"
    need_deep_ta_allowed = True
    restricted = False

    critical_for_type = _CRITICAL_FIELDS_BY_CANDIDATE_TYPE.get(candidate_type, [])

    if candidate_type in ("POLICY_AMBUSH", "POLICY_CONFIRM"):
        if not has_policy_evidence:
            tier_allowed = "B"
            restricted = True
            reasons.append("政策候选缺少政策/公司证据，不得进入A层")
            refs.append({
                "field": "policy_evidence_gate",
                "value": "missing",
                "candidate_type": candidate_type,
                "gate": "POLICY_AMBUSH_requires_policy_evidence",
            })
        if audit:
            for f in ("fundamentals", "news", "announcements"):
                if f in audit.failed_fields or f in audit.not_queried_fields:
                    if f not in reasons:
                        reasons.append(f"政策候选关键证据缺失: {_EVIDENCE_FIELD_LABELS.get(f, f)}")

    if candidate_type == "TECH_TRADE":
        if not has_realtime_quote and not has_tech_signal:
            tier_allowed = "C"
            restricted = True
            reasons.append("技术候选缺少实时行情/量价证据，降级")
            refs.append({
                "field": "tech_evidence_gate",
                "value": "missing_realtime_and_tech",
                "candidate_type": candidate_type,
            })
        elif not has_realtime_quote:
            tier_allowed = _worse_tier(tier_allowed, "B")
            restricted = True
            reasons.append("技术候选缺少实时行情，限制升级")
            refs.append({
                "field": "tech_evidence_gate",
                "value": "missing_realtime_quote",
                "candidate_type": candidate_type,
            })
        if not fund_flow_unit_verified:
            need_deep_ta_allowed = False
            reasons.append("技术候选资金流单位未校验，禁止深挖")
            refs.append({
                "field": "fund_flow_unit_gate",
                "value": "not_verified",
                "candidate_type": candidate_type,
            })

    if candidate_type == "UNCLASSIFIED_DATA_GAP":
        tier_allowed = "C"
        restricted = True
        need_deep_ta_allowed = False
        reasons.append("证据缺口候选不得升级")
        refs.append({
            "field": "data_gap_gate",
            "value": "unclassified",
            "candidate_type": candidate_type,
        })

    if audit and audit.evidence_coverage < 0.3:
        tier_allowed = _worse_tier(tier_allowed, "B")
        restricted = True
        if not any("证据覆盖率" in r for r in reasons):
            reasons.append(f"证据覆盖率过低({audit.evidence_coverage:.0%})，限制升级")

    if audit and audit.evidence_coverage < 0.5:
        need_deep_ta_allowed = False
        if not any("覆盖率不足" in r for r in reasons):
            reasons.append(f"证据覆盖率不足({audit.evidence_coverage:.0%})，禁止深挖")

    if evidence_gate_applied:
        if not any("证据门控" in r for r in reasons):
            reasons.append("证据门控已触发")

    if missing_data_fields:
        critical_missing_labels = [f for f in missing_data_fields if "关键" in f or "OHLCV" in f or "行情" in f]
        if critical_missing_labels and tier_allowed == "A":
            tier_allowed = _worse_tier(tier_allowed, "B")
            restricted = True

    quality = audit.evidence_quality_level if audit else "UNKNOWN"
    if quality in ("CRITICAL", "LOW"):
        tier_allowed = _worse_tier(tier_allowed, "B")
        restricted = True

    if restricted:
        if quality in ("HIGH", "MEDIUM"):
            credibility = "MEDIUM"
        else:
            credibility = quality
    else:
        credibility = quality

    result.tier_allowed = tier_allowed
    result.tier_restricted = restricted
    result.need_deep_ta_allowed = need_deep_ta_allowed
    result.credibility_level = credibility
    result.credibility_reasons = reasons
    result.credibility_refs = refs

    return result


def apply_credibility_linkage(
    candidate_tier: str = "",
    candidate_need_deep_ta: bool = False,
    credibility: Optional[CandidateCredibilityResult] = None,
) -> Dict[str, Any]:
    """Apply credibility linkage to a candidate's tier and need_deep_ta.

    If the credibility assessment restricts the candidate, this function
    overrides tier and need_deep_ta accordingly.

    Args:
        candidate_tier: Current tier (A/B/C).
        candidate_need_deep_ta: Whether deep TA is requested.
        credibility: CandidateCredibilityResult from audit_candidate_evidence.

    Returns:
        Dict with potentially overridden tier/need_deep_ta and reasons.
    """
    if credibility is None:
        return {
            "tier": candidate_tier,
            "need_deep_ta": candidate_need_deep_ta,
            "tier_overridden": False,
            "need_deep_ta_overridden": False,
            "tier_reason_addition": "",
            "why_not_deep_ta_addition": "",
            "credibility_applied": False,
        }

    tier = candidate_tier
    need_deep_ta = candidate_need_deep_ta
    tier_overridden = False
    deep_ta_overridden = False
    tier_reasons: List[str] = []
    deep_ta_reasons: List[str] = []

    allowed = credibility.tier_allowed
    if tier < allowed:
        tier = allowed
        tier_overridden = True
        tier_reasons.append(
            f"可信度联动: 候选限制为{allowed}层({credibility.credibility_level})"
        )

    if need_deep_ta and not credibility.need_deep_ta_allowed:
        need_deep_ta = False
        deep_ta_overridden = True
        deep_ta_reasons.append(
            f"可信度联动: 禁止深挖({credibility.credibility_level})"
        )

    return {
        "tier": tier,
        "need_deep_ta": need_deep_ta,
        "tier_overridden": tier_overridden,
        "need_deep_ta_overridden": deep_ta_overridden,
        "tier_reason_addition": "; ".join(tier_reasons),
        "why_not_deep_ta_addition": "; ".join(deep_ta_reasons),
        "credibility_applied": tier_overridden or deep_ta_overridden,
    }


def render_audit_summary(audit: EvidenceAuditResult) -> str:
    """Render an EvidenceAuditResult as a Markdown summary string."""
    lines: List[str] = []
    lines.append("### raw_evidence 覆盖率审计")
    lines.append("")
    lines.append(f"- **覆盖率**: {audit.evidence_coverage:.1%} ({audit.satisfied_fields}/{audit.total_fields})")
    lines.append(f"- **质量等级**: {audit.evidence_quality_level}")
    lines.append(f"- **有数据字段**: {len(audit.has_data_fields)}")
    lines.append(f"- **失败字段**: {len(audit.failed_fields)}")
    lines.append(f"- **未查询字段**: {len(audit.not_queried_fields)}")
    lines.append(f"- **Stale 字段**: {len(audit.stale_fields)}")
    lines.append(f"- **单位未知字段**: {len(audit.unit_unknown_fields)}")
    lines.append(f"- **Fallback 字段**: {len(audit.fallback_fields)}")
    lines.append(f"- **关键缺失字段**: {len(audit.critical_missing_fields)}")

    if audit.critical_missing_fields:
        lines.append("")
        lines.append("**关键缺失**:")
        for f in audit.critical_missing_fields:
            label = _EVIDENCE_FIELD_LABELS.get(f, f)
            lines.append(f"- {label} ({f})")

    if audit.failed_fields:
        lines.append("")
        lines.append("**失败字段**:")
        for f in audit.failed_fields:
            label = _EVIDENCE_FIELD_LABELS.get(f, f)
            lines.append(f"- {label} ({f})")

    if audit.stale_fields:
        lines.append("")
        lines.append("**Stale 字段 (实时补丁)**:")
        for f in audit.stale_fields:
            label = _EVIDENCE_FIELD_LABELS.get(f, f)
            lines.append(f"- {label} ({f})")

    if audit.unit_unknown_fields:
        lines.append("")
        lines.append("**单位未知字段**:")
        for f in audit.unit_unknown_fields:
            label = _EVIDENCE_FIELD_LABELS.get(f, f)
            lines.append(f"- {label} ({f})")

    if audit.fallback_fields:
        lines.append("")
        lines.append("**Fallback 字段**:")
        for f in audit.fallback_fields:
            label = _EVIDENCE_FIELD_LABELS.get(f, f)
            lines.append(f"- {label} ({f})")

    lines.append("")
    lines.append("---")
    lines.append("*Generated by evidence_coverage_audit.py — [DATA-007] evidence_coverage_audit*")
    return "\n".join(lines)


def render_credibility_summary(credibility: CandidateCredibilityResult) -> str:
    """Render a CandidateCredibilityResult as a Markdown summary."""
    lines: List[str] = []
    lines.append(f"### 候选可信度评估: {credibility.symbol}")
    lines.append("")
    lines.append(f"- **候选类型**: {credibility.candidate_type}")
    lines.append(f"- **可信度等级**: {credibility.credibility_level}")
    lines.append(f"- **允许最高层**: {credibility.tier_allowed}")
    lines.append(f"- **等级受限**: {'是' if credibility.tier_restricted else '否'}")
    lines.append(f"- **允许深挖**: {'是' if credibility.need_deep_ta_allowed else '否'}")

    if credibility.evidence_audit:
        lines.append(f"- **证据覆盖率**: {credibility.evidence_audit.evidence_coverage:.1%}")

    if credibility.credibility_reasons:
        lines.append("")
        lines.append("**限制原因**:")
        for r in credibility.credibility_reasons:
            lines.append(f"- {r}")

    lines.append("")
    lines.append("---")
    lines.append("*Generated by evidence_coverage_audit.py — [DATA-007] evidence_coverage_audit*")
    return "\n".join(lines)


def build_evidence_coverage_section(
    audits: Optional[List[EvidenceAuditResult]] = None,
    credibilities: Optional[List[CandidateCredibilityResult]] = None,
) -> str:
    """Build an evidence coverage section for the nightly daily digest.

    Args:
        audits: List of EvidenceAuditResult from raw_evidence audits.
        credibilities: List of CandidateCredibilityResult from candidate audits.

    Returns:
        Markdown section string for inclusion in nightly report.
    """
    lines: List[str] = []
    lines.append("## 证据覆盖率审计")
    lines.append("")
    now = datetime.now()
    lines.append(f"- **审计时间**: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    if audits:
        high_count = sum(1 for a in audits if a.evidence_quality_level == "HIGH")
        medium_count = sum(1 for a in audits if a.evidence_quality_level == "MEDIUM")
        low_count = sum(1 for a in audits if a.evidence_quality_level == "LOW")
        critical_count = sum(1 for a in audits if a.evidence_quality_level == "CRITICAL")
        unknown_count = sum(1 for a in audits if a.evidence_quality_level == "UNKNOWN")
        avg_coverage = (
            sum(a.evidence_coverage for a in audits) / len(audits)
            if audits else 0.0
        )

        lines.append("### raw_evidence 审计汇总")
        lines.append("")
        lines.append(f"- **审计样本数**: {len(audits)}")
        lines.append(f"- **平均覆盖率**: {avg_coverage:.1%}")
        lines.append(f"- **质量分布**: HIGH={high_count}, MEDIUM={medium_count}, LOW={low_count}, CRITICAL={critical_count}, UNKNOWN={unknown_count}")
        lines.append("")

    if credibilities:
        restricted_count = sum(1 for c in credibilities if c.tier_restricted)
        deep_ta_blocked = sum(1 for c in credibilities if not c.need_deep_ta_allowed)
        avg_cred = (
            sum(
                {"HIGH": 4, "MEDIUM": 3, "LOW": 2, "CRITICAL": 1, "UNKNOWN": 0}.get(
                    c.credibility_level, 0
                )
                for c in credibilities
            )
            / len(credibilities)
            if credibilities else 0.0
        )

        lines.append("### 候选可信度汇总")
        lines.append("")
        lines.append(f"- **候选样本数**: {len(credibilities)}")
        lines.append(f"- **等级受限**: {restricted_count}/{len(credibilities)}")
        lines.append(f"- **深挖被阻**: {deep_ta_blocked}/{len(credibilities)}")
        lines.append(f"- **可信度均值**: {avg_cred:.2f}/4.0")
        lines.append("")

        type_breakdown: Dict[str, List[str]] = {}
        for c in credibilities:
            type_breakdown.setdefault(c.candidate_type, []).append(c.credibility_level)
        if type_breakdown:
            lines.append("| 候选类型 | 数量 | 可信度分布 |")
            lines.append("|----------|------|------------|")
            for ct, levels in sorted(type_breakdown.items()):
                dist = ", ".join(
                    f"{lvl}={sum(1 for l in levels if l == lvl)}"
                    for lvl in ("HIGH", "MEDIUM", "LOW", "CRITICAL", "UNKNOWN")
                    if sum(1 for l in levels if l == lvl) > 0
                )
                lines.append(f"| {ct or 'UNKNOWN'} | {len(levels)} | {dist} |")
            lines.append("")

    lines.append("---")
    lines.append("*Generated by evidence_coverage_audit.py — [DATA-007] evidence_coverage_audit*")
    lines.append("")
    return "\n".join(lines)
