# [S-006] candidate_false_positive_audit — tests
"""Tests for the candidate false positive audit module.

Covers:
- Audit entry classification: HIT_STRATEGY / FILTERED / RISK_DEMOTED / EVIDENCE_GAP
- Filter reason subcategory classification: liquidity / data_missing / no_strategy / other
- Risk demotion detection: high severity / too many / heavy penalty / bad balance / low completeness
- FP/FN type determination: true_positive / false_positive / true_negative / false_negative
- Audit summary aggregation: by_category / by_strategy / by_priority_rank / by_fp_type
- Common evidence gaps and risk demotions
- Fixture generation and replay stability
- Audit report rendering: no forbidden words
- Integration with Discovery / Plan Runner
- DB persistence round-trip (no production DB)
- Evidence gap summary in output
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from tradingagents.tradeflow.false_positive_audit import (
    audit_candidate,
    audit_filtered_symbol,
    build_audit_report,
    render_audit_report,
    generate_fixture_samples,
    replay_fixtures,
    classify_filter_reason,
    classify_risk_demotion,
    AuditEntry,
    AuditSummary,
    AuditReport,
    AUDIT_CATEGORY_HIT_STRATEGY,
    AUDIT_CATEGORY_FILTERED,
    AUDIT_CATEGORY_RISK_DEMOTED,
    AUDIT_CATEGORY_EVIDENCE_GAP,
    ALL_AUDIT_CATEGORIES,
    FP_TYPE_FALSE_POSITIVE,
    FP_TYPE_FALSE_NEGATIVE,
    FP_TYPE_TRUE_POSITIVE,
    FP_TYPE_TRUE_NEGATIVE,
    FILTER_SUBCATEGORY_LIQUIDITY,
    FILTER_SUBCATEGORY_DATA_MISSING,
    FILTER_SUBCATEGORY_NO_STRATEGY,
    FILTER_SUBCATEGORY_DATA_INSUFFICIENT,
    FILTER_SUBCATEGORY_OTHER,
    ALL_FILTER_SUBCATEGORIES,
    RISK_DEMOTION_HIGH_SEVERITY,
    RISK_DEMOTION_TOO_MANY_RISKS,
    RISK_DEMOTION_HEAVY_PENALTY,
    RISK_DEMOTION_GAME_BALANCE,
    RISK_DEMOTION_LOW_COMPLETENESS,
    _determine_fp_fn_type,
)
from tradingagents.tradeflow.schemas import (
    Candidate,
    CandidateSignal,
    DailyPlan,
    STRATEGY_VCP,
    STRATEGY_PULLBACK,
    STRATEGY_EVENT,
    STRATEGY_POLICY_VERSION,
    STRATEGY_NARRATIVE,
    STRATEGY_FUND_FLOW,
    FORBIDDEN_WORDS,
)


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


# ═══════════════════════════════════════════════════════════════
# Unit tests: classify_filter_reason()
# ═══════════════════════════════════════════════════════════════

class TestClassifyFilterReason:
    def test_liquidity(self):
        assert classify_filter_reason("流动性差(avg_amount=0.02亿)") == FILTER_SUBCATEGORY_LIQUIDITY

    def test_data_missing(self):
        assert classify_filter_reason("数据缺失") == FILTER_SUBCATEGORY_DATA_MISSING

    def test_data_insufficient(self):
        assert classify_filter_reason("数据不足(需至少40日)") == FILTER_SUBCATEGORY_DATA_INSUFFICIENT

    def test_no_strategy(self):
        assert classify_filter_reason("无策略命中") == FILTER_SUBCATEGORY_NO_STRATEGY

    def test_other(self):
        assert classify_filter_reason("ST股") == FILTER_SUBCATEGORY_OTHER

    def test_combined_liquidity_and_data(self):
        r = classify_filter_reason("流动性差；数据缺失")
        assert r == FILTER_SUBCATEGORY_LIQUIDITY

    def test_empty_string(self):
        assert classify_filter_reason("") == FILTER_SUBCATEGORY_OTHER


# ═══════════════════════════════════════════════════════════════
# Unit tests: classify_risk_demotion()
# ═══════════════════════════════════════════════════════════════

class TestClassifyRiskDemotion:
    def test_no_risk(self):
        reasons, refs = classify_risk_demotion(data_completeness=1.0)
        assert reasons == []
        assert refs == []

    def test_high_severity_inquiry(self):
        reasons, refs = classify_risk_demotion(risk_flags=["INQUIRY_RISK"])
        assert any(RISK_DEMOTION_HIGH_SEVERITY in r for r in reasons)
        assert any(ref.get("value") == "high_severity" for ref in refs)

    def test_high_severity_financial(self):
        reasons, refs = classify_risk_demotion(risk_flags=["FINANCIAL_QUALITY_RISK"])
        assert any(RISK_DEMOTION_HIGH_SEVERITY in r for r in reasons)

    def test_too_many_risks(self):
        flags = ["LOCKUP_RISK", "REDUCE_HOLDING_RISK", "MARGIN_CROWDING_RISK"]
        reasons, refs = classify_risk_demotion(risk_flags=flags)
        assert any(RISK_DEMOTION_TOO_MANY_RISKS in r for r in reasons)

    def test_heavy_penalty(self):
        reasons, refs = classify_risk_demotion(risk_penalty=-20.0)
        assert any(RISK_DEMOTION_HEAVY_PENALTY in r for r in reasons)

    def test_light_penalty_not_demoted(self):
        reasons, _ = classify_risk_demotion(risk_penalty=-5.0)
        assert not any(RISK_DEMOTION_HEAVY_PENALTY in r for r in reasons)

    def test_game_balance_fragile(self):
        reasons, refs = classify_risk_demotion(game_balance="fragile")
        assert any(RISK_DEMOTION_GAME_BALANCE in r for r in reasons)

    def test_game_balance_crowded(self):
        reasons, _ = classify_risk_demotion(game_balance="crowded")
        assert any(RISK_DEMOTION_GAME_BALANCE in r for r in reasons)

    def test_game_balance_favorable_ok(self):
        reasons, _ = classify_risk_demotion(game_balance="favorable")
        assert not any(RISK_DEMOTION_GAME_BALANCE in r for r in reasons)

    def test_low_completeness(self):
        reasons, refs = classify_risk_demotion(data_completeness=0.3)
        assert any(RISK_DEMOTION_LOW_COMPLETENESS in r for r in reasons)

    def test_high_completeness_ok(self):
        reasons, _ = classify_risk_demotion(data_completeness=0.8)
        assert not any(RISK_DEMOTION_LOW_COMPLETENESS in r for r in reasons)

    def test_multiple_demotions(self):
        reasons, refs = classify_risk_demotion(
            risk_flags=["INQUIRY_RISK", "LOCKUP_RISK", "MARGIN_CROWDING_RISK"],
            risk_penalty=-20.0,
            game_balance="fragile",
            data_completeness=0.3,
        )
        assert len(reasons) == 5
        assert len(refs) == 5

    def test_refs_have_field_and_value(self):
        _, refs = classify_risk_demotion(risk_flags=["INQUIRY_RISK"])
        for ref in refs:
            assert "field" in ref
            assert "value" in ref


# ═══════════════════════════════════════════════════════════════
# Unit tests: _determine_fp_fn_type()
# ═══════════════════════════════════════════════════════════════

class TestDetermineFpFnType:
    def test_hit_strategy_true_positive(self):
        fp, fn = _determine_fp_fn_type(
            AUDIT_CATEGORY_HIT_STRATEGY, ["VCP"], True, [], [], "B"
        )
        assert fp == FP_TYPE_TRUE_POSITIVE
        assert fn == ""

    def test_hit_strategy_with_risk_demotion(self):
        fp, fn = _determine_fp_fn_type(
            AUDIT_CATEGORY_HIT_STRATEGY, ["VCP"], False, ["高风险"], [], "C"
        )
        assert fp == FP_TYPE_FALSE_POSITIVE
        assert fn == ""

    def test_filtered_true_negative(self):
        fp, fn = _determine_fp_fn_type(
            AUDIT_CATEGORY_FILTERED, [], False, [], [], ""
        )
        assert fn == FP_TYPE_TRUE_NEGATIVE

    def test_risk_demoted(self):
        fp, fn = _determine_fp_fn_type(
            AUDIT_CATEGORY_RISK_DEMOTED, ["VCP"], False, ["high_severity"], [], "C"
        )
        assert fp == FP_TYPE_FALSE_POSITIVE
        assert fn == FP_TYPE_FALSE_NEGATIVE

    def test_evidence_gap(self):
        fp, fn = _determine_fp_fn_type(
            AUDIT_CATEGORY_EVIDENCE_GAP, ["VCP"], False, [], ["gap1", "gap2"], "C"
        )
        assert fp == FP_TYPE_FALSE_POSITIVE
        assert fn == FP_TYPE_FALSE_NEGATIVE


# ═══════════════════════════════════════════════════════════════
# Unit tests: audit_candidate()
# ═══════════════════════════════════════════════════════════════

class TestAuditCandidate:
    def test_clean_vcp_hit(self):
        c = _make_candidate(
            symbol="600519.SH",
            strategy_tags=["VCP"],
            score=45.0,
            composite_score=50.0,
            priority_rank="B",
            need_deep_ta=True,
            data_completeness=0.75,
        )
        entry = audit_candidate(c)
        assert entry.audit_category == AUDIT_CATEGORY_HIT_STRATEGY
        assert entry.fp_type == FP_TYPE_TRUE_POSITIVE
        assert "VCP" in entry.hit_strategies

    def test_risk_demoted_inquiry(self):
        c = _make_candidate(
            symbol="300999.SZ",
            strategy_tags=["VCP"],
            score=40.0,
            risk_flags=["INQUIRY_RISK", "LOCKUP_RISK", "MARGIN_CROWDING_RISK"],
            risk_penalty=-25.0,
            game_balance="fragile",
            need_deep_ta=False,
            data_completeness=0.5,
        )
        entry = audit_candidate(c)
        assert entry.audit_category == AUDIT_CATEGORY_RISK_DEMOTED
        assert entry.fp_type == FP_TYPE_FALSE_POSITIVE
        assert len(entry.risk_demotion_reasons) >= 2

    def test_evidence_gap_low_completeness(self):
        c = _make_candidate(
            symbol="688981.SH",
            strategy_tags=["VCP"],
            score=35.0,
            data_completeness=0.25,
            need_deep_ta=False,
            missing_evidence=["事件/新闻数据", "资金流数据", "资金单位校验"],
        )
        entry = audit_candidate(c)
        assert entry.audit_category == AUDIT_CATEGORY_EVIDENCE_GAP
        assert entry.fp_type == FP_TYPE_FALSE_POSITIVE
        assert len(entry.evidence_gaps) >= 2

    def test_fund_flow_unverified_evidence_gap(self):
        c = _make_candidate(
            symbol="002415.SZ",
            strategy_tags=["VCP", "FUND_FLOW_ANOMALY"],
            score=40.0,
            data_completeness=0.5,
            need_deep_ta=False,
            missing_evidence=["资金单位校验", "事件/新闻数据"],
            fund_flow_unit_verified=False,
        )
        entry = audit_candidate(c)
        assert entry.audit_category == AUDIT_CATEGORY_EVIDENCE_GAP

    def test_event_catalyst_hit(self):
        c = _make_candidate(
            symbol="002138.SZ",
            strategy_tags=["EVENT_CATALYST", "NARRATIVE_QUALITY"],
            score=30.0,
            composite_score=55.0,
            narrative_score=25.0,
            priority_rank="B",
            need_deep_ta=True,
            data_completeness=0.625,
        )
        entry = audit_candidate(c)
        assert entry.audit_category == AUDIT_CATEGORY_HIT_STRATEGY
        assert entry.fp_type == FP_TYPE_TRUE_POSITIVE

    def test_candidate_no_strategies(self):
        c = _make_candidate(
            symbol="000001.SZ",
            strategy_tags=[],
            score=0.0,
            data_completeness=0.375,
            need_deep_ta=False,
            missing_evidence=["技术形态信号", "政策版本信号", "叙事质量信号"],
        )
        entry = audit_candidate(c)
        assert entry.audit_category == AUDIT_CATEGORY_EVIDENCE_GAP

    def test_refs_populated(self):
        c = _make_candidate(
            symbol="600519.SH",
            strategy_tags=["VCP", "POLICY_VERSION"],
            score=45.0,
            composite_score=50.0,
            priority_rank="B",
            need_deep_ta=True,
            data_completeness=0.75,
            risk_flags=["INQUIRY_RISK"],
            risk_penalty=-10.0,
        )
        entry = audit_candidate(c)
        assert len(entry.refs) > 0
        ref_fields = {r.get("field") for r in entry.refs}
        assert "hit_strategies" in ref_fields

    def test_high_risk_demoted_not_deep_ta(self):
        c = _make_candidate(
            symbol="300001.SZ",
            strategy_tags=["VCP"],
            score=40.0,
            risk_flags=["INQUIRY_RISK"],
            risk_penalty=-15.0,
            game_balance="fragile",
            need_deep_ta=False,
            data_completeness=0.3,
        )
        entry = audit_candidate(c)
        assert entry.audit_category == AUDIT_CATEGORY_RISK_DEMOTED
        assert entry.need_deep_ta is False


# ═══════════════════════════════════════════════════════════════
# Unit tests: audit_filtered_symbol()
# ═══════════════════════════════════════════════════════════════

class TestAuditFilteredSymbol:
    def test_liquidity_filter(self):
        entry = audit_filtered_symbol(
            symbol="830799.NQ",
            name="某新三板",
            source="watchlist",
            reason="流动性差(avg_amount=0.02亿, avg_volume=50万, threshold=0.30亿)",
        )
        assert entry.audit_category == AUDIT_CATEGORY_FILTERED
        assert entry.filter_subcategory == FILTER_SUBCATEGORY_LIQUIDITY
        assert entry.fn_type == FP_TYPE_TRUE_NEGATIVE

    def test_no_strategy_filter(self):
        entry = audit_filtered_symbol(
            symbol="000001.SZ",
            name="平安银行",
            source="manual",
            reason="无策略命中",
        )
        assert entry.audit_category == AUDIT_CATEGORY_FILTERED
        assert entry.filter_subcategory == FILTER_SUBCATEGORY_NO_STRATEGY
        assert entry.fn_type == FP_TYPE_TRUE_NEGATIVE

    def test_data_missing_filter(self):
        entry = audit_filtered_symbol(
            symbol="688001.SH",
            source="industry_pool",
            reason="数据缺失",
        )
        assert entry.filter_subcategory == FILTER_SUBCATEGORY_DATA_MISSING

    def test_data_insufficient_filter(self):
        entry = audit_filtered_symbol(
            symbol="300001.SZ",
            reason="数据不足(需至少40日)",
        )
        assert entry.filter_subcategory == FILTER_SUBCATEGORY_DATA_INSUFFICIENT

    def test_empty_reason(self):
        entry = audit_filtered_symbol(symbol="000000.SZ", reason="")
        assert entry.filter_subcategory == FILTER_SUBCATEGORY_OTHER

    def test_refs_have_filter_fields(self):
        entry = audit_filtered_symbol(symbol="000001.SZ", reason="无策略命中", source="manual")
        assert any(r.get("field") == "filter_reason" for r in entry.refs)
        assert any(r.get("field") == "filter_subcategory" for r in entry.refs)
        assert any(r.get("field") == "source" for r in entry.refs)


# ═══════════════════════════════════════════════════════════════
# Unit tests: build_audit_report()
# ═══════════════════════════════════════════════════════════════

class TestBuildAuditReport:
    def test_empty_inputs(self):
        report = build_audit_report()
        assert report.summary is not None
        assert report.summary.total_candidates == 0
        assert report.summary.total_filtered == 0
        assert len(report.entries) == 0

    def test_candidates_only(self):
        c1 = _make_candidate(symbol="600519.SH", strategy_tags=["VCP"], score=45.0, data_completeness=0.75)
        c2 = _make_candidate(symbol="000001.SZ", strategy_tags=[], score=0.0, data_completeness=0.25, missing_evidence=["技术形态信号", "事件/新闻数据"])
        report = build_audit_report(candidates=[c1, c2], trade_date="2026-05-29")
        assert len(report.entries) == 2
        assert report.summary.total_candidates == 2
        assert report.summary.total_filtered == 0
        assert report.trade_date == "2026-05-29"

    def test_filtered_only(self):
        filtered = [
            {"symbol": "830799.NQ", "reason": "流动性差"},
            {"symbol": "000001.SZ", "reason": "无策略命中"},
        ]
        report = build_audit_report(filtered_symbols=filtered)
        assert len(report.entries) == 2
        assert report.summary.total_filtered == 2

    def test_mixed(self):
        c1 = _make_candidate(symbol="600519.SH", strategy_tags=["VCP"], score=45.0, data_completeness=0.75)
        filtered = [{"symbol": "000001.SZ", "reason": "无策略命中"}]
        report = build_audit_report(candidates=[c1], filtered_symbols=filtered)
        assert report.summary.total_candidates == 1
        assert report.summary.total_filtered == 1
        assert len(report.entries) == 2

    def test_summary_by_category(self):
        c1 = _make_candidate(symbol="600519.SH", strategy_tags=["VCP"], score=45.0, composite_score=50.0, data_completeness=0.75, priority_rank="B", need_deep_ta=True)
        c2 = _make_candidate(symbol="300999.SZ", strategy_tags=["VCP"], score=40.0, risk_flags=["INQUIRY_RISK", "LOCKUP_RISK", "MARGIN_CROWDING_RISK"], risk_penalty=-25.0, game_balance="fragile", data_completeness=0.5)
        report = build_audit_report(candidates=[c1, c2])
        assert AUDIT_CATEGORY_HIT_STRATEGY in report.summary.by_category
        assert AUDIT_CATEGORY_RISK_DEMOTED in report.summary.by_category

    def test_summary_by_strategy(self):
        c1 = _make_candidate(symbol="600519.SH", strategy_tags=["VCP", "POLICY_VERSION"], score=45.0, data_completeness=0.75)
        c2 = _make_candidate(symbol="000001.SZ", strategy_tags=["VCP"], score=30.0, data_completeness=0.5)
        report = build_audit_report(candidates=[c1, c2])
        assert report.summary.by_strategy.get("VCP") == 2
        assert report.summary.by_strategy.get("POLICY_VERSION") == 1

    def test_summary_by_priority_rank(self):
        c1 = _make_candidate(symbol="600519.SH", strategy_tags=["VCP"], score=45.0, composite_score=70.0, priority_rank="A", data_completeness=0.75, need_deep_ta=True)
        c2 = _make_candidate(symbol="000001.SZ", strategy_tags=["VCP"], score=30.0, composite_score=35.0, priority_rank="B", data_completeness=0.625)
        report = build_audit_report(candidates=[c1, c2])
        assert report.summary.by_priority_rank.get("A") == 1
        assert report.summary.by_priority_rank.get("B") == 1

    def test_summary_common_evidence_gaps(self):
        c1 = _make_candidate(symbol="600519.SH", strategy_tags=["VCP"], score=45.0, data_completeness=0.375, missing_evidence=["事件/新闻数据", "资金流数据"])
        c2 = _make_candidate(symbol="000001.SZ", strategy_tags=["VCP"], score=30.0, data_completeness=0.375, missing_evidence=["事件/新闻数据"])
        report = build_audit_report(candidates=[c1, c2])
        gap_names = [g["gap"] for g in report.summary.common_evidence_gaps]
        assert "事件/新闻数据" in gap_names
        event_count = next(g["count"] for g in report.summary.common_evidence_gaps if g["gap"] == "事件/新闻数据")
        assert event_count == 2

    def test_summary_common_risk_demotions(self):
        c1 = _make_candidate(symbol="300001.SZ", strategy_tags=["VCP"], score=40.0, risk_flags=["INQUIRY_RISK", "LOCKUP_RISK", "MARGIN_CROWDING_RISK"], risk_penalty=-20.0, game_balance="fragile", data_completeness=0.3)
        report = build_audit_report(candidates=[c1])
        assert len(report.summary.common_risk_demotions) > 0

    def test_summary_by_fp_type(self):
        c1 = _make_candidate(symbol="600519.SH", strategy_tags=["VCP"], score=45.0, composite_score=50.0, priority_rank="B", need_deep_ta=True, data_completeness=0.75)
        c2 = _make_candidate(symbol="300999.SZ", strategy_tags=["VCP"], score=40.0, risk_flags=["INQUIRY_RISK", "LOCKUP_RISK", "MARGIN_CROWDING_RISK"], risk_penalty=-25.0, game_balance="fragile", data_completeness=0.5)
        report = build_audit_report(candidates=[c1, c2])
        assert FP_TYPE_TRUE_POSITIVE in report.summary.by_fp_type
        assert FP_TYPE_FALSE_POSITIVE in report.summary.by_fp_type

    def test_trade_date_default(self):
        report = build_audit_report()
        assert report.trade_date != ""


# ═══════════════════════════════════════════════════════════════
# Unit tests: render_audit_report()
# ═══════════════════════════════════════════════════════════════

class TestRenderAuditReport:
    def test_empty_report(self):
        report = build_audit_report()
        text = render_audit_report(report)
        assert "候选误报审计报告" in text

    def test_no_forbidden_words(self):
        c = _make_candidate(
            symbol="600519.SH",
            strategy_tags=["VCP"],
            score=45.0,
            composite_score=50.0,
            priority_rank="B",
            need_deep_ta=True,
            data_completeness=0.75,
        )
        report = build_audit_report(candidates=[c])
        text = render_audit_report(report)
        for word in FORBIDDEN_WORDS:
            assert word not in text

    def test_summary_displayed(self):
        c = _make_candidate(symbol="600519.SH", strategy_tags=["VCP"], score=45.0, data_completeness=0.75)
        report = build_audit_report(candidates=[c])
        text = render_audit_report(report)
        assert "摘要" in text
        assert "HIT_STRATEGY" in text

    def test_filtered_displayed(self):
        filtered = [{"symbol": "830799.NQ", "reason": "流动性差(avg=0.02亿)"}]
        report = build_audit_report(filtered_symbols=filtered)
        text = render_audit_report(report)
        assert "830799.NQ" in text
        assert "流动性差" in text

    def test_evidence_gaps_displayed(self):
        c = _make_candidate(
            symbol="688981.SH",
            strategy_tags=["VCP"],
            score=35.0,
            data_completeness=0.25,
            missing_evidence=["事件/新闻数据", "资金流数据", "资金单位校验"],
        )
        report = build_audit_report(candidates=[c])
        text = render_audit_report(report)
        assert "证据缺口" in text

    def test_risk_demotions_displayed(self):
        c = _make_candidate(
            symbol="300999.SZ",
            strategy_tags=["VCP"],
            score=40.0,
            risk_flags=["INQUIRY_RISK", "LOCKUP_RISK", "MARGIN_CROWDING_RISK"],
            risk_penalty=-25.0,
            game_balance="fragile",
            data_completeness=0.5,
        )
        report = build_audit_report(candidates=[c])
        text = render_audit_report(report)
        assert "风险降权" in text

    def test_filter_reason_distribution_displayed(self):
        filtered = [
            {"symbol": "830799.NQ", "reason": "流动性差"},
            {"symbol": "000001.SZ", "reason": "无策略命中"},
        ]
        report = build_audit_report(filtered_symbols=filtered)
        text = render_audit_report(report)
        assert "过滤原因分布" in text


# ═══════════════════════════════════════════════════════════════
# Unit tests: generate_fixture_samples() and replay_fixtures()
# ═══════════════════════════════════════════════════════════════

class TestFixtureSamples:
    def test_fixtures_generated(self):
        fixtures = generate_fixture_samples()
        assert len(fixtures) == 8
        expected_names = {
            "vcp_hit", "liquidity_filter", "no_strategy_hit",
            "event_catalyst", "fund_flow_anomaly", "risk_demoted",
            "evidence_gap", "fund_flow_unverified",
        }
        assert set(fixtures.keys()) == expected_names

    def test_fixture_has_description(self):
        fixtures = generate_fixture_samples()
        for name, f in fixtures.items():
            assert "description" in f, f"Fixture {name} missing description"

    def test_fixture_candidate_has_expected_category(self):
        fixtures = generate_fixture_samples()
        for name, f in fixtures.items():
            if "expected_audit_category" in f:
                assert f["expected_audit_category"] in ALL_AUDIT_CATEGORIES, f"Fixture {name} invalid category"

    def test_fixture_candidate_has_expected_fp_type(self):
        fixtures = generate_fixture_samples()
        valid_fp_types = {FP_TYPE_TRUE_POSITIVE, FP_TYPE_FALSE_POSITIVE, FP_TYPE_TRUE_NEGATIVE, FP_TYPE_FALSE_NEGATIVE}
        for name, f in fixtures.items():
            if "expected_fp_type" in f:
                assert f["expected_fp_type"] in valid_fp_types, f"Fixture {name} invalid fp_type"

    def test_replay_returns_results(self):
        results = replay_fixtures()
        assert len(results) == 8

    def test_replay_vcp_hit(self):
        results = replay_fixtures()
        vcp = next(r for r in results if r["fixture_name"] == "vcp_hit")
        assert vcp["category_match"] is True
        assert vcp["fp_match"] is True

    def test_replay_liquidity_filter(self):
        results = replay_fixtures()
        liq = next(r for r in results if r["fixture_name"] == "liquidity_filter")
        assert liq["subcategory_match"] is True
        assert liq["fn_match"] is True

    def test_replay_no_strategy(self):
        results = replay_fixtures()
        ns = next(r for r in results if r["fixture_name"] == "no_strategy_hit")
        assert ns["subcategory_match"] is True
        assert ns["fn_match"] is True

    def test_replay_event_catalyst(self):
        results = replay_fixtures()
        ev = next(r for r in results if r["fixture_name"] == "event_catalyst")
        assert ev["category_match"] is True
        assert ev["fp_match"] is True

    def test_replay_fund_flow_anomaly(self):
        results = replay_fixtures()
        ff = next(r for r in results if r["fixture_name"] == "fund_flow_anomaly")
        assert ff["category_match"] is True
        assert ff["fp_match"] is True

    def test_replay_risk_demoted(self):
        results = replay_fixtures()
        rd = next(r for r in results if r["fixture_name"] == "risk_demoted")
        assert rd["category_match"] is True
        assert rd["fp_match"] is True

    def test_replay_evidence_gap(self):
        results = replay_fixtures()
        eg = next(r for r in results if r["fixture_name"] == "evidence_gap")
        assert eg["category_match"] is True
        assert eg["fp_match"] is True

    def test_replay_fund_flow_unverified(self):
        results = replay_fixtures()
        fu = next(r for r in results if r["fixture_name"] == "fund_flow_unverified")
        assert fu["category_match"] is True
        assert fu["fp_match"] is True

    def test_replay_stable_count(self):
        results1 = replay_fixtures()
        results2 = replay_fixtures()
        assert len(results1) == len(results2)

    def test_replay_custom_fixtures(self):
        custom = {
            "test": {
                "description": "custom test",
                "candidate": _make_candidate(symbol="000001.SZ", strategy_tags=["VCP"], score=30.0, data_completeness=0.75),
                "expected_audit_category": AUDIT_CATEGORY_HIT_STRATEGY,
                "expected_fp_type": FP_TYPE_TRUE_POSITIVE,
            },
        }
        results = replay_fixtures(custom)
        assert len(results) == 1
        assert results[0]["fixture_name"] == "test"


# ═══════════════════════════════════════════════════════════════
# Unit tests: AuditEntry / AuditSummary dataclass
# ═══════════════════════════════════════════════════════════════

class TestAuditDataclasses:
    def test_audit_entry_defaults(self):
        e = AuditEntry(symbol="000001.SZ")
        assert e.audit_category == ""
        assert e.hit_strategies == []
        assert e.risk_flags == []
        assert e.evidence_gaps == []

    def test_audit_summary_defaults(self):
        s = AuditSummary()
        assert s.total_candidates == 0
        assert s.by_category == {}
        assert s.common_evidence_gaps == []

    def test_audit_report_defaults(self):
        r = AuditReport()
        assert r.entries == []
        assert r.generated_at != ""

    def test_audit_report_post_init(self):
        r = AuditReport(trade_date="2026-05-29")
        assert r.generated_at != ""
        assert r.trade_date == "2026-05-29"


# ═══════════════════════════════════════════════════════════════
# Integration: Discovery with audit
# ═══════════════════════════════════════════════════════════════

class TestDiscoveryAuditIntegration:
    def test_discovery_result_has_audit_report(self):
        from tradingagents.tradeflow.discovery import DiscoveryResult
        result = DiscoveryResult(trade_date="2026-05-29")
        assert hasattr(result, "audit_report")

    def test_discovery_audit_summary_in_metadata(self):
        from tradingagents.tradeflow.discovery import run_discovery
        from unittest.mock import patch, MagicMock
        import pandas as pd

        mock_df = pd.DataFrame({
            "Date": pd.date_range("2026-01-01", periods=60),
            "Open": range(60),
            "High": range(1, 61),
            "Low": range(60),
            "Close": range(60),
            "Volume": [1000000] * 60,
            "Amount": [50000000] * 60,
        })

        with patch("tradingagents.tradeflow.candidate_engine._fetch_price_data", return_value=mock_df):
            result = run_discovery(
                trade_date="2026-05-29",
                symbols=["000001.SZ"],
                include_holdings=False,
                include_watchlist=False,
            )
        assert result.audit_report is not None
        assert "audit_summary" in result.metadata

    def test_discovery_render_includes_evidence_gap(self):
        from tradingagents.tradeflow.discovery import DiscoveryResult, render_discovery_text
        from tradingagents.tradeflow.false_positive_audit import build_audit_report

        c = _make_candidate(
            symbol="688981.SH",
            strategy_tags=["VCP"],
            score=35.0,
            data_completeness=0.25,
            missing_evidence=["事件/新闻数据", "资金流数据"],
        )
        audit = build_audit_report(candidates=[c], trade_date="2026-05-29")

        result = DiscoveryResult(
            trade_date="2026-05-29",
            candidates=[{"symbol": "688981.SH"}],
            audit_report=audit,
        )
        text = render_discovery_text(result)
        assert "证据缺口摘要" in text


# ═══════════════════════════════════════════════════════════════
# Integration: Plan Runner with audit
# ═══════════════════════════════════════════════════════════════

class TestPlanRunnerAuditIntegration:
    def test_plan_has_audit_summary_in_metadata(self):
        from tradingagents.tradeflow.plan_runner import generate_daily_plan

        c = _make_candidate(
            symbol="600519.SH",
            strategy_tags=["VCP"],
            score=45.0,
            composite_score=50.0,
            priority_rank="B",
            need_deep_ta=True,
            data_completeness=0.75,
        )
        plan = generate_daily_plan(
            trade_date="2026-05-29",
            candidates=[c],
        )
        assert "audit_summary" in plan.metadata
        assert plan.metadata["audit_summary"]["total_candidates"] == 1

    def test_plan_audit_summary_by_category(self):
        from tradingagents.tradeflow.plan_runner import generate_daily_plan

        c1 = _make_candidate(symbol="600519.SH", strategy_tags=["VCP"], score=45.0, composite_score=50.0, priority_rank="B", need_deep_ta=True, data_completeness=0.75)
        c2 = _make_candidate(symbol="300999.SZ", strategy_tags=["VCP"], score=40.0, risk_flags=["INQUIRY_RISK", "LOCKUP_RISK", "MARGIN_CROWDING_RISK"], risk_penalty=-25.0, game_balance="fragile", data_completeness=0.5)

        plan = generate_daily_plan(
            trade_date="2026-05-29",
            candidates=[c1, c2],
        )
        audit_summary = plan.metadata["audit_summary"]
        assert "by_category" in audit_summary
        assert len(audit_summary["by_category"]) >= 2

    def test_plan_audit_evidence_gaps(self):
        from tradingagents.tradeflow.plan_runner import generate_daily_plan

        c = _make_candidate(
            symbol="688981.SH",
            strategy_tags=["VCP"],
            score=35.0,
            data_completeness=0.25,
            missing_evidence=["事件/新闻数据", "资金流数据", "资金单位校验"],
        )
        plan = generate_daily_plan(
            trade_date="2026-05-29",
            candidates=[c],
        )
        gaps = plan.metadata["audit_summary"]["common_evidence_gaps"]
        gap_names = [g["gap"] for g in gaps]
        assert "事件/新闻数据" in gap_names


# ═══════════════════════════════════════════════════════════════
# Edge cases
# ═══════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_candidate_with_only_risk_no_strategy(self):
        c = _make_candidate(
            symbol="300001.SZ",
            strategy_tags=[],
            risk_flags=["INQUIRY_RISK"],
            risk_penalty=-10.0,
            data_completeness=0.375,
        )
        entry = audit_candidate(c)
        assert entry.audit_category == AUDIT_CATEGORY_RISK_DEMOTED

    def test_candidate_all_complete_no_risk_no_strategy(self):
        c = _make_candidate(
            symbol="000001.SZ",
            strategy_tags=[],
            data_completeness=0.75,
            missing_evidence=["技术形态信号", "政策版本信号", "叙事质量信号"],
        )
        entry = audit_candidate(c)
        assert entry.audit_category == AUDIT_CATEGORY_EVIDENCE_GAP

    def test_many_candidates_aggregation(self):
        candidates = []
        for i in range(50):
            c = _make_candidate(
                symbol=f"{i:06d}.SZ",
                strategy_tags=["VCP"] if i % 3 == 0 else [],
                score=30.0 + i if i % 3 == 0 else 0.0,
                data_completeness=0.75 if i % 3 == 0 else 0.25,
                missing_evidence=[] if i % 3 == 0 else ["事件/新闻数据"],
                risk_flags=["INQUIRY_RISK", "LOCKUP_RISK", "MARGIN_CROWDING_RISK"] if i % 7 == 0 else [],
                risk_penalty=-20.0 if i % 7 == 0 else 0.0,
                game_balance="fragile" if i % 7 == 0 else "",
            )
            candidates.append(c)
        report = build_audit_report(candidates=candidates)
        assert len(report.entries) == 50
        assert report.summary.total_candidates == 50
        assert len(report.summary.by_category) >= 2

    def test_render_large_report(self):
        candidates = [
            _make_candidate(symbol=f"{i:06d}.SZ", strategy_tags=["VCP"], score=30.0, data_completeness=0.75)
            for i in range(100)
        ]
        report = build_audit_report(candidates=candidates)
        text = render_audit_report(report)
        assert "候选误报审计报告" in text

    def test_filtered_with_empty_name(self):
        entry = audit_filtered_symbol(symbol="000001.SZ", reason="无策略命中")
        assert entry.name == ""
        assert entry.filter_subcategory == FILTER_SUBCATEGORY_NO_STRATEGY

    def test_fund_flow_unit_unverified_not_high_confidence(self):
        c = _make_candidate(
            symbol="002415.SZ",
            strategy_tags=["VCP", "FUND_FLOW_ANOMALY"],
            score=40.0,
            data_completeness=0.5,
            need_deep_ta=False,
            missing_evidence=["资金单位校验"],
            fund_flow_unit_verified=False,
        )
        entry = audit_candidate(c)
        assert "资金单位校验" in entry.evidence_gaps
        assert entry.audit_category in {AUDIT_CATEGORY_EVIDENCE_GAP, AUDIT_CATEGORY_HIT_STRATEGY}
        assert entry.need_deep_ta is False

    def test_liquidity_insufficient_not_misjudged_high_priority(self):
        filtered = [{"symbol": "830799.NQ", "reason": "流动性差(avg_amount=0.02亿)"}]
        report = build_audit_report(filtered_symbols=filtered)
        assert report.summary.by_filter_subcategory.get(FILTER_SUBCATEGORY_LIQUIDITY, 0) == 1
        for e in report.entries:
            assert e.audit_category == AUDIT_CATEGORY_FILTERED
            assert e.composite_score == 0.0

    def test_no_strategy_not_misjudged_high_priority(self):
        filtered = [{"symbol": "000001.SZ", "reason": "无策略命中"}]
        report = build_audit_report(filtered_symbols=filtered)
        for e in report.entries:
            assert e.audit_category == AUDIT_CATEGORY_FILTERED
            assert e.need_deep_ta is False
            assert e.composite_score == 0.0

    def test_all_audit_categories_covered(self):
        c1 = _make_candidate(symbol="600519.SH", strategy_tags=["VCP"], score=45.0, composite_score=50.0, priority_rank="B", need_deep_ta=True, data_completeness=0.75)
        c2 = _make_candidate(symbol="300999.SZ", strategy_tags=["VCP"], score=40.0, risk_flags=["INQUIRY_RISK", "LOCKUP_RISK", "MARGIN_CROWDING_RISK"], risk_penalty=-25.0, game_balance="fragile", data_completeness=0.5)
        c3 = _make_candidate(symbol="688981.SH", strategy_tags=["VCP"], score=35.0, data_completeness=0.25, missing_evidence=["事件/新闻数据", "资金流数据"])
        filtered = [{"symbol": "000001.SZ", "reason": "无策略命中"}]
        report = build_audit_report(candidates=[c1, c2, c3], filtered_symbols=filtered)
        categories = set(report.summary.by_category.keys())
        assert categories >= {AUDIT_CATEGORY_HIT_STRATEGY, AUDIT_CATEGORY_RISK_DEMOTED, AUDIT_CATEGORY_EVIDENCE_GAP, AUDIT_CATEGORY_FILTERED}

    def test_sanitized_output(self):
        c = _make_candidate(
            symbol="000001.SZ",
            strategy_tags=["VCP"],
            score=45.0,
            data_completeness=0.75,
        )
        report = build_audit_report(candidates=[c])
        text = render_audit_report(report)
        for word in FORBIDDEN_WORDS:
            assert word not in text
