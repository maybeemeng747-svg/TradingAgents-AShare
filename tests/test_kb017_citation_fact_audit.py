# [KB-017] citation_fact_audit
"""Tests for 研报观点 vs 公告/半年报事实 citation 审计 (KB-017).

覆盖（对应任务验收方式）：
  - fixture 覆盖五类：支持 (supported) / 削弱 (weakened) / 打脸 (contradicted)
    / 待验证 (pending) / 缺事实 (insufficient_data)。
  - citation_audit_status 枚举正确（5 个状态）。
  - 弱来源不得覆盖强来源（KB-014 硬约束）：
    ``weak_source_blocked=True`` 显式标记。
  - 缺半年报事实时不得强行判定观点错误（只走 insufficient_data / pending）。
  - 输出 ``audit_summary`` 不含买卖建议词，TA 动作语义不被 audit 覆写。
  - KB-014 tier 优先级：original_filing > official_notice > broker_research >
    media > user_note。
  - JSON 序列化稳定。
  - 失败路径：缺观点 / 缺事实 / 事实页不可用 / 输入类型错误。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

from tradingagents.dataflows.citation_fact_audit import (
    ALL_AUDIT_STATUSES,
    EVIDENCE_MODERATE,
    EVIDENCE_NONE,
    EVIDENCE_STRONG,
    EVIDENCE_WEAK,
    STATUS_CONTRADICTED,
    STATUS_INSUFFICIENT_DATA,
    STATUS_PENDING,
    STATUS_SUPPORTED,
    STATUS_WEAKENED,
    TASK_CODE,
    CitationAuditFlag,
    CitationAuditResult,
    _aggregate_audit_status,
    _audit_financial_metric_claim,
    _audit_risk_claim,
    _audit_segment_claim,
    _build_tiered_fact_index,
    _collect_claims,
    _collect_risk_claims,
    _is_weak_source_override,
    _looks_like_future_period_claim,
    _page_source_tier,
    _TieredFactIndex,
    audit_citation_against_facts,
    audit_to_ta_consumable_summary,
    has_forbidden_action_words,
    render_citation_audit_inline,
    render_citation_audit_report,
    suggest_report_output_path,
)
from tradingagents.dataflows.citation_policy import (
    TIER_BROKER_RESEARCH,
    TIER_MEDIA,
    TIER_OFFICIAL_NOTICE,
    TIER_ORIGINAL_FILING,
    TIER_UNKNOWN,
    TIER_USER_NOTE,
)
from tradingagents.dataflows.half_year_facts_provider import (
    DATA_CONFLICT,
    DATA_FRESH,
    DATA_MISSING_FACTS,
    DATA_MISSING_PERIOD,
    DATA_OPINION_ONLY,
    DATA_STALE,
    HalfYearFactsPage,
    HalfYearFactsQueryResult,
    ParsedMetric,
)
from tradingagents.dataflows.local_knowledge_provider import (
    STATUS_HAS_DATA,
    STATUS_NORMAL_NO_DATA,
)
from tradingagents.dataflows.research_fact_opinion_index import (
    CLAIM_FACT,
    CLAIM_FORECAST,
    CLAIM_OPINION,
    CLAIM_RISK,
    CLAIM_UNKNOWN,
    ResearchClaimItem,
    ResearchFactOpinionIndexResult,
    ResearchFactOpinionPage,
    STALE_FRESH,
)


# ── helpers ──────────────────────────────────────────────────────────


_FORBIDDEN_ACTION_WORDS = (
    "买入", "卖出", "加仓", "减仓", "强烈推荐", "BUY", "SELL",
    "立即买入", "全仓", "止损",
)


def _assert_no_strong_action_words(text: str) -> None:
    for forbidden in _FORBIDDEN_ACTION_WORDS:
        assert forbidden not in text, (
            f"text contains forbidden action word: {forbidden}"
        )


def _make_metric(
    metric_key: str,
    label: str,
    raw: str,
    value: str | None = None,
    change: str | None = None,
) -> ParsedMetric:
    return ParsedMetric(
        metric_key=metric_key,
        metric_label=label,
        raw=raw,
        value=value,
        change=change,
    )


def _make_facts_page(
    *,
    rel_path: str = "wiki/investment/test-half-year.md",
    title: str = "测试半年报",
    period: str = "2025H1",
    facts: List[ParsedMetric] | None = None,
    segments: List[str] | None = None,
    risks: List[str] | None = None,
    data_status: str = DATA_FRESH,
    source_type: List[str] | None = None,
    disclosure_date: str = "2026-08-29",
) -> HalfYearFactsPage:
    return HalfYearFactsPage(
        rel_path=rel_path,
        title=title,
        financial_period=period,
        disclosure_date=disclosure_date,
        source_type=source_type or ["exchange_filing", "fact_table"],
        financial_facts=facts or [],
        segment_facts=segments or [],
        risk_factors=risks or [],
        data_status=data_status,
    )


def _make_facts_result(
    *,
    pages: List[HalfYearFactsPage],
    symbol: str = "300750",
    name: str = "测试公司",
    status: str = STATUS_HAS_DATA,
) -> HalfYearFactsQueryResult:
    return HalfYearFactsQueryResult(
        status=status,
        symbol=symbol,
        name=name,
        pages=pages,
        latest_period=pages[0].financial_period if pages else None,
        data_status=pages[0].data_status if pages else DATA_MISSING_FACTS,
    )


def _make_claim(
    text: str,
    *,
    claim_type: str = CLAIM_OPINION,
    origin_field: str = "body:opinion",
    rel_path: str = "wiki/investment/test-research.md",
    symbol: str = "300750",
    name: str = "测试公司",
    tier: str = TIER_BROKER_RESEARCH,
    confidence: str = "medium",
) -> ResearchClaimItem:
    return ResearchClaimItem(
        claim_type=claim_type,
        text=text,
        origin_field=origin_field,
        symbol=symbol,
        name=name,
        source_path=rel_path,
        source_quality_tier=tier,
        report_date="2026-02-15",
        stale_status=STALE_FRESH,
        confidence=confidence,
    )


def _make_opinion_index(
    *,
    research_claims: List[ResearchClaimItem] | None = None,
    forecast_items: List[ResearchClaimItem] | None = None,
    risk_items: List[ResearchClaimItem] | None = None,
    pages: List[ResearchFactOpinionPage] | None = None,
    symbol: str = "300750",
    name: str = "测试公司",
    page_tier: str = TIER_BROKER_RESEARCH,
) -> ResearchFactOpinionIndexResult:
    if pages is None:
        page = ResearchFactOpinionPage(
            rel_path="wiki/investment/test-research.md",
            title="测试研报",
            symbols=[f"{symbol}.SZ {name}"],
            symbol=symbol,
            name=name,
            source_quality_tier=page_tier,
            stale_status=STALE_FRESH,
            confidence="medium",
            research_claims=research_claims or [],
            forecast_items=forecast_items or [],
            risk_items=risk_items or [],
        )
        pages = [page]
    return ResearchFactOpinionIndexResult(
        status=STATUS_HAS_DATA,
        pages=pages,
        symbols=[f"{symbol}.SZ {name}"],
        names=[name],
    )


def _make_fact_idx(
    *,
    metrics: Dict[str, ParsedMetric] | None = None,
    segments: List[str] | None = None,
    risks: List[str] | None = None,
    period: str = "2025H1",
    data_status: str = DATA_FRESH,
    source_path: str = "wiki/investment/test-half-year.md",
    source_tier: str = TIER_ORIGINAL_FILING,
    available: bool = True,
) -> _TieredFactIndex:
    return _TieredFactIndex(
        metrics=metrics or {},
        segments=segments or [],
        risks=risks or [],
        period=period,
        data_status=data_status,
        source_path=source_path,
        source_tier=source_tier,
        available=available,
    )


# ── 单元：枚举与基础常量 ─────────────────────────────────────────────


class TestEnums:
    def test_all_audit_statuses_has_five_values(self) -> None:
        assert set(ALL_AUDIT_STATUSES) == {
            STATUS_SUPPORTED,
            STATUS_WEAKENED,
            STATUS_CONTRADICTED,
            STATUS_PENDING,
            STATUS_INSUFFICIENT_DATA,
        }

    def test_pending_is_new_vs_hy005(self) -> None:
        """KB-017 新增 pending 状态（HY-005 只有 4 个状态）。"""
        assert STATUS_PENDING == "pending"
        assert STATUS_PENDING in ALL_AUDIT_STATUSES

    def test_task_code(self) -> None:
        assert TASK_CODE == "KB-017"


# ── 单元：未来期观点检测（pending 触发）──────────────────────────────


class TestLooksLikeFuturePeriodClaim:
    def test_forecast_origin_field_triggers(self) -> None:
        assert _looks_like_future_period_claim("营收增长", "forward_guidance") is True
        assert _looks_like_future_period_claim("营收增长", "body:forecast") is True

    def test_keyword_2026(self) -> None:
        assert _looks_like_future_period_claim("2026全年营收将爆发") is True

    def test_keyword_next_year(self) -> None:
        assert _looks_like_future_period_claim("明年业绩展望") is True

    def test_keyword_second_half(self) -> None:
        assert _looks_like_future_period_claim("下半年展望") is True

    def test_no_future_keyword(self) -> None:
        assert _looks_like_future_period_claim("营收高增", "body:opinion") is False

    def test_empty_text(self) -> None:
        assert _looks_like_future_period_claim("", "body:opinion") is False


# ── 单元：KB-014 tier 优先级 ─────────────────────────────────────────


class TestPageSourceTier:
    def test_exchange_filing_is_original_filing(self) -> None:
        page = _make_facts_page(source_type=["exchange_filing", "fact_table"])
        assert _page_source_tier(page) == TIER_ORIGINAL_FILING

    def test_official_notice(self) -> None:
        page = _make_facts_page(source_type=["official_notice"])
        assert _page_source_tier(page) == TIER_OFFICIAL_NOTICE

    def test_broker_report(self) -> None:
        page = _make_facts_page(source_type=["broker_report"])
        assert _page_source_tier(page) == TIER_BROKER_RESEARCH

    def test_media(self) -> None:
        page = _make_facts_page(source_type=["media"])
        assert _page_source_tier(page) == TIER_MEDIA

    def test_empty_source_type(self) -> None:
        # 直接构造，绕过 _make_facts_page 的 `source_type or default` 兜底。
        page = HalfYearFactsPage(
            rel_path="p.md",
            title="t",
            financial_period="2025H1",
            disclosure_date="2026-08-29",
            source_type=[],
            data_status=DATA_FRESH,
        )
        assert _page_source_tier(page) == TIER_UNKNOWN

    def test_mixed_fact_and_opinion_picks_strongest(self) -> None:
        """事实类 + 观点类混用时，取最高 tier（original_filing）。"""
        page = _make_facts_page(
            source_type=["exchange_filing", "broker_report"]
        )
        assert _page_source_tier(page) == TIER_ORIGINAL_FILING


class TestIsWeakSourceOverride:
    def test_broker_over_filing_blocked(self) -> None:
        """broker 观点 + 观点正向 + 事实负向 → 弱来源覆盖被阻止。"""
        assert (
            _is_weak_source_override(
                TIER_BROKER_RESEARCH, TIER_ORIGINAL_FILING, "positive", "negative"
            )
            is True
        )

    def test_media_over_broker_blocked(self) -> None:
        assert (
            _is_weak_source_override(
                TIER_MEDIA, TIER_BROKER_RESEARCH, "positive", "negative"
            )
            is True
        )

    def test_same_tier_not_blocked(self) -> None:
        assert (
            _is_weak_source_override(
                TIER_BROKER_RESEARCH, TIER_BROKER_RESEARCH, "positive", "negative"
            )
            is False
        )

    def test_strong_over_weak_not_blocked(self) -> None:
        """强来源观点与弱来源事实方向相反时不算"弱覆盖强"。"""
        assert (
            _is_weak_source_override(
                TIER_ORIGINAL_FILING, TIER_MEDIA, "positive", "negative"
            )
            is False
        )

    def test_neutral_direction_not_blocked(self) -> None:
        assert (
            _is_weak_source_override(
                TIER_MEDIA, TIER_ORIGINAL_FILING, "neutral", "negative"
            )
            is False
        )

    def test_same_direction_not_blocked(self) -> None:
        assert (
            _is_weak_source_override(
                TIER_MEDIA, TIER_ORIGINAL_FILING, "positive", "positive"
            )
            is False
        )


# ── 单元：tier 优先级事实索引构建 ───────────────────────────────────


class TestBuildTieredFactIndex:
    def test_picks_highest_tier_page(self) -> None:
        """filing 页 + broker 页同时存在 → 选 filing。"""
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    rel_path="broker.md",
                    period="2025H1",
                    source_type=["broker_report"],
                    facts=[_make_metric("revenue", "营收", "营收 100亿 (+10%)")],
                ),
                _make_facts_page(
                    rel_path="filing.md",
                    period="2025H1",
                    source_type=["exchange_filing", "fact_table"],
                    facts=[_make_metric("revenue", "营收", "营收 200亿 (+30%)")],
                ),
            ]
        )
        idx = _build_tiered_fact_index(facts)
        assert idx.available is True
        assert idx.source_tier == TIER_ORIGINAL_FILING
        assert idx.source_path == "filing.md"
        assert idx.metrics["revenue"].raw.startswith("营收 200亿")

    def test_same_tier_picks_latest_period(self) -> None:
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    rel_path="old.md",
                    period="2024H1",
                    source_type=["exchange_filing"],
                    facts=[_make_metric("revenue", "营收", "营收 100亿")],
                ),
                _make_facts_page(
                    rel_path="new.md",
                    period="2025H1",
                    source_type=["exchange_filing"],
                    facts=[_make_metric("revenue", "营收", "营收 200亿")],
                ),
            ]
        )
        idx = _build_tiered_fact_index(facts)
        assert idx.period == "2025H1"
        assert idx.source_path == "new.md"

    def test_opinion_only_excluded(self) -> None:
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    data_status=DATA_OPINION_ONLY,
                    source_type=["broker_report"],
                    facts=[_make_metric("revenue", "营收", "营收 100亿")],
                ),
            ]
        )
        idx = _build_tiered_fact_index(facts)
        assert idx.available is False
        assert idx.metrics == {}

    def test_missing_period_excluded(self) -> None:
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    data_status=DATA_MISSING_PERIOD,
                    source_type=["exchange_filing"],
                ),
            ]
        )
        idx = _build_tiered_fact_index(facts)
        assert idx.available is False

    def test_empty_pages(self) -> None:
        facts = _make_facts_result(pages=[])
        idx = _build_tiered_fact_index(facts)
        assert idx.available is False

    def test_not_a_query_result(self) -> None:
        idx = _build_tiered_fact_index("not a result")  # type: ignore[arg-type]
        assert idx.available is False

    def test_conflict_page_is_reliable(self) -> None:
        """conflict 页仍可用于反证（事实值可能冲突，但仍是可靠来源）。"""
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    data_status=DATA_CONFLICT,
                    source_type=["exchange_filing"],
                    facts=[_make_metric("revenue", "营收", "营收 100亿")],
                ),
            ]
        )
        idx = _build_tiered_fact_index(facts)
        assert idx.available is True
        assert idx.data_status == DATA_CONFLICT


# ── 单元：财务指标观点审计（5 状态）──────────────────────────────────


class TestAuditFinancialMetricClaim:
    def test_contradicted_direction(self) -> None:
        """观点正向 + 事实负向 → contradicted。"""
        claim = _make_claim("储能业务将爆发式增长，营收 +60%")
        fact_idx = _make_fact_idx(
            metrics={
                "revenue": _make_metric(
                    "revenue", "营收", "营收 120亿 (-20.0% YoY)", "120亿", "-20.0%"
                )
            }
        )
        flag = _audit_financial_metric_claim(
            claim, "p.md", TIER_BROKER_RESEARCH, "revenue", fact_idx
        )
        assert flag.audit_status == STATUS_CONTRADICTED
        assert flag.evidence_level == EVIDENCE_STRONG
        assert flag.opinion_direction == "positive"
        assert flag.fact_direction == "negative"

    def test_weak_source_blocked_on_contradicted(self) -> None:
        """弱来源观点（broker）vs 强来源事实（filing）方向相反 → blocked=True。"""
        claim = _make_claim("营收将爆发增长", tier=TIER_BROKER_RESEARCH)
        fact_idx = _make_fact_idx(
            metrics={
                "revenue": _make_metric(
                    "revenue", "营收", "营收 120亿 (-20%)", "120亿", "-20%"
                )
            },
            source_tier=TIER_ORIGINAL_FILING,
        )
        flag = _audit_financial_metric_claim(
            claim, "p.md", TIER_BROKER_RESEARCH, "revenue", fact_idx
        )
        assert flag.audit_status == STATUS_CONTRADICTED
        assert flag.weak_source_blocked is True
        assert "弱来源" in flag.reason

    def test_supported_same_direction(self) -> None:
        claim = _make_claim("营收将增长")
        fact_idx = _make_fact_idx(
            metrics={
                "revenue": _make_metric(
                    "revenue", "营收", "营收 150亿 (+30% YoY)", "150亿", "+30%"
                )
            }
        )
        flag = _audit_financial_metric_claim(
            claim, "p.md", TIER_BROKER_RESEARCH, "revenue", fact_idx
        )
        assert flag.audit_status == STATUS_SUPPORTED
        assert flag.evidence_level == EVIDENCE_MODERATE

    def test_weakened_low_amplitude(self) -> None:
        """观点预期 +60% 事实 +20% (20 < 60*0.5=30) → weakened。"""
        claim = _make_claim("营收将爆发式增长 +60%")
        fact_idx = _make_fact_idx(
            metrics={
                "revenue": _make_metric(
                    "revenue", "营收", "营收 150亿 (+20% YoY)", "150亿", "+20%"
                )
            }
        )
        flag = _audit_financial_metric_claim(
            claim, "p.md", TIER_BROKER_RESEARCH, "revenue", fact_idx
        )
        assert flag.audit_status == STATUS_WEAKENED

    def test_insufficient_no_metric(self) -> None:
        """观点讨论 revenue 但事实无 revenue → insufficient_data。"""
        claim = _make_claim("营收将爆发")
        fact_idx = _make_fact_idx(metrics={})
        flag = _audit_financial_metric_claim(
            claim, "p.md", TIER_BROKER_RESEARCH, "revenue", fact_idx
        )
        assert flag.audit_status == STATUS_INSUFFICIENT_DATA

    def test_pending_forecast_origin(self) -> None:
        """forward_guidance 类观点 → pending（不论事实是否存在）。"""
        claim = _make_claim(
            "全年营收将继续高增",
            claim_type=CLAIM_FORECAST,
            origin_field="forward_guidance",
        )
        fact_idx = _make_fact_idx(
            metrics={
                "revenue": _make_metric(
                    "revenue", "营收", "营收 150亿 (+30%)", "150亿", "+30%"
                )
            }
        )
        flag = _audit_financial_metric_claim(
            claim, "p.md", TIER_BROKER_RESEARCH, "revenue", fact_idx
        )
        assert flag.audit_status == STATUS_PENDING
        assert flag.pending_reason != ""
        # pending 状态不进 contradicted（缺数据不得强行判定观点错误）。
        assert flag.evidence_level == EVIDENCE_NONE

    def test_pending_with_future_keyword(self) -> None:
        """非 forecast origin 但观点含 "2026全年" → pending。"""
        claim = _make_claim("2026全年营收将爆发", origin_field="body:opinion")
        fact_idx = _make_fact_idx(
            metrics={
                "revenue": _make_metric(
                    "revenue", "营收", "营收 150亿 (+30%)", "150亿", "+30%"
                )
            }
        )
        flag = _audit_financial_metric_claim(
            claim, "p.md", TIER_BROKER_RESEARCH, "revenue", fact_idx
        )
        assert flag.audit_status == STATUS_PENDING


# ── 单元：分业务观点审计 ─────────────────────────────────────────────


class TestAuditSegmentClaim:
    def test_contradicted_segment(self) -> None:
        claim = _make_claim("储能业务将爆发式增长")
        fact_idx = _make_fact_idx(segments=["储能出货同比下滑"])
        flag = _audit_segment_claim(
            claim, "p.md", TIER_BROKER_RESEARCH, fact_idx
        )
        assert flag.audit_status == STATUS_CONTRADICTED
        assert flag.metric_key == "segment"

    def test_supported_segment(self) -> None:
        claim = _make_claim("AI服务器业务将放量增长")
        fact_idx = _make_fact_idx(segments=["AI服务器营收大幅增长"])
        flag = _audit_segment_claim(
            claim, "p.md", TIER_BROKER_RESEARCH, fact_idx
        )
        assert flag.audit_status == STATUS_SUPPORTED

    def test_insufficient_segment_not_found(self) -> None:
        claim = _make_claim("储能业务将爆发")
        fact_idx = _make_fact_idx(segments=["AI服务器营收增长"])
        flag = _audit_segment_claim(
            claim, "p.md", TIER_BROKER_RESEARCH, fact_idx
        )
        assert flag.audit_status == STATUS_INSUFFICIENT_DATA


# ── 单元：风险观点审计 ───────────────────────────────────────────────


class TestAuditRiskClaim:
    def test_supported_risk_confirmed(self) -> None:
        claim = _make_claim(
            "客户集中度风险较高", claim_type=CLAIM_RISK, origin_field="risk_factors"
        )
        fact_idx = _make_fact_idx(risks=["客户集中度", "原材料价格"])
        flag = _audit_risk_claim(claim, "p.md", TIER_BROKER_RESEARCH, fact_idx)
        assert flag.audit_status == STATUS_SUPPORTED
        assert flag.metric_key == "risk"

    def test_insufficient_risk_not_listed(self) -> None:
        claim = _make_claim(
            "客户集中度风险较高", claim_type=CLAIM_RISK, origin_field="risk_factors"
        )
        fact_idx = _make_fact_idx(risks=["原材料价格"])
        flag = _audit_risk_claim(claim, "p.md", TIER_BROKER_RESEARCH, fact_idx)
        assert flag.audit_status == STATUS_INSUFFICIENT_DATA


# ── 单元：聚合状态 ───────────────────────────────────────────────────


class TestAggregateAuditStatus:
    def test_contradicted_dominates(self) -> None:
        flags = [
            CitationAuditFlag("a", "revenue", STATUS_SUPPORTED),
            CitationAuditFlag("b", "revenue", STATUS_CONTRADICTED),
            CitationAuditFlag("c", "revenue", STATUS_PENDING),
        ]
        assert _aggregate_audit_status(flags) == STATUS_CONTRADICTED

    def test_weakened_over_supported(self) -> None:
        flags = [
            CitationAuditFlag("a", "revenue", STATUS_SUPPORTED),
            CitationAuditFlag("b", "revenue", STATUS_WEAKENED),
        ]
        assert _aggregate_audit_status(flags) == STATUS_WEAKENED

    def test_supported_over_pending(self) -> None:
        flags = [
            CitationAuditFlag("a", "revenue", STATUS_SUPPORTED),
            CitationAuditFlag("b", "revenue", STATUS_PENDING),
        ]
        assert _aggregate_audit_status(flags) == STATUS_SUPPORTED

    def test_pending_over_insufficient(self) -> None:
        flags = [
            CitationAuditFlag("a", "revenue", STATUS_PENDING),
            CitationAuditFlag("b", "revenue", STATUS_INSUFFICIENT_DATA),
        ]
        assert _aggregate_audit_status(flags) == STATUS_PENDING

    def test_all_insufficient(self) -> None:
        flags = [
            CitationAuditFlag("a", "revenue", STATUS_INSUFFICIENT_DATA),
        ]
        assert _aggregate_audit_status(flags) == STATUS_INSUFFICIENT_DATA

    def test_empty(self) -> None:
        assert _aggregate_audit_status([]) == STATUS_INSUFFICIENT_DATA


# ── 主入口：五类 fixture 覆盖 ────────────────────────────────────────


class TestAuditCitationAgainstFactsFiveCategories:
    """验收：fixture 覆盖支持、削弱、打脸、待验证、缺事实五类。"""

    def test_supported(self) -> None:
        """观点正向 + 事实同向 → 整体 supported。"""
        opinions = _make_opinion_index(
            research_claims=[_make_claim("营收将高增长")]
        )
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    facts=[
                        _make_metric(
                            "revenue", "营收", "营收 200亿 (+30%)", "200亿", "+30%"
                        )
                    ]
                )
            ]
        )
        result = audit_citation_against_facts(opinions, facts)
        assert result.citation_audit_status == STATUS_SUPPORTED
        assert len(result.supported_flags) == 1
        assert result.checked_claim_count == 1

    def test_weakened(self) -> None:
        opinions = _make_opinion_index(
            research_claims=[_make_claim("营收将爆发 +60%")]
        )
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    facts=[
                        _make_metric(
                            "revenue", "营收", "营收 200亿 (+20%)", "200亿", "+20%"
                        )
                    ]
                )
            ]
        )
        result = audit_citation_against_facts(opinions, facts)
        assert result.citation_audit_status == STATUS_WEAKENED
        assert len(result.weakened_flags) == 1

    def test_contradicted(self) -> None:
        opinions = _make_opinion_index(
            research_claims=[_make_claim("储能业务将爆发，营收 +60%")]
        )
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    facts=[
                        _make_metric(
                            "revenue",
                            "营收",
                            "营收 120亿 (-20% YoY)",
                            "120亿",
                            "-20%",
                        )
                    ]
                )
            ]
        )
        result = audit_citation_against_facts(opinions, facts)
        assert result.citation_audit_status == STATUS_CONTRADICTED
        assert len(result.contradicted_flags) == 1
        assert result.needs_tree_work_review is True

    def test_pending(self) -> None:
        """前瞻指引观点 + 事实存在但观点指向未来期 → pending。"""
        opinions = _make_opinion_index(
            forecast_items=[
                _make_claim(
                    "2026全年营收将继续爆发",
                    claim_type=CLAIM_FORECAST,
                    origin_field="forward_guidance",
                )
            ]
        )
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    period="2025H1",
                    facts=[
                        _make_metric(
                            "revenue", "营收", "营收 200亿 (+30%)", "200亿", "+30%"
                        )
                    ],
                )
            ]
        )
        result = audit_citation_against_facts(opinions, facts)
        assert result.citation_audit_status == STATUS_PENDING
        assert len(result.pending_flags) == 1
        # pending 不算 contradicted（缺数据不得强行判定错误）。
        assert len(result.contradicted_flags) == 0
        assert result.needs_tree_work_review is False

    def test_insufficient_data_no_matching_metric(self) -> None:
        """观点讨论 revenue 但事实无 revenue → insufficient_data。"""
        opinions = _make_opinion_index(
            research_claims=[_make_claim("营收将爆发")]
        )
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    facts=[
                        _make_metric(
                            "net_profit", "归母净利", "归母净利 5亿 (+10%)"
                        )
                    ]
                )
            ]
        )
        result = audit_citation_against_facts(opinions, facts)
        assert result.citation_audit_status == STATUS_INSUFFICIENT_DATA
        assert len(result.insufficient_flags) == 1

    def test_insufficient_data_when_fact_unavailable(self) -> None:
        """缺半年报事实（事实页 data_status 不可用）→ 整体 insufficient_data。"""
        opinions = _make_opinion_index(
            research_claims=[_make_claim("营收将爆发")]
        )
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    data_status=DATA_OPINION_ONLY,
                    source_type=["broker_report"],
                    facts=[
                        _make_metric("revenue", "营收", "营收 200亿 (+30%)")
                    ],
                )
            ]
        )
        result = audit_citation_against_facts(opinions, facts)
        assert result.citation_audit_status == STATUS_INSUFFICIENT_DATA
        assert result.total_claim_count == 0  # 事实页不可用直接 short-circuit

    def test_mixed_flags_dominant_contradicted(self) -> None:
        """多观点混合：1 支持 + 1 打脸 → 整体 contradicted。"""
        opinions = _make_opinion_index(
            research_claims=[
                _make_claim("AI服务器业务将放量"),
                _make_claim("储能业务将爆发，营收 +60%"),
            ]
        )
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    facts=[
                        _make_metric(
                            "revenue",
                            "营收",
                            "营收 120亿 (-20% YoY)",
                            "120亿",
                            "-20%",
                        )
                    ],
                    segments=["AI服务器营收大幅增长"],
                )
            ]
        )
        result = audit_citation_against_facts(opinions, facts)
        assert result.citation_audit_status == STATUS_CONTRADICTED
        assert len(result.supported_flags) == 1
        assert len(result.contradicted_flags) == 1


# ── 主入口：KB-014 tier 优先级与弱来源覆盖 ──────────────────────────


class TestTierPriorityAndWeakSourceOverride:
    """验收：弱来源不得覆盖强来源。"""

    def test_broker_opinion_does_not_override_filing_fact(self) -> None:
        """broker 看多 + filing 事实下滑 → contradicted + weak_source_blocked。"""
        opinions = _make_opinion_index(
            research_claims=[
                _make_claim("营收将爆发增长", tier=TIER_BROKER_RESEARCH)
            ],
            page_tier=TIER_BROKER_RESEARCH,
        )
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    source_type=["exchange_filing", "fact_table"],
                    facts=[
                        _make_metric(
                            "revenue",
                            "营收",
                            "营收 120亿 (-20%)",
                            "120亿",
                            "-20%",
                        )
                    ],
                )
            ]
        )
        result = audit_citation_against_facts(opinions, facts)
        assert result.citation_audit_status == STATUS_CONTRADICTED
        assert result.weak_source_override_blocked_count == 1
        assert result.fact_source_tier == TIER_ORIGINAL_FILING
        assert result.needs_tree_work_review is True

    def test_media_opinion_does_not_override_broker_fact(self) -> None:
        """media 观点 + broker 事实方向相反 → blocked."""
        opinions = _make_opinion_index(
            research_claims=[_make_claim("营收将爆发", tier=TIER_MEDIA)],
            page_tier=TIER_MEDIA,
        )
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    source_type=["broker_report"],
                    facts=[
                        _make_metric(
                            "revenue",
                            "营收",
                            "营收 120亿 (-20%)",
                            "120亿",
                            "-20%",
                        )
                    ],
                )
            ]
        )
        result = audit_citation_against_facts(opinions, facts)
        # 方向相反（positive vs negative）→ contradicted, blocked.
        assert result.citation_audit_status == STATUS_CONTRADICTED
        assert result.weak_source_override_blocked_count == 1
        assert result.fact_source_tier == TIER_BROKER_RESEARCH

    def test_filing_opinion_over_media_fact_not_blocked(self) -> None:
        """强来源观点 vs 弱来源事实方向相反 → 不算"弱覆盖强"。"""
        opinions = _make_opinion_index(
            research_claims=[
                _make_claim("营收将爆发", tier=TIER_ORIGINAL_FILING)
            ],
            page_tier=TIER_ORIGINAL_FILING,
        )
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    source_type=["media"],
                    facts=[
                        _make_metric(
                            "revenue", "营收", "营收 120亿 (-20%)", "120亿", "-20%"
                        )
                    ],
                )
            ]
        )
        result = audit_citation_against_facts(opinions, facts)
        assert result.citation_audit_status == STATUS_CONTRADICTED
        assert result.weak_source_override_blocked_count == 0

    def test_filing_fact_preferred_over_broker_fact(self) -> None:
        """filing 页 + broker 页同时存在，基准事实选 filing。"""
        opinions = _make_opinion_index(
            research_claims=[_make_claim("营收将下滑")]
        )
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    rel_path="broker.md",
                    period="2025H1",
                    source_type=["broker_report"],
                    facts=[
                        _make_metric(
                            "revenue", "营收", "营收 100亿 (+10%)", "100亿", "+10%"
                        )
                    ],
                ),
                _make_facts_page(
                    rel_path="filing.md",
                    period="2025H1",
                    source_type=["exchange_filing", "fact_table"],
                    facts=[
                        _make_metric(
                            "revenue", "营收", "营收 200亿 (+30%)", "200亿", "+30%"
                        )
                    ],
                ),
            ]
        )
        result = audit_citation_against_facts(opinions, facts)
        # broker 说 +10%, filing 说 +30%；观点说"下滑"（negative）。
        # 基准事实是 filing (+30%)，与观点"下滑"方向相反 → contradicted。
        assert result.fact_source_tier == TIER_ORIGINAL_FILING
        assert result.citation_audit_status == STATUS_CONTRADICTED
        flag = result.contradicted_flags[0]
        assert "+30%" in flag.fact_text


# ── 主入口：失败路径与边界 ───────────────────────────────────────────


class TestFailurePaths:
    def test_no_opinions(self) -> None:
        opinions = _make_opinion_index(research_claims=[])
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    facts=[
                        _make_metric("revenue", "营收", "营收 100亿 (+10%)")
                    ]
                )
            ]
        )
        result = audit_citation_against_facts(opinions, facts)
        assert result.citation_audit_status == STATUS_INSUFFICIENT_DATA
        assert result.total_claim_count == 0
        assert any("无可用观点" in e for e in result.errors)

    def test_no_facts(self) -> None:
        opinions = _make_opinion_index(
            research_claims=[_make_claim("营收将爆发")]
        )
        facts = _make_facts_result(pages=[])
        result = audit_citation_against_facts(opinions, facts)
        assert result.citation_audit_status == STATUS_INSUFFICIENT_DATA
        # 缺事实不得强行判定观点错误（不出现 contradicted）。
        assert len(result.contradicted_flags) == 0
        assert len(result.pending_flags) == 0

    def test_facts_status_not_has_data(self) -> None:
        opinions = _make_opinion_index(
            research_claims=[_make_claim("营收将爆发")]
        )
        facts = _make_facts_result(
            pages=[],
            status=STATUS_NORMAL_NO_DATA,
        )
        result = audit_citation_against_facts(opinions, facts)
        assert result.citation_audit_status == STATUS_INSUFFICIENT_DATA
        assert any("不可用" in e for e in result.errors)

    def test_wrong_input_types_do_not_raise(self) -> None:
        """错误输入类型不应抛异常（容错）。"""
        result = audit_citation_against_facts(
            "not an index",  # type: ignore[arg-type]
            "not facts",  # type: ignore[arg-type]
        )
        assert result.citation_audit_status == STATUS_INSUFFICIENT_DATA

    def test_symbol_inferred_from_facts(self) -> None:
        opinions = _make_opinion_index(symbol="", name="")
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    facts=[_make_metric("revenue", "营收", "营收 100亿 (+10%)")]
                )
            ],
            symbol="600519",
            name="贵州茅台",
        )
        # opinion_index 没 symbol，从 facts 推断。
        empty_opinions = ResearchFactOpinionIndexResult(
            status=STATUS_HAS_DATA, pages=[], symbols=[], names=[]
        )
        result = audit_citation_against_facts(empty_opinions, facts)
        assert result.symbol == "600519"
        assert result.name == "贵州茅台"


# ── 输出约束：不含买卖建议词 ─────────────────────────────────────────


class TestNoStrongActionWords:
    """验收：TA 报告动作语义不被 audit 直接覆写。"""

    def test_report_has_no_strong_action_words(self) -> None:
        opinions = _make_opinion_index(
            research_claims=[
                _make_claim("营收将爆发 +60%"),
                _make_claim("储能业务将爆发"),
                _make_claim("2026全年业绩展望继续向好", origin_field="body:opinion"),
            ],
            risk_items=[
                _make_claim("客户集中度风险", claim_type=CLAIM_RISK)
            ],
        )
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    facts=[
                        _make_metric(
                            "revenue",
                            "营收",
                            "营收 120亿 (-20% YoY)",
                            "120亿",
                            "-20%",
                        )
                    ],
                    segments=["储能出货下滑"],
                    risks=["客户集中度"],
                )
            ]
        )
        result = audit_citation_against_facts(opinions, facts)
        report = render_citation_audit_report(result)
        _assert_no_strong_action_words(report)

    def test_inline_summary_has_no_strong_action_words(self) -> None:
        opinions = _make_opinion_index(
            research_claims=[_make_claim("营收将爆发")]
        )
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    facts=[
                        _make_metric(
                            "revenue",
                            "营收",
                            "营收 120亿 (-20%)",
                            "120亿",
                            "-20%",
                        )
                    ]
                )
            ]
        )
        result = audit_citation_against_facts(opinions, facts)
        inline = render_citation_audit_inline(result)
        _assert_no_strong_action_words(inline)

    def test_audit_summary_has_no_strong_action_words(self) -> None:
        opinions = _make_opinion_index(
            research_claims=[_make_claim("营收将爆发")]
        )
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    facts=[
                        _make_metric(
                            "revenue",
                            "营收",
                            "营收 120亿 (-20%)",
                            "120亿",
                            "-20%",
                        )
                    ]
                )
            ]
        )
        result = audit_citation_against_facts(opinions, facts)
        for line in result.audit_summary:
            _assert_no_strong_action_words(line)

    def test_audit_result_has_no_action_fields(self) -> None:
        """CitationAuditResult 不携带 decision / execution_action 字段。"""
        opinions = _make_opinion_index(
            research_claims=[_make_claim("营收将爆发")]
        )
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    facts=[
                        _make_metric(
                            "revenue", "营收", "营收 100亿 (+30%)", "100亿", "+30%"
                        )
                    ]
                )
            ]
        )
        result = audit_citation_against_facts(opinions, facts)
        d = result.to_dict()
        assert "decision" not in d
        assert "execution_action" not in d
        assert "action_label" not in d
        assert "buy_level" not in d

    def test_has_forbidden_action_words_helper(self) -> None:
        assert has_forbidden_action_words("建议买入") is True
        assert has_forbidden_action_words("正常审计文本") is False


# ── 序列化与渲染 ─────────────────────────────────────────────────────


class TestSerializationAndRendering:
    def test_to_dict_json_round_trip(self) -> None:
        opinions = _make_opinion_index(
            research_claims=[_make_claim("营收将爆发 +60%")]
        )
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    facts=[
                        _make_metric(
                            "revenue",
                            "营收",
                            "营收 120亿 (-20%)",
                            "120亿",
                            "-20%",
                        )
                    ]
                )
            ]
        )
        result = audit_citation_against_facts(opinions, facts)
        d = result.to_dict()
        # 必须可 JSON 序列化（dataclass 内不含 Path / datetime 等复杂对象）。
        s = json.dumps(d, ensure_ascii=False)
        d2 = json.loads(s)
        assert d2["citation_audit_status"] == STATUS_CONTRADICTED
        assert d2["symbol"] == "300750"

    def test_flag_to_dict_round_trip(self) -> None:
        flag = CitationAuditFlag(
            claim_text="测试观点",
            metric_key="revenue",
            audit_status=STATUS_CONTRADICTED,
            evidence_level=EVIDENCE_STRONG,
            opinion_source_tier=TIER_BROKER_RESEARCH,
            fact_source_tier=TIER_ORIGINAL_FILING,
            opinion_direction="positive",
            fact_direction="negative",
            fact_text="营收 -20%",
            weak_source_blocked=True,
            reason="测试原因",
        )
        d = flag.to_dict()
        s = json.dumps(d, ensure_ascii=False)
        d2 = json.loads(s)
        assert d2["audit_status"] == STATUS_CONTRADICTED
        assert d2["weak_source_blocked"] is True

    def test_report_has_task_code_header(self) -> None:
        opinions = _make_opinion_index(research_claims=[])
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    facts=[_make_metric("revenue", "营收", "营收 100亿")]
                )
            ]
        )
        result = audit_citation_against_facts(opinions, facts)
        report = render_citation_audit_report(result)
        assert TASK_CODE in report
        assert "citation 审计" in report

    def test_inline_returns_empty_for_insufficient(self) -> None:
        opinions = _make_opinion_index(research_claims=[])
        facts = _make_facts_result(pages=[])
        result = audit_citation_against_facts(opinions, facts)
        assert render_citation_audit_inline(result) == ""

    def test_inline_non_empty_when_supported(self) -> None:
        opinions = _make_opinion_index(
            research_claims=[_make_claim("营收将增长")]
        )
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    facts=[
                        _make_metric(
                            "revenue", "营收", "营收 100亿 (+30%)", "100亿", "+30%"
                        )
                    ]
                )
            ]
        )
        result = audit_citation_against_facts(opinions, facts)
        inline = render_citation_audit_inline(result)
        assert inline.startswith(f"[{TASK_CODE}]")
        assert "支持" in inline

    def test_inline_mentions_pending(self) -> None:
        opinions = _make_opinion_index(
            forecast_items=[
                _make_claim(
                    "2026全年业绩展望",
                    claim_type=CLAIM_FORECAST,
                    origin_field="forward_guidance",
                )
            ]
        )
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    facts=[
                        _make_metric(
                            "revenue", "营收", "营收 100亿 (+30%)", "100亿", "+30%"
                        )
                    ]
                )
            ]
        )
        result = audit_citation_against_facts(opinions, facts)
        inline = render_citation_audit_inline(result)
        assert "待验证" in inline

    def test_ta_consumable_summary_shape(self) -> None:
        opinions = _make_opinion_index(
            research_claims=[_make_claim("营收将爆发")]
        )
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    facts=[
                        _make_metric(
                            "revenue",
                            "营收",
                            "营收 120亿 (-20%)",
                            "120亿",
                            "-20%",
                        )
                    ]
                )
            ]
        )
        result = audit_citation_against_facts(opinions, facts)
        summary = audit_to_ta_consumable_summary(result)
        assert summary["task"] == TASK_CODE
        assert summary["citation_audit_status"] == STATUS_CONTRADICTED
        assert "counts" in summary
        assert set(summary["counts"].keys()) == set(ALL_AUDIT_STATUSES)
        # TA 消费摘要同样不含强动作字段。
        assert "decision" not in summary
        assert "execution_action" not in summary

    def test_ta_consumable_summary_handles_wrong_type(self) -> None:
        summary = audit_to_ta_consumable_summary("not a result")  # type: ignore[arg-type]
        assert summary["citation_audit_status"] == STATUS_INSUFFICIENT_DATA
        assert summary["counts"][STATUS_PENDING] == 0

    def test_suggest_report_output_path(self) -> None:
        path = suggest_report_output_path()
        assert "citation_fact_audit-" in path
        assert path.endswith(".md")


# ── 集成：从 KB-015 + HY-003 真实输入流跑通 ─────────────────────────


class TestIntegrationWithUpstream:
    """从 KB-015 / HY-003 风格的输入端到端跑一次审计。"""

    def test_full_pipeline_supported(self) -> None:
        """模拟一份合格半年报 + 一份券商观点 → supported."""
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    rel_path="wiki/investment/filing-2025H1.md",
                    title="测试公司-2025H1半年报",
                    period="2025H1",
                    source_type=["exchange_filing", "fact_table"],
                    facts=[
                        _make_metric(
                            "revenue",
                            "营收",
                            "营收 420.4亿 (+60.0% YoY)",
                            "420.4亿",
                            "+60.0%",
                        ),
                        _make_metric(
                            "net_profit",
                            "归母净利",
                            "归母净利 12.5亿 (+45.2% YoY)",
                            "12.5亿",
                            "+45.2%",
                        ),
                    ],
                    segments=["AI服务器营收占比提升"],
                    risks=["上游GPU供应", "客户集中度"],
                )
            ]
        )
        opinions = _make_opinion_index(
            research_claims=[
                _make_claim(
                    "AI服务器业务放量增长，营收高增", tier=TIER_BROKER_RESEARCH
                )
            ],
            risk_items=[
                _make_claim(
                    "客户集中度风险值得关注",
                    claim_type=CLAIM_RISK,
                    origin_field="risk_factors",
                )
            ],
        )
        result = audit_citation_against_facts(opinions, facts)
        assert result.citation_audit_status == STATUS_SUPPORTED
        assert result.fact_source_tier == TIER_ORIGINAL_FILING
        assert result.fact_period == "2025H1"

    def test_forecast_origin_always_pending_regardless_of_fact(self) -> None:
        """前瞻指引无论事实如何都标 pending（事实期未覆盖未来期）。"""
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    period="2025H1",
                    source_type=["exchange_filing"],
                    facts=[
                        _make_metric(
                            "revenue",
                            "营收",
                            "营收 420.4亿 (+60.0% YoY)",
                            "420.4亿",
                            "+60.0%",
                        )
                    ],
                )
            ]
        )
        opinions = _make_opinion_index(
            forecast_items=[
                _make_claim(
                    "下半年营收将继续高增",
                    claim_type=CLAIM_FORECAST,
                    origin_field="forward_guidance",
                )
            ]
        )
        result = audit_citation_against_facts(opinions, facts)
        # 前瞻指引触发 pending（即使事实存在）。
        assert len(result.pending_flags) == 1
        assert result.citation_audit_status == STATUS_PENDING

    def test_multiple_metrics_mixed_status(self) -> None:
        """多指标混合：revenue 反向 + segment 同向 + risk 命中 → 整体 contradicted。"""
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    source_type=["exchange_filing"],
                    facts=[
                        _make_metric(
                            "revenue",
                            "营收",
                            "营收 120亿 (-20%)",
                            "120亿",
                            "-20%",
                        )
                    ],
                    segments=["AI服务器营收大幅增长"],
                    risks=["客户集中度"],
                )
            ]
        )
        opinions = _make_opinion_index(
            research_claims=[
                _make_claim("营收将爆发 +60%"),  # → contradicted
                _make_claim("AI服务器业务将放量"),  # → supported
            ],
            risk_items=[
                _make_claim(
                    "客户集中度风险",
                    claim_type=CLAIM_RISK,
                    origin_field="risk_factors",
                )  # → supported
            ],
        )
        result = audit_citation_against_facts(opinions, facts)
        assert result.citation_audit_status == STATUS_CONTRADICTED
        assert len(result.contradicted_flags) == 1
        assert len(result.supported_flags) == 2
        assert result.total_claim_count == 3
        assert result.checked_claim_count == 3


# ── 收集函数 ─────────────────────────────────────────────────────────


class TestCollectFunctions:
    def test_collect_claims_includes_forecast(self) -> None:
        idx = _make_opinion_index(
            research_claims=[_make_claim("营收增长")],
            forecast_items=[
                _make_claim(
                    "全年指引",
                    claim_type=CLAIM_FORECAST,
                    origin_field="forward_guidance",
                )
            ],
        )
        claims = _collect_claims(idx)
        assert len(claims) == 2

    def test_collect_claims_excludes_neutral_facts(self) -> None:
        """reported_facts 中无方向关键词的不进入审计。"""
        page = ResearchFactOpinionPage(
            rel_path="p.md",
            title="t",
            source_quality_tier=TIER_ORIGINAL_FILING,
            reported_facts=[
                _make_claim("营收 100亿", claim_type=CLAIM_FACT),  # 无方向
            ],
        )
        idx = ResearchFactOpinionIndexResult(
            status=STATUS_HAS_DATA, pages=[page], symbols=[], names=[]
        )
        claims = _collect_claims(idx)
        assert len(claims) == 0

    def test_collect_claims_includes_directional_facts(self) -> None:
        """reported_facts 中含方向关键词的进入审计。"""
        page = ResearchFactOpinionPage(
            rel_path="p.md",
            title="t",
            source_quality_tier=TIER_ORIGINAL_FILING,
            reported_facts=[
                _make_claim("营收高增 +30%", claim_type=CLAIM_FACT),
            ],
        )
        idx = ResearchFactOpinionIndexResult(
            status=STATUS_HAS_DATA, pages=[page], symbols=[], names=[]
        )
        claims = _collect_claims(idx)
        assert len(claims) == 1

    def test_collect_risk_claims(self) -> None:
        idx = _make_opinion_index(
            risk_items=[
                _make_claim(
                    "客户集中度风险",
                    claim_type=CLAIM_RISK,
                    origin_field="risk_factors",
                )
            ]
        )
        risks = _collect_risk_claims(idx)
        assert len(risks) == 1
        assert risks[0][0].claim_type == CLAIM_RISK

    def test_collect_claims_wrong_type(self) -> None:
        assert _collect_claims("not an index") == []  # type: ignore[arg-type]
        assert _collect_risk_claims("not an index") == []  # type: ignore[arg-type]
