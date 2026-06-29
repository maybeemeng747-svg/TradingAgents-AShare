# [TF-QUALITY-005] candidate_pool_strength_tiers
"""Tests for TradeFlow candidate pool strength tier classification.

Verifies the three-tier split (primary / secondary / observation-overflow)
introduced by TF-QUALITY-005 to address the "candidate pool too large and
looks like catching falling knives" complaint.

Coverage:
1. StrategyConfig exposes the new tier_* fields with conservative defaults.
2. ``_classify_strength_tier`` classifies TECH / POLICY / EVENT candidates:
   - TECH primary requires 量能 + 资金 + 触发价 + composite + resonance.
   - TECH secondary reasons: 弱缩量无资金确认 / 反弹但趋势未修复 /
     数据不足 / 共振维度偏少 / 综合分偏低.
   - POLICY primary requires strong topic + multi-dim support + resonance.
   - POLICY secondary reasons: 弱主题 / 证据单一 / 共振维度偏少 /
     综合分偏低.
3. ``run_pool_gate`` integration:
   - main_candidates carries strength_tier / tier_reason metadata.
   - primary_candidates + secondary_candidates correctly split.
   - Secondary overflow demoted to observation with readable reason.
   - Existing scoring fields (composite_score, precision_dimensions,
     ranking_reasons) preserved.
4. 20-stock fixture: primary clearly fewer than total main, every
   downgraded candidate has a readable reason, no false-kill on strong names.
5. Backward compat: pool_counts / calibration_summary / concentration_summary
   still populated.
6. Edge cases: empty input, all primary, all secondary, mixed types.
"""

import pytest

from tradingagents.tradeflow.candidate_pool_gate import (
    PoolGateResult,
    TIER_PRIMARY,
    TIER_SECONDARY,
    _classify_strength_tier,
    _apply_strength_tiers,
    _haotian_support_dim_count,
    run_pool_gate,
)
from tradingagents.tradeflow.mandate_concentration_gate import STRENGTH_WEAK
from tradingagents.tradeflow.strategy_config import StrategyConfig


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
    technical_score=0.0,
    policy_score=0.0,
    fund_flow_score=0.0,
    event_score=0.0,
    data_quality_score=0.0,
    narrative_score=0.0,
    version_score=0.0,
    beneficiary_path=None,
    ranking_reasons=None,
    precision_dimensions=None,
    precision_resonance_count=None,
    concentration_strength="",
    concentration_topic="",
):
    """Build a candidate entry dict mirroring discovery.py output shape.

    Tests can pre-populate ``precision_dimensions`` /
    ``precision_resonance_count`` to exercise the tier classifier directly
    without running the full precision gate.
    """
    entry = {
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
        "technical_score": technical_score,
        "policy_score": policy_score,
        "fund_flow_score": fund_flow_score,
        "event_score": event_score,
        "data_quality_score": data_quality_score,
        "narrative_score": narrative_score,
        "version_score": version_score,
        "beneficiary_path": beneficiary_path or [],
        "ranking_reasons": ranking_reasons or [],
        "concentration_strength": concentration_strength,
        "concentration_topic": concentration_topic,
    }
    if precision_dimensions is not None:
        entry["precision_dimensions"] = precision_dimensions
    if precision_resonance_count is not None:
        entry["precision_resonance_count"] = precision_resonance_count
    return entry


def _tech_dims(pattern=True, volume=True, capital=True, trigger=True, data_quality=True):
    return {
        "形态": pattern,
        "量能": volume,
        "资金": capital,
        "触发/失效价": trigger,
        "数据质量": data_quality,
    }


def _policy_dims(theme=True, benefit=True, no_overheat=True, evidence=True):
    return {
        "政策主题": theme,
        "受益路径": benefit,
        "反证不过热": no_overheat,
        "证据覆盖": evidence,
    }


class TestTierConfig:
    """[TF-QUALITY-005] StrategyConfig exposes tier_* fields."""

    def test_default_config_has_tier_fields(self):
        cfg = StrategyConfig()
        assert cfg.tier_primary_min_composite == 65.0
        assert cfg.tier_primary_min_resonance == 3
        assert cfg.tier_primary_require_volume_and_capital is True
        assert cfg.tier_primary_haotian_min_support_dims == 2
        assert cfg.tier_secondary_data_completeness_min == 0.5
        assert cfg.tier_secondary_max_count == 3

    def test_custom_tier_config(self):
        cfg = StrategyConfig(
            tier_primary_min_composite=80.0,
            tier_primary_min_resonance=4,
            tier_secondary_max_count=2,
        )
        assert cfg.tier_primary_min_composite == 80.0
        assert cfg.tier_primary_min_resonance == 4
        assert cfg.tier_secondary_max_count == 2


class TestClassifyTechTier:
    """[TF-QUALITY-005] _classify_strength_tier for TECH_TRADE."""

    def test_primary_when_all_dimensions_hit(self):
        entry = _make_entry(
            symbol="STRONG",
            candidate_type="TECH_TRADE",
            composite_score=80.0,
            data_completeness=0.8,
            tradeflow_data_completeness=0.8,
            precision_dimensions=_tech_dims(),
            precision_resonance_count=5,
        )
        tier, reason = _classify_strength_tier(entry, StrategyConfig())
        assert tier == TIER_PRIMARY
        assert reason == ""

    def test_secondary_when_no_capital(self):
        """弱缩量无资金确认 — 量能=True but 资金=False."""
        entry = _make_entry(
            symbol="WEAK_CAP",
            candidate_type="TECH_TRADE",
            composite_score=80.0,
            data_completeness=0.8,
            tradeflow_data_completeness=0.8,
            precision_dimensions=_tech_dims(capital=False),
            precision_resonance_count=4,
        )
        tier, reason = _classify_strength_tier(entry, StrategyConfig())
        assert tier == TIER_SECONDARY
        assert "弱缩量" in reason
        assert "资金" in reason

    def test_secondary_when_no_volume(self):
        """弱缩量无资金确认 — 量能=False."""
        entry = _make_entry(
            symbol="WEAK_VOL",
            candidate_type="TECH_TRADE",
            composite_score=80.0,
            data_completeness=0.8,
            tradeflow_data_completeness=0.8,
            precision_dimensions=_tech_dims(volume=False),
            precision_resonance_count=4,
        )
        tier, reason = _classify_strength_tier(entry, StrategyConfig())
        assert tier == TIER_SECONDARY
        assert "弱缩量" in reason
        assert "量能" in reason

    def test_secondary_data_insufficient_takes_precedence(self):
        """数据不足 should fire before other TECH rules."""
        entry = _make_entry(
            symbol="LOW_DATA",
            candidate_type="TECH_TRADE",
            composite_score=80.0,
            data_completeness=0.2,
            tradeflow_data_completeness=0.2,
            precision_dimensions=_tech_dims(volume=False, capital=False),
            precision_resonance_count=2,
        )
        tier, reason = _classify_strength_tier(entry, StrategyConfig())
        assert tier == TIER_SECONDARY
        assert "数据不足" in reason

    def test_secondary_rebound_trend_unrepaired(self):
        """反弹但趋势未修复 — only 形态 + 触发价, no volume/capital, low composite.

        Note: when 量能/资金 both False, rule 2 (弱缩量无资金确认) fires first.
        To exercise rule 3 explicitly we relax the require-volume-and-capital
        flag so rule 2 does not short-circuit.
        """
        cfg = StrategyConfig(tier_primary_require_volume_and_capital=False)
        entry = _make_entry(
            symbol="REBOUND",
            candidate_type="TECH_TRADE",
            composite_score=55.0,
            data_completeness=0.7,
            tradeflow_data_completeness=0.7,
            precision_dimensions=_tech_dims(
                volume=False, capital=False, trigger=True, data_quality=True
            ),
            precision_resonance_count=3,
        )
        tier, reason = _classify_strength_tier(entry, cfg)
        assert tier == TIER_SECONDARY
        assert "反弹" in reason
        assert "趋势未修复" in reason

    def test_secondary_low_resonance(self):
        """共振维度偏少 — resonance below primary minimum."""
        cfg = StrategyConfig(tier_primary_require_volume_and_capital=False)
        entry = _make_entry(
            symbol="LOW_RES",
            candidate_type="TECH_TRADE",
            composite_score=80.0,
            data_completeness=0.8,
            tradeflow_data_completeness=0.8,
            precision_dimensions=_tech_dims(),
            precision_resonance_count=2,
        )
        tier, reason = _classify_strength_tier(entry, cfg)
        assert tier == TIER_SECONDARY
        assert "共振维度" in reason

    def test_secondary_low_composite(self):
        """综合分偏低 — passes other checks but composite below threshold."""
        cfg = StrategyConfig(tier_primary_require_volume_and_capital=False)
        entry = _make_entry(
            symbol="LOW_SCORE",
            candidate_type="TECH_TRADE",
            composite_score=50.0,
            data_completeness=0.8,
            tradeflow_data_completeness=0.8,
            precision_dimensions=_tech_dims(),
            precision_resonance_count=4,
        )
        tier, reason = _classify_strength_tier(entry, cfg)
        assert tier == TIER_SECONDARY
        assert "综合分" in reason


class TestClassifyHaotianTier:
    """[TF-QUALITY-005] _classify_strength_tier for POLICY (昊天)."""

    def test_primary_when_mandate_beneficiary_strong_topic(self):
        entry = _make_entry(
            symbol="STRONG_HAOTIAN",
            candidate_type="POLICY_AMBUSH",
            composite_score=80.0,
            mandate_score_component=60,
            beneficiary_score_component=50,
            policy_tags=["低空经济"],
            version_score=20,
            data_completeness=0.7,
            tradeflow_data_completeness=0.7,
            concentration_strength="strong",
            concentration_topic="低空经济",
            precision_dimensions=_policy_dims(),
            precision_resonance_count=4,
        )
        tier, reason = _classify_strength_tier(entry, StrategyConfig())
        assert tier == TIER_PRIMARY
        assert reason == ""

    def test_secondary_weak_topic(self):
        """弱主题 — H-014 concentration_strength == 'weak'."""
        entry = _make_entry(
            symbol="WEAK_TOPIC",
            candidate_type="POLICY_AMBUSH",
            composite_score=80.0,
            mandate_score_component=60,
            beneficiary_score_component=50,
            concentration_strength=STRENGTH_WEAK,
            concentration_topic="退潮主题",
            data_completeness=0.7,
            tradeflow_data_completeness=0.7,
            precision_dimensions=_policy_dims(),
            precision_resonance_count=4,
        )
        tier, reason = _classify_strength_tier(entry, StrategyConfig())
        assert tier == TIER_SECONDARY
        assert "弱主题" in reason

    def test_secondary_single_evidence(self):
        """证据单一 — only 1 support dim."""
        entry = _make_entry(
            symbol="SINGLE_EV",
            candidate_type="POLICY_AMBUSH",
            composite_score=80.0,
            mandate_score_component=60,
            beneficiary_score_component=0,  # only mandate → 1 support dim
            concentration_strength="strong",
            concentration_topic="AI",
            data_completeness=0.7,
            tradeflow_data_completeness=0.7,
            precision_dimensions=_policy_dims(),
            precision_resonance_count=4,
        )
        tier, reason = _classify_strength_tier(entry, StrategyConfig())
        assert tier == TIER_SECONDARY
        assert "证据单一" in reason

    def test_secondary_low_resonance(self):
        entry = _make_entry(
            symbol="LOW_RES_H",
            candidate_type="POLICY_AMBUSH",
            composite_score=80.0,
            mandate_score_component=60,
            beneficiary_score_component=50,
            concentration_strength="strong",
            concentration_topic="AI",
            data_completeness=0.7,
            tradeflow_data_completeness=0.7,
            precision_dimensions=_policy_dims(),
            precision_resonance_count=2,
        )
        tier, reason = _classify_strength_tier(entry, StrategyConfig())
        assert tier == TIER_SECONDARY
        assert "共振维度" in reason

    def test_secondary_low_composite(self):
        entry = _make_entry(
            symbol="LOW_SCORE_H",
            candidate_type="POLICY_AMBUSH",
            composite_score=50.0,
            mandate_score_component=60,
            beneficiary_score_component=50,
            concentration_strength="strong",
            concentration_topic="AI",
            data_completeness=0.7,
            tradeflow_data_completeness=0.7,
            precision_dimensions=_policy_dims(),
            precision_resonance_count=4,
        )
        tier, reason = _classify_strength_tier(entry, StrategyConfig())
        assert tier == TIER_SECONDARY
        assert "综合分" in reason


class TestClassifyEventTier:
    """[TF-QUALITY-005] Other candidate types (EVENT_WATCH etc.) — primary by composite."""

    def test_event_primary_high_composite(self):
        entry = _make_entry(
            symbol="EVENT_OK",
            candidate_type="EVENT_WATCH",
            composite_score=80.0,
            data_completeness=0.7,
            tradeflow_data_completeness=0.7,
        )
        tier, reason = _classify_strength_tier(entry, StrategyConfig())
        assert tier == TIER_PRIMARY
        assert reason == ""

    def test_event_secondary_low_composite(self):
        entry = _make_entry(
            symbol="EVENT_WEAK",
            candidate_type="EVENT_WATCH",
            composite_score=40.0,
            data_completeness=0.7,
            tradeflow_data_completeness=0.7,
        )
        tier, reason = _classify_strength_tier(entry, StrategyConfig())
        assert tier == TIER_SECONDARY
        assert "综合分" in reason

    def test_event_secondary_data_insufficient(self):
        entry = _make_entry(
            symbol="EVENT_LOW_DATA",
            candidate_type="EVENT_WATCH",
            composite_score=80.0,
            data_completeness=0.2,
            tradeflow_data_completeness=0.2,
        )
        tier, reason = _classify_strength_tier(entry, StrategyConfig())
        assert tier == TIER_SECONDARY
        assert "数据不足" in reason


class TestHaotianSupportDimCount:
    """[TF-QUALITY-005] _haotian_support_dim_count mirrors calibration logic."""

    def test_zero_when_no_support(self):
        entry = _make_entry(symbol="NONE")
        assert _haotian_support_dim_count(entry) == 0

    def test_counts_all_five_dims(self):
        entry = _make_entry(
            symbol="ALL",
            event_score=10,
            fund_flow_score=10,
            narrative_score=10,
            beneficiary_score_component=10,
            mandate_score_component=10,
        )
        assert _haotian_support_dim_count(entry) == 5

    def test_beneficiary_path_counts_as_dim(self):
        entry = _make_entry(
            symbol="PATH",
            beneficiary_path=["step1", "step2"],
        )
        assert _haotian_support_dim_count(entry) == 1


class TestApplyStrengthTiers:
    """[TF-QUALITY-005] _apply_strength_tiers splits main correctly."""

    def test_split_mixed_entries(self):
        cfg = StrategyConfig()
        entries = [
            _make_entry(
                symbol="P1",
                candidate_type="TECH_TRADE",
                composite_score=85,
                precision_dimensions=_tech_dims(),
                precision_resonance_count=5,
            ),
            _make_entry(
                symbol="S1",
                candidate_type="TECH_TRADE",
                composite_score=80,
                precision_dimensions=_tech_dims(capital=False),
                precision_resonance_count=4,
            ),
        ]
        primary, secondary, summary = _apply_strength_tiers(entries, cfg)
        primary_syms = {e["symbol"] for e in primary}
        secondary_syms = {e["symbol"] for e in secondary}
        assert primary_syms == {"P1"}
        assert secondary_syms == {"S1"}
        assert summary["primary_count"] == 1
        assert summary["secondary_count"] == 1

    def test_entries_get_strength_tier_metadata(self):
        cfg = StrategyConfig()
        entries = [
            _make_entry(
                symbol="X",
                candidate_type="TECH_TRADE",
                composite_score=85,
                precision_dimensions=_tech_dims(),
                precision_resonance_count=5,
            ),
        ]
        primary, secondary, _ = _apply_strength_tiers(entries, cfg)
        assert primary[0]["strength_tier"] == TIER_PRIMARY
        assert primary[0]["tier_reason"] == ""

    def test_downgrade_reasons_tracked(self):
        cfg = StrategyConfig()
        entries = [
            _make_entry(
                symbol="WEAK_VOL",
                candidate_type="TECH_TRADE",
                composite_score=80,
                precision_dimensions=_tech_dims(volume=False),
                precision_resonance_count=4,
            ),
            _make_entry(
                symbol="WEAK_CAP",
                candidate_type="TECH_TRADE",
                composite_score=80,
                precision_dimensions=_tech_dims(capital=False),
                precision_resonance_count=4,
            ),
        ]
        _, _, summary = _apply_strength_tiers(entries, cfg)
        # Both fall under "weak_volume_capital" bucket.
        bucket = summary["downgrade_reasons"]["weak_volume_capital"]
        assert "WEAK_VOL" in bucket
        assert "WEAK_CAP" in bucket

    def test_headline_in_summary(self):
        cfg = StrategyConfig()
        _, _, summary = _apply_strength_tiers([], cfg)
        assert "headline" in summary
        assert "主候选分层" in summary["headline"]


class TestRunPoolGateTierIntegration:
    """[TF-QUALITY-005] run_pool_gate stamps tier metadata and splits results."""

    def test_result_has_tier_fields(self):
        result = run_pool_gate([])
        assert hasattr(result, "primary_candidates")
        assert hasattr(result, "secondary_candidates")
        assert hasattr(result, "strength_tier_summary")

    def test_main_candidates_get_strength_tier(self):
        entries = [
            _make_entry(
                symbol="STRONG",
                candidate_type="TECH_TRADE",
                tier="A",
                positive_category_count=3,
                composite_score=85,
                data_completeness=0.8,
                tradeflow_data_completeness=0.8,
                strategies=["VCP"],
                trigger_price=10.0,
                invalid_price=9.0,
                fund_flow_anomaly_score=15,
                fund_flow_unit_verified=True,
                fund_flow_anomaly_tags=["CONSECUTIVE_INFLOW"],
                ranking_reasons=["VCP形态", "资金共振"],
            ),
        ]
        result = run_pool_gate(entries)
        assert len(result.main_candidates) == 1
        assert result.main_candidates[0]["strength_tier"] == TIER_PRIMARY
        assert result.main_candidates[0]["tier_reason"] == ""
        assert len(result.primary_candidates) == 1
        assert len(result.secondary_candidates) == 0

    def test_weak_capital_goes_to_secondary(self):
        entries = [
            _make_entry(
                symbol="NO_CAP",
                candidate_type="TECH_TRADE",
                tier="A",
                positive_category_count=3,
                composite_score=80,
                data_completeness=0.8,
                tradeflow_data_completeness=0.8,
                strategies=["VCP"],
                trigger_price=10.0,
                invalid_price=9.0,
                fund_flow_anomaly_score=0,  # no capital
                fund_flow_unit_verified=False,
            ),
        ]
        result = run_pool_gate(entries)
        assert len(result.secondary_candidates) == 1
        assert result.secondary_candidates[0]["symbol"] == "NO_CAP"
        assert "弱缩量" in result.secondary_candidates[0]["tier_reason"]
        # Still in main (secondary is a subset of main).
        assert any(c["symbol"] == "NO_CAP" for c in result.main_candidates)

    def test_secondary_overflow_demoted_to_observation(self):
        """When secondary count exceeds tier_secondary_max_count, overflow → observation."""
        cfg = StrategyConfig(
            pool_main_max=10,
            pool_tech_max=10,
            tier_secondary_max_count=2,
        )
        entries = [
            _make_entry(
                symbol=f"SEC{i}",
                candidate_type="TECH_TRADE",
                tier="A",
                positive_category_count=3,
                composite_score=80,
                data_completeness=0.8,
                tradeflow_data_completeness=0.8,
                strategies=["VCP"],
                trigger_price=10.0,
                invalid_price=9.0,
                # No fund flow → 资金=False → secondary
            )
            for i in range(5)
        ]
        result = run_pool_gate(entries, cfg)
        assert len(result.secondary_candidates) == 2  # capped
        assert len(result.primary_candidates) == 0
        # 3 demoted to observation
        assert len(result.strength_tier_summary["secondary_overflow_to_observation"]) == 3
        obs_syms = {c["symbol"] for c in result.observation_candidates}
        assert obs_syms == {"SEC2", "SEC3", "SEC4"}
        # Each overflow obs has readable reason
        for obs in result.observation_candidates:
            assert "[TF-QUALITY-005]次优候选已满" in obs["pool_filter_reason"]

    def test_strength_tier_summary_populated(self):
        entries = [
            _make_entry(
                symbol="X",
                candidate_type="TECH_TRADE",
                tier="A",
                positive_category_count=3,
                composite_score=85,
                data_completeness=0.8,
                tradeflow_data_completeness=0.8,
                strategies=["VCP"],
                trigger_price=10.0,
                invalid_price=9.0,
                fund_flow_anomaly_score=15,
                fund_flow_unit_verified=True,
                fund_flow_anomaly_tags=["CONSECUTIVE_INFLOW"],
            ),
        ]
        result = run_pool_gate(entries)
        summary = result.strength_tier_summary
        assert summary["primary_count"] == 1
        assert summary["secondary_count"] == 0
        assert summary["main_before_tier"] == 1
        assert summary["main_after_tier"] == 1
        assert summary["secondary_overflow_to_observation"] == []
        assert "downgrade_reasons" in summary
        assert "headline" in summary

    def test_pool_counts_has_tier_split(self):
        entries = [
            _make_entry(
                symbol="X",
                candidate_type="TECH_TRADE",
                tier="A",
                positive_category_count=3,
                composite_score=85,
                data_completeness=0.8,
                tradeflow_data_completeness=0.8,
                strategies=["VCP"],
                trigger_price=10.0,
                invalid_price=9.0,
                fund_flow_anomaly_score=15,
                fund_flow_unit_verified=True,
                fund_flow_anomaly_tags=["CONSECUTIVE_INFLOW"],
            ),
        ]
        result = run_pool_gate(entries)
        counts = result.pool_counts
        assert counts["primary"] == 1
        assert counts["secondary"] == 0
        assert counts["main"] == 1
        assert counts["secondary_overflow"] == 0

    def test_existing_scoring_fields_preserved(self):
        """[acceptance] 现有候选评分字段不丢失."""
        entries = [
            _make_entry(
                symbol="KEEP",
                candidate_type="TECH_TRADE",
                tier="A",
                positive_category_count=3,
                composite_score=85,
                data_completeness=0.8,
                tradeflow_data_completeness=0.8,
                strategies=["VCP", "PULLBACK_SUPPORT"],
                trigger_price=10.0,
                invalid_price=9.0,
                fund_flow_anomaly_score=15,
                fund_flow_unit_verified=True,
                fund_flow_anomaly_tags=["CONSECUTIVE_INFLOW"],
                ranking_reasons=["VCP形态(60分)", "资金共振(+10分)"],
                technical_score=40.0,
                policy_score=0.0,
                fund_flow_score=15.0,
            ),
        ]
        result = run_pool_gate(entries)
        main = result.main_candidates[0]
        assert main["composite_score"] == 85
        assert main["technical_score"] == 40.0
        assert main["fund_flow_score"] == 15.0
        assert "VCP" in main["strategies"]
        assert len(main["ranking_reasons"]) == 2
        assert "precision_dimensions" in main
        assert "precision_resonance_count" in main


class TestAcceptanceFixtureMixed:
    """[TF-QUALITY-005 acceptance] Mixed fixture covering VCP / 回踩 / 事件 / 昊天."""

    def _build_mixed_fixture(self):
        """Build a fixture that mirrors the task's four themes."""
        entries = []

        # ── VCP strong (primary) ──
        entries.append(_make_entry(
            symbol="VCP_PRIM",
            candidate_type="TECH_TRADE",
            tier="A",
            positive_category_count=3,
            composite_score=88,
            data_completeness=0.8,
            tradeflow_data_completeness=0.8,
            strategies=["VCP"],
            trigger_price=10.0,
            invalid_price=9.0,
            fund_flow_anomaly_score=15,
            fund_flow_unit_verified=True,
            fund_flow_anomaly_tags=["CONSECUTIVE_INFLOW"],
            ranking_reasons=["VCP形态", "资金共振"],
        ))

        # ── VCP weak (secondary: no capital) ──
        entries.append(_make_entry(
            symbol="VCP_SEC",
            candidate_type="TECH_TRADE",
            tier="B",
            positive_category_count=2,
            composite_score=68,
            data_completeness=0.7,
            tradeflow_data_completeness=0.7,
            strategies=["VCP"],
            trigger_price=10.0,
            invalid_price=9.0,
            fund_flow_anomaly_score=0,
            fund_flow_unit_verified=False,
        ))

        # ── 回踩 (PULLBACK_SUPPORT) ──
        entries.append(_make_entry(
            symbol="PULL_STRG",
            candidate_type="TECH_TRADE",
            tier="A",
            positive_category_count=3,
            composite_score=75,
            data_completeness=0.7,
            tradeflow_data_completeness=0.7,
            strategies=["PULLBACK_SUPPORT"],
            trigger_price=10.0,
            invalid_price=9.0,
            fund_flow_anomaly_score=10,
            fund_flow_unit_verified=True,
            fund_flow_anomaly_tags=["CONSECUTIVE_INFLOW"],
        ))

        # ── 事件 (EVENT_WATCH) ──
        entries.append(_make_entry(
            symbol="EV_OK",
            candidate_type="EVENT_WATCH",
            tier="A",
            positive_category_count=3,
            composite_score=72,
            data_completeness=0.7,
            tradeflow_data_completeness=0.7,
            event_score=20,
        ))

        # ── 昊天 strong (primary) ──
        entries.append(_make_entry(
            symbol="HAO_PRIM",
            candidate_type="POLICY_AMBUSH",
            tier="A",
            positive_category_count=3,
            composite_score=82,
            data_completeness=0.7,
            tradeflow_data_completeness=0.7,
            mandate_score_component=60,
            beneficiary_score_component=50,
            policy_tags=["低空经济"],
            version_score=20,
        ))

        # ── 昊天 weak (secondary: single evidence) ──
        entries.append(_make_entry(
            symbol="HAO_SEC",
            candidate_type="POLICY_AMBUSH",
            tier="B",
            positive_category_count=3,
            composite_score=70,
            data_completeness=0.7,
            tradeflow_data_completeness=0.7,
            mandate_score_component=30,
            beneficiary_score_component=0,
            policy_tags=["AI"],
            version_score=10,
        ))

        return entries

    def test_primary_count_less_than_total_main(self):
        """[acceptance] fixture 中主候选数量明显少于总候选."""
        entries = self._build_mixed_fixture()
        result = run_pool_gate(entries)
        assert len(result.primary_candidates) < len(entries)
        # And primary is at most equal to main_candidates.
        assert len(result.primary_candidates) <= len(result.main_candidates)

    def test_every_downgraded_candidate_has_readable_reason(self):
        """[acceptance] 每只被降层候选有可读原因."""
        entries = self._build_mixed_fixture()
        result = run_pool_gate(entries)
        for sec in result.secondary_candidates:
            assert sec["strength_tier"] == TIER_SECONDARY
            assert sec["tier_reason"] != ""
            # Reason must contain at least one of the named patterns.
            named = ("弱缩量", "反弹", "数据不足", "共振维度", "综合分", "弱主题", "证据单一")
            assert any(p in sec["tier_reason"] for p in named)

    def test_scoring_fields_not_lost(self):
        """[acceptance] 现有候选评分字段不丢失."""
        entries = self._build_mixed_fixture()
        result = run_pool_gate(entries)
        for c in result.main_candidates:
            # Original scoring fields preserved.
            assert "composite_score" in c
            assert "precision_dimensions" in c
            assert "precision_resonance_count" in c
            # New tier fields added.
            assert "strength_tier" in c
            assert "tier_reason" in c

    def test_strong_vcp_is_primary(self):
        entries = self._build_mixed_fixture()
        result = run_pool_gate(entries)
        primary_syms = {c["symbol"] for c in result.primary_candidates}
        assert "VCP_PRIM" in primary_syms

    def test_weak_vcp_is_secondary(self):
        entries = self._build_mixed_fixture()
        result = run_pool_gate(entries)
        secondary_syms = {c["symbol"] for c in result.secondary_candidates}
        # VCP_SEC has positive_category_count=2 (so 量能=True) but no fund flow
        # → 资金=False → secondary.
        assert "VCP_SEC" in secondary_syms

    def test_strong_haotian_is_primary(self):
        entries = self._build_mixed_fixture()
        result = run_pool_gate(entries)
        primary_syms = {c["symbol"] for c in result.primary_candidates}
        assert "HAO_PRIM" in primary_syms

    def test_event_candidate_classified(self):
        entries = self._build_mixed_fixture()
        result = run_pool_gate(entries)
        all_main_syms = {c["symbol"] for c in result.main_candidates}
        # EV_OK composite=72 > 65 threshold → primary, OR may land in main either way.
        assert "EV_OK" in all_main_syms or "EV_OK" in {
            c["symbol"] for c in result.observation_candidates
        }

    def test_main_not_exceed_effective_cap(self):
        """Tier layer must not inflate main beyond effective_main_cap."""
        entries = self._build_mixed_fixture()
        result = run_pool_gate(entries)
        assert len(result.main_candidates) <= result.pool_counts["effective_main_max"]


class TestTwentyCandidateFixture:
    """[TF-QUALITY-005] 20-candidate fixture: primary << total, no false-kill."""

    def _build_20(self):
        entries = []
        # 3 strong TECH (primary)
        for i in range(3):
            entries.append(_make_entry(
                symbol=f"TECH_S{i}",
                candidate_type="TECH_TRADE",
                tier="A",
                positive_category_count=3,
                composite_score=85 - i * 5,
                data_completeness=0.8,
                tradeflow_data_completeness=0.8,
                strategies=["VCP"],
                trigger_price=10.0,
                invalid_price=9.0,
                fund_flow_anomaly_score=15,
                fund_flow_unit_verified=True,
                fund_flow_anomaly_tags=["CONSECUTIVE_INFLOW"],
            ))
        # 3 strong POLICY (primary, with mandate + beneficiary)
        for i in range(3):
            entries.append(_make_entry(
                symbol=f"POL_S{i}",
                candidate_type="POLICY_AMBUSH",
                tier="A",
                positive_category_count=3,
                composite_score=80 - i * 5,
                data_completeness=0.7,
                tradeflow_data_completeness=0.7,
                mandate_score_component=60,
                beneficiary_score_component=50,
                policy_tags=["低空经济"],
                version_score=20,
            ))
        # 7 weak TECH (secondary: no capital)
        for i in range(7):
            entries.append(_make_entry(
                symbol=f"TECH_W{i}",
                candidate_type="TECH_TRADE",
                tier="B",
                positive_category_count=3,
                composite_score=68,
                data_completeness=0.7,
                tradeflow_data_completeness=0.7,
                strategies=["VCP"],
                trigger_price=10.0,
                invalid_price=9.0,
                fund_flow_anomaly_score=0,
            ))
        # 7 weak POLICY (observation via calibration, never primary)
        for i in range(7):
            entries.append(_make_entry(
                symbol=f"POL_W{i}",
                candidate_type="POLICY_AMBUSH",
                tier="B",
                positive_category_count=3,
                composite_score=50,
                data_completeness=0.7,
                tradeflow_data_completeness=0.7,
                mandate_score_component=0,
                beneficiary_score_component=0,
                policy_tags=["未知主题"],
                version_score=0,
            ))
        return entries

    def test_primary_fewer_than_total_main(self):
        entries = self._build_20()
        result = run_pool_gate(entries)
        assert len(result.primary_candidates) < len(entries)
        assert len(result.primary_candidates) <= 5

    def test_primary_includes_strong_tech(self):
        entries = self._build_20()
        result = run_pool_gate(entries)
        primary_syms = {c["symbol"] for c in result.primary_candidates}
        assert "TECH_S0" in primary_syms

    def test_primary_includes_strong_policy(self):
        entries = self._build_20()
        result = run_pool_gate(entries)
        primary_syms = {c["symbol"] for c in result.primary_candidates}
        assert "POL_S0" in primary_syms

    def test_weak_tech_not_in_primary(self):
        entries = self._build_20()
        result = run_pool_gate(entries)
        primary_syms = {c["symbol"] for c in result.primary_candidates}
        for i in range(7):
            assert f"TECH_W{i}" not in primary_syms

    def test_secondary_respects_max_count(self):
        entries = self._build_20()
        result = run_pool_gate(entries)
        assert len(result.secondary_candidates) <= StrategyConfig().tier_secondary_max_count

    def test_every_secondary_has_readable_reason(self):
        entries = self._build_20()
        result = run_pool_gate(entries)
        for sec in result.secondary_candidates:
            assert sec["tier_reason"] != ""

    def test_overflow_candidates_have_pool_filter_reason(self):
        entries = self._build_20()
        result = run_pool_gate(entries)
        for sym in result.strength_tier_summary["secondary_overflow_to_observation"]:
            obs = next(c for c in result.observation_candidates if c["symbol"] == sym)
            assert "[TF-QUALITY-005]" in obs["pool_filter_reason"]


class TestBackwardCompat:
    """[TF-QUALITY-005] Existing summaries and counts remain populated."""

    def test_calibration_summary_still_present(self):
        entries = [_make_entry(symbol="X", composite_score=80, positive_category_count=3)]
        result = run_pool_gate(entries)
        assert "weak_vcp_downgraded" in result.calibration_summary
        assert "effective_main_cap" in result.calibration_summary

    def test_concentration_summary_still_present(self):
        entries = [
            _make_entry(
                symbol="H",
                candidate_type="POLICY_AMBUSH",
                positive_category_count=3,
                composite_score=80,
                mandate_score_component=60,
                beneficiary_score_component=50,
                policy_tags=["低空经济"],
                version_score=20,
            )
        ]
        result = run_pool_gate(entries)
        assert "enabled" in result.concentration_summary

    def test_pool_counts_legacy_keys(self):
        entries = [_make_entry(symbol="X", composite_score=80, positive_category_count=3)]
        result = run_pool_gate(entries)
        counts = result.pool_counts
        for key in ("main", "observation", "filtered", "main_max", "effective_main_max"):
            assert key in counts

    def test_gate_summary_still_present(self):
        entries = [_make_entry(symbol="X", composite_score=80, positive_category_count=3)]
        result = run_pool_gate(entries)
        assert "主候选" in result.gate_summary


class TestEdgeCases:
    """[TF-QUALITY-005] Edge cases: empty, all primary, all secondary, mixed."""

    def test_empty_entries(self):
        result = run_pool_gate([])
        assert result.primary_candidates == []
        assert result.secondary_candidates == []
        assert result.main_candidates == []
        assert result.strength_tier_summary["primary_count"] == 0
        assert result.strength_tier_summary["secondary_count"] == 0

    def test_all_primary(self):
        cfg = StrategyConfig(pool_main_max=5, pool_tech_max=5)
        entries = [
            _make_entry(
                symbol=f"P{i}",
                candidate_type="TECH_TRADE",
                tier="A",
                positive_category_count=3,
                composite_score=85,
                data_completeness=0.8,
                tradeflow_data_completeness=0.8,
                strategies=["VCP"],
                trigger_price=10.0,
                invalid_price=9.0,
                fund_flow_anomaly_score=15,
                fund_flow_unit_verified=True,
                fund_flow_anomaly_tags=["CONSECUTIVE_INFLOW"],
            )
            for i in range(3)
        ]
        result = run_pool_gate(entries, cfg)
        assert len(result.primary_candidates) == 3
        assert len(result.secondary_candidates) == 0

    def test_all_secondary_with_overflow(self):
        """All secondary candidates: tier_secondary_max_count enforces compression."""
        cfg = StrategyConfig(
            pool_main_max=10,
            pool_tech_max=10,
            tier_secondary_max_count=2,
        )
        entries = [
            _make_entry(
                symbol=f"S{i}",
                candidate_type="TECH_TRADE",
                tier="A",
                positive_category_count=3,
                composite_score=68,
                data_completeness=0.7,
                tradeflow_data_completeness=0.7,
                strategies=["VCP"],
                trigger_price=10.0,
                invalid_price=9.0,
                fund_flow_anomaly_score=0,
            )
            for i in range(5)
        ]
        result = run_pool_gate(entries, cfg)
        assert len(result.primary_candidates) == 0
        assert len(result.secondary_candidates) == 2
        assert len(result.strength_tier_summary["secondary_overflow_to_observation"]) == 3

    def test_mixed_primary_secondary_observation_filtered(self):
        """Full pipeline: primary + secondary + observation + filtered all populated."""
        entries = [
            # primary TECH
            _make_entry(
                symbol="P1",
                candidate_type="TECH_TRADE",
                tier="A",
                positive_category_count=3,
                composite_score=85,
                data_completeness=0.8,
                tradeflow_data_completeness=0.8,
                strategies=["VCP"],
                trigger_price=10.0,
                invalid_price=9.0,
                fund_flow_anomaly_score=15,
                fund_flow_unit_verified=True,
                fund_flow_anomaly_tags=["CONSECUTIVE_INFLOW"],
            ),
            # secondary TECH (no capital)
            _make_entry(
                symbol="S1",
                candidate_type="TECH_TRADE",
                tier="A",
                positive_category_count=3,
                composite_score=70,
                data_completeness=0.7,
                tradeflow_data_completeness=0.7,
                strategies=["VCP"],
                trigger_price=10.0,
                invalid_price=9.0,
                fund_flow_anomaly_score=0,
            ),
            # filtered C-tier
            _make_entry(
                symbol="F1",
                tier="C",
                composite_score=10,
            ),
        ]
        result = run_pool_gate(entries)
        primary_syms = {c["symbol"] for c in result.primary_candidates}
        secondary_syms = {c["symbol"] for c in result.secondary_candidates}
        filtered_syms = {c["symbol"] for c in result.filtered_candidates}
        assert "P1" in primary_syms
        assert "S1" in secondary_syms
        assert "F1" in filtered_syms


class TestNoFalseKill:
    """[TF-QUALITY-005] Strong candidates must not be demoted."""

    def test_strong_tech_remains_primary(self):
        entry = _make_entry(
            symbol="STRONG",
            candidate_type="TECH_TRADE",
            tier="A",
            positive_category_count=3,
            composite_score=85,
            data_completeness=0.8,
            tradeflow_data_completeness=0.8,
            strategies=["VCP"],
            trigger_price=10.0,
            invalid_price=9.0,
            fund_flow_anomaly_score=15,
            fund_flow_unit_verified=True,
            fund_flow_anomaly_tags=["CONSECUTIVE_INFLOW"],
        )
        result = run_pool_gate([entry])
        assert len(result.primary_candidates) == 1
        assert result.primary_candidates[0]["symbol"] == "STRONG"

    def test_strong_haotian_remains_primary(self):
        entry = _make_entry(
            symbol="STRONG_H",
            candidate_type="POLICY_AMBUSH",
            tier="A",
            positive_category_count=3,
            composite_score=82,
            mandate_score_component=60,
            beneficiary_score_component=50,
            policy_tags=["低空经济"],
            version_score=20,
            data_completeness=0.7,
            tradeflow_data_completeness=0.7,
        )
        result = run_pool_gate([entry])
        assert len(result.primary_candidates) == 1
        assert result.primary_candidates[0]["symbol"] == "STRONG_H"

    def test_haotian_with_event_narrative_remains_primary(self):
        """昊天 with mandate + event + narrative but no beneficiary → primary if topic strong."""
        entry = _make_entry(
            symbol="CROSS_H",
            candidate_type="POLICY_AMBUSH",
            tier="A",
            positive_category_count=3,
            composite_score=78,
            mandate_score_component=40,
            beneficiary_score_component=0,
            policy_tags=["AI"],
            version_score=15,
            event_score=20,
            narrative_score=15,
            data_completeness=0.7,
            tradeflow_data_completeness=0.7,
        )
        result = run_pool_gate([entry])
        # mandate=40 + event=20 + narrative=15 → 3 support dims → primary eligible.
        assert len(result.primary_candidates) == 1


class TestConvergenceReport:
    """[TF-QUALITY-005] Output explains N → M compression."""

    def test_compression_report_keys(self):
        entries = [
            _make_entry(
                symbol="A",
                candidate_type="TECH_TRADE",
                tier="A",
                positive_category_count=3,
                composite_score=85,
                data_completeness=0.8,
                tradeflow_data_completeness=0.8,
                strategies=["VCP"],
                trigger_price=10.0,
                invalid_price=9.0,
                fund_flow_anomaly_score=15,
                fund_flow_unit_verified=True,
                fund_flow_anomaly_tags=["CONSECUTIVE_INFLOW"],
            ),
            _make_entry(
                symbol="B",
                candidate_type="TECH_TRADE",
                tier="A",
                positive_category_count=3,
                composite_score=70,
                data_completeness=0.7,
                tradeflow_data_completeness=0.7,
                strategies=["VCP"],
                trigger_price=10.0,
                invalid_price=9.0,
                fund_flow_anomaly_score=0,
            ),
        ]
        result = run_pool_gate(entries)
        summary = result.strength_tier_summary
        # Compression report must explain N → M.
        assert "main_before_tier" in summary
        assert "main_after_tier" in summary
        assert summary["main_before_tier"] >= summary["main_after_tier"]
        assert "downgrade_reasons" in summary
        assert "headline" in summary

    def test_downgrade_reasons_buckets_complete(self):
        entries = [
            _make_entry(
                symbol="X",
                candidate_type="TECH_TRADE",
                tier="A",
                positive_category_count=3,
                composite_score=80,
                data_completeness=0.8,
                tradeflow_data_completeness=0.8,
                strategies=["VCP"],
                trigger_price=10.0,
                invalid_price=9.0,
                fund_flow_anomaly_score=0,
            ),
        ]
        result = run_pool_gate(entries)
        reasons = result.strength_tier_summary["downgrade_reasons"]
        for bucket in (
            "weak_volume_capital",
            "rebound_trend_unrepaired",
            "data_insufficient",
            "low_resonance",
            "low_composite",
            "weak_topic",
            "single_evidence",
        ):
            assert bucket in reasons
            assert isinstance(reasons[bucket], list)
