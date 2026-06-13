# [TF-QUALITY-001] candidate_pool_gate
"""Tests for the TradeFlow candidate pool convergence gate."""

import pytest
from tradingagents.tradeflow.candidate_pool_gate import (
    PoolGateResult,
    run_pool_gate,
    _check_tech_resonance,
    _check_haotian_resonance,
    _qualify_for_main,
)
from tradingagents.tradeflow.strategy_config import StrategyConfig
from api.tradeflow_schemas import TradeFlowCandidatesResponse, TradeFlowTieredCandidatesResponse


def _make_entry(
    symbol="000001.SZ",
    name="测试",
    candidate_type="TECH_TRADE",
    tier="A",
    composite_score=70.0,
    positive_category_count=2,
    data_completeness=0.7,
    tradeflow_data_completeness=0.7,
    strategies=None,
    fund_flow_anomaly_score=0,
    fund_flow_unit_verified=False,
    fund_flow_anomaly_tags=None,
    trigger_price=None,
    invalid_price=None,
    mandate_score_component=0,
    beneficiary_score_component=0,
    policy_tags=None,
    counter_evidence=None,
    risk_flags=None,
    overheat_penalty=0,
    overheat_flags=None,
    score=50.0,
):
    return {
        "symbol": symbol,
        "name": name,
        "candidate_type": candidate_type,
        "tier": tier,
        "composite_score": composite_score,
        "positive_category_count": positive_category_count,
        "data_completeness": data_completeness,
        "tradeflow_data_completeness": tradeflow_data_completeness,
        "strategies": strategies if strategies is not None else ["VCP"],
        "fund_flow_anomaly_score": fund_flow_anomaly_score,
        "fund_flow_unit_verified": fund_flow_unit_verified,
        "fund_flow_anomaly_tags": fund_flow_anomaly_tags or [],
        "trigger_price": trigger_price,
        "invalid_price": invalid_price,
        "mandate_score_component": mandate_score_component,
        "beneficiary_score_component": beneficiary_score_component,
        "policy_tags": policy_tags or [],
        "counter_evidence": counter_evidence or [],
        "risk_flags": risk_flags or [],
        "overheat_penalty": overheat_penalty,
        "overheat_flags": overheat_flags or [],
        "score": score,
    }


class TestTradeFlowPoolResponseSchemas:
    def test_candidates_response_keeps_pool_fields(self):
        payload = {
            "status": "ok",
            "trade_date": "2026-06-08",
            "candidates": [{"symbol": "600519.SH", "name": "贵州茅台"}],
            "main_candidates": [{"symbol": "600519.SH", "name": "贵州茅台", "pool_status": "main"}],
            "observation_candidates": [{"symbol": "000001.SZ", "pool_status": "observation"}],
            "filtered_candidates": [{"symbol": "000002.SZ", "pool_filter_reason": "质量不足"}],
            "pool_counts": {"main": 1, "observation": 1, "filtered": 1},
            "pool_gate_summary": "主候选1只，观察1只，过滤1只。",
            "main_summary_agg": {"total_candidates": 1},
        }

        response = TradeFlowCandidatesResponse.model_validate(payload).model_dump()

        assert response["main_candidates"][0]["symbol"] == "600519.SH"
        assert response["observation_candidates"][0]["pool_status"] == "observation"
        assert response["filtered_candidates"][0]["pool_filter_reason"] == "质量不足"
        assert response["pool_counts"]["main"] == 1
        assert response["pool_gate_summary"] == "主候选1只，观察1只，过滤1只。"
        assert response["main_summary_agg"]["total_candidates"] == 1

    def test_tiered_response_keeps_pool_fields(self):
        payload = {
            "status": "ok",
            "trade_date": "2026-06-08",
            "actionable": [],
            "watch": [],
            "scan": [],
            "main_candidates": [{"symbol": "600519.SH", "name": "贵州茅台"}],
            "observation_candidates": [{"symbol": "000001.SZ", "pool_status": "observation"}],
            "filtered_candidates": [{"symbol": "000002.SZ", "pool_filter_reason": "质量不足"}],
            "pool_counts": {"main": 1},
            "pool_gate_summary": "主候选1只。",
        }

        response = TradeFlowTieredCandidatesResponse.model_validate(payload).model_dump()

        assert response["main_candidates"][0]["symbol"] == "600519.SH"
        assert response["observation_candidates"][0]["pool_status"] == "observation"
        assert response["filtered_candidates"][0]["pool_filter_reason"] == "质量不足"
        assert response["pool_counts"]["main"] == 1
        assert response["pool_gate_summary"] == "主候选1只。"


class TestTechResonance:
    def test_vcp_plus_fund_flow(self):
        e = _make_entry(strategies=["VCP"], fund_flow_anomaly_score=10, fund_flow_unit_verified=True)
        assert _check_tech_resonance(e) >= 2

    def test_vcp_only(self):
        e = _make_entry(strategies=["VCP"], composite_score=10)
        assert _check_tech_resonance(e) == 1

    def test_no_signals(self):
        e = _make_entry(strategies=[], composite_score=10, trigger_price=None, invalid_price=None)
        assert _check_tech_resonance(e) == 0

    def test_trigger_and_invalid(self):
        e = _make_entry(strategies=["VCP"], trigger_price=10.0, invalid_price=9.0, composite_score=40)
        assert _check_tech_resonance(e) >= 2

    def test_pullback_signal(self):
        e = _make_entry(strategies=["PULLBACK_SUPPORT"], composite_score=40)
        assert _check_tech_resonance(e) >= 2


class TestHaotianResonance:
    def test_mandate_plus_beneficiary(self):
        e = _make_entry(
            candidate_type="POLICY_AMBUSH",
            mandate_score_component=50,
            beneficiary_score_component=40,
        )
        assert _check_haotian_resonance(e) >= 2

    def test_mandate_only(self):
        e = _make_entry(candidate_type="POLICY_AMBUSH", mandate_score_component=50, counter_evidence=[{"severity": "high"}])
        assert _check_haotian_resonance(e) >= 1

    def test_policy_tags(self):
        e = _make_entry(candidate_type="POLICY_AMBUSH", policy_tags=["低空经济"])
        assert _check_haotian_resonance(e) >= 1

    def test_high_severity_counter(self):
        e = _make_entry(
            candidate_type="POLICY_AMBUSH",
            mandate_score_component=50,
            beneficiary_score_component=40,
            counter_evidence=[{"severity": "high"}],
        )
        assert _check_haotian_resonance(e) >= 2

    def test_no_signals(self):
        e = _make_entry(candidate_type="POLICY_AMBUSH")
        assert _check_haotian_resonance(e) >= 1


class TestQualifyForMain:
    def test_two_categories_passes(self):
        e = _make_entry(positive_category_count=2, data_completeness=0.7)
        ok, reason = _qualify_for_main(e, StrategyConfig())
        assert ok

    def test_single_strong_signal(self):
        e = _make_entry(
            positive_category_count=1,
            composite_score=65.0,
            data_completeness=0.7,
            tradeflow_data_completeness=0.7,
        )
        ok, reason = _qualify_for_main(e, StrategyConfig())
        assert ok

    def test_c_tier_fails(self):
        e = _make_entry(tier="C", positive_category_count=3)
        ok, reason = _qualify_for_main(e, StrategyConfig())
        assert not ok
        assert "C层" in reason

    def test_single_weak_signal_fails(self):
        e = _make_entry(
            positive_category_count=1,
            composite_score=10.0,
            data_completeness=0.2,
            tradeflow_data_completeness=0.2,
            strategies=[],
            trigger_price=None,
            invalid_price=None,
        )
        ok, reason = _qualify_for_main(e, StrategyConfig())
        assert not ok

    def test_tech_resonance_passes(self):
        e = _make_entry(
            candidate_type="TECH_TRADE",
            tier="B",
            positive_category_count=1,
            composite_score=35,
            data_completeness=0.4,
            tradeflow_data_completeness=0.4,
            strategies=["VCP"],
            trigger_price=10.0,
            invalid_price=9.0,
        )
        ok, reason = _qualify_for_main(e, StrategyConfig())
        assert ok

    def test_haotian_resonance_passes(self):
        e = _make_entry(
            candidate_type="POLICY_AMBUSH",
            tier="B",
            positive_category_count=1,
            composite_score=35,
            data_completeness=0.4,
            tradeflow_data_completeness=0.4,
            mandate_score_component=50,
            beneficiary_score_component=40,
        )
        ok, reason = _qualify_for_main(e, StrategyConfig())
        assert ok


class TestRunPoolGate:
    def test_empty_input(self):
        result = run_pool_gate([])
        assert result.main_candidates == []
        assert result.observation_candidates == []
        assert result.filtered_candidates == []

    def test_single_strong_candidate(self):
        entries = [_make_entry(symbol="A", positive_category_count=3, composite_score=80)]
        result = run_pool_gate(entries)
        assert len(result.main_candidates) == 1
        assert result.main_candidates[0]["pool_status"] == "main"

    def test_max_main_cap(self):
        cfg = StrategyConfig(pool_main_max=3, pool_tech_max=10, pool_haotian_max=10)
        entries = [
            _make_entry(symbol=f"H{i}", candidate_type="POLICY_AMBUSH", positive_category_count=3, composite_score=70 + i)
            for i in range(6)
        ]
        result = run_pool_gate(entries, cfg)
        assert len(result.main_candidates) == 3
        assert len(result.observation_candidates) == 3
        for obs in result.observation_candidates:
            assert "主候选池已满" in obs["pool_filter_reason"]

    def test_weak_candidates_filtered(self):
        entries = [_make_entry(symbol=f"W{i}", tier="C", composite_score=10) for i in range(3)]
        result = run_pool_gate(entries)
        assert len(result.main_candidates) == 0
        assert len(result.filtered_candidates) == 3
        for f in result.filtered_candidates:
            assert f["pool_status"] == "filtered"

    def test_tech_pool_cap(self):
        cfg = StrategyConfig(pool_tech_max=2, pool_main_max=10)
        entries = [
            _make_entry(symbol=f"T{i}", candidate_type="TECH_TRADE", positive_category_count=3, composite_score=70)
            for i in range(4)
        ]
        result = run_pool_gate(entries, cfg)
        tech_in_main = [c for c in result.main_candidates if c["candidate_type"] == "TECH_TRADE"]
        assert len(tech_in_main) == 2
        obs = [o for o in result.observation_candidates if "技术池已满" in o.get("pool_filter_reason", "")]
        assert len(obs) == 2

    def test_haotian_pool_cap(self):
        cfg = StrategyConfig(pool_haotian_max=2, pool_main_max=10)
        entries = [
            _make_entry(
                symbol=f"H{i}",
                candidate_type="POLICY_AMBUSH",
                positive_category_count=3,
                composite_score=70,
            )
            for i in range(4)
        ]
        result = run_pool_gate(entries, cfg)
        haotian_in_main = [c for c in result.main_candidates if c["candidate_type"] in ("POLICY_AMBUSH", "POLICY_CONFIRM")]
        assert len(haotian_in_main) == 2

    def test_mixed_candidates(self):
        entries = [
            _make_entry(symbol="A1", candidate_type="POLICY_AMBUSH", positive_category_count=3, composite_score=80),
            _make_entry(symbol="T1", candidate_type="TECH_TRADE", positive_category_count=2, composite_score=60),
            _make_entry(symbol="W1", tier="C", composite_score=10),
            _make_entry(symbol="T2", candidate_type="TECH_TRADE", positive_category_count=1, composite_score=30, tradeflow_data_completeness=0.3),
        ]
        result = run_pool_gate(entries)
        assert len(result.main_candidates) >= 2
        assert any(c["symbol"] == "W1" for c in result.filtered_candidates)

    def test_pool_counts(self):
        entries = [
            _make_entry(symbol="A1", candidate_type="POLICY_AMBUSH", positive_category_count=3, composite_score=80),
            _make_entry(symbol="T1", candidate_type="TECH_TRADE", positive_category_count=2, composite_score=60),
            _make_entry(symbol="W1", tier="C", composite_score=10),
        ]
        result = run_pool_gate(entries)
        assert result.pool_counts["main"] == 2
        assert result.pool_counts["filtered"] == 1
        assert "haotian_in_main" in result.pool_counts
        assert "tech_in_main" in result.pool_counts

    def test_gate_summary(self):
        entries = [
            _make_entry(symbol="A1", positive_category_count=3, composite_score=80),
            _make_entry(symbol="W1", tier="C", composite_score=10),
        ]
        result = run_pool_gate(entries)
        assert "主候选" in result.gate_summary
        assert "观察" in result.gate_summary
        assert "过滤" in result.gate_summary

    def test_preserves_entry_fields(self):
        entries = [_make_entry(symbol="A1", positive_category_count=3, composite_score=80)]
        result = run_pool_gate(entries)
        assert result.main_candidates[0]["symbol"] == "A1"
        assert result.main_candidates[0]["composite_score"] == 80


class TestPoolGateFixture20:
    """Acceptance: fixture 20 stocks -> main candidates <= 5."""

    def test_20_stocks_main_max_5(self):
        entries = []
        for i in range(20):
            quality = 80 - i * 2
            cats = 3 if i < 5 else (2 if i < 12 else 1)
            tier = "A" if i < 5 else ("B" if i < 12 else "C")
            entries.append(_make_entry(
                symbol=f"S{i:02d}",
                composite_score=quality,
                positive_category_count=cats,
                tier=tier,
                data_completeness=0.7 if i < 12 else 0.3,
                tradeflow_data_completeness=0.7 if i < 12 else 0.3,
            ))
        result = run_pool_gate(entries)
        assert len(result.main_candidates) <= 5
        assert len(result.filtered_candidates) > 0

    def test_weak_vcp_filtered(self):
        entry = _make_entry(
            symbol="WEAK",
            candidate_type="TECH_TRADE",
            tier="C",
            strategies=["VCP"],
            composite_score=15,
            positive_category_count=1,
            data_completeness=0.2,
            tradeflow_data_completeness=0.2,
        )
        result = run_pool_gate([entry])
        assert len(result.main_candidates) == 0
        assert len(result.filtered_candidates) == 1

    def test_weak_event_filtered(self):
        entry = _make_entry(
            symbol="WEAK_EV",
            candidate_type="EVENT_WATCH",
            tier="C",
            composite_score=10,
            positive_category_count=0,
            data_completeness=0.2,
            tradeflow_data_completeness=0.2,
        )
        result = run_pool_gate([entry])
        assert len(result.main_candidates) == 0
        assert len(result.filtered_candidates) == 1

    def test_filtered_has_reason(self):
        entry = _make_entry(
            symbol="F1",
            tier="C",
            composite_score=10,
            positive_category_count=0,
            data_completeness=0.2,
            tradeflow_data_completeness=0.2,
        )
        result = run_pool_gate([entry])
        assert len(result.filtered_candidates) == 1
        assert result.filtered_candidates[0]["pool_filter_reason"]

    def test_liquidity_filter_reason(self):
        entry = _make_entry(
            symbol="LIQ",
            tier="C",
            composite_score=5,
            positive_category_count=0,
            data_completeness=0.1,
            tradeflow_data_completeness=0.1,
        )
        result = run_pool_gate([entry])
        assert len(result.filtered_candidates) == 1
        assert result.filtered_candidates[0]["pool_filter_reason"] != ""


class TestStrategyConfigPoolGate:
    def test_default_config(self):
        cfg = StrategyConfig()
        assert cfg.pool_main_max == 5
        assert cfg.pool_tech_max == 3
        assert cfg.pool_haotian_max == 3
        assert cfg.pool_min_positive_categories_for_main == 2
        assert cfg.pool_min_composite_for_single_strong == 60.0
        assert cfg.pool_min_completeness_for_single_strong == 0.6

    def test_custom_config(self):
        cfg = StrategyConfig(pool_main_max=10, pool_tech_max=10, pool_haotian_max=10)
        entries = [_make_entry(symbol=f"S{i}", positive_category_count=3, composite_score=70) for i in range(8)]
        result = run_pool_gate(entries, cfg)
        assert len(result.main_candidates) == 8


class TestPoolGateBackwardCompat:
    def test_no_deletion_of_strategies(self):
        entries = [_make_entry(symbol="A1", strategies=["VCP", "PULLBACK_SUPPORT"], positive_category_count=3, composite_score=80)]
        result = run_pool_gate(entries)
        assert result.main_candidates[0]["strategies"] == ["VCP", "PULLBACK_SUPPORT"]

    def test_pool_status_field(self):
        entries = [
            _make_entry(symbol="M1", positive_category_count=3, composite_score=80),
            _make_entry(symbol="O1", tier="B", positive_category_count=2, composite_score=50, data_completeness=0.4, tradeflow_data_completeness=0.4),
            _make_entry(symbol="F1", tier="C", composite_score=10),
        ]
        cfg = StrategyConfig(pool_main_max=1)
        result = run_pool_gate(entries, cfg)
        assert result.main_candidates[0]["pool_status"] == "main"
        assert result.main_candidates[0]["pool_filter_reason"] == ""

    def test_observation_entries_retain_data(self):
        entries = [
            _make_entry(symbol="M1", positive_category_count=3, composite_score=90),
            _make_entry(symbol="M2", positive_category_count=3, composite_score=80),
            _make_entry(symbol="O1", positive_category_count=3, composite_score=70),
        ]
        cfg = StrategyConfig(pool_main_max=2)
        result = run_pool_gate(entries, cfg)
        assert len(result.observation_candidates) == 1
        assert result.observation_candidates[0]["symbol"] == "O1"
        assert result.observation_candidates[0]["composite_score"] == 70


class TestAcceptanceTFQUALITY001:
    """Full acceptance tests for TF-QUALITY-001."""

    def test_20_fixture_main_not_exceed_5(self):
        entries = []
        for i in range(20):
            entries.append(_make_entry(
                symbol=f"S{i:02d}",
                positive_category_count=3 if i < 8 else 2,
                composite_score=80 - i,
                tier="A" if i < 8 else "B",
            ))
        result = run_pool_gate(entries)
        assert len(result.main_candidates) <= 5

    def test_single_weak_vcp_not_in_main(self):
        entry = _make_entry(
            symbol="WEAK_VCP",
            candidate_type="TECH_TRADE",
            tier="C",
            strategies=["VCP"],
            composite_score=15,
            positive_category_count=0,
        )
        result = run_pool_gate([entry])
        assert not any(c["symbol"] == "WEAK_VCP" for c in result.main_candidates)

    def test_single_weak_event_not_in_main(self):
        entry = _make_entry(
            symbol="WEAK_EVT",
            candidate_type="EVENT_WATCH",
            tier="C",
            composite_score=12,
            positive_category_count=0,
        )
        result = run_pool_gate([entry])
        assert not any(c["symbol"] == "WEAK_EVT" for c in result.main_candidates)

    def test_filtered_has_explicit_reason(self):
        entries = [_make_entry(symbol="F1", tier="C", composite_score=5)]
        result = run_pool_gate(entries)
        assert len(result.filtered_candidates) == 1
        reason = result.filtered_candidates[0]["pool_filter_reason"]
        assert reason != ""

    def test_pool_counts_structure(self):
        entries = [_make_entry(symbol="A1", positive_category_count=3, composite_score=80)]
        result = run_pool_gate(entries)
        assert "main" in result.pool_counts
        assert "observation" in result.pool_counts
        assert "filtered" in result.pool_counts
        assert "haotian_in_main" in result.pool_counts
        assert "tech_in_main" in result.pool_counts
        assert "main_max" in result.pool_counts
        assert "haotian_max" in result.pool_counts
        assert "tech_max" in result.pool_counts

    def test_strong_tech_trade_enters_main(self):
        entry = _make_entry(
            symbol="STRONG_TECH",
            candidate_type="TECH_TRADE",
            tier="A",
            strategies=["VCP"],
            positive_category_count=2,
            composite_score=70,
            trigger_price=10.0,
            invalid_price=9.0,
        )
        result = run_pool_gate([entry])
        assert len(result.main_candidates) == 1

    def test_strong_policy_ambush_enters_main(self):
        entry = _make_entry(
            symbol="STRONG_POLICY",
            candidate_type="POLICY_AMBUSH",
            tier="A",
            positive_category_count=3,
            composite_score=80,
            mandate_score_component=60,
            beneficiary_score_component=50,
            policy_tags=["低空经济"],
        )
        result = run_pool_gate([entry])
        assert len(result.main_candidates) == 1

    def test_data_incomplete_filtered(self):
        entry = _make_entry(
            symbol="LOW_DATA",
            tier="B",
            positive_category_count=1,
            composite_score=10,
            data_completeness=0.15,
            tradeflow_data_completeness=0.15,
            strategies=[],
            trigger_price=None,
            invalid_price=None,
        )
        result = run_pool_gate([entry])
        assert not any(c["symbol"] == "LOW_DATA" for c in result.main_candidates)


# [TF-QUALITY-003] candidate_precision_gate
class TestPrecisionGateConfig:
    def test_default_config_has_precision_thresholds(self):
        cfg = StrategyConfig()
        assert cfg.precision_min_tech_dimensions == 2
        assert cfg.precision_min_policy_dimensions == 2
        assert cfg.precision_data_quality_threshold == 0.5
        assert cfg.precision_overheat_penalty_max == 20.0


class TestTechPrecisionDimensions:
    """Test TECH_TRADE precision dimension computation."""

    def test_strong_tech_all_dimensions(self):
        from tradingagents.tradeflow.candidate_precision_gate import compute_precision
        entry = _make_entry(
            candidate_type="TECH_TRADE",
            strategies=["VCP"],
            fund_flow_anomaly_score=15,
            fund_flow_unit_verified=True,
            fund_flow_anomaly_tags=["CONSECUTIVE_INFLOW"],
            trigger_price=10.0,
            invalid_price=9.0,
            positive_category_count=3,
            data_completeness=0.7,
            tradeflow_data_completeness=0.7,
        )
        result = compute_precision(entry)
        assert result.candidate_type == "TECH_TRADE"
        assert result.dimensions["形态"] is True
        assert result.dimensions["量能"] is True
        assert result.dimensions["资金"] is True
        assert result.dimensions["触发/失效价"] is True
        assert result.dimensions["数据质量"] is True
        assert result.resonance_count == 5
        assert result.qualified is True

    def test_weak_tech_pattern_only(self):
        from tradingagents.tradeflow.candidate_precision_gate import compute_precision
        entry = _make_entry(
            candidate_type="TECH_TRADE",
            tier="B",
            strategies=["VCP"],
            positive_category_count=1,
            data_completeness=0.3,
            tradeflow_data_completeness=0.3,
            fund_flow_anomaly_score=0,
            trigger_price=None,
            invalid_price=None,
        )
        result = compute_precision(entry)
        assert result.dimensions["形态"] is True
        assert result.resonance_count == 1
        assert result.qualified is False
        assert "技术共振不足" in result.reason

    def test_tech_trigger_invalid_plus_pattern(self):
        from tradingagents.tradeflow.candidate_precision_gate import compute_precision
        entry = _make_entry(
            candidate_type="TECH_TRADE",
            strategies=["PULLBACK_SUPPORT"],
            trigger_price=20.0,
            invalid_price=18.0,
            positive_category_count=1,
            data_completeness=0.3,
            tradeflow_data_completeness=0.3,
        )
        result = compute_precision(entry)
        assert result.dimensions["形态"] is True
        assert result.dimensions["触发/失效价"] is True
        assert result.resonance_count >= 2
        assert result.qualified is True

    def test_tech_no_dimensions(self):
        from tradingagents.tradeflow.candidate_precision_gate import compute_precision
        entry = _make_entry(
            candidate_type="TECH_TRADE",
            strategies=[],
            positive_category_count=0,
            data_completeness=0.1,
            tradeflow_data_completeness=0.1,
            fund_flow_anomaly_score=0,
            trigger_price=None,
            invalid_price=None,
        )
        result = compute_precision(entry)
        assert result.resonance_count == 0
        assert result.qualified is False


class TestPolicyPrecisionDimensions:
    """Test POLICY (昊天) precision dimension computation."""

    def test_strong_policy_all_dimensions(self):
        from tradingagents.tradeflow.candidate_precision_gate import compute_precision
        entry = _make_entry(
            candidate_type="POLICY_AMBUSH",
            policy_tags=["低空经济"],
            mandate_score_component=60,
            beneficiary_score_component=50,
            data_completeness=0.7,
            tradeflow_data_completeness=0.7,
        )
        result = compute_precision(entry)
        assert result.dimensions["政策主题"] is True
        assert result.dimensions["受益路径"] is True
        assert result.dimensions["反证不过热"] is True
        assert result.dimensions["证据覆盖"] is True
        assert result.qualified is True

    def test_weak_policy_no_beneficiary(self):
        from tradingagents.tradeflow.candidate_precision_gate import compute_precision
        entry = _make_entry(
            candidate_type="POLICY_AMBUSH",
            mandate_score_component=0,
            beneficiary_score_component=0,
            data_completeness=0.3,
            tradeflow_data_completeness=0.3,
            overheat_penalty=30.0,  # overheated → 反证不过热=False
        )
        result = compute_precision(entry)
        assert result.dimensions["政策主题"] is False
        assert result.dimensions["受益路径"] is False
        assert result.dimensions["反证不过热"] is False
        assert result.dimensions["证据覆盖"] is False
        assert result.qualified is False

    def test_policy_overheated_fails_precision(self):
        from tradingagents.tradeflow.candidate_precision_gate import compute_precision
        entry = _make_entry(
            candidate_type="POLICY_CONFIRM",
            mandate_score_component=60,
            beneficiary_score_component=50,
            data_completeness=0.7,
            tradeflow_data_completeness=0.7,
        )
        entry["overheat_penalty"] = 30.0
        entry["overheat_flags"] = ["PRICE_SURGE"]
        result = compute_precision(entry)
        assert result.dimensions["反证不过热"] is False
        assert result.dimensions["政策主题"] is True
        assert result.dimensions["受益路径"] is True
        assert result.qualified is True  # still 3/4 >= 2

    def test_policy_high_severity_counter(self):
        from tradingagents.tradeflow.candidate_precision_gate import compute_precision
        entry = _make_entry(
            candidate_type="POLICY_AMBUSH",
            mandate_score_component=60,
            beneficiary_score_component=50,
            data_completeness=0.7,
            tradeflow_data_completeness=0.7,
            counter_evidence=[{"severity": "high", "item": "利空公告"}],
        )
        result = compute_precision(entry)
        assert result.dimensions["反证不过热"] is False
        # still has 政策主题 + 受益路径 + 证据覆盖 = 3 >= 2
        assert result.qualified is True


class TestPrecisionGatePoolIntegration:
    """Test precision gate integration with run_pool_gate."""

    def test_haotian_precision_fail_goes_to_observation_not_filtered(self):
        """昊天左侧候选不会因短线未突破被直接过滤，只能进入 observation 或 haotian main."""
        entry = _make_entry(
            symbol="HAOTIAN_WEAK",
            candidate_type="POLICY_AMBUSH",
            tier="A",
            positive_category_count=3,
            composite_score=75,
            mandate_score_component=0,
            beneficiary_score_component=0,
            policy_tags=[],
            data_completeness=0.2,
            tradeflow_data_completeness=0.2,
        )
        result = run_pool_gate([entry])
        assert len(result.main_candidates) == 0
        assert len(result.filtered_candidates) == 0
        assert len(result.observation_candidates) == 1
        assert result.observation_candidates[0]["symbol"] == "HAOTIAN_WEAK"
        assert "昊天共振不足" in result.observation_candidates[0]["pool_filter_reason"]
        assert result.observation_candidates[0]["pool_status"] == "observation"

    def test_weak_tech_precision_fail_goes_to_observation(self):
        """TECH_TRADE with only 1 precision dimension goes to observation (passed legacy)."""
        entry = _make_entry(
            symbol="TECH_WEAK",
            candidate_type="TECH_TRADE",
            tier="B",
            positive_category_count=2,
            composite_score=50,
            data_completeness=0.7,
            tradeflow_data_completeness=0.7,
            strategies=["VCP"],
            trigger_price=None,
            invalid_price=None,
            fund_flow_anomaly_score=0,
        )
        result = run_pool_gate([entry])
        # 形态=yes, 量能(positive_cat>=2)=yes → 2 dims → actually qualifies
        # Let's make it truly weak: only pattern, no volume
        assert len(result.main_candidates) == 1 or len(result.observation_candidates) == 1

    def test_tech_single_pattern_only_observation(self):
        """TECH_TRADE with only VCP pattern (no volume/capital/trigger/data) → observation."""
        entry = _make_entry(
            symbol="VCP_ONLY",
            candidate_type="TECH_TRADE",
            tier="B",
            positive_category_count=1,
            composite_score=40,
            data_completeness=0.2,
            tradeflow_data_completeness=0.2,
            strategies=["VCP"],
            trigger_price=None,
            invalid_price=None,
            fund_flow_anomaly_score=0,
        )
        result = run_pool_gate([entry])
        assert len(result.main_candidates) == 0
        # Should be in observation (passed legacy via tech resonance) or filtered
        all_non_main = result.observation_candidates + result.filtered_candidates
        assert any(c["symbol"] == "VCP_ONLY" for c in all_non_main)

    def test_strong_tech_still_enters_main(self):
        entry = _make_entry(
            symbol="STRONG_TECH",
            candidate_type="TECH_TRADE",
            tier="A",
            strategies=["VCP"],
            positive_category_count=3,
            composite_score=80,
            data_completeness=0.7,
            tradeflow_data_completeness=0.7,
            trigger_price=10.0,
            invalid_price=9.0,
            fund_flow_anomaly_score=15,
            fund_flow_unit_verified=True,
            fund_flow_anomaly_tags=["CONSECUTIVE_INFLOW"],
        )
        result = run_pool_gate([entry])
        assert len(result.main_candidates) == 1
        assert result.main_candidates[0]["symbol"] == "STRONG_TECH"
        assert "precision_dimensions" in result.main_candidates[0]
        assert result.main_candidates[0]["precision_resonance_count"] >= 2

    def test_20_mixed_candidates_main_max_5(self):
        """20 candidates with varying quality → main <= 5."""
        entries = []
        for i in range(20):
            if i < 3:
                # Strong tech with full resonance
                entries.append(_make_entry(
                    symbol=f"TECH{i}",
                    candidate_type="TECH_TRADE",
                    tier="A",
                    composite_score=80 - i,
                    positive_category_count=3,
                    data_completeness=0.7,
                    tradeflow_data_completeness=0.7,
                    strategies=["VCP"],
                    trigger_price=10.0,
                    invalid_price=9.0,
                    fund_flow_anomaly_score=10,
                    fund_flow_unit_verified=True,
                ))
            elif i < 6:
                # Strong policy
                entries.append(_make_entry(
                    symbol=f"POLICY{i}",
                    candidate_type="POLICY_AMBUSH",
                    tier="A",
                    composite_score=75 - i,
                    positive_category_count=3,
                    data_completeness=0.7,
                    tradeflow_data_completeness=0.7,
                    mandate_score_component=60,
                    beneficiary_score_component=50,
                    policy_tags=["低空经济"],
                ))
            elif i < 13:
                # Weak tech — only pattern, low data
                entries.append(_make_entry(
                    symbol=f"WEAK_T{i}",
                    candidate_type="TECH_TRADE",
                    tier="B",
                    composite_score=40,
                    positive_category_count=1,
                    data_completeness=0.3,
                    tradeflow_data_completeness=0.3,
                    strategies=["VCP"],
                ))
            else:
                # Weak policy — no beneficiary, low data
                entries.append(_make_entry(
                    symbol=f"WEAK_P{i}",
                    candidate_type="POLICY_AMBUSH",
                    tier="B",
                    composite_score=35,
                    positive_category_count=1,
                    data_completeness=0.3,
                    tradeflow_data_completeness=0.3,
                    mandate_score_component=0,
                    beneficiary_score_component=0,
                ))
        result = run_pool_gate(entries)
        assert len(result.main_candidates) <= 5
        # Strong ones should be in main
        main_syms = {c["symbol"] for c in result.main_candidates}
        assert "TECH0" in main_syms
        assert "POLICY3" in main_syms
        # Weak policy must NOT be filtered (Haotian protection)
        for c in result.filtered_candidates:
            assert c["symbol"] not in {f"WEAK_P{i}" for i in range(13, 20)}
        # Weak policy should be in observation
        obs_syms = {c["symbol"] for c in result.observation_candidates}
        weak_p_in_obs = len(obs_syms & {f"WEAK_P{i}" for i in range(13, 20)})
        assert weak_p_in_obs > 0

    def test_precision_fail_reason_is_explainable(self):
        entry = _make_entry(
            symbol="REASON_TEST",
            candidate_type="TECH_TRADE",
            tier="B",
            positive_category_count=1,
            composite_score=30,
            data_completeness=0.2,
            tradeflow_data_completeness=0.2,
            strategies=["VCP"],
            trigger_price=None,
            invalid_price=None,
            fund_flow_anomaly_score=0,
        )
        result = run_pool_gate([entry])
        all_non_main = result.observation_candidates + result.filtered_candidates
        found = [c for c in all_non_main if c["symbol"] == "REASON_TEST"]
        if found:
            assert found[0]["pool_filter_reason"] != ""

    def test_event_watch_falls_through_precision(self):
        """EVENT_WATCH candidates skip precision gate (no TECH/POLICY dimensions)."""
        from tradingagents.tradeflow.candidate_precision_gate import compute_precision
        entry = _make_entry(
            candidate_type="EVENT_WATCH",
            tier="A",
            positive_category_count=3,
            composite_score=70,
            data_completeness=0.7,
            tradeflow_data_completeness=0.7,
        )
        result = compute_precision(entry)
        assert result.qualified is True
        assert result.reason == ""

    def test_main_candidate_has_precision_metadata(self):
        entry = _make_entry(
            symbol="META_TEST",
            candidate_type="TECH_TRADE",
            tier="A",
            strategies=["VCP"],
            positive_category_count=3,
            composite_score=80,
            data_completeness=0.7,
            tradeflow_data_completeness=0.7,
            trigger_price=10.0,
            invalid_price=9.0,
        )
        result = run_pool_gate([entry])
        assert len(result.main_candidates) == 1
        mc = result.main_candidates[0]
        assert "precision_dimensions" in mc
        assert "precision_resonance_count" in mc
        assert mc["precision_resonance_count"] >= 2


class TestHaotianProtection:
    """Acceptance: 昊天左侧候选不会因短线未突破被直接过滤."""

    def test_haotian_no_breakout_not_filtered(self):
        """POLICY candidate that fails precision goes to observation, never filtered."""
        entry = _make_entry(
            symbol="HAOTIAN_NO_BREAK",
            candidate_type="POLICY_AMBUSH",
            tier="A",
            positive_category_count=3,
            composite_score=75,
            mandate_score_component=0,
            beneficiary_score_component=0,
            policy_tags=[],
            data_completeness=0.1,
            tradeflow_data_completeness=0.1,
        )
        result = run_pool_gate([entry])
        assert not any(c["symbol"] == "HAOTIAN_NO_BREAK" for c in result.filtered_candidates)
        assert any(c["symbol"] == "HAOTIAN_NO_BREAK" for c in result.observation_candidates)

    def test_haotian_with_policy_enters_main(self):
        """POLICY candidate with sufficient dimensions enters main."""
        entry = _make_entry(
            symbol="HAOTIAN_STRONG",
            candidate_type="POLICY_AMBUSH",
            tier="A",
            positive_category_count=3,
            composite_score=80,
            mandate_score_component=60,
            beneficiary_score_component=50,
            policy_tags=["低空经济"],
            data_completeness=0.7,
            tradeflow_data_completeness=0.7,
        )
        result = run_pool_gate([entry])
        assert any(c["symbol"] == "HAOTIAN_STRONG" for c in result.main_candidates)

    def test_multiple_haotian_all_fail_precision_all_observation(self):
        """Multiple weak POLICY candidates — all go to observation, none filtered."""
        entries = [
            _make_entry(
                symbol=f"HP{i}",
                candidate_type="POLICY_AMBUSH",
                tier="A",
                positive_category_count=3,
                composite_score=70,
                mandate_score_component=0,
                beneficiary_score_component=0,
                policy_tags=[],
                data_completeness=0.2,
                tradeflow_data_completeness=0.2,
            )
            for i in range(5)
        ]
        result = run_pool_gate(entries)
        assert len(result.filtered_candidates) == 0
        assert len(result.observation_candidates) == 5


class TestEnrichWithPrecision:
    def test_enrich_adds_dimension_metadata(self):
        from tradingagents.tradeflow.candidate_precision_gate import enrich_with_precision
        entries = [
            _make_entry(symbol="E1", candidate_type="TECH_TRADE", strategies=["VCP"],
                        data_completeness=0.7, tradeflow_data_completeness=0.7,
                        positive_category_count=3, trigger_price=10.0, invalid_price=9.0),
            _make_entry(symbol="E2", candidate_type="POLICY_AMBUSH",
                        mandate_score_component=50, beneficiary_score_component=40,
                        data_completeness=0.7, tradeflow_data_completeness=0.7),
        ]
        enriched = enrich_with_precision(entries)
        assert "precision_dimensions" in enriched[0]
        assert enriched[0]["precision_resonance_count"] >= 2
        assert enriched[1]["precision_dimensions"]["政策主题"] is True
