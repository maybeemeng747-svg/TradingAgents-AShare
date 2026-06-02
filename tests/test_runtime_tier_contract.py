# [PERF-001] runtime_tier_contract
"""Tests for the runtime tier contract.

Validates:
1. RuntimeTier enum: FAST_RADAR / LIGHT_RESEARCH / FULL_TA
2. RuntimeTierSpec fields for each tier
3. tier_to_meta produces correct dict
4. TradeFlow endpoints all map to FAST_RADAR
5. tradeflow_meta injects runtime_tier_meta into all TradeFlow service responses
6. FULL_TA requires confirmation
7. Scheduler defaults to LIGHT_RESEARCH (not FULL_TA)
8. No live LLM/TA triggered
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

# ── Module under test ──
from api.runtime_tier import (
    RuntimeTier,
    RuntimeTierSpec,
    all_tier_specs,
    get_tier_spec,
    is_full_ta_allowed_without_confirmation,
    scheduler_default_tier,
    tier_labels_cn,
    tier_to_meta,
    tradeflow_endpoint_tier,
    tradeflow_meta,
)
from api.tradeflow_schemas import RuntimeTierMeta


# ═══════════════════════════════════════════════════════════════════
# TestRuntimeTierEnum
# ═══════════════════════════════════════════════════════════════════

class TestRuntimeTierEnum:
    def test_three_tiers(self):
        assert set(RuntimeTier) == {
            RuntimeTier.FAST_RADAR,
            RuntimeTier.LIGHT_RESEARCH,
            RuntimeTier.FULL_TA,
        }

    def test_values(self):
        assert RuntimeTier.FAST_RADAR.value == "FAST_RADAR"
        assert RuntimeTier.LIGHT_RESEARCH.value == "LIGHT_RESEARCH"
        assert RuntimeTier.FULL_TA.value == "FULL_TA"

    def test_from_string(self):
        assert RuntimeTier("FAST_RADAR") == RuntimeTier.FAST_RADAR
        assert RuntimeTier("LIGHT_RESEARCH") == RuntimeTier.LIGHT_RESEARCH
        assert RuntimeTier("FULL_TA") == RuntimeTier.FULL_TA

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            RuntimeTier("INVALID")

    def test_str_comparison(self):
        assert RuntimeTier.FAST_RADAR == "FAST_RADAR"
        assert RuntimeTier.LIGHT_RESEARCH == "LIGHT_RESEARCH"
        assert RuntimeTier.FULL_TA == "FULL_TA"


# ═══════════════════════════════════════════════════════════════════
# TestTierSpecs
# ═══════════════════════════════════════════════════════════════════

class TestTierSpecs:
    def test_fast_radar_spec(self):
        spec = get_tier_spec(RuntimeTier.FAST_RADAR)
        assert spec.label_cn == "快速筛选"
        assert spec.expected_latency == "5-30s"
        assert spec.llm_allowed is False
        assert spec.requires_confirmation is False
        assert spec.cost_risk == "none"

    def test_light_research_spec(self):
        spec = get_tier_spec(RuntimeTier.LIGHT_RESEARCH)
        assert spec.label_cn == "轻量研究"
        assert spec.expected_latency == "1-3min"
        assert spec.llm_allowed is True
        assert spec.requires_confirmation is False
        assert spec.cost_risk == "low"

    def test_full_ta_spec(self):
        spec = get_tier_spec(RuntimeTier.FULL_TA)
        assert spec.label_cn == "完整 TA"
        assert spec.expected_latency == "10-20min"
        assert spec.llm_allowed is True
        assert spec.requires_confirmation is True
        assert spec.cost_risk == "high"

    def test_all_specs_count(self):
        assert len(all_tier_specs()) == 3

    def test_all_specs_have_description(self):
        for spec in all_tier_specs():
            assert spec.description
            assert len(spec.description) > 10


# ═══════════════════════════════════════════════════════════════════
# TestTierToMeta
# ═══════════════════════════════════════════════════════════════════

class TestTierToMeta:
    def test_fast_meta_keys(self):
        meta = tier_to_meta(RuntimeTier.FAST_RADAR)
        assert set(meta.keys()) == {
            "runtime_tier", "expected_latency", "llm_allowed",
            "requires_confirmation", "cost_risk", "tier_label", "tier_description",
        }

    def test_fast_meta_values(self):
        meta = tier_to_meta(RuntimeTier.FAST_RADAR)
        assert meta["runtime_tier"] == "FAST_RADAR"
        assert meta["expected_latency"] == "5-30s"
        assert meta["llm_allowed"] is False
        assert meta["requires_confirmation"] is False
        assert meta["cost_risk"] == "none"
        assert meta["tier_label"] == "快速筛选"

    def test_full_ta_meta_values(self):
        meta = tier_to_meta(RuntimeTier.FULL_TA)
        assert meta["runtime_tier"] == "FULL_TA"
        assert meta["llm_allowed"] is True
        assert meta["requires_confirmation"] is True
        assert meta["cost_risk"] == "high"

    def test_light_meta_values(self):
        meta = tier_to_meta(RuntimeTier.LIGHT_RESEARCH)
        assert meta["runtime_tier"] == "LIGHT_RESEARCH"
        assert meta["llm_allowed"] is True
        assert meta["requires_confirmation"] is False


# ═══════════════════════════════════════════════════════════════════
# TestTradeflowEndpointTier
# ═══════════════════════════════════════════════════════════════════

class TestTradeflowEndpointTier:
    TRADEFLOW_ENDPOINTS = [
        "tradeflow_daily_plan",
        "tradeflow_candidates",
        "tradeflow_candidate_detail",
        "tradeflow_observe",
        "tradeflow_ta_queue",
        "tradeflow_review",
        "tradeflow_data_health",
        "tradeflow_filtered",
        "tradeflow_discovery",
        "tradeflow_observe_run",
    ]

    @pytest.mark.parametrize("endpoint", TRADEFLOW_ENDPOINTS)
    def test_all_tradeflow_fast(self, endpoint: str):
        assert tradeflow_endpoint_tier(endpoint) == RuntimeTier.FAST_RADAR

    def test_unknown_endpoint_is_light(self):
        assert tradeflow_endpoint_tier("unknown_endpoint") == RuntimeTier.LIGHT_RESEARCH

    def test_discovery_is_fast(self):
        assert tradeflow_endpoint_tier("tradeflow_discovery") == RuntimeTier.FAST_RADAR

    def test_observe_run_is_fast(self):
        assert tradeflow_endpoint_tier("tradeflow_observe_run") == RuntimeTier.FAST_RADAR


# ═══════════════════════════════════════════════════════════════════
# TestTradeflowMeta
# ═══════════════════════════════════════════════════════════════════

class TestTradeflowMeta:
    def test_tradeflow_meta_is_fast(self):
        meta = tradeflow_meta("tradeflow_candidates")
        assert meta["runtime_tier"] == "FAST_RADAR"
        assert meta["llm_allowed"] is False

    def test_tradeflow_meta_includes_latency(self):
        meta = tradeflow_meta("tradeflow_data_health")
        assert meta["expected_latency"] == "5-30s"


# ═══════════════════════════════════════════════════════════════════
# TestFullTAGate
# ═══════════════════════════════════════════════════════════════════

class TestFullTAGate:
    def test_full_ta_not_allowed_without_confirmation(self):
        assert is_full_ta_allowed_without_confirmation("FULL_TA") is False

    def test_fast_allowed(self):
        assert is_full_ta_allowed_without_confirmation("FAST_RADAR") is True

    def test_light_allowed(self):
        assert is_full_ta_allowed_without_confirmation("LIGHT_RESEARCH") is True

    def test_none_not_allowed(self):
        assert is_full_ta_allowed_without_confirmation(None) is False

    def test_invalid_not_allowed(self):
        assert is_full_ta_allowed_without_confirmation("INVALID") is False

    def test_empty_not_allowed(self):
        assert is_full_ta_allowed_without_confirmation("") is False


# ═══════════════════════════════════════════════════════════════════
# TestSchedulerDefault
# ═══════════════════════════════════════════════════════════════════

class TestSchedulerDefault:
    def test_default_is_not_full_ta(self):
        assert scheduler_default_tier() != RuntimeTier.FULL_TA

    def test_default_is_light_research(self):
        assert scheduler_default_tier() == RuntimeTier.LIGHT_RESEARCH


# ═══════════════════════════════════════════════════════════════════
# TestTierLabelsCN
# ═══════════════════════════════════════════════════════════════════

class TestTierLabelsCN:
    def test_three_labels(self):
        labels = tier_labels_cn()
        assert labels["FAST_RADAR"] == "快速筛选"
        assert labels["LIGHT_RESEARCH"] == "轻量研究"
        assert labels["FULL_TA"] == "完整 TA"

    def test_count(self):
        assert len(tier_labels_cn()) == 3


# ═══════════════════════════════════════════════════════════════════
# TestRuntimeTierMetaSchema
# ═══════════════════════════════════════════════════════════════════

class TestRuntimeTierMetaSchema:
    def test_default_is_fast_radar(self):
        meta = RuntimeTierMeta()
        assert meta.runtime_tier == "FAST_RADAR"
        assert meta.llm_allowed is False
        assert meta.requires_confirmation is False
        assert meta.cost_risk == "none"

    def test_custom_values(self):
        meta = RuntimeTierMeta(
            runtime_tier="FULL_TA",
            expected_latency="10-20min",
            llm_allowed=True,
            requires_confirmation=True,
            cost_risk="high",
            tier_label="完整 TA",
            tier_description="Full 14-agent analysis",
        )
        assert meta.runtime_tier == "FULL_TA"
        assert meta.cost_risk == "high"

    def test_from_dict_roundtrip(self):
        d = tier_to_meta(RuntimeTier.LIGHT_RESEARCH)
        meta = RuntimeTierMeta(**d)
        assert meta.runtime_tier == "LIGHT_RESEARCH"
        assert meta.tier_label == "轻量研究"


# ═══════════════════════════════════════════════════════════════════
# TestTradeflowServiceMetaInjection
# ═══════════════════════════════════════════════════════════════════

def _create_test_db(tmp_path: str) -> str:
    db_path = os.path.join(tmp_path, "tradeflow.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tradeflow_daily_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT NOT NULL,
            mode TEXT DEFAULT 'pre_market',
            summary TEXT DEFAULT '',
            candidates_json TEXT DEFAULT '[]',
            metadata_json TEXT DEFAULT '{}',
            created_at TEXT DEFAULT '',
            plan_date TEXT DEFAULT '',
            effective_trade_date TEXT DEFAULT '',
            observe_date TEXT DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tradeflow_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            name TEXT DEFAULT '',
            tier TEXT DEFAULT '',
            composite_score REAL DEFAULT 0.0,
            score REAL DEFAULT 0.0,
            strategy_tags_json TEXT DEFAULT '[]',
            primary_strategy TEXT DEFAULT '',
            trigger_price REAL,
            support_price REAL,
            invalid_price REAL,
            need_deep_ta INTEGER DEFAULT 0,
            observe_state TEXT DEFAULT 'WAITING',
            observe_trigger_count INTEGER DEFAULT 0,
            observe_first_trigger_time TEXT DEFAULT '',
            tradeflow_data_completeness REAL DEFAULT 0.0,
            missing_data_fields_json TEXT DEFAULT '[]',
            data_completeness REAL DEFAULT 0.0,
            missing_evidence_json TEXT DEFAULT '[]',
            game_balance TEXT DEFAULT '',
            bull_case TEXT DEFAULT '',
            bear_case TEXT DEFAULT '',
            policy_case TEXT DEFAULT '',
            fund_flow_case TEXT DEFAULT '',
            why_deep_ta TEXT DEFAULT '',
            why_not_deep_ta TEXT DEFAULT '',
            risk_flags_json TEXT DEFAULT '[]',
            policy_tags_json TEXT DEFAULT '[]',
            version_score REAL DEFAULT 0.0,
            narrative_score REAL DEFAULT 0.0,
            fund_flow_anomaly_score REAL DEFAULT 0.0,
            fund_flow_anomaly_tags_json TEXT DEFAULT '[]',
            fund_flow_unit_verified INTEGER DEFAULT 0,
            evidence_gate_applied INTEGER DEFAULT 0,
            deep_ta_status TEXT DEFAULT '',
            deep_ta_dispatch_reason TEXT DEFAULT '',
            deep_ta_model TEXT DEFAULT '',
            source TEXT DEFAULT 'manual',
            universe_sources_json TEXT DEFAULT '[]',
            resonance_count INTEGER DEFAULT 0,
            ta_budget_priority INTEGER DEFAULT 0,
            tier_reason TEXT DEFAULT '',
            missing_evidence_for_upgrade_json TEXT DEFAULT '[]',
            candidate_type TEXT DEFAULT '',
            mandate_score_component REAL DEFAULT 0.0,
            ambush_score REAL DEFAULT 0.0,
            mandate_topic TEXT DEFAULT '',
            company_role TEXT DEFAULT '',
            beneficiary_path_json TEXT DEFAULT '[]',
            candidate_type_reason TEXT DEFAULT '',
            deep_ta_route TEXT DEFAULT '',
            research_queue TEXT DEFAULT '',
            research_intent TEXT DEFAULT '',
            research_route_reason TEXT DEFAULT '',
            watchlist_note TEXT DEFAULT '',
            watchlist_note_suggested TEXT DEFAULT '',
            watchlist_topic TEXT DEFAULT '',
            watchlist_benefit_score REAL DEFAULT 0.0,
            watchlist_consensus_score REAL DEFAULT 0.0,
            watchlist_evidence_gap_json TEXT DEFAULT '[]',
            plan_date TEXT DEFAULT '',
            effective_trade_date TEXT DEFAULT '',
            observe_date TEXT DEFAULT '',
            status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT '',
            evidence_json TEXT DEFAULT '{}',
            policy_evidence_refs_json TEXT DEFAULT '[]',
            narrative_evidence_refs_json TEXT DEFAULT '[]',
            narrative_reasons_json TEXT DEFAULT '[]',
            risk_evidence_refs_json TEXT DEFAULT '[]',
            risk_reasons_json TEXT DEFAULT '[]',
            game_balance_refs_json TEXT DEFAULT '[]',
            fund_flow_anomaly_refs_json TEXT DEFAULT '[]',
            fund_flow_individual_summary TEXT DEFAULT '',
            fund_flow_board_summary TEXT DEFAULT '',
            signal_category_hits_json TEXT DEFAULT '[]',
            positive_category_count INTEGER DEFAULT 0,
            what_to_upgrade_json TEXT DEFAULT '[]',
            deep_ta_report_path TEXT DEFAULT '',
            deep_ta_dispatch_time TEXT DEFAULT '',
            deep_ta_position_context TEXT DEFAULT '',
            ambush_reasons_json TEXT DEFAULT '[]',
            ambush_evidence_refs_json TEXT DEFAULT '[]',
            mandate_evidence_refs_json TEXT DEFAULT '[]'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tradeflow_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            signal_type TEXT DEFAULT '',
            signal_time TEXT DEFAULT '',
            evidence_json TEXT DEFAULT '{}'
        )
    """)
    conn.commit()
    conn.close()
    return db_path


def _insert_sample_candidate(db_path: str, trade_date: str = "2026-06-02"):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO tradeflow_candidates (trade_date, symbol, name, tier, composite_score, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (trade_date, "600519.SH", "贵州茅台", "A", 85.0, "2026-06-02T09:00:00", "2026-06-02T09:00:00"),
    )
    conn.commit()
    conn.close()


class TestTradeflowServiceMetaInjection:
    def test_candidates_has_runtime_tier_meta(self):
        from api.services.tradeflow_service import get_candidates

        with tempfile.TemporaryDirectory() as tmp:
            db_path = _create_test_db(tmp)
            _insert_sample_candidate(db_path, "2026-06-02")
            result = get_candidates("2026-06-02", tf_db_path=db_path)
            assert "runtime_tier_meta" in result
            assert result["runtime_tier_meta"]["runtime_tier"] == "FAST_RADAR"
            assert result["runtime_tier_meta"]["llm_allowed"] is False

    def test_observe_has_runtime_tier_meta(self):
        from api.services.tradeflow_service import get_observe

        with tempfile.TemporaryDirectory() as tmp:
            db_path = _create_test_db(tmp)
            _insert_sample_candidate(db_path, "2026-06-02")
            result = get_observe("2026-06-02", tf_db_path=db_path)
            assert "runtime_tier_meta" in result
            assert result["runtime_tier_meta"]["runtime_tier"] == "FAST_RADAR"

    def test_data_health_has_runtime_tier_meta(self):
        from api.services.tradeflow_service import get_data_health

        with tempfile.TemporaryDirectory() as tmp:
            db_path = _create_test_db(tmp)
            result = get_data_health(tf_db_path=db_path)
            assert "runtime_tier_meta" in result
            assert result["runtime_tier_meta"]["runtime_tier"] == "FAST_RADAR"
            assert result["runtime_tier_meta"]["expected_latency"] == "5-30s"

    def test_ta_queue_has_runtime_tier_meta(self):
        from api.services.tradeflow_service import get_ta_queue

        with tempfile.TemporaryDirectory() as tmp:
            db_path = _create_test_db(tmp)
            _insert_sample_candidate(db_path, "2026-06-02")
            result = get_ta_queue("2026-06-02", tf_db_path=db_path)
            assert "runtime_tier_meta" in result
            assert result["runtime_tier_meta"]["runtime_tier"] == "FAST_RADAR"

    def test_review_has_runtime_tier_meta(self):
        from api.services.tradeflow_service import get_review

        with tempfile.TemporaryDirectory() as tmp:
            db_path = _create_test_db(tmp)
            result = get_review("2026-06-02", tf_db_path=db_path)
            assert "runtime_tier_meta" in result

    def test_filtered_has_runtime_tier_meta(self):
        from api.services.tradeflow_service import get_filtered

        with tempfile.TemporaryDirectory() as tmp:
            db_path = _create_test_db(tmp)
            result = get_filtered("2026-06-02", tf_db_path=db_path)
            assert "runtime_tier_meta" in result

    def test_candidate_detail_has_runtime_tier_meta(self):
        from api.services.tradeflow_service import get_candidate_detail

        with tempfile.TemporaryDirectory() as tmp:
            db_path = _create_test_db(tmp)
            _insert_sample_candidate(db_path, "2026-06-02")
            result = get_candidate_detail("600519.SH", "2026-06-02", tf_db_path=db_path)
            assert "runtime_tier_meta" in result

    def test_daily_plan_no_data_has_meta(self):
        from api.services.tradeflow_service import get_daily_plan

        with tempfile.TemporaryDirectory() as tmp:
            db_path = _create_test_db(tmp)
            result = get_daily_plan("2026-06-02", tf_db_path=db_path)
            assert result.get("status") == "no_data"
            assert result["runtime_tier_meta"]["runtime_tier"] == "FAST_RADAR"  # [PERF-001]

    def test_no_db_candidates_has_meta(self):
        from api.services.tradeflow_service import get_candidates

        result = get_candidates("2026-06-02", tf_db_path="/nonexistent/path.db")
        assert result.get("status") == "no_data"
        assert result["runtime_tier_meta"]["runtime_tier"] == "FAST_RADAR"  # [PERF-001]


# ═══════════════════════════════════════════════════════════════════
# TestResponseModels
# ═══════════════════════════════════════════════════════════════════

class TestResponseModels:
    def test_candidates_response_has_meta(self):
        from api.tradeflow_schemas import TradeFlowCandidatesResponse

        resp = TradeFlowCandidatesResponse()
        assert resp.runtime_tier_meta.runtime_tier == "FAST_RADAR"

    def test_observe_response_has_meta(self):
        from api.tradeflow_schemas import TradeFlowObserveResponse

        resp = TradeFlowObserveResponse()
        assert resp.runtime_tier_meta.runtime_tier == "FAST_RADAR"

    def test_data_health_response_has_meta(self):
        from api.tradeflow_schemas import TradeFlowDataHealthResponse

        resp = TradeFlowDataHealthResponse()
        assert resp.runtime_tier_meta.runtime_tier == "FAST_RADAR"

    def test_daily_plan_response_has_meta(self):
        from api.tradeflow_schemas import TradeFlowDailyPlanResponse

        resp = TradeFlowDailyPlanResponse()
        assert resp.runtime_tier_meta.runtime_tier == "FAST_RADAR"

    def test_ta_queue_response_has_meta(self):
        from api.tradeflow_schemas import TradeFlowTAQueueResponse

        resp = TradeFlowTAQueueResponse()
        assert resp.runtime_tier_meta.runtime_tier == "FAST_RADAR"

    def test_review_response_has_meta(self):
        from api.tradeflow_schemas import TradeFlowReviewResponse

        resp = TradeFlowReviewResponse()
        assert resp.runtime_tier_meta.runtime_tier == "FAST_RADAR"

    def test_filtered_response_has_meta(self):
        from api.tradeflow_schemas import TradeFlowFilteredResponse

        resp = TradeFlowFilteredResponse()
        assert resp.runtime_tier_meta.runtime_tier == "FAST_RADAR"

    def test_candidate_detail_response_has_meta(self):
        from api.tradeflow_schemas import TradeFlowCandidateDetailResponse

        resp = TradeFlowCandidateDetailResponse()
        assert resp.runtime_tier_meta.runtime_tier == "FAST_RADAR"

    def test_response_json_includes_meta(self):
        from api.tradeflow_schemas import TradeFlowCandidatesResponse

        resp = TradeFlowCandidatesResponse()
        d = resp.model_dump()
        assert "runtime_tier_meta" in d
        assert d["runtime_tier_meta"]["runtime_tier"] == "FAST_RADAR"

    def test_custom_meta_overrides(self):
        from api.tradeflow_schemas import TradeFlowCandidatesResponse

        custom = RuntimeTierMeta(
            runtime_tier="LIGHT_RESEARCH",
            expected_latency="1-3min",
            llm_allowed=True,
            cost_risk="low",
            tier_label="轻量研究",
        )
        resp = TradeFlowCandidatesResponse(runtime_tier_meta=custom)
        assert resp.runtime_tier_meta.runtime_tier == "LIGHT_RESEARCH"
        assert resp.runtime_tier_meta.llm_allowed is True


# ═══════════════════════════════════════════════════════════════════
# TestAcceptancePERF001
# ═══════════════════════════════════════════════════════════════════

class TestAcceptancePERF001:
    def test_tradeflow_candidates_returns_fast_tier(self):
        from api.services.tradeflow_service import get_candidates

        with tempfile.TemporaryDirectory() as tmp:
            db_path = _create_test_db(tmp)
            _insert_sample_candidate(db_path)
            result = get_candidates("2026-06-02", tf_db_path=db_path)
            assert result["runtime_tier_meta"]["runtime_tier"] == "FAST_RADAR"

    def test_tradeflow_data_health_returns_fast_tier(self):
        from api.services.tradeflow_service import get_data_health

        with tempfile.TemporaryDirectory() as tmp:
            db_path = _create_test_db(tmp)
            result = get_data_health(tf_db_path=db_path)
            assert result["runtime_tier_meta"]["runtime_tier"] == "FAST_RADAR"

    def test_full_ta_requires_confirmation(self):
        assert is_full_ta_allowed_without_confirmation("FULL_TA") is False

    def test_scheduler_not_full_ta(self):
        assert scheduler_default_tier() != RuntimeTier.FULL_TA

    def test_three_tier_labels_cn(self):
        labels = tier_labels_cn()
        assert labels["FAST_RADAR"] == "快速筛选"
        assert labels["LIGHT_RESEARCH"] == "轻量研究"
        assert labels["FULL_TA"] == "完整 TA"

    def test_fast_radar_no_llm(self):
        meta = tier_to_meta(RuntimeTier.FAST_RADAR)
        assert meta["llm_allowed"] is False

    def test_full_ta_llm_with_confirmation(self):
        meta = tier_to_meta(RuntimeTier.FULL_TA)
        assert meta["llm_allowed"] is True
        assert meta["requires_confirmation"] is True

    def test_all_tradeflow_endpoints_fast(self):
        fast = {
            "tradeflow_daily_plan", "tradeflow_candidates", "tradeflow_candidate_detail",
            "tradeflow_observe", "tradeflow_ta_queue", "tradeflow_review",
            "tradeflow_data_health", "tradeflow_filtered", "tradeflow_discovery",
            "tradeflow_observe_run",
        }
        for ep in fast:
            assert tradeflow_endpoint_tier(ep) == RuntimeTier.FAST_RADAR, f"{ep} should be FAST_RADAR"

    def test_no_live_ta_triggered(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = _create_test_db(tmp)
            _insert_sample_candidate(db_path)
            from api.services.tradeflow_service import (
                get_candidates, get_observe, get_data_health,
                get_ta_queue, get_review, get_filtered,
                get_candidate_detail,
            )
            for fn in [get_candidates, get_observe, get_ta_queue, get_review, get_filtered]:
                result = fn("2026-06-02", tf_db_path=db_path)  # type: ignore[operator]
                assert result["runtime_tier_meta"]["llm_allowed"] is False
            result = get_data_health(tf_db_path=db_path)
            assert result["runtime_tier_meta"]["llm_allowed"] is False
            result = get_candidate_detail("600519.SH", "2026-06-02", tf_db_path=db_path)
            assert result["runtime_tier_meta"]["llm_allowed"] is False
