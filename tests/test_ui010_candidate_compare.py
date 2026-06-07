# [UI-010] mandate_candidate_compare
"""Tests for candidate comparison view and evidence gap sorting."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from datetime import datetime
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

from api.services.tradeflow_service import (
    get_candidate_comparison,
    _counter_evidence_severity,
    _topic_lifecycle_order,
)


def _make_candidate_row(
    symbol: str = "600519.SH",
    name: str = "贵州茅台",
    trade_date: str = "2026-06-08",
    tier: str = "A",
    candidate_type: str = "POLICY_AMBUSH",
    mandate_score: float = 75.0,
    ambush_score: float = 80.0,
    mandate_topic: str = "低空经济",
    company_role: str = "LEADER",
    tradeflow_data_completeness: float = 0.85,
    counter_evidence_json: str = "[]",
    blocking_evidence_gaps_json: str = "[]",
    next_verification_steps_json: str = "[]",
    topic_lifecycle_state: str = "ACCELERATING",
    composite_score: float = 70.0,
    status: str = "active",
    strategy_tags_json: str = "[]",
    missing_data_fields_json: str = "[]",
    missing_evidence_json: str = "[]",
    risk_flags_json: str = "[]",
    policy_tags_json: str = "[]",
    fund_flow_anomaly_tags_json: str = "[]",
    beneficiary_path_json: str = "[]",
    watchlist_evidence_gap_json: str = "[]",
    universe_sources_json: str = "[]",
    overheat_flags_json: str = "[]",
    downgrade_reasons_json: str = "[]",
    what_would_change_mind_json: str = "[]",
    contradiction_items_json: str = "[]",
    missing_evidence_for_upgrade_json: str = "[]",
) -> dict:
    return {
        "symbol": symbol,
        "name": name,
        "trade_date": trade_date,
        "effective_trade_date": trade_date,
        "plan_date": trade_date,
        "observe_date": trade_date,
        "tier": tier,
        "candidate_type": candidate_type,
        "mandate_score_component": mandate_score,
        "ambush_score": ambush_score,
        "mandate_topic": mandate_topic,
        "company_role": company_role,
        "tradeflow_data_completeness": tradeflow_data_completeness,
        "data_completeness": tradeflow_data_completeness,
        "composite_score": composite_score,
        "score": composite_score,
        "status": status,
        "strategy_tags_json": strategy_tags_json,
        "missing_data_fields_json": missing_data_fields_json,
        "missing_evidence_json": missing_evidence_json,
        "risk_flags_json": risk_flags_json,
        "policy_tags_json": policy_tags_json,
        "fund_flow_anomaly_tags_json": fund_flow_anomaly_tags_json,
        "beneficiary_path_json": beneficiary_path_json,
        "watchlist_evidence_gap_json": watchlist_evidence_gap_json,
        "universe_sources_json": universe_sources_json,
        "counter_evidence_json": counter_evidence_json,
        "overheat_flags_json": overheat_flags_json,
        "downgrade_reasons_json": downgrade_reasons_json,
        "what_would_change_mind_json": what_would_change_mind_json,
        "topic_lifecycle_state": topic_lifecycle_state,
        "topic_lifecycle_reason": "",
        "topic_last_signal_date": "",
        "topic_signal_count": 0,
        "contradiction_level": "",
        "contradiction_items_json": contradiction_items_json,
        "blocking_evidence_gaps_json": blocking_evidence_gaps_json,
        "next_verification_steps_json": next_verification_steps_json,
        "missing_evidence_for_upgrade_json": missing_evidence_for_upgrade_json,
        "observe_state": "WAITING",
        "observe_trigger_count": 0,
        "observe_first_trigger_time": "",
        "fund_flow_unit_verified": 0,
        "evidence_gate_applied": 0,
        "need_deep_ta": 0,
        "trade_priority_score": composite_score,
        "action_tier": "scan",
        "action_tier_reason": "",
        "version_score": 0.0,
        "narrative_score": 0.0,
        "fund_flow_anomaly_score": 0.0,
        "resonance_count": 0,
        "ta_budget_priority": 0,
        "watchlist_benefit_score": 0.0,
        "watchlist_consensus_score": 0.0,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
    }


def _create_test_db(candidates: List[dict]) -> str:
    db_path = tempfile.mktemp(suffix=".db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    conn.execute("""
        CREATE TABLE IF NOT EXISTS tradeflow_candidates (
            symbol TEXT,
            name TEXT,
            trade_date TEXT,
            effective_trade_date TEXT,
            plan_date TEXT,
            observe_date TEXT,
            tier TEXT DEFAULT '',
            candidate_type TEXT DEFAULT '',
            mandate_score_component REAL DEFAULT 0,
            ambush_score REAL DEFAULT 0,
            mandate_topic TEXT DEFAULT '',
            company_role TEXT DEFAULT '',
            tradeflow_data_completeness REAL DEFAULT 0,
            data_completeness REAL DEFAULT 0,
            composite_score REAL DEFAULT 0,
            score REAL DEFAULT 0,
            status TEXT DEFAULT 'active',
            strategy_tags_json TEXT DEFAULT '[]',
            missing_data_fields_json TEXT DEFAULT '[]',
            missing_evidence_json TEXT DEFAULT '[]',
            risk_flags_json TEXT DEFAULT '[]',
            policy_tags_json TEXT DEFAULT '[]',
            fund_flow_anomaly_tags_json TEXT DEFAULT '[]',
            beneficiary_path_json TEXT DEFAULT '[]',
            watchlist_evidence_gap_json TEXT DEFAULT '[]',
            universe_sources_json TEXT DEFAULT '[]',
            counter_evidence_json TEXT DEFAULT '[]',
            overheat_flags_json TEXT DEFAULT '[]',
            downgrade_reasons_json TEXT DEFAULT '[]',
            what_would_change_mind_json TEXT DEFAULT '[]',
            topic_lifecycle_state TEXT DEFAULT '',
            topic_lifecycle_reason TEXT DEFAULT '',
            topic_last_signal_date TEXT DEFAULT '',
            topic_signal_count INTEGER DEFAULT 0,
            contradiction_level TEXT DEFAULT '',
            contradiction_items_json TEXT DEFAULT '[]',
            blocking_evidence_gaps_json TEXT DEFAULT '[]',
            next_verification_steps_json TEXT DEFAULT '[]',
            missing_evidence_for_upgrade_json TEXT DEFAULT '[]',
            observe_state TEXT DEFAULT 'WAITING',
            observe_trigger_count INTEGER DEFAULT 0,
            observe_first_trigger_time TEXT DEFAULT '',
            fund_flow_unit_verified INTEGER DEFAULT 0,
            evidence_gate_applied INTEGER DEFAULT 0,
            need_deep_ta INTEGER DEFAULT 0,
            trade_priority_score REAL DEFAULT 0,
            action_tier TEXT DEFAULT 'scan',
            action_tier_reason TEXT DEFAULT '',
            version_score REAL DEFAULT 0,
            narrative_score REAL DEFAULT 0,
            fund_flow_anomaly_score REAL DEFAULT 0,
            resonance_count INTEGER DEFAULT 0,
            ta_budget_priority INTEGER DEFAULT 0,
            watchlist_benefit_score REAL DEFAULT 0,
            watchlist_consensus_score REAL DEFAULT 0,
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT '',
            PRIMARY KEY (trade_date, symbol)
        )
    """)

    for c in candidates:
        cols = ", ".join(c.keys())
        placeholders = ", ".join(["?"] * len(c))
        conn.execute(f"INSERT INTO tradeflow_candidates ({cols}) VALUES ({placeholders})", list(c.values()))

    conn.commit()
    conn.close()
    return db_path


class TestCounterEvidenceSeverity:
    def test_empty(self):
        assert _counter_evidence_severity([]) == 0.0

    def test_single_triggered(self):
        assert _counter_evidence_severity([{"rule": "test", "severity": 0.8}]) == 0.8

    def test_multiple(self):
        items = [{"severity": 0.3}, {"severity": 0.5}]
        assert abs(_counter_evidence_severity(items) - 0.8) < 0.001

    def test_no_severity(self):
        assert _counter_evidence_severity([{"rule": "test"}]) == 0.0

    def test_mixed(self):
        items = [{"severity": 0.4}, {"rule": "x"}, {"severity": 0.6}]
        assert abs(_counter_evidence_severity(items) - 1.0) < 0.001


class TestTopicLifecycleOrder:
    def test_emerging(self):
        assert _topic_lifecycle_order("EMERGING") == 0

    def test_accelerating(self):
        assert _topic_lifecycle_order("ACCELERATING") == 1

    def test_confirming(self):
        assert _topic_lifecycle_order("CONFIRMING") == 2

    def test_unknown(self):
        assert _topic_lifecycle_order("UNKNOWN") == 3

    def test_empty(self):
        assert _topic_lifecycle_order("") == 3

    def test_crowded(self):
        assert _topic_lifecycle_order("CROWDED") == 4

    def test_fading(self):
        assert _topic_lifecycle_order("FADING") == 5

    def test_unrecognized(self):
        assert _topic_lifecycle_order("INVALID") == 3


class TestGetCandidateComparisonNoDb:
    def test_no_db(self):
        with patch("api.services.tradeflow_service._connect", return_value=None):
            result = get_candidate_comparison("2026-06-08", tf_db_path="/nonexistent")
            assert result["status"] == "no_data"


class TestGetCandidateComparisonWithDb:
    @pytest.fixture()
    def db_with_three(self):
        c1 = _make_candidate_row(
            symbol="600519.SH", name="贵州茅台", mandate_score=90, ambush_score=85,
            company_role="LEADER", topic_lifecycle_state="ACCELERATING",
            tradeflow_data_completeness=0.9, candidate_type="POLICY_AMBUSH",
        )
        c2 = _make_candidate_row(
            symbol="000001.SZ", name="平安银行", mandate_score=50, ambush_score=40,
            company_role="CONCEPT_ONLY", topic_lifecycle_state="CROWDED",
            tradeflow_data_completeness=0.5, candidate_type="TECH_TRADE",
        )
        c3 = _make_candidate_row(
            symbol="601689.SH", name="拓普集团", mandate_score=70, ambush_score=65,
            company_role="CORE_SUPPLIER", topic_lifecycle_state="EMERGING",
            tradeflow_data_completeness=0.7, candidate_type="POLICY_CONFIRM",
        )
        db_path = _create_test_db([c1, c2, c3])
        yield db_path
        if os.path.exists(db_path):
            os.unlink(db_path)

    def test_sort_by_mandate_score_desc(self, db_with_three):
        result = get_candidate_comparison("2026-06-08", sort_by="mandate_score", sort_order="desc", tf_db_path=db_with_three)
        assert result["status"] == "ok"
        assert result["sort_by"] == "mandate_score"
        assert result["total"] == 3
        scores = [c["mandate_score"] for c in result["candidates"]]
        assert scores == sorted(scores, reverse=True)

    def test_sort_by_mandate_score_asc(self, db_with_three):
        result = get_candidate_comparison("2026-06-08", sort_by="mandate_score", sort_order="asc", tf_db_path=db_with_three)
        scores = [c["mandate_score"] for c in result["candidates"]]
        assert scores == sorted(scores)

    def test_sort_by_ambush_score(self, db_with_three):
        result = get_candidate_comparison("2026-06-08", sort_by="ambush_score", sort_order="desc", tf_db_path=db_with_three)
        scores = [c["ambush_score"] for c in result["candidates"]]
        assert scores == sorted(scores, reverse=True)

    def test_sort_by_evidence_coverage(self, db_with_three):
        result = get_candidate_comparison("2026-06-08", sort_by="evidence_coverage", sort_order="desc", tf_db_path=db_with_three)
        coverages = [c["tradeflow_data_completeness"] for c in result["candidates"]]
        assert coverages == sorted(coverages, reverse=True)

    def test_sort_by_topic_lifecycle(self, db_with_three):
        result = get_candidate_comparison("2026-06-08", sort_by="topic_lifecycle_state", sort_order="asc", tf_db_path=db_with_three)
        states = [c["topic_lifecycle_state"] for c in result["candidates"]]
        assert states[0] == "EMERGING"

    def test_sort_by_company_role(self, db_with_three):
        result = get_candidate_comparison("2026-06-08", sort_by="company_role", sort_order="asc", tf_db_path=db_with_three)
        roles = [c["company_role"] for c in result["candidates"]]
        assert roles == sorted(roles)

    def test_invalid_sort_key_defaults_to_mandate(self, db_with_three):
        result = get_candidate_comparison("2026-06-08", sort_by="nonexistent_key", tf_db_path=db_with_three)
        assert result["sort_by"] == "mandate_score"

    def test_no_internal_keys_in_output(self, db_with_three):
        result = get_candidate_comparison("2026-06-08", tf_db_path=db_with_three)
        for c in result["candidates"]:
            assert "_counter_evidence_severity" not in c
            assert "_evidence_gap_count" not in c
            assert "_topic_lifecycle_order" not in c

    def test_empty_candidates(self):
        db_path = _create_test_db([])
        try:
            result = get_candidate_comparison("2026-06-08", tf_db_path=db_path)
            assert result["status"] == "ok"
            assert result["total"] == 0
            assert result["candidates"] == []
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)


class TestGetCandidateComparisonWithCounterEvidence:
    @pytest.fixture()
    def db_with_ce(self):
        c1 = _make_candidate_row(
            symbol="600519.SH", name="贵州茅台", mandate_score=80,
            counter_evidence_json=json.dumps([{"rule": "overheated", "severity": 0.8}]),
            blocking_evidence_gaps_json=json.dumps(["政策原文", "订单数据"]),
        )
        c2 = _make_candidate_row(
            symbol="000001.SZ", name="平安银行", mandate_score=70,
            counter_evidence_json=json.dumps([]),
            blocking_evidence_gaps_json=json.dumps([]),
        )
        c3 = _make_candidate_row(
            symbol="601689.SH", name="拓普集团", mandate_score=60,
            counter_evidence_json=json.dumps([{"rule": "weak_path", "severity": 0.3}]),
            blocking_evidence_gaps_json=json.dumps(["资金流"]),
        )
        db_path = _create_test_db([c1, c2, c3])
        yield db_path
        if os.path.exists(db_path):
            os.unlink(db_path)

    def test_sort_by_counter_evidence_desc(self, db_with_ce):
        result = get_candidate_comparison("2026-06-08", sort_by="counter_evidence_count", sort_order="desc", tf_db_path=db_with_ce)
        assert result["status"] == "ok"
        assert result["total"] == 3

    def test_sort_by_evidence_gap_count_asc(self, db_with_ce):
        result = get_candidate_comparison("2026-06-08", sort_by="evidence_gap_count", sort_order="asc", tf_db_path=db_with_ce)
        gaps = [len(c.get("blocking_evidence_gaps", [])) for c in result["candidates"]]
        assert gaps == sorted(gaps)


class TestGetCandidateComparisonPoolFilter:
    @pytest.fixture()
    def db_mixed(self):
        c1 = _make_candidate_row(symbol="600519.SH", candidate_type="POLICY_AMBUSH")
        c2 = _make_candidate_row(symbol="000001.SZ", candidate_type="TECH_TRADE")
        c3 = _make_candidate_row(symbol="601689.SH", candidate_type="UNCLASSIFIED_DATA_GAP")
        db_path = _create_test_db([c1, c2, c3])
        yield db_path
        if os.path.exists(db_path):
            os.unlink(db_path)

    def test_pool_haotian(self, db_mixed):
        result = get_candidate_comparison("2026-06-08", pool="haotian", tf_db_path=db_mixed)
        types = [c["candidate_type"] for c in result["candidates"]]
        assert all(t in ("POLICY_AMBUSH", "POLICY_CONFIRM") for t in types)

    def test_pool_tech(self, db_mixed):
        result = get_candidate_comparison("2026-06-08", pool="tech", tf_db_path=db_mixed)
        types = [c["candidate_type"] for c in result["candidates"]]
        assert all(t == "TECH_TRADE" for t in types)

    def test_pool_all(self, db_mixed):
        result = get_candidate_comparison("2026-06-08", pool="all", tf_db_path=db_mixed)
        assert result["total"] == 3


class TestAcceptanceUI010:
    def test_three_mock_candidates_stable(self):
        c1 = _make_candidate_row(symbol="A.SH", mandate_score=90, ambush_score=85, tradeflow_data_completeness=0.9, company_role="LEADER", topic_lifecycle_state="ACCELERATING", candidate_type="POLICY_AMBUSH")
        c2 = _make_candidate_row(symbol="B.SZ", mandate_score=50, ambush_score=40, tradeflow_data_completeness=0.5, company_role="CONCEPT_ONLY", topic_lifecycle_state="CROWDED", candidate_type="TECH_TRADE")
        c3 = _make_candidate_row(symbol="C.SH", mandate_score=70, ambush_score=65, tradeflow_data_completeness=0.7, company_role="CORE_SUPPLIER", topic_lifecycle_state="EMERGING", candidate_type="POLICY_CONFIRM")
        db_path = _create_test_db([c1, c2, c3])
        try:
            result = get_candidate_comparison("2026-06-08", sort_by="mandate_score", tf_db_path=db_path)
            assert result["status"] == "ok"
            assert result["total"] == 3
            symbols = [c["symbol"] for c in result["candidates"]]
            assert symbols == ["A.SH", "C.SH", "B.SZ"]
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_sort_by_coverage_stable(self):
        c1 = _make_candidate_row(symbol="A.SH", tradeflow_data_completeness=0.3)
        c2 = _make_candidate_row(symbol="B.SZ", tradeflow_data_completeness=0.9)
        c3 = _make_candidate_row(symbol="C.SH", tradeflow_data_completeness=0.6)
        db_path = _create_test_db([c1, c2, c3])
        try:
            result = get_candidate_comparison("2026-06-08", sort_by="evidence_coverage", tf_db_path=db_path)
            coverages = [c["tradeflow_data_completeness"] for c in result["candidates"]]
            assert coverages == [0.9, 0.6, 0.3]
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_sort_by_counter_evidence_stable(self):
        c1 = _make_candidate_row(symbol="A.SH", counter_evidence_json=json.dumps([{"severity": 0.9}]))
        c2 = _make_candidate_row(symbol="B.SZ", counter_evidence_json=json.dumps([]))
        c3 = _make_candidate_row(symbol="C.SH", counter_evidence_json=json.dumps([{"severity": 0.3}]))
        db_path = _create_test_db([c1, c2, c3])
        try:
            result = get_candidate_comparison("2026-06-08", sort_by="counter_evidence_count", tf_db_path=db_path)
            assert result["status"] == "ok"
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_sort_by_evidence_gap_fewest_first(self):
        c1 = _make_candidate_row(symbol="A.SH", blocking_evidence_gaps_json=json.dumps(["a", "b", "c"]))
        c2 = _make_candidate_row(symbol="B.SZ", blocking_evidence_gaps_json=json.dumps([]))
        c3 = _make_candidate_row(symbol="C.SH", blocking_evidence_gaps_json=json.dumps(["x"]))
        db_path = _create_test_db([c1, c2, c3])
        try:
            result = get_candidate_comparison("2026-06-08", sort_by="evidence_gap_count", sort_order="asc", tf_db_path=db_path)
            gap_counts = [len(c.get("blocking_evidence_gaps", [])) for c in result["candidates"]]
            assert gap_counts == [0, 1, 3]
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_runtime_tier_meta(self):
        c1 = _make_candidate_row()
        db_path = _create_test_db([c1])
        try:
            result = get_candidate_comparison("2026-06-08", tf_db_path=db_path)
            rtm = result["runtime_tier_meta"]
            assert rtm["runtime_tier"] == "FAST_RADAR"
            assert rtm["llm_allowed"] is False
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_long_text_not_in_sort_keys(self):
        c1 = _make_candidate_row(symbol="A.SH", mandate_topic="这是一个非常长的政策主题名称用于测试是否会导致问题")
        db_path = _create_test_db([c1])
        try:
            result = get_candidate_comparison("2026-06-08", tf_db_path=db_path)
            assert result["status"] == "ok"
            assert result["total"] == 1
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_no_strong_action_words(self):
        c1 = _make_candidate_row()
        db_path = _create_test_db([c1])
        try:
            result = get_candidate_comparison("2026-06-08", tf_db_path=db_path)
            text = json.dumps(result, ensure_ascii=False)
            for word in ["买入", "卖出", "清仓", "满仓", "重仓"]:
                assert word not in text
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_empty_fields_show_default(self):
        c1 = _make_candidate_row(company_role="", topic_lifecycle_state="", mandate_topic="")
        db_path = _create_test_db([c1])
        try:
            result = get_candidate_comparison("2026-06-08", tf_db_path=db_path)
            c = result["candidates"][0]
            assert c["company_role"] == ""
            assert c["topic_lifecycle_state"] == ""
            assert c["mandate_topic"] == ""
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)
