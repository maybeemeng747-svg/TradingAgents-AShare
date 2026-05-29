# [M-004] strategy_config tests
"""Tests for centralized StrategyConfig and its integration across TradeFlow modules.

Covers:
1. Default config values match previous hardcoded constants.
2. Strategies read thresholds from config.
3. Modifying config thresholds changes results predictably.
4. None cfg falls back to DEFAULT_STRATEGY_CONFIG.
5. Daily plan output includes config_version in metadata.
"""

import sys
import os
import pytest
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.tradeflow.strategy_config import (
    StrategyConfig,
    DEFAULT_STRATEGY_CONFIG,
    STRATEGY_CONFIG_VERSION,
)
from tradingagents.tradeflow.strategies.vcp import score_vcp
from tradingagents.tradeflow.strategies.pullback_support import score_pullback_support
from tradingagents.tradeflow.strategies.event_catalyst import score_event_catalyst
from tradingagents.tradeflow.policy_version_signal import detect_policy_version
from tradingagents.tradeflow.narrative_quality import score_narrative_quality
from tradingagents.tradeflow.underwater_risk_flags import detect_underwater_risks
from tradingagents.tradeflow.fund_flow_anomaly import detect_fund_flow_anomaly
from tradingagents.tradeflow.selection_priority_gate import run_selection_priority_gate
from tradingagents.tradeflow.tier_budget import classify_candidate_tier, allocate_tier_budget
from tradingagents.tradeflow.evidence_gate import compute_evidence_completeness
from tradingagents.tradeflow.game_balance import assess_game_balance
from tradingagents.tradeflow.candidate_engine import evaluate_symbol
from tradingagents.tradeflow.plan_runner import generate_daily_plan


def _make_vcp_df():
    np.random.seed(123)
    n = 120
    dates = pd.date_range("2026-01-01", periods=n, freq="B")
    prices = [20.0]
    for i in range(1, 80):
        ret = 0.004 + np.random.normal(0, 0.015)
        prices.append(prices[-1] * (1 + ret))
    base = prices[-1]
    for i in range(40):
        amp = 0.03 * (1 - i / 40)
        price = base + amp * base * np.sin(i * 0.3) + np.random.normal(0, 0.005) * base
        prices.append(max(price, base * 0.95))
    prices = np.array(prices[:n])
    volumes = np.concatenate([
        np.random.uniform(8e6, 15e6, 80),
        np.linspace(10e6, 3e6, 40),
    ])
    return pd.DataFrame({
        "Date": dates,
        "Open": prices * (1 + np.random.uniform(-0.003, 0.003, n)),
        "High": prices * (1 + np.random.uniform(0.005, 0.015, n)),
        "Low": prices * (1 - np.random.uniform(0.005, 0.015, n)),
        "Close": prices,
        "Volume": volumes,
    })


def _make_pullback_df():
    np.random.seed(456)
    n = 120
    dates = pd.date_range("2026-01-01", periods=n, freq="B")
    prices = [20.0]
    for i in range(1, 100):
        ret = 0.003 + np.random.normal(0, 0.012)
        prices.append(prices[-1] * (1 + ret))
    peak = prices[-1]
    for i in range(20):
        ret = -0.004 + np.random.normal(0, 0.008)
        prices.append(prices[-1] * (1 + ret))
    prices = np.array(prices[:n])
    volumes = np.concatenate([
        np.random.uniform(8e6, 15e6, 100),
        np.linspace(12e6, 5e6, 20),
    ])
    return pd.DataFrame({
        "Date": dates,
        "Open": prices * (1 + np.random.uniform(-0.003, 0.003, n)),
        "High": prices * (1 + np.random.uniform(0.005, 0.015, n)),
        "Low": prices * (1 - np.random.uniform(0.005, 0.015, n)),
        "Close": prices,
        "Volume": volumes,
    })


class TestStrategyConfigDefaults:
    def test_default_config_exists(self):
        assert DEFAULT_STRATEGY_CONFIG is not None
        assert isinstance(DEFAULT_STRATEGY_CONFIG, StrategyConfig)

    def test_config_version(self):
        assert DEFAULT_STRATEGY_CONFIG.config_version == STRATEGY_CONFIG_VERSION
        assert DEFAULT_STRATEGY_CONFIG.config_version == "v1.0"

    def test_liquidity_threshold(self):
        assert DEFAULT_STRATEGY_CONFIG.amount_threshold == 30_000_000.0

    def test_vcp_thresholds(self):
        assert DEFAULT_STRATEGY_CONFIG.vcp_min_data_len == 40
        assert DEFAULT_STRATEGY_CONFIG.vcp_range_ratio_max == 0.8
        assert DEFAULT_STRATEGY_CONFIG.vcp_vol_ratio_max == 0.9
        assert DEFAULT_STRATEGY_CONFIG.vcp_price_position_min == 0.5
        assert DEFAULT_STRATEGY_CONFIG.vcp_ma20_break_pct == 0.97
        assert DEFAULT_STRATEGY_CONFIG.vcp_score_max == 80.0
        assert DEFAULT_STRATEGY_CONFIG.vcp_need_deep_ta_score == 55.0
        assert DEFAULT_STRATEGY_CONFIG.vcp_need_deep_ta_position == 0.8

    def test_pullback_thresholds(self):
        assert DEFAULT_STRATEGY_CONFIG.pullback_min_data_len == 60
        assert DEFAULT_STRATEGY_CONFIG.pullback_min_pct == -0.03
        assert DEFAULT_STRATEGY_CONFIG.pullback_max_pct == -0.20
        assert DEFAULT_STRATEGY_CONFIG.pullback_vol_ratio_max == 1.0
        assert DEFAULT_STRATEGY_CONFIG.pullback_score_max == 80.0
        assert DEFAULT_STRATEGY_CONFIG.pullback_need_deep_ta_score == 60.0

    def test_event_thresholds(self):
        assert DEFAULT_STRATEGY_CONFIG.event_score_max == 80.0
        assert DEFAULT_STRATEGY_CONFIG.event_need_deep_ta_score == 60.0

    def test_policy_thresholds(self):
        assert DEFAULT_STRATEGY_CONFIG.policy_max_bonus == 30.0
        assert DEFAULT_STRATEGY_CONFIG.policy_industry_weight_factor == 0.3
        assert DEFAULT_STRATEGY_CONFIG.policy_need_deep_ta_score == 15.0

    def test_narrative_thresholds(self):
        assert DEFAULT_STRATEGY_CONFIG.narrative_max_score == 40.0
        assert DEFAULT_STRATEGY_CONFIG.narrative_need_deep_ta_score == 20.0

    def test_risk_thresholds(self):
        assert DEFAULT_STRATEGY_CONFIG.risk_max_penalty == 30.0
        assert DEFAULT_STRATEGY_CONFIG.risk_lockup_score_factor == 0.8
        assert DEFAULT_STRATEGY_CONFIG.risk_many_flags_threshold == 3
        assert DEFAULT_STRATEGY_CONFIG.risk_heavy_penalty_threshold == -15.0
        assert "INQUIRY_RISK" in DEFAULT_STRATEGY_CONFIG.risk_high_severity_flags
        assert "FINANCIAL_QUALITY_RISK" in DEFAULT_STRATEGY_CONFIG.risk_high_severity_flags

    def test_fund_flow_thresholds(self):
        assert DEFAULT_STRATEGY_CONFIG.fund_flow_max_bonus == 25.0
        assert DEFAULT_STRATEGY_CONFIG.fund_flow_anomaly_threshold_wan == 50000.0
        assert DEFAULT_STRATEGY_CONFIG.fund_flow_consecutive_min_days == 3
        assert DEFAULT_STRATEGY_CONFIG.fund_flow_high_proportion_threshold == 5.0
        assert DEFAULT_STRATEGY_CONFIG.fund_flow_need_deep_ta_score == 10.0

    def test_gate_thresholds(self):
        assert DEFAULT_STRATEGY_CONFIG.gate_min_positive_categories == 2
        assert DEFAULT_STRATEGY_CONFIG.gate_max_composite_score == 200.0
        assert DEFAULT_STRATEGY_CONFIG.gate_completeness_bonus_factor == 5.0
        assert DEFAULT_STRATEGY_CONFIG.gate_completeness_min == 0.5

    def test_tier_thresholds(self):
        assert DEFAULT_STRATEGY_CONFIG.tier_a_hard_cap == 5
        assert DEFAULT_STRATEGY_CONFIG.tier_a_budget == 100
        assert DEFAULT_STRATEGY_CONFIG.tier_b_budget == 30
        assert DEFAULT_STRATEGY_CONFIG.tier_c_budget == 0
        assert DEFAULT_STRATEGY_CONFIG.tier_a_min_completeness == 0.6

    def test_evidence_thresholds(self):
        assert DEFAULT_STRATEGY_CONFIG.evidence_total_dimensions == 10
        assert DEFAULT_STRATEGY_CONFIG.evidence_completeness_for_a_tier == 0.6
        assert DEFAULT_STRATEGY_CONFIG.evidence_completeness_for_deep_ta == 0.5

    def test_to_dict(self):
        d = DEFAULT_STRATEGY_CONFIG.to_dict()
        assert isinstance(d, dict)
        assert d["config_version"] == "v1.0"
        assert isinstance(d["risk_high_severity_flags"], list)


class TestConfigNoneFallback:
    """All functions should work with cfg=None, using DEFAULT_STRATEGY_CONFIG."""

    def test_vcp_none_cfg(self):
        df = _make_vcp_df()
        result = score_vcp(df, "TEST.SZ", cfg=None)
        assert result is not None
        assert result.strategy_tag == "VCP"

    def test_pullback_none_cfg(self):
        df = _make_pullback_df()
        result = score_pullback_support(df, "TEST.SZ", cfg=None)
        assert result is not None
        assert result.strategy_tag == "PULLBACK_SUPPORT"

    def test_event_none_cfg(self):
        result = score_event_catalyst("TEST.SZ", news_texts=["并购重组"], latest_close=25.0, cfg=None)
        assert result is not None

    def test_policy_none_cfg(self):
        result = detect_policy_version(event_texts=["新质生产力发展"], cfg=None)
        assert len(result.policy_tags) > 0

    def test_narrative_none_cfg(self):
        result = score_narrative_quality(event_texts=["回购计划，重大订单"], cfg=None)
        assert result.narrative_score > 0

    def test_risk_none_cfg(self):
        result = detect_underwater_risks(event_texts=["解禁限售股上市"], cfg=None)
        assert "LOCKUP_RISK" in result.risk_flags

    def test_fund_flow_none_cfg(self):
        result = detect_fund_flow_anomaly(
            fund_flow_individual="主力净流入-净额\n100000\n200000\n300000\n400000\n500000",
            cfg=None,
        )
        assert result.fund_flow_unit_verified is True

    def test_gate_none_cfg(self):
        result = run_selection_priority_gate(
            score=50, strategy_tags=["VCP", "POLICY_VERSION"],
            cfg=None,
        )
        assert result.composite_score > 0

    def test_tier_none_cfg(self):
        result = classify_candidate_tier(
            priority_rank="A", composite_score=70,
            positive_category_count=3, data_completeness=0.8,
            game_balance="favorable", gate_passed=True, cfg=None,
        )
        assert result.tier == "A"

    def test_evidence_none_cfg(self):
        result = compute_evidence_completeness(
            has_ohlcv=True, has_liquidity=True, cfg=None,
        )
        assert result.tradeflow_data_completeness >= 0.2


class TestConfigOverridePredictable:
    """Changing config thresholds should produce predictable changes in results."""

    def test_vcp_score_cap_override(self):
        df = _make_vcp_df()
        result_default = score_vcp(df, "TEST.SZ")
        assert result_default is not None

        low_cap_cfg = StrategyConfig(vcp_score_max=30.0)
        result_low = score_vcp(df, "TEST.SZ", cfg=low_cap_cfg)
        assert result_low is not None
        assert result_low.score <= 30.0
        assert result_low.score <= result_default.score

    def test_vcp_need_deep_ta_threshold(self):
        df = _make_vcp_df()
        result_default = score_vcp(df, "TEST.SZ")
        assert result_default is not None
        default_deep_ta = result_default.need_deep_ta

        high_threshold_cfg = StrategyConfig(vcp_need_deep_ta_score=100.0, vcp_need_deep_ta_position=1.0)
        result_high = score_vcp(df, "TEST.SZ", cfg=high_threshold_cfg)
        assert result_high is not None
        if result_high.score < 100.0 and result_high.evidence.get("price_position", 0) < 1.0:
            assert result_high.need_deep_ta is False

    def test_event_score_cap_override(self):
        result_default = score_event_catalyst("TEST.SZ", news_texts=["并购重组"], latest_close=25.0)
        assert result_default is not None
        assert result_default.score == 65

        low_cap_cfg = StrategyConfig(event_score_max=50.0)
        result_low = score_event_catalyst("TEST.SZ", news_texts=["并购重组"], latest_close=25.0, cfg=low_cap_cfg)
        assert result_low is not None
        assert result_low.score == 50.0

    def test_event_need_deep_ta_threshold(self):
        result_default = score_event_catalyst("TEST.SZ", news_texts=["并购重组"], latest_close=25.0)
        assert result_default is not None
        assert result_default.need_deep_ta is True

        high_cfg = StrategyConfig(event_need_deep_ta_score=100.0)
        result_high = score_event_catalyst("TEST.SZ", news_texts=["并购重组"], latest_close=25.0, cfg=high_cfg)
        assert result_high is not None
        assert result_high.need_deep_ta is False

    def test_policy_max_bonus_override(self):
        result_default = detect_policy_version(event_texts=["新质生产力发展 算力基础设施"])
        assert result_default.version_score > 0

        low_bonus_cfg = StrategyConfig(policy_max_bonus=5.0)
        result_low = detect_policy_version(event_texts=["新质生产力发展 算力基础设施"], cfg=low_bonus_cfg)
        assert result_low.version_score <= 5.0
        assert result_low.version_score <= result_default.version_score

    def test_narrative_max_score_override(self):
        texts = ["国务院发布新政策支持产业发展，公司中标重大合同"]
        result_default = score_narrative_quality(event_texts=texts)
        assert result_default.narrative_score > 0

        low_cfg = StrategyConfig(narrative_max_score=5.0)
        result_low = score_narrative_quality(event_texts=texts, cfg=low_cfg)
        assert result_low.narrative_score <= 5.0

    def test_risk_max_penalty_override(self):
        texts = ["解禁限售股上市 问询函 财务造假"]
        result_default = detect_underwater_risks(event_texts=texts)
        assert result_default.risk_penalty < 0

        low_penalty_cfg = StrategyConfig(risk_max_penalty=5.0)
        result_low = detect_underwater_risks(event_texts=texts, cfg=low_penalty_cfg)
        assert result_low.risk_penalty >= -5.0
        assert result_low.risk_penalty >= result_default.risk_penalty

    def test_gate_min_categories_override(self):
        result_default = run_selection_priority_gate(
            score=50, strategy_tags=["VCP", "POLICY_VERSION"],
            version_score=10.0,
        )
        assert result_default.positive_category_count >= 2

        high_min_cfg = StrategyConfig(gate_min_positive_categories=5)
        result_high = run_selection_priority_gate(
            score=50, strategy_tags=["VCP", "POLICY_VERSION"],
            version_score=10.0, cfg=high_min_cfg,
        )
        if result_high.positive_category_count < 5:
            assert result_high.gate_passed is False

    def test_tier_a_min_completeness_override(self):
        result_default = classify_candidate_tier(
            priority_rank="A", composite_score=70,
            positive_category_count=3, data_completeness=0.65,
            game_balance="favorable", gate_passed=True,
        )
        assert result_default.tier == "A"

        high_comp_cfg = StrategyConfig(tier_a_min_completeness=0.9)
        result_high = classify_candidate_tier(
            priority_rank="A", composite_score=70,
            positive_category_count=3, data_completeness=0.65,
            game_balance="favorable", gate_passed=True, cfg=high_comp_cfg,
        )
        assert result_high.tier != "A"

    def test_evidence_completeness_threshold_override(self):
        result_default = compute_evidence_completeness(has_ohlcv=True, has_liquidity=True)
        assert result_default.tradeflow_data_completeness == 0.2
        assert result_default.can_trigger_deep_ta is False

        low_threshold_cfg = StrategyConfig(evidence_completeness_for_deep_ta=0.1)
        result_low = compute_evidence_completeness(has_ohlcv=True, has_liquidity=True, cfg=low_threshold_cfg)
        assert result_low.can_trigger_deep_ta is True

    def test_liquidity_threshold_override(self):
        df = _make_vcp_df()
        candidate_default, _ = evaluate_symbol("TEST.SZ", df=df)
        assert candidate_default is not None

        high_liquidity_cfg = StrategyConfig(amount_threshold=100_000_000_000.0)
        candidate_filtered, reason = evaluate_symbol("TEST.SZ", df=df, cfg=high_liquidity_cfg)
        assert candidate_filtered is None
        assert "流动性" in reason

    def test_lockup_score_factor_override(self):
        from tradingagents.tradeflow.schemas import SIGNAL_TAG_LOCKUP_RISK
        candidate_default, _ = evaluate_symbol(
            "TEST.SZ",
            news_texts=["回购计划"],
            event_overrides=[{"event_type": "buyback", "direction": "bullish", "title": "回购计划"}],
            df=_make_vcp_df(),
        )
        assert candidate_default is not None

        texts_with_lockup = ["回购计划", "限售股解禁即将上市"]
        candidate_lockup, _ = evaluate_symbol(
            "TEST.SZ",
            news_texts=texts_with_lockup,
            df=_make_vcp_df(),
        )
        if candidate_lockup is not None and SIGNAL_TAG_LOCKUP_RISK in candidate_lockup.risk_flags:
            score_with_default_factor = candidate_lockup.score

            heavy_cfg = StrategyConfig(risk_lockup_score_factor=0.5)
            candidate_heavy, _ = evaluate_symbol(
                "TEST.SZ",
                news_texts=texts_with_lockup,
                df=_make_vcp_df(),
                cfg=heavy_cfg,
            )
            if candidate_heavy is not None and SIGNAL_TAG_LOCKUP_RISK in candidate_heavy.risk_flags:
                assert candidate_heavy.score <= score_with_default_factor


class TestDailyPlanConfigVersion:
    def test_plan_includes_config_version(self):
        candidates = []
        plan = generate_daily_plan(
            trade_date="2026-05-30",
            candidates=candidates,
        )
        assert "strategy_config_version" in plan.metadata
        assert plan.metadata["strategy_config_version"] == STRATEGY_CONFIG_VERSION

    def test_plan_custom_config_version(self):
        custom_cfg = StrategyConfig(config_version="v2.0-test")
        plan = generate_daily_plan(
            trade_date="2026-05-30",
            candidates=[],
            cfg=custom_cfg,
        )
        assert plan.metadata["strategy_config_version"] == "v2.0-test"

    def test_plan_with_candidates_uses_config(self):
        from tradingagents.tradeflow.schemas import Candidate, CandidateSignal
        c = Candidate(
            symbol="TEST.SZ",
            signals=[CandidateSignal(strategy_tag="VCP", score=50.0)],
        )
        c.merge_signals()
        plan = generate_daily_plan(
            trade_date="2026-05-30",
            candidates=[c],
        )
        assert plan.metadata["strategy_config_version"] == STRATEGY_CONFIG_VERSION
        assert len(plan.candidates) == 1


class TestConfigImmutability:
    def test_default_config_is_singleton(self):
        c1 = DEFAULT_STRATEGY_CONFIG
        c2 = DEFAULT_STRATEGY_CONFIG
        assert c1 is c2

    def test_custom_config_independent(self):
        custom = StrategyConfig(vcp_score_max=50.0)
        assert custom.vcp_score_max == 50.0
        assert DEFAULT_STRATEGY_CONFIG.vcp_score_max == 80.0

    def test_custom_config_default_version(self):
        custom = StrategyConfig()
        assert custom.config_version == STRATEGY_CONFIG_VERSION
