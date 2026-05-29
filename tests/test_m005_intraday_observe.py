"""Tests for M-005: Intraday Observe State Machine."""

import sys
import os
import tempfile
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.tradeflow.intraday_observe import (
    ObserveState,
    ObserveSnapshot,
    ObserveTracker,
    run_observe_check,
    expire_tracker,
)
from tradingagents.tradeflow.schemas import Candidate
from tradingagents.tradeflow.candidate_engine import init_db, save_candidate
from tradingagents.tradeflow.strategy_config import StrategyConfig


class TestObserveState:
    def test_enum_values(self):
        assert ObserveState.WAITING == "WAITING"
        assert ObserveState.TRIGGERED == "TRIGGERED"
        assert ObserveState.INVALIDATED == "INVALIDATED"
        assert ObserveState.EXPIRED == "EXPIRED"

    def test_all_states(self):
        expected = {"WAITING", "TRIGGERED", "INVALIDATED", "EXPIRED"}
        assert set(s.value for s in ObserveState) == expected


class TestObserveSnapshot:
    def test_basic_creation(self):
        snap = ObserveSnapshot(
            symbol="002353.SZ",
            observe_state=ObserveState.WAITING,
            current_price=95.0,
            trigger_price=100.0,
            invalid_price=90.0,
        )
        assert snap.symbol == "002353.SZ"
        assert snap.observe_state == ObserveState.WAITING
        assert snap.current_price == 95.0
        assert snap.check_time != ""

    def test_auto_timestamp(self):
        snap = ObserveSnapshot(
            symbol="600519.SH",
            observe_state=ObserveState.WAITING,
        )
        assert snap.check_time != ""
        assert "T" in snap.check_time or "-" in snap.check_time


class TestObserveTracker:
    def test_default_state(self):
        t = ObserveTracker(symbol="002353.SZ", trigger_price=100.0, invalid_price=90.0)
        assert t.state == ObserveState.WAITING
        assert t.daily_trigger_count == 0
        assert t.check_history == []
        assert t.trade_date != ""

    def test_custom_trade_date(self):
        t = ObserveTracker(symbol="002353.SZ", trade_date="2026-05-30")
        assert t.trade_date == "2026-05-30"


class TestRunObserveCheck:
    def test_price_below_trigger_stays_waiting(self):
        t = ObserveTracker(symbol="002353.SZ", trigger_price=100.0, invalid_price=90.0)
        snap = run_observe_check(t, current_price=95.0)
        assert snap.observe_state == ObserveState.WAITING
        assert t.state == ObserveState.WAITING
        assert snap.trigger_reason == ""

    def test_price_breaks_trigger_becomes_triggered(self):
        t = ObserveTracker(symbol="002353.SZ", trigger_price=100.0, invalid_price=90.0)
        snap = run_observe_check(t, current_price=101.0)
        assert snap.observe_state == ObserveState.TRIGGERED
        assert t.state == ObserveState.TRIGGERED
        assert "突破触发价" in snap.trigger_reason
        assert t.daily_trigger_count == 1
        assert snap.price_evidence["current_price"] == 101.0
        assert snap.price_evidence["trigger_price"] == 100.0
        assert snap.price_evidence["rise_pct"] > 0

    def test_price_exactly_at_trigger_is_triggered(self):
        t = ObserveTracker(symbol="002353.SZ", trigger_price=100.0, invalid_price=90.0)
        snap = run_observe_check(t, current_price=100.0)
        assert snap.observe_state == ObserveState.TRIGGERED
        assert t.daily_trigger_count == 1

    def test_price_drops_below_invalid_becomes_invalidated(self):
        t = ObserveTracker(symbol="002353.SZ", trigger_price=100.0, invalid_price=90.0)
        snap = run_observe_check(t, current_price=89.0)
        assert snap.observe_state == ObserveState.INVALIDATED
        assert t.state == ObserveState.INVALIDATED
        assert "跌破失效价" in snap.trigger_reason
        assert snap.price_evidence["breach_pct"] > 0

    def test_price_exactly_at_invalid_is_invalidated(self):
        t = ObserveTracker(symbol="002353.SZ", trigger_price=100.0, invalid_price=90.0)
        snap = run_observe_check(t, current_price=90.0)
        assert snap.observe_state == ObserveState.INVALIDATED

    def test_invalidation_checked_before_trigger(self):
        t = ObserveTracker(symbol="002353.SZ", trigger_price=100.0, invalid_price=95.0)
        snap = run_observe_check(t, current_price=94.0)
        assert snap.observe_state == ObserveState.INVALIDATED
        assert "跌破失效价" in snap.trigger_reason

    def test_no_trigger_price_stays_waiting(self):
        t = ObserveTracker(symbol="002353.SZ", trigger_price=None, invalid_price=90.0)
        snap = run_observe_check(t, current_price=95.0)
        assert snap.observe_state == ObserveState.WAITING

    def test_no_invalid_price_stays_waiting(self):
        t = ObserveTracker(symbol="002353.SZ", trigger_price=100.0, invalid_price=None)
        snap = run_observe_check(t, current_price=95.0)
        assert snap.observe_state == ObserveState.WAITING

    def test_no_prices_stays_waiting(self):
        t = ObserveTracker(symbol="002353.SZ", trigger_price=None, invalid_price=None)
        snap = run_observe_check(t, current_price=95.0)
        assert snap.observe_state == ObserveState.WAITING

    def test_volume_and_amount_recorded(self):
        t = ObserveTracker(symbol="002353.SZ", trigger_price=100.0, invalid_price=90.0)
        snap = run_observe_check(t, current_price=101.0, current_volume=500000.0, current_amount=50500000.0)
        assert snap.current_volume == 500000.0
        assert snap.current_amount == 50500000.0
        assert snap.price_evidence["current_volume"] == 500000.0
        assert snap.price_evidence["current_amount"] == 50500000.0

    def test_daily_trigger_cap(self):
        cfg = StrategyConfig(observe_max_daily_triggers=2)
        t = ObserveTracker(symbol="002353.SZ", trigger_price=100.0, invalid_price=90.0)

        snap1 = run_observe_check(t, current_price=101.0, cfg=cfg)
        assert snap1.observe_state == ObserveState.TRIGGERED
        assert t.daily_trigger_count == 1

        t.state = ObserveState.WAITING
        snap2 = run_observe_check(t, current_price=102.0, cfg=cfg)
        assert snap2.observe_state == ObserveState.TRIGGERED
        assert t.daily_trigger_count == 2

        t.state = ObserveState.WAITING
        snap3 = run_observe_check(t, current_price=103.0, cfg=cfg)
        assert snap3.observe_state == ObserveState.WAITING
        assert "上限" in snap3.trigger_reason
        assert t.daily_trigger_count == 2

    def test_history_recorded(self):
        t = ObserveTracker(symbol="002353.SZ", trigger_price=100.0, invalid_price=90.0)
        run_observe_check(t, current_price=95.0)
        run_observe_check(t, current_price=96.0)
        assert len(t.check_history) == 2
        assert t.check_history[0].current_price == 95.0
        assert t.check_history[1].current_price == 96.0

    def test_default_config_cap_is_3(self):
        cfg = StrategyConfig()
        assert cfg.observe_max_daily_triggers == 3


class TestTerminalStates:
    def test_triggered_stays_triggered(self):
        t = ObserveTracker(symbol="002353.SZ", trigger_price=100.0, invalid_price=90.0)
        run_observe_check(t, current_price=101.0)
        assert t.state == ObserveState.TRIGGERED

        snap = run_observe_check(t, current_price=95.0)
        assert snap.observe_state == ObserveState.TRIGGERED
        assert "已触发" in snap.trigger_reason

    def test_invalidated_stays_invalidated(self):
        t = ObserveTracker(symbol="002353.SZ", trigger_price=100.0, invalid_price=90.0)
        run_observe_check(t, current_price=89.0)
        assert t.state == ObserveState.INVALIDATED

        snap = run_observe_check(t, current_price=95.0)
        assert snap.observe_state == ObserveState.INVALIDATED
        assert "已失效" in snap.trigger_reason

    def test_expired_stays_expired(self):
        t = ObserveTracker(symbol="002353.SZ", trigger_price=100.0, invalid_price=90.0)
        expire_tracker(t)
        assert t.state == ObserveState.EXPIRED

        snap = run_observe_check(t, current_price=101.0)
        assert snap.observe_state == ObserveState.EXPIRED


class TestExpireTracker:
    def test_waiting_becomes_expired(self):
        t = ObserveTracker(symbol="002353.SZ", trigger_price=100.0, invalid_price=90.0)
        snap = expire_tracker(t)
        assert snap.observe_state == ObserveState.EXPIRED
        assert t.state == ObserveState.EXPIRED
        assert "收盘到期" in snap.trigger_reason

    def test_triggered_stays_triggered_on_expire(self):
        t = ObserveTracker(symbol="002353.SZ", trigger_price=100.0, invalid_price=90.0)
        run_observe_check(t, current_price=101.0)
        assert t.state == ObserveState.TRIGGERED
        snap = expire_tracker(t)
        assert snap.observe_state == ObserveState.TRIGGERED
        assert "收盘到期" in snap.trigger_reason

    def test_invalidated_stays_invalidated_on_expire(self):
        t = ObserveTracker(symbol="002353.SZ", trigger_price=100.0, invalid_price=90.0)
        run_observe_check(t, current_price=89.0)
        assert t.state == ObserveState.INVALIDATED
        snap = expire_tracker(t)
        assert snap.observe_state == ObserveState.INVALIDATED


class TestCandidateObserveIntegration:
    def test_candidate_default_observe_state(self):
        c = Candidate(symbol="002353.SZ", name="杰恩设计")
        assert c.observe_state == "WAITING"
        assert c.observe_trigger_count == 0
        assert c.observe_first_trigger_time == ""

    def test_candidate_to_db_row_includes_observe(self):
        c = Candidate(symbol="002353.SZ", trigger_price=100.0, invalid_price=90.0)
        c.observe_state = "TRIGGERED"
        c.observe_trigger_count = 1
        c.observe_first_trigger_time = "2026-05-30T10:30:00"
        row = c.to_db_row()
        assert row["observe_state"] == "TRIGGERED"
        assert row["observe_trigger_count"] == 1
        assert row["observe_first_trigger_time"] == "2026-05-30T10:30:00"

    def test_candidate_from_db_row_includes_observe(self):
        row = {
            "symbol": "002353.SZ",
            "name": "杰恩设计",
            "source": "manual",
            "strategy_tags_json": "[]",
            "primary_strategy": "",
            "score": 0.0,
            "status": "active",
            "trigger_price": 100.0,
            "support_price": None,
            "invalid_price": 90.0,
            "need_deep_ta": 0,
            "evidence_json": "{}",
            "risk_flags_json": "[]",
            "trade_date": "2026-05-30",
            "created_at": "2026-05-30T09:00:00",
            "updated_at": "2026-05-30T09:00:00",
            "observe_state": "TRIGGERED",
            "observe_trigger_count": 2,
            "observe_first_trigger_time": "2026-05-30T10:30:00",
        }
        c = Candidate.from_db_row(row)
        assert c.observe_state == "TRIGGERED"
        assert c.observe_trigger_count == 2
        assert c.observe_first_trigger_time == "2026-05-30T10:30:00"

    def test_candidate_from_db_row_defaults(self):
        row = {
            "symbol": "002353.SZ",
            "trade_date": "2026-05-30",
            "created_at": "2026-05-30T09:00:00",
        }
        c = Candidate.from_db_row(row)
        assert c.observe_state == "WAITING"
        assert c.observe_trigger_count == 0
        assert c.observe_first_trigger_time == ""


class TestDbPersistence:
    def test_save_and_read_observe_state(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            init_db(db_path)
            c = Candidate(
                symbol="002353.SZ",
                name="杰恩设计",
                trigger_price=100.0,
                invalid_price=90.0,
                observe_state="TRIGGERED",
                observe_trigger_count=1,
                observe_first_trigger_time="2026-05-30T10:30:00",
            )
            save_candidate(c, db_path)

            import sqlite3
            conn = sqlite3.connect(db_path)
            row = conn.execute(
                "SELECT observe_state, observe_trigger_count, observe_first_trigger_time "
                "FROM tradeflow_candidates WHERE symbol='002353.SZ'"
            ).fetchone()
            conn.close()

            assert row[0] == "TRIGGERED"
            assert row[1] == 1
            assert row[2] == "2026-05-30T10:30:00"
        finally:
            os.unlink(db_path)

    def test_upsert_observe_state(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            init_db(db_path)
            c1 = Candidate(symbol="002353.SZ", name="杰恩设计", trigger_price=100.0)
            save_candidate(c1, db_path)

            c2 = Candidate(
                symbol="002353.SZ",
                name="杰恩设计",
                trigger_price=100.0,
                observe_state="TRIGGERED",
                observe_trigger_count=1,
            )
            save_candidate(c2, db_path)

            import sqlite3
            conn = sqlite3.connect(db_path)
            row = conn.execute(
                "SELECT observe_state, observe_trigger_count FROM tradeflow_candidates WHERE symbol='002353.SZ'"
            ).fetchone()
            conn.close()

            assert row[0] == "TRIGGERED"
            assert row[1] == 1
        finally:
            os.unlink(db_path)


class TestTrackerToCandidateSync:
    def test_sync_triggered_tracker_to_candidate(self):
        t = ObserveTracker(symbol="002353.SZ", trigger_price=100.0, invalid_price=90.0)
        run_observe_check(t, current_price=101.0)

        c = Candidate(symbol="002353.SZ", name="杰恩设计", trigger_price=100.0, invalid_price=90.0)
        c.observe_state = t.state.value
        c.observe_trigger_count = t.daily_trigger_count
        c.observe_first_trigger_time = t.first_trigger_time or ""

        assert c.observe_state == "TRIGGERED"
        assert c.observe_trigger_count == 1
        assert c.observe_first_trigger_time != ""

    def test_sync_invalidated_tracker_to_candidate(self):
        t = ObserveTracker(symbol="002353.SZ", trigger_price=100.0, invalid_price=90.0)
        run_observe_check(t, current_price=89.0)

        c = Candidate(symbol="002353.SZ", trigger_price=100.0, invalid_price=90.0)
        c.observe_state = t.state.value
        c.observe_trigger_count = t.daily_trigger_count

        assert c.observe_state == "INVALIDATED"

    def test_sync_waiting_tracker_to_candidate(self):
        t = ObserveTracker(symbol="002353.SZ", trigger_price=100.0, invalid_price=90.0)
        run_observe_check(t, current_price=95.0)

        c = Candidate(symbol="002353.SZ")
        c.observe_state = t.state.value

        assert c.observe_state == "WAITING"


class TestStrategyConfigObserve:
    def test_default_observe_config(self):
        cfg = StrategyConfig()
        assert cfg.observe_max_daily_triggers == 3
        assert cfg.observe_trigger_breach_pct == 0.0
        assert cfg.observe_invalidate_breach_pct == 0.0

    def test_custom_observe_config(self):
        cfg = StrategyConfig(observe_max_daily_triggers=5)
        t = ObserveTracker(symbol="002353.SZ", trigger_price=100.0, invalid_price=90.0)
        for i in range(5):
            if i > 0:
                t.state = ObserveState.WAITING
            snap = run_observe_check(t, current_price=101.0, cfg=cfg)
            assert snap.observe_state == ObserveState.TRIGGERED

        t.state = ObserveState.WAITING
        snap6 = run_observe_check(t, current_price=102.0, cfg=cfg)
        assert snap6.observe_state == ObserveState.WAITING
        assert "上限" in snap6.trigger_reason


class TestFullObserveWorkflow:
    def test_full_day_workflow(self):
        cfg = StrategyConfig(observe_max_daily_triggers=3)
        t = ObserveTracker(symbol="002353.SZ", trigger_price=100.0, invalid_price=90.0)

        snap1 = run_observe_check(t, current_price=95.0, cfg=cfg)
        assert snap1.observe_state == ObserveState.WAITING

        snap2 = run_observe_check(t, current_price=97.0, cfg=cfg)
        assert snap2.observe_state == ObserveState.WAITING

        snap3 = run_observe_check(t, current_price=101.0, cfg=cfg)
        assert snap3.observe_state == ObserveState.TRIGGERED
        assert t.daily_trigger_count == 1
        assert t.first_trigger_time is not None

        assert len(t.check_history) == 3

    def test_invalidation_workflow(self):
        t = ObserveTracker(symbol="002353.SZ", trigger_price=100.0, invalid_price=90.0)

        run_observe_check(t, current_price=95.0)
        assert t.state == ObserveState.WAITING

        run_observe_check(t, current_price=89.5)
        assert t.state == ObserveState.INVALIDATED
        assert t.first_invalidation_time is not None

        snap = run_observe_check(t, current_price=95.0)
        assert snap.observe_state == ObserveState.INVALIDATED
        assert "已失效" in snap.trigger_reason

        expire_snap = expire_tracker(t)
        assert expire_snap.observe_state == ObserveState.INVALIDATED

    def test_untriggered_no_output(self):
        t = ObserveTracker(symbol="002353.SZ", trigger_price=100.0, invalid_price=90.0)
        for _ in range(5):
            snap = run_observe_check(t, current_price=95.0)
            assert snap.observe_state == ObserveState.WAITING
            assert snap.trigger_reason == ""
        assert t.daily_trigger_count == 0

    def test_multiple_symbols_isolated(self):
        t1 = ObserveTracker(symbol="002353.SZ", trigger_price=100.0, invalid_price=90.0)
        t2 = ObserveTracker(symbol="600519.SH", trigger_price=1800.0, invalid_price=1700.0)

        run_observe_check(t1, current_price=101.0)
        assert t1.state == ObserveState.TRIGGERED
        assert t2.state == ObserveState.WAITING

        run_observe_check(t2, current_price=1690.0)
        assert t2.state == ObserveState.INVALIDATED
        assert t1.state == ObserveState.TRIGGERED
