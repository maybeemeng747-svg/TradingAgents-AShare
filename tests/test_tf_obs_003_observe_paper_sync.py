# [TF-OBS-003] observe_paper_sync
"""Tests for TF-OBS-003: 盘中观察触发到模拟账本待确认联动.

Covers:
- _sync_paper_from_observe unit tests (triggered/invalidated/waiting/no-paper).
- End-to-end: run_observe(quote_provider) + sync → paper trade transitions.
- run_observe_check service wrapper returns paper_synced counts + syncs.
- get_observe auto-run path syncs paper ledger on page open.
- get_observe response enriches items with paper_status.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.tradeflow.candidate_engine import init_db, save_candidate
from tradingagents.tradeflow.observe_runner import run_observe, ObserveRunResult
from tradingagents.tradeflow.schemas import Candidate

from api.services.tradeflow_service import (
    _sync_paper_from_observe,
    add_paper_candidate,
    get_observe,
    get_paper_ledger,
    run_observe_check,
    update_paper_observe_state,
)


TODAY = datetime.now().strftime("%Y-%m-%d")


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_db(tmp_path):
    db_path = str(tmp_path / "test_obs_paper_sync.db")
    init_db(db_path)
    return db_path


def _make_candidate(symbol, trigger_price=10.0, invalid_price=9.0,
                    trade_date=TODAY, observe_state="WAITING"):
    return Candidate(
        symbol=symbol,
        name=f"Test {symbol}",
        source="manual",
        trade_date=trade_date,
        trigger_price=trigger_price,
        invalid_price=invalid_price,
        status="active",
        observe_state=observe_state,
        effective_trade_date=trade_date,
    )


def _mock_quotes(symbols, price_map):
    """Build a quote_provider that returns a fixed price per symbol."""
    def provider(syms):
        return {
            s: {
                "current_price": price_map.get(s, 10.0),
                "current_volume": 1000,
                "current_amount": price_map.get(s, 10.0) * 1000,
                "quote_time": "10:00",
                "source": "mock",
            }
            for s in syms
        }
    return provider


def _setup_candidate_and_paper(db_path, symbol="601689.SH", trigger_price=10.0,
                               invalid_price=9.0, trade_date=TODAY):
    """Insert a candidate into tradeflow_candidates AND the paper ledger."""
    save_candidate(_make_candidate(
        symbol, trigger_price=trigger_price, invalid_price=invalid_price,
        trade_date=trade_date,
    ), db_path)
    add_paper_candidate(
        symbol=symbol,
        name=f"Test {symbol}",
        trade_date=trade_date,
        trigger_price=trigger_price,
        invalid_price=invalid_price,
        planned_amount=1000,
        candidate_type="TECH_TRADE",
        tf_db_path=db_path,
    )


# ── _sync_paper_from_observe unit tests ──────────────────────────────────


class TestSyncPaperFromObserveUnit:

    def test_sync_triggered_to_pending(self, tmp_db):
        _setup_candidate_and_paper(tmp_db, "601689.SH", 10.0, 9.0)
        details = [{"symbol": "601689.SH", "observe_state": "TRIGGERED"}]
        result = _sync_paper_from_observe(details, tmp_db)
        assert result["synced"] == 1
        assert result["pending"] == 1
        ledger = get_paper_ledger(tf_db_path=tmp_db)
        trade = next(t for t in ledger["trades"] if t["symbol"] == "601689.SH")
        assert trade["status"] == "pending"
        assert trade["observe_state"] == "TRIGGERED"

    def test_sync_invalidated(self, tmp_db):
        _setup_candidate_and_paper(tmp_db, "601689.SH", 10.0, 9.0)
        details = [{"symbol": "601689.SH", "observe_state": "INVALIDATED"}]
        result = _sync_paper_from_observe(details, tmp_db)
        assert result["invalidated"] == 1
        ledger = get_paper_ledger(tf_db_path=tmp_db)
        trade = next(t for t in ledger["trades"] if t["symbol"] == "601689.SH")
        assert trade["status"] == "invalidated"

    def test_sync_waiting_keeps_tracking(self, tmp_db):
        _setup_candidate_and_paper(tmp_db, "601689.SH", 10.0, 9.0)
        details = [{"symbol": "601689.SH", "observe_state": "WAITING"}]
        _sync_paper_from_observe(details, tmp_db)
        ledger = get_paper_ledger(tf_db_path=tmp_db)
        trade = next(t for t in ledger["trades"] if t["symbol"] == "601689.SH")
        assert trade["status"] == "tracking"

    def test_sync_non_paper_candidate_creates_nothing(self, tmp_db):
        """Candidate not in paper ledger must not generate a record."""
        save_candidate(_make_candidate("600519.SH"), tmp_db)
        details = [{"symbol": "600519.SH", "observe_state": "TRIGGERED"}]
        result = _sync_paper_from_observe(details, tmp_db)
        assert result["synced"] == 0
        ledger = get_paper_ledger(tf_db_path=tmp_db)
        assert all(t["symbol"] != "600519.SH" for t in ledger["trades"])

    def test_sync_skips_unknown_state(self, tmp_db):
        _setup_candidate_and_paper(tmp_db, "601689.SH", 10.0, 9.0)
        details = [{"symbol": "601689.SH", "observe_state": "EXPIRED"}]
        result = _sync_paper_from_observe(details, tmp_db)
        assert result["synced"] == 0

    def test_sync_empty_details(self, tmp_db):
        result = _sync_paper_from_observe([], tmp_db)
        assert result == {"synced": 0, "pending": 0, "invalidated": 0}

    def test_sync_skips_skipped_no_price_entries(self, tmp_db):
        """Observe details with status=skipped_no_price should be ignored."""
        _setup_candidate_and_paper(tmp_db, "601689.SH", 10.0, 9.0)
        details = [{"symbol": "601689.SH", "status": "skipped_no_price"}]
        result = _sync_paper_from_observe(details, tmp_db)
        assert result["synced"] == 0


# ── End-to-end: run_observe + sync ───────────────────────────────────────


class TestObserveToPaperE2E:

    def test_triggered_candidate_becomes_pending(self, tmp_db):
        _setup_candidate_and_paper(tmp_db, "601689.SH", 10.0, 9.0)
        quotes = _mock_quotes(["601689.SH"], {"601689.SH": 10.5})
        result = run_observe(TODAY, tmp_db, quote_provider=quotes)
        assert result.triggered == 1

        _sync_paper_from_observe(result.details, tmp_db)

        ledger = get_paper_ledger(tf_db_path=tmp_db)
        trade = next(t for t in ledger["trades"] if t["symbol"] == "601689.SH")
        assert trade["status"] == "pending"
        assert trade["observe_state"] == "TRIGGERED"

    def test_invalidated_candidate_becomes_invalidated(self, tmp_db):
        _setup_candidate_and_paper(tmp_db, "002353.SZ", 35.0, 33.0)
        quotes = _mock_quotes(["002353.SZ"], {"002353.SZ": 32.0})
        result = run_observe(TODAY, tmp_db, quote_provider=quotes)
        assert result.invalidated == 1

        _sync_paper_from_observe(result.details, tmp_db)

        ledger = get_paper_ledger(tf_db_path=tmp_db)
        trade = next(t for t in ledger["trades"] if t["symbol"] == "002353.SZ")
        assert trade["status"] == "invalidated"

    def test_waiting_candidate_stays_tracking(self, tmp_db):
        _setup_candidate_and_paper(tmp_db, "601689.SH", 10.0, 9.0)
        quotes = _mock_quotes(["601689.SH"], {"601689.SH": 9.6})
        result = run_observe(TODAY, tmp_db, quote_provider=quotes)
        assert result.waiting == 1

        _sync_paper_from_observe(result.details, tmp_db)

        ledger = get_paper_ledger(tf_db_path=tmp_db)
        trade = next(t for t in ledger["trades"] if t["symbol"] == "601689.SH")
        assert trade["status"] == "tracking"

    def test_non_paper_candidate_no_record_after_observe(self, tmp_db):
        """Candidate observed but never added to paper ledger → no record."""
        save_candidate(_make_candidate("600519.SH", 10.0, 9.0), tmp_db)
        quotes = _mock_quotes(["600519.SH"], {"600519.SH": 10.5})
        result = run_observe(TODAY, tmp_db, quote_provider=quotes)
        assert result.triggered == 1

        _sync_paper_from_observe(result.details, tmp_db)

        ledger = get_paper_ledger(tf_db_path=tmp_db)
        assert all(t["symbol"] != "600519.SH" for t in ledger["trades"])

    def test_mixed_paper_and_non_paper(self, tmp_db):
        """One paper candidate + one non-paper candidate observed together."""
        _setup_candidate_and_paper(tmp_db, "601689.SH", 10.0, 9.0)
        save_candidate(_make_candidate("600519.SH", 200.0, 180.0), tmp_db)
        quotes = _mock_quotes(
            ["601689.SH", "600519.SH"],
            {"601689.SH": 10.5, "600519.SH": 210.0},
        )
        result = run_observe(TODAY, tmp_db, quote_provider=quotes)
        assert result.triggered == 2

        sync = _sync_paper_from_observe(result.details, tmp_db)
        assert sync["synced"] == 1  # only the paper candidate

        ledger = get_paper_ledger(tf_db_path=tmp_db)
        paper_syms = {t["symbol"] for t in ledger["trades"]}
        assert "601689.SH" in paper_syms
        assert "600519.SH" not in paper_syms


# ── run_observe_check service wrapper ────────────────────────────────────


class TestRunObserveCheckPaperSync:
    """The service wrapper run_observe_check should report paper_synced counts
    and sync the paper ledger after running observe."""

    def test_wrapper_syncs_paper_on_trigger(self, tmp_db, monkeypatch):
        _setup_candidate_and_paper(tmp_db, "601689.SH", 10.0, 9.0)

        def fake_run_observe(trade_date, db_path, **kwargs):
            r = ObserveRunResult()
            r.checked = 1
            r.triggered = 1
            r.signals_written = 1
            r.details = [{"symbol": "601689.SH", "observe_state": "TRIGGERED"}]
            return r

        monkeypatch.setattr(
            "tradingagents.tradeflow.observe_runner.run_observe",
            fake_run_observe,
        )

        resp = run_observe_check(TODAY, tf_db_path=tmp_db)
        assert resp["status"] == "ok"
        assert resp["paper_synced"] == 1
        assert resp["paper_pending"] == 1

        ledger = get_paper_ledger(tf_db_path=tmp_db)
        trade = next(t for t in ledger["trades"] if t["symbol"] == "601689.SH")
        assert trade["status"] == "pending"

    def test_wrapper_syncs_paper_on_invalidate(self, tmp_db, monkeypatch):
        _setup_candidate_and_paper(tmp_db, "601689.SH", 10.0, 9.0)

        def fake_run_observe(trade_date, db_path, **kwargs):
            r = ObserveRunResult()
            r.checked = 1
            r.invalidated = 1
            r.details = [{"symbol": "601689.SH", "observe_state": "INVALIDATED"}]
            return r

        monkeypatch.setattr(
            "tradingagents.tradeflow.observe_runner.run_observe",
            fake_run_observe,
        )

        resp = run_observe_check(TODAY, tf_db_path=tmp_db)
        assert resp["paper_synced"] == 1
        assert resp["paper_invalidated"] == 1
        ledger = get_paper_ledger(tf_db_path=tmp_db)
        trade = next(t for t in ledger["trades"] if t["symbol"] == "601689.SH")
        assert trade["status"] == "invalidated"

    def test_wrapper_non_paper_candidate_syncs_zero(self, tmp_db, monkeypatch):
        save_candidate(_make_candidate("600519.SH"), tmp_db)

        def fake_run_observe(trade_date, db_path, **kwargs):
            r = ObserveRunResult()
            r.triggered = 1
            r.details = [{"symbol": "600519.SH", "observe_state": "TRIGGERED"}]
            return r

        monkeypatch.setattr(
            "tradingagents.tradeflow.observe_runner.run_observe",
            fake_run_observe,
        )

        resp = run_observe_check(TODAY, tf_db_path=tmp_db)
        assert resp["paper_synced"] == 0
        assert resp["paper_pending"] == 0


# ── get_observe auto-run syncs paper ledger ──────────────────────────────


class TestGetObserveAutoRunSync:

    def test_auto_run_syncs_triggered_to_pending(self, tmp_db, monkeypatch):
        _setup_candidate_and_paper(tmp_db, "601689.SH", 10.0, 9.0)

        def fake_run_observe(trade_date, db_path, **kwargs):
            r = ObserveRunResult()
            r.checked = 1
            r.triggered = 1
            r.signals_written = 1
            r.details = [{"symbol": "601689.SH", "observe_state": "TRIGGERED"}]
            return r

        monkeypatch.setattr(
            "tradingagents.tradeflow.observe_runner.run_observe",
            fake_run_observe,
        )

        result = get_observe(TODAY, tf_db_path=tmp_db)
        assert result["observe_auto_run"] is True

        # Paper trade should have been synced to pending
        ledger = get_paper_ledger(tf_db_path=tmp_db)
        trade = next(t for t in ledger["trades"] if t["symbol"] == "601689.SH")
        assert trade["status"] == "pending"

    def test_auto_run_syncs_invalidated(self, tmp_db, monkeypatch):
        _setup_candidate_and_paper(tmp_db, "002353.SZ", 35.0, 33.0)

        def fake_run_observe(trade_date, db_path, **kwargs):
            r = ObserveRunResult()
            r.invalidated = 1
            r.details = [{"symbol": "002353.SZ", "observe_state": "INVALIDATED"}]
            return r

        monkeypatch.setattr(
            "tradingagents.tradeflow.observe_runner.run_observe",
            fake_run_observe,
        )

        get_observe(TODAY, tf_db_path=tmp_db)
        ledger = get_paper_ledger(tf_db_path=tmp_db)
        trade = next(t for t in ledger["trades"] if t["symbol"] == "002353.SZ")
        assert trade["status"] == "invalidated"

    def test_auto_run_non_paper_candidate_no_record(self, tmp_db, monkeypatch):
        save_candidate(_make_candidate("600519.SH"), tmp_db)

        def fake_run_observe(trade_date, db_path, **kwargs):
            r = ObserveRunResult()
            r.triggered = 1
            r.signals_written = 1
            r.details = [{"symbol": "600519.SH", "observe_state": "TRIGGERED"}]
            return r

        monkeypatch.setattr(
            "tradingagents.tradeflow.observe_runner.run_observe",
            fake_run_observe,
        )

        get_observe(TODAY, tf_db_path=tmp_db)
        ledger = get_paper_ledger(tf_db_path=tmp_db)
        assert all(t["symbol"] != "600519.SH" for t in ledger["trades"])


# ── get_observe enriches items with paper_status ─────────────────────────


class TestObserveItemsPaperStatus:

    def test_observe_item_has_paper_status_pending(self, tmp_db, monkeypatch):
        _setup_candidate_and_paper(tmp_db, "601689.SH", 10.0, 9.0)

        def fake_run_observe(trade_date, db_path, **kwargs):
            r = ObserveRunResult()
            r.triggered = 1
            r.signals_written = 1
            r.details = [{"symbol": "601689.SH", "observe_state": "TRIGGERED"}]
            return r

        monkeypatch.setattr(
            "tradingagents.tradeflow.observe_runner.run_observe",
            fake_run_observe,
        )

        resp = get_observe(TODAY, tf_db_path=tmp_db)
        assert resp["status"] == "ok"
        item = next(
            it for it in resp["observe_items"] if it["symbol"] == "601689.SH"
        )
        assert item["paper_status"] == "pending"

    def test_non_paper_candidate_paper_status_empty(self, tmp_db):
        save_candidate(_make_candidate("600519.SH", 10.0, 9.0), tmp_db)
        resp = get_observe(TODAY, tf_db_path=tmp_db)
        assert resp["status"] == "ok"
        item = next(
            it for it in resp["observe_items"] if it["symbol"] == "600519.SH"
        )
        assert item["paper_status"] == ""

    def test_tracking_candidate_shows_tracking_status(self, tmp_db, monkeypatch):
        _setup_candidate_and_paper(tmp_db, "601689.SH", 10.0, 9.0)

        def fake_run_observe(trade_date, db_path, **kwargs):
            r = ObserveRunResult()
            r.waiting = 1
            r.signals_written = 1
            r.details = [{"symbol": "601689.SH", "observe_state": "WAITING"}]
            return r

        monkeypatch.setattr(
            "tradingagents.tradeflow.observe_runner.run_observe",
            fake_run_observe,
        )

        resp = get_observe(TODAY, tf_db_path=tmp_db)
        assert resp["status"] == "ok"
        item = next(
            it for it in resp["observe_items"] if it["symbol"] == "601689.SH"
        )
        assert item["paper_status"] == "tracking"
