# [HY-005] thesis_fact_check
"""Tests for 旧研报观点 vs 半年报事实反证检测 (HY-005).

覆盖（对应任务验收方式）：
  - fixture 覆盖四类：支持 (supported) / 削弱 (weakened) / 打脸 (contradicted)
    / 缺数据 (insufficient_data)。
  - thesis_check_status 枚举正确。
  - contradiction_flags / weakened_flags / supported_flags 分流正确。
  - needs_tree_work_review 触发条件。
  - KB-007 research attention 联动：priority_reminder。
  - 报告不出现强买卖词。
  - 不把单一指标变化扩大成"逻辑破坏"（evidence_level）。
  - 反证提醒可输出 inline 摘要。
  - JSON 序列化稳定。
  - 缺观点 / 缺事实 / 事实页不可用的失败路径。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

import pytest

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
    STATUS_FAILED,
    STATUS_HAS_DATA,
    STATUS_NORMAL_NO_DATA,
    STATUS_STALE,
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
from tradingagents.dataflows.thesis_fact_check import (
    ALL_THESIS_STATUSES,
    DIR_NEGATIVE,
    DIR_NEUTRAL,
    DIR_POSITIVE,
    EVIDENCE_MODERATE,
    EVIDENCE_NONE,
    EVIDENCE_STRONG,
    EVIDENCE_WEAK,
    STATUS_CONTRADICTED,
    STATUS_INSUFFICIENT_DATA,
    STATUS_SUPPORTED,
    STATUS_WEAKENED,
    TASK_CODE,
    ThesisCheckFlag,
    ThesisFactCheckResult,
    _aggregate_thesis_status,
    _build_fact_index,
    _check_financial_metric_opinion,
    _check_risk_opinion,
    _check_segment_opinion,
    _classify_direction,
    _collect_opinions,
    _FactIndex,
    _match_opinion_to_metric,
    _parse_pct,
    check_thesis_against_facts,
    render_thesis_check_inline,
    render_thesis_fact_check_report,
    suggest_report_output_path,
)
from tests.half_year_fixtures import (
    FIXTURES_BY_NAME,
    build_half_year_fixture_kb,
)


# ── helpers ──────────────────────────────────────────────────────────


_FORBIDDEN_ACTION_WORDS = (
    "买入", "卖出", "加仓", "减仓", "强烈推荐", "BUY", "SELL",
    "立即买入", "全仓", "止损",
)


def _assert_no_strong_action_words(text: str) -> None:
    for forbidden in _FORBIDDEN_ACTION_WORDS:
        assert forbidden not in text, f"text contains forbidden word: {forbidden}"


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
    symbol: str = "000977",
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
    symbol: str = "000977",
    name: str = "测试公司",
) -> ResearchClaimItem:
    return ResearchClaimItem(
        claim_type=claim_type,
        text=text,
        origin_field=origin_field,
        symbol=symbol,
        name=name,
        source_path=rel_path,
        report_date="2026-02-15",
        stale_status=STALE_FRESH,
        confidence="medium",
    )


def _make_opinion_index(
    *,
    research_claims: List[ResearchClaimItem] | None = None,
    forecast_items: List[ResearchClaimItem] | None = None,
    risk_items: List[ResearchClaimItem] | None = None,
    pages: List[ResearchFactOpinionPage] | None = None,
    symbol: str = "000977",
    name: str = "测试公司",
) -> ResearchFactOpinionIndexResult:
    if pages is None:
        page = ResearchFactOpinionPage(
            rel_path="wiki/investment/test-research.md",
            title="测试研报",
            symbols=[f"{symbol}.SZ {name}"],
            symbol=symbol,
            name=name,
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
) -> _FactIndex:
    return _FactIndex(
        metrics=metrics or {},
        segments=segments or [],
        risks=risks or [],
        period=period,
        data_status=data_status,
        source_path=source_path,
    )


# ── 单元：方向分类 ───────────────────────────────────────────────────


class TestClassifyDirection:
    def test_positive_keywords(self) -> None:
        assert _classify_direction("营收高增") == DIR_POSITIVE
        assert _classify_direction("放量增长") == DIR_POSITIVE
        assert _classify_direction("强劲爆发") == DIR_POSITIVE

    def test_negative_keywords(self) -> None:
        assert _classify_direction("储能营收下滑") == DIR_NEGATIVE
        assert _classify_direction("业务萎缩") == DIR_NEGATIVE
        assert _classify_direction("大幅下降") == DIR_NEGATIVE

    def test_positive_pct_overrides_keywords(self) -> None:
        # "+30%" 覆盖"下降"关键词——但这不会同时出现，测试 pct 优先。
        assert _classify_direction("营收 +30%") == DIR_POSITIVE

    def test_negative_pct(self) -> None:
        assert _classify_direction("营收 -20%") == DIR_NEGATIVE

    def test_neutral_no_signal(self) -> None:
        assert _classify_direction("公司发布半年报") == DIR_NEUTRAL

    def test_empty(self) -> None:
        assert _classify_direction("") == DIR_NEUTRAL

    def test_mixed_pos_neg_keywords_defaults_neutral(self) -> None:
        # 同时含正负关键词 → neutral。
        assert _classify_direction("增长但下滑") == DIR_NEUTRAL


class TestParsePct:
    def test_positive(self) -> None:
        assert _parse_pct("营收 +30.1% YoY") == pytest.approx(30.1)

    def test_negative(self) -> None:
        assert _parse_pct("储能营收 -20.0% YoY") == pytest.approx(-20.0)

    def test_pp(self) -> None:
        assert _parse_pct("毛利率 +1.2pp") == pytest.approx(1.2)

    def test_no_match(self) -> None:
        assert _parse_pct("公司增长强劲") is None

    def test_empty(self) -> None:
        assert _parse_pct("") is None


class TestMatchOpinionToMetric:
    def test_revenue(self) -> None:
        assert _match_opinion_to_metric("营收将爆发") == "revenue"

    def test_net_profit(self) -> None:
        assert _match_opinion_to_metric("归母净利高增") == "net_profit"

    def test_gross_margin(self) -> None:
        assert _match_opinion_to_metric("毛利率提升") == "gross_margin"

    def test_cash_flow(self) -> None:
        assert _match_opinion_to_metric("经营现金流改善") == "operating_cash_flow"

    def test_segment(self) -> None:
        assert _match_opinion_to_metric("储能业务将爆发") == "segment"

    def test_other(self) -> None:
        assert _match_opinion_to_metric("公司治理优秀") == "other"


# ── 单元：事实索引构建 ───────────────────────────────────────────────


class TestBuildFactIndex:
    def test_picks_fresh_page(self) -> None:
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    period="2024H1",
                    data_status=DATA_STALE,
                    facts=[_make_metric("revenue", "营收", "营收 100亿 (+10%)")],
                ),
                _make_facts_page(
                    period="2025H1",
                    data_status=DATA_FRESH,
                    facts=[_make_metric("revenue", "营收", "营收 200亿 (+30%)")],
                ),
            ]
        )
        idx = _build_fact_index(facts)
        assert idx.data_status == DATA_FRESH
        assert idx.period == "2025H1"
        assert "revenue" in idx.metrics

    def test_falls_back_to_stale(self) -> None:
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    period="2024H1",
                    data_status=DATA_STALE,
                    facts=[_make_metric("revenue", "营收", "营收 100亿 (+10%)")],
                ),
            ]
        )
        idx = _build_fact_index(facts)
        assert idx.data_status == DATA_STALE
        assert idx.period == "2024H1"

    def test_opinion_only_excluded(self) -> None:
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    data_status=DATA_OPINION_ONLY,
                    facts=[_make_metric("revenue", "营收", "营收 100亿 (+10%)")],
                ),
            ]
        )
        idx = _build_fact_index(facts)
        assert idx.data_status is None
        assert idx.metrics == {}

    def test_empty_pages(self) -> None:
        facts = _make_facts_result(pages=[])
        idx = _build_fact_index(facts)
        assert idx.data_status is None

    def test_not_a_query_result(self) -> None:
        idx = _build_fact_index("not a result")  # type: ignore[arg-type]
        assert idx.data_status is None


# ── 单元：财务指标观点比对 ───────────────────────────────────────────


class TestCheckFinancialMetricOpinion:
    def test_contradicted_direction(self) -> None:
        """观点正向但事实负向 → contradicted。"""
        claim = _make_claim("储能业务将爆发式增长，营收 +60%")
        fact_idx = _make_fact_idx(
            metrics={
                "revenue": _make_metric(
                    "revenue", "营收", "营收 120亿 (-20.0% YoY)", "120亿", "-20.0%"
                )
            }
        )
        flag = _check_financial_metric_opinion(claim, "p.md", "revenue", fact_idx)
        assert flag.check_status == STATUS_CONTRADICTED
        assert flag.evidence_level == EVIDENCE_STRONG
        assert flag.opinion_direction == DIR_POSITIVE
        assert flag.fact_direction == DIR_NEGATIVE

    def test_supported_same_direction(self) -> None:
        """观点正向且事实同向 → supported。"""
        claim = _make_claim("营收将增长")
        fact_idx = _make_fact_idx(
            metrics={
                "revenue": _make_metric(
                    "revenue", "营收", "营收 150亿 (+30% YoY)", "150亿", "+30%"
                )
            }
        )
        flag = _check_financial_metric_opinion(claim, "p.md", "revenue", fact_idx)
        assert flag.check_status == STATUS_SUPPORTED
        assert flag.evidence_level == EVIDENCE_MODERATE

    def test_weakened_low_amplitude(self) -> None:
        """观点预期 +60% 但事实只有 +20% → weakened（20 < 60×0.5=30）。"""
        claim = _make_claim("营收将爆发式增长 +60%")
        fact_idx = _make_fact_idx(
            metrics={
                "revenue": _make_metric(
                    "revenue", "营收", "营收 150亿 (+20% YoY)", "150亿", "+20%"
                )
            }
        )
        flag = _check_financial_metric_opinion(claim, "p.md", "revenue", fact_idx)
        assert flag.check_status == STATUS_WEAKENED

    def test_supported_high_amplitude(self) -> None:
        """观点预期 +30% 事实 +50%（50 >= 30×0.5=15）→ supported。"""
        claim = _make_claim("营收将增长 +30%")
        fact_idx = _make_fact_idx(
            metrics={
                "revenue": _make_metric(
                    "revenue", "营收", "营收 150亿 (+50%)", "150亿", "+50%"
                )
            }
        )
        flag = _check_financial_metric_opinion(claim, "p.md", "revenue", fact_idx)
        assert flag.check_status == STATUS_SUPPORTED

    def test_insufficient_no_metric(self) -> None:
        """观点讨论 revenue 但事实无 revenue → insufficient_data。"""
        claim = _make_claim("营收将爆发")
        fact_idx = _make_fact_idx(metrics={})
        flag = _check_financial_metric_opinion(claim, "p.md", "revenue", fact_idx)
        assert flag.check_status == STATUS_INSUFFICIENT_DATA

    def test_stale_fact_downgrades_evidence(self) -> None:
        """事实页 stale → contradicted 但 evidence_level=weak。"""
        claim = _make_claim("储能营收爆发 +60%")
        fact_idx = _make_fact_idx(
            metrics={
                "revenue": _make_metric(
                    "revenue", "营收", "营收 120亿 (-20% YoY)", "120亿", "-20%"
                )
            },
            data_status=DATA_STALE,
        )
        flag = _check_financial_metric_opinion(claim, "p.md", "revenue", fact_idx)
        assert flag.check_status == STATUS_CONTRADICTED
        assert flag.evidence_level == EVIDENCE_WEAK


# ── 单元：分业务观点比对 ─────────────────────────────────────────────


class TestCheckSegmentOpinion:
    def test_contradicted_segment(self) -> None:
        """观点说储能爆发，事实说储能下滑 → contradicted。"""
        claim = _make_claim("储能业务将爆发式增长")
        fact_idx = _make_fact_idx(segments=["储能出货同比下滑"])
        flag = _check_segment_opinion(claim, "p.md", fact_idx)
        assert flag.check_status == STATUS_CONTRADICTED
        assert flag.metric_key == "segment"

    def test_supported_segment(self) -> None:
        """观点说 AI服务器 放量，事实说 AI服务器增长 → supported。"""
        claim = _make_claim("AI服务器业务将放量增长")
        fact_idx = _make_fact_idx(segments=["AI服务器营收占比提升，大幅增长"])
        flag = _check_segment_opinion(claim, "p.md", fact_idx)
        assert flag.check_status == STATUS_SUPPORTED

    def test_insufficient_segment_not_found(self) -> None:
        """观点说储能但事实无储能 → insufficient_data。"""
        claim = _make_claim("储能业务将爆发")
        fact_idx = _make_fact_idx(segments=["AI服务器营收增长"])
        flag = _check_segment_opinion(claim, "p.md", fact_idx)
        assert flag.check_status == STATUS_INSUFFICIENT_DATA

    def test_segment_matched_in_facts_raw(self) -> None:
        """分业务事实列表无，但 financial_facts raw 含 topic → 匹配。"""
        claim = _make_claim("储能业务将爆发增长")
        fact_idx = _make_fact_idx(
            metrics={
                "revenue": _make_metric(
                    "revenue", "储能系统营收", "储能系统营收 120亿 (-20%)"
                )
            }
        )
        flag = _check_segment_opinion(claim, "p.md", fact_idx)
        assert flag.check_status == STATUS_CONTRADICTED


# ── 单元：风险观点比对 ───────────────────────────────────────────────


class TestCheckRiskOpinion:
    def test_supported_risk_confirmed(self) -> None:
        """风险观点在事实风险列表中复现 → supported。"""
        claim = _make_claim("客户集中度风险较高", claim_type=CLAIM_RISK)
        fact_idx = _make_fact_idx(risks=["客户集中度", "原材料价格"])
        flag = _check_risk_opinion(claim, "p.md", fact_idx)
        assert flag.check_status == STATUS_SUPPORTED
        assert flag.metric_key == "risk"

    def test_insufficient_risk_not_listed(self) -> None:
        """风险观点未在事实风险列表 → insufficient_data（不轻易说打脸）。"""
        claim = _make_claim("汇率波动风险", claim_type=CLAIM_RISK)
        fact_idx = _make_fact_idx(risks=["客户集中度"])
        flag = _check_risk_opinion(claim, "p.md", fact_idx)
        assert flag.check_status == STATUS_INSUFFICIENT_DATA


# ── 单元：聚合状态 ───────────────────────────────────────────────────


class TestAggregateThesisStatus:
    def test_contradicted_dominates(self) -> None:
        flags = [
            ThesisCheckFlag(metric_key="revenue", opinion_text="a", fact_text="b", check_status=STATUS_SUPPORTED),
            ThesisCheckFlag(metric_key="segment", opinion_text="c", fact_text="d", check_status=STATUS_CONTRADICTED),
        ]
        assert _aggregate_thesis_status(flags) == STATUS_CONTRADICTED

    def test_weakened_over_supported(self) -> None:
        flags = [
            ThesisCheckFlag(metric_key="revenue", opinion_text="a", fact_text="b", check_status=STATUS_SUPPORTED),
            ThesisCheckFlag(metric_key="segment", opinion_text="c", fact_text="d", check_status=STATUS_WEAKENED),
        ]
        assert _aggregate_thesis_status(flags) == STATUS_WEAKENED

    def test_all_insufficient(self) -> None:
        flags = [
            ThesisCheckFlag(metric_key="revenue", opinion_text="a", fact_text="b", check_status=STATUS_INSUFFICIENT_DATA),
        ]
        assert _aggregate_thesis_status(flags) == STATUS_INSUFFICIENT_DATA

    def test_empty(self) -> None:
        assert _aggregate_thesis_status([]) == STATUS_INSUFFICIENT_DATA

    def test_supported_only(self) -> None:
        flags = [
            ThesisCheckFlag(metric_key="revenue", opinion_text="a", fact_text="b", check_status=STATUS_SUPPORTED),
        ]
        assert _aggregate_thesis_status(flags) == STATUS_SUPPORTED


# ── 集成：四类 fixture 场景 ─────────────────────────────────────────


class TestThesisFactCheckFourScenarios:
    """覆盖任务验收要求的四类 fixture 场景：支持/削弱/打脸/缺数据。"""

    def test_contradicted_scenario(self) -> None:
        """场景：旧观点说储能爆发，事实显示储能下滑 → contradicted。"""
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    period="2025H1",
                    data_status=DATA_FRESH,
                    facts=[
                        _make_metric(
                            "revenue", "储能系统营收",
                            "储能系统营收 120亿 (-20.0% YoY)", "120亿", "-20.0%"
                        ),
                    ],
                    segments=["储能出货同比下滑"],
                    risks=["储能增速不及预期"],
                )
            ],
            symbol="300750",
            name="宁德时代",
        )
        opinions = _make_opinion_index(
            research_claims=[
                _make_claim("储能业务下半年将迎爆发式增长", symbol="300750", name="宁德时代"),
            ],
            risk_items=[
                _make_claim("储能增速不及预期", claim_type=CLAIM_RISK, symbol="300750", name="宁德时代"),
            ],
            symbol="300750",
            name="宁德时代",
        )
        result = check_thesis_against_facts(opinions, facts)

        assert result.thesis_check_status == STATUS_CONTRADICTED
        assert len(result.contradiction_flags) >= 1
        assert result.checked_opinion_count >= 1
        assert result.needs_tree_work_review is True
        assert result.evidence_level in (EVIDENCE_STRONG, EVIDENCE_MODERATE)

    def test_supported_scenario(self) -> None:
        """场景：观点说营收高增，事实营收 +60% → supported。"""
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    period="2025H1",
                    data_status=DATA_FRESH,
                    facts=[
                        _make_metric(
                            "revenue", "营收",
                            "营收 420.4亿 (+60.0% YoY)", "420.4亿", "+60.0%"
                        ),
                    ],
                    segments=["AI服务器营收占比提升"],
                )
            ],
            symbol="000977",
            name="浪潮信息",
        )
        opinions = _make_opinion_index(
            research_claims=[
                _make_claim("AI服务器整机放量，营收高增", symbol="000977", name="浪潮信息"),
            ],
            symbol="000977",
            name="浪潮信息",
        )
        result = check_thesis_against_facts(opinions, facts)

        assert result.thesis_check_status == STATUS_SUPPORTED
        assert len(result.supported_flags) >= 1
        assert len(result.contradiction_flags) == 0
        assert result.needs_tree_work_review is False

    def test_weakened_scenario(self) -> None:
        """场景：观点预期 +60%，事实只有 +20% → weakened。"""
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    period="2025H1",
                    data_status=DATA_FRESH,
                    facts=[
                        _make_metric(
                            "revenue", "营收",
                            "营收 120亿 (+20% YoY)", "120亿", "+20%"
                        ),
                    ],
                )
            ],
            symbol="000977",
            name="测试公司",
        )
        opinions = _make_opinion_index(
            research_claims=[
                _make_claim("营收将爆发式增长 +60%", symbol="000977", name="测试公司"),
            ],
            symbol="000977",
            name="测试公司",
        )
        result = check_thesis_against_facts(opinions, facts)

        assert result.thesis_check_status == STATUS_WEAKENED
        assert len(result.weakened_flags) >= 1
        # 单条 weakened 不触发 review（需 ≥2）。
        assert result.needs_tree_work_review is False

    def test_insufficient_data_no_facts(self) -> None:
        """场景：无半年报事实 → insufficient_data。"""
        facts = _make_facts_result(
            pages=[],
            status=STATUS_NORMAL_NO_DATA,
        )
        opinions = _make_opinion_index(
            research_claims=[_make_claim("营收将爆发")]
        )
        result = check_thesis_against_facts(opinions, facts)

        assert result.thesis_check_status == STATUS_INSUFFICIENT_DATA
        assert result.checked_opinion_count == 0
        assert result.fact_period is None
        assert result.needs_tree_work_review is False

    def test_insufficient_data_opinion_only_facts(self) -> None:
        """场景：事实页 opinion_only → 不可用于反证 → insufficient_data。"""
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    data_status=DATA_OPINION_ONLY,
                    source_type=["broker_report"],
                    facts=[_make_metric("revenue", "营收", "营收 100亿 (+20%)")],
                )
            ],
            symbol="000977",
        )
        opinions = _make_opinion_index(
            research_claims=[_make_claim("营收将爆发")]
        )
        result = check_thesis_against_facts(opinions, facts)

        assert result.thesis_check_status == STATUS_INSUFFICIENT_DATA


# ── 集成：fixture KB 回放 ────────────────────────────────────────────


class TestFixtureKBReplay:
    """用 KB-013 fixture 样本做真实知识库回放。"""

    def test_weakened_old_opinion_kb(self, tmp_path: Path) -> None:
        """WEAKENED_OLD_OPINION_PAGE fixture：旧观点被事实削弱/打脸。"""
        from tradingagents.dataflows.half_year_facts_provider import query_half_year_facts
        from tradingagents.dataflows.research_fact_opinion_index import (
            build_research_fact_opinion_index,
        )

        root = build_half_year_fixture_kb(tmp_path, include=["weakened_old_opinion"])

        facts = query_half_year_facts(str(root), symbol="300750")
        assert facts.status == STATUS_HAS_DATA

        opinions = build_research_fact_opinion_index(str(root), symbol="300750")
        assert opinions.status == STATUS_HAS_DATA

        result = check_thesis_against_facts(opinions, facts)

        # management_commentary 含旧观点「储能业务下半年将迎爆发式增长」，
        # 但 financial_facts 显示储能 -20% → 至少有 contradicted 或 weakened。
        assert result.thesis_check_status in (
            STATUS_CONTRADICTED,
            STATUS_WEAKENED,
        )
        assert result.total_opinion_count >= 1
        # 风险「储能增速不及预期」在 fact risk_factors 中复现 → supported。
        assert any(
            f.metric_key == "risk" and f.check_status == STATUS_SUPPORTED
            for f in result.flags
        )

    def test_qualified_half_year_no_contradiction(self, tmp_path: Path) -> None:
        """QUALIFIED_HALF_YEAR_PAGE fixture：观点与事实一致 → supported 或 insufficient。"""
        from tradingagents.dataflows.half_year_facts_provider import query_half_year_facts
        from tradingagents.dataflows.research_fact_opinion_index import (
            build_research_fact_opinion_index,
        )

        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])

        facts = query_half_year_facts(str(root), symbol="000977")
        opinions = build_research_fact_opinion_index(str(root), symbol="000977")

        result = check_thesis_against_facts(opinions, facts)

        # 不应出现 contradicted（观点与事实一致）。
        assert STATUS_CONTRADICTED not in [
            f.check_status for f in result.flags
        ]
        assert result.thesis_check_status in (
            STATUS_SUPPORTED,
            STATUS_INSUFFICIENT_DATA,
        )

    def test_non_financial_control_no_facts(self, tmp_path: Path) -> None:
        """NON_FINANCIAL_CONTROL_PAGE：非财报页 → facts 无半年报 → insufficient。"""
        from tradingagents.dataflows.half_year_facts_provider import query_half_year_facts
        from tradingagents.dataflows.research_fact_opinion_index import (
            build_research_fact_opinion_index,
        )

        root = build_half_year_fixture_kb(
            tmp_path, include=["non_financial_control"]
        )

        facts = query_half_year_facts(str(root), symbol="000001")
        opinions = build_research_fact_opinion_index(str(root), symbol="000001")

        result = check_thesis_against_facts(opinions, facts)

        assert result.thesis_check_status == STATUS_INSUFFICIENT_DATA


# ── 约束：不输出强买卖词 ─────────────────────────────────────────────


class TestNoStrongActionWords:
    def test_report_no_strong_action_words(self) -> None:
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    facts=[
                        _make_metric(
                            "revenue", "营收", "营收 120亿 (-20%)", "120亿", "-20%"
                        )
                    ],
                )
            ]
        )
        opinions = _make_opinion_index(
            research_claims=[_make_claim("营收将爆发增长 +60%")],
            risk_items=[_make_claim("客户集中度", claim_type=CLAIM_RISK)],
        )
        result = check_thesis_against_facts(opinions, facts)
        report = render_thesis_fact_check_report(result)
        inline = render_thesis_check_inline(result)
        _assert_no_strong_action_words(report)
        _assert_no_strong_action_words(inline)

    def test_summary_no_strong_action_words(self) -> None:
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    facts=[
                        _make_metric(
                            "revenue", "营收", "营收 120亿 (-20%)", "120亿", "-20%"
                        )
                    ],
                )
            ]
        )
        opinions = _make_opinion_index(
            research_claims=[_make_claim("营收将爆发增长 +60%")]
        )
        result = check_thesis_against_facts(opinions, facts)
        for s in result.summary:
            _assert_no_strong_action_words(s)


# ── 约束：不把单一指标变化扩大成逻辑破坏 ───────────────────────────


class TestNoSingleMetricBlowUp:
    def test_single_metric_evidence_level(self) -> None:
        """单条 contradicted flag 有 evidence_level 字段，不会全盘标破坏。"""
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    facts=[
                        _make_metric(
                            "revenue", "营收", "营收 120亿 (-20%)", "120亿", "-20%"
                        ),
                        _make_metric(
                            "net_profit", "归母净利", "归母净利 15亿 (+30%)", "15亿", "+30%"
                        ),
                    ],
                )
            ]
        )
        opinions = _make_opinion_index(
            research_claims=[
                _make_claim("营收将爆发 +60%"),  # contradicted
                _make_claim("归母净利高增"),       # supported
            ]
        )
        result = check_thesis_against_facts(opinions, facts)

        # 每条 flag 都有 evidence_level。
        for f in result.flags:
            assert f.evidence_level in (
                EVIDENCE_STRONG,
                EVIDENCE_MODERATE,
                EVIDENCE_WEAK,
                EVIDENCE_NONE,
            )
        # 整体 contradicted（优先级最高），但有 supported flag 存在。
        assert result.thesis_check_status == STATUS_CONTRADICTED
        assert len(result.supported_flags) >= 1


# ── KB-007 research attention 联动 ──────────────────────────────────


class TestResearchAttentionLinkage:
    def test_priority_reminder_high_attention_contradicted(self) -> None:
        """高关注度 + contradicted → priority_reminder=True。"""
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    facts=[
                        _make_metric(
                            "revenue", "营收", "营收 120亿 (-20%)", "120亿", "-20%"
                        )
                    ],
                )
            ]
        )
        opinions = _make_opinion_index(
            research_claims=[_make_claim("营收将爆发 +60%")]
        )
        result = check_thesis_against_facts(
            opinions, facts, research_attention_score=3.5
        )
        assert result.thesis_check_status == STATUS_CONTRADICTED
        assert result.priority_reminder is True

    def test_no_priority_low_attention(self) -> None:
        """低关注度不触发 priority_reminder。"""
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    facts=[
                        _make_metric(
                            "revenue", "营收", "营收 120亿 (-20%)", "120亿", "-20%"
                        )
                    ],
                )
            ]
        )
        opinions = _make_opinion_index(
            research_claims=[_make_claim("营收将爆发 +60%")]
        )
        result = check_thesis_against_facts(
            opinions, facts, research_attention_score=0.5
        )
        assert result.priority_reminder is False

    def test_no_priority_supported_status(self) -> None:
        """高关注度但 supported → 不触发 priority_reminder。"""
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    facts=[
                        _make_metric(
                            "revenue", "营收", "营收 150亿 (+50%)", "150亿", "+50%"
                        )
                    ],
                )
            ]
        )
        opinions = _make_opinion_index(
            research_claims=[_make_claim("营收将增长")]
        )
        result = check_thesis_against_facts(
            opinions, facts, research_attention_score=5.0
        )
        assert result.thesis_check_status == STATUS_SUPPORTED
        assert result.priority_reminder is False

    def test_attention_none_no_priority(self) -> None:
        """不传 attention score → priority_reminder=False。"""
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    facts=[
                        _make_metric(
                            "revenue", "营收", "营收 120亿 (-20%)", "120亿", "-20%"
                        )
                    ],
                )
            ]
        )
        opinions = _make_opinion_index(
            research_claims=[_make_claim("营收将爆发 +60%")]
        )
        result = check_thesis_against_facts(opinions, facts)
        assert result.priority_reminder is False
        assert result.research_attention_score is None


# ── needs_tree_work_review 触发条件 ──────────────────────────────────


class TestNeedsTreeWorkReview:
    def test_review_on_contradicted(self) -> None:
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    facts=[
                        _make_metric(
                            "revenue", "营收", "营收 120亿 (-20%)", "120亿", "-20%"
                        )
                    ],
                )
            ]
        )
        opinions = _make_opinion_index(
            research_claims=[_make_claim("营收将爆发 +60%")]
        )
        result = check_thesis_against_facts(opinions, facts)
        assert result.needs_tree_work_review is True

    def test_review_on_two_weakened(self) -> None:
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    facts=[
                        _make_metric(
                            "revenue", "营收", "营收 120亿 (+20%)", "120亿", "+20%"
                        ),
                        _make_metric(
                            "net_profit", "归母净利", "归母净利 10亿 (+10%)", "10亿", "+10%"
                        ),
                    ],
                )
            ]
        )
        opinions = _make_opinion_index(
            research_claims=[
                _make_claim("营收爆发 +60%"),  # weakened
                _make_claim("归母净利爆发 +60%"),  # weakened
            ]
        )
        result = check_thesis_against_facts(opinions, facts)
        assert len(result.weakened_flags) >= 2
        assert result.needs_tree_work_review is True

    def test_no_review_single_weakened(self) -> None:
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    facts=[
                        _make_metric(
                            "revenue", "营收", "营收 120亿 (+20%)", "120亿", "+20%"
                        ),
                    ],
                )
            ]
        )
        opinions = _make_opinion_index(
            research_claims=[_make_claim("营收爆发 +60%")]  # weakened
        )
        result = check_thesis_against_facts(opinions, facts)
        assert result.needs_tree_work_review is False


# ── 失败路径 ─────────────────────────────────────────────────────────


class TestFailurePaths:
    def test_no_opinions(self) -> None:
        """无观点 → insufficient_data + errors 记录。"""
        facts = _make_facts_result(
            pages=[_make_facts_page(facts=[_make_metric("revenue", "营收", "营收 100亿")])]
        )
        opinions = _make_opinion_index()
        result = check_thesis_against_facts(opinions, facts)
        assert result.thesis_check_status == STATUS_INSUFFICIENT_DATA
        assert result.total_opinion_count == 0
        assert any("无可用观点" in e for e in result.errors)

    def test_failed_facts_query(self) -> None:
        """facts 查询 FAILED → insufficient_data。"""
        facts = HalfYearFactsQueryResult(
            status=STATUS_FAILED,
            symbol="000977",
        )
        opinions = _make_opinion_index(
            research_claims=[_make_claim("营收将爆发")]
        )
        result = check_thesis_against_facts(opinions, facts)
        assert result.thesis_check_status == STATUS_INSUFFICIENT_DATA
        assert result.fact_period is None

    def test_symbol_inferred_from_facts(self) -> None:
        facts = _make_facts_result(
            pages=[_make_facts_page()],
            symbol="600519",
            name="贵州茅台",
        )
        opinions = _make_opinion_index()
        result = check_thesis_against_facts(opinions, facts)
        assert result.symbol == "600519"
        assert result.name == "贵州茅台"

    def test_symbol_inferred_from_opinions(self) -> None:
        facts = HalfYearFactsQueryResult(status=STATUS_NORMAL_NO_DATA)
        opinions = _make_opinion_index(symbol="000977", name="浪潮信息")
        result = check_thesis_against_facts(opinions, facts)
        assert result.symbol == "000977"


# ── 序列化与渲染 ─────────────────────────────────────────────────────


class TestSerializationAndRendering:
    def test_to_dict_json_serializable(self) -> None:
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    facts=[
                        _make_metric(
                            "revenue", "营收", "营收 120亿 (-20%)", "120亿", "-20%"
                        )
                    ],
                )
            ]
        )
        opinions = _make_opinion_index(
            research_claims=[_make_claim("营收将爆发 +60%")]
        )
        result = check_thesis_against_facts(opinions, facts)
        d = result.to_dict()
        # 确保可 JSON 序列化。
        json_str = json.dumps(d, ensure_ascii=False)
        restored = json.loads(json_str)
        assert restored["thesis_check_status"] == STATUS_CONTRADICTED
        assert len(restored["flags"]) >= 1

    def test_flag_to_dict(self) -> None:
        flag = ThesisCheckFlag(
            metric_key="revenue",
            opinion_text="营收爆发",
            fact_text="营收 -20%",
            check_status=STATUS_CONTRADICTED,
            evidence_level=EVIDENCE_STRONG,
        )
        d = flag.to_dict()
        assert d["metric_key"] == "revenue"
        assert d["check_status"] == STATUS_CONTRADICTED
        json.dumps(d, ensure_ascii=False)

    def test_render_report_full(self) -> None:
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    facts=[
                        _make_metric(
                            "revenue", "营收", "营收 120亿 (-20%)", "120亿", "-20%"
                        )
                    ],
                )
            ]
        )
        opinions = _make_opinion_index(
            research_claims=[_make_claim("营收将爆发 +60%")]
        )
        result = check_thesis_against_facts(opinions, facts)
        report = render_thesis_fact_check_report(result)
        assert TASK_CODE in report
        assert "contradicted" in report
        assert "营收" in report

    def test_render_inline_contradicted(self) -> None:
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    facts=[
                        _make_metric(
                            "revenue", "营收", "营收 120亿 (-20%)", "120亿", "-20%"
                        )
                    ],
                )
            ]
        )
        opinions = _make_opinion_index(
            research_claims=[_make_claim("营收将爆发 +60%")]
        )
        result = check_thesis_against_facts(opinions, facts)
        inline = render_thesis_check_inline(result)
        assert TASK_CODE in inline
        assert "打脸" in inline

    def test_render_inline_insufficient_empty(self) -> None:
        facts = _make_facts_result(pages=[])
        opinions = _make_opinion_index(
            research_claims=[_make_claim("营收将爆发")]
        )
        result = check_thesis_against_facts(opinions, facts)
        assert render_thesis_check_inline(result) == ""

    def test_render_inline_supported(self) -> None:
        facts = _make_facts_result(
            pages=[
                _make_facts_page(
                    facts=[
                        _make_metric(
                            "revenue", "营收", "营收 150亿 (+50%)", "150亿", "+50%"
                        )
                    ],
                )
            ]
        )
        opinions = _make_opinion_index(
            research_claims=[_make_claim("营收将增长")]
        )
        result = check_thesis_against_facts(opinions, facts)
        inline = render_thesis_check_inline(result)
        assert "支持" in inline

    def test_render_report_insufficient(self) -> None:
        facts = _make_facts_result(pages=[])
        opinions = _make_opinion_index(
            research_claims=[_make_claim("营收将爆发")]
        )
        result = check_thesis_against_facts(opinions, facts)
        report = render_thesis_fact_check_report(result)
        assert "insufficient_data" in report


# ── 辅助函数 ─────────────────────────────────────────────────────────


class TestUtilities:
    def test_suggest_report_output_path(self) -> None:
        path = suggest_report_output_path()
        assert "thesis_fact_check-" in path
        assert path.endswith(".md")

    def test_all_thesis_statuses_complete(self) -> None:
        assert set(ALL_THESIS_STATUSES) == {
            STATUS_SUPPORTED,
            STATUS_WEAKENED,
            STATUS_CONTRADICTED,
            STATUS_INSUFFICIENT_DATA,
        }

    def test_task_code(self) -> None:
        assert TASK_CODE == "HY-005"


# ── 观点收集 ─────────────────────────────────────────────────────────


class TestCollectOpinions:
    def test_collect_research_claims_and_forecasts(self) -> None:
        idx = _make_opinion_index(
            research_claims=[_make_claim("观点1")],
            forecast_items=[_make_claim("预测1", claim_type=CLAIM_FORECAST)],
            risk_items=[_make_claim("风险1", claim_type=CLAIM_RISK)],
        )
        opinions = _collect_opinions(idx)
        assert len(opinions) == 2  # research_claims + forecast_items
        # risk 不在 opinions 中（由 _collect_risk_opinions 单独处理）。

    def test_collect_empty(self) -> None:
        idx = ResearchFactOpinionIndexResult()
        assert _collect_opinions(idx) == []

    def test_collect_not_an_index(self) -> None:
        assert _collect_opinions("not an index") == []  # type: ignore[arg-type]


# ── KB-010 cache 等价（通过 fixture 回放验证）─────────────────────────


class TestCacheEquivalence:
    def test_cache_vs_scan_same_result(self, tmp_path: Path) -> None:
        """缓存路径与全量扫描产出语义等价的反证结果。"""
        from tradingagents.dataflows.half_year_facts_provider import query_half_year_facts
        from tradingagents.dataflows.local_knowledge_cache import get_or_build_cache
        from tradingagents.dataflows.research_fact_opinion_index import (
            build_research_fact_opinion_index,
        )

        root = build_half_year_fixture_kb(tmp_path, include=["weakened_old_opinion"])
        cache = get_or_build_cache(str(root))

        facts_scan = query_half_year_facts(str(root), symbol="300750")
        facts_cache = query_half_year_facts(
            str(root), symbol="300750", cache=cache
        )

        opinions_scan = build_research_fact_opinion_index(str(root), symbol="300750")
        opinions_cache = build_research_fact_opinion_index(
            str(root), symbol="300750", cache=cache
        )

        result_scan = check_thesis_against_facts(opinions_scan, facts_scan)
        result_cache = check_thesis_against_facts(opinions_cache, facts_cache)

        assert result_scan.thesis_check_status == result_cache.thesis_check_status
        assert result_scan.total_opinion_count == result_cache.total_opinion_count
        assert len(result_scan.flags) == len(result_cache.flags)
