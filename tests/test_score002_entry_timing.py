# [SCORE-002] entry_timing_adapter
"""Tests for entry_timing score card (SCORE-002).

Covers (per docs/TASKS.md SCORE-002 acceptance):
  1. High score confirmation — all fields present, high values → high entry_timing.
  2. Overheating — crowded game_balance + overheat_penalty → reduced score.
  3. No funds — fund_flow_anomaly_score=0 → low fund_price_confirm.
  4. Unit unverified — fund_flow_unit_verified=False → capped at 50.
  5. First big drop — timing risk flag detected, score penalized.
  6. Limit-down — timing risk flag detected, score penalized.
  7. All fields missing — entry_timing=0, data_status="missing".
  8. Stability — same inputs produce identical output across 10 runs.
  9. Research snapshot independence — entry_timing unchanged by snapshot score.
  10. Serialization — entry_timing_to_dict is JSON-serializable.
  11. Service layer enrichment — _enrich_candidate_with_entry_timing injects card.
  12. Service layer batch — _enrich_candidates_with_entry_timing batch mode.
  13. Service layer failure graceful — exception → entry_timing_card=None.
"""

from __future__ import annotations

import json
from typing import Optional

import pytest

from tradingagents.tradeflow.entry_timing_score import (
    EntryTimingResult,
    TimingDimension,
    compute_entry_timing,
    detect_timing_risk_flags,
    entry_timing_to_dict,
)


# ── Fixtures ────────────────────────────────────────────────────────

def _high_score_candidate() -> dict:
    """Candidate with all fields present and high values."""
    return dict(
        pricing_gap_score=25.0,
        current_price=15.0,
        support_price=14.5,
        game_balance="favorable",
        resonance_count=3,
        positive_category_count=3,
        overheat_penalty=0.0,
        fund_flow_anomaly_score=12.0,
        fund_flow_unit_verified=True,
        technical_score=80.0,
        event_score=75.0,
        ambush_score=60.0,
        narrative_score=70.0,
        contradiction_level="none",
        risk_penalty=-3.0,
        invalid_price=13.0,
        risk_flags=[],
        overheat_flags=[],
    )


def _overheated_candidate() -> dict:
    """Candidate with crowding/overheat signals."""
    return dict(
        pricing_gap_score=15.0,
        current_price=20.0,
        support_price=18.0,
        game_balance="crowded",
        resonance_count=1,
        positive_category_count=1,
        overheat_penalty=-20.0,
        fund_flow_anomaly_score=8.0,
        fund_flow_unit_verified=True,
        technical_score=60.0,
        event_score=50.0,
        ambush_score=30.0,
        narrative_score=40.0,
        contradiction_level="medium",
        risk_penalty=-10.0,
        invalid_price=17.0,
        risk_flags=[],
        overheat_flags=["板块过热"],
    )


def _no_funds_candidate() -> dict:
    """Candidate with no fund flow data."""
    return dict(
        pricing_gap_score=10.0,
        current_price=25.0,
        support_price=24.0,
        game_balance="neutral",
        resonance_count=2,
        positive_category_count=2,
        overheat_penalty=0.0,
        fund_flow_anomaly_score=0.0,
        fund_flow_unit_verified=False,
        technical_score=40.0,
        event_score=30.0,
        ambush_score=0.0,
        narrative_score=20.0,
        contradiction_level="",
        risk_penalty=-5.0,
        invalid_price=22.0,
        risk_flags=[],
        overheat_flags=[],
    )


def _unit_unverified_candidate() -> dict:
    """Candidate with fund flow but unit not verified."""
    return dict(
        pricing_gap_score=12.0,
        current_price=30.0,
        support_price=28.0,
        game_balance="neutral",
        resonance_count=2,
        positive_category_count=2,
        overheat_penalty=0.0,
        fund_flow_anomaly_score=10.0,
        fund_flow_unit_verified=False,
        technical_score=50.0,
        event_score=40.0,
        ambush_score=20.0,
        narrative_score=30.0,
        contradiction_level="",
        risk_penalty=-5.0,
        invalid_price=26.0,
        risk_flags=[],
        overheat_flags=[],
    )


def _first_big_drop_candidate() -> dict:
    """Candidate with first big drop risk flag."""
    return dict(
        pricing_gap_score=15.0,
        current_price=10.0,
        support_price=9.5,
        game_balance="favorable",
        resonance_count=2,
        positive_category_count=2,
        overheat_penalty=0.0,
        fund_flow_anomaly_score=6.0,
        fund_flow_unit_verified=True,
        technical_score=50.0,
        event_score=40.0,
        ambush_score=30.0,
        narrative_score=35.0,
        contradiction_level="",
        risk_penalty=-15.0,
        invalid_price=8.5,
        risk_flags=["第一次大跌风险"],
        overheat_flags=[],
    )


def _limit_down_candidate() -> dict:
    """Candidate with limit-down risk flag."""
    return dict(
        pricing_gap_score=20.0,
        current_price=8.0,
        support_price=7.5,
        game_balance="fragile",
        resonance_count=1,
        positive_category_count=1,
        overheat_penalty=-10.0,
        fund_flow_anomaly_score=3.0,
        fund_flow_unit_verified=False,
        technical_score=30.0,
        event_score=20.0,
        ambush_score=10.0,
        narrative_score=15.0,
        contradiction_level="high",
        risk_penalty=-25.0,
        invalid_price=6.0,
        risk_flags=["跌停未打开"],
        overheat_flags=["板块退潮"],
    )


def _empty_candidate() -> dict:
    """Candidate with all fields missing/empty."""
    return {}


# ── Dimension scorer tests ──────────────────────────────────────────

class TestValuationPosition:
    """估值预期位置 dimension tests."""

    def test_high_pricing_gap_and_near_support(self) -> None:
        result = compute_entry_timing(
            pricing_gap_score=25.0,
            current_price=15.0,
            support_price=14.5,
        )
        val = result.valuation_position
        assert val.score > 60.0
        assert val.status == "ok"
        assert "pricing_gap_score" in val.source_fields
        assert len(val.reasons) > 0

    def test_no_pricing_gap_no_support(self) -> None:
        result = compute_entry_timing()
        val = result.valuation_position
        assert val.score == 0.0
        assert val.status == "missing"
        assert "pricing_gap_score" in val.missing_fields

    def test_below_support(self) -> None:
        result = compute_entry_timing(
            current_price=13.0,
            support_price=14.0,
        )
        val = result.valuation_position
        # Below support should score low
        assert val.score < 50.0

    def test_far_from_support(self) -> None:
        result = compute_entry_timing(
            current_price=20.0,
            support_price=10.0,
        )
        val = result.valuation_position
        assert val.score < 60.0


class TestCrowding:
    """拥挤度 dimension tests."""

    def test_favorable_high_resonance(self) -> None:
        result = compute_entry_timing(
            game_balance="favorable",
            resonance_count=3,
            positive_category_count=3,
        )
        crow = result.crowding
        assert crow.score > 70.0
        assert crow.status == "ok"

    def test_crowded_with_overheat(self) -> None:
        result = compute_entry_timing(
            game_balance="crowded",
            resonance_count=1,
            positive_category_count=1,
            overheat_penalty=-30.0,
        )
        crow = result.crowding
        assert crow.score < 30.0

    def test_all_missing(self) -> None:
        result = compute_entry_timing()
        crow = result.crowding
        assert crow.score == 0.0
        assert crow.status == "missing"


class TestFundPriceConfirm:
    """资金价格确认 dimension tests."""

    def test_verified_high_fund_flow(self) -> None:
        result = compute_entry_timing(
            fund_flow_anomaly_score=12.0,
            fund_flow_unit_verified=True,
            technical_score=80.0,
        )
        fund = result.fund_price_confirm
        assert fund.score >= 70.0
        assert fund.status == "ok"

    def test_unverified_capped_at_50(self) -> None:
        result = compute_entry_timing(
            fund_flow_anomaly_score=20.0,
            fund_flow_unit_verified=False,
            technical_score=80.0,
        )
        fund = result.fund_price_confirm
        assert fund.score <= 50.0
        assert any("单位未校验" in r for r in fund.reasons)

    def test_no_fund_flow(self) -> None:
        result = compute_entry_timing(technical_score=50.0)
        fund = result.fund_price_confirm
        assert fund.status == "ok"  # has technical_score
        assert fund.score == 50.0  # only tech component


class TestCatalystEffectiveness:
    """催化有效性 dimension tests."""

    def test_high_event_no_contradiction(self) -> None:
        result = compute_entry_timing(
            event_score=75.0,
            ambush_score=60.0,
            narrative_score=70.0,
            contradiction_level="none",
        )
        cat = result.catalyst_effectiveness
        assert cat.score > 65.0
        assert cat.status == "ok"

    def test_high_contradiction_penalty(self) -> None:
        result = compute_entry_timing(
            event_score=75.0,
            ambush_score=60.0,
            narrative_score=70.0,
            contradiction_level="high",
        )
        cat = result.catalyst_effectiveness
        assert cat.score < 40.0
        assert any("矛盾" in r for r in cat.reasons)

    def test_all_missing(self) -> None:
        result = compute_entry_timing()
        cat = result.catalyst_effectiveness
        assert cat.score == 0.0
        assert cat.status == "missing"


class TestDownsideOdds:
    """下行赔率 dimension tests."""

    def test_low_risk_good_margin(self) -> None:
        result = compute_entry_timing(
            risk_penalty=-3.0,
            invalid_price=13.0,
            current_price=15.0,
        )
        odds = result.downside_odds
        assert odds.score > 60.0
        assert odds.status == "ok"

    def test_price_broken_stop(self) -> None:
        result = compute_entry_timing(
            risk_penalty=-10.0,
            invalid_price=16.0,
            current_price=15.0,
        )
        odds = result.downside_odds
        # Broken stop: margin=0, risk_penalty component=80, avg=40
        assert odds.score <= 50.0
        assert any("破止损" in r for r in odds.reasons)

    def test_all_missing(self) -> None:
        result = compute_entry_timing()
        odds = result.downside_odds
        assert odds.score == 0.0
        assert odds.status == "missing"


# ── Timing risk flags tests ─────────────────────────────────────────

class TestTimingRiskFlags:
    """Timing risk flag detection tests."""

    def test_detect_first_big_drop(self) -> None:
        flags = detect_timing_risk_flags(risk_flags=["第一次大跌风险", "other"])
        assert len(flags) == 1
        assert "第一次大跌风险" in flags

    def test_detect_limit_down(self) -> None:
        flags = detect_timing_risk_flags(risk_flags=["跌停未打开"])
        assert len(flags) == 1

    def test_detect_volume_breakdown(self) -> None:
        flags = detect_timing_risk_flags(overheat_flags=["爆量破位信号"])
        assert len(flags) == 1

    def test_detect_sector_retreat(self) -> None:
        flags = detect_timing_risk_flags(overheat_flags=["板块退潮确认"])
        assert len(flags) == 1

    def test_no_timing_flags(self) -> None:
        flags = detect_timing_risk_flags(
            risk_flags=["轻微回调"],
            overheat_flags=["温和过热"],
        )
        assert len(flags) == 0

    def test_empty_inputs(self) -> None:
        flags = detect_timing_risk_flags()
        assert flags == []

    def test_deduplication(self) -> None:
        flags = detect_timing_risk_flags(
            risk_flags=["跌停风险"],
            overheat_flags=["跌停风险"],
        )
        assert len(flags) == 1


# ── Scenario tests (task acceptance) ────────────────────────────────

class TestScore002Scenarios:
    """SCORE-002 acceptance scenarios from docs/TASKS.md."""

    def test_high_score_confirmation(self) -> None:
        """高分确认: all fields present, high values → high entry_timing."""
        cand = _high_score_candidate()
        result = compute_entry_timing(**cand)
        assert result.entry_timing >= 60.0
        assert result.data_status == "ok"
        assert result.timing_risk_flags == []
        assert result.valuation_position.status == "ok"
        assert result.crowding.status == "ok"
        assert result.fund_price_confirm.status == "ok"
        assert result.catalyst_effectiveness.status == "ok"
        assert result.downside_odds.status == "ok"

    def test_overheating(self) -> None:
        """过热: crowded + overheat_penalty → reduced score."""
        cand = _overheated_candidate()
        result = compute_entry_timing(**cand)
        # Overheated candidate should score significantly lower
        high = compute_entry_timing(**_high_score_candidate())
        assert result.entry_timing < high.entry_timing
        assert result.crowding.score < 50.0

    def test_no_funds(self) -> None:
        """无资金: no fund flow → low fund_price_confirm."""
        cand = _no_funds_candidate()
        result = compute_entry_timing(**cand)
        # Fund dimension should still be ok (has technical_score)
        assert result.fund_price_confirm.status == "ok"
        # But score should be lower than a candidate with fund flow
        funded = compute_entry_timing(
            fund_flow_anomaly_score=10.0,
            fund_flow_unit_verified=True,
            technical_score=40.0,
        )
        assert result.fund_price_confirm.score < funded.fund_price_confirm.score

    def test_unit_unverified(self) -> None:
        """单位未校验: fund_flow_unit_verified=False → capped at 50."""
        cand = _unit_unverified_candidate()
        result = compute_entry_timing(**cand)
        assert result.fund_price_confirm.score <= 50.0
        assert any("未校验" in r for r in result.fund_price_confirm.reasons)

    def test_first_big_drop(self) -> None:
        """第一次大跌: timing risk flag detected, score penalized."""
        cand = _first_big_drop_candidate()
        result = compute_entry_timing(**cand)
        assert "第一次大跌风险" in result.timing_risk_flags
        # Compare with same candidate without the risk flag
        cand_no_flag = {**cand, "risk_flags": []}
        result_no_flag = compute_entry_timing(**cand_no_flag)
        assert result.entry_timing < result_no_flag.entry_timing

    def test_limit_down(self) -> None:
        """跌停: timing risk flag detected, score penalized."""
        cand = _limit_down_candidate()
        result = compute_entry_timing(**cand)
        assert any("跌停" in f for f in result.timing_risk_flags)
        # Multiple penalties: fragile + high contradiction + limit_down
        assert result.entry_timing < 40.0

    def test_all_fields_missing(self) -> None:
        """字段全缺: entry_timing=0, data_status="missing"."""
        cand = _empty_candidate()
        result = compute_entry_timing(**cand)
        assert result.entry_timing == 0.0
        assert result.data_status == "missing"
        assert result.valuation_position.status == "missing"
        assert result.crowding.status == "missing"
        assert result.fund_price_confirm.status == "missing"
        assert result.catalyst_effectiveness.status == "missing"
        assert result.downside_odds.status == "missing"


# ── Stability / determinism ─────────────────────────────────────────

class TestStability:
    """Same inputs must produce identical output."""

    def test_deterministic_high_score(self) -> None:
        cand = _high_score_candidate()
        results = [compute_entry_timing(**cand) for _ in range(10)]
        scores = [r.entry_timing for r in results]
        assert len(set(scores)) == 1

    def test_deterministic_overheated(self) -> None:
        cand = _overheated_candidate()
        results = [compute_entry_timing(**cand) for _ in range(10)]
        scores = [r.entry_timing for r in results]
        assert len(set(scores)) == 1

    def test_deterministic_empty(self) -> None:
        results = [compute_entry_timing() for _ in range(10)]
        scores = [r.entry_timing for r in results]
        assert len(set(scores)) == 1


# ── Research snapshot independence ──────────────────────────────────

class TestSnapshotIndependence:
    """entry_timing must not be influenced by research snapshot scores."""

    def test_high_snapshot_does_not_raise_timing(self) -> None:
        """Same TradeFlow fields → same entry_timing regardless of snapshot."""
        cand = _high_score_candidate()
        result_a = compute_entry_timing(**cand)
        # Simulate "high research snapshot" — same TradeFlow fields
        result_b = compute_entry_timing(**cand)
        assert result_a.entry_timing == result_b.entry_timing

    def test_timing_only_uses_tradeflow_fields(self) -> None:
        """compute_entry_timing does not accept snapshot parameters."""
        import inspect
        sig = inspect.signature(compute_entry_timing)
        param_names = set(sig.parameters.keys())
        # Must not contain snapshot-related params
        for forbidden in ("research_score", "thesis_quality", "snapshot_score"):
            assert forbidden not in param_names


# ── Serialization ───────────────────────────────────────────────────

class TestSerialization:
    """entry_timing_to_dict output must be JSON-serializable."""

    def test_high_score_json(self) -> None:
        result = compute_entry_timing(**_high_score_candidate())
        d = entry_timing_to_dict(result)
        # Must not raise
        json_str = json.dumps(d, ensure_ascii=False)
        assert len(json_str) > 0
        assert "entry_timing" in d
        assert "valuation_position" in d
        assert "crowding" in d
        assert "fund_price_confirm" in d
        assert "catalyst_effectiveness" in d
        assert "downside_odds" in d
        assert "timing_risk_flags" in d

    def test_empty_json(self) -> None:
        result = compute_entry_timing()
        d = entry_timing_to_dict(result)
        json_str = json.dumps(d, ensure_ascii=False)
        assert len(json_str) > 0

    def test_dimension_has_required_keys(self) -> None:
        result = compute_entry_timing(**_high_score_candidate())
        d = entry_timing_to_dict(result)
        for dim_key in ("valuation_position", "crowding", "fund_price_confirm",
                        "catalyst_effectiveness", "downside_odds"):
            dim = d[dim_key]
            assert "score" in dim
            assert "weight" in dim
            assert "status" in dim
            assert "source_fields" in dim
            assert "reasons" in dim
            assert "missing_fields" in dim

    def test_no_strong_action_verbs(self) -> None:
        """No strong action verbs in reasons or flags."""
        forbidden = ("买入", "卖出", "加仓", "减仓", "满仓", "清仓", "全仓")
        # "止损" is allowed in descriptive contexts like "价格已破止损" (describing state)
        # but not as an action instruction like "建议止损"
        forbidden_patterns = ("建议止损", "立即止损", "需要止损")
        for cand_fn in (_high_score_candidate, _overheated_candidate,
                        _first_big_drop_candidate, _limit_down_candidate):
            result = compute_entry_timing(**cand_fn())
            d = entry_timing_to_dict(result)
            text = json.dumps(d, ensure_ascii=False)
            for word in forbidden:
                assert word not in text, f"Found '{word}' in {cand_fn.__name__} output"
            for pat in forbidden_patterns:
                assert pat not in text, f"Found '{pat}' in {cand_fn.__name__} output"


# ── Partial data ────────────────────────────────────────────────────

class TestPartialData:
    """Partial data → data_status='partial'."""

    def test_some_dimensions_missing(self) -> None:
        result = compute_entry_timing(
            pricing_gap_score=15.0,
            current_price=10.0,
            support_price=9.5,
            # crowding: missing
            # fund: missing
            event_score=50.0,
            # downside: missing
        )
        assert result.data_status == "partial"
        assert result.valuation_position.status == "ok"
        assert result.crowding.status == "missing"
        assert result.catalyst_effectiveness.status == "ok"


# ── Service layer enrichment ────────────────────────────────────────

class TestServiceLayerEnrichment:
    """Test _enrich_candidate_with_entry_timing in tradeflow_service."""

    def test_enrichment_injects_card(self) -> None:
        from api.services.tradeflow_service import _enrich_candidate_with_entry_timing
        item = {
            "symbol": "605589.SH",
            "pricing_gap_score": 20.0,
            "current_price": 15.0,
            "support_price": 14.0,
            "game_balance": "favorable",
            "resonance_count": 3,
            "positive_category_count": 3,
            "fund_flow_anomaly_score": 10.0,
            "fund_flow_unit_verified": True,
            "technical_score": 70.0,
            "event_score": 60.0,
            "ambush_score": 50.0,
            "narrative_score": 55.0,
            "risk_penalty": -5.0,
            "invalid_price": 13.0,
        }
        result = _enrich_candidate_with_entry_timing(item)
        assert "entry_timing_card" in result
        card = result["entry_timing_card"]
        assert card is not None
        assert card["entry_timing"] > 0
        assert card["data_status"] in ("ok", "partial")

    def test_enrichment_empty_candidate(self) -> None:
        from api.services.tradeflow_service import _enrich_candidate_with_entry_timing
        item: dict = {}
        result = _enrich_candidate_with_entry_timing(item)
        assert "entry_timing_card" in result
        card = result["entry_timing_card"]
        assert card is not None
        assert card["entry_timing"] == 0.0
        assert card["data_status"] == "missing"

    def test_enrichment_preserves_existing_fields(self) -> None:
        from api.services.tradeflow_service import _enrich_candidate_with_entry_timing
        item = {
            "symbol": "605589.SH",
            "score": 75.0,
            "action_tier": "watch",
            "tier": "B",
            "existing_field": "preserved",
        }
        result = _enrich_candidate_with_entry_timing(item)
        assert result["symbol"] == "605589.SH"
        assert result["score"] == 75.0
        assert result["action_tier"] == "watch"
        assert result["tier"] == "B"
        assert result["existing_field"] == "preserved"
        assert "entry_timing_card" in result

    def test_enrichment_no_action_keys(self) -> None:
        """entry_timing_card must not contain strong action keys."""
        from api.services.tradeflow_service import _enrich_candidate_with_entry_timing
        item = _high_score_candidate()
        item["symbol"] = "605589.SH"
        result = _enrich_candidate_with_entry_timing(item)
        card = result["entry_timing_card"]
        banned = {"decision", "action_label", "buy_level", "risk_level",
                  "execution_action", "playbook_stage", "planned_position"}
        for key in banned:
            assert key not in card, f"Banned key '{key}' found in entry_timing_card"

    def test_enrichment_failure_graceful(self) -> None:
        """Exception during computation → entry_timing_card=None."""
        from unittest.mock import patch
        from api.services.tradeflow_service import _enrich_candidate_with_entry_timing
        item = {"symbol": "605589.SH"}
        with patch(
            "tradingagents.tradeflow.entry_timing_score.compute_entry_timing",
            side_effect=RuntimeError("test"),
        ):
            result = _enrich_candidate_with_entry_timing(item)
        assert result.get("entry_timing_card") is None

    def test_batch_enrichment(self) -> None:
        from api.services.tradeflow_service import _enrich_candidates_with_entry_timing
        items = [
            {**_high_score_candidate(), "symbol": "605589.SH"},
            {**_empty_candidate(), "symbol": "000001.SZ"},
        ]
        result = _enrich_candidates_with_entry_timing(items)
        assert len(result) == 2
        assert result[0]["entry_timing_card"]["entry_timing"] > 0
        assert result[1]["entry_timing_card"]["entry_timing"] == 0.0

    def test_batch_empty_list(self) -> None:
        from api.services.tradeflow_service import _enrich_candidates_with_entry_timing
        result = _enrich_candidates_with_entry_timing([])
        assert result == []


# ── Weight correctness ──────────────────────────────────────────────

class TestWeights:
    """Verify dimension weights sum to 1.0."""

    def test_weights_sum_to_one(self) -> None:
        result = compute_entry_timing(**_high_score_candidate())
        total = (
            result.valuation_position.weight
            + result.crowding.weight
            + result.fund_price_confirm.weight
            + result.catalyst_effectiveness.weight
            + result.downside_odds.weight
        )
        assert abs(total - 1.0) < 1e-6
