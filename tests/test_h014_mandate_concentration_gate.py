# [H-014] mandate_concentration_gate
"""Tests for the 昊天 theme concentration gate (H-014).

Verifies:
1. Topic strength classification: strong / moderate / weak.
2. Strong theme → at most ``concentration_per_topic_max`` main slots.
3. Weak theme → 0 main slots, all go to observation with a reason.
4. Moderate theme → at most 1 main slot.
5. Candidates without a resolvable topic fall through (no H-014 block).
6. Feature flag ``concentration_enabled=False`` disables the gate.
7. 20-candidate fixture → main candidates ≤ 5 (acceptance criterion).
8. ``concentration_summary`` answers the 3 questions: top theme / symbols /
   evidence gaps, plus downgrade audit trails.
9. Integration via ``run_pool_gate`` keeps backward compatibility for
   non-haotian candidates and existing pool caps.
"""

import pytest

from tradingagents.tradeflow.candidate_pool_gate import run_pool_gate
from tradingagents.tradeflow.mandate_concentration_gate import (
    STRENGTH_MODERATE,
    STRENGTH_STRONG,
    STRENGTH_UNKNOWN,
    STRENGTH_WEAK,
    TopicConcentrationMetrics,
    assess_topic_strength,
    build_concentration_summary,
    compute_topic_metrics_map,
    resolve_topic_for_entry,
)
from tradingagents.tradeflow.strategy_config import StrategyConfig
from tradingagents.tradeflow.topic_lifecycle import TopicLifecycleState


def _haotian_entry(
    symbol="H001",
    topic="低空经济",
    lifecycle_state=TopicLifecycleState.ACCELERATING.value,
    signal_count=5,
    mandate_score=60.0,
    composite_score=75.0,
    candidate_type="POLICY_AMBUSH",
    policy_tags=None,
    blocking_evidence_gaps=None,
    overheat_flags=None,
    name="测试",
):
    """Build a POLICY candidate dict that passes precision + calibration."""
    return {
        "symbol": symbol,
        "name": name,
        "candidate_type": candidate_type,
        "tier": "A",
        "composite_score": composite_score,
        "positive_category_count": 3,
        "data_completeness": 0.7,
        "tradeflow_data_completeness": 0.7,
        "strategies": [],
        "fund_flow_anomaly_score": 0,
        "fund_flow_unit_verified": False,
        "fund_flow_anomaly_tags": [],
        "trigger_price": None,
        "invalid_price": None,
        "mandate_score_component": mandate_score,
        "beneficiary_score_component": 30.0,
        "policy_tags": policy_tags if policy_tags is not None else [topic],
        "mandate_topic": topic,
        "counter_evidence": [],
        "risk_flags": [],
        "overheat_penalty": 0,
        "overheat_flags": overheat_flags or [],
        "topic_lifecycle_state": lifecycle_state,
        "topic_signal_count": signal_count,
        "topic_last_signal_date": "2026-06-20",
        "blocking_evidence_gaps": blocking_evidence_gaps or [],
        "score": composite_score,
        # split scores so precision 政策主题/受益路径 dims light up
        "policy_score": 20.0,
        "version_score": 15.0,
        "mandate_score": mandate_score,
        "beneficiary_path": ["整机"],
        "event_score": 10.0,
        "narrative_score": 5.0,
        "fund_flow_score": 0.0,
        "data_quality_score": 70.0,
    }


# ── Unit: assess_topic_strength ───────────────────────────────────────


class TestAssessTopicStrength:
    def test_strong_topic(self):
        entries = [
            _haotian_entry(symbol="A", mandate_score=60),
            _haotian_entry(symbol="B", mandate_score=55),
        ]
        m = assess_topic_strength("低空经济", entries)
        assert m.strength == STRENGTH_STRONG
        assert m.admission_cap == StrategyConfig().concentration_per_topic_max
        assert m.has_left_side_lifecycle is True
        assert m.is_observe_only is False
        assert "加速" in m.strength_reason or "条政策信号" in m.strength_reason

    def test_weak_topic_observe_only(self):
        entries = [_haotian_entry(lifecycle_state=TopicLifecycleState.FADING.value)]
        m = assess_topic_strength("新能源", entries)
        assert m.strength == STRENGTH_WEAK
        assert m.admission_cap == 0
        assert m.is_observe_only is True

    def test_weak_topic_crowded(self):
        entries = [_haotian_entry(lifecycle_state=TopicLifecycleState.CROWDED.value)]
        m = assess_topic_strength("算力", entries)
        assert m.strength == STRENGTH_WEAK
        assert m.admission_cap == 0

    def test_moderate_topic_low_signals(self):
        # left-side lifecycle but signal_count below threshold and no mandate score
        entries = [_haotian_entry(signal_count=1, mandate_score=0.0)]
        m = assess_topic_strength("机器人", entries)
        assert m.strength == STRENGTH_MODERATE
        assert m.admission_cap == StrategyConfig().concentration_moderate_topic_max

    def test_moderate_topic_unknown_lifecycle(self):
        entries = [_haotian_entry(lifecycle_state=TopicLifecycleState.UNKNOWN.value)]
        m = assess_topic_strength("出海", entries)
        assert m.strength == STRENGTH_MODERATE

    def test_empty_topic(self):
        m = assess_topic_strength("", [_haotian_entry()])
        assert m.strength in (STRENGTH_WEAK, STRENGTH_UNKNOWN) or m.candidate_count == 0
        assert m.admission_cap == 0 or m.strength in (STRENGTH_MODERATE, STRENGTH_WEAK, STRENGTH_UNKNOWN)

    def test_evidence_gap_burden_aggregated(self):
        entries = [
            _haotian_entry(blocking_evidence_gaps=["gap1", "gap2"]),
            _haotian_entry(blocking_evidence_gaps=["gap3"]),
        ]
        m = assess_topic_strength("低空经济", entries)
        assert m.evidence_gap_burden == 3


# ── Unit: compute_topic_metrics_map ───────────────────────────────────


class TestComputeTopicMetricsMap:
    def test_groups_by_topic(self):
        entries = [
            _haotian_entry(symbol="A", topic="低空经济"),
            _haotian_entry(symbol="B", topic="低空经济"),
            _haotian_entry(symbol="C", topic="算力"),
        ]
        metrics_map = compute_topic_metrics_map(entries)
        assert set(metrics_map.keys()) == {"低空经济", "算力"}
        assert metrics_map["低空经济"].candidate_count == 2

    def test_skips_non_haotian(self):
        tech_entry = _haotian_entry()
        tech_entry["candidate_type"] = "TECH_TRADE"
        metrics_map = compute_topic_metrics_map([tech_entry])
        assert metrics_map == {}

    def test_skips_unresolved_topic(self):
        entry = _haotian_entry()
        entry["mandate_topic"] = ""
        entry["policy_tags"] = []
        entry["name"] = "无名公司"
        metrics_map = compute_topic_metrics_map([entry])
        # "无名公司" does not match any predefined topic keyword
        assert metrics_map == {}


# ── Unit: resolve_topic_for_entry ─────────────────────────────────────


class TestResolveTopic:
    def test_resolves_via_mandate_topic(self):
        assert resolve_topic_for_entry(_haotian_entry(topic="低空经济")) == "低空经济"

    def test_resolves_via_policy_tag(self):
        entry = _haotian_entry()
        entry["mandate_topic"] = ""
        entry["policy_tags"] = ["光模块"]
        assert resolve_topic_for_entry(entry) == "算力"

    def test_empty_when_no_signal(self):
        entry = _haotian_entry()
        entry["mandate_topic"] = ""
        entry["policy_tags"] = []
        entry["name"] = "某公司"
        assert resolve_topic_for_entry(entry) == ""


# ── Integration: run_pool_gate concentration ──────────────────────────


class TestRunPoolGateConcentration:
    def test_concentration_summary_present(self):
        entries = [_haotian_entry(symbol="A")]
        result = run_pool_gate(entries)
        assert "enabled" in result.concentration_summary
        assert "headline" in result.concentration_summary
        assert "per_topic" in result.concentration_summary

    def test_strong_topic_cap_at_three(self):
        """5 candidates in one strong theme → only Top 3 in main."""
        cfg = StrategyConfig(pool_haotian_max=10, pool_main_max=10)
        entries = [
            _haotian_entry(symbol=f"H{i}", composite_score=80 - i)
            for i in range(5)
        ]
        result = run_pool_gate(entries, cfg)
        haotian_main = [c for c in result.main_candidates if c["candidate_type"] in ("POLICY_AMBUSH", "POLICY_CONFIRM")]
        assert len(haotian_main) == 3
        overflow = [o for o in result.observation_candidates if "H-014主题集中度" in o.get("pool_filter_reason", "")]
        assert len(overflow) == 2
        assert "concentration_strength" in result.main_candidates[0]

    def test_weak_topic_all_downgraded(self):
        """Receding theme → no main slots, all observation with reason."""
        entries = [
            _haotian_entry(symbol=f"W{i}", lifecycle_state=TopicLifecycleState.FADING.value)
            for i in range(3)
        ]
        result = run_pool_gate(entries)
        assert len(result.main_candidates) == 0
        assert len(result.observation_candidates) == 3
        for obs in result.observation_candidates:
            assert "H-014主题集中度" in obs["pool_filter_reason"]
            assert obs.get("concentration_strength") == STRENGTH_WEAK
        assert result.concentration_summary["weak_topics_downgraded"]

    def test_moderate_topic_cap_at_one(self):
        cfg = StrategyConfig(pool_haotian_max=10, pool_main_max=10)
        entries = [
            _haotian_entry(
                symbol=f"M{i}",
                lifecycle_state=TopicLifecycleState.UNKNOWN.value,
                signal_count=0,
                mandate_score=0.0,
            )
            for i in range(3)
        ]
        result = run_pool_gate(entries, cfg)
        haotian_main = [c for c in result.main_candidates if c["candidate_type"] in ("POLICY_AMBUSH", "POLICY_CONFIRM")]
        # moderate → at most 1 (may be fewer if other gates fire)
        assert len(haotian_main) <= 1

    def test_mixed_strong_and_weak_topics(self):
        """Strong theme contributes Top 3, weak theme contributes 0."""
        cfg = StrategyConfig(pool_haotian_max=10, pool_main_max=10)
        entries = [
            _haotian_entry(symbol=f"S{i}", topic="低空经济", composite_score=80 - i)
            for i in range(4)
        ] + [
            _haotian_entry(symbol=f"W{i}", topic="新能源",
                           lifecycle_state=TopicLifecycleState.FADING.value,
                           composite_score=60)
            for i in range(2)
        ]
        result = run_pool_gate(entries, cfg)
        haotian_main = [c for c in result.main_candidates if c["candidate_type"] in ("POLICY_AMBUSH", "POLICY_CONFIRM")]
        assert len(haotian_main) == 3
        # all main come from 低空经济
        assert all(c.get("concentration_topic") == "低空经济" for c in haotian_main)
        # 新能源 all in observation
        new_energy_obs = [o for o in result.observation_candidates if o.get("concentration_topic") == "新能源"]
        assert len(new_energy_obs) == 2

    def test_disabled_flag_skips_gate(self):
        cfg = StrategyConfig(concentration_enabled=False, pool_haotian_max=10, pool_main_max=10)
        entries = [
            _haotian_entry(symbol=f"H{i}", composite_score=80 - i)
            for i in range(6)
        ]
        result = run_pool_gate(entries, cfg)
        # gate disabled → no concentration metrics, only pool_haotian_max governs
        assert result.concentration_summary["enabled"] is False
        haotian_main = [c for c in result.main_candidates if c["candidate_type"] in ("POLICY_AMBUSH", "POLICY_CONFIRM")]
        assert len(haotian_main) == 6  # pool_haotian_max=10 allows all
        assert "concentration_strength" not in haotian_main[0]

    def test_unresolved_topic_falls_through(self):
        """Haotian candidate whose topic can't be resolved still goes through
        the existing pool caps (not blocked by H-014)."""
        entry = _haotian_entry()
        entry["mandate_topic"] = ""
        entry["policy_tags"] = []
        entry["name"] = "完全无主题信号公司"
        result = run_pool_gate([entry])
        # Should reach main or observation via existing caps, never hard-blocked
        all_results = result.main_candidates + result.observation_candidates
        assert any(c["symbol"] == "H001" for c in all_results)

    def test_concentration_runs_before_global_haotian_cap(self):
        """Per-topic cap is stricter than the global haotian_max."""
        # Two strong topics, 3 each, global cap 10 → per-topic admits 3+3=6
        cfg = StrategyConfig(pool_haotian_max=10, pool_main_max=10)
        entries = [
            _haotian_entry(symbol=f"A{i}", topic="低空经济", composite_score=80 - i)
            for i in range(3)
        ] + [
            _haotian_entry(symbol=f"B{i}", topic="算力", composite_score=70 - i)
            for i in range(3)
        ]
        result = run_pool_gate(entries, cfg)
        haotian_main = [c for c in result.main_candidates if c["candidate_type"] in ("POLICY_AMBUSH", "POLICY_CONFIRM")]
        assert len(haotian_main) == 6
        topics_in_main = {c.get("concentration_topic") for c in haotian_main}
        assert topics_in_main == {"低空经济", "算力"}


# ── Acceptance: fixture 20 candidates → ≤ 5 main ──────────────────────


class TestFixtureTwentyCandidates:
    """Acceptance criterion from H-014: 20 haotian candidates → ≤ 5 main."""

    def test_twenty_haotian_main_max_five(self):
        cfg = StrategyConfig()  # default: per_topic_max=3, haotian_max=3, main_max=5
        entries = []
        # 4 topics × 5 candidates each = 20, mixed lifecycle
        topics = [
            ("低空经济", TopicLifecycleState.ACCELERATING.value, 5),    # strong
            ("算力", TopicLifecycleState.CONFIRMING.value, 5),          # strong
            ("新能源", TopicLifecycleState.FADING.value, 5),            # weak → 0
            ("机器人", TopicLifecycleState.UNKNOWN.value, 5),           # moderate → 1
        ]
        idx = 0
        for topic, lifecycle, count in topics:
            for _ in range(count):
                entries.append(_haotian_entry(
                    symbol=f"S{idx:02d}",
                    topic=topic,
                    lifecycle_state=lifecycle,
                    composite_score=80 - idx,
                ))
                idx += 1
        result = run_pool_gate(entries, cfg)
        # main ≤ 5 acceptance criterion
        assert len(result.main_candidates) <= 5
        # weak (新能源) must NOT be in main
        assert not any(c.get("concentration_topic") == "新能源" for c in result.main_candidates)
        # observation keeps the weak-theme reasons
        weak_obs = [o for o in result.observation_candidates if o.get("concentration_topic") == "新能源"]
        assert len(weak_obs) == 5

    def test_summary_answers_three_questions(self):
        """concentration_summary answers: top theme / top symbols / gaps."""
        cfg = StrategyConfig(pool_haotian_max=10, pool_main_max=10)
        entries = [
            _haotian_entry(symbol="A1", topic="低空经济", composite_score=85),
            _haotian_entry(symbol="A2", topic="低空经济", composite_score=80,
                           blocking_evidence_gaps=["政策原文缺失"]),
            _haotian_entry(symbol="W1", topic="新能源",
                           lifecycle_state=TopicLifecycleState.FADING.value),
        ]
        result = run_pool_gate(entries, cfg)
        summary = result.concentration_summary
        assert summary["top_theme"] == "低空经济"
        assert summary["top_theme_status"]  # non-empty status label
        assert "A1" in summary["top_theme_main_symbols"]
        assert "新能源" in summary["weak_topics_downgraded"]
        # per_topic payload records evidence gaps
        topic_payload = summary["per_topic"]["低空经济"]
        assert topic_payload["evidence_gap_burden"] >= 1


# ── Backward compatibility ────────────────────────────────────────────


class TestBackwardCompat:
    def test_tech_candidates_unaffected(self):
        """TECH_TRADE candidates must not be touched by the concentration gate."""
        from tests.test_tf_quality001_pool_gate import _make_entry  # reuse helper
        entries = [
            _make_entry(symbol=f"T{i}", candidate_type="TECH_TRADE",
                        positive_category_count=3, composite_score=70)
            for i in range(3)
        ]
        result = run_pool_gate(entries)
        assert len(result.main_candidates) == 3
        for c in result.main_candidates:
            assert "concentration_strength" not in c

    def test_pool_counts_still_present(self):
        entries = [_haotian_entry(symbol="A")]
        result = run_pool_gate(entries)
        assert "main" in result.pool_counts
        assert "haotian_in_main" in result.pool_counts
        assert "effective_main_max" in result.pool_counts

    def test_pool_result_has_concentration_summary_field(self):
        from tradingagents.tradeflow.candidate_pool_gate import PoolGateResult
        result = PoolGateResult()
        assert hasattr(result, "concentration_summary")
        assert result.concentration_summary == {}
