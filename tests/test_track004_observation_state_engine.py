# [TRACK-004] observation_state_engine
"""Tests for TRACK-004: 观察仓入场区/失效区规则引擎与状态流转.

Covers:
- Pure state engine ``evaluate_observation_state``:
  - data_missing (trading day, no live price)
  - needs_review (non-trading day, no live price)
  - invalidated (live <= invalid_price)
  - in_entry_zone (entry_low <= live <= entry_high)
  - near_entry (within 5% below entry_low)
  - missed_entry (live > entry_high * 1.05)
  - ta_required (stored status, no higher price state)
  - watching (default, with/without zone)
- Constraint: data missing NEVER judged as entry-able.
- Constraint: no forbidden strong buy/sell wording in any reason.
- Every result carries ``reason`` and ``data_fields``.
- ``should_emit_guidance`` and ``result_to_guidance`` shaping.
- Integration: ``_aggregate_today_guidance`` uses the engine (missed_entry,
  dual ta_required emission, watching suppression for zone-less items).
- ``missed_entry`` is now an accepted stored status in tradeflow_service.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.tradeflow.observation_state_engine import (
    STATE_DATA_MISSING,
    STATE_NEEDS_REVIEW,
    STATE_WATCHING,
    STATE_NEAR_ENTRY,
    STATE_IN_ENTRY_ZONE,
    STATE_MISSED_ENTRY,
    STATE_INVALIDATED,
    STATE_TA_REQUIRED,
    FORBIDDEN_STRONG_WORDS,
    evaluate_observation_state,
    result_to_guidance,
    should_emit_guidance,
)
from api.services.tradeflow_service import (
    ALLOWED_OBSERVATION_STATUSES,
    OBSERVATION_STATUS_MISSED_ENTRY,
    _validate_observation_status,
)
from api.services.tracking_board_service import _aggregate_today_guidance


def _now():
    from datetime import datetime
    return datetime.now()


def _item(**overrides):
    base = {
        "symbol": "000001.SZ",
        "name": "平安银行",
        "status": "watching",
        "entry_low": 10.0,
        "entry_high": 12.0,
        "trigger_price": 0.0,
        "invalid_price": 9.0,
        "live_price": 11.0,
        "quote_time": "10:00:00",
        "quote_source": "tencent",
    }
    base.update(overrides)
    return base


# ── evaluate_observation_state: core state coverage ──────────────────────

class TestEvaluateState:

    def test_in_entry_zone(self):
        r = evaluate_observation_state(_item(live_price=11.0), is_trading_day=True)
        assert r["state"] == STATE_IN_ENTRY_ZONE
        assert r["priority"] == "P0"
        assert r["guidance_type"] == "observation_in_entry_zone"
        assert "entry_low" in r["data_fields"] and "entry_high" in r["data_fields"]

    def test_in_entry_zone_at_lower_bound(self):
        r = evaluate_observation_state(_item(live_price=10.0), is_trading_day=True)
        assert r["state"] == STATE_IN_ENTRY_ZONE

    def test_in_entry_zone_at_upper_bound(self):
        r = evaluate_observation_state(_item(live_price=12.0), is_trading_day=True)
        assert r["state"] == STATE_IN_ENTRY_ZONE

    def test_near_entry_within_5pct_below(self):
        # entry_low=10, 9.6 is within 5% below (>= 9.5)
        r = evaluate_observation_state(_item(live_price=9.6), is_trading_day=True)
        assert r["state"] == STATE_NEAR_ENTRY
        assert r["priority"] == "P1"

    def test_near_entry_boundary_inclusive(self):
        # exactly 9.5 = entry_low * 0.95 -> near_entry (inclusive lower bound)
        r = evaluate_observation_state(_item(live_price=9.5), is_trading_day=True)
        assert r["state"] == STATE_NEAR_ENTRY

    def test_below_near_band_is_watching(self):
        # 9.4 < 9.5 -> not near, not invalidated (invalid=9.0) -> watching
        r = evaluate_observation_state(_item(live_price=9.4), is_trading_day=True)
        assert r["state"] == STATE_WATCHING

    def test_invalidated_at_invalid_price(self):
        r = evaluate_observation_state(_item(live_price=9.0), is_trading_day=True)
        assert r["state"] == STATE_INVALIDATED
        assert r["priority"] == "P1"
        assert r["data_fields"]["invalid_price"] == 9.0

    def test_invalidated_below_invalid_price(self):
        r = evaluate_observation_state(_item(live_price=8.5), is_trading_day=True)
        assert r["state"] == STATE_INVALIDATED

    def test_missed_entry_above_upper_band(self):
        # entry_high=12, 12.7 > 12 * 1.05 = 12.6 -> missed_entry
        r = evaluate_observation_state(_item(live_price=12.7), is_trading_day=True)
        assert r["state"] == STATE_MISSED_ENTRY
        assert r["priority"] == "P2"
        assert r["guidance_type"] == "observation_missed_entry"

    def test_missed_entry_boundary_exclusive(self):
        # exactly 12.6 == entry_high * 1.05 -> NOT missed (> is strict); falls to watching
        r = evaluate_observation_state(_item(live_price=12.6), is_trading_day=True)
        assert r["state"] == STATE_WATCHING

    def test_data_missing_trading_day(self):
        r = evaluate_observation_state(_item(live_price=None), is_trading_day=True)
        assert r["state"] == STATE_DATA_MISSING
        assert r["priority"] == "P3"
        assert r["guidance_type"] == "observation_data_missing"

    def test_needs_review_non_trading_day_no_price(self):
        r = evaluate_observation_state(_item(live_price=None), is_trading_day=False)
        assert r["state"] == STATE_NEEDS_REVIEW
        assert r["guidance_type"] == "observation_needs_review"

    def test_ta_required_when_no_price_state(self):
        # zone defined but price far above (missed) would normally be missed_entry;
        # here we use no zone + stored ta_required
        r = evaluate_observation_state(
            _item(entry_low=0.0, entry_high=0.0, invalid_price=0.0,
                  status="ta_required", live_price=15.0),
            is_trading_day=True,
        )
        assert r["state"] == STATE_TA_REQUIRED
        assert r["priority"] == "P1"

    def test_in_zone_beats_ta_required_stored(self):
        # stored ta_required but price in zone -> in_entry_zone wins as primary
        r = evaluate_observation_state(
            _item(status="ta_required", live_price=11.0),
            is_trading_day=True,
        )
        assert r["state"] == STATE_IN_ENTRY_ZONE

    def test_watching_with_zone_far_below(self):
        r = evaluate_observation_state(_item(live_price=9.3), is_trading_day=True)
        assert r["state"] == STATE_WATCHING
        # data_fields records whether a zone was configured
        assert r["data_fields"]["has_zone"] is True

    def test_watching_without_zone(self):
        r = evaluate_observation_state(
            _item(entry_low=0.0, entry_high=0.0, invalid_price=0.0, live_price=15.0),
            is_trading_day=True,
        )
        assert r["state"] == STATE_WATCHING
        assert r["data_fields"]["has_zone"] is False

    def test_invalidated_beats_in_zone(self):
        # invalid_price inside the zone; live <= invalid -> invalidated first
        r = evaluate_observation_state(
            _item(entry_low=10.0, entry_high=12.0, invalid_price=10.5, live_price=10.5),
            is_trading_day=True,
        )
        assert r["state"] == STATE_INVALIDATED

    def test_result_always_carries_reason_and_data_fields(self):
        for live in (None, 8.0, 9.4, 9.6, 10.0, 11.0, 12.7, 15.0):
            r = evaluate_observation_state(_item(live_price=live), is_trading_day=True)
            assert "reason" in r and isinstance(r["reason"], str) and r["reason"]
            assert "data_fields" in r and isinstance(r["data_fields"], dict)


# ── Constraint: data missing never entry-able ────────────────────────────

class TestDataMissingSafety:

    def test_no_live_price_never_entry_able_with_zone(self):
        r = evaluate_observation_state(_item(live_price=None), is_trading_day=True)
        assert r["state"] not in {STATE_IN_ENTRY_ZONE, STATE_NEAR_ENTRY, STATE_MISSED_ENTRY}

    def test_no_live_price_never_entry_able_non_trading(self):
        r = evaluate_observation_state(_item(live_price=None), is_trading_day=False)
        assert r["state"] in {STATE_NEEDS_REVIEW, STATE_DATA_MISSING}

    def test_no_live_price_takes_precedence_over_ta_required(self):
        # Even if stored status is ta_required, no price -> data_missing (can't judge)
        r = evaluate_observation_state(
            _item(status="ta_required", live_price=None), is_trading_day=True,
        )
        assert r["state"] == STATE_DATA_MISSING


# ── Constraint: no forbidden strong wording ──────────────────────────────

class TestNoStrongActionWording:

    def test_engine_reasons_have_no_forbidden_words(self):
        scenarios = [
            _item(live_price=None),               # data_missing
            _item(live_price=9.0),                # invalidated
            _item(live_price=11.0),               # in_entry_zone
            _item(live_price=9.6),                # near_entry
            _item(live_price=12.7),               # missed_entry
            _item(entry_low=0.0, entry_high=0.0, invalid_price=0.0,
                  status="ta_required", live_price=15.0),  # ta_required
            _item(entry_low=0.0, entry_high=0.0, invalid_price=0.0,
                  live_price=15.0),               # watching no zone
        ]
        for item in scenarios:
            r = evaluate_observation_state(item, is_trading_day=True)
            for word in FORBIDDEN_STRONG_WORDS:
                assert word not in r["reason"], f"reason 含禁用词 {word}: {r['reason']}"

    def test_guidance_payload_has_no_forbidden_words(self):
        r = evaluate_observation_state(_item(live_price=11.0), is_trading_day=True)
        g = result_to_guidance(r, as_of="2026-06-23 10:00:00")
        for word in FORBIDDEN_STRONG_WORDS:
            assert word not in g["reason"]


# ── result_to_guidance / should_emit_guidance shaping ─────────────────────

class TestGuidanceShaping:

    def test_result_to_guidance_keys(self):
        r = evaluate_observation_state(_item(live_price=11.0), is_trading_day=True)
        g = result_to_guidance(r, as_of="2026-06-23 10:00:00")
        for key in ("type", "priority", "symbol", "name", "reason", "source", "as_of", "state", "data_fields"):
            assert key in g
        assert g["source"] == "tracking_board_v2"
        assert g["type"] == "observation_in_entry_zone"
        assert g["state"] == STATE_IN_ENTRY_ZONE

    def test_should_emit_for_non_watching(self):
        r = evaluate_observation_state(_item(live_price=11.0), is_trading_day=True)
        assert should_emit_guidance(r) is True

    def test_should_emit_watching_with_zone(self):
        r = evaluate_observation_state(_item(live_price=9.3), is_trading_day=True)
        assert r["state"] == STATE_WATCHING
        assert should_emit_guidance(r) is True  # has_zone True

    def test_should_not_emit_watching_without_zone(self):
        r = evaluate_observation_state(
            _item(entry_low=0.0, entry_high=0.0, invalid_price=0.0, live_price=15.0),
            is_trading_day=True,
        )
        assert r["state"] == STATE_WATCHING
        assert should_emit_guidance(r) is False  # has_zone False -> suppress noise


# ── Integration: _aggregate_today_guidance uses the engine ────────────────

class TestAggregateIntegration:

    def test_missed_entry_appears_in_guidance(self):
        obs = [_item(symbol="000001.SZ", live_price=12.7)]
        guidance = _aggregate_today_guidance([], obs, {}, True, _now())
        missed = [g for g in guidance if g["type"] == "observation_missed_entry"]
        assert len(missed) == 1
        assert missed[0]["priority"] == "P2"
        assert missed[0]["state"] == STATE_MISSED_ENTRY

    def test_dual_ta_required_when_in_zone_and_stored_ta_required(self):
        obs = [_item(symbol="000001.SZ", status="ta_required", live_price=11.0)]
        guidance = _aggregate_today_guidance([], obs, {}, True, _now())
        types = [g["type"] for g in guidance]
        assert "observation_in_entry_zone" in types
        assert "observation_ta_required" in types

    def test_no_dual_when_engine_returned_ta_required(self):
        # no zone + stored ta_required -> engine returns ta_required; no duplicate
        obs = [_item(symbol="000001.SZ", entry_low=0.0, entry_high=0.0,
                     invalid_price=0.0, status="ta_required", live_price=15.0)]
        guidance = _aggregate_today_guidance([], obs, {}, True, _now())
        ta = [g for g in guidance if g["type"] == "observation_ta_required"]
        assert len(ta) == 1

    def test_zone_less_watching_suppressed_from_guidance(self):
        obs = [_item(symbol="000001.SZ", entry_low=0.0, entry_high=0.0,
                     invalid_price=0.0, status="watching", live_price=15.0)]
        guidance = _aggregate_today_guidance([], obs, {}, True, _now())
        assert guidance == []

    def test_zone_watching_emitted_as_p3(self):
        # has zone but price far below near band -> watching P3 emitted
        obs = [_item(symbol="000001.SZ", live_price=9.3)]
        guidance = _aggregate_today_guidance([], obs, {}, True, _now())
        watching = [g for g in guidance if g["type"] == "observation_watching"]
        assert len(watching) == 1
        assert watching[0]["priority"] == "P3"

    def test_needs_review_emitted_on_non_trading_day(self):
        obs = [_item(symbol="000001.SZ", live_price=None)]
        guidance = _aggregate_today_guidance([], obs, {}, False, _now())
        review = [g for g in guidance if g["type"] == "observation_needs_review"]
        assert len(review) == 1
        # and no data_missing on non-trading day
        assert not any(g["type"] == "observation_data_missing" for g in guidance)

    def test_guidance_priority_sorting_with_new_states(self):
        obs = [
            _item(symbol="A", live_price=12.7),   # missed P2
            _item(symbol="B", live_price=11.0),   # in zone P0
            _item(symbol="C", live_price=9.6),    # near P1
            _item(symbol="D", live_price=9.3),    # watching P3
        ]
        guidance = _aggregate_today_guidance([], obs, {}, True, _now())
        priorities = [g["priority"] for g in guidance]
        assert priorities == sorted(priorities, key={"P0": 0, "P1": 1, "P2": 2, "P3": 3}.get)

    def test_every_guidance_has_source_reason_as_of_state(self):
        obs = [_item(symbol="000001.SZ", live_price=11.0)]
        for g in _aggregate_today_guidance([], obs, {}, True, _now()):
            assert g["source"] == "tracking_board_v2"
            assert g["as_of"] and g["reason"] and g["state"]
            assert "data_fields" in g


# ── missed_entry accepted as a stored status ──────────────────────────────

class TestMissedEntryStatus:

    def test_missed_entry_in_allowed_statuses(self):
        assert OBSERVATION_STATUS_MISSED_ENTRY == "missed_entry"
        assert "missed_entry" in ALLOWED_OBSERVATION_STATUSES

    def test_validate_missed_entry(self):
        assert _validate_observation_status("missed_entry") == "missed_entry"

    def test_validate_rejects_unknown(self):
        with pytest.raises(ValueError):
            _validate_observation_status("bogus_state")
