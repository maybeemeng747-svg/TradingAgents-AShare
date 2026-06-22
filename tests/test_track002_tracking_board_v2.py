# [TRACK-002] tracking_board_v2_groups
"""Tests for TRACK-002: 跟踪看板 v2 分组接口与今日指引聚合.

Covers:
- Empty state: no holdings, no observation items → empty arrays + stable structure.
- Holdings with analysis vs without analysis.
- Observation items near entry zone, in entry zone, invalidated.
- Today guidance priority sorting (P0 > P1 > P2 > P3).
- Guidance aggregation: holdings_risk, observation_in_entry_zone, observation_near_entry,
  observation_ta_required, holdings_no_analysis, observation_data_missing.
- Data freshness metadata.
- Alerts filter (P0 only).
- No real-time quotes → data_freshness stale/missing markers.
"""

from __future__ import annotations

import sys
import os

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Pure unit tests on the service helpers (no DB / no network)
# ---------------------------------------------------------------------------
from api.services.tracking_board_service import (
    _aggregate_today_guidance,
    _build_data_freshness,
    _enrich_observation_items,
)


class TestAggregateTodayGuidance:
    """Unit tests for _aggregate_today_guidance."""

    def test_empty_holdings_and_observation(self):
        guidance = _aggregate_today_guidance([], [], {}, True, _now())
        assert guidance == []

    def test_holdings_no_analysis(self):
        holdings = [{"symbol": "600000.SH", "name": "浦发银行", "price_change_pct": 0.5, "analysis": None}]
        guidance = _aggregate_today_guidance(holdings, [], {}, True, _now())
        assert len(guidance) == 1
        assert guidance[0]["type"] == "holdings_no_analysis"
        assert guidance[0]["priority"] == "P2"

    def test_holdings_risk_large_drop(self):
        holdings = [{"symbol": "600000.SH", "name": "浦发银行", "price_change_pct": -4.0,
                      "analysis": {"decision": "HOLD"}}]
        guidance = _aggregate_today_guidance(holdings, [], {}, True, _now())
        risk = [g for g in guidance if g["type"] == "holdings_risk"]
        assert len(risk) == 1
        assert risk[0]["priority"] == "P0"

    def test_holdings_risk_moderate_drop(self):
        holdings = [{"symbol": "600000.SH", "name": "浦发银行", "price_change_pct": -2.0,
                      "analysis": {"decision": "HOLD"}}]
        guidance = _aggregate_today_guidance(holdings, [], {}, True, _now())
        risk = [g for g in guidance if g["type"] == "holdings_risk"]
        assert len(risk) == 1
        assert risk[0]["priority"] == "P1"

    def test_observation_in_entry_zone(self):
        obs = [{"symbol": "000001.SZ", "name": "平安银行", "status": "watching",
                "entry_low": 10.0, "entry_high": 12.0, "invalid_price": 9.0,
                "live_price": 11.0}]
        guidance = _aggregate_today_guidance([], obs, {}, True, _now())
        assert any(g["type"] == "observation_in_entry_zone" for g in guidance)

    def test_observation_near_entry(self):
        obs = [{"symbol": "000001.SZ", "name": "平安银行", "status": "watching",
                "entry_low": 11.0, "entry_high": 12.0, "invalid_price": 9.0,
                "live_price": 10.5}]  # below entry_low but within 5%
        guidance = _aggregate_today_guidance([], obs, {}, True, _now())
        assert any(g["type"] == "observation_near_entry" for g in guidance)
        assert not any(g["type"] == "observation_in_entry_zone" for g in guidance)

    def test_observation_invalidated(self):
        obs = [{"symbol": "000001.SZ", "name": "平安银行", "status": "watching",
                "entry_low": 10.0, "entry_high": 12.0, "invalid_price": 9.5,
                "live_price": 9.0}]
        guidance = _aggregate_today_guidance([], obs, {}, True, _now())
        assert any(g["type"] == "observation_invalidated" for g in guidance)

    def test_observation_no_live_price_on_trading_day(self):
        obs = [{"symbol": "000001.SZ", "name": "平安银行", "status": "watching",
                "entry_low": 10.0, "entry_high": 12.0, "invalid_price": 9.0,
                "live_price": None}]
        guidance = _aggregate_today_guidance([], obs, {}, True, _now())
        assert any(g["type"] == "observation_data_missing" for g in guidance)

    def test_observation_no_live_price_non_trading_day(self):
        obs = [{"symbol": "000001.SZ", "name": "平安银行", "status": "watching",
                "entry_low": 10.0, "entry_high": 12.0, "invalid_price": 9.0,
                "live_price": None}]
        guidance = _aggregate_today_guidance([], obs, {}, False, _now())
        assert not any(g["type"] == "observation_data_missing" for g in guidance)

    def test_observation_ta_required(self):
        obs = [{"symbol": "000001.SZ", "name": "平安银行", "status": "ta_required",
                "entry_low": 0.0, "entry_high": 0.0, "invalid_price": 0.0,
                "live_price": 15.0}]
        guidance = _aggregate_today_guidance([], obs, {}, True, _now())
        assert any(g["type"] == "observation_ta_required" for g in guidance)

    def test_priority_sorting(self):
        holdings = [
            {"symbol": "A", "name": "A", "price_change_pct": 0.0, "analysis": None},  # P2
            {"symbol": "B", "name": "B", "price_change_pct": -4.0, "analysis": {}},  # P0
        ]
        guidance = _aggregate_today_guidance(holdings, [], {}, True, _now())
        priorities = [g["priority"] for g in guidance]
        assert priorities == sorted(priorities)

    def test_every_guidance_has_source_and_as_of(self):
        holdings = [{"symbol": "X", "name": "X", "price_change_pct": 0.0, "analysis": None}]
        obs = [{"symbol": "Y", "name": "Y", "status": "watching",
                "entry_low": 0.0, "entry_high": 0.0, "invalid_price": 0.0,
                "live_price": None}]
        guidance = _aggregate_today_guidance(holdings, obs, {}, True, _now())
        for g in guidance:
            assert g["source"] == "tracking_board_v2"
            assert "as_of" in g
            assert "reason" in g
            assert "symbol" in g


class TestBuildDataFreshness:
    """Unit tests for _build_data_freshness."""

    def test_empty_state(self):
        df = _build_data_freshness([], [], {}, True, _now())
        assert df["holdings_total"] == 0
        assert df["observation_total"] == 0
        assert df["status"] == "fresh"

    def test_stale_when_no_quotes_on_trading_day(self):
        holdings = [{"symbol": "A", "live_price": None}]
        df = _build_data_freshness(holdings, [], {}, True, _now())
        assert df["status"] == "stale"

    def test_fresh_with_quotes(self):
        holdings = [{"symbol": "A", "live_price": 10.0, "quote_time": "09:30:00"}]
        df = _build_data_freshness(holdings, [], {}, True, _now())
        assert df["status"] == "fresh"
        assert df["holdings_with_live_quotes"] == 1

    def test_non_trading_day(self):
        df = _build_data_freshness([{"symbol": "A", "live_price": None}], [], {}, False, _now())
        assert df["status"] == "non_trading_day"

    def test_latest_quote_time(self):
        holdings = [{"symbol": "A", "live_price": 10.0, "quote_time": "10:00:00"}]
        obs = [{"symbol": "B", "live_price": 5.0, "quote_time": "10:30:00"}]
        df = _build_data_freshness(holdings, obs, {}, True, _now())
        assert df["latest_quote_time"] == "10:30:00"


class TestEnrichObservationItems:
    """Unit tests for _enrich_observation_items."""

    def test_empty(self):
        result = _enrich_observation_items([], {})
        assert result == []

    def test_enriches_with_quote(self):
        items = [{"symbol": "000001.SZ", "name": "平安银行"}]
        quotes = {"000001.SZ": {"price": 12.5, "change_pct": 1.2, "quote_time": "10:00", "source": "tencent"}}
        result = _enrich_observation_items(items, quotes)
        assert len(result) == 1
        assert result[0]["live_price"] == 12.5
        assert result[0]["price_change_pct"] == 1.2

    def test_no_quote_returns_none_live_price(self):
        items = [{"symbol": "000001.SZ", "name": "平安银行"}]
        result = _enrich_observation_items(items, {})
        assert result[0]["live_price"] is None


def _now():
    from datetime import datetime
    return datetime.now()
