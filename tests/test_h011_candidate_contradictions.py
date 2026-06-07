# [H-011] candidate_contradiction_explainer — tests
"""Tests for the candidate contradiction aggregator.

Verifies that cross-dimension contradictions are correctly identified
across policy/company, theme/funding, fundamental/theme, tech/thesis,
data/score, and risk/narrative dimensions.
"""

import json
import pytest
import sqlite3
import tempfile
import os

from tradingagents.tradeflow.candidate_contradictions import (
    CONTRADICTION_NONE,
    CONTRADICTION_LOW,
    CONTRADICTION_MEDIUM,
    CONTRADICTION_HIGH,
    ALL_CONTRADICTION_LEVELS,
    ALL_CONTRADICTION_TYPES,
    CONTRADICTION_LABELS,
    CONTRADICTION_TYPE_POLICY_COMPANY,
    CONTRADICTION_TYPE_THEME_FUNDING,
    CONTRADICTION_TYPE_FUNDAMENTAL_THEME,
    CONTRADICTION_TYPE_TECH_THESIS,
    CONTRADICTION_TYPE_DATA_SCORE,
    CONTRADICTION_TYPE_RISK_NARRATIVE,
    ContradictionItem,
    ContradictionResult,
    _check_policy_vs_company,
    _check_theme_vs_funding,
    _check_fundamental_vs_theme,
    _check_tech_vs_thesis,
    _check_data_vs_score,
    _check_risk_vs_narrative,
    _classify_contradiction_level,
    _build_blocking_gaps,
    _build_verification_steps,
    evaluate_contradictions,
    render_contradiction_summary,
)
from tradingagents.tradeflow.schemas import Candidate


# ── ContradictionItem ──

class TestContradictionItem:
    def test_default(self):
        item = ContradictionItem()
        assert item.contradiction_type == ""
        assert item.severity == 0.0

    def test_to_dict(self):
        item = ContradictionItem(
            contradiction_type=CONTRADICTION_TYPE_POLICY_COMPANY,
            dimension_a="政策强",
            dimension_b="公司弱",
            description="政策强但公司弱",
            severity=0.8,
        )
        d = item.to_dict()
        assert d["contradiction_type"] == CONTRADICTION_TYPE_POLICY_COMPANY
        assert d["severity"] == 0.8
        assert d["dimension_a"] == "政策强"

    def test_severity_rounded(self):
        item = ContradictionItem(severity=0.12345)
        assert item.to_dict()["severity"] == 0.12


# ── ContradictionResult ──

class TestContradictionResult:
    def test_default(self):
        r = ContradictionResult()
        assert r.contradiction_level == CONTRADICTION_NONE
        assert r.contradiction_items == []
        assert r.blocking_evidence_gaps == []
        assert r.next_verification_steps == []

    def test_to_dict(self):
        r = ContradictionResult(
            contradiction_level=CONTRADICTION_HIGH,
            contradiction_items=[{"type": "x"}],
            blocking_evidence_gaps=["gap1"],
            next_verification_steps=["step1"],
        )
        d = r.to_dict()
        assert d["contradiction_level"] == CONTRADICTION_HIGH
        assert len(d["contradiction_items"]) == 1
        assert "_items_raw" not in d

    def test_to_dict_empty(self):
        r = ContradictionResult()
        d = r.to_dict()
        assert d["contradiction_level"] == CONTRADICTION_NONE
        assert d["contradiction_items"] == []


# ── Constants ──

class TestConstants:
    def test_levels(self):
        assert ALL_CONTRADICTION_LEVELS == [CONTRADICTION_NONE, CONTRADICTION_LOW, CONTRADICTION_MEDIUM, CONTRADICTION_HIGH]

    def test_types_count(self):
        assert len(ALL_CONTRADICTION_TYPES) == 6

    def test_labels_match(self):
        for t in ALL_CONTRADICTION_TYPES:
            assert t in CONTRADICTION_LABELS


# ── Rule: policy_vs_company ──

class TestPolicyVsCompany:
    def test_strong_policy_weak_company_unknown(self):
        item = _check_policy_vs_company(
            policy_tags=["半导体"],
            version_score=30,
            has_policy_document=True,
            company_role="UNKNOWN",
        )
        assert item is not None
        assert item.contradiction_type == CONTRADICTION_TYPE_POLICY_COMPANY
        assert item.severity >= 0.5

    def test_strong_policy_concept_only(self):
        item = _check_policy_vs_company(
            policy_tags=["AI"],
            version_score=30,
            has_policy_document=True,
            company_role="CONCEPT_ONLY",
        )
        assert item is not None
        assert item.severity >= 0.5

    def test_no_policy_returns_none(self):
        item = _check_policy_vs_company(
            company_role="UNKNOWN",
        )
        assert item is None

    def test_strong_company_returns_none(self):
        item = _check_policy_vs_company(
            policy_tags=["半导体"],
            version_score=30,
            company_role="LEADER",
            beneficiary_path=["芯片"],
            has_company_evidence=True,
        )
        assert item is None

    def test_no_path_no_evidence(self):
        item = _check_policy_vs_company(
            policy_tags=["半导体"],
            version_score=20,
        )
        assert item is not None

    def test_mandate_score_threshold(self):
        item = _check_policy_vs_company(
            mandate_score_component=25,
            company_role="",
        )
        assert item is not None


# ── Rule: theme_vs_funding ──

class TestThemeVsFunding:
    def test_theme_hot_funding_cold(self):
        item = _check_theme_vs_funding(
            narrative_score=40,
            fund_flow_anomaly_score=-5,
            fund_flow_anomaly_tags=["NET_OUTFLOW_DOMINANT"],
        )
        assert item is not None
        assert item.contradiction_type == CONTRADICTION_TYPE_THEME_FUNDING

    def test_mandate_hot_funding_cold(self):
        item = _check_theme_vs_funding(
            mandate_score_component=35,
            fund_flow_anomaly_score=-2,
        )
        assert item is not None

    def test_no_theme_returns_none(self):
        item = _check_theme_vs_funding(
            fund_flow_anomaly_score=-10,
        )
        assert item is None

    def test_funding_positive_returns_none(self):
        item = _check_theme_vs_funding(
            narrative_score=50,
            fund_flow_anomaly_score=20,
        )
        assert item is None

    def test_net_outflow_high_severity(self):
        item = _check_theme_vs_funding(
            narrative_score=50,
            fund_flow_anomaly_score=0,
            fund_flow_anomaly_tags=["NET_OUTFLOW_DOMINANT"],
        )
        assert item is not None
        assert item.severity >= 0.8


# ── Rule: fundamental_vs_theme ──

class TestFundamentalVsTheme:
    def test_financial_risk_strong_theme(self):
        item = _check_fundamental_vs_theme(
            risk_flags=["FINANCIAL_QUALITY_RISK"],
            narrative_score=50,
            mandate_score_component=45,
        )
        assert item is not None
        assert item.contradiction_type == CONTRADICTION_TYPE_FUNDAMENTAL_THEME
        assert item.severity >= 0.9

    def test_inquiry_risk_strong_theme(self):
        item = _check_fundamental_vs_theme(
            risk_flags=["INQUIRY_RISK"],
            narrative_score=40,
        )
        assert item is not None

    def test_no_risk_returns_none(self):
        item = _check_fundamental_vs_theme(
            risk_penalty=0,
            narrative_score=50,
        )
        assert item is None

    def test_weak_theme_returns_none(self):
        item = _check_fundamental_vs_theme(
            risk_penalty=-20,
            narrative_score=10,
        )
        assert item is None

    def test_high_penalty_moderate_theme(self):
        item = _check_fundamental_vs_theme(
            risk_penalty=-30,
            mandate_score_component=50,
        )
        assert item is not None
        assert item.severity >= 0.8


# ── Rule: tech_vs_thesis ──

class TestTechVsThesis:
    def test_crowded_policy_ambush(self):
        item = _check_tech_vs_thesis(
            game_balance="crowded",
            candidate_type="POLICY_AMBUSH",
            mandate_score_component=30,
        )
        assert item is not None
        assert item.contradiction_type == CONTRADICTION_TYPE_TECH_THESIS

    def test_fragile_policy_confirm(self):
        item = _check_tech_vs_thesis(
            game_balance="fragile",
            candidate_type="POLICY_CONFIRM",
            mandate_score_component=30,
        )
        assert item is not None

    def test_overheat_penalty(self):
        item = _check_tech_vs_thesis(
            overheat_penalty=30,
            candidate_type="POLICY_AMBUSH",
            mandate_score_component=30,
        )
        assert item is not None

    def test_tech_trade_returns_none(self):
        item = _check_tech_vs_thesis(
            game_balance="crowded",
            candidate_type="TECH_TRADE",
            mandate_score_component=30,
        )
        assert item is None

    def test_no_thesis_returns_none(self):
        item = _check_tech_vs_thesis(
            game_balance="crowded",
            candidate_type="POLICY_AMBUSH",
            mandate_score_component=10,
        )
        assert item is None


# ── Rule: data_vs_score ──

class TestDataVsScore:
    def test_low_completeness_high_ambush(self):
        item = _check_data_vs_score(
            data_completeness=0.3,
            ambush_score=60,
        )
        assert item is not None
        assert item.contradiction_type == CONTRADICTION_TYPE_DATA_SCORE

    def test_low_completeness_high_composite(self):
        item = _check_data_vs_score(
            tradeflow_data_completeness=0.2,
            composite_score=70,
        )
        assert item is not None

    def test_good_completeness_returns_none(self):
        item = _check_data_vs_score(
            data_completeness=0.6,
            ambush_score=60,
        )
        assert item is None

    def test_low_score_returns_none(self):
        item = _check_data_vs_score(
            data_completeness=0.2,
            ambush_score=30,
        )
        assert item is None

    def test_very_low_completeness_high_severity(self):
        item = _check_data_vs_score(
            data_completeness=0.1,
            ambush_score=80,
        )
        assert item is not None
        assert item.severity >= 0.9


# ── Rule: risk_vs_narrative ──

class TestRiskVsNarrative:
    def test_inquiry_risk_bullish(self):
        item = _check_risk_vs_narrative(
            risk_flags=["INQUIRY_RISK"],
            narrative_score=40,
        )
        assert item is not None
        assert item.contradiction_type == CONTRADICTION_TYPE_RISK_NARRATIVE

    def test_lockup_risk_bull_case(self):
        item = _check_risk_vs_narrative(
            risk_flags=["LOCKUP_RISK"],
            bull_case="政策催化明确",
        )
        assert item is not None

    def test_no_risk_returns_none(self):
        item = _check_risk_vs_narrative(
            narrative_score=50,
        )
        assert item is None

    def test_no_narrative_returns_none(self):
        item = _check_risk_vs_narrative(
            risk_flags=["FINANCIAL_QUALITY_RISK"],
        )
        assert item is None


# ── classify_contradiction_level ──

class TestClassifyLevel:
    def test_empty(self):
        assert _classify_contradiction_level([]) == CONTRADICTION_NONE

    def test_single_low(self):
        items = [ContradictionItem(severity=0.3)]
        assert _classify_contradiction_level(items) == CONTRADICTION_LOW

    def test_single_medium(self):
        items = [ContradictionItem(severity=0.6)]
        assert _classify_contradiction_level(items) == CONTRADICTION_MEDIUM

    def test_single_high(self):
        items = [ContradictionItem(severity=0.9)]
        assert _classify_contradiction_level(items) == CONTRADICTION_HIGH

    def test_count_four(self):
        items = [ContradictionItem(severity=0.3)] * 4
        assert _classify_contradiction_level(items) == CONTRADICTION_HIGH

    def test_count_three(self):
        items = [ContradictionItem(severity=0.3)] * 3
        assert _classify_contradiction_level(items) == CONTRADICTION_MEDIUM


# ── build_blocking_gaps ──

class TestBuildBlockingGaps:
    def test_policy_company_gaps(self):
        items = [ContradictionItem(contradiction_type=CONTRADICTION_TYPE_POLICY_COMPANY)]
        gaps = _build_blocking_gaps(items, company_role="")
        assert any("角色" in g for g in gaps)
        assert any("受益路径" in g for g in gaps)

    def test_theme_funding_gaps(self):
        items = [ContradictionItem(contradiction_type=CONTRADICTION_TYPE_THEME_FUNDING)]
        gaps = _build_blocking_gaps(items)
        assert any("资金" in g for g in gaps)

    def test_fundamental_theme_gaps(self):
        items = [ContradictionItem(contradiction_type=CONTRADICTION_TYPE_FUNDAMENTAL_THEME)]
        gaps = _build_blocking_gaps(items)
        assert any("基本面" in g or "财务" in g for g in gaps)

    def test_tech_thesis_gaps(self):
        items = [ContradictionItem(contradiction_type=CONTRADICTION_TYPE_TECH_THESIS)]
        gaps = _build_blocking_gaps(items)
        assert any("技术" in g or "破位" in g for g in gaps)

    def test_data_score_gaps(self):
        items = [ContradictionItem(contradiction_type=CONTRADICTION_TYPE_DATA_SCORE)]
        gaps = _build_blocking_gaps(items, data_completeness=0.2)
        assert any("完整度" in g or "数据" in g for g in gaps)

    def test_risk_narrative_gaps(self):
        items = [ContradictionItem(contradiction_type=CONTRADICTION_TYPE_RISK_NARRATIVE)]
        gaps = _build_blocking_gaps(items)
        assert any("风险" in g for g in gaps)

    def test_empty_items(self):
        gaps = _build_blocking_gaps([])
        assert gaps == []

    def test_no_duplicates(self):
        items = [ContradictionItem(contradiction_type=CONTRADICTION_TYPE_POLICY_COMPANY)]
        gaps = _build_blocking_gaps(items)
        assert len(gaps) == len(set(gaps))


# ── build_verification_steps ──

class TestBuildVerificationSteps:
    def test_policy_company_steps(self):
        items = [ContradictionItem(contradiction_type=CONTRADICTION_TYPE_POLICY_COMPANY)]
        steps = _build_verification_steps(items, mandate_topic="半导体")
        assert any("半导体" in s for s in steps)

    def test_theme_funding_steps(self):
        items = [ContradictionItem(contradiction_type=CONTRADICTION_TYPE_THEME_FUNDING)]
        steps = _build_verification_steps(items)
        assert any("资金" in s or "龙虎榜" in s for s in steps)

    def test_fundamental_theme_steps(self):
        items = [ContradictionItem(contradiction_type=CONTRADICTION_TYPE_FUNDAMENTAL_THEME)]
        steps = _build_verification_steps(items)
        assert any("财报" in s or "基本面" in s for s in steps)

    def test_tech_thesis_policy_ambush(self):
        items = [ContradictionItem(contradiction_type=CONTRADICTION_TYPE_TECH_THESIS)]
        steps = _build_verification_steps(items, candidate_type="POLICY_AMBUSH")
        assert any("洗盘" in s or "逻辑" in s for s in steps)

    def test_tech_thesis_other(self):
        items = [ContradictionItem(contradiction_type=CONTRADICTION_TYPE_TECH_THESIS)]
        steps = _build_verification_steps(items, candidate_type="TECH_TRADE")
        assert any("不宜加仓" in s for s in steps)

    def test_data_score_steps(self):
        items = [ContradictionItem(contradiction_type=CONTRADICTION_TYPE_DATA_SCORE)]
        steps = _build_verification_steps(items)
        assert any("补全" in s or "数据" in s for s in steps)

    def test_risk_narrative_steps(self):
        items = [ContradictionItem(contradiction_type=CONTRADICTION_TYPE_RISK_NARRATIVE)]
        steps = _build_verification_steps(items)
        assert any("风险" in s or "定价" in s for s in steps)

    def test_empty_items(self):
        steps = _build_verification_steps([])
        assert steps == []


# ── evaluate_contradictions (integration) ──

class TestEvaluateContradictions:
    def test_no_contradictions(self):
        result = evaluate_contradictions(
            policy_tags=["半导体"],
            company_role="LEADER",
            beneficiary_path=["芯片设计"],
            has_company_evidence=True,
            narrative_score=20,
            fund_flow_anomaly_score=15,
        )
        assert result.contradiction_level == CONTRADICTION_NONE
        assert result.contradiction_items == []

    def test_policy_vs_company(self):
        result = evaluate_contradictions(
            policy_tags=["半导体"],
            version_score=30,
            has_policy_document=True,
            company_role="UNKNOWN",
        )
        assert result.contradiction_level != CONTRADICTION_NONE
        types = [item["contradiction_type"] for item in result.contradiction_items]
        assert CONTRADICTION_TYPE_POLICY_COMPANY in types

    def test_theme_vs_funding(self):
        result = evaluate_contradictions(
            narrative_score=50,
            fund_flow_anomaly_score=-5,
            fund_flow_anomaly_tags=["NET_OUTFLOW_DOMINANT"],
        )
        types = [item["contradiction_type"] for item in result.contradiction_items]
        assert CONTRADICTION_TYPE_THEME_FUNDING in types

    def test_fundamental_vs_theme(self):
        result = evaluate_contradictions(
            risk_flags=["FINANCIAL_QUALITY_RISK"],
            narrative_score=50,
            mandate_score_component=45,
        )
        types = [item["contradiction_type"] for item in result.contradiction_items]
        assert CONTRADICTION_TYPE_FUNDAMENTAL_THEME in types

    def test_tech_vs_thesis(self):
        result = evaluate_contradictions(
            game_balance="crowded",
            candidate_type="POLICY_AMBUSH",
            mandate_score_component=30,
        )
        types = [item["contradiction_type"] for item in result.contradiction_items]
        assert CONTRADICTION_TYPE_TECH_THESIS in types

    def test_data_vs_score(self):
        result = evaluate_contradictions(
            data_completeness=0.2,
            ambush_score=70,
        )
        types = [item["contradiction_type"] for item in result.contradiction_items]
        assert CONTRADICTION_TYPE_DATA_SCORE in types

    def test_risk_vs_narrative(self):
        result = evaluate_contradictions(
            risk_flags=["INQUIRY_RISK"],
            narrative_score=50,
        )
        types = [item["contradiction_type"] for item in result.contradiction_items]
        assert CONTRADICTION_TYPE_RISK_NARRATIVE in types

    def test_multiple_contradictions(self):
        result = evaluate_contradictions(
            policy_tags=["半导体"],
            version_score=30,
            has_policy_document=True,
            company_role="UNKNOWN",
            narrative_score=50,
            fund_flow_anomaly_score=-5,
            fund_flow_anomaly_tags=["NET_OUTFLOW_DOMINANT"],
            data_completeness=0.2,
            ambush_score=70,
        )
        assert len(result.contradiction_items) >= 3
        assert result.contradiction_level in (CONTRADICTION_MEDIUM, CONTRADICTION_HIGH)

    def test_blocking_gaps_populated(self):
        result = evaluate_contradictions(
            policy_tags=["半导体"],
            version_score=30,
            company_role="UNKNOWN",
        )
        if result.contradiction_items:
            assert len(result.blocking_evidence_gaps) > 0

    def test_verification_steps_populated(self):
        result = evaluate_contradictions(
            policy_tags=["半导体"],
            version_score=30,
            company_role="UNKNOWN",
        )
        if result.contradiction_items:
            assert len(result.next_verification_steps) > 0


# ── 4 fixture scenarios (acceptance) ──

class TestFourContradictionScenarios:
    def test_scenario_1_policy_strong_company_weak(self):
        result = evaluate_contradictions(
            policy_tags=["低空经济"],
            version_score=40,
            has_policy_document=True,
            company_role="CONCEPT_ONLY",
            beneficiary_path=[],
            has_company_evidence=False,
            mandate_score_component=35,
        )
        types = [item["contradiction_type"] for item in result.contradiction_items]
        assert CONTRADICTION_TYPE_POLICY_COMPANY in types
        assert result.contradiction_level != CONTRADICTION_NONE

    def test_scenario_2_theme_hot_funding_cold(self):
        result = evaluate_contradictions(
            narrative_score=60,
            mandate_score_component=40,
            fund_flow_anomaly_score=-10,
            fund_flow_anomaly_tags=["NET_OUTFLOW_DOMINANT"],
        )
        types = [item["contradiction_type"] for item in result.contradiction_items]
        assert CONTRADICTION_TYPE_THEME_FUNDING in types
        assert result.contradiction_level != CONTRADICTION_NONE

    def test_scenario_3_tech_broken_thesis_intact(self):
        result = evaluate_contradictions(
            game_balance="fragile",
            overheat_penalty=30,
            candidate_type="POLICY_AMBUSH",
            mandate_score_component=35,
        )
        types = [item["contradiction_type"] for item in result.contradiction_items]
        assert CONTRADICTION_TYPE_TECH_THESIS in types

    def test_scenario_4_data_sparse_score_high(self):
        result = evaluate_contradictions(
            data_completeness=0.15,
            tradeflow_data_completeness=0.1,
            ambush_score=80,
            composite_score=75,
            missing_evidence=["fund_flow", "announcements"],
        )
        types = [item["contradiction_type"] for item in result.contradiction_items]
        assert CONTRADICTION_TYPE_DATA_SCORE in types
        assert result.contradiction_level in (CONTRADICTION_HIGH, CONTRADICTION_MEDIUM)

    def test_no_contradiction_scenario(self):
        result = evaluate_contradictions(
            policy_tags=["半导体"],
            version_score=30,
            company_role="LEADER",
            beneficiary_path=["芯片设计"],
            has_company_evidence=True,
            narrative_score=30,
            fund_flow_anomaly_score=15,
            data_completeness=0.7,
            ambush_score=40,
        )
        assert result.contradiction_level == CONTRADICTION_NONE
        assert result.contradiction_items == []
        assert result.blocking_evidence_gaps == []
        assert result.next_verification_steps == []


# ── render_contradiction_summary ──

class TestRenderSummary:
    def test_none_level(self):
        result = ContradictionResult(contradiction_level=CONTRADICTION_NONE)
        text = render_contradiction_summary(result)
        assert "无明显矛盾" in text

    def test_with_items(self):
        result = ContradictionResult(
            contradiction_level=CONTRADICTION_HIGH,
            contradiction_items=[
                {
                    "contradiction_type": CONTRADICTION_TYPE_POLICY_COMPANY,
                    "description": "政策强但公司弱",
                },
            ],
            blocking_evidence_gaps=["公司受益角色未明确"],
            next_verification_steps=["核实订单/合同"],
        )
        text = render_contradiction_summary(result)
        assert "矛盾等级" in text
        assert "政策强但公司路径弱" in text
        assert "公司受益角色未明确" in text
        assert "核实订单/合同" in text

    def test_medium_level(self):
        result = ContradictionResult(
            contradiction_level=CONTRADICTION_MEDIUM,
            contradiction_items=[{"contradiction_type": CONTRADICTION_TYPE_THEME_FUNDING, "description": "test"}],
        )
        text = render_contradiction_summary(result)
        assert "中" in text


# ── Candidate schema roundtrip ──

class TestCandidateSchemaRoundtrip:
    def test_to_db_row_has_fields(self):
        c = Candidate(
            symbol="600519.SH",
            contradiction_level="high",
            contradiction_items=[{"type": "x"}],
            blocking_evidence_gaps=["gap1"],
            next_verification_steps=["step1"],
        )
        row = c.to_db_row()
        assert row["contradiction_level"] == "high"
        assert json.loads(row["contradiction_items_json"]) == [{"type": "x"}]
        assert json.loads(row["blocking_evidence_gaps_json"]) == ["gap1"]
        assert json.loads(row["next_verification_steps_json"]) == ["step1"]

    def test_from_db_row(self):
        row = {
            "symbol": "600519.SH",
            "name": "贵州茅台",
            "trade_date": "2026-06-07",
            "contradiction_level": "medium",
            "contradiction_items_json": json.dumps([{"type": "y"}]),
            "blocking_evidence_gaps_json": json.dumps(["gap2"]),
            "next_verification_steps_json": json.dumps(["step2"]),
        }
        c = Candidate.from_db_row(row)
        assert c.contradiction_level == "medium"
        assert c.contradiction_items == [{"type": "y"}]
        assert c.blocking_evidence_gaps == ["gap2"]
        assert c.next_verification_steps == ["step2"]

    def test_empty_from_db_row(self):
        row = {"symbol": "600519.SH"}
        c = Candidate.from_db_row(row)
        assert c.contradiction_level == ""
        assert c.contradiction_items == []
        assert c.blocking_evidence_gaps == []
        assert c.next_verification_steps == []

    def test_full_roundtrip(self):
        c = Candidate(
            symbol="000001.SZ",
            contradiction_level="high",
            contradiction_items=[{"contradiction_type": CONTRADICTION_TYPE_POLICY_COMPANY}],
            blocking_evidence_gaps=["公司受益角色未明确", "受益路径缺失"],
            next_verification_steps=["核实订单/合同"],
        )
        row = c.to_db_row()
        c2 = Candidate.from_db_row(row)
        assert c2.contradiction_level == "high"
        assert c2.contradiction_items == [{"contradiction_type": CONTRADICTION_TYPE_POLICY_COMPANY}]
        assert c2.blocking_evidence_gaps == ["公司受益角色未明确", "受益路径缺失"]
        assert c2.next_verification_steps == ["核实订单/合同"]


# ── DB save_candidate integration ──

class TestDBSaveIntegration:
    def test_save_and_load(self):
        from tradingagents.tradeflow.candidate_engine import save_candidate, init_db

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            init_db(db_path)

            c = Candidate(
                symbol="600519.SH",
                name="贵州茅台",
                trade_date="2026-06-07",
                contradiction_level="high",
                contradiction_items=[{"contradiction_type": CONTRADICTION_TYPE_POLICY_COMPANY}],
                blocking_evidence_gaps=["公司受益角色未明确"],
                next_verification_steps=["核实订单"],
            )
            save_candidate(c, db_path)

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM tradeflow_candidates WHERE trade_date=? AND symbol=?",
                ("2026-06-07", "600519.SH"),
            ).fetchone()
            conn.close()

            assert row is not None
            assert row["contradiction_level"] == "high"
            assert json.loads(row["contradiction_items_json"]) == [{"contradiction_type": CONTRADICTION_TYPE_POLICY_COMPANY}]
            assert json.loads(row["blocking_evidence_gaps_json"]) == ["公司受益角色未明确"]
        finally:
            os.unlink(db_path)

    def test_upsert_preserves_fields(self):
        from tradingagents.tradeflow.candidate_engine import save_candidate, init_db

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            init_db(db_path)

            c1 = Candidate(symbol="600519.SH", trade_date="2026-06-07", contradiction_level="medium")
            save_candidate(c1, db_path)

            c2 = Candidate(symbol="600519.SH", trade_date="2026-06-07", contradiction_level="high",
                           contradiction_items=[{"t": "x"}])
            save_candidate(c2, db_path)

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM tradeflow_candidates WHERE trade_date=? AND symbol=?",
                ("2026-06-07", "600519.SH"),
            ).fetchone()
            conn.close()

            assert row["contradiction_level"] == "high"
            assert json.loads(row["contradiction_items_json"]) == [{"t": "x"}]
        finally:
            os.unlink(db_path)


# ── Acceptance: 4 types of contradiction samples output different explanations ──

class TestAcceptanceH011:
    def test_acceptance_4_types_different(self):
        scenarios = [
            {
                "policy_tags": ["半导体"],
                "version_score": 40,
                "has_policy_document": True,
                "company_role": "UNKNOWN",
            },
            {
                "narrative_score": 60,
                "fund_flow_anomaly_score": -10,
                "fund_flow_anomaly_tags": ["NET_OUTFLOW_DOMINANT"],
            },
            {
                "risk_flags": ["FINANCIAL_QUALITY_RISK"],
                "narrative_score": 50,
                "mandate_score_component": 45,
            },
            {
                "game_balance": "fragile",
                "candidate_type": "POLICY_AMBUSH",
                "mandate_score_component": 35,
            },
        ]
        types_seen = set()
        for kwargs in scenarios:
            result = evaluate_contradictions(**kwargs)
            for item in result.contradiction_items:
                types_seen.add(item["contradiction_type"])
        assert len(types_seen) >= 4

    def test_acceptance_no_contradiction_empty_array(self):
        result = evaluate_contradictions(
            policy_tags=["半导体"],
            company_role="LEADER",
            beneficiary_path=["芯片"],
            has_company_evidence=True,
            narrative_score=10,
            fund_flow_anomaly_score=5,
            data_completeness=0.8,
            ambush_score=30,
        )
        assert result.contradiction_items == []
        assert result.contradiction_level == CONTRADICTION_NONE
        assert not result.blocking_evidence_gaps
        assert not result.next_verification_steps

    def test_acceptance_no_buy_sell_words(self):
        forbidden = {"立即买入", "重仓买入", "立即清仓", "满仓", "梭哈"}
        result = evaluate_contradictions(
            policy_tags=["半导体"],
            version_score=40,
            company_role="UNKNOWN",
            narrative_score=50,
            fund_flow_anomaly_score=-10,
        )
        text = render_contradiction_summary(result)
        for w in forbidden:
            assert w not in text

    def test_acceptance_unknown_not_treated_as_confirmed_negative(self):
        result = evaluate_contradictions(
            company_role="",
            narrative_score=5,
            fund_flow_anomaly_score=0,
            data_completeness=0.8,
            ambush_score=20,
        )
        assert result.contradiction_level == CONTRADICTION_NONE

    def test_acceptance_contradiction_items_have_description(self):
        result = evaluate_contradictions(
            policy_tags=["半导体"],
            version_score=40,
            company_role="UNKNOWN",
        )
        for item in result.contradiction_items:
            assert "description" in item
            assert len(item["description"]) > 0

    def test_acceptance_blocking_gaps_actionable(self):
        result = evaluate_contradictions(
            policy_tags=["半导体"],
            version_score=40,
            company_role="UNKNOWN",
        )
        if result.blocking_evidence_gaps:
            for gap in result.blocking_evidence_gaps:
                assert len(gap) > 3

    def test_acceptance_verification_steps_actionable(self):
        result = evaluate_contradictions(
            policy_tags=["半导体"],
            version_score=40,
            company_role="UNKNOWN",
        )
        if result.next_verification_steps:
            for step in result.next_verification_steps:
                assert len(step) > 3

    def test_acceptance_all_6_types_covered(self):
        assert len(ALL_CONTRADICTION_TYPES) == 6
        for t in ALL_CONTRADICTION_TYPES:
            assert t in CONTRADICTION_LABELS
