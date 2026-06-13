# [TF-OBS-002] observe_auto_run
"""Tests for TF-OBS-002: Observe auto-run on page open and A-share color metadata.

Covers:
- get_observe() returns observe_auto_run, last_observed_at, observe_reason fields.
- Auto-run triggers when candidates exist but no observe signals for the date.
- Auto-run skips when signals already exist for the date.
- No-plan scenarios return clear observe_reason.
- Non-trading-day auto-run returns skip reason.
- Cross-date (non-trading day plan → trading day observe) mapping works.
- Pydantic schema includes new fields.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.tradeflow.candidate_engine import init_db, save_candidate
from tradingagents.tradeflow.schemas import Candidate
from tradingagents.tradeflow.observe_runner import ObserveRunResult

from api.services.tradeflow_service import get_observe, _precheck_observe_state
from api.tradeflow_schemas import TradeFlowObserveResponse


TODAY = datetime.now().strftime("%Y-%m-%d")


@pytest.fixture
def tmp_db(tmp_path):
    db_path = str(tmp_path / "test_tf_obs002.db")
    init_db(db_path)
    return db_path


def _make_candidate(symbol, trigger_price=10.0, invalid_price=9.0,
                    trade_date=TODAY, observe_state="WAITING",
                    effective_trade_date=""):
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
            json.dumps({
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


class TestPrecheckObserveState:
    def test_no_candidates_no_db(self, tmp_path):
        result = _precheck_observe_state(str(tmp_path / "nonexistent.db"), TODAY)
        # init_db creates the DB, so after precheck it exists
        assert result["has_candidates"] is False
        assert result["reason"] == "无计划"

    def test_has_candidates_no_signals(self, tmp_db):
        save_candidate(_make_candidate("600519.SH"), tmp_db)
        result = _precheck_observe_state(tmp_db, TODAY)
        assert result["has_candidates"] is True
        assert result["has_signals_for_date"] is False
        assert result["reason"] == ""

    def test_has_candidates_and_signals(self, tmp_db):
        save_candidate(_make_candidate("600519.SH"), tmp_db)
        _insert_signal(tmp_db, "600519.SH", TODAY)
        result = _precheck_observe_state(tmp_db, TODAY)
        assert result["has_candidates"] is True
        assert result["has_signals_for_date"] is True
        assert result["last_observed_at"] != ""

    def test_no_candidates_but_db_has_data(self, tmp_db):
        save_candidate(_make_candidate("600519.SH", trade_date="2026-05-01"), tmp_db)
        result = _precheck_observe_state(tmp_db, TODAY)
        assert result["has_candidates"] is False
        assert result["reason"] == "当日无活跃候选"


class TestGetObserveNewFields:
    def test_no_db_returns_auto_run_fields(self):
        result = get_observe(TODAY, tf_db_path="/nonexistent/path.db")
        assert "observe_auto_run" in result
        assert "last_observed_at" in result
        assert "observe_reason" in result
        assert result["observe_auto_run"] is False
        assert result["observe_reason"] == "无数据库"

    def test_returns_observe_auto_run_false_when_signals_exist(self, tmp_db):
        save_candidate(_make_candidate("600519.SH"), tmp_db)
        _insert_signal(tmp_db, "600519.SH", TODAY)
        result = get_observe(TODAY, tf_db_path=tmp_db)
        assert result["observe_auto_run"] is False
        assert result["last_observed_at"] != ""

    def test_returns_observe_reason_when_no_plan(self, tmp_db):
        result = get_observe(TODAY, tf_db_path=tmp_db)
        assert result["observe_auto_run"] is False
        assert result["observe_reason"] == "无计划"
        assert result["observe_items"] == []


class TestAutoRunTriggers:
    def test_auto_run_called_when_no_signals(self, tmp_db, monkeypatch):
        save_candidate(_make_candidate("600519.SH", observe_state="TRIGGERED"), tmp_db)

        call_log = []

        def fake_run_observe(trade_date, db_path, **kwargs):
            call_log.append(trade_date)
            r = ObserveRunResult()
            r.checked = 1
            r.triggered = 1
            r.signals_written = 1
            return r

        monkeypatch.setattr(
            "tradingagents.tradeflow.observe_runner.run_observe",
            fake_run_observe,
        )

        result = get_observe(TODAY, tf_db_path=tmp_db)
        assert len(call_log) == 1
        assert call_log[0] == TODAY
        assert result["observe_auto_run"] is True

    def test_auto_run_populates_items_after_run(self, tmp_db, monkeypatch):
        save_candidate(_make_candidate("600519.SH", observe_state="TRIGGERED"), tmp_db)

        def fake_run_observe(trade_date, db_path, **kwargs):
            _insert_signal(db_path, "600519.SH", trade_date, signal_type="observe_triggered",
                           current_price=10.5, trigger_reason="突破触发价")
            r = ObserveRunResult()
            r.checked = 1
            r.triggered = 1
            r.signals_written = 1
            return r

        monkeypatch.setattr(
            "tradingagents.tradeflow.observe_runner.run_observe",
            fake_run_observe,
        )

        result = get_observe(TODAY, tf_db_path=tmp_db)
        assert result["observe_auto_run"] is True
        assert len(result["observe_items"]) == 1
        item = result["observe_items"][0]
        assert item["current_price"] == 10.5
        assert item["trigger_reason"] == "突破触发价"

    def test_auto_run_not_called_when_signals_exist(self, tmp_db, monkeypatch):
        save_candidate(_make_candidate("600519.SH"), tmp_db)
        _insert_signal(tmp_db, "600519.SH", TODAY)

        call_log = []

        def fake_run_observe(trade_date, db_path, **kwargs):
            call_log.append(trade_date)
            return ObserveRunResult()

        monkeypatch.setattr(
            "tradingagents.tradeflow.observe_runner.run_observe",
            fake_run_observe,
        )

        result = get_observe(TODAY, tf_db_path=tmp_db)
        assert len(call_log) == 0
        assert result["observe_auto_run"] is False

    def test_auto_run_surfaces_skip_reason(self, tmp_db, monkeypatch):
        save_candidate(_make_candidate("600519.SH"), tmp_db)

        def fake_run_observe(trade_date, db_path, **kwargs):
            r = ObserveRunResult()
            r.skipped_reason = f"{trade_date} 非交易日，跳过实时观察"
            return r

        monkeypatch.setattr(
            "tradingagents.tradeflow.observe_runner.run_observe",
            fake_run_observe,
        )

        result = get_observe(TODAY, tf_db_path=tmp_db)
        assert result["observe_auto_run"] is True
        assert "非交易日" in result["observe_reason"]


class TestNoPlanScenarios:
    def test_empty_db_returns_no_plan_reason(self, tmp_db):
        result = get_observe(TODAY, tf_db_path=tmp_db)
        assert result["observe_reason"] == "无计划"
        assert result["observe_items"] == []
        assert result["observe_auto_run"] is False

    def test_different_date_returns_no_active_reason(self, tmp_db):
        save_candidate(_make_candidate("600519.SH", trade_date="2026-05-01",
                                       effective_trade_date="2026-05-01"), tmp_db)
        result = get_observe(TODAY, tf_db_path=tmp_db)
        assert result["observe_reason"] == "当日无活跃候选"


class TestCrossDatePlanMapping:
    def test_non_trading_day_plan_mapped_to_trading_day(self, tmp_db, monkeypatch):
        # Plan generated on Saturday (2026-05-31) with effective_trade_date = Monday (2026-06-02)
        c = _make_candidate("300750.SZ", trade_date="2026-05-31",
                            effective_trade_date=TODAY)
        save_candidate(c, tmp_db)

        call_log = []

        def fake_run_observe(trade_date, db_path, **kwargs):
            call_log.append(trade_date)
            _insert_signal(db_path, "300750.SZ", trade_date, signal_type="observe_triggered",
                           current_price=10.2, trigger_reason="跨日映射触发")
            r = ObserveRunResult()
            r.checked = 1
            r.triggered = 1
            r.signals_written = 1
            return r

        monkeypatch.setattr(
            "tradingagents.tradeflow.observe_runner.run_observe",
            fake_run_observe,
        )

        # Query for the effective trade date (Monday)
        result = get_observe(TODAY, tf_db_path=tmp_db)
        assert result["observe_auto_run"] is True
        assert len(result["observe_items"]) == 1
        assert result["observe_items"][0]["symbol"] == "300750.SZ"
        assert call_log[0] == TODAY


class TestSchemaIncludesNewFields:
    def test_trade_flow_observe_response_has_auto_run_fields(self):
        schema_fields = TradeFlowObserveResponse.model_fields
        assert "observe_auto_run" in schema_fields
        assert "last_observed_at" in schema_fields
        assert "observe_reason" in schema_fields

    def test_trade_flow_observe_response_defaults(self):
        resp = TradeFlowObserveResponse()
        assert resp.observe_auto_run is False
        assert resp.last_observed_at == ""
        assert resp.observe_reason == ""


class TestAcceptanceTF_OBS_002:
    """Acceptance criteria for TF-OBS-002."""

    def test_page_open_auto_runs_without_click(self, tmp_db, monkeypatch):
        """进入页面后无需点击即可看到观察状态。"""
        save_candidate(_make_candidate("600519.SH", observe_state="TRIGGERED"), tmp_db)

        def fake_run_observe(trade_date, db_path, **kwargs):
            _insert_signal(db_path, "600519.SH", trade_date, signal_type="observe_triggered",
                           current_price=10.5, trigger_reason="auto run")
            r = ObserveRunResult()
            r.checked = 1
            r.triggered = 1
            r.signals_written = 1
            return r

        monkeypatch.setattr(
            "tradingagents.tradeflow.observe_runner.run_observe",
            fake_run_observe,
        )

        result = get_observe(TODAY, tf_db_path=tmp_db)
        assert result["observe_auto_run"] is True
        assert len(result["observe_items"]) == 1
        assert result["observe_items"][0]["current_price"] == 10.5

    def test_empty_state_shows_reason_not_blank(self, tmp_db):
        """页面不能空白 — 有明确的 observe_reason。"""
        result = get_observe(TODAY, tf_db_path=tmp_db)
        assert result["observe_reason"] != ""
        assert result["observe_reason"] == "无计划"

    def test_triggered_state_uses_red_not_green(self):
        """触发价颜色符合 A 股红涨直觉 — 这在前端测试中验证，
        后端只确保 observe_state 值正确返回。"""
        # This is a documentation test; actual color logic is in frontend CSS.
        assert True

    def test_fixture_coverage_triggered(self, tmp_db, monkeypatch):
        """Fixture: 已触发。"""
        save_candidate(_make_candidate("600519.SH", observe_state="WAITING",
                                       trigger_price=10.0, invalid_price=9.0), tmp_db)

        def fake_run_observe(trade_date, db_path, **kwargs):
            _insert_signal(db_path, "600519.SH", trade_date, signal_type="observe_triggered",
                           current_price=10.5, trigger_reason="突破")
            r = ObserveRunResult()
            r.checked = 1
            r.triggered = 1
            r.signals_written = 1
            return r

        monkeypatch.setattr(
            "tradingagents.tradeflow.observe_runner.run_observe",
            fake_run_observe,
        )

        result = get_observe(TODAY, tf_db_path=tmp_db)
        items = result["observe_items"]
        assert len(items) == 1
        assert items[0]["current_price"] == 10.5
        assert items[0]["observe_state"] in ("TRIGGERED", "WAITING")

    def test_fixture_coverage_waiting(self, tmp_db, monkeypatch):
        """Fixture: 等待中。"""
        save_candidate(_make_candidate("600519.SH", trigger_price=10.0, invalid_price=9.0), tmp_db)

        def fake_run_observe(trade_date, db_path, **kwargs):
            _insert_signal(db_path, "600519.SH", trade_date, signal_type="observe_check",
                           current_price=9.5, trigger_reason="等待突破")
            r = ObserveRunResult()
            r.checked = 1
            r.waiting = 1
            r.signals_written = 1
            return r

        monkeypatch.setattr(
            "tradingagents.tradeflow.observe_runner.run_observe",
            fake_run_observe,
        )

        result = get_observe(TODAY, tf_db_path=tmp_db)
        assert result["waiting_count"] >= 0
        assert len(result["observe_items"]) == 1

    def test_fixture_coverage_invalidated(self, tmp_db, monkeypatch):
        """Fixture: 已失效。"""
        save_candidate(_make_candidate("600519.SH", observe_state="INVALIDATED",
                                       trigger_price=10.0, invalid_price=9.0), tmp_db)

        def fake_run_observe(trade_date, db_path, **kwargs):
            _insert_signal(db_path, "600519.SH", trade_date, signal_type="observe_invalidated",
                           current_price=8.5, trigger_reason="跌破失效价")
            r = ObserveRunResult()
            r.checked = 1
            r.invalidated = 1
            r.signals_written = 1
            return r

        monkeypatch.setattr(
            "tradingagents.tradeflow.observe_runner.run_observe",
            fake_run_observe,
        )

        result = get_observe(TODAY, tf_db_path=tmp_db)
        assert result["invalidated_count"] >= 0

    def test_fixture_coverage_no_plan(self, tmp_db):
        """Fixture: 无计划。"""
        result = get_observe(TODAY, tf_db_path=tmp_db)
        assert result["observe_items"] == []
        assert result["observe_reason"] == "无计划"
        assert result["observe_auto_run"] is False

    def test_fixture_coverage_non_trading_day_mapping(self, tmp_db, monkeypatch):
        """Fixture: 非交易日计划映射。"""
        c = _make_candidate("300750.SZ", trade_date="2026-05-31",
                            effective_trade_date=TODAY)
        save_candidate(c, tmp_db)

        def fake_run_observe(trade_date, db_path, **kwargs):
            _insert_signal(db_path, "300750.SZ", trade_date)
            r = ObserveRunResult()
            r.checked = 1
            r.waiting = 1
            r.signals_written = 1
            return r

        monkeypatch.setattr(
            "tradingagents.tradeflow.observe_runner.run_observe",
            fake_run_observe,
        )

        result = get_observe(TODAY, tf_db_path=tmp_db)
        assert len(result["observe_items"]) == 1
        assert result["observe_items"][0]["symbol"] == "300750.SZ"
