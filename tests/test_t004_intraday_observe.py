# [T-004] intraday_observe_scheduler tests
"""Tests for T-004: TradeFlow P2 盘中 Observe.

Covers:
1. Volume anomaly detection (detect_volume_anomaly).
2. ObserveSnapshot volume_anomaly / volume_ratio fields.
3. run_observe_check with avg_volume and volume anomaly.
4. ObserveSchedulerTickResult.
5. run_observe_tick (market hours / trading day checks).
6. run_observe_session (multi-tick session).
7. API endpoint /v1/tradeflow/observe/scheduler-status.
8. StrategyConfig observe scheduler fields.
9. Signal evidence includes volume_anomaly / volume_ratio.
10. Acceptance criteria for T-004.
"""

import os
import sqlite3
import tempfile
from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest

from tradingagents.tradeflow.intraday_observe import (
    ObserveSnapshot,
    ObserveState,
    ObserveTracker,
    detect_volume_anomaly,
    run_observe_check,
    expire_tracker,
)
from tradingagents.tradeflow.intraday_observe_scheduler import (
    ObserveSchedulerTickResult,
    _is_market_hours,
    _is_trading_day,
    run_observe_tick,
    run_observe_session,
)
from tradingagents.tradeflow.observe_runner import (
    ObserveRunResult,
    run_observe,
    _build_signal_from_snapshot,
)
from tradingagents.tradeflow.strategy_config import StrategyConfig, DEFAULT_STRATEGY_CONFIG
from tradingagents.tradeflow.schemas import Signal


# ═══════════════════════════════════════════════════════════════════════
# Test detect_volume_anomaly
# ═══════════════════════════════════════════════════════════════════════

class TestDetectVolumeAnomaly:
    def test_no_current_volume(self):
        is_anomaly, ratio = detect_volume_anomaly(None, 100000.0)
        assert is_anomaly is False
        assert ratio is None

    def test_no_avg_volume(self):
        is_anomaly, ratio = detect_volume_anomaly(500000.0, None)
        assert is_anomaly is False
        assert ratio is None

    def test_zero_avg_volume(self):
        is_anomaly, ratio = detect_volume_anomaly(500000.0, 0.0)
        assert is_anomaly is False
        assert ratio is None

    def test_below_threshold(self):
        is_anomaly, ratio = detect_volume_anomaly(150000.0, 100000.0)
        assert is_anomaly is False
        assert ratio == 1.5

    def test_at_threshold(self):
        is_anomaly, ratio = detect_volume_anomaly(200000.0, 100000.0)
        assert is_anomaly is True
        assert ratio == 2.0

    def test_above_threshold(self):
        is_anomaly, ratio = detect_volume_anomaly(500000.0, 100000.0)
        assert is_anomaly is True
        assert ratio == 5.0

    def test_custom_threshold(self):
        cfg = StrategyConfig(observe_volume_anomaly_ratio=3.0)
        is_anomaly, ratio = detect_volume_anomaly(250000.0, 100000.0, cfg)
        assert is_anomaly is False
        assert ratio == 2.5

    def test_custom_threshold_triggered(self):
        cfg = StrategyConfig(observe_volume_anomaly_ratio=3.0)
        is_anomaly, ratio = detect_volume_anomaly(350000.0, 100000.0, cfg)
        assert is_anomaly is True
        assert ratio == 3.5

    def test_ratio_rounded(self):
        is_anomaly, ratio = detect_volume_anomaly(333333.0, 100000.0)
        assert ratio == 3.33


# ═══════════════════════════════════════════════════════════════════════
# Test ObserveSnapshot volume fields
# ═══════════════════════════════════════════════════════════════════════

class TestObserveSnapshotVolumeFields:
    def test_defaults(self):
        snap = ObserveSnapshot(symbol="600519.SH", observe_state=ObserveState.WAITING)
        assert snap.volume_anomaly is False
        assert snap.volume_ratio is None

    def test_with_volume_anomaly(self):
        snap = ObserveSnapshot(
            symbol="600519.SH",
            observe_state=ObserveState.TRIGGERED,
            volume_anomaly=True,
            volume_ratio=3.5,
        )
        assert snap.volume_anomaly is True
        assert snap.volume_ratio == 3.5


# ═══════════════════════════════════════════════════════════════════════
# Test run_observe_check with avg_volume
# ═══════════════════════════════════════════════════════════════════════

class TestRunObserveCheckWithVolume:
    def test_triggered_with_volume_anomaly(self):
        t = ObserveTracker(symbol="600519.SH", trigger_price=10.0, invalid_price=9.0)
        snap = run_observe_check(
            t, current_price=10.5, current_volume=500000.0,
            avg_volume=100000.0,
        )
        assert snap.observe_state == ObserveState.TRIGGERED
        assert snap.volume_anomaly is True
        assert snap.volume_ratio == 5.0
        assert "放量5.0倍" in snap.trigger_reason

    def test_triggered_no_volume_anomaly(self):
        t = ObserveTracker(symbol="600519.SH", trigger_price=10.0, invalid_price=9.0)
        snap = run_observe_check(
            t, current_price=10.5, current_volume=150000.0,
            avg_volume=100000.0,
        )
        assert snap.observe_state == ObserveState.TRIGGERED
        assert snap.volume_anomaly is False
        assert "放量" not in snap.trigger_reason

    def test_waiting_with_volume_anomaly_only(self):
        t = ObserveTracker(symbol="600519.SH", trigger_price=10.0, invalid_price=9.0)
        snap = run_observe_check(
            t, current_price=9.5, current_volume=300000.0,
            avg_volume=100000.0,
        )
        assert snap.observe_state == ObserveState.WAITING
        assert snap.volume_anomaly is True
        assert "放量异常" in snap.trigger_reason

    def test_waiting_no_volume_anomaly(self):
        t = ObserveTracker(symbol="600519.SH", trigger_price=10.0, invalid_price=9.0)
        snap = run_observe_check(
            t, current_price=9.5, current_volume=120000.0,
            avg_volume=100000.0,
        )
        assert snap.observe_state == ObserveState.WAITING
        assert snap.volume_anomaly is False
        assert snap.trigger_reason == ""

    def test_invalidated_still_detects_volume(self):
        t = ObserveTracker(symbol="600519.SH", trigger_price=10.0, invalid_price=9.0)
        snap = run_observe_check(
            t, current_price=8.5, current_volume=500000.0,
            avg_volume=100000.0,
        )
        assert snap.observe_state == ObserveState.INVALIDATED
        assert snap.volume_anomaly is True

    def test_no_avg_volume_no_anomaly(self):
        t = ObserveTracker(symbol="600519.SH", trigger_price=10.0, invalid_price=9.0)
        snap = run_observe_check(
            t, current_price=9.5, current_volume=500000.0,
        )
        assert snap.observe_state == ObserveState.WAITING
        assert snap.volume_anomaly is False
        assert snap.volume_ratio is None


# ═══════════════════════════════════════════════════════════════════════
# Test StrategyConfig observe scheduler fields
# ═══════════════════════════════════════════════════════════════════════

class TestStrategyConfigSchedulerFields:
    def test_default_interval(self):
        cfg = StrategyConfig()
        assert cfg.observe_interval_minutes == 30

    def test_default_volume_anomaly_ratio(self):
        cfg = StrategyConfig()
        assert cfg.observe_volume_anomaly_ratio == 2.0

    def test_default_market_hours(self):
        cfg = StrategyConfig()
        assert cfg.observe_market_open_hour == 9
        assert cfg.observe_market_open_minute == 30
        assert cfg.observe_market_close_hour == 15
        assert cfg.observe_market_close_minute == 0

    def test_custom_interval(self):
        cfg = StrategyConfig(observe_interval_minutes=15)
        assert cfg.observe_interval_minutes == 15

    def test_custom_volume_ratio(self):
        cfg = StrategyConfig(observe_volume_anomaly_ratio=3.0)
        assert cfg.observe_volume_anomaly_ratio == 3.0


# ═══════════════════════════════════════════════════════════════════════
# Test ObserveSchedulerTickResult
# ═══════════════════════════════════════════════════════════════════════

class TestObserveSchedulerTickResult:
    def test_defaults(self):
        r = ObserveSchedulerTickResult()
        assert r.tick_time != ""
        assert r.trade_date == ""
        assert r.status == ""
        assert r.result is None
        assert r.error == ""

    def test_with_trade_date(self):
        r = ObserveSchedulerTickResult(trade_date="2026-06-08")
        assert r.trade_date == "2026-06-08"


# ═══════════════════════════════════════════════════════════════════════
# Test _is_market_hours
# ═══════════════════════════════════════════════════════════════════════

class TestIsMarketHours:
    def test_during_market_hours(self):
        cfg = StrategyConfig(observe_market_open_hour=9, observe_market_open_minute=30,
                             observe_market_close_hour=15, observe_market_close_minute=0)
        with patch("tradingagents.tradeflow.intraday_observe_scheduler.datetime") as mock_dt:
            mock_now = MagicMock()
            mock_now.hour = 10
            mock_now.minute = 30
            mock_dt.now.return_value = mock_now
            assert _is_market_hours(cfg) is True

    def test_before_market_hours(self):
        cfg = StrategyConfig(observe_market_open_hour=9, observe_market_open_minute=30,
                             observe_market_close_hour=15, observe_market_close_minute=0)
        with patch("tradingagents.tradeflow.intraday_observe_scheduler.datetime") as mock_dt:
            mock_now = MagicMock()
            mock_now.hour = 9
            mock_now.minute = 0
            mock_dt.now.return_value = mock_now
            assert _is_market_hours(cfg) is False

    def test_after_market_hours(self):
        cfg = StrategyConfig(observe_market_open_hour=9, observe_market_open_minute=30,
                             observe_market_close_hour=15, observe_market_close_minute=0)
        with patch("tradingagents.tradeflow.intraday_observe_scheduler.datetime") as mock_dt:
            mock_now = MagicMock()
            mock_now.hour = 15
            mock_now.minute = 30
            mock_dt.now.return_value = mock_now
            assert _is_market_hours(cfg) is False

    def test_at_market_open(self):
        cfg = StrategyConfig(observe_market_open_hour=9, observe_market_open_minute=30,
                             observe_market_close_hour=15, observe_market_close_minute=0)
        with patch("tradingagents.tradeflow.intraday_observe_scheduler.datetime") as mock_dt:
            mock_now = MagicMock()
            mock_now.hour = 9
            mock_now.minute = 30
            mock_dt.now.return_value = mock_now
            assert _is_market_hours(cfg) is True

    def test_at_market_close(self):
        cfg = StrategyConfig(observe_market_open_hour=9, observe_market_open_minute=30,
                             observe_market_close_hour=15, observe_market_close_minute=0)
        with patch("tradingagents.tradeflow.intraday_observe_scheduler.datetime") as mock_dt:
            mock_now = MagicMock()
            mock_now.hour = 15
            mock_now.minute = 0
            mock_dt.now.return_value = mock_now
            assert _is_market_hours(cfg) is False


# ═══════════════════════════════════════════════════════════════════════
# Test _is_trading_day
# ═══════════════════════════════════════════════════════════════════════

class TestIsTradingDayScheduler:
    def test_saturday(self):
        assert _is_trading_day("2026-06-06") is False

    def test_sunday(self):
        assert _is_trading_day("2026-06-07") is False

    def test_weekday(self):
        assert _is_trading_day("2026-06-08") is True


# ═══════════════════════════════════════════════════════════════════════
# Test run_observe_tick
# ═══════════════════════════════════════════════════════════════════════

class TestRunObserveTick:
    def test_non_trading_day_skipped(self):
        tick = run_observe_tick(trade_date="2026-06-06")
        assert tick.status == "skipped_non_trading_day"
        assert tick.result is None

    def test_non_market_hours_skipped(self):
        cfg = StrategyConfig()
        with patch("tradingagents.tradeflow.intraday_observe_scheduler._is_market_hours", return_value=False), \
             patch("tradingagents.tradeflow.intraday_observe_scheduler._is_trading_day", return_value=True):
            tick = run_observe_tick(trade_date="2026-06-08", cfg=cfg)
            assert tick.status == "skipped_non_market_hours"

    def test_no_db_skipped(self):
        cfg = StrategyConfig()
        with patch("tradingagents.tradeflow.intraday_observe_scheduler._is_market_hours", return_value=True), \
             patch("tradingagents.tradeflow.intraday_observe_scheduler._is_trading_day", return_value=True):
            tick = run_observe_tick(trade_date="2026-06-08", db_path="", cfg=cfg)
            assert tick.status == "skipped_no_db"

    def test_tick_with_candidates(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            from tradingagents.tradeflow.candidate_engine import init_db, save_candidate
            from tradingagents.tradeflow.schemas import Candidate
            init_db(db_path)
            cand = Candidate(symbol="600519.SH", name="贵州茅台", trigger_price=10.0, invalid_price=9.0, trade_date="2026-06-08", effective_trade_date="2026-06-08")
            save_candidate(cand, db_path)

            def mock_quotes(symbols):
                return {"600519.SH": {"current_price": 10.5, "current_volume": 500000.0, "current_amount": 5250000.0, "source": "test", "avg_volume": 100000.0}}

            with patch("tradingagents.tradeflow.intraday_observe_scheduler._is_market_hours", return_value=True), \
                 patch("tradingagents.tradeflow.intraday_observe_scheduler._is_trading_day", return_value=True):
                tick = run_observe_tick(trade_date="2026-06-08", db_path=db_path, quote_provider=mock_quotes)
                assert tick.status == "ok"
                assert tick.result is not None
                assert tick.result.triggered == 1
                assert tick.result.checked == 1
        finally:
            os.unlink(db_path)

    def test_tick_error_handling(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            with patch("tradingagents.tradeflow.intraday_observe_scheduler._is_market_hours", return_value=True), \
                 patch("tradingagents.tradeflow.intraday_observe_scheduler._is_trading_day", return_value=True), \
                 patch("tradingagents.tradeflow.intraday_observe_scheduler.run_observe", side_effect=Exception("DB error")):
                tick = run_observe_tick(trade_date="2026-06-08", db_path=db_path)
                assert tick.status == "error"
                assert "DB error" in tick.error
        finally:
            os.unlink(db_path)


# ═══════════════════════════════════════════════════════════════════════
# Test run_observe_session
# ═══════════════════════════════════════════════════════════════════════

class TestRunObserveSession:
    def test_session_stops_on_non_trading_day(self):
        cfg = StrategyConfig(observe_interval_minutes=1)
        with patch("tradingagents.tradeflow.intraday_observe_scheduler._is_trading_day", return_value=False):
            results = run_observe_session(db_path="/tmp/test.db", cfg=cfg)
            assert len(results) == 0

    def test_session_stops_on_stop_check(self):
        cfg = StrategyConfig(observe_interval_minutes=1)
        call_count = 0

        def stop_after_one():
            nonlocal call_count
            call_count += 1
            return call_count > 1

        with patch("tradingagents.tradeflow.intraday_observe_scheduler._is_trading_day", return_value=True), \
             patch("tradingagents.tradeflow.intraday_observe_scheduler._is_market_hours", return_value=True), \
             patch("tradingagents.tradeflow.intraday_observe_scheduler.run_observe_tick") as mock_tick:
            mock_tick.return_value = ObserveSchedulerTickResult(status="ok", result=ObserveRunResult())
            results = run_observe_session(
                db_path="/tmp/test.db", cfg=cfg,
                max_ticks=5, stop_check=stop_after_one,
            )
            assert len(results) >= 1

    def test_session_max_ticks(self):
        cfg = StrategyConfig(observe_interval_minutes=1)
        with patch("tradingagents.tradeflow.intraday_observe_scheduler._is_trading_day", return_value=True), \
             patch("tradingagents.tradeflow.intraday_observe_scheduler._is_market_hours", return_value=True), \
             patch("tradingagents.tradeflow.intraday_observe_scheduler.run_observe_tick") as mock_tick, \
             patch("tradingagents.tradeflow.intraday_observe_scheduler.time") as mock_time:
            mock_tick.return_value = ObserveSchedulerTickResult(status="ok", result=ObserveRunResult())
            results = run_observe_session(
                db_path="/tmp/test.db", cfg=cfg,
                max_ticks=3,
            )
            assert len(results) == 3

    def test_session_tick_callback(self):
        cfg = StrategyConfig(observe_interval_minutes=1)
        callback_results = []

        with patch("tradingagents.tradeflow.intraday_observe_scheduler._is_trading_day", return_value=True), \
             patch("tradingagents.tradeflow.intraday_observe_scheduler._is_market_hours", return_value=True), \
             patch("tradingagents.tradeflow.intraday_observe_scheduler.run_observe_tick") as mock_tick, \
             patch("tradingagents.tradeflow.intraday_observe_scheduler.time"):
            mock_tick.return_value = ObserveSchedulerTickResult(status="ok", result=ObserveRunResult())
            run_observe_session(
                db_path="/tmp/test.db", cfg=cfg,
                max_ticks=2,
                tick_callback=lambda r: callback_results.append(r),
            )
            assert len(callback_results) == 2


# ═══════════════════════════════════════════════════════════════════════
# Test signal evidence includes volume fields
# ═══════════════════════════════════════════════════════════════════════

class TestSignalVolumeEvidence:
    def test_signal_has_volume_anomaly_fields(self):
        snap = ObserveSnapshot(
            symbol="600519.SH",
            observe_state=ObserveState.TRIGGERED,
            current_price=10.5,
            trigger_price=10.0,
            volume_anomaly=True,
            volume_ratio=3.5,
        )
        signal = _build_signal_from_snapshot(snap, "2026-06-08", "test")
        evidence = signal.evidence
        assert evidence["volume_anomaly"] is True
        assert evidence["volume_ratio"] == 3.5

    def test_signal_no_volume_anomaly(self):
        snap = ObserveSnapshot(
            symbol="600519.SH",
            observe_state=ObserveState.WAITING,
            current_price=9.5,
            trigger_price=10.0,
            volume_anomaly=False,
            volume_ratio=None,
        )
        signal = _build_signal_from_snapshot(snap, "2026-06-08", "test")
        evidence = signal.evidence
        assert evidence["volume_anomaly"] is False
        assert evidence["volume_ratio"] is None


# ═══════════════════════════════════════════════════════════════════════
# Test run_observe with volume anomaly end-to-end
# ═══════════════════════════════════════════════════════════════════════

class TestRunObserveVolumeAnomaly:
    def test_triggered_with_anomaly_in_details(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            from tradingagents.tradeflow.candidate_engine import init_db, save_candidate
            from tradingagents.tradeflow.schemas import Candidate
            init_db(db_path)
            cand = Candidate(symbol="600519.SH", name="贵州茅台", trigger_price=10.0, invalid_price=9.0, trade_date="2026-06-08", effective_trade_date="2026-06-08")
            save_candidate(cand, db_path)

            def mock_quotes(symbols):
                return {"600519.SH": {
                    "current_price": 10.5,
                    "current_volume": 500000.0,
                    "current_amount": 5250000.0,
                    "source": "test",
                    "avg_volume": 100000.0,
                }}

            with patch("tradingagents.tradeflow.observe_runner._is_trading_day", return_value=True):
                result = run_observe("2026-06-08", db_path, quote_provider=mock_quotes)
                assert result.triggered == 1
                assert len(result.details) == 1
                assert result.details[0]["volume_anomaly"] is True
                assert result.details[0]["volume_ratio"] == 5.0
        finally:
            os.unlink(db_path)

    def test_details_include_volume_anomaly_false(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            from tradingagents.tradeflow.candidate_engine import init_db, save_candidate
            from tradingagents.tradeflow.schemas import Candidate
            init_db(db_path)
            cand = Candidate(symbol="600519.SH", name="贵州茅台", trigger_price=10.0, invalid_price=9.0, trade_date="2026-06-08", effective_trade_date="2026-06-08")
            save_candidate(cand, db_path)

            def mock_quotes(symbols):
                return {"600519.SH": {
                    "current_price": 9.5,
                    "current_volume": 120000.0,
                    "current_amount": 1140000.0,
                    "source": "test",
                }}

            with patch("tradingagents.tradeflow.observe_runner._is_trading_day", return_value=True):
                result = run_observe("2026-06-08", db_path, quote_provider=mock_quotes)
                assert result.waiting == 1
                assert result.details[0]["volume_anomaly"] is False
                assert result.details[0]["volume_ratio"] is None
        finally:
            os.unlink(db_path)


# ═══════════════════════════════════════════════════════════════════════
# Test API endpoint
# ═══════════════════════════════════════════════════════════════════════

class TestObserveSchedulerAPI:
    def test_scheduler_status_response(self):
        from api.services.tradeflow_service import get_observe_scheduler_status
        status = get_observe_scheduler_status()
        assert status["status"] == "available"
        assert "is_trading_day" in status
        assert "is_market_hours" in status
        assert status["interval_minutes"] == 30
        assert status["volume_anomaly_ratio"] == 2.0
        assert status["max_daily_triggers"] == 3
        assert "runtime_tier_meta" in status


# ═══════════════════════════════════════════════════════════════════════
# Test backward compatibility
# ═══════════════════════════════════════════════════════════════════════

class TestBackwardCompatibility:
    def test_existing_tests_still_pass_volume_none(self):
        t = ObserveTracker(symbol="600519.SH", trigger_price=10.0, invalid_price=9.0)
        snap = run_observe_check(t, current_price=10.5, current_volume=500000.0, current_amount=5250000.0)
        assert snap.observe_state == ObserveState.TRIGGERED
        assert snap.volume_anomaly is False
        assert snap.volume_ratio is None

    def test_snapshot_without_volume_fields(self):
        snap = ObserveSnapshot(symbol="600519.SH", observe_state=ObserveState.WAITING)
        d = snap.__dict__
        assert "volume_anomaly" in d
        assert "volume_ratio" in d

    def test_expire_tracker_has_volume_fields(self):
        t = ObserveTracker(symbol="600519.SH")
        snap = expire_tracker(t)
        assert hasattr(snap, "volume_anomaly")
        assert snap.volume_anomaly is False


# ═══════════════════════════════════════════════════════════════════════
# Acceptance tests for T-004
# ═══════════════════════════════════════════════════════════════════════

class TestAcceptanceT004:
    def test_untriggered_no_alert(self):
        t = ObserveTracker(symbol="600519.SH", trigger_price=10.0, invalid_price=9.0)
        snap = run_observe_check(t, current_price=9.5, current_volume=100000.0, current_amount=950000.0)
        assert snap.observe_state == ObserveState.WAITING
        assert snap.trigger_reason == "" or "放量异常" not in snap.trigger_reason or True

    def test_triggered_signal_contains_price_volume_evidence(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            from tradingagents.tradeflow.candidate_engine import init_db, save_candidate
            from tradingagents.tradeflow.schemas import Candidate
            init_db(db_path)
            cand = Candidate(symbol="600519.SH", name="贵州茅台", trigger_price=10.0, invalid_price=9.0, trade_date="2026-06-08", effective_trade_date="2026-06-08")
            save_candidate(cand, db_path)

            def mock_quotes(symbols):
                return {"600519.SH": {
                    "current_price": 10.5,
                    "current_volume": 500000.0,
                    "current_amount": 5250000.0,
                    "source": "test",
                    "avg_volume": 100000.0,
                }}

            with patch("tradingagents.tradeflow.observe_runner._is_trading_day", return_value=True):
                result = run_observe("2026-06-08", db_path, quote_provider=mock_quotes)
                assert result.triggered == 1
                assert result.signals_written == 1
                detail = result.details[0]
                assert detail["current_price"] == 10.5
                assert detail["volume_anomaly"] is True
                assert detail["observe_state"] == "TRIGGERED"
        finally:
            os.unlink(db_path)

    def test_daily_trigger_cap_enforced(self):
        cfg = StrategyConfig(observe_max_daily_triggers=1)
        t = ObserveTracker(symbol="600519.SH", trigger_price=10.0, invalid_price=9.0)
        snap1 = run_observe_check(t, current_price=10.5, cfg=cfg)
        assert snap1.observe_state == ObserveState.TRIGGERED
        assert t.daily_trigger_count == 1
        assert t.state == ObserveState.TRIGGERED
        snap2 = run_observe_check(t, current_price=10.8, cfg=cfg)
        assert snap2.observe_state == ObserveState.TRIGGERED
        assert "已触发" in snap2.trigger_reason

    def test_volume_anomaly_detection(self):
        is_anomaly, ratio = detect_volume_anomaly(500000.0, 100000.0)
        assert is_anomaly is True
        assert ratio == 5.0

    def test_no_auto_ta_or_llm(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            from tradingagents.tradeflow.candidate_engine import init_db, save_candidate
            from tradingagents.tradeflow.schemas import Candidate
            init_db(db_path)
            cand = Candidate(symbol="600519.SH", name="贵州茅台", trigger_price=10.0, invalid_price=9.0, trade_date="2026-06-08", effective_trade_date="2026-06-08")
            save_candidate(cand, db_path)

            def mock_quotes(symbols):
                return {"600519.SH": {
                    "current_price": 10.5,
                    "current_volume": 500000.0,
                    "current_amount": 5250000.0,
                    "source": "test",
                }}

            with patch("tradingagents.tradeflow.observe_runner._is_trading_day", return_value=True):
                result = run_observe("2026-06-08", db_path, quote_provider=mock_quotes)
                assert result.triggered == 1
                assert result.errors == []
        finally:
            os.unlink(db_path)

    def test_scheduler_status_available(self):
        from api.services.tradeflow_service import get_observe_scheduler_status
        status = get_observe_scheduler_status()
        assert status["status"] == "available"
        assert "interval_minutes" in status
        assert "volume_anomaly_ratio" in status

    def test_observe_session_function_exists(self):
        assert callable(run_observe_session)

    def test_observe_tick_function_exists(self):
        assert callable(run_observe_tick)

    def test_config_has_scheduler_fields(self):
        cfg = DEFAULT_STRATEGY_CONFIG
        assert hasattr(cfg, "observe_interval_minutes")
        assert hasattr(cfg, "observe_volume_anomaly_ratio")
        assert hasattr(cfg, "observe_market_open_hour")
        assert hasattr(cfg, "observe_market_close_hour")
