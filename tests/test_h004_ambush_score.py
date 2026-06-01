# [H-004] mandate_ambush_score — tests
"""Tests for ambush scoring and candidate type classification.

Covers:
1. CandidateType enum values.
2. compute_ambush_score() scoring components.
3. classify_candidate_type() decision logic.
4. route_deep_ta() routing.
5. Integration with Candidate schema (to_db_row / from_db_row).
6. Verification scenarios from TASKS.md:
   - Strong policy + clear company path + no breakout → POLICY_AMBUSH.
   - Pure VCP without policy → TECH_TRADE.
   - Overheated / high-position / risk-crowded → OVERHEATED_AVOID.
   - Daily Plan returns candidate_type and scores.
"""

import pytest
import json

from tradingagents.tradeflow.ambush_score import (
    CandidateType,
    CANDIDATE_TYPE_LABELS,
    compute_ambush_score,
    classify_candidate_type,
    route_deep_ta,
    AmbushScoreResult,
    _compute_mandate_score_component,
    _compute_beneficiary_score_component,
    _compute_pricing_gap_score,
    _compute_overheat_penalty,
)
from tradingagents.tradeflow.schemas import Candidate


# ── CandidateType enum ──

class TestCandidateTypeEnum:
    def test_all_six_types(self):
        expected = {"POLICY_AMBUSH", "POLICY_CONFIRM", "TECH_TRADE",
                    "EVENT_WATCH", "PSEUDO_POLICY", "OVERHEATED_AVOID"}
        actual = {t.value for t in CandidateType}
        assert actual == expected

    def test_labels_exist_for_all(self):
        for t in CandidateType:
            assert t in CANDIDATE_TYPE_LABELS
            assert len(CANDIDATE_TYPE_LABELS[t]) > 0


# ── _compute_mandate_score_component ──

class TestMandateScoreComponent:
    def test_no_policy_evidence(self):
        score, reasons = _compute_mandate_score_component()
        assert score == 0.0
        assert "no_policy_evidence" in reasons

    def test_with_policy_tags(self):
        score, reasons = _compute_mandate_score_component(
            policy_tags=["低空经济"],
            version_score=30.0,
        )
        assert score > 0
        assert any("低空经济" in r or "policy_tags" in r.lower() for r in reasons) is False

    def test_noise_reduction(self):
        score_normal, _ = _compute_mandate_score_component(
            mandate_score=60.0,
            policy_tags=["低空经济"],
        )
        score_noise, reasons = _compute_mandate_score_component(
            mandate_score=60.0,
            policy_tags=["低空经济"],
            is_noise=True,
        )
        assert score_noise < score_normal
        assert any("noise" in r for r in reasons)

    def test_policy_document_bonus(self):
        score_no_doc, _ = _compute_mandate_score_component(
            mandate_score=50.0, policy_tags=["算力"],
        )
        score_doc, _ = _compute_mandate_score_component(
            mandate_score=50.0, policy_tags=["算力"],
            has_policy_document=True,
        )
        assert score_doc >= score_no_doc

    def test_high_authority_bonus(self):
        score_no_ha, _ = _compute_mandate_score_component(
            mandate_score=50.0, policy_tags=["机器人"],
        )
        score_ha, _ = _compute_mandate_score_component(
            mandate_score=50.0, policy_tags=["机器人"],
            has_high_authority=True,
        )
        assert score_ha >= score_no_ha

    def test_multi_day_continuity(self):
        score_1day, _ = _compute_mandate_score_component(
            mandate_score=50.0, policy_tags=["军工"], unique_dates=1,
        )
        score_3day, _ = _compute_mandate_score_component(
            mandate_score=50.0, policy_tags=["军工"], unique_dates=3,
        )
        assert score_3day >= score_1day


# ── _compute_beneficiary_score_component ──

class TestBeneficiaryScoreComponent:
    def test_no_role(self):
        score, reasons = _compute_beneficiary_score_component()
        assert score == 0.0
        assert "no_company_role" in reasons

    def test_unknown_role(self):
        score, reasons = _compute_beneficiary_score_component(company_role="UNKNOWN")
        assert score == 0.0

    def test_leader_role(self):
        score, reasons = _compute_beneficiary_score_component(
            company_role="LEADER",
            beneficiary_path=["整机", "动力系统"],
            has_company_evidence=True,
        )
        assert score > 0
        assert "LEADER" in str(reasons)

    def test_concept_only_role(self):
        score, reasons = _compute_beneficiary_score_component(
            company_role="CONCEPT_ONLY",
            beneficiary_path=["应用场景"],
        )
        assert score > 0
        assert score < 30

    def test_no_company_evidence_reduces(self):
        score_with, _ = _compute_beneficiary_score_component(
            company_role="CORE_SUPPLIER",
            beneficiary_path=["减速器"],
            has_company_evidence=True,
        )
        score_without, _ = _compute_beneficiary_score_component(
            company_role="CORE_SUPPLIER",
            beneficiary_path=["减速器"],
            has_company_evidence=False,
        )
        assert score_without < score_with

    def test_path_confidence_increases(self):
        score_low, _ = _compute_beneficiary_score_component(
            company_role="LEADER",
            beneficiary_path=["整机"],
            has_company_evidence=True,
            path_confidence=0.3,
        )
        score_high, _ = _compute_beneficiary_score_component(
            company_role="LEADER",
            beneficiary_path=["整机"],
            has_company_evidence=True,
            path_confidence=0.9,
        )
        assert score_high >= score_low


# ── _compute_pricing_gap_score ──

class TestPricingGapScore:
    def test_no_tech_signal(self):
        score, reasons = _compute_pricing_gap_score()
        assert score == 0.0
        assert "no_tech_signal" in reasons

    def test_breakout_confirmed(self):
        score, reasons = _compute_pricing_gap_score(
            has_tech_breakout=True,
            trigger_price=10.0,
            current_price=10.5,
        )
        assert score >= 80
        assert "breakout_confirmed" in reasons

    def test_vcp_near_trigger(self):
        score, reasons = _compute_pricing_gap_score(
            trigger_price=10.0,
            current_price=9.8,
            strategy_tags=["VCP"],
        )
        assert score >= 50
        assert any("near_trigger" in r or "approaching" in r for r in reasons)

    def test_far_from_trigger(self):
        score, reasons = _compute_pricing_gap_score(
            trigger_price=10.0,
            current_price=8.5,
            strategy_tags=["VCP"],
        )
        assert score < 55

    def test_no_price_context(self):
        score, reasons = _compute_pricing_gap_score(
            strategy_tags=["PULLBACK_SUPPORT"],
        )
        assert score > 0


# ── _compute_overheat_penalty ──

class TestOverheatPenalty:
    def test_no_risk(self):
        penalty, reasons = _compute_overheat_penalty()
        assert penalty == 0.0

    def test_lhb_overheat(self):
        penalty, reasons = _compute_overheat_penalty(
            risk_flags=["LHB_OVERHEAT_RISK"],
        )
        assert penalty >= 25
        assert any("LHB" in r for r in reasons)

    def test_margin_crowding(self):
        penalty, reasons = _compute_overheat_penalty(
            risk_flags=["MARGIN_CROWDING_RISK"],
        )
        assert penalty >= 20

    def test_net_outflow(self):
        penalty, reasons = _compute_overheat_penalty(
            fund_flow_anomaly_tags=["NET_OUTFLOW_DOMINANT"],
        )
        assert penalty >= 15

    def test_crowded_game_balance(self):
        penalty, reasons = _compute_overheat_penalty(
            game_balance="crowded",
        )
        assert penalty >= 20

    def test_fragile_game_balance(self):
        penalty, reasons = _compute_overheat_penalty(
            game_balance="fragile",
        )
        assert penalty >= 25

    def test_many_risk_flags(self):
        penalty, reasons = _compute_overheat_penalty(
            risk_flags=["LOCKUP_RISK", "INQUIRY_RISK", "BUYBACK_EVENT"],
        )
        assert penalty >= 10

    def test_heavy_risk_penalty(self):
        penalty, reasons = _compute_overheat_penalty(
            risk_penalty=-25,
        )
        assert penalty > 0


# ── compute_ambush_score ──

class TestComputeAmbushScore:
    def test_returns_result(self):
        result = compute_ambush_score()
        assert isinstance(result, AmbushScoreResult)
        assert result.ambush_score >= 0

    def test_to_dict(self):
        result = compute_ambush_score(policy_tags=["低空经济"], mandate_score=50)
        d = result.to_dict()
        assert "ambush_score" in d
        assert "candidate_type" in d
        assert "deep_ta_route" in d

    def test_ambush_score_bounded(self):
        result = compute_ambush_score(
            mandate_score=100,
            version_score=100,
            policy_tags=["算力"],
            company_role="LEADER",
            beneficiary_path=["芯片", "服务器"],
            has_company_evidence=True,
            has_tech_breakout=True,
            risk_penalty=0,
        )
        assert 0 <= result.ambush_score <= 100


# ── classify_candidate_type ──

class TestClassifyCandidateType:
    """Verification scenarios from TASKS.md H-004."""

    def test_policy_ambush_strong_policy_no_breakout(self):
        """政策强、公司路径明确、技术未突破 → POLICY_AMBUSH."""
        ct, reason = classify_candidate_type(
            ambush_score=70,
            mandate_score_component=60,
            beneficiary_score_component=55,
            pricing_gap_score=20,
            overheat_penalty=5,
            has_policy=True,
            has_beneficiary_path=True,
            has_tech_breakout=False,
            is_overheated=False,
        )
        assert ct == CandidateType.POLICY_AMBUSH
        assert "左侧" in reason or "ambush" in reason.lower() or "mandate" in reason.lower()

    def test_policy_confirm_with_breakout(self):
        """政策强 + 受益路径 + 已突破 → POLICY_CONFIRM."""
        ct, reason = classify_candidate_type(
            ambush_score=80,
            mandate_score_component=60,
            beneficiary_score_component=55,
            pricing_gap_score=85,
            overheat_penalty=3,
            has_policy=True,
            has_beneficiary_path=True,
            has_tech_breakout=True,
            is_overheated=False,
        )
        assert ct == CandidateType.POLICY_CONFIRM

    def test_tech_trade_no_policy(self):
        """纯 VCP 无政策证据 → TECH_TRADE."""
        ct, reason = classify_candidate_type(
            ambush_score=30,
            mandate_score_component=0,
            beneficiary_score_component=0,
            pricing_gap_score=70,
            overheat_penalty=0,
            has_policy=False,
            has_beneficiary_path=False,
            has_tech_breakout=True,
            is_overheated=False,
        )
        assert ct == CandidateType.TECH_TRADE

    def test_overheated_avoid(self):
        """过热/高位/风险拥挤 → OVERHEATED_AVOID."""
        ct, reason = classify_candidate_type(
            ambush_score=10,
            mandate_score_component=50,
            beneficiary_score_component=40,
            pricing_gap_score=80,
            overheat_penalty=45,
            has_policy=True,
            has_beneficiary_path=True,
            has_tech_breakout=True,
            is_overheated=True,
        )
        assert ct == CandidateType.OVERHEATED_AVOID

    def test_overheated_high_risk_flags(self):
        """INQUIRY_RISK → OVERHEATED_AVOID."""
        ct, reason = classify_candidate_type(
            is_overheated=False,
            risk_flags=["INQUIRY_RISK"],
        )
        assert ct == CandidateType.OVERHEATED_AVOID
        assert "INQUIRY_RISK" in reason

    def test_overheated_financial_risk(self):
        """FINANCIAL_QUALITY_RISK → OVERHEATED_AVOID."""
        ct, _ = classify_candidate_type(
            is_overheated=False,
            risk_flags=["FINANCIAL_QUALITY_RISK"],
        )
        assert ct == CandidateType.OVERHEATED_AVOID

    def test_pseudo_policy_weak_evidence(self):
        """有政策信号但无公司受益路径 → PSEUDO_POLICY."""
        ct, reason = classify_candidate_type(
            ambush_score=25,
            mandate_score_component=40,
            beneficiary_score_component=0,
            pricing_gap_score=10,
            overheat_penalty=0,
            has_policy=True,
            has_beneficiary_path=False,
            has_tech_breakout=False,
            is_overheated=False,
        )
        assert ct == CandidateType.PSEUDO_POLICY

    def test_event_watch_weak_policy(self):
        """弱政策 + 无受益路径 → EVENT_WATCH."""
        ct, reason = classify_candidate_type(
            ambush_score=15,
            mandate_score_component=22,
            beneficiary_score_component=0,
            pricing_gap_score=5,
            overheat_penalty=0,
            has_policy=True,
            has_beneficiary_path=False,
            has_tech_breakout=False,
            is_overheated=False,
        )
        assert ct == CandidateType.EVENT_WATCH

    def test_fragile_game_balance_overheated(self):
        """fragile game balance → OVERHEATED_AVOID."""
        ct, reason = classify_candidate_type(
            is_overheated=False,
            game_balance="fragile",
        )
        assert ct == CandidateType.OVERHEATED_AVOID

    def test_concept_only_role_not_ambush(self):
        """CONCEPT_ONLY company_role → no beneficiary path for ambush."""
        ct, reason = classify_candidate_type(
            ambush_score=40,
            mandate_score_component=50,
            beneficiary_score_component=15,
            pricing_gap_score=20,
            overheat_penalty=0,
            has_policy=True,
            has_beneficiary_path=False,
            has_tech_breakout=False,
            is_overheated=False,
        )
        assert ct != CandidateType.POLICY_AMBUSH


# ── route_deep_ta ──

class TestRouteDeepTa:
    def test_policy_ambush_routes_to_policy_verify(self):
        route, reason = route_deep_ta(CandidateType.POLICY_AMBUSH)
        assert route == "policy_verify"
        assert "验证" in reason or "政策" in reason

    def test_policy_confirm_routes_to_deep_ta(self):
        route, reason = route_deep_ta(
            CandidateType.POLICY_CONFIRM,
            has_beneficiary_path=True,
        )
        assert route == "deep_ta"

    def test_policy_confirm_no_path_routes_to_policy_verify(self):
        route, reason = route_deep_ta(
            CandidateType.POLICY_CONFIRM,
            has_beneficiary_path=False,
        )
        assert route == "policy_verify"

    def test_tech_trade_routes_to_short_term(self):
        route, reason = route_deep_ta(CandidateType.TECH_TRADE)
        assert route == "short_term"

    def test_overheated_routes_to_skip(self):
        route, reason = route_deep_ta(CandidateType.OVERHEATED_AVOID)
        assert route == "skip"

    def test_pseudo_policy_routes_to_skip(self):
        route, reason = route_deep_ta(CandidateType.PSEUDO_POLICY)
        assert route == "skip"

    def test_event_watch_low_score_routes_to_skip(self):
        route, reason = route_deep_ta(
            CandidateType.EVENT_WATCH,
            ambush_score=10,
        )
        assert route == "skip"

    def test_event_watch_moderate_score_routes_to_observe(self):
        route, reason = route_deep_ta(
            CandidateType.EVENT_WATCH,
            ambush_score=40,
        )
        assert route == "observe"


# ── Integration with Candidate schema ──

class TestCandidateSchemaIntegration:
    def test_candidate_h004_fields_default(self):
        c = Candidate(symbol="000001.SZ")
        assert c.candidate_type == ""
        assert c.ambush_score == 0.0
        assert c.mandate_score_component == 0.0
        assert c.beneficiary_score_component == 0.0
        assert c.pricing_gap_score == 0.0
        assert c.overheat_penalty == 0.0
        assert c.candidate_type_reason == ""
        assert c.deep_ta_route == ""
        assert c.deep_ta_route_reason == ""
        assert c.ambush_reasons == []
        assert c.ambush_evidence_refs == []

    def test_to_db_row_includes_h004(self):
        c = Candidate(
            symbol="000001.SZ",
            candidate_type="POLICY_AMBUSH",
            ambush_score=72.5,
            mandate_score_component=60.0,
            beneficiary_score_component=55.0,
            pricing_gap_score=20.0,
            overheat_penalty=5.0,
            candidate_type_reason="test",
            deep_ta_route="policy_verify",
            deep_ta_route_reason="test route",
            ambush_reasons=["reason1"],
            ambush_evidence_refs=[{"title": "test"}],
        )
        row = c.to_db_row()
        assert row["candidate_type"] == "POLICY_AMBUSH"
        assert row["ambush_score"] == 72.5
        assert row["mandate_score_component"] == 60.0
        assert row["beneficiary_score_component"] == 55.0
        assert row["pricing_gap_score"] == 20.0
        assert row["overheat_penalty"] == 5.0
        assert row["candidate_type_reason"] == "test"
        assert row["deep_ta_route"] == "policy_verify"
        assert row["deep_ta_route_reason"] == "test route"
        assert json.loads(row["ambush_reasons_json"]) == ["reason1"]
        assert json.loads(row["ambush_evidence_refs_json"]) == [{"title": "test"}]

    def test_from_db_row_roundtrip(self):
        original = Candidate(
            symbol="600000.SH",
            candidate_type="POLICY_CONFIRM",
            ambush_score=85.3,
            mandate_score_component=70.0,
            beneficiary_score_component=60.0,
            pricing_gap_score=80.0,
            overheat_penalty=3.0,
            candidate_type_reason="政策右侧确认",
            deep_ta_route="deep_ta",
            deep_ta_route_reason="可进入TA深度分析",
            ambush_reasons=["has_policy_document:加成", "has_company_evidence"],
            ambush_evidence_refs=[{"title": "国务院关于印发低空经济行动计划"}],
        )
        row = original.to_db_row()
        restored = Candidate.from_db_row(row)

        assert restored.candidate_type == "POLICY_CONFIRM"
        assert restored.ambush_score == 85.3
        assert restored.mandate_score_component == 70.0
        assert restored.beneficiary_score_component == 60.0
        assert restored.pricing_gap_score == 80.0
        assert restored.overheat_penalty == 3.0
        assert restored.candidate_type_reason == "政策右侧确认"
        assert restored.deep_ta_route == "deep_ta"
        assert restored.deep_ta_route_reason == "可进入TA深度分析"
        assert "has_policy_document:加成" in restored.ambush_reasons
        assert len(restored.ambush_evidence_refs) == 1

    def test_from_db_row_missing_columns_graceful(self):
        """Old DB rows without H-004 columns should not crash."""
        row = {
            "symbol": "000001.SZ",
            "name": "平安银行",
            "source": "manual",
            "strategy_tags_json": "[]",
            "primary_strategy": "",
            "score": 0.0,
            "status": "active",
            "trigger_price": None,
            "support_price": None,
            "invalid_price": None,
            "need_deep_ta": 0,
            "evidence_json": "{}",
            "risk_flags_json": "[]",
            "trade_date": "2026-06-01",
            "created_at": "",
            "updated_at": "",
        }
        c = Candidate.from_db_row(row)
        assert c.candidate_type == ""
        assert c.ambush_score == 0.0
        assert c.ambush_reasons == []


# ── Full pipeline scenarios ──

class TestFullPipelineScenarios:
    """End-to-end scenarios from H-004 acceptance criteria."""

    def test_scenario_1_policy_ambush(self):
        """政策强、公司路径明确、技术未突破 → POLICY_AMBUSH."""
        result = compute_ambush_score(
            mandate_score=65.0,
            version_score=40.0,
            policy_tags=["低空经济"],
            has_policy_document=True,
            has_high_authority=True,
            unique_dates=3,
            company_role="CORE_SUPPLIER",
            beneficiary_path=["整机", "动力系统"],
            has_company_evidence=True,
            path_confidence=0.8,
            has_tech_breakout=False,
            trigger_price=25.0,
            current_price=22.5,
            strategy_tags=["EVENT_CATALYST"],
            tech_score=15.0,
            risk_flags=[],
            risk_penalty=0.0,
            game_balance="favorable",
            fund_flow_anomaly_tags=[],
            narrative_score=20.0,
        )
        assert result.candidate_type == "POLICY_AMBUSH"
        assert result.ambush_score > 40
        assert result.deep_ta_route == "policy_verify"
        assert result.mandate_score_component > 0
        assert result.beneficiary_score_component > 0

    def test_scenario_2_policy_confirm(self):
        """政策强 + 受益路径 + 技术已突破 → POLICY_CONFIRM."""
        result = compute_ambush_score(
            mandate_score=65.0,
            version_score=40.0,
            policy_tags=["算力"],
            has_policy_document=True,
            has_high_authority=True,
            unique_dates=4,
            company_role="LEADER",
            beneficiary_path=["芯片", "服务器"],
            has_company_evidence=True,
            path_confidence=0.9,
            has_tech_breakout=True,
            trigger_price=50.0,
            current_price=51.0,
            strategy_tags=["VCP"],
            tech_score=35.0,
            risk_flags=[],
            risk_penalty=0.0,
            game_balance="favorable",
            fund_flow_anomaly_tags=["NET_INFLOW_ANOMALY"],
            narrative_score=25.0,
        )
        assert result.candidate_type == "POLICY_CONFIRM"
        assert result.deep_ta_route == "deep_ta"
        assert result.ambush_score > 50

    def test_scenario_3_tech_trade(self):
        """纯 VCP 无政策证据 → TECH_TRADE."""
        result = compute_ambush_score(
            mandate_score=0,
            version_score=0,
            policy_tags=[],
            company_role="",
            beneficiary_path=[],
            has_tech_breakout=True,
            trigger_price=15.0,
            current_price=15.2,
            strategy_tags=["VCP"],
            tech_score=30.0,
            risk_flags=[],
            risk_penalty=0.0,
            game_balance="neutral",
        )
        assert result.candidate_type == "TECH_TRADE"
        assert result.deep_ta_route == "short_term"

    def test_scenario_4_overheated_avoid(self):
        """过热/高位/风险拥挤 → OVERHEATED_AVOID."""
        result = compute_ambush_score(
            mandate_score=50.0,
            version_score=30.0,
            policy_tags=["机器人"],
            company_role="LEADER",
            beneficiary_path=["本体"],
            has_company_evidence=True,
            has_tech_breakout=True,
            trigger_price=100.0,
            current_price=105.0,
            strategy_tags=["VCP"],
            tech_score=40.0,
            risk_flags=["LHB_OVERHEAT_RISK", "MARGIN_CROWDING_RISK"],
            risk_penalty=-20.0,
            game_balance="crowded",
            fund_flow_anomaly_tags=["NET_OUTFLOW_DOMINANT"],
            narrative_score=50.0,
        )
        assert result.candidate_type == "OVERHEATED_AVOID"
        assert result.deep_ta_route == "skip"
        assert result.overheat_penalty > 30

    def test_scenario_5_pseudo_policy(self):
        """有政策但无受益路径 → PSEUDO_POLICY."""
        result = compute_ambush_score(
            mandate_score=40.0,
            version_score=25.0,
            policy_tags=["AI应用"],
            company_role="CONCEPT_ONLY",
            beneficiary_path=["应用场景"],
            has_company_evidence=False,
            path_confidence=0.2,
            has_tech_breakout=False,
            strategy_tags=["EVENT_CATALYST"],
            tech_score=10.0,
            risk_flags=[],
            risk_penalty=0.0,
            game_balance="neutral",
        )
        assert result.candidate_type == "PSEUDO_POLICY"
        assert result.deep_ta_route == "skip"

    def test_scenario_6_event_watch(self):
        """弱政策，无受益路径，无技术 → EVENT_WATCH."""
        result = compute_ambush_score(
            mandate_score=10.0,
            version_score=8.0,
            policy_tags=["出海"],
            company_role="UNKNOWN",
            beneficiary_path=[],
            has_company_evidence=False,
            has_tech_breakout=False,
            strategy_tags=[],
            tech_score=5.0,
            risk_flags=[],
            risk_penalty=0.0,
            game_balance="neutral",
        )
        assert result.candidate_type == "EVENT_WATCH"

    def test_high_scores_have_evidence(self):
        """All high ambush scores must have evidence refs."""
        result = compute_ambush_score(
            mandate_score=80.0,
            version_score=50.0,
            policy_tags=["半导体"],
            has_policy_document=True,
            has_high_authority=True,
            company_role="LEADER",
            beneficiary_path=["设计", "制造"],
            has_company_evidence=True,
            has_tech_breakout=False,
            strategy_tags=["EVENT_CATALYST"],
            tech_score=20.0,
            mandate_evidence_refs=[
                {"title": "国务院关于印发半导体产业规划", "source_level": "STATE_COUNCIL", "date": "2026-05-28"},
                {"title": "工信部推进国产替代政策", "source_level": "MINISTRY", "date": "2026-05-30"},
            ],
        )
        if result.ambush_score > 50:
            assert len(result.ambush_evidence_refs) > 0


# ── DailyPlan render includes H-004 ──

class TestDailyPlanRender:
    def test_render_includes_candidate_type(self):
        from tradingagents.tradeflow.schemas import DailyPlan

        plan = DailyPlan(
            trade_date="2026-06-02",
            summary="测试",
            candidates=[{
                "symbol": "000001.SZ",
                "name": "平安银行",
                "action": "OBSERVE",
                "candidate_type": "POLICY_AMBUSH",
                "ambush_score": 72.5,
                "mandate_score_component": 60.0,
                "beneficiary_score_component": 55.0,
                "pricing_gap_score": 20.0,
                "overheat_penalty": 5.0,
                "candidate_type_reason": "政策左侧埋伏",
                "deep_ta_route": "policy_verify",
                "deep_ta_route_reason": "中线研究/政策验证",
            }],
        )
        text = plan.render_text()
        assert "POLICY_AMBUSH" in text or "政策左侧埋伏" in text
        assert "72" in text
