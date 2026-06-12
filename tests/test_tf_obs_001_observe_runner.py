"""Tests for TF-OBS-001: TradeFlow Observe Runner and Signal Persistence."""

import sys
import os
import json
import tempfile
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.tradeflow.observe_runner import (
    ObserveRunResult,
    run_observe,
    _is_trading_day,
    _fetch_realtime_quotes,
    _load_active_candidates,
    _save_signal,
    _build_signal_from_snapshot,
)
from tradingagents.tradeflow.intraday_observe import (
    ObserveState,
    ObserveSnapshot,
    ObserveTracker,
    run_observe_check,
)
from tradingagents.tradeflow.schemas import Candidate, Signal
from tradingagents.tradeflow.candidate_engine import init_db, save_candidate
from tradingagents.tradeflow.strategy_config import StrategyConfig


@pytest.fixture
def tmp_db(tmp_path):
    db_path = str(tmp_path / "test_tradeflow.db")
    init_db(db_path)
    return db_path


def _make_candidate(symbol, trigger_price=10.0, invalid_price=9.0, trade_date="2026-06-01",
                     observe_state="WAITING", effective_trade_date=""):
    c = Candidate(
        symbol=symbol,
        name=f"Test {symbol}",
        source="manual",
        trade_date=trade_date,
        trigger_price=trigger_price,
        invalid_price=invalid_price,
        status="active",
        observe_state=observe_state,
        effective_trade_date=effective_trade_date or trade_date,
    )
    return c


def _save_candidates(db_path, candidates):
    for c in candidates:
        save_candidate(c, db_path)


class TestObserveRunResult:
    def test_defaults(self):
        r = ObserveRunResult()
        assert r.checked == 0
        assert r.triggered == 0
        assert r.invalidated == 0
        assert r.waiting == 0
        assert r.skipped == 0
        assert r.signals_written == 0
        assert r.errors == []
        assert r.skipped_reason == ""
        assert r.run_time != ""
        assert r.details == []

    def test_custom(self):
        r = ObserveRunResult(checked=5, triggered=2, run_time="2026-06-01T10:00:00")
        assert r.checked == 5
        assert r.triggered == 2


class TestIsTradingDay:
    def test_weekday(self):
        assert _is_trading_day("2026-06-01") is True  # Monday

    def test_saturday(self):
        assert _is_trading_day("2026-05-30") is False  # Saturday

    def test_sunday(self):
        assert _is_trading_day("2026-05-31") is False  # Sunday


class TestFetchRealtimeQuotes:
    def test_empty_symbols(self):
        assert _fetch_realtime_quotes([]) == {}

    def test_mock_provider(self):
        def mock_provider(symbols):
            return {s: {"current_price": 10.5, "current_volume": 1000, "current_amount": 10500, "quote_time": "10:00", "source": "mock"} for s in symbols}
        result = mock_provider(["600519.SH"])
        assert result["600519.SH"]["current_price"] == 10.5


class TestLoadActiveCandidates:
    def test_no_candidates(self, tmp_db):
        candidates = _load_active_candidates(tmp_db, "2026-06-01")
        assert candidates == []

    def test_with_candidates(self, tmp_db):
        c = _make_candidate("600519.SH", effective_trade_date="2026-06-01")
        _save_candidates(tmp_db, [c])
        candidates = _load_active_candidates(tmp_db, "2026-06-01")
        assert len(candidates) == 1
        assert candidates[0].symbol == "600519.SH"

    def test_inactive_filtered(self, tmp_db):
        c = _make_candidate("600519.SH")
        c.status = "inactive"
        _save_candidates(tmp_db, [c])
        candidates = _load_active_candidates(tmp_db, "2026-06-01")
        assert len(candidates) == 0

    def test_effective_trade_date_lookup(self, tmp_db):
        c = _make_candidate("600519.SH", trade_date="2026-05-31", effective_trade_date="2026-06-01")
        _save_candidates(tmp_db, [c])
        candidates = _load_active_candidates(tmp_db, "2026-06-01")
        assert len(candidates) == 1

    def test_fallback_uses_latest_active_plan_on_or_before_observe_date(self, tmp_db):
        older = _make_candidate("600519.SH", trade_date="2026-05-31", effective_trade_date="2026-06-01")
        future = _make_candidate("000001.SZ", trade_date="2026-06-03", effective_trade_date="2026-06-03")
        _save_candidates(tmp_db, [older, future])

        candidates = _load_active_candidates(tmp_db, "2026-06-02")

        assert [c.symbol for c in candidates] == ["600519.SH"]

    def test_fallback_does_not_load_future_plan_for_historical_observe_date(self, tmp_db):
        future = _make_candidate("000001.SZ", trade_date="2026-06-03", effective_trade_date="2026-06-03")
        _save_candidates(tmp_db, [future])

        candidates = _load_active_candidates(tmp_db, "2026-06-02")

        assert candidates == []

    def test_fallback_does_not_load_stale_plan_after_current_filtered_run(self, tmp_db):
        older = _make_candidate("600519.SH", trade_date="2026-05-31", effective_trade_date="2026-06-01")
        current_filtered = _make_candidate("000001.SZ", trade_date="2026-06-02", effective_trade_date="2026-06-02")
        current_filtered.status = "filtered"
        _save_candidates(tmp_db, [older, current_filtered])

        candidates = _load_active_candidates(tmp_db, "2026-06-02")

        assert candidates == []

    def test_fallback_does_not_load_stale_old_plan(self, tmp_db):
        stale = _make_candidate("600519.SH", trade_date="2026-05-01", effective_trade_date="2026-05-01")
        _save_candidates(tmp_db, [stale])

        candidates = _load_active_candidates(tmp_db, "2026-06-02")

        assert candidates == []


class TestSaveSignal:
    def test_basic(self, tmp_db):
        signal = Signal(
            symbol="600519.SH",
            signal_type="observe_triggered",
            signal_level="warning",
            source="observe_runner",
            evidence={"current_price": 10.5},
        )
        sid = _save_signal(signal, tmp_db)
        assert sid > 0

    def test_persisted(self, tmp_db):
        import sqlite3
        signal = Signal(
            symbol="600519.SH",
            signal_type="observe_check",
            signal_level="info",
            source="observe_runner",
            evidence={"current_price": 9.5},
        )
        _save_signal(signal, tmp_db)
        conn = sqlite3.connect(tmp_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM tradeflow_signals WHERE symbol = '600519.SH'").fetchone()
        conn.close()
        assert row is not None
        assert row["signal_type"] == "observe_check"
        ev = json.loads(row["evidence_json"])
        assert ev["current_price"] == 9.5


class TestBuildSignalFromSnapshot:
    def test_triggered(self):
        snap = ObserveSnapshot(
            symbol="600519.SH",
            observe_state=ObserveState.TRIGGERED,
            current_price=10.5,
            trigger_price=10.0,
            trigger_reason="突破触发价",
        )
        signal = _build_signal_from_snapshot(snap, "2026-06-01", "mock")
        assert signal.signal_type == "observe_triggered"
        assert signal.signal_level == "warning"
        assert signal.evidence["current_price"] == 10.5
        assert signal.evidence["trigger_reason"] == "突破触发价"

    def test_invalidated(self):
        snap = ObserveSnapshot(
            symbol="600519.SH",
            observe_state=ObserveState.INVALIDATED,
            current_price=8.5,
            invalid_price=9.0,
            trigger_reason="跌破失效价",
        )
        signal = _build_signal_from_snapshot(snap, "2026-06-01")
        assert signal.signal_type == "observe_invalidated"
        assert signal.signal_level == "error"

    def test_waiting(self):
        snap = ObserveSnapshot(
            symbol="600519.SH",
            observe_state=ObserveState.WAITING,
            current_price=9.5,
            trigger_price=10.0,
        )
        signal = _build_signal_from_snapshot(snap, "2026-06-01")
        assert signal.signal_type == "observe_check"
        assert signal.signal_level == "info"

    def test_expired(self):
        snap = ObserveSnapshot(
            symbol="600519.SH",
            observe_state=ObserveState.EXPIRED,
            trigger_reason="收盘到期",
        )
        signal = _build_signal_from_snapshot(snap, "2026-06-01")
        assert signal.signal_type == "observe_expired"
        assert signal.signal_level == "info"


class TestRunObserveTriggered:
    def test_price_above_trigger(self, tmp_db):
        c = _make_candidate("600519.SH", trigger_price=10.0, invalid_price=9.0)
        _save_candidates(tmp_db, [c])

        def mock_quotes(symbols):
            return {s: {"current_price": 10.1, "current_volume": 1000, "current_amount": 10100, "quote_time": "10:00", "source": "mock"} for s in symbols}

        result = run_observe("2026-06-01", tmp_db, quote_provider=mock_quotes)
        assert result.checked == 1
        assert result.triggered == 1
        assert result.signals_written == 1
        assert result.errors == []

        import sqlite3
        conn = sqlite3.connect(tmp_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT observe_state, observe_trigger_count FROM tradeflow_candidates WHERE symbol = '600519.SH'").fetchone()
        assert row["observe_state"] == "TRIGGERED"
        assert row["observe_trigger_count"] == 1

        sig = conn.execute("SELECT * FROM tradeflow_signals WHERE symbol = '600519.SH'").fetchone()
        assert sig is not None
        assert sig["signal_type"] == "observe_triggered"
        ev = json.loads(sig["evidence_json"])
        assert ev["current_price"] == 10.1
        conn.close()


class TestRunObserveInvalidated:
    def test_price_below_invalid(self, tmp_db):
        c = _make_candidate("600519.SH", trigger_price=10.0, invalid_price=9.0)
        _save_candidates(tmp_db, [c])

        def mock_quotes(symbols):
            return {s: {"current_price": 8.5, "current_volume": 1000, "current_amount": 8500, "quote_time": "10:00", "source": "mock"} for s in symbols}

        result = run_observe("2026-06-01", tmp_db, quote_provider=mock_quotes)
        assert result.checked == 1
        assert result.invalidated == 1
        assert result.signals_written == 1

        import sqlite3
        conn = sqlite3.connect(tmp_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT observe_state FROM tradeflow_candidates WHERE symbol = '600519.SH'").fetchone()
        assert row["observe_state"] == "INVALIDATED"
        conn.close()


class TestRunObserveWaiting:
    def test_price_between_trigger_and_invalid(self, tmp_db):
        c = _make_candidate("600519.SH", trigger_price=10.0, invalid_price=9.0)
        _save_candidates(tmp_db, [c])

        def mock_quotes(symbols):
            return {s: {"current_price": 9.5, "current_volume": 500, "current_amount": 4750, "quote_time": "10:00", "source": "mock"} for s in symbols}

        result = run_observe("2026-06-01", tmp_db, quote_provider=mock_quotes)
        assert result.checked == 1
        assert result.waiting == 1
        assert result.triggered == 0
        assert result.invalidated == 0


class TestRunObserveNoQuotes:
    def test_no_price_skipped(self, tmp_db):
        c = _make_candidate("600519.SH", trigger_price=10.0, invalid_price=9.0)
        _save_candidates(tmp_db, [c])

        def mock_quotes(symbols):
            return {}

        result = run_observe("2026-06-01", tmp_db, quote_provider=mock_quotes)
        assert result.skipped == 1
        assert result.checked == 0
        assert result.signals_written == 0


class TestRunObserveNonTradingDay:
    def test_saturday_skip(self, tmp_db):
        c = _make_candidate("600519.SH", trade_date="2026-05-30")
        _save_candidates(tmp_db, [c])

        result = run_observe("2026-05-30", tmp_db)
        assert result.skipped_reason != ""
        assert "非交易日" in result.skipped_reason
        assert result.checked == 0

    def test_sunday_skip(self, tmp_db):
        result = run_observe("2026-05-31", tmp_db)
        assert result.skipped_reason != ""
        assert "非交易日" in result.skipped_reason


class TestRunObserveNoCandidates:
    def test_empty_db(self, tmp_db):
        result = run_observe("2026-06-01", tmp_db)
        assert result.skipped_reason != ""
        assert "无活跃候选" in result.skipped_reason
        assert result.checked == 0


class TestRunObserveMultipleSymbols:
    def test_three_symbols(self, tmp_db):
        c1 = _make_candidate("600519.SH", trigger_price=10.0, invalid_price=9.0)
        c2 = _make_candidate("000858.SZ", trigger_price=20.0, invalid_price=18.0)
        c3 = _make_candidate("002353.SZ", trigger_price=15.0, invalid_price=14.0)
        _save_candidates(tmp_db, [c1, c2, c3])

        def mock_quotes(symbols):
            prices = {"600519.SH": 10.5, "000858.SZ": 17.0, "002353.SZ": 15.2}
            return {s: {"current_price": prices.get(s, 0), "current_volume": 1000, "current_amount": 10000, "quote_time": "10:00", "source": "mock"} for s in symbols}

        result = run_observe("2026-06-01", tmp_db, quote_provider=mock_quotes)
        assert result.checked == 3
        assert result.triggered == 2  # 600519 (10.5>10) + 002353 (15.2>15)
        assert result.invalidated == 1  # 000858 (17.0<18)
        assert result.signals_written == 3


class TestRunObservePreservesTriggerCount:
    def test_incremental(self, tmp_db):
        c = _make_candidate("600519.SH", trigger_price=10.0, invalid_price=9.0, observe_state="WAITING")
        _save_candidates(tmp_db, [c])

        def mock_quotes(symbols):
            return {s: {"current_price": 10.1, "current_volume": 1000, "current_amount": 10100, "quote_time": "10:00", "source": "mock"} for s in symbols}

        result1 = run_observe("2026-06-01", tmp_db, quote_provider=mock_quotes)
        assert result1.triggered == 1

        import sqlite3
        conn = sqlite3.connect(tmp_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT observe_trigger_count, observe_first_trigger_time FROM tradeflow_candidates WHERE symbol = '600519.SH'").fetchone()
        assert row["observe_trigger_count"] == 1
        assert row["observe_first_trigger_time"] != ""
        conn.close()


class TestRunObserveTerminalStatePreserved:
    def test_triggered_stays(self, tmp_db):
        c = _make_candidate("600519.SH", trigger_price=10.0, invalid_price=9.0, observe_state="TRIGGERED")
        _save_candidates(tmp_db, [c])

        def mock_quotes(symbols):
            return {s: {"current_price": 8.0, "current_volume": 1000, "current_amount": 8000, "quote_time": "10:00", "source": "mock"} for s in symbols}

        result = run_observe("2026-06-01", tmp_db, quote_provider=mock_quotes)
        assert result.checked == 1
        assert result.details[0]["observe_state"] == "TRIGGERED"

        import sqlite3
        conn = sqlite3.connect(tmp_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT observe_state FROM tradeflow_candidates WHERE symbol = '600519.SH'").fetchone()
        assert row["observe_state"] == "TRIGGERED"
        conn.close()

    def test_invalidated_stays(self, tmp_db):
        c = _make_candidate("600519.SH", trigger_price=10.0, invalid_price=9.0, observe_state="INVALIDATED")
        _save_candidates(tmp_db, [c])

        def mock_quotes(symbols):
            return {s: {"current_price": 11.0, "current_volume": 1000, "current_amount": 11000, "quote_time": "10:00", "source": "mock"} for s in symbols}

        result = run_observe("2026-06-01", tmp_db, quote_provider=mock_quotes)
        assert result.checked == 1
        assert result.details[0]["observe_state"] == "INVALIDATED"


class TestRunObserveEffectiveTradeDate:
    def test_cross_date_lookup(self, tmp_db):
        c = _make_candidate("600519.SH", trade_date="2026-05-31", effective_trade_date="2026-06-01")
        _save_candidates(tmp_db, [c])

        def mock_quotes(symbols):
            return {s: {"current_price": 10.5, "current_volume": 1000, "current_amount": 10500, "quote_time": "10:00", "source": "mock"} for s in symbols}

        result = run_observe("2026-06-01", tmp_db, quote_provider=mock_quotes)
        assert result.checked == 1
        assert result.triggered == 1


class TestSignalEvidenceCompleteness:
    def test_signal_has_all_fields(self, tmp_db):
        c = _make_candidate("600519.SH", trigger_price=10.0, invalid_price=9.0)
        _save_candidates(tmp_db, [c])

        def mock_quotes(symbols):
            return {s: {"current_price": 10.1, "current_volume": 1000, "current_amount": 10100, "quote_time": "10:00:00", "source": "mock_src"} for s in symbols}

        run_observe("2026-06-01", tmp_db, quote_provider=mock_quotes)

        import sqlite3
        conn = sqlite3.connect(tmp_db)
        conn.row_factory = sqlite3.Row
        sig = conn.execute("SELECT * FROM tradeflow_signals WHERE symbol = '600519.SH'").fetchone()
        ev = json.loads(sig["evidence_json"])
        assert "trade_date" in ev
        assert "check_time" in ev
        assert "observe_state" in ev
        assert "trigger_price" in ev
        assert "invalid_price" in ev
        assert "current_price" in ev
        assert "current_volume" in ev
        assert "current_amount" in ev
        assert "trigger_reason" in ev
        assert "daily_trigger_count" in ev
        assert "price_evidence" in ev
        assert ev["quote_source"] == "mock_src"
        conn.close()


class TestRunObserveWithExactTriggerPrice:
    def test_exact_trigger(self, tmp_db):
        c = _make_candidate("600519.SH", trigger_price=10.0, invalid_price=9.0)
        _save_candidates(tmp_db, [c])

        def mock_quotes(symbols):
            return {s: {"current_price": 10.0, "current_volume": 1000, "current_amount": 10000, "quote_time": "10:00", "source": "mock"} for s in symbols}

        result = run_observe("2026-06-01", tmp_db, quote_provider=mock_quotes)
        assert result.triggered == 1


class TestRunObserveWithExactInvalidPrice:
    def test_exact_invalid(self, tmp_db):
        c = _make_candidate("600519.SH", trigger_price=10.0, invalid_price=9.0)
        _save_candidates(tmp_db, [c])

        def mock_quotes(symbols):
            return {s: {"current_price": 9.0, "current_volume": 1000, "current_amount": 9000, "quote_time": "10:00", "source": "mock"} for s in symbols}

        result = run_observe("2026-06-01", tmp_db, quote_provider=mock_quotes)
        assert result.invalidated == 1


class TestRunObserveNoTriggerPrice:
    def test_no_trigger_stays_waiting(self, tmp_db):
        c = _make_candidate("600519.SH", trigger_price=None, invalid_price=None)
        _save_candidates(tmp_db, [c])

        def mock_quotes(symbols):
            return {s: {"current_price": 15.0, "current_volume": 1000, "current_amount": 15000, "quote_time": "10:00", "source": "mock"} for s in symbols}

        result = run_observe("2026-06-01", tmp_db, quote_provider=mock_quotes)
        assert result.checked == 1
        assert result.waiting == 1


class TestRunObserveDetails:
    def test_details_populated(self, tmp_db):
        c = _make_candidate("600519.SH", trigger_price=10.0, invalid_price=9.0)
        _save_candidates(tmp_db, [c])

        def mock_quotes(symbols):
            return {s: {"current_price": 10.1, "current_volume": 1000, "current_amount": 10100, "quote_time": "10:00", "source": "mock"} for s in symbols}

        result = run_observe("2026-06-01", tmp_db, quote_provider=mock_quotes)
        assert len(result.details) == 1
        d = result.details[0]
        assert d["symbol"] == "600519.SH"
        assert d["observe_state"] == "TRIGGERED"
        assert d["current_price"] == 10.1
        assert "突破" in d["trigger_reason"]

    def test_skipped_detail(self, tmp_db):
        c = _make_candidate("600519.SH", trigger_price=10.0, invalid_price=9.0)
        _save_candidates(tmp_db, [c])

        def mock_quotes(symbols):
            return {}

        result = run_observe("2026-06-01", tmp_db, quote_provider=mock_quotes)
        assert len(result.details) == 1
        assert result.details[0]["status"] == "skipped_no_price"
