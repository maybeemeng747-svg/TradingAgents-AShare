# [S-006] candidate_false_positive_audit
"""Candidate False Positive Audit — classifies candidates and filtered symbols by
audit category, produces a replayable audit report, and generates evidence gap summaries.

Responsibilities:
1. Classify each candidate by audit category: HIT_STRATEGY / FILTERED / RISK_DEMOTED / EVIDENCE_GAP.
2. For filtered symbols, record specific filter reason (liquidity / data_missing / no_strategy).
3. For candidates, record false_positive_type and false_negative_type for tuning.
4. Produce replayable audit fixtures that cover: VCP hit, liquidity filter, no strategy hit,
   event catalyst, fund flow anomaly, risk demotion, evidence gap.
5. Output evidence_gap summary for Daily Plan / Discovery display.

Design constraints:
- No external LLM calls.
- No full-market scan.
- No production DB writes.
- No strong buy/sell words.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional

from .schemas import Candidate, FORBIDDEN_WORDS, ALL_STRATEGIES


# ── Audit categories ──

AUDIT_CATEGORY_HIT_STRATEGY = "HIT_STRATEGY"
AUDIT_CATEGORY_FILTERED = "FILTERED"
AUDIT_CATEGORY_RISK_DEMOTED = "RISK_DEMOTED"
AUDIT_CATEGORY_EVIDENCE_GAP = "EVIDENCE_GAP"

ALL_AUDIT_CATEGORIES = {
    AUDIT_CATEGORY_HIT_STRATEGY,
    AUDIT_CATEGORY_FILTERED,
    AUDIT_CATEGORY_RISK_DEMOTED,
    AUDIT_CATEGORY_EVIDENCE_GAP,
}

# ── False positive / false negative types ──

FP_TYPE_FALSE_POSITIVE = "false_positive"
FP_TYPE_FALSE_NEGATIVE = "false_negative"
FP_TYPE_TRUE_POSITIVE = "true_positive"
FP_TYPE_TRUE_NEGATIVE = "true_negative"

# ── Filter reason subcategories ──

FILTER_SUBCATEGORY_LIQUIDITY = "流动性差"
FILTER_SUBCATEGORY_DATA_MISSING = "数据缺失"
FILTER_SUBCATEGORY_NO_STRATEGY = "无策略命中"
FILTER_SUBCATEGORY_DATA_INSUFFICIENT = "数据不足"
FILTER_SUBCATEGORY_OTHER = "其他"

ALL_FILTER_SUBCATEGORIES = {
    FILTER_SUBCATEGORY_LIQUIDITY,
    FILTER_SUBCATEGORY_DATA_MISSING,
    FILTER_SUBCATEGORY_NO_STRATEGY,
    FILTER_SUBCATEGORY_DATA_INSUFFICIENT,
    FILTER_SUBCATEGORY_OTHER,
}

# ── Risk demotion reasons ──

RISK_DEMOTION_HIGH_SEVERITY = "高风险标签"
RISK_DEMOTION_TOO_MANY_RISKS = "风险标签过多"
RISK_DEMOTION_HEAVY_PENALTY = "风险罚分过重"
RISK_DEMOTION_GAME_BALANCE = "博弈平衡不佳"
RISK_DEMOTION_LOW_COMPLETENESS = "数据完整度不足"


def _sanitize(text: str) -> str:
    for w in FORBIDDEN_WORDS:
        if w in text:
            text = text.replace(w, "***")
    return text


@dataclass
class AuditEntry:
    symbol: str
    name: str = ""
    audit_category: str = ""
    hit_strategies: list[str] = field(default_factory=list)
    filter_reason: str = ""
    filter_subcategory: str = ""
    risk_flags: list[str] = field(default_factory=list)
    risk_penalty: float = 0.0
    risk_demotion_reasons: list[str] = field(default_factory=list)
    evidence_gaps: list[str] = field(default_factory=list)
    composite_score: float = 0.0
    priority_rank: str = ""
    need_deep_ta: bool = False
    fp_type: str = ""
    fn_type: str = ""
    data_completeness: float = 0.0
    game_balance: str = ""
    refs: list[dict] = field(default_factory=list)


@dataclass
class AuditSummary:
    total_candidates: int = 0
    total_filtered: int = 0
    by_category: dict[str, int] = field(default_factory=dict)
    by_filter_subcategory: dict[str, int] = field(default_factory=dict)
    by_strategy: dict[str, int] = field(default_factory=dict)
    by_priority_rank: dict[str, int] = field(default_factory=dict)
    by_fp_type: dict[str, int] = field(default_factory=dict)
    common_evidence_gaps: list[dict] = field(default_factory=list)
    common_risk_demotions: list[dict] = field(default_factory=list)


@dataclass
class AuditReport:
    trade_date: str = ""
    generated_at: str = ""
    entries: list[AuditEntry] = field(default_factory=list)
    summary: Optional[AuditSummary] = None

    def __post_init__(self):
        if not self.generated_at:
            self.generated_at = datetime.now().isoformat()


def classify_filter_reason(reason: str) -> str:
    if "流动性" in reason:
        return FILTER_SUBCATEGORY_LIQUIDITY
    elif "数据缺失" in reason or "数据不足" in reason:
        return FILTER_SUBCATEGORY_DATA_MISSING if "数据缺失" in reason else FILTER_SUBCATEGORY_DATA_INSUFFICIENT
    elif "无策略" in reason:
        return FILTER_SUBCATEGORY_NO_STRATEGY
    else:
        return FILTER_SUBCATEGORY_OTHER


def classify_risk_demotion(
    risk_flags: Optional[list[str]] = None,
    risk_penalty: float = 0.0,
    game_balance: str = "",
    data_completeness: float = 0.0,
) -> tuple[list[str], list[dict]]:
    _SEVERITY_HIGH = {"INQUIRY_RISK", "FINANCIAL_QUALITY_RISK"}
    reasons: list[str] = []
    refs: list[dict] = []

    r_flags = set(risk_flags or [])

    if r_flags & _SEVERITY_HIGH:
        high_flags = sorted(r_flags & _SEVERITY_HIGH)
        reasons.append(f"{RISK_DEMOTION_HIGH_SEVERITY}({', '.join(high_flags)})")
        refs.append({"field": "risk_demotion", "value": "high_severity", "flags": high_flags})

    if len(r_flags) >= 3:
        reasons.append(f"{RISK_DEMOTION_TOO_MANY_RISKS}({len(r_flags)}个)")
        refs.append({"field": "risk_demotion", "value": "too_many_risks", "count": len(r_flags)})

    if risk_penalty <= -15:
        reasons.append(f"{RISK_DEMOTION_HEAVY_PENALTY}({risk_penalty})")
        refs.append({"field": "risk_demotion", "value": "heavy_penalty", "penalty": risk_penalty})

    if game_balance in {"fragile", "crowded"}:
        reasons.append(f"{RISK_DEMOTION_GAME_BALANCE}({game_balance})")
        refs.append({"field": "risk_demotion", "value": "bad_game_balance", "balance": game_balance})

    if data_completeness < 0.5:
        reasons.append(f"{RISK_DEMOTION_LOW_COMPLETENESS}({data_completeness:.0%})")
        refs.append({"field": "risk_demotion", "value": "low_completeness", "completeness": data_completeness})

    return reasons, refs


def _determine_fp_fn_type(
    audit_category: str,
    hit_strategies: list[str],
    need_deep_ta: bool,
    risk_demotion_reasons: list[str],
    evidence_gaps: list[str],
    priority_rank: str,
) -> tuple[str, str]:
    fp_type = ""
    fn_type = ""

    if audit_category == AUDIT_CATEGORY_HIT_STRATEGY:
        if need_deep_ta and priority_rank in {"A", "B"}:
            fp_type = FP_TYPE_TRUE_POSITIVE
        elif risk_demotion_reasons or evidence_gaps:
            fp_type = FP_TYPE_FALSE_POSITIVE
        else:
            fp_type = FP_TYPE_TRUE_POSITIVE
    elif audit_category == AUDIT_CATEGORY_FILTERED:
        fn_type = FP_TYPE_TRUE_NEGATIVE
    elif audit_category == AUDIT_CATEGORY_RISK_DEMOTED:
        fp_type = FP_TYPE_FALSE_POSITIVE
        fn_type = FP_TYPE_FALSE_NEGATIVE
    elif audit_category == AUDIT_CATEGORY_EVIDENCE_GAP:
        fp_type = FP_TYPE_FALSE_POSITIVE
        fn_type = FP_TYPE_FALSE_NEGATIVE

    return fp_type, fn_type


def audit_candidate(candidate: Candidate) -> AuditEntry:
    risk_demotion_reasons, risk_refs = classify_risk_demotion(
        risk_flags=candidate.risk_flags,
        risk_penalty=candidate.risk_penalty,
        game_balance=candidate.game_balance,
        data_completeness=candidate.data_completeness,
    )

    evidence_gaps = list(candidate.missing_evidence) if candidate.missing_evidence else []

    has_hit = bool(candidate.strategy_tags)
    non_completeness_demotions = [r for r in risk_demotion_reasons
                                   if RISK_DEMOTION_LOW_COMPLETENESS not in r]
    has_structural_risk = bool(non_completeness_demotions)
    has_evidence_gap = len(evidence_gaps) >= 2 or (candidate.data_completeness < 0.5 and not candidate.need_deep_ta)

    if has_structural_risk:
        audit_category = AUDIT_CATEGORY_RISK_DEMOTED
    elif has_evidence_gap and not candidate.need_deep_ta:
        audit_category = AUDIT_CATEGORY_EVIDENCE_GAP
    elif has_hit:
        audit_category = AUDIT_CATEGORY_HIT_STRATEGY
    else:
        audit_category = AUDIT_CATEGORY_FILTERED

    fp_type, fn_type = _determine_fp_fn_type(
        audit_category=audit_category,
        hit_strategies=candidate.strategy_tags,
        need_deep_ta=candidate.need_deep_ta,
        risk_demotion_reasons=risk_demotion_reasons,
        evidence_gaps=evidence_gaps,
        priority_rank=candidate.priority_rank,
    )

    refs = list(risk_refs)
    if evidence_gaps:
        refs.append({"field": "evidence_gaps", "gaps": evidence_gaps})
    if candidate.strategy_tags:
        refs.append({"field": "hit_strategies", "strategies": candidate.strategy_tags})

    return AuditEntry(
        symbol=candidate.symbol,
        name=candidate.name,
        audit_category=audit_category,
        hit_strategies=list(candidate.strategy_tags),
        risk_flags=list(candidate.risk_flags),
        risk_penalty=candidate.risk_penalty,
        risk_demotion_reasons=risk_demotion_reasons,
        evidence_gaps=evidence_gaps,
        composite_score=candidate.composite_score,
        priority_rank=candidate.priority_rank,
        need_deep_ta=candidate.need_deep_ta,
        fp_type=fp_type,
        fn_type=fn_type,
        data_completeness=candidate.data_completeness,
        game_balance=candidate.game_balance,
        refs=refs,
    )


def audit_filtered_symbol(
    symbol: str,
    name: str = "",
    source: str = "",
    reason: str = "",
) -> AuditEntry:
    filter_subcategory = classify_filter_reason(reason)

    refs = [
        {"field": "filter_reason", "value": reason},
        {"field": "filter_subcategory", "value": filter_subcategory},
    ]
    if source:
        refs.append({"field": "source", "value": source})

    return AuditEntry(
        symbol=symbol,
        name=name,
        audit_category=AUDIT_CATEGORY_FILTERED,
        filter_reason=reason,
        filter_subcategory=filter_subcategory,
        fp_type="",
        fn_type=FP_TYPE_TRUE_NEGATIVE,
        refs=refs,
    )


def build_audit_report(
    candidates: Optional[list[Candidate]] = None,
    filtered_symbols: Optional[list[dict]] = None,
    trade_date: str = "",
) -> AuditReport:
    entries: list[AuditEntry] = []

    if candidates:
        for c in candidates:
            entries.append(audit_candidate(c))

    if filtered_symbols:
        for f in filtered_symbols:
            entries.append(audit_filtered_symbol(
                symbol=f.get("symbol", ""),
                name=f.get("name", ""),
                source=f.get("source", ""),
                reason=f.get("reason", ""),
            ))

    summary = _build_audit_summary(entries)

    return AuditReport(
        trade_date=trade_date or datetime.now().strftime("%Y-%m-%d"),
        entries=entries,
        summary=summary,
    )


def _build_audit_summary(entries: list[AuditEntry]) -> AuditSummary:
    n_candidates = sum(1 for e in entries if e.audit_category != AUDIT_CATEGORY_FILTERED)
    n_filtered = sum(1 for e in entries if e.audit_category == AUDIT_CATEGORY_FILTERED)

    by_category: dict[str, int] = {}
    by_filter_subcategory: dict[str, int] = {}
    by_strategy: dict[str, int] = {}
    by_priority_rank: dict[str, int] = {}
    by_fp_type: dict[str, int] = {}

    gap_counts: dict[str, int] = {}
    risk_demotion_counts: dict[str, int] = {}

    for e in entries:
        by_category[e.audit_category] = by_category.get(e.audit_category, 0) + 1

        if e.filter_subcategory:
            by_filter_subcategory[e.filter_subcategory] = by_filter_subcategory.get(e.filter_subcategory, 0) + 1

        for s in e.hit_strategies:
            by_strategy[s] = by_strategy.get(s, 0) + 1

        if e.priority_rank:
            by_priority_rank[e.priority_rank] = by_priority_rank.get(e.priority_rank, 0) + 1

        if e.fp_type:
            by_fp_type[e.fp_type] = by_fp_type.get(e.fp_type, 0) + 1

        for g in e.evidence_gaps:
            gap_counts[g] = gap_counts.get(g, 0) + 1

        for r in e.risk_demotion_reasons:
            risk_demotion_counts[r] = risk_demotion_counts.get(r, 0) + 1

    common_evidence_gaps = sorted(
        [{"gap": g, "count": c} for g, c in gap_counts.items()],
        key=lambda x: x["count"],
        reverse=True,
    )[:10]

    common_risk_demotions = sorted(
        [{"reason": r, "count": c} for r, c in risk_demotion_counts.items()],
        key=lambda x: x["count"],
        reverse=True,
    )[:10]

    return AuditSummary(
        total_candidates=n_candidates,
        total_filtered=n_filtered,
        by_category=by_category,
        by_filter_subcategory=by_filter_subcategory,
        by_strategy=by_strategy,
        by_priority_rank=by_priority_rank,
        by_fp_type=by_fp_type,
        common_evidence_gaps=common_evidence_gaps,
        common_risk_demotions=common_risk_demotions,
    )


def render_audit_report(report: AuditReport) -> str:
    lines = [
        f"📋 候选误报审计报告 {report.trade_date}",
        "=" * 50,
        "",
    ]

    s = report.summary
    if s is None:
        lines.append("（无审计数据）")
        return "\n".join(lines)

    lines.append(f"📊 摘要:")
    lines.append(f"  候选: {s.total_candidates}只 | 被过滤: {s.total_filtered}只")

    if s.by_category:
        cat_parts = [f"{cat}: {cnt}只" for cat, cnt in sorted(s.by_category.items())]
        lines.append(f"  分类: {' | '.join(cat_parts)}")

    if s.by_priority_rank:
        rank_parts = [f"{rank}层: {cnt}只" for rank, cnt in sorted(s.by_priority_rank.items())]
        lines.append(f"  优先级: {' | '.join(rank_parts)}")

    if s.by_fp_type:
        fp_parts = [f"{t}: {cnt}只" for t, cnt in sorted(s.by_fp_type.items())]
        lines.append(f"  正误判: {' | '.join(fp_parts)}")

    if s.common_evidence_gaps:
        lines.append("")
        lines.append("🔍 常见证据缺口:")
        for item in s.common_evidence_gaps[:5]:
            lines.append(f"  - {item['gap']} ({item['count']}只)")

    if s.common_risk_demotions:
        lines.append("")
        lines.append("⚠️ 常见风险降权原因:")
        for item in s.common_risk_demotions[:5]:
            lines.append(f"  - {item['reason']} ({item['count']}只)")

    if s.by_filter_subcategory:
        lines.append("")
        lines.append("🗑️ 过滤原因分布:")
        for sub, cnt in sorted(s.by_filter_subcategory.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"  - {sub}: {cnt}只")

    lines.append("")
    lines.append("--- 详细条目 ---")
    lines.append("")

    for e in report.entries:
        lines.append(f"  {e.symbol} {e.name}")
        lines.append(f"    分类: {e.audit_category}")
        if e.hit_strategies:
            lines.append(f"    命中策略: {', '.join(e.hit_strategies)}")
        if e.filter_reason:
            lines.append(f"    过滤原因: {e.filter_reason}")
            lines.append(f"    子分类: {e.filter_subcategory}")
        if e.risk_flags:
            lines.append(f"    风险标签: {', '.join(e.risk_flags)}")
        if e.risk_demotion_reasons:
            lines.append(f"    风险降权: {'; '.join(e.risk_demotion_reasons)}")
        if e.evidence_gaps:
            lines.append(f"    证据缺口: {', '.join(e.evidence_gaps)}")
        if e.composite_score > 0:
            lines.append(f"    综合分: {e.composite_score:.1f} | 优先级: {e.priority_rank} | 完整度: {e.data_completeness:.0%}")
        if e.fp_type:
            lines.append(f"    正误判类型: {e.fp_type}")
        lines.append("")

    return "\n".join(lines)


# ── Fixture generation for replay ──

def generate_fixture_samples() -> dict:
    """Generate replayable fixture samples covering key audit scenarios.

    Returns a dict mapping scenario name → fixture data with
    predefined candidates and filtered symbols.
    """
    fixtures = {}

    # 1. VCP hit — clean candidate, should be true_positive
    fixtures["vcp_hit"] = {
        "description": "VCP形态命中，无风险，数据完整 → true_positive",
        "candidate": _make_candidate(
            symbol="600519.SH",
            name="贵州茅台",
            strategy_tags=["VCP"],
            score=45.0,
            composite_score=50.0,
            priority_rank="B",
            need_deep_ta=True,
            data_completeness=0.75,
            positive_category_count=2,
        ),
        "expected_audit_category": AUDIT_CATEGORY_HIT_STRATEGY,
        "expected_fp_type": FP_TYPE_TRUE_POSITIVE,
    }

    # 2. Liquidity filter — should be true_negative
    fixtures["liquidity_filter"] = {
        "description": "流动性不足被过滤 → true_negative",
        "filtered_symbol": {
            "symbol": "830799.NQ",
            "name": "某新三板",
            "source": "watchlist",
            "reason": "流动性差(avg_amount=0.02亿, avg_volume=50万, threshold=0.30亿)",
        },
        "expected_filter_subcategory": FILTER_SUBCATEGORY_LIQUIDITY,
        "expected_fn_type": FP_TYPE_TRUE_NEGATIVE,
    }

    # 3. No strategy hit — should be true_negative
    fixtures["no_strategy_hit"] = {
        "description": "数据完整但无策略命中 → true_negative",
        "filtered_symbol": {
            "symbol": "000001.SZ",
            "name": "平安银行",
            "source": "manual",
            "reason": "无策略命中",
        },
        "expected_filter_subcategory": FILTER_SUBCATEGORY_NO_STRATEGY,
        "expected_fn_type": FP_TYPE_TRUE_NEGATIVE,
    }

    # 4. Event catalyst — event-driven candidate
    fixtures["event_catalyst"] = {
        "description": "事件催化命中，高叙事质量 → true_positive",
        "candidate": _make_candidate(
            symbol="002138.SZ",
            name="顺络电子",
            strategy_tags=["EVENT_CATALYST", "NARRATIVE_QUALITY"],
            score=30.0,
            composite_score=55.0,
            narrative_score=25.0,
            priority_rank="B",
            need_deep_ta=True,
            data_completeness=0.625,
            positive_category_count=2,
        ),
        "expected_audit_category": AUDIT_CATEGORY_HIT_STRATEGY,
        "expected_fp_type": FP_TYPE_TRUE_POSITIVE,
    }

    # 5. Fund flow anomaly — capital anomaly detected
    fixtures["fund_flow_anomaly"] = {
        "description": "资金异动+VCP共振 → true_positive",
        "candidate": _make_candidate(
            symbol="601012.SH",
            name="隆基绿能",
            strategy_tags=["VCP", "FUND_FLOW_ANOMALY"],
            score=50.0,
            composite_score=65.0,
            fund_flow_anomaly_score=15.0,
            fund_flow_unit_verified=True,
            fund_flow_anomaly_tags=["CONSECUTIVE_INFLOW"],
            priority_rank="A",
            need_deep_ta=True,
            data_completeness=0.75,
            positive_category_count=3,
        ),
        "expected_audit_category": AUDIT_CATEGORY_HIT_STRATEGY,
        "expected_fp_type": FP_TYPE_TRUE_POSITIVE,
    }

    # 6. Risk demoted — high risk flags downgrade
    fixtures["risk_demoted"] = {
        "description": "有策略命中但风险标签过多 → risk_demoted / false_positive",
        "candidate": _make_candidate(
            symbol="300999.SZ",
            name="金龙鱼",
            strategy_tags=["VCP"],
            score=40.0,
            composite_score=15.0,
            risk_flags=["INQUIRY_RISK", "LOCKUP_RISK", "MARGIN_CROWDING_RISK"],
            risk_penalty=-25.0,
            priority_rank="C",
            need_deep_ta=False,
            data_completeness=0.5,
            game_balance="fragile",
        ),
        "expected_audit_category": AUDIT_CATEGORY_RISK_DEMOTED,
        "expected_fp_type": FP_TYPE_FALSE_POSITIVE,
    }

    # 7. Evidence gap — insufficient data completeness
    fixtures["evidence_gap"] = {
        "description": "有策略命中但数据完整度低 → evidence_gap / false_positive",
        "candidate": _make_candidate(
            symbol="688981.SH",
            name="中芯国际",
            strategy_tags=["VCP"],
            score=35.0,
            composite_score=20.0,
            priority_rank="C",
            need_deep_ta=False,
            data_completeness=0.25,
            missing_evidence=["事件/新闻数据", "资金流数据", "资金单位校验", "政策版本信号", "叙事质量信号"],
        ),
        "expected_audit_category": AUDIT_CATEGORY_EVIDENCE_GAP,
        "expected_fp_type": FP_TYPE_FALSE_POSITIVE,
    }

    # 8. Fund flow unit unverified — should not be high confidence
    fixtures["fund_flow_unverified"] = {
        "description": "资金异动但单位未校验 → evidence_gap",
        "candidate": _make_candidate(
            symbol="002415.SZ",
            name="海康威视",
            strategy_tags=["VCP", "FUND_FLOW_ANOMALY"],
            score=40.0,
            composite_score=35.0,
            fund_flow_anomaly_score=10.0,
            fund_flow_unit_verified=False,
            fund_flow_anomaly_tags=["CONSECUTIVE_INFLOW"],
            priority_rank="C",
            need_deep_ta=False,
            data_completeness=0.5,
            missing_evidence=["资金单位校验", "事件/新闻数据"],
        ),
        "expected_audit_category": AUDIT_CATEGORY_EVIDENCE_GAP,
        "expected_fp_type": FP_TYPE_FALSE_POSITIVE,
    }

    return fixtures


def _make_candidate(**kwargs) -> Candidate:
    defaults = {
        "symbol": "000000.SZ",
        "name": "",
        "source": "manual",
        "strategy_tags": [],
        "score": 0.0,
        "risk_flags": [],
        "risk_penalty": 0.0,
        "composite_score": 0.0,
        "priority_rank": "",
        "need_deep_ta": False,
        "data_completeness": 0.0,
        "missing_evidence": [],
        "game_balance": "",
        "trade_date": "2026-05-29",
    }
    defaults.update(kwargs)
    return Candidate(**defaults)


def replay_fixtures(fixtures: Optional[dict] = None) -> list[dict]:
    """Replay fixture samples and return audit results for each.

    Args:
        fixtures: Dict from generate_fixture_samples(). If None, generates defaults.

    Returns:
        List of dicts with fixture name, audit entry, and pass/fail info.
    """
    if fixtures is None:
        fixtures = generate_fixture_samples()

    results = []
    for name, fixture in fixtures.items():
        result_entry = {"fixture_name": name, "description": fixture.get("description", "")}

        if "candidate" in fixture:
            entry = audit_candidate(fixture["candidate"])
            result_entry["audit_entry"] = asdict(entry)
            result_entry["actual_category"] = entry.audit_category
            result_entry["expected_category"] = fixture.get("expected_audit_category", "")

            if "expected_fp_type" in fixture:
                result_entry["actual_fp_type"] = entry.fp_type
                result_entry["expected_fp_type"] = fixture["expected_fp_type"]
                result_entry["fp_match"] = entry.fp_type == fixture["expected_fp_type"]

            result_entry["category_match"] = entry.audit_category == fixture.get("expected_audit_category", "")

        elif "filtered_symbol" in fixture:
            f = fixture["filtered_symbol"]
            entry = audit_filtered_symbol(
                symbol=f.get("symbol", ""),
                name=f.get("name", ""),
                source=f.get("source", ""),
                reason=f.get("reason", ""),
            )
            result_entry["audit_entry"] = asdict(entry)
            result_entry["actual_subcategory"] = entry.filter_subcategory
            result_entry["expected_subcategory"] = fixture.get("expected_filter_subcategory", "")

            if "expected_fn_type" in fixture:
                result_entry["actual_fn_type"] = entry.fn_type
                result_entry["expected_fn_type"] = fixture["expected_fn_type"]
                result_entry["fn_match"] = entry.fn_type == fixture["expected_fn_type"]

            result_entry["subcategory_match"] = entry.filter_subcategory == fixture.get("expected_filter_subcategory", "")

        results.append(result_entry)

    return results
