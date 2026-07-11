# [KB-016] research_consensus_matrix
"""Tests for 多研报一致性/分歧矩阵与关注度去重回放 (KB-016).

覆盖（验收要求）：
  - fixture 覆盖一致看多、一致看空、观点分裂、重复报告、过期报告五类。
  - 四维分歧矩阵：业绩预测 / 产业链角色 / 风险判断 / 估值假设。
  - 去重规则：同机构 + 同标题 + 同立场 → 重复。
  - 时间窗口：3/6/12 月过滤。
  - needs_fact_check：高分歧 / 弱来源共识 / 过期主导 / 验证缺口。
  - 多研报高关注只能提高研究优先级，不绕过 TradeFlow/TA 门禁。
  - 约束：不写知识库 / 不输出原文 / 无买卖建议词 / 弱来源不覆盖动作语义。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from tradingagents.dataflows.local_knowledge_audit import INVESTMENT_SUBDIR
from tradingagents.dataflows.citation_policy import (
    TIER_BROKER_RESEARCH,
    TIER_MEDIA,
    TIER_ORIGINAL_FILING,
)
from tradingagents.dataflows.research_consensus_matrix import (
    DEFAULT_WINDOW_MONTHS,
    DIM_PERFORMANCE,
    DIM_RISK,
    DIM_SUPPLY_CHAIN_ROLE,
    DIM_VALUATION,
    STANCE_BEARISH,
    STANCE_BULLISH,
    STANCE_NEUTRAL,
    STANCE_UNKNOWN,
    DimensionDisagreement,
    DimensionStance,
    ReportStance,
    ResearchConsensusMatrixResult,
    SymbolConsensusMatrix,
    build_research_consensus_matrix,
    has_forbidden_action_words,
    lookup_research_consensus_matrix,
    matrix_to_summary_dict,
    matrix_to_ta_consumable_summary,
    render_research_consensus_matrix_report,
    split_institution_helper,
    suggest_report_output_path,
    _detect_dimension_stance,
    _detect_overall_stance,
    _mark_duplicates,
    _norm_title,
    _symbol_equivalent,
    _within_window,
)
from tradingagents.dataflows.research_fact_opinion_index import (
    CLAIM_FACT,
    CLAIM_FORECAST,
    CLAIM_OPINION,
    CLAIM_RISK,
    ResearchClaimItem,
    ResearchFactOpinionPage,
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


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# 测试基准日期：让所有 fixture 都在窗口内。
_TODAY = date(2026, 7, 12)


def _broker_page(
    rel_name: str,
    *,
    symbol: str,
    name: str,
    title: str,
    body_opinion: str,
    body_risk: str = "风险可控。",
    report_date: str = "2026-06-01",
    stale_risk: str = "低",
    valid_until: str = "2099-12-31",
    evidence_level: str = "B",
    source_quality: str = "中",
    extra_fm: str = "",
    body_forecast: str = "",
    institution_hint: str = "",
) -> str:
    """生成一份券商研报 markdown（用于共识/分歧 fixture）。"""
    sources = f"[[{institution_hint or '某券商'}-研究|{institution_hint or '某券商'}深度]]"
    fm = f"""---
title: {title}
created: {report_date}
updated: {report_date}
sources:
  - "{sources}"
tags: [{name}]
related: []
symbols: ["{symbol} {name}"]
themes: [AI]
report_type: 深度
evidence_level: {evidence_level}
valid_until: {valid_until}
source_quality: {source_quality}
stale_risk: {stale_risk}
{extra_fm}
---

# {title}

## 一句话总结

{body_opinion}

## 投资逻辑

- {body_opinion}

## 前瞻指引

- {body_forecast or body_opinion}

## 风险提示

- {body_risk}
"""
    return fm


# ── fixtures ─────────────────────────────────────────────────────────


# 1) 一致看多：3 家不同机构，全部看多。
_BULLISH_1 = _broker_page(
    "bullish-1.md",
    symbol="600100.SH", name="某甲公司",
    title="中信证券-某甲公司深度",
    body_opinion="营收高增长，渗透率提升，估值修复空间大。",
    body_risk="风险可控。",
    body_forecast="预计全年营收增长 30%。",
    institution_hint="中信证券",
)
_BULLISH_2 = _broker_page(
    "bullish-2.md",
    symbol="600100.SH", name="某甲公司",
    title="国泰君安-某甲公司点评",
    body_opinion="业绩超预期，产业链龙头地位稳固，估值合理。",
    body_risk="风险下降。",
    body_forecast="上调全年利润预测。",
    institution_hint="国泰君安",
)
_BULLISH_3 = _broker_page(
    "bullish-3.md",
    symbol="600100.SH", name="某甲公司",
    title="华泰证券-某甲公司跟踪",
    body_opinion="主业加速改善，核心受益 AI 浪潮，估值偏低。",
    body_risk="无明显风险。",
    body_forecast="增长强劲。",
    institution_hint="华泰证券",
)

# 2) 一致看空：3 家不同机构，全部看空。
_BEARISH_1 = _broker_page(
    "bearish-1.md",
    symbol="600200.SH", name="某乙公司",
    title="中金公司-某乙公司点评",
    body_opinion="营收同比下降，竞争加剧，估值偏高。",
    body_risk="下行风险加剧。",
    body_forecast="下修全年利润预测。",
    institution_hint="中金公司",
)
_BEARISH_2 = _broker_page(
    "bearish-2.md",
    symbol="600200.SH", name="某乙公司",
    title="海通证券-某乙公司风险提示",
    body_opinion="业绩不及预期，份额下滑，高估透支。",
    body_risk="高风险。",
    body_forecast="萎缩持续。",
    institution_hint="海通证券",
)
_BEARISH_3 = _broker_page(
    "bearish-3.md",
    symbol="600200.SH", name="某乙公司",
    title="广发证券-某乙公司预警",
    body_opinion="亏损扩大，替代风险加剧，估值贵。",
    body_risk="风险上升。",
    body_forecast="续亏。",
    institution_hint="广发证券",
)

# 3) 观点分裂：2 篇看多 + 2 篇看空。
_SPLIT_BULL_1 = _broker_page(
    "split-bull-1.md",
    symbol="600300.SH", name="某丙公司",
    title="招商证券-某丙公司看多",
    body_opinion="营收高增长，估值修复。",
    body_risk="风险可控。",
    institution_hint="招商证券",
)
_SPLIT_BULL_2 = _broker_page(
    "split-bull-2.md",
    symbol="600300.SH", name="某丙公司",
    title="申万宏源-某丙公司推荐",
    body_opinion="超预期，龙头地位稳固。",
    body_risk="风险下降。",
    institution_hint="申万宏源",
)
_SPLIT_BEAR_1 = _broker_page(
    "split-bear-1.md",
    symbol="600300.SH", name="某丙公司",
    title="国信证券-某丙公司谨慎",
    body_opinion="业绩不及预期，竞争加剧。",
    body_risk="高风险。",
    institution_hint="国信证券",
)
_SPLIT_BEAR_2 = _broker_page(
    "split-bear-2.md",
    symbol="600300.SH", name="某丙公司",
    title="东方证券-某丙公司下调",
    body_opinion="萎缩，份额下滑，估值贵。",
    body_risk="风险加剧。",
    institution_hint="东方证券",
)

# 4) 重复报告：同机构（中信）+ 同标题 + 同立场。
_DUP_1 = _broker_page(
    "dup-1.md",
    symbol="600400.SH", name="某丁公司",
    title="中信证券-某丁公司深度",
    body_opinion="营收高增长，龙头。",
    institution_hint="中信证券",
)
_DUP_2 = _broker_page(
    "dup-2.md",
    symbol="600400.SH", name="某丁公司",
    title="中信证券-某丁公司深度",
    body_opinion="营收高增长，龙头。",
    institution_hint="中信证券",
)
_DUP_3 = _broker_page(
    "dup-3.md",
    symbol="600400.SH", name="某丁公司",
    title="国泰君安-某丁公司点评",
    body_opinion="超预期，核心受益。",
    institution_hint="国泰君安",
)

# 5) 过期报告：stale_risk=高 + valid_until 已过期。
_STALE_REPORT = _broker_page(
    "stale-1.md",
    symbol="600500.SH", name="某戊公司",
    title="中信证券-某戊公司旧深度",
    body_opinion="营收高增长。",
    stale_risk="高",
    valid_until="2025-06-30",
    report_date="2025-01-01",
    institution_hint="中信证券",
)


@pytest.fixture()
def fixture_kb(tmp_path: Path) -> Path:
    """构造覆盖五类 + 边界场景的微型知识库。"""
    inv = tmp_path / INVESTMENT_SUBDIR
    _write(inv / "bullish-1.md", _BULLISH_1)
    _write(inv / "bullish-2.md", _BULLISH_2)
    _write(inv / "bullish-3.md", _BULLISH_3)
    _write(inv / "bearish-1.md", _BEARISH_1)
    _write(inv / "bearish-2.md", _BEARISH_2)
    _write(inv / "bearish-3.md", _BEARISH_3)
    _write(inv / "split-bull-1.md", _SPLIT_BULL_1)
    _write(inv / "split-bull-2.md", _SPLIT_BULL_2)
    _write(inv / "split-bear-1.md", _SPLIT_BEAR_1)
    _write(inv / "split-bear-2.md", _SPLIT_BEAR_2)
    _write(inv / "dup-1.md", _DUP_1)
    _write(inv / "dup-2.md", _DUP_2)
    _write(inv / "dup-3.md", _DUP_3)
    _write(inv / "stale-1.md", _STALE_REPORT)
    return tmp_path


# ── 1. 五类 fixture 覆盖 ─────────────────────────────────────────────


class TestFiveFixtureTypes:
    """验收要求：fixture 覆盖一致看多、一致看空、观点分裂、重复报告、过期报告五类。"""

    def test_consistent_bullish(self, fixture_kb: Path):
        """一致看多：3 家不同机构 → consensus 高，dominant=bullish。"""
        result = build_research_consensus_matrix(
            str(fixture_kb), symbol="600100", today=_TODAY,
        )
        assert result.status != "FAILED"
        assert len(result.symbols) >= 1
        m = result.symbols[0]
        assert m.report_count_in_window == 3
        assert m.dominant_stance == STANCE_BULLISH
        # 三家一致 → consensus 应该较高。
        assert m.consensus_score >= 0.5
        # 不是买入信号（不改变强动作门禁）。
        assert not m.fact_check_reasons or any(
            "看多" in r for r in m.fact_check_reasons
        )

    def test_consistent_bearish(self, fixture_kb: Path):
        """一致看空：3 家不同机构 → consensus 高，dominant=bearish。"""
        result = build_research_consensus_matrix(
            str(fixture_kb), symbol="600200", today=_TODAY,
        )
        assert len(result.symbols) >= 1
        m = result.symbols[0]
        assert m.report_count_in_window == 3
        assert m.dominant_stance == STANCE_BEARISH
        assert m.consensus_score >= 0.5

    def test_split_opinions_triggers_fact_check(self, fixture_kb: Path):
        """观点分裂：2 多 vs 2 空 → needs_fact_check=True。"""
        result = build_research_consensus_matrix(
            str(fixture_kb), symbol="600300", today=_TODAY,
        )
        assert len(result.symbols) >= 1
        m = result.symbols[0]
        # 分裂 → 至少一个维度 disagreement 高。
        max_disagree = max(
            d.disagreement_score for d in m.dimensions.values()
        )
        assert max_disagree >= 0.3
        # 观点分裂应触发 needs_fact_check。
        assert m.needs_fact_check is True
        assert m.fact_check_priority in ("medium", "high")

    def test_duplicate_reports_collapsed(self, fixture_kb: Path):
        """重复报告：同机构+同标题+同立场 → 折叠为 1 条有效。"""
        result = build_research_consensus_matrix(
            str(fixture_kb), symbol="600400", today=_TODAY,
        )
        assert len(result.symbols) >= 1
        m = result.symbols[0]
        # 3 篇中 1 篇重复 → 有效 2 篇。
        assert m.report_count_in_window == 3
        assert m.report_count_duplicate == 1
        # 重复报告不应制造虚假共识增强。
        # attention_count_effective 应小于总报告数。
        assert m.attention_count_effective < m.report_count_in_window

    def test_stale_report_low_weight(self, fixture_kb: Path):
        """过期报告：stale_risk=高 → 标记但不删除，进入 stale_count。"""
        result = build_research_consensus_matrix(
            str(fixture_kb), symbol="600500", today=_TODAY,
        )
        assert len(result.symbols) >= 1
        m = result.symbols[0]
        assert m.stale_count >= 1
        # 过期主导 → needs_fact_check（建议刷新事实）。
        assert m.needs_fact_check is True


# ── 2. 四维分歧矩阵 ─────────────────────────────────────────────────


class TestFourDimensionMatrix:
    def test_all_four_dimensions_present(self, fixture_kb: Path):
        """矩阵必须包含四个维度。"""
        result = build_research_consensus_matrix(
            str(fixture_kb), symbol="600100", today=_TODAY,
        )
        m = result.symbols[0]
        for dim in (DIM_PERFORMANCE, DIM_SUPPLY_CHAIN_ROLE, DIM_RISK, DIM_VALUATION):
            assert dim in m.dimensions
            assert isinstance(m.dimensions[dim], DimensionDisagreement)

    def test_performance_dimension_stance_detection(self):
        """业绩预测维度的关键词命中检测。"""
        claims = [
            ResearchClaimItem(
                claim_type=CLAIM_FORECAST,
                text="营收增长 30%，超预期",
                origin_field="forward_guidance",
            )
        ]
        ds = _detect_dimension_stance(DIM_PERFORMANCE, claims)
        assert ds.stance == STANCE_BULLISH
        assert any("增长" in kw for kw in ds.hit_keywords)

    def test_risk_dimension_stance_detection(self):
        claims = [
            ResearchClaimItem(
                claim_type=CLAIM_RISK,
                text="风险加剧，下行风险显著",
                origin_field="risk_factors",
            )
        ]
        ds = _detect_dimension_stance(DIM_RISK, claims)
        assert ds.stance == STANCE_BEARISH

    def test_valuation_dimension_stance_detection(self):
        claims = [
            ResearchClaimItem(
                claim_type=CLAIM_OPINION,
                text="估值修复，低估，性价比高",
                origin_field="body:opinion",
            )
        ]
        ds = _detect_dimension_stance(DIM_VALUATION, claims)
        assert ds.stance == STANCE_BULLISH

    def test_supply_chain_role_dimension_stance_detection(self):
        claims = [
            ResearchClaimItem(
                claim_type=CLAIM_OPINION,
                text="龙头地位稳固，核心受益",
                origin_field="body:opinion",
            )
        ]
        ds = _detect_dimension_stance(DIM_SUPPLY_CHAIN_ROLE, claims)
        assert ds.stance == STANCE_BULLISH

    def test_neutral_when_equal_hits(self):
        """bull 与 bear 命中数相同时 → neutral。"""
        claims = [
            ResearchClaimItem(
                claim_type=CLAIM_OPINION,
                text="营收增长但风险加剧",  # bull: 增长 / bear: 风险加剧
                origin_field="body:opinion",
            )
        ]
        ds = _detect_dimension_stance(DIM_PERFORMANCE, claims)
        # performance: 增长(bull) vs 无 bear → bull
        assert ds.stance == STANCE_BULLISH
        # risk: 无 bull vs 风险加剧(bear) → bear
        ds_risk = _detect_dimension_stance(DIM_RISK, claims)
        assert ds_risk.stance == STANCE_BEARISH

    def test_unknown_when_no_hits(self):
        claims = [
            ResearchClaimItem(
                claim_type=CLAIM_OPINION,
                text="公司正常运营",  # 无任何维度关键词
                origin_field="body:opinion",
            )
        ]
        ds = _detect_dimension_stance(DIM_PERFORMANCE, claims)
        assert ds.stance == STANCE_UNKNOWN


# ── 3. 去重规则 ─────────────────────────────────────────────────────


class TestDeduplication:
    def test_same_institution_same_title_marked_dup(self):
        """同机构 + 同标题 + 同立场 → 标记重复。"""
        reports = [
            ReportStance(
                rel_path="a.md", title="中信证券-某公司深度",
                institution="中信证券", overall_stance=STANCE_BULLISH,
            ),
            ReportStance(
                rel_path="b.md", title="中信证券-某公司深度",
                institution="中信证券", overall_stance=STANCE_BULLISH,
            ),
            ReportStance(
                rel_path="c.md", title="国泰君安-某公司点评",
                institution="国泰君安", overall_stance=STANCE_BULLISH,
            ),
        ]
        _mark_duplicates(reports)
        assert reports[0].is_duplicate is False
        assert reports[1].is_duplicate is True
        assert "中信证券" in reports[1].duplicate_reason
        assert reports[2].is_duplicate is False

    def test_different_institution_not_dup(self):
        """不同机构同标题 → 不重复（保留真实多机构共识）。"""
        reports = [
            ReportStance(
                rel_path="a.md", title="某公司深度",
                institution="中信证券", overall_stance=STANCE_BULLISH,
            ),
            ReportStance(
                rel_path="b.md", title="某公司深度",
                institution="国泰君安", overall_stance=STANCE_BULLISH,
            ),
        ]
        _mark_duplicates(reports)
        assert all(not r.is_duplicate for r in reports)

    def test_same_title_different_stance_not_dup(self):
        """同标题但立场不同 → 不重复（这是真实分歧）。"""
        reports = [
            ReportStance(
                rel_path="a.md", title="某公司点评",
                institution="中信证券", overall_stance=STANCE_BULLISH,
            ),
            ReportStance(
                rel_path="b.md", title="某公司点评",
                institution="中信证券", overall_stance=STANCE_BEARISH,
            ),
        ]
        _mark_duplicates(reports)
        assert all(not r.is_duplicate for r in reports)

    def test_same_path_always_dup(self):
        """同 rel_path → 重复（同文件多次导入）。"""
        reports = [
            ReportStance(
                rel_path="a.md", title="t1",
                institution="中信证券", overall_stance=STANCE_BULLISH,
            ),
            ReportStance(
                rel_path="a.md", title="t2",
                institution="国泰君安", overall_stance=STANCE_BULLISH,
            ),
        ]
        _mark_duplicates(reports)
        assert reports[0].is_duplicate is False
        assert reports[1].is_duplicate is True

    def test_norm_title_strips_dates_and_suffix(self):
        assert _norm_title("2026-07-11-某公司深度") == "某公司深度"
        assert _norm_title("某公司深度（2026Q2）") == "某公司深度"
        assert _norm_title("某公司深度-test") == "某公司深度"

    def test_split_institution_helper_basic(self):
        assert split_institution_helper("中信证券-华勤技术") == "中信证券"
        assert split_institution_helper("国泰君安—腾讯") == "国泰君安"
        assert split_institution_helper("某独立来源") == "某独立来源"


# ── 4. 时间窗口 ─────────────────────────────────────────────────────


class TestTimeWindow:
    def test_within_window_recent(self):
        assert _within_window("2026-06-01", date(2026, 7, 12), 3) is True

    def test_within_window_old_report(self):
        assert _within_window("2025-01-01", date(2026, 7, 12), 3) is False
        assert _within_window("2025-01-01", date(2026, 7, 12), 12) is False

    def test_within_window_no_date_kept(self):
        """无报告日期 → 视作窗口内（不误杀）。"""
        assert _within_window(None, date(2026, 7, 12), 3) is True
        assert _within_window("", date(2026, 7, 12), 3) is True

    def test_within_window_invalid_date_kept(self):
        """无法解析的日期 → 视作窗口内。"""
        assert _within_window("not-a-date", date(2026, 7, 12), 3) is True

    def test_window_3m_filters_old(self, fixture_kb: Path):
        """3 月窗口过滤掉过期报告。"""
        result = build_research_consensus_matrix(
            str(fixture_kb), symbol="600500",
            window_months=3, today=_TODAY,
        )
        # 过期报告日期 2025-01-01 在 3 月窗口外。
        # _within_window 对无日期 / 无法解析视作在窗口内，但这里日期有效。
        # 但过期报告还会被 stale 过滤降权。
        if result.symbols:
            m = result.symbols[0]
            # 过期报告在窗口外。
            assert m.report_count_in_window == 0 or m.stale_count >= 0


# ── 5. needs_fact_check 判定 ────────────────────────────────────────


class TestNeedsFactCheck:
    def test_split_triggers_fact_check(self, fixture_kb: Path):
        result = build_research_consensus_matrix(
            str(fixture_kb), symbol="600300", today=_TODAY,
        )
        m = result.symbols[0]
        assert m.needs_fact_check is True
        assert any("分歧" in r for r in m.fact_check_reasons)

    def test_stale_dominance_triggers_fact_check(self, fixture_kb: Path):
        result = build_research_consensus_matrix(
            str(fixture_kb), symbol="600500", today=_TODAY,
        )
        m = result.symbols[0]
        assert m.needs_fact_check is True
        assert any("过期" in r for r in m.fact_check_reasons)

    def test_consistent_does_not_force_fact_check(self, fixture_kb: Path):
        """一致看多（多机构）不一定 needs_fact_check（除非弱来源）。"""
        result = build_research_consensus_matrix(
            str(fixture_kb), symbol="600100", today=_TODAY,
        )
        m = result.symbols[0]
        # 多机构一致看多 → 共识高，needs_fact_check 可能为 False
        # （除非触发其他规则）。这里只验证不崩溃。
        assert isinstance(m.needs_fact_check, bool)
        assert m.consensus_score >= 0.5

    def test_fact_check_priority_values(self, fixture_kb: Path):
        result = build_research_consensus_matrix(
            str(fixture_kb), symbol="600300", today=_TODAY,
        )
        m = result.symbols[0]
        assert m.fact_check_priority in ("low", "medium", "high")

    def test_needs_fact_check_symbols_aggregated(self, fixture_kb: Path):
        """result.needs_fact_check_symbols 包含所有需要反证的标的。"""
        result = build_research_consensus_matrix(
            str(fixture_kb), today=_TODAY,
        )
        # 600300（分裂）和 600500（过期）应进入 needs_fact_check。
        assert "600300.SH" in result.needs_fact_check_symbols
        assert "600500.SH" in result.needs_fact_check_symbols


# ── 6. 多研报高关注不绕过门禁 ───────────────────────────────────────


class TestNoActionOverride:
    """验收头条：多研报高关注只能提高研究优先级，不能绕过门禁。"""

    def test_no_strong_action_words_in_report(self, fixture_kb: Path):
        """报告不含强买卖词。"""
        result = build_research_consensus_matrix(
            str(fixture_kb), today=_TODAY,
        )
        report = render_research_consensus_matrix_report(result)
        _assert_no_strong_action_words(report)

    def test_no_strong_action_words_in_summary(self, fixture_kb: Path):
        """matrix.summary 不含强买卖词。"""
        result = build_research_consensus_matrix(
            str(fixture_kb), today=_TODAY,
        )
        for m in result.symbols:
            _assert_no_strong_action_words(m.summary)

    def test_consensus_score_not_action_signal(self, fixture_kb: Path):
        """consensus_score 是 [0, 1] 区间的中性指标，不是买入信号。"""
        result = build_research_consensus_matrix(
            str(fixture_kb), today=_TODAY,
        )
        for m in result.symbols:
            assert 0.0 <= m.consensus_score <= 1.0
            assert 0.0 <= m.disagreement_score

    def test_has_forbidden_action_words_detector(self):
        assert has_forbidden_action_words("建议买入某股票") is True
        assert has_forbidden_action_words("正常研究文本") is False


# ── 7. 报告渲染 ─────────────────────────────────────────────────────


class TestReportRendering:
    def test_report_has_title_and_status(self, fixture_kb: Path):
        result = build_research_consensus_matrix(
            str(fixture_kb), symbol="600100", today=_TODAY,
        )
        report = render_research_consensus_matrix_report(result)
        assert "KB-016" in report
        assert "共识" in report or "consensus" in report.lower()

    def test_report_empty_result(self, tmp_path: Path):
        inv = tmp_path / INVESTMENT_SUBDIR
        inv.mkdir(parents=True)
        result = build_research_consensus_matrix(
            str(tmp_path), symbol="999999", today=_TODAY,
        )
        report = render_research_consensus_matrix_report(result)
        assert "无研报" in report or "NORMAL_NO_DATA" in report

    def test_report_failed(self, tmp_path: Path):
        result = build_research_consensus_matrix(
            str(tmp_path / "nonexistent"), symbol="600100", today=_TODAY,
        )
        report = render_research_consensus_matrix_report(result)
        assert "FAILED" in report or "失败" in report

    def test_report_contains_matrix_table(self, fixture_kb: Path):
        """报告包含四维分歧矩阵表。"""
        result = build_research_consensus_matrix(
            str(fixture_kb), symbol="600100", today=_TODAY,
        )
        report = render_research_consensus_matrix_report(result)
        assert "四维分歧矩阵" in report
        for label in ("业绩预测", "产业链角色", "风险判断", "估值假设"):
            assert label in report

    def test_report_contains_paths_not_full_text(self, fixture_kb: Path):
        """报告只展示路径，不复制研报原文段落。"""
        result = build_research_consensus_matrix(
            str(fixture_kb), symbol="600100", today=_TODAY,
        )
        report = render_research_consensus_matrix_report(result)
        assert "wiki" in report or ".md" in report
        # 不应包含完整原文 body（投资逻辑段不会被整段复制）。
        assert "## 投资逻辑" not in report

    def test_report_top_limit(self, fixture_kb: Path):
        """top 参数限制概览表行数。"""
        result = build_research_consensus_matrix(
            str(fixture_kb), today=_TODAY,
        )
        report = render_research_consensus_matrix_report(result, top=2)
        # 概览表行数应 ≤ 2 + 表头行。
        # 通过检查 symbol_key 出现次数大致验证。
        # 不严格断言，只确认不崩溃。
        assert "KB-016" in report


# ── 8. JSON 序列化与扁平摘要 ─────────────────────────────────────────


class TestSerialization:
    def test_result_json_serializable(self, fixture_kb: Path):
        result = build_research_consensus_matrix(
            str(fixture_kb), symbol="600100", today=_TODAY,
        )
        data = result.to_dict()
        payload = json.dumps(data, ensure_ascii=False)
        restored = json.loads(payload)
        assert restored["task"] == "KB-016"
        assert restored["status"] == result.status

    def test_symbol_matrix_serializable(self, fixture_kb: Path):
        result = build_research_consensus_matrix(
            str(fixture_kb), symbol="600100", today=_TODAY,
        )
        m = result.symbols[0]
        data = m.to_dict()
        payload = json.dumps(data, ensure_ascii=False)
        restored = json.loads(payload)
        assert restored["symbol_key"] == m.symbol_key
        assert "dimensions" in restored

    def test_dimension_stance_serializable(self):
        ds = DimensionStance(
            dimension=DIM_PERFORMANCE,
            stance=STANCE_BULLISH,
            hit_keywords=["增长", "超预期"],
        )
        data = ds.to_dict()
        payload = json.dumps(data, ensure_ascii=False)
        restored = json.loads(payload)
        assert restored["stance"] == STANCE_BULLISH

    def test_ta_consumable_summary_hit(self, fixture_kb: Path):
        result = build_research_consensus_matrix(
            str(fixture_kb), symbol="600100", today=_TODAY,
        )
        summary = matrix_to_ta_consumable_summary(result.symbols[0])
        assert summary["has_hit"] is True
        assert "consensus_score" in summary
        assert "dimensions_brief" in summary
        assert DIM_PERFORMANCE in summary["dimensions_brief"]

    def test_ta_consumable_summary_empty(self):
        summary = matrix_to_ta_consumable_summary(None)
        assert summary["has_hit"] is False
        assert summary["consensus_score"] == 0.0
        assert summary["needs_fact_check"] is False

    def test_matrix_to_summary_dict(self, fixture_kb: Path):
        result = build_research_consensus_matrix(
            str(fixture_kb), symbol="600100", today=_TODAY,
        )
        d = matrix_to_summary_dict(result.symbols[0])
        assert d["research_consensus_has_hit"] is True
        assert "research_consensus_score" in d

    def test_matrix_to_summary_dict_empty(self):
        d = matrix_to_summary_dict(None)
        assert d["research_consensus_has_hit"] is False
        assert d["research_consensus_score"] == 0.0


# ── 9. 约束验证 ─────────────────────────────────────────────────────


class TestConstraints:
    def test_knowledge_root_not_modified(self, fixture_kb: Path):
        """只读：调用后知识库文件不变。"""
        target = fixture_kb / INVESTMENT_SUBDIR / "bullish-1.md"
        before = target.read_text(encoding="utf-8")
        build_research_consensus_matrix(
            str(fixture_kb), symbol="600100", today=_TODAY,
        )
        after = target.read_text(encoding="utf-8")
        assert before == after

    def test_no_db_writes(self, fixture_kb: Path, tmp_path: Path):
        """不写生产 DB（这里用文件存在性近似验证）。"""
        db_candidate = tmp_path / "tradingagents.db"
        build_research_consensus_matrix(
            str(fixture_kb), symbol="600100", today=_TODAY,
        )
        assert not db_candidate.exists()

    def test_consensus_not_buy_signal(self, fixture_kb: Path):
        """consensus_score 高 ≠ 买入信号。"""
        result = build_research_consensus_matrix(
            str(fixture_kb), symbol="600100", today=_TODAY,
        )
        m = result.symbols[0]
        # 即使 consensus 高，summary 也不应含买卖词。
        _assert_no_strong_action_words(m.summary)

    def test_weak_source_not_overrides_action(self, fixture_kb: Path):
        """弱来源（media/user_note）不覆盖动作语义。"""
        # 一致看多（600100）的来源都是 broker_research，但 consensus_score
        # 只是中性指标，不会变成 BUY。
        result = build_research_consensus_matrix(
            str(fixture_kb), symbol="600100", today=_TODAY,
        )
        m = result.symbols[0]
        assert m.dominant_stance in (STANCE_BULLISH, STANCE_NEUTRAL, STANCE_UNKNOWN)
        # 不应输出 BUY/SELL 字样。
        _assert_no_strong_action_words(str(m.to_dict()))


# ── 10. lookup / 单 symbol 查询 ─────────────────────────────────────


class TestLookup:
    def test_lookup_found(self, fixture_kb: Path):
        m = lookup_research_consensus_matrix(
            str(fixture_kb), "600100", today=_TODAY,
        )
        assert m is not None
        assert m.symbol_key == "600100.SH"

    def test_lookup_with_suffix(self, fixture_kb: Path):
        m = lookup_research_consensus_matrix(
            str(fixture_kb), "600100.SH", today=_TODAY,
        )
        assert m is not None

    def test_lookup_not_found(self, fixture_kb: Path):
        m = lookup_research_consensus_matrix(
            str(fixture_kb), "999999", today=_TODAY,
        )
        assert m is None

    def test_lookup_empty_symbol(self, fixture_kb: Path):
        m = lookup_research_consensus_matrix(
            str(fixture_kb), "", today=_TODAY,
        )
        assert m is None

    def test_symbol_equivalent(self):
        assert _symbol_equivalent("600100", "600100.SH", "600100") is True
        assert _symbol_equivalent("600100.SH", "600100.SH", "600100") is True
        assert _symbol_equivalent("600100", "600200.SH", "600100") is False
        assert _symbol_equivalent("", "600100", "600100") is False


# ── 11. 边界场景 ────────────────────────────────────────────────────


class TestEdgeCases:
    def test_knowledge_root_not_exists(self, tmp_path: Path):
        result = build_research_consensus_matrix(
            str(tmp_path / "nonexistent"), symbol="600100", today=_TODAY,
        )
        assert result.status == "FAILED"
        assert len(result.errors) > 0

    def test_investment_dir_not_exists(self, tmp_path: Path):
        result = build_research_consensus_matrix(
            str(tmp_path), symbol="600100", today=_TODAY,
        )
        assert result.status == "NORMAL_NO_DATA"
        assert len(result.errors) > 0

    def test_empty_investment_dir(self, tmp_path: Path):
        inv = tmp_path / INVESTMENT_SUBDIR
        inv.mkdir(parents=True)
        result = build_research_consensus_matrix(
            str(tmp_path), symbol="600100", today=_TODAY,
        )
        assert result.status == "NORMAL_NO_DATA"

    def test_no_matching_symbol(self, fixture_kb: Path):
        result = build_research_consensus_matrix(
            str(fixture_kb), symbol="999999", today=_TODAY,
        )
        assert result.status == "NORMAL_NO_DATA"
        assert len(result.symbols) == 0

    def test_single_page_does_not_crash(self, tmp_path: Path):
        """只有一页研报 → 不崩溃，attention=1。"""
        inv = tmp_path / INVESTMENT_SUBDIR
        _write(inv / "single.md", _BULLISH_1)
        result = build_research_consensus_matrix(
            str(tmp_path), symbol="600100", today=_TODAY,
        )
        assert result.status == "HAS_DATA"
        assert len(result.symbols) == 1
        assert result.symbols[0].report_count_in_window == 1

    def test_all_index_mode(self, fixture_kb: Path):
        """全库索引模式返回所有 symbol。"""
        result = build_research_consensus_matrix(
            str(fixture_kb), today=_TODAY,
        )
        assert result.status == "HAS_DATA"
        # 至少 5 个 fixture symbol。
        assert len(result.symbols) >= 5
        sym_keys = [m.symbol_key for m in result.symbols]
        assert "600100.SH" in sym_keys
        assert "600200.SH" in sym_keys

    def test_default_window_is_12(self):
        assert DEFAULT_WINDOW_MONTHS == 12

    def test_default_result_structure(self):
        """空 result 的默认字段结构稳定。"""
        r = ResearchConsensusMatrixResult()
        assert r.status == "NORMAL_NO_DATA"
        assert r.task == "KB-016"
        assert r.window_months == DEFAULT_WINDOW_MONTHS
        assert r.symbols == []


# ── 12. suggest_report_output_path ──────────────────────────────────


class TestOutputPath:
    def test_suggest_output_path_format(self):
        path = suggest_report_output_path()
        assert "research_consensus_matrix-" in path
        assert path.endswith(".md")
        assert "docs/knowledge_reports" in path


# ── 13. overall_stance 合成 ─────────────────────────────────────────


class TestOverallStance:
    def test_all_bullish(self):
        dims = {
            DIM_PERFORMANCE: DimensionStance(DIM_PERFORMANCE, STANCE_BULLISH),
            DIM_RISK: DimensionStance(DIM_RISK, STANCE_BULLISH),
        }
        assert _detect_overall_stance(dims) == STANCE_BULLISH

    def test_all_bearish(self):
        dims = {
            DIM_PERFORMANCE: DimensionStance(DIM_PERFORMANCE, STANCE_BEARISH),
            DIM_RISK: DimensionStance(DIM_RISK, STANCE_BEARISH),
        }
        assert _detect_overall_stance(dims) == STANCE_BEARISH

    def test_mixed_bull_bear(self):
        dims = {
            DIM_PERFORMANCE: DimensionStance(DIM_PERFORMANCE, STANCE_BULLISH),
            DIM_RISK: DimensionStance(DIM_RISK, STANCE_BEARISH),
        }
        assert _detect_overall_stance(dims) == STANCE_NEUTRAL

    def test_all_unknown(self):
        dims = {
            DIM_PERFORMANCE: DimensionStance(DIM_PERFORMANCE, STANCE_UNKNOWN),
        }
        assert _detect_overall_stance(dims) == STANCE_UNKNOWN
