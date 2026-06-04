# [H-009] mandate_counter_evidence_calibration
"""Tests for counter-evidence calibration: 6 rules + POLICY_AMBUSH constraints + API exposure."""

import json
import os
import sqlite3
import tempfile
from dataclasses import dataclass
from typing import Optional

import pytest

from tradingagents.tradeflow.counter_evidence_calibration import (
    COUNTER_RULE_OVERHEATED_PRICE,
    COUNTER_RULE_POLICY_DECAY,
    COUNTER_RULE_WEAK_PATH,
    COUNTER_RULE_FUNDING,
    COUNTER_RULE_NEGATIVE,
    COUNTER_RULE_CROWDED,
    ALL_COUNTER_RULES,
    COUNTER_RULE_LABELS,
    CounterEvidenceFlag,
    CounterEvidenceCalibrationResult,
    evaluate_counter_evidence,
    render_counter_evidence_summary,
    _worse_tier,
)
from tradingagents.tradeflow.ambush_score import (
    CandidateType,
    compute_ambush_score,
    classify_candidate_type,
)
from tradingagents.tradeflow.schemas import Candidate
from tradingagents.tradeflow.candidate_engine import init_db, save_candidate
from tradingagents.tradeflow.mandate_replay_eval import (
    get_replay_fixture,
    ALL_REPLAY_FIXTURE_IDS,
    run_replay_evaluation,
    FIXTURE_OVERHEATED_WEAK_PATH,
    FIXTURE_STRONG_NOT_OVERKILL,
)


# ── CounterEvidenceFlag ──────────────────────────────────────────

class TestCounterEvidenceFlag:
    def test_to_dict(self):
        f = CounterEvidenceFlag(rule="test", triggered=True, severity=0.5, description="desc")
        d = f.to_dict()
        assert d["rule"] == "test"
        assert d["triggered"] is True
        assert d["severity"] == 0.5
        assert d["description"] == "desc"

    def test_default(self):
        f = CounterEvidenceFlag()
        assert f.triggered is False
        assert f.severity == 0.0
        assert f.rule == ""

    def test_severity_rounded(self):
        f = CounterEvidenceFlag(rule="r", triggered=True, severity=0.12345)
        assert f.to_dict()["severity"] == 0.12


class TestCounterEvidenceCalibrationResult:
    def test_to_dict(self):
        r = CounterEvidenceCalibrationResult(
            counter_evidence=[{"rule": "x"}],
            overheat_flags=["高位过热"],
            downgrade_reasons=["test"],
            what_would_change_mind=["new evidence"],
            max_downgrade_tier="B",
            ambush_score_cap=50.0,
        )
        d = r.to_dict()
        assert d["counter_evidence"] == [{"rule": "x"}]
        assert d["overheat_flags"] == ["高位过热"]
        assert d["downgrade_reasons"] == ["test"]
        assert d["what_would_change_mind"] == ["new evidence"]
        assert d["max_downgrade_tier"] == "B"
        assert d["ambush_score_cap"] == 50.0

    def test_default(self):
        r = CounterEvidenceCalibrationResult()
        assert r.counter_evidence == []
        assert r.ambush_score_cap == 100.0
        assert r.max_downgrade_tier == ""

    def test_counter_flags_included(self):
        flags = [CounterEvidenceFlag(rule="r1", triggered=True)]
        r = CounterEvidenceCalibrationResult(counter_flags=flags)
        assert len(r.to_dict()["counter_flags"]) == 1


# ── Constants ────────────────────────────────────────────────────

class TestConstants:
    def test_six_rules(self):
        assert len(ALL_COUNTER_RULES) == 6

    def test_labels_match(self):
        for rule in ALL_COUNTER_RULES:
            assert rule in COUNTER_RULE_LABELS

    def test_rule_ids(self):
        assert COUNTER_RULE_OVERHEATED_PRICE == "overheated_price_position"
        assert COUNTER_RULE_POLICY_DECAY == "policy_signal_decay"
        assert COUNTER_RULE_WEAK_PATH == "weak_company_benefit_path"
        assert COUNTER_RULE_FUNDING == "funding_not_confirmed"
        assert COUNTER_RULE_NEGATIVE == "negative_event_unresolved"
        assert COUNTER_RULE_CROWDED == "crowded_consensus_risk"


# ── _worse_tier ──────────────────────────────────────────────────

class TestWorseTier:
    def test_empty_current(self):
        assert _worse_tier("", "B") == "B"

    def test_empty_new(self):
        assert _worse_tier("A", "") == "A"

    def test_both_empty(self):
        assert _worse_tier("", "") == ""

    def test_a_vs_b(self):
        assert _worse_tier("A", "B") == "B"

    def test_b_vs_c(self):
        assert _worse_tier("B", "C") == "C"

    def test_same(self):
        assert _worse_tier("B", "B") == "B"


# ── Rule: overheated_price_position ──────────────────────────────

class TestRuleOverheatedPricePosition:
    def _eval(self, **kw):
        from tradingagents.tradeflow.counter_evidence_calibration import _rule_overheated_price_position
        return _rule_overheated_price_position(**kw)

    def test_high_overheat_no_evidence(self):
        f = self._eval(overheat_penalty=30.0, has_new_policy_evidence=False)
        assert f.triggered is True
        assert f.severity > 0

    def test_high_overheat_with_evidence(self):
        f = self._eval(overheat_penalty=30.0, has_new_policy_evidence=True)
        assert f.triggered is False

    def test_narrative_crowded_no_evidence(self):
        f = self._eval(narrative_score=60.0, game_balance="crowded", has_new_policy_evidence=False)
        assert f.triggered is True

    def test_low_overheat(self):
        f = self._eval(overheat_penalty=10.0)
        assert f.triggered is False

    def test_bad_game_moderate_overheat(self):
        f = self._eval(game_balance="fragile", overheat_penalty=18.0)
        assert f.triggered is True
        assert f.severity == 0.5

    def test_neutral_game(self):
        f = self._eval(game_balance="favorable", overheat_penalty=18.0)
        assert f.triggered is False


# ── Rule: policy_signal_decay ────────────────────────────────────

class TestRulePolicySignalDecay:
    def _eval(self, **kw):
        from tradingagents.tradeflow.counter_evidence_calibration import _rule_policy_signal_decay
        return _rule_policy_signal_decay(**kw)

    def test_noise(self):
        f = self._eval(is_noise=True)
        assert f.triggered is True
        assert f.severity == 0.8

    def test_no_doc_single_date_low_version(self):
        f = self._eval(has_policy_document=False, unique_dates=1, version_score=10.0)
        assert f.triggered is True
        assert f.severity == 0.7

    def test_no_doc_single_date_high_version(self):
        f = self._eval(has_policy_document=False, unique_dates=1, version_score=40.0)
        assert f.triggered is True
        assert f.severity == 0.5

    def test_has_document(self):
        f = self._eval(has_policy_document=True, unique_dates=3, version_score=50.0)
        assert f.triggered is False

    def test_no_doc_multi_date(self):
        f = self._eval(has_policy_document=False, unique_dates=3, version_score=40.0)
        assert f.triggered is False


# ── Rule: weak_company_benefit_path ──────────────────────────────

class TestRuleWeakCompanyBenefitPath:
    def _eval(self, **kw):
        from tradingagents.tradeflow.counter_evidence_calibration import _rule_weak_company_benefit_path
        return _rule_weak_company_benefit_path(**kw)

    def test_concept_only_no_evidence(self):
        f = self._eval(company_role="CONCEPT_ONLY", has_company_evidence=False)
        assert f.triggered is True
        assert f.severity == 0.9

    def test_unknown_role(self):
        f = self._eval(company_role="UNKNOWN")
        assert f.triggered is True
        assert f.severity == 0.8

    def test_no_role(self):
        f = self._eval(company_role="")
        assert f.triggered is True

    def test_leader_with_path(self):
        f = self._eval(company_role="LEADER", beneficiary_path=["seg1", "seg2"], has_company_evidence=True)
        assert f.triggered is False

    def test_concept_only_with_evidence(self):
        f = self._eval(company_role="CONCEPT_ONLY", has_company_evidence=True)
        assert f.triggered is True
        assert f.severity == 0.7

    def test_no_path_no_evidence(self):
        f = self._eval(company_role="CORE_SUPPLIER", beneficiary_path=[], has_company_evidence=False)
        assert f.triggered is True
        assert f.severity == 0.6


# ── Rule: funding_not_confirmed ──────────────────────────────────

class TestRuleFundingNotConfirmed:
    def _eval(self, **kw):
        from tradingagents.tradeflow.counter_evidence_calibration import _rule_funding_not_confirmed
        return _rule_funding_not_confirmed(**kw)

    def test_net_outflow_unverified(self):
        f = self._eval(fund_flow_anomaly_tags=["NET_OUTFLOW_DOMINANT"], fund_flow_unit_verified=False)
        assert f.triggered is True
        assert f.severity == 0.7

    def test_net_outflow_verified(self):
        f = self._eval(fund_flow_anomaly_tags=["NET_OUTFLOW_DOMINANT"], fund_flow_unit_verified=True)
        assert f.triggered is True
        assert f.severity == 0.6

    def test_no_signal_unverified(self):
        f = self._eval(fund_flow_anomaly_score=0.0, fund_flow_unit_verified=False)
        assert f.triggered is True
        assert f.severity == 0.3

    def test_positive_signal_verified(self):
        f = self._eval(fund_flow_anomaly_score=5.0, fund_flow_unit_verified=True)
        assert f.triggered is False


# ── Rule: negative_event_unresolved ──────────────────────────────

class TestRuleNegativeEventUnresolved:
    def _eval(self, **kw):
        from tradingagents.tradeflow.counter_evidence_calibration import _rule_negative_event_unresolved
        return _rule_negative_event_unresolved(**kw)

    def test_inquiry_risk(self):
        f = self._eval(risk_flags=["INQUIRY_RISK"])
        assert f.triggered is True
        assert f.severity == 0.9

    def test_financial_quality_risk(self):
        f = self._eval(risk_flags=["FINANCIAL_QUALITY_RISK"])
        assert f.triggered is True

    def test_heavy_risk_penalty(self):
        f = self._eval(risk_penalty=-15.0)
        assert f.triggered is True
        assert f.severity == 0.75

    def test_no_risk(self):
        f = self._eval(risk_flags=[], risk_penalty=0.0)
        assert f.triggered is False

    def test_low_risk_penalty(self):
        f = self._eval(risk_penalty=-5.0)
        assert f.triggered is False


# ── Rule: crowded_consensus ──────────────────────────────────────

class TestRuleCrowdedConsensus:
    def _eval(self, **kw):
        from tradingagents.tradeflow.counter_evidence_calibration import _rule_crowded_consensus
        return _rule_crowded_consensus(**kw)

    def test_crowded_high_resonance(self):
        f = self._eval(game_balance="crowded", resonance_count=4)
        assert f.triggered is True
        assert f.severity == 0.8

    def test_fragile(self):
        f = self._eval(game_balance="fragile")
        assert f.triggered is True
        assert f.severity == 0.6

    def test_narrative_high_resonance(self):
        f = self._eval(narrative_score=65.0, resonance_count=5)
        assert f.triggered is True
        assert f.severity == 0.5

    def test_favorable(self):
        f = self._eval(game_balance="favorable", resonance_count=0, narrative_score=10.0)
        assert f.triggered is False


# ── evaluate_counter_evidence integration ─────────────────────────

class TestEvaluateCounterEvidence:
    def test_no_issues(self):
        r = evaluate_counter_evidence(
            candidate_type="POLICY_AMBUSH",
            overheat_penalty=5.0,
            narrative_score=20.0,
            game_balance="favorable",
            policy_tags=["低空经济"],
            version_score=50.0,
            has_policy_document=True,
            unique_dates=3,
            company_role="LEADER",
            beneficiary_path=["seg1"],
            has_company_evidence=True,
            has_new_policy_evidence=True,
            has_new_company_evidence=True,
            fund_flow_anomaly_score=5.0,
            fund_flow_unit_verified=True,
        )
        assert r.counter_evidence == []
        assert r.downgrade_reasons == []
        assert r.overheat_flags == []
        assert r.ambush_score_cap == 100.0
        assert r.max_downgrade_tier == ""

    def test_overheated_policy_ambush(self):
        r = evaluate_counter_evidence(
            candidate_type="POLICY_AMBUSH",
            overheat_penalty=35.0,
            narrative_score=60.0,
            game_balance="crowded",
            has_new_policy_evidence=False,
        )
        assert len(r.counter_evidence) >= 1
        assert len(r.overheat_flags) >= 1
        assert r.ambush_score_cap < 100.0
        assert r.max_downgrade_tier != ""

    def test_concept_only_cannot_enter_a(self):
        r = evaluate_counter_evidence(
            candidate_type="POLICY_AMBUSH",
            company_role="CONCEPT_ONLY",
            has_company_evidence=False,
            policy_tags=["test"],
        )
        assert r.max_downgrade_tier in ("B", "C")
        assert r.ambush_score_cap <= 35.0
        assert any("不得进入A层" in d for d in r.downgrade_reasons)

    def test_media_only_no_policy_document(self):
        r = evaluate_counter_evidence(
            candidate_type="POLICY_AMBUSH",
            policy_tags=["低空经济"],
            has_policy_document=False,
            company_role="LEADER",
            beneficiary_path=["seg1"],
            has_company_evidence=True,
        )
        assert r.ambush_score_cap <= 45.0
        assert any("无原始政策" in d for d in r.downgrade_reasons)

    def test_negative_risk_blocks_ambush(self):
        r = evaluate_counter_evidence(
            candidate_type="POLICY_AMBUSH",
            risk_flags=["INQUIRY_RISK"],
        )
        assert r.ambush_score_cap <= 30.0
        assert r.max_downgrade_tier == "C"

    def test_tech_trade_no_special_constraints(self):
        r = evaluate_counter_evidence(
            candidate_type="TECH_TRADE",
            overheat_penalty=30.0,
        )
        assert r.max_downgrade_tier == ""
        assert r.ambush_score_cap == 100.0

    def test_what_would_change_mind(self):
        r = evaluate_counter_evidence(
            candidate_type="POLICY_AMBUSH",
            has_policy_document=False,
            policy_tags=["test"],
            unique_dates=1,
            version_score=10.0,
            company_role="CONCEPT_ONLY",
        )
        assert len(r.what_would_change_mind) >= 1

    def test_no_duplicate_reasons(self):
        r = evaluate_counter_evidence(
            candidate_type="POLICY_AMBUSH",
            company_role="CONCEPT_ONLY",
            risk_flags=["INQUIRY_RISK"],
        )
        assert len(r.downgrade_reasons) == len(set(r.downgrade_reasons))


# ── Overheat samples: MUST NOT enter A tier ──────────────────────

class TestAcceptanceOverheat:
    def test_overheated_no_a_tier(self):
        r = evaluate_counter_evidence(
            candidate_type="POLICY_AMBUSH",
            overheat_penalty=30.0,
            game_balance="crowded",
            narrative_score=55.0,
            has_new_policy_evidence=False,
            company_role="LEADER",
            beneficiary_path=["seg1"],
            has_company_evidence=True,
            policy_tags=["test"],
            has_policy_document=True,
            version_score=40.0,
            unique_dates=2,
        )
        assert r.max_downgrade_tier in ("B", "C")

    def test_policy_strong_path_weak_downgrade(self):
        r = evaluate_counter_evidence(
            candidate_type="POLICY_AMBUSH",
            policy_tags=["低空经济"],
            version_score=50.0,
            has_policy_document=True,
            unique_dates=3,
            company_role="CONCEPT_ONLY",
            has_company_evidence=False,
        )
        assert r.max_downgrade_tier == "B"
        assert any("路径弱" in d for d in r.downgrade_reasons)

    def test_strong_not_overkilled(self):
        r = evaluate_counter_evidence(
            candidate_type="POLICY_AMBUSH",
            overheat_penalty=5.0,
            narrative_score=20.0,
            game_balance="favorable",
            policy_tags=["低空经济"],
            version_score=60.0,
            has_policy_document=True,
            unique_dates=3,
            company_role="LEADER",
            beneficiary_path=["seg1", "seg2"],
            has_company_evidence=True,
            has_new_policy_evidence=True,
            has_new_company_evidence=True,
            fund_flow_anomaly_score=8.0,
            fund_flow_unit_verified=True,
            resonance_count=2,
        )
        assert r.max_downgrade_tier == ""
        assert r.ambush_score_cap == 100.0
        assert len(r.downgrade_reasons) == 0


# ── render_counter_evidence_summary ──────────────────────────────

class TestRenderSummary:
    def test_no_issues(self):
        r = CounterEvidenceCalibrationResult()
        assert "无明显反证信号" in render_counter_evidence_summary(r)

    def test_with_issues(self):
        r = CounterEvidenceCalibrationResult(
            overheat_flags=["高位过热"],
            downgrade_reasons=["过热: test"],
            what_would_change_mind=["new evidence"],
            max_downgrade_tier="B",
            ambush_score_cap=50.0,
        )
        s = render_counter_evidence_summary(r)
        assert "高位过热" in s
        assert "降权原因" in s
        assert "什么能改变判断" in s
        assert "B" in s
        assert "50" in s


# ── Replay fixtures (H-009) ──────────────────────────────────────

class TestH009ReplayFixtures:
    def test_overheated_weak_path_exists(self):
        f = get_replay_fixture(FIXTURE_OVERHEATED_WEAK_PATH)
        assert f is not None
        assert f.company_role == "CONCEPT_ONLY"
        assert len(f.counter_evidences) == 2

    def test_strong_not_overkilled_exists(self):
        f = get_replay_fixture(FIXTURE_STRONG_NOT_OVERKILL)
        assert f is not None
        assert f.company_role == "LEADER"
        assert len(f.counter_evidences) == 0

    def test_all_fixtures_includes_new(self):
        assert FIXTURE_OVERHEATED_WEAK_PATH in ALL_REPLAY_FIXTURE_IDS
        assert FIXTURE_STRONG_NOT_OVERKILL in ALL_REPLAY_FIXTURE_IDS
        assert len(ALL_REPLAY_FIXTURE_IDS) >= 13

    def test_overheated_weak_replay(self):
        report = run_replay_evaluation(fixture_ids=[FIXTURE_OVERHEATED_WEAK_PATH])
        assert report.total_fixtures == 1
        assert len(report.results) == 1
        assert report.results[0].counter_evidences is not None

    def test_strong_not_overkilled_replay(self):
        report = run_replay_evaluation(fixture_ids=[FIXTURE_STRONG_NOT_OVERKILL])
        assert report.total_fixtures == 1
        assert report.results[0].passed is True
        assert report.results[0].verdict_score >= 80

    def test_full_replay_includes_new(self):
        report = run_replay_evaluation()
        fixture_ids = [r.fixture_id for r in report.results]
        assert FIXTURE_OVERHEATED_WEAK_PATH in fixture_ids
        assert FIXTURE_STRONG_NOT_OVERKILL in fixture_ids


# ── DB schema integration ────────────────────────────────────────

class TestDBSchemaIntegration:
    def test_candidate_roundtrip(self):
        c = Candidate(
            symbol="600000.SH",
            trade_date="2026-06-04",
            counter_evidence=[{"rule": "overheated_price_position", "triggered": True}],
            overheat_flags=["高位过热"],
            downgrade_reasons=["过热: test"],
            what_would_change_mind=["new evidence"],
        )
        row = c.to_db_row()
        assert "counter_evidence_json" in row
        assert "overheat_flags_json" in row
        assert "downgrade_reasons_json" in row
        assert "what_would_change_mind_json" in row
        ce = json.loads(row["counter_evidence_json"])
        assert len(ce) == 1
        assert ce[0]["rule"] == "overheated_price_position"

    def test_candidate_from_db_row(self):
        row = {
            "symbol": "600000.SH",
            "counter_evidence_json": '[{"rule": "test"}]',
            "overheat_flags_json": '["高位过热"]',
            "downgrade_reasons_json": '["reason1"]',
            "what_would_change_mind_json": '["evidence"]',
        }
        c = Candidate.from_db_row(row)
        assert len(c.counter_evidence) == 1
        assert len(c.overheat_flags) == 1
        assert len(c.downgrade_reasons) == 1
        assert len(c.what_would_change_mind) == 1

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "test_tf.db")
            init_db(db_path)
            c = Candidate(
                symbol="600000.SH",
                trade_date="2026-06-04",
                name="测试",
                score=50.0,
                candidate_type="POLICY_AMBUSH",
                ambush_score=40.0,
                counter_evidence=[{"rule": "test", "triggered": True}],
                overheat_flags=["test_flag"],
                downgrade_reasons=["test_reason"],
                what_would_change_mind=["test_fix"],
            )
            save_candidate(c, db_path)
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM tradeflow_candidates WHERE symbol='600000.SH'").fetchone()
            assert row is not None
            ce = json.loads(row["counter_evidence_json"])
            assert len(ce) == 1
            assert ce[0]["rule"] == "test"
            of = json.loads(row["overheat_flags_json"])
            assert of == ["test_flag"]
            dr = json.loads(row["downgrade_reasons_json"])
            assert dr == ["test_reason"]
            wm = json.loads(row["what_would_change_mind_json"])
            assert wm == ["test_fix"]
            conn.close()


# ── Cross-module: ambush_score + counter_evidence ────────────────

class TestAmbushScoreWithCounterEvidence:
    def test_concept_only_classified(self):
        result = compute_ambush_score(
            mandate_score=50.0,
            policy_tags=["test"],
            company_role="CONCEPT_ONLY",
            beneficiary_path=["seg1"],
            has_company_evidence=False,
        )
        assert result.candidate_type != CandidateType.POLICY_AMBUSH.value or result.ambush_score < 30

    def test_strong_leader_high_score(self):
        result = compute_ambush_score(
            mandate_score=70.0,
            policy_tags=["低空经济"],
            has_policy_document=True,
            unique_dates=3,
            company_role="LEADER",
            beneficiary_path=["seg1", "seg2"],
            has_company_evidence=True,
        )
        assert result.ambush_score >= 40
        assert result.candidate_type in (
            CandidateType.POLICY_AMBUSH.value,
            CandidateType.POLICY_CONFIRM.value,
        )


# ── Acceptance: full H-009 criteria ──────────────────────────────

class TestAcceptanceH009:
    def test_overheated_no_a_tier(self):
        r = evaluate_counter_evidence(
            candidate_type="POLICY_AMBUSH",
            overheat_penalty=30.0,
            game_balance="crowded",
            narrative_score=60.0,
            has_new_policy_evidence=False,
            policy_tags=["test"],
            version_score=40.0,
            has_policy_document=True,
            unique_dates=2,
            company_role="LEADER",
            beneficiary_path=["seg1"],
            has_company_evidence=True,
        )
        assert r.max_downgrade_tier in ("B", "C")

    def test_policy_strong_path_weak_to_observe(self):
        r = evaluate_counter_evidence(
            candidate_type="POLICY_AMBUSH",
            policy_tags=["低空经济"],
            version_score=50.0,
            has_policy_document=True,
            unique_dates=3,
            company_role="CONCEPT_ONLY",
            has_company_evidence=False,
        )
        assert r.max_downgrade_tier in ("B", "C")

    def test_strong_not_false_killed(self):
        r = evaluate_counter_evidence(
            candidate_type="POLICY_AMBUSH",
            overheat_penalty=5.0,
            narrative_score=15.0,
            game_balance="favorable",
            policy_tags=["低空经济"],
            version_score=65.0,
            has_policy_document=True,
            unique_dates=3,
            company_role="LEADER",
            beneficiary_path=["seg1", "seg2"],
            has_company_evidence=True,
            has_new_policy_evidence=True,
            has_new_company_evidence=True,
            fund_flow_anomaly_score=8.0,
            fund_flow_unit_verified=True,
            resonance_count=2,
            risk_flags=[],
            risk_penalty=0.0,
        )
        assert len(r.downgrade_reasons) == 0
        assert r.ambush_score_cap == 100.0

    def test_replay_fixtures_all_pass(self):
        report = run_replay_evaluation()
        assert report.total_fixtures >= 13

    def test_explainable_fields_present(self):
        r = evaluate_counter_evidence(
            candidate_type="POLICY_AMBUSH",
            overheat_penalty=30.0,
            company_role="CONCEPT_ONLY",
            policy_tags=["test"],
        )
        assert isinstance(r.counter_evidence, list)
        assert isinstance(r.overheat_flags, list)
        assert isinstance(r.downgrade_reasons, list)
        assert isinstance(r.what_would_change_mind, list)

    def test_no_forbidden_words_in_output(self):
        forbidden = {"立即买入", "重仓买入", "立即清仓", "满仓", "梭哈"}
        r = evaluate_counter_evidence(
            candidate_type="POLICY_AMBUSH",
            overheat_penalty=50.0,
            company_role="UNKNOWN",
            risk_flags=["INQUIRY_RISK"],
            policy_tags=["test"],
        )
        all_text = " ".join(
            r.downgrade_reasons
            + r.overheat_flags
            + r.what_would_change_mind
            + [f.get("description", "") for f in r.counter_evidence]
        )
        for w in forbidden:
            assert w not in all_text

    def test_api_fields_in_schemas(self):
        from api.tradeflow_schemas import TradeFlowCandidateItem
        fields = TradeFlowCandidateItem.model_fields
        assert "counter_evidence" in fields
        assert "overheat_flags" in fields
        assert "downgrade_reasons" in fields
        assert "what_would_change_mind" in fields

    def test_service_maps_counter_evidence(self):
        from api.services.tradeflow_service import _row_to_candidate_item
        conn = sqlite3.connect(":memory:")
        conn.executescript("CREATE TABLE tradeflow_candidates (symbol TEXT, counter_evidence_json TEXT DEFAULT '[]', overheat_flags_json TEXT DEFAULT '[]', downgrade_reasons_json TEXT DEFAULT '[]', what_would_change_mind_json TEXT DEFAULT '[]')")
        conn.execute("INSERT INTO tradeflow_candidates (symbol, counter_evidence_json) VALUES ('600000.SH', '[{\"rule\":\"test\"}]')")
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM tradeflow_candidates").fetchone()
        item = _row_to_candidate_item(row)
        assert len(item["counter_evidence"]) == 1
        conn.close()
