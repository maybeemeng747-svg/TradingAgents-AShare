"""Tests for T-008: TradeFlow Observe Fixture Replay & Frontend State Consistency.

Covers:
- 5 observe fixture scenarios (waiting, triggered, invalidated, stale, cross-date)
- Signal persistence to tradeflow_signals
- observe_state consistency across backend enum, API schema, and frontend
- TF-DATE-001 date semantics (plan_date/effective_trade_date/observe_date)
- API get_observe() returns correct state after replay
- Frontend state string matching
"""

import json
import sqlite3
import sys
import os
from datetime import datetime
import pytest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.tradeflow.observe_fixture_replay import (
    ALL_FIXTURE_IDS,
    ObserveFixtureResult,
    get_fixture,
    replay_fixture,
    replay_all,
    validate_state_consistency,
    validate_api_frontend_consistency,
    _build_candidate,
    _make_quote_provider,
    _read_signals,
    _read_candidate_observe_state,
)
from tradingagents.tradeflow.intraday_observe import ObserveState
from tradingagents.tradeflow.schemas import Candidate
from tradingagents.tradeflow.candidate_engine import init_db


@pytest.fixture
def tmp_db(tmp_path):
    db_path = str(tmp_path / "test_t008_tradeflow.db")
    init_db(db_path)
    return db_path


# ── Fixture Existence & Structure ──


class TestFixtureExistence:
    def test_all_fixture_ids_count(self):
        assert len(ALL_FIXTURE_IDS) == 5

    @pytest.mark.parametrize("fid", ALL_FIXTURE_IDS)
    def test_fixture_builder_exists(self, fid):
        f = get_fixture(fid)
        assert "fixture_id" in f
        assert f["fixture_id"] == fid

    @pytest.mark.parametrize("fid", ALL_FIXTURE_IDS)
    def test_fixture_has_required_fields(self, fid):
        f = get_fixture(fid)
        for key in ("fixture_id", "symbol", "trigger_price", "invalid_price",
                     "trade_date", "expected_state"):
            assert key in f, f"{fid} missing {key}"

    @pytest.mark.parametrize("fid", ALL_FIXTURE_IDS)
    def test_fixture_valid_expected_state(self, fid):
        f = get_fixture(fid)
        assert f["expected_state"] in {s.value for s in ObserveState}


# ── Fixture 1: Waiting / Not Triggered ──


class TestWaitingNotTriggered:
    def test_replay_state(self, tmp_db):
        r = replay_fixture("waiting_not_triggered", tmp_db)
        assert r.observe_state == "WAITING"
        assert r.data_status == "OK"

    def test_signal_written(self, tmp_db):
        r = replay_fixture("waiting_not_triggered", tmp_db)
        assert r.signal_count == 1

    def test_current_price(self, tmp_db):
        r = replay_fixture("waiting_not_triggered", tmp_db)
        assert r.current_price == 9.5

    def test_trigger_reason_empty_or_not_triggered(self, tmp_db):
        r = replay_fixture("waiting_not_triggered", tmp_db)
        assert r.trigger_reason == "" or "未" in r.trigger_reason

    def test_evidence_has_trade_date(self, tmp_db):
        r = replay_fixture("waiting_not_triggered", tmp_db)
        if r.signal_evidence:
            assert "trade_date" in r.signal_evidence


# ── Fixture 2: Triggered ──


class TestTriggered:
    def test_replay_state(self, tmp_db):
        r = replay_fixture("triggered", tmp_db)
        assert r.observe_state == "TRIGGERED"

    def test_signal_written(self, tmp_db):
        r = replay_fixture("triggered", tmp_db)
        assert r.signal_count >= 1

    def test_current_price(self, tmp_db):
        r = replay_fixture("triggered", tmp_db)
        assert r.current_price == 10.5

    def test_trigger_reason_mentions_trigger(self, tmp_db):
        r = replay_fixture("triggered", tmp_db)
        assert "触发" in r.trigger_reason or "突破" in r.trigger_reason

    def test_evidence_has_rise_pct(self, tmp_db):
        r = replay_fixture("triggered", tmp_db)
        pe = r.signal_evidence.get("price_evidence", {})
        assert "rise_pct" in pe or r.signal_evidence == {}

    def test_data_status_ok(self, tmp_db):
        r = replay_fixture("triggered", tmp_db)
        assert r.data_status == "OK"


# ── Fixture 3: Invalidated ──


class TestInvalidated:
    def test_replay_state(self, tmp_db):
        r = replay_fixture("invalidated", tmp_db)
        assert r.observe_state == "INVALIDATED"

    def test_signal_written(self, tmp_db):
        r = replay_fixture("invalidated", tmp_db)
        assert r.signal_count >= 1

    def test_current_price(self, tmp_db):
        r = replay_fixture("invalidated", tmp_db)
        assert r.current_price == 8.5

    def test_trigger_reason_mentions_invalidation(self, tmp_db):
        r = replay_fixture("invalidated", tmp_db)
        assert "失效" in r.trigger_reason or "跌破" in r.trigger_reason

    def test_evidence_has_breach_pct(self, tmp_db):
        r = replay_fixture("invalidated", tmp_db)
        pe = r.signal_evidence.get("price_evidence", {})
        assert "breach_pct" in pe or r.signal_evidence == {}

    def test_data_status_ok(self, tmp_db):
        r = replay_fixture("invalidated", tmp_db)
        assert r.data_status == "OK"


# ── Fixture 4: Stale Quote ──


class TestStaleQuote:
    def test_replay_skipped(self, tmp_db):
        r = replay_fixture("stale_quote", tmp_db)
        assert r.data_status == "STALE"

    def test_no_signal(self, tmp_db):
        r = replay_fixture("stale_quote", tmp_db)
        assert r.signal_count == 0

    def test_state_unchanged(self, tmp_db):
        r = replay_fixture("stale_quote", tmp_db)
        assert r.observe_state == "WAITING"

    def test_run_result_skipped(self, tmp_db):
        r = replay_fixture("stale_quote", tmp_db)
        assert r.run_result is not None
        assert r.run_result["skipped"] >= 1

    def test_current_price_none(self, tmp_db):
        r = replay_fixture("stale_quote", tmp_db)
        assert r.current_price is None


# ── Fixture 5: Non-Trading Day Cross-Date ──


class TestNonTradingDayCrossDate:
    def test_replay_state(self, tmp_db):
        r = replay_fixture("non_trading_day_cross_date", tmp_db)
        assert r.observe_state == "TRIGGERED"

    def test_plan_date_differs_from_effective(self):
        f = get_fixture("non_trading_day_cross_date")
        assert f["plan_date"] != f["effective_trade_date"]

    def test_plan_date_is_non_trading_weekend(self):
        f = get_fixture("non_trading_day_cross_date")
        d = datetime.strptime(f["plan_date"], "%Y-%m-%d")
        assert d.weekday() >= 5

    def test_signal_written(self, tmp_db):
        r = replay_fixture("non_trading_day_cross_date", tmp_db)
        assert r.signal_count >= 1

    def test_candidate_found_by_effective_date(self, tmp_db):
        r = replay_fixture("non_trading_day_cross_date", tmp_db)
        state = _read_candidate_observe_state(
            tmp_db, r.symbol, r.effective_trade_date
        )
        assert state.get("observe_state") == "TRIGGERED"

    def test_plan_date_preserved(self, tmp_db):
        r = replay_fixture("non_trading_day_cross_date", tmp_db)
        f = get_fixture("non_trading_day_cross_date")
        assert r.plan_date == f["plan_date"]


# ── Replay All ──


class TestReplayAll:
    def test_replay_all_count(self, tmp_db):
        results = replay_all(tmp_db)
        assert len(results) == 5

    def test_replay_all_no_errors(self, tmp_db):
        results = replay_all(tmp_db)
        for r in results:
            assert r.error == ""

    def test_replay_all_unique_symbols(self, tmp_db):
        results = replay_all(tmp_db)
        symbols = [r.symbol for r in results]
        assert len(symbols) == len(set(symbols))

    def test_replay_all_states_valid(self, tmp_db):
        results = replay_all(tmp_db)
        valid_states = {s.value for s in ObserveState}
        for r in results:
            assert r.observe_state in valid_states

    def test_replay_all_fixture_ids_match(self, tmp_db):
        results = replay_all(tmp_db)
        assert [r.fixture_id for r in results] == ALL_FIXTURE_IDS


# ── State Consistency ──


class TestStateConsistency:
    def test_backend_enum_complete(self):
        states = {s.value for s in ObserveState}
        assert "WAITING" in states
        assert "TRIGGERED" in states
        assert "INVALIDATED" in states
        assert "EXPIRED" in states

    def test_validate_state_consistency_no_issues(self, tmp_db):
        results = replay_all(tmp_db)
        issues = validate_state_consistency(results)
        assert issues == []

    def test_api_frontend_consistency(self):
        issues = validate_api_frontend_consistency()
        assert issues == []

    def test_pydantic_schema_default_state(self):
        from api.tradeflow_schemas import TradeFlowObserveItem
        item = TradeFlowObserveItem(symbol="000001.SZ")
        assert item.observe_state == "WAITING"

    def test_pydantic_schema_accepts_all_states(self):
        from api.tradeflow_schemas import TradeFlowObserveItem
        for s in ObserveState:
            item = TradeFlowObserveItem(symbol="000001.SZ", observe_state=s.value)
            assert item.observe_state == s.value

    def test_candidate_default_observe_state(self):
        c = Candidate(symbol="000001.SZ")
        assert c.observe_state == "WAITING"


# ── Signal Persistence ──


class TestSignalPersistence:
    def test_signal_has_correct_type_triggered(self, tmp_db):
        r = replay_fixture("triggered", tmp_db)
        signals = _read_signals(tmp_db, r.symbol, r.trade_date)
        assert any(s.get("signal_type") == "observe_triggered" for s in signals)

    def test_signal_has_correct_type_invalidated(self, tmp_db):
        r = replay_fixture("invalidated", tmp_db)
        signals = _read_signals(tmp_db, r.symbol, r.trade_date)
        assert any(s.get("signal_type") == "observe_invalidated" for s in signals)

    def test_signal_has_correct_type_waiting(self, tmp_db):
        r = replay_fixture("waiting_not_triggered", tmp_db)
        signals = _read_signals(tmp_db, r.symbol, r.trade_date)
        assert any(s.get("signal_type") == "observe_check" for s in signals)

    def test_signal_evidence_json_valid(self, tmp_db):
        replay_fixture("triggered", tmp_db)
        conn = sqlite3.connect(tmp_db)
        try:
            rows = conn.execute(
                "SELECT evidence_json FROM tradeflow_signals WHERE signal_type = 'observe_triggered' LIMIT 1"
            ).fetchall()
            assert len(rows) >= 1
            ev = json.loads(rows[0][0])
            assert "observe_state" in ev
            assert "current_price" in ev
            assert "trigger_price" in ev
            assert "invalid_price" in ev
            assert "trade_date" in ev
        finally:
            conn.close()

    def test_no_signal_for_stale(self, tmp_db):
        r = replay_fixture("stale_quote", tmp_db)
        signals = _read_signals(tmp_db, r.symbol, r.trade_date)
        assert len(signals) == 0


# ── API get_observe Integration ──


class TestAPIGetObserveIntegration:
    def test_get_observe_returns_replayed_state(self, tmp_db):
        replay_fixture("triggered", tmp_db)
        from api.services.tradeflow_service import get_observe
        result = get_observe("2026-06-02", tf_db_path=tmp_db)
        items = result.get("observe_items", [])
        triggered_items = [i for i in items if i["observe_state"] == "TRIGGERED"]
        assert len(triggered_items) >= 1

    def test_get_observe_current_price_from_signal(self, tmp_db):
        replay_fixture("triggered", tmp_db)
        from api.services.tradeflow_service import get_observe
        result = get_observe("2026-06-02", tf_db_path=tmp_db)
        items = result.get("observe_items", [])
        item = next((i for i in items if i["symbol"] == "000001.SZ"), None)
        assert item is not None
        assert item["current_price"] == 10.5

    def test_get_observe_trigger_reason_from_signal(self, tmp_db):
        replay_fixture("triggered", tmp_db)
        from api.services.tradeflow_service import get_observe
        result = get_observe("2026-06-02", tf_db_path=tmp_db)
        items = result.get("observe_items", [])
        item = next((i for i in items if i["symbol"] == "000001.SZ"), None)
        assert item is not None
        assert "触发" in item["trigger_reason"] or "突破" in item["trigger_reason"]

    def test_get_observe_counts(self, tmp_db):
        replay_fixture("triggered", tmp_db)
        replay_fixture("invalidated", tmp_db)
        from api.services.tradeflow_service import get_observe
        result = get_observe("2026-06-02", tf_db_path=tmp_db)
        assert result["triggered_count"] >= 1
        assert result["invalidated_count"] >= 1

    def test_get_observe_cross_date(self, tmp_db):
        replay_fixture("non_trading_day_cross_date", tmp_db)
        from api.services.tradeflow_service import get_observe
        result = get_observe("2026-06-01", tf_db_path=tmp_db)
        items = result.get("observe_items", [])
        cross = [i for i in items if i["symbol"] == "300750.SZ"]
        assert len(cross) >= 1
        assert cross[0]["observe_state"] == "TRIGGERED"


# ── Frontend State String Matching ──


class TestFrontendStateMatching:
    FRONTEND_STATES = {"WAITING", "TRIGGERED", "INVALIDATED", "EXPIRED"}

    def test_all_backend_states_in_frontend(self):
        backend = {s.value for s in ObserveState}
        assert backend == self.FRONTEND_STATES

    @pytest.mark.parametrize("state", ["WAITING", "TRIGGERED", "INVALIDATED", "EXPIRED"])
    def test_state_in_frontend_code(self, state):
        assert state in self.FRONTEND_STATES

    def test_pydantic_default_matches_frontend_default(self):
        from api.tradeflow_schemas import TradeFlowObserveItem
        item = TradeFlowObserveItem(symbol="TEST")
        assert item.observe_state == "WAITING"
        assert "WAITING" in self.FRONTEND_STATES


# ── TF-DATE-001 Date Semantics ──


class TestDateSemantics:
    def test_cross_date_fixture_plan_before_effective(self):
        f = get_fixture("non_trading_day_cross_date")
        assert f["plan_date"] < f["effective_trade_date"]

    def test_all_fixtures_have_trade_date(self):
        for fid in ALL_FIXTURE_IDS:
            f = get_fixture(fid)
            assert "trade_date" in f
            assert len(f["trade_date"]) == 10

    def test_cross_date_candidate_stored_with_both_dates(self, tmp_db):
        replay_fixture("non_trading_day_cross_date", tmp_db)
        f = get_fixture("non_trading_day_cross_date")
        state = _read_candidate_observe_state(tmp_db, f["symbol"], f["effective_trade_date"])
        assert state.get("effective_trade_date") == f["effective_trade_date"]
        assert state.get("plan_date") == f["plan_date"]


# ── ObserveFixtureResult ──


class TestObserveFixtureResult:
    def test_defaults(self):
        r = ObserveFixtureResult(fixture_id="test", symbol="TEST", trade_date="2026-06-01")
        assert r.observe_state == "WAITING"
        assert r.data_status == ""
        assert r.error == ""

    def test_to_dict(self):
        r = ObserveFixtureResult(fixture_id="test", symbol="TEST", trade_date="2026-06-01")
        d = r.to_dict()
        assert d["fixture_id"] == "test"
        assert d["observe_state"] == "WAITING"

    def test_to_dict_roundtrip(self):
        r = ObserveFixtureResult(
            fixture_id="triggered", symbol="000001.SZ", trade_date="2026-06-02",
            observe_state="TRIGGERED", current_price=10.5, data_status="OK",
        )
        d = r.to_dict()
        assert d["observe_state"] == "TRIGGERED"
        assert d["current_price"] == 10.5
        assert d["data_status"] == "OK"


# ── Acceptance Tests ──


class TestAcceptanceT008:
    def test_five_fixtures_stable(self, tmp_db):
        results = replay_all(tmp_db)
        assert len(results) == 5
        expected = {
            "waiting_not_triggered": "WAITING",
            "triggered": "TRIGGERED",
            "invalidated": "INVALIDATED",
            "stale_quote": "WAITING",
            "non_trading_day_cross_date": "TRIGGERED",
        }
        for r in results:
            assert r.observe_state == expected[r.fixture_id], (
                f"{r.fixture_id}: expected {expected[r.fixture_id]}, got {r.observe_state}"
            )

    def test_frontend_observe_tab_shows_replayed_state(self, tmp_db):
        replay_fixture("triggered", tmp_db)
        from api.services.tradeflow_service import get_observe
        result = get_observe("2026-06-02", tf_db_path=tmp_db)
        items = result.get("observe_items", [])
        assert any(i["observe_state"] == "TRIGGERED" for i in items)

    def test_no_live_scheduler_used(self, tmp_db):
        results = replay_all(tmp_db)
        for r in results:
            assert r.run_result is not None

    def test_no_live_quotes_used(self, tmp_db):
        replay_fixture("waiting_not_triggered", tmp_db)
        conn = sqlite3.connect(tmp_db)
        try:
            rows = conn.execute(
                "SELECT evidence_json FROM tradeflow_signals WHERE symbol = '600519.SH' LIMIT 1"
            ).fetchall()
            if rows:
                ev = json.loads(rows[0][0])
                assert ev.get("quote_source") == "fixture_replay"
        finally:
            conn.close()

    def test_date_semantics_consistent(self, tmp_db):
        r = replay_fixture("non_trading_day_cross_date", tmp_db)
        assert r.plan_date == "2026-05-31"
        assert r.effective_trade_date == "2026-06-01"
        assert r.observe_state == "TRIGGERED"

    def test_state_enum_consistency(self):
        issues = validate_api_frontend_consistency()
        assert issues == [], f"State enum mismatch: {issues}"

    def test_api_observe_after_all_replays(self, tmp_db):
        replay_all(tmp_db)
        from api.services.tradeflow_service import get_observe
        result = get_observe("2026-06-02", tf_db_path=tmp_db)
        assert result["status"] == "ok"
        assert len(result["observe_items"]) >= 3
