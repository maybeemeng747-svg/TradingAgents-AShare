# [TF-OBS-004] observe_refresh_alert_queue
"""Tests for TF-OBS-004: 盘中观察自动刷新、触发提醒与人工确认队列降噪.

Covers:
- get_observe() returns refresh metadata (refresh_interval_seconds,
  is_market_hours, is_trading_day, near_trigger_count, pending_count).
- _build_trigger_explain produces structured "why triggered / why not /
  how far off" for triggered / near_trigger / invalidated / waiting / no_data.
- Observe items are enriched with trigger_distance_pct / near_trigger /
  trigger_explain fields.
- Near-trigger grouping: candidates within the near band are flagged
  near_trigger=True; those far from trigger are not.
- Pending confirmation queue: triggered candidates that are in the paper
  ledger land in status=pending (no auto-order; never 'open').
- Triggered / near-trigger / invalidated three-way fixture coverage.
- Pydantic schema accepts the new fields.
- No forbidden strong words in any structured explanation text.
- Refresh metadata respects the speed budget (interval >= 60s, no
  high-frequency polling introduced).
"""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.tradeflow.candidate_engine import init_db, save_candidate
from tradingagents.tradeflow.observe_runner import ObserveRunResult
from tradingagents.tradeflow.schemas import FORBIDDEN_WORDS, Candidate
from tradingagents.tradeflow.strategy_config import DEFAULT_STRATEGY_CONFIG

from api.services.tradeflow_service import (
    _build_trigger_explain,
    add_paper_candidate,
    get_observe,
    get_paper_ledger,
)
from api.tradeflow_schemas import TradeFlowObserveItem, TradeFlowObserveResponse


TODAY = datetime.now().strftime("%Y-%m-%d")


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_db(tmp_path):
    db_path = str(tmp_path / "test_tf_obs004.db")
    init_db(db_path)
    return db_path


def _make_candidate(symbol, trigger_price=10.0, invalid_price=9.0,
                    trade_date=TODAY, observe_state="WAITING",
                    effective_trade_date=""):
    return Candidate(
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


def _insert_signal(db_path, symbol, trade_date, signal_type="observe_check",
                   current_price=10.0, trigger_reason="test"):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO tradeflow_signals "
        "(signal_time, symbol, signal_type, signal_level, source, evidence_json, action_hint, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            f"{trade_date}T10:30:00",
            symbol,
            signal_type,
            "info",
            "observe_runner",
            __import__("json").dumps({
                "trade_date": trade_date,
                "current_price": current_price,
                "trigger_reason": trigger_reason,
                "observe_state": "WAITING",
            }),
            "OBSERVE",
            "new",
            f"{trade_date}T10:30:00",
        ),
    )
    conn.commit()
    conn.close()


def _add_paper(db_path, symbol, trigger_price=10.0, invalid_price=9.0,
               trade_date=TODAY):
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


def _no_auto_run(monkeypatch):
    """Disable page-open auto-run so get_observe reads pre-seeded signals."""
    monkeypatch.setattr(
        "tradingagents.tradeflow.observe_runner.run_observe",
        lambda trade_date, db_path, **kw: ObserveRunResult(),
    )


# ── _build_trigger_explain unit tests ────────────────────────────────────


class TestBuildTriggerExplain:
    def test_triggered(self):
        e = _build_trigger_explain("WAITING", 10.20, 10.00, 9.00, 0.03)
        assert e["category"] == "triggered"
        assert e["why_triggered"] != ""
        assert e["why_not"] == ""
        assert "10.20" in e["why_triggered"]
        assert e["breach_pct"] == pytest.approx(2.0, abs=0.01)

    def test_triggered_by_state(self):
        e = _build_trigger_explain("TRIGGERED", 10.10, 10.00, 9.00, 0.03)
        assert e["category"] == "triggered"

    def test_near_trigger(self):
        # 1.5% below trigger, within 3% band
        e = _build_trigger_explain("WAITING", 9.85, 10.00, 9.00, 0.03)
        assert e["category"] == "near_trigger"
        assert e["how_far_off"] != ""
        assert "还差" in e["how_far_off"]
        assert e["why_not"] != ""

    def test_near_trigger_boundary_inclusive(self):
        # exactly 3% below trigger → near
        e = _build_trigger_explain("WAITING", 9.70, 10.00, 9.00, 0.03)
        assert e["category"] == "near_trigger"

    def test_waiting_far_from_trigger(self):
        # 5% below trigger, outside 3% band
        e = _build_trigger_explain("WAITING", 9.50, 10.00, 9.00, 0.03)
        assert e["category"] == "waiting"
        assert "还差" in e["how_far_off"]

    def test_invalidated_by_state(self):
        e = _build_trigger_explain("INVALIDATED", 9.50, 10.00, 9.00, 0.03)
        assert e["category"] == "invalidated"
        assert e["why_not"] != ""
        assert "失效" in e["why_not"]

    def test_invalidated_by_price(self):
        # price below invalid_price even if state is WAITING
        e = _build_trigger_explain("WAITING", 8.80, 10.00, 9.00, 0.03)
        assert e["category"] == "invalidated"

    def test_no_data_missing_price(self):
        e = _build_trigger_explain("WAITING", None, 10.00, 9.00, 0.03)
        assert e["category"] == "no_data"
        assert "缺少" in e["why_not"]

    def test_no_data_missing_trigger(self):
        e = _build_trigger_explain("WAITING", 10.0, None, 9.00, 0.03)
        assert e["category"] == "no_data"

    def test_no_data_zero_trigger(self):
        e = _build_trigger_explain("WAITING", 10.0, 0.0, 9.00, 0.03)
        assert e["category"] == "no_data"

    def test_explain_has_all_required_keys(self):
        e = _build_trigger_explain("WAITING", 10.0, 10.0, 9.0, 0.03)
        for key in ("category", "why_triggered", "why_not", "how_far_off", "breach_pct"):
            assert key in e


# ── get_observe refresh metadata ─────────────────────────────────────────


class TestRefreshMetadata:
    def test_no_db_returns_refresh_fields(self):
        result = get_observe(TODAY, tf_db_path="/nonexistent/path.db")
        assert "refresh_interval_seconds" in result
        assert "is_market_hours" in result
        assert "is_trading_day" in result
        assert "near_trigger_count" in result
        assert "pending_count" in result
        assert isinstance(result["refresh_interval_seconds"], int)
        assert isinstance(result["is_market_hours"], bool)
        assert isinstance(result["is_trading_day"], bool)

    def test_refresh_interval_respects_speed_budget(self):
        """[TF-OBS-004] constraint: no high-frequency polling — interval >= 60s."""
        result = get_observe(TODAY, tf_db_path="/nonexistent/path.db")
        assert result["refresh_interval_seconds"] >= 60
        # equals config observe_interval_minutes * 60
        expected = DEFAULT_STRATEGY_CONFIG.observe_interval_minutes * 60
        assert result["refresh_interval_seconds"] == expected

    def test_refresh_interval_matches_config(self, tmp_db, monkeypatch):
        save_candidate(_make_candidate("600519.SH"), tmp_db)
        _insert_signal(tmp_db, "600519.SH", TODAY)
        _no_auto_run(monkeypatch)
        result = get_observe(TODAY, tf_db_path=tmp_db)
        expected = DEFAULT_STRATEGY_CONFIG.observe_interval_minutes * 60
        assert result["refresh_interval_seconds"] == expected

    def test_refresh_metadata_present_with_candidates(self, tmp_db, monkeypatch):
        save_candidate(_make_candidate("600519.SH"), tmp_db)
        _insert_signal(tmp_db, "600519.SH", TODAY)
        _no_auto_run(monkeypatch)
        result = get_observe(TODAY, tf_db_path=tmp_db)
        assert "refresh_interval_seconds" in result
        assert "is_market_hours" in result
        assert "is_trading_day" in result

    def test_near_trigger_count_zero_when_empty(self, tmp_db, monkeypatch):
        _no_auto_run(monkeypatch)
        result = get_observe(TODAY, tf_db_path=tmp_db)
        assert result["near_trigger_count"] == 0
        assert result["pending_count"] == 0


# ── Item enrichment: trigger_explain / near_trigger / trigger_distance_pct ─


class TestItemEnrichment:
    def _get_item(self, db_path, monkeypatch, symbol="600519.SH",
                  current_price=10.0):
        save_candidate(_make_candidate(symbol, trigger_price=10.0,
                                       invalid_price=9.0), db_path)
        _insert_signal(db_path, symbol, TODAY, current_price=current_price)
        _no_auto_run(monkeypatch)
        result = get_observe(TODAY, tf_db_path=db_path)
        return next(it for it in result["observe_items"] if it["symbol"] == symbol)

    def test_item_has_trigger_explain(self, tmp_db, monkeypatch):
        item = self._get_item(tmp_db, monkeypatch, current_price=10.0)
        assert "trigger_explain" in item
        te = item["trigger_explain"]
        assert te["category"] in ("triggered", "near_trigger", "waiting",
                                  "invalidated", "no_data")

    def test_item_has_trigger_distance_pct(self, tmp_db, monkeypatch):
        item = self._get_item(tmp_db, monkeypatch, current_price=10.0)
        assert "trigger_distance_pct" in item
        # at trigger price → breach_pct ~ 0
        assert item["trigger_distance_pct"] == pytest.approx(0.0, abs=0.01)

    def test_item_has_near_trigger_flag(self, tmp_db, monkeypatch):
        item = self._get_item(tmp_db, monkeypatch, current_price=10.0)
        assert "near_trigger" in item
        assert isinstance(item["near_trigger"], bool)

    def test_near_trigger_flagged_when_close(self, tmp_db, monkeypatch):
        # 1.5% below trigger → near
        item = self._get_item(tmp_db, monkeypatch, current_price=9.85)
        assert item["near_trigger"] is True
        assert item["trigger_explain"]["category"] == "near_trigger"

    def test_near_trigger_not_flagged_when_far(self, tmp_db, monkeypatch):
        # 5% below trigger → not near
        item = self._get_item(tmp_db, monkeypatch, current_price=9.50)
        assert item["near_trigger"] is False
        assert item["trigger_explain"]["category"] == "waiting"

    def test_triggered_item_has_why_triggered(self, tmp_db, monkeypatch):
        item = self._get_item(tmp_db, monkeypatch, current_price=10.30)
        assert item["trigger_explain"]["category"] == "triggered"
        assert item["trigger_explain"]["why_triggered"] != ""

    def test_item_has_paper_status(self, tmp_db, monkeypatch):
        item = self._get_item(tmp_db, monkeypatch, current_price=10.0)
        assert "paper_status" in item


# ── Three-way fixture coverage: triggered / near-trigger / invalidated ───


class TestThreeWayGrouping:
    def _seed_and_run(self, db_path, monkeypatch, candidates):
        """Seed multiple candidates with signals, return get_observe items."""
        for sym, price, state, tp, ip in candidates:
            save_candidate(_make_candidate(sym, trigger_price=tp,
                                           invalid_price=ip,
                                           observe_state=state), db_path)
            _insert_signal(db_path, sym, TODAY, current_price=price)
        _no_auto_run(monkeypatch)
        return get_observe(TODAY, tf_db_path=db_path)

    def test_three_categories_present(self, tmp_db, monkeypatch):
        result = self._seed_and_run(tmp_db, monkeypatch, [
            ("600001.SH", 10.30, "TRIGGERED", 10.0, 9.0),   # triggered
            ("600002.SH", 9.85, "WAITING", 10.0, 9.0),      # near-trigger
            ("600003.SH", 8.50, "INVALIDATED", 10.0, 9.0),  # invalidated
        ])
        items = {it["symbol"]: it for it in result["observe_items"]}
        assert items["600001.SH"]["trigger_explain"]["category"] == "triggered"
        assert items["600002.SH"]["trigger_explain"]["category"] == "near_trigger"
        assert items["600003.SH"]["trigger_explain"]["category"] == "invalidated"
        assert result["near_trigger_count"] == 1

    def test_triggered_count_matches(self, tmp_db, monkeypatch):
        result = self._seed_and_run(tmp_db, monkeypatch, [
            ("600001.SH", 10.30, "TRIGGERED", 10.0, 9.0),
            ("600002.SH", 10.50, "TRIGGERED", 10.0, 9.0),
            ("600003.SH", 9.85, "WAITING", 10.0, 9.0),
        ])
        assert result["triggered_count"] == 2

    def test_invalidated_count_matches(self, tmp_db, monkeypatch):
        result = self._seed_and_run(tmp_db, monkeypatch, [
            ("600001.SH", 8.50, "INVALIDATED", 10.0, 9.0),
            ("600002.SH", 9.85, "WAITING", 10.0, 9.0),
        ])
        assert result["invalidated_count"] == 1

    def test_near_trigger_count_aggregates(self, tmp_db, monkeypatch):
        result = self._seed_and_run(tmp_db, monkeypatch, [
            ("600001.SH", 9.85, "WAITING", 10.0, 9.0),   # near (1.5%)
            ("600002.SH", 9.80, "WAITING", 10.0, 9.0),   # near (2.0%)
            ("600003.SH", 9.50, "WAITING", 10.0, 9.0),   # far (5%)
        ])
        assert result["near_trigger_count"] == 2


# ── Pending confirmation queue (no auto-order) ───────────────────────────


class TestPendingConfirmationQueue:
    def _setup_triggered_paper_candidate(self, db_path, symbol="601689.SH",
                                         trigger_price=10.0, invalid_price=9.0):
        """Seed a TRIGGERED candidate + paper ledger entry, but NO signal yet
        so get_observe's page-open auto-run branch fires and syncs the ledger."""
        save_candidate(_make_candidate(symbol, trigger_price=trigger_price,
                                       invalid_price=invalid_price,
                                       observe_state="TRIGGERED"), db_path)
        _add_paper(db_path, symbol, trigger_price=trigger_price, invalid_price=invalid_price)

    def _fake_triggered_run(self, monkeypatch, symbol, db_path_holder,
                            current_price=10.30):
        """Fake run_observe: inserts a signal + returns details so get_observe
        syncs the paper ledger (triggered → pending)."""
        def fake_run(trade_date, db_path, **kw):
            _insert_signal(db_path, symbol, trade_date,
                           signal_type="observe_triggered",
                           current_price=current_price,
                           trigger_reason="突破触发价")
            r = ObserveRunResult()
            r.checked = 1
            r.triggered = 1
            r.signals_written = 1
            r.details = [{"symbol": symbol, "observe_state": "TRIGGERED",
                          "trade_date": trade_date}]
            return r
        monkeypatch.setattr("tradingagents.tradeflow.observe_runner.run_observe", fake_run)

    def test_triggered_paper_candidate_becomes_pending_not_open(self, tmp_db, monkeypatch):
        """[TF-OBS-004] constraint: trigger alerts only go to pending queue,
        never auto-order (status must NOT become 'open' without manual confirm)."""
        self._setup_triggered_paper_candidate(tmp_db, "601689.SH")
        self._fake_triggered_run(monkeypatch, "601689.SH", tmp_db)
        result = get_observe(TODAY, tf_db_path=tmp_db)
        ledger = get_paper_ledger(tf_db_path=tmp_db)
        trade = next(t for t in ledger["trades"] if t["symbol"] == "601689.SH")
        assert trade["status"] == "pending"
        assert trade["status"] != "open"
        assert result["pending_count"] >= 1

    def test_pending_count_reflects_paper_ledger(self, tmp_db, monkeypatch):
        self._setup_triggered_paper_candidate(tmp_db, "601689.SH")
        self._fake_triggered_run(monkeypatch, "601689.SH", tmp_db)
        result = get_observe(TODAY, tf_db_path=tmp_db)
        assert result["pending_count"] == 1

    def test_non_paper_triggered_does_not_create_pending(self, tmp_db, monkeypatch):
        """A triggered candidate NOT in the paper ledger must not appear in pending_count."""
        save_candidate(_make_candidate("600519.SH", trigger_price=10.0,
                                       invalid_price=9.0,
                                       observe_state="TRIGGERED"), tmp_db)
        # No _add_paper → not in paper ledger
        self._fake_triggered_run(monkeypatch, "600519.SH", tmp_db, current_price=10.30)
        result = get_observe(TODAY, tf_db_path=tmp_db)
        assert result["pending_count"] == 0
        # And no paper trade record created for it
        ledger = get_paper_ledger(tf_db_path=tmp_db)
        assert all(t["symbol"] != "600519.SH" for t in ledger["trades"])


# ── Pydantic schema acceptance ───────────────────────────────────────────


class TestSchemaAcceptance:
    def test_observe_item_accepts_new_fields(self):
        item = TradeFlowObserveItem(
            symbol="600519.SH",
            paper_status="pending",
            trigger_distance_pct=1.5,
            near_trigger=False,
            trigger_explain={"category": "triggered", "why_triggered": "x",
                             "why_not": "", "how_far_off": "", "breach_pct": 1.5},
        )
        assert item.paper_status == "pending"
        assert item.trigger_distance_pct == 1.5
        assert item.trigger_explain["category"] == "triggered"

    def test_observe_response_accepts_refresh_fields(self):
        resp = TradeFlowObserveResponse(
            trade_date=TODAY,
            refresh_interval_seconds=180,
            is_market_hours=True,
            is_trading_day=True,
            near_trigger_count=2,
            pending_count=1,
        )
        assert resp.refresh_interval_seconds == 180
        assert resp.is_market_hours is True
        assert resp.near_trigger_count == 2
        assert resp.pending_count == 1

    def test_observe_item_defaults(self):
        item = TradeFlowObserveItem(symbol="600519.SH")
        assert item.paper_status == ""
        assert item.trigger_distance_pct is None
        assert item.near_trigger is False
        assert item.trigger_explain == {}

    def test_observe_response_defaults(self):
        resp = TradeFlowObserveResponse()
        assert resp.refresh_interval_seconds == 180
        assert resp.is_market_hours is False
        assert resp.is_trading_day is False
        assert resp.near_trigger_count == 0
        assert resp.pending_count == 0


# ── No forbidden strong words ────────────────────────────────────────────


class TestNoForbiddenWords:
    def test_trigger_explain_has_no_forbidden_words(self):
        """[TF-OBS-004] validation: no 强买卖词 in structured explanations."""
        scenarios = [
            _build_trigger_explain("TRIGGERED", 10.30, 10.00, 9.00, 0.03),
            _build_trigger_explain("WAITING", 9.85, 10.00, 9.00, 0.03),
            _build_trigger_explain("WAITING", 9.50, 10.00, 9.00, 0.03),
            _build_trigger_explain("INVALIDATED", 8.50, 10.00, 9.00, 0.03),
            _build_trigger_explain("WAITING", None, 10.00, 9.00, 0.03),
        ]
        for e in scenarios:
            text = " ".join([e["why_triggered"], e["why_not"], e["how_far_off"]])
            for w in FORBIDDEN_WORDS:
                assert w not in text, f"forbidden word '{w}' in: {text}"


# ── Speed budget compliance ──────────────────────────────────────────────


class TestSpeedBudgetCompliance:
    def test_no_full_ta_or_llm_fields(self, tmp_db, monkeypatch):
        """[TF-OBS-004] constraint: must not increase FULL_TA or LLM calls.
        get_observe must not introduce any llm_call_count / runtime_profile escalation."""
        save_candidate(_make_candidate("600519.SH"), tmp_db)
        _insert_signal(tmp_db, "600519.SH", TODAY)
        _no_auto_run(monkeypatch)
        result = get_observe(TODAY, tf_db_path=tmp_db)
        # runtime_tier_meta should remain a fast tier (no FULL_TA escalation)
        meta = result.get("runtime_tier_meta", {})
        tier = meta.get("runtime_tier", "")
        assert tier != "FULL_TA"
