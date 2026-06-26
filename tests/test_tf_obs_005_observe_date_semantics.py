# [TF-OBS-005] observe_date_semantics
"""Tests for TF-OBS-005: 非交易日候选计划到下一交易日观察语义回归.

Covers:
- Weekend-generated candidate pool (plan_date=Saturday/Sunday) maps to the next
  trading day (effective_trade_date=Monday) and can be observed on Monday.
- get_observe() on the non-trading plan date itself no longer returns the false
  "当日无活跃候选" — it surfaces a cross-date hint explaining the plan is queued
  for the next trading day.
- get_observe() on the effective trading day returns the candidates and reports
  plan_date / effective_trade_date / observe_date explicitly.
- run_observe_check() response carries the same date-semantics fields.
- Old trading-day fixtures (plan_date == effective_trade_date) do NOT regress:
  non_trading_day_plan stays False and no misleading hint is shown.
- Pydantic TradeFlowObserveResponse accepts the new fields.
- _resolve_observe_date_semantics() handles no-DB / no-columns / empty-DB edges.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.tradeflow.candidate_engine import init_db, save_candidate
from tradingagents.tradeflow.date_semantics import (
    next_cn_trading_day,
    resolve_effective_trade_date,
)
from tradingagents.tradeflow.observe_runner import ObserveRunResult
from tradingagents.tradeflow.schemas import Candidate

from api.services.tradeflow_service import (
    _resolve_observe_date_semantics,
    get_observe,
    run_observe_check,
)
from api.tradeflow_schemas import TradeFlowObserveResponse


# 2026-05-30 = Saturday, 2026-05-31 = Sunday, 2026-06-01 = Monday (trading)
WEEKEND_SAT = "2026-05-30"
WEEKEND_SUN = "2026-05-31"
MONDAY = "2026-06-01"


@pytest.fixture
def tmp_db(tmp_path):
    db_path = str(tmp_path / "test_tf_obs005.db")
    init_db(db_path)
    return db_path


def _make_candidate(
    symbol: str,
    *,
    trade_date: str,
    effective_trade_date: str,
    plan_date: str = "",
    trigger_price: float = 10.0,
    invalid_price: float = 9.0,
    observe_state: str = "WAITING",
    status: str = "active",
) -> Candidate:
    return Candidate(
        symbol=symbol,
        name=f"Test {symbol}",
        source="manual",
        trade_date=trade_date,
        trigger_price=trigger_price,
        invalid_price=invalid_price,
        status=status,
        observe_state=observe_state,
        plan_date=plan_date or trade_date,
        effective_trade_date=effective_trade_date,
        observe_date=effective_trade_date,
    )


def _no_auto_run(monkeypatch):
    """Disable page-open auto-run so get_observe reads pre-seeded state only."""
    monkeypatch.setattr(
        "tradingagents.tradeflow.observe_runner.run_observe",
        lambda trade_date, db_path, **kw: ObserveRunResult(),
    )


# ── date_semantics sanity ────────────────────────────────────────────────


class TestDateSemanticsMapping:
    def test_weekend_resolves_to_next_trading_day(self):
        assert resolve_effective_trade_date(WEEKEND_SAT) == MONDAY
        assert resolve_effective_trade_date(WEEKEND_SUN) == MONDAY
        assert next_cn_trading_day(WEEKEND_SAT) == MONDAY
        assert next_cn_trading_day(WEEKEND_SUN) == MONDAY


# ── _resolve_observe_date_semantics unit tests ───────────────────────────


class TestResolveObserveDateSemantics:
    def test_no_db_returns_defaults(self, tmp_path):
        no_db = str(tmp_path / "missing.db")
        sem = _resolve_observe_date_semantics(no_db, MONDAY)
        assert sem["plan_date"] == ""
        assert sem["effective_trade_date"] == ""
        assert sem["non_trading_day_plan"] is False
        assert sem["next_trading_day_hint"] == ""
        # Monday is a trading day
        assert sem["is_view_trading_day"] is True

    def test_empty_db_returns_defaults(self, tmp_db):
        sem = _resolve_observe_date_semantics(tmp_db, MONDAY)
        assert sem["plan_date"] == ""
        assert sem["non_trading_day_plan"] is False

    def test_non_trading_day_view_with_cross_date_plan(self, tmp_db):
        """Viewing the Saturday plan date detects the next-Monday mapping."""
        c = _make_candidate("600519.SH", trade_date=WEEKEND_SAT, effective_trade_date=MONDAY,
                            plan_date=WEEKEND_SAT)
        save_candidate(c, tmp_db)

        sem = _resolve_observe_date_semantics(tmp_db, WEEKEND_SAT)
        assert sem["plan_date"] == WEEKEND_SAT
        assert sem["effective_trade_date"] == MONDAY
        assert sem["observe_date"] == MONDAY
        assert sem["non_trading_day_plan"] is True
        assert sem["is_view_trading_day"] is False
        assert WEEKEND_SAT in sem["next_trading_day_hint"]
        assert MONDAY in sem["next_trading_day_hint"]
        assert "下一交易日" in sem["next_trading_day_hint"]

    def test_trading_day_view_reports_plan_provenance(self, tmp_db):
        """Viewing Monday reports the originating Saturday plan_date."""
        c = _make_candidate("600519.SH", trade_date=WEEKEND_SAT, effective_trade_date=MONDAY,
                            plan_date=WEEKEND_SAT)
        save_candidate(c, tmp_db)

        sem = _resolve_observe_date_semantics(tmp_db, MONDAY)
        assert sem["plan_date"] == WEEKEND_SAT
        assert sem["effective_trade_date"] == MONDAY
        assert sem["non_trading_day_plan"] is True
        assert sem["is_view_trading_day"] is True
        assert WEEKEND_SAT in sem["next_trading_day_hint"]
        assert MONDAY in sem["next_trading_day_hint"]

    def test_same_day_plan_not_flagged_as_non_trading_day(self, tmp_db):
        """Old fixture: plan_date == effective_trade_date must not regress."""
        c = _make_candidate("600519.SH", trade_date=MONDAY, effective_trade_date=MONDAY,
                            plan_date=MONDAY)
        save_candidate(c, tmp_db)

        sem = _resolve_observe_date_semantics(tmp_db, MONDAY)
        assert sem["non_trading_day_plan"] is False
        assert sem["next_trading_day_hint"] == ""


# ── get_observe: weekend plan → Monday observe ───────────────────────────


class TestGetObserveCrossDate:
    def test_observe_on_monday_returns_weekend_candidates(self, tmp_db, monkeypatch):
        _no_auto_run(monkeypatch)
        c = _make_candidate("600519.SH", trade_date=WEEKEND_SAT, effective_trade_date=MONDAY,
                            plan_date=WEEKEND_SAT, trigger_price=10.0, invalid_price=9.0)
        save_candidate(c, tmp_db)

        result = get_observe(MONDAY, tf_db_path=tmp_db)

        assert result["status"] == "ok"
        # Weekend candidates must be observable on Monday (no false "no active")
        assert len(result["observe_items"]) == 1
        assert result["observe_items"][0]["symbol"] == "600519.SH"
        # Date semantics surfaced explicitly
        assert result["plan_date"] == WEEKEND_SAT
        assert result["effective_trade_date"] == MONDAY
        assert result["observe_date"] == MONDAY
        assert result["non_trading_day_plan"] is True
        assert WEEKEND_SAT in result["next_trading_day_hint"]

    def test_observe_on_saturday_no_false_negative(self, tmp_db, monkeypatch):
        """Viewing Saturday itself must NOT say '当日无活跃候选'."""
        _no_auto_run(monkeypatch)
        c = _make_candidate("600519.SH", trade_date=WEEKEND_SAT, effective_trade_date=MONDAY,
                            plan_date=WEEKEND_SAT)
        save_candidate(c, tmp_db)

        result = get_observe(WEEKEND_SAT, tf_db_path=tmp_db)

        # No candidates are observable on Saturday itself (non-trading day)
        assert result["status"] == "ok"
        assert len(result["observe_items"]) == 0
        # The reason must explain the cross-date mapping, not the false negative
        assert "当日无活跃候选" not in result["observe_reason"]
        assert MONDAY in result["observe_reason"]
        assert "下一交易日" in result["observe_reason"]
        # And the structured hint is populated
        assert result["non_trading_day_plan"] is True
        assert result["effective_trade_date"] == MONDAY

    def test_observe_on_sunday_no_false_negative(self, tmp_db, monkeypatch):
        _no_auto_run(monkeypatch)
        c = _make_candidate("000858.SZ", trade_date=WEEKEND_SUN, effective_trade_date=MONDAY,
                            plan_date=WEEKEND_SUN)
        save_candidate(c, tmp_db)

        result = get_observe(WEEKEND_SUN, tf_db_path=tmp_db)
        assert len(result["observe_items"]) == 0
        assert "当日无活跃候选" not in result["observe_reason"]
        assert MONDAY in result["observe_reason"]
        assert result["plan_date"] == WEEKEND_SUN

    def test_observe_no_db_returns_semantics_defaults(self, tmp_path):
        no_db = str(tmp_path / "missing.db")
        result = get_observe(MONDAY, tf_db_path=no_db)
        assert result["status"] == "no_data"
        # Defaults present so frontend schema stays consistent
        assert result["plan_date"] == ""
        assert result["non_trading_day_plan"] is False
        assert result["next_trading_day_hint"] == ""


# ── get_observe: same-day plan regression ────────────────────────────────


class TestGetObserveSameDayRegression:
    def test_trading_day_plan_not_flagged_as_cross_date(self, tmp_db, monkeypatch):
        _no_auto_run(monkeypatch)
        c = _make_candidate("600519.SH", trade_date=MONDAY, effective_trade_date=MONDAY,
                            plan_date=MONDAY)
        save_candidate(c, tmp_db)

        result = get_observe(MONDAY, tf_db_path=tmp_db)
        assert result["status"] == "ok"
        assert len(result["observe_items"]) == 1
        # Same-day plan: no cross-date hint
        assert result["non_trading_day_plan"] is False
        assert result["next_trading_day_hint"] == ""


# ── run_observe_check: date semantics in run response ────────────────────


class TestRunObserveCheckDateSemantics:
    def test_run_on_monday_carries_cross_date_fields(self, tmp_db, monkeypatch):
        """observe/run on Monday for a weekend plan returns cross-date fields."""
        c = _make_candidate("600519.SH", trade_date=WEEKEND_SAT, effective_trade_date=MONDAY,
                            plan_date=WEEKEND_SAT, trigger_price=10.0, invalid_price=9.0)
        save_candidate(c, tmp_db)

        # Stub the actual quote fetch so no live API is hit; runner still loads
        # candidates by effective_trade_date and reports the check counts.
        def _stub_run(trade_date, db_path, **kw):
            r = ObserveRunResult()
            r.checked = 1
            r.triggered = 1
            r.details = [{"symbol": "600519.SH", "observe_state": "TRIGGERED",
                          "current_price": 10.5}]
            return r
        monkeypatch.setattr(
            "tradingagents.tradeflow.observe_runner.run_observe", _stub_run
        )

        result = run_observe_check(MONDAY, tf_db_path=tmp_db)
        assert result["status"] == "ok"
        assert result["checked"] == 1
        # Cross-date provenance surfaced on the run response too
        assert result["plan_date"] == WEEKEND_SAT
        assert result["effective_trade_date"] == MONDAY
        assert result["observe_date"] == MONDAY
        assert result["non_trading_day_plan"] is True

    def test_run_on_same_day_no_cross_date(self, tmp_db, monkeypatch):
        c = _make_candidate("600519.SH", trade_date=MONDAY, effective_trade_date=MONDAY,
                            plan_date=MONDAY)
        save_candidate(c, tmp_db)

        monkeypatch.setattr(
            "tradingagents.tradeflow.observe_runner.run_observe",
            lambda trade_date, db_path, **kw: ObserveRunResult(checked=1, triggered=1),
        )
        result = run_observe_check(MONDAY, tf_db_path=tmp_db)
        assert result["non_trading_day_plan"] is False
        assert result["next_trading_day_hint"] == ""


# ── Pydantic schema accepts new fields ───────────────────────────────────


class TestObserveResponseSchema:
    def test_schema_accepts_cross_date_fields(self):
        data = {
            "status": "ok",
            "trade_date": MONDAY,
            "observe_items": [],
            "triggered_count": 0,
            "invalidated_count": 0,
            "waiting_count": 0,
            "plan_date": WEEKEND_SAT,
            "effective_trade_date": MONDAY,
            "observe_date": MONDAY,
            "non_trading_day_plan": True,
            "next_trading_day_hint": f"该候选池于 {WEEKEND_SAT}（非交易日）生成，将在 {MONDAY}（下一交易日）观察",
            "is_view_trading_day": True,
        }
        resp = TradeFlowObserveResponse(**data)
        assert resp.non_trading_day_plan is True
        assert resp.plan_date == WEEKEND_SAT
        assert resp.effective_trade_date == MONDAY
        assert "下一交易日" in resp.next_trading_day_hint

    def test_schema_defaults_when_fields_absent(self):
        # Old-shape payload (no TF-OBS-005 fields) must still validate
        resp = TradeFlowObserveResponse(status="ok", trade_date=MONDAY)
        assert resp.non_trading_day_plan is False
        assert resp.plan_date == ""
        assert resp.next_trading_day_hint == ""
        assert resp.is_view_trading_day is True

    def test_get_observe_result_serializes_through_schema(self, tmp_db, monkeypatch):
        """End-to-end: get_observe dict → TradeFlowObserveResponse round-trip."""
        _no_auto_run(monkeypatch)
        c = _make_candidate("600519.SH", trade_date=WEEKEND_SAT, effective_trade_date=MONDAY,
                            plan_date=WEEKEND_SAT)
        save_candidate(c, tmp_db)

        result = get_observe(MONDAY, tf_db_path=tmp_db)
        resp = TradeFlowObserveResponse(**result)
        assert resp.observe_items  # candidates present
        assert resp.non_trading_day_plan is True
        assert resp.effective_trade_date == MONDAY


# ── Forbidden strong words guard ─────────────────────────────────────────


class TestNoForbiddenStrongWords:
    def test_hint_has_no_strong_buy_sell_words(self, tmp_db, monkeypatch):
        from tradingagents.tradeflow.schemas import FORBIDDEN_WORDS

        _no_auto_run(monkeypatch)
        c = _make_candidate("600519.SH", trade_date=WEEKEND_SAT, effective_trade_date=MONDAY,
                            plan_date=WEEKEND_SAT)
        save_candidate(c, tmp_db)

        result = get_observe(WEEKEND_SAT, tf_db_path=tmp_db)
        hint = result["next_trading_day_hint"] + result["observe_reason"]
        for w in FORBIDDEN_WORDS:
            assert w not in hint, f"forbidden word {w!r} in observe hint"
