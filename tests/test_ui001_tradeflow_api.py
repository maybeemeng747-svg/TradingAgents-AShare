# [UI-001] tradeflow_api
"""Tests for TradeFlow read-only API endpoints.

Covers:
- Empty data (no DB) → status="no_data"
- Candidate data with DB
- Candidate detail endpoint
- Review endpoint
- Observe endpoint
- TA queue endpoint
- Data health endpoint
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime
from typing import Any, Dict
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.tradeflow.candidate_engine import init_db, save_candidate
from tradingagents.tradeflow.candidate_engine import save_filtered_symbols, get_filtered_symbols  # [UI-007] tradeflow_filtered_trace
from tradingagents.tradeflow.schemas import Candidate, CandidateSignal
from tradingagents.tradeflow.plan_runner import save_plan, DailyPlan

from api.services.tradeflow_service import (
    _get_tradeflow_db_path,
    get_daily_plan,
    get_candidates,
    get_candidate_detail,
    get_observe,
    get_ta_queue,
    get_review,
    get_data_health,
    run_discovery_scan,
    run_observe_check,  # [TF-OBS-001] tradeflow_observe_runner
    get_filtered,  # [UI-007] tradeflow_filtered_trace
)
from api.tradeflow_schemas import TradeFlowDataHealthResponse  # [TF-OBS-001]


@pytest.fixture
def tf_db(tmp_path):
    db_path = str(tmp_path / "test_tradeflow.db")
    init_db(db_path)
    return db_path


@pytest.fixture
def sample_candidate():
    c = Candidate(
        symbol="002353.SZ",
        name="杰瑞股份",
        source="manual",
        strategy_tags=["VCP", "PULLBACK_SUPPORT"],
        primary_strategy="VCP",
        score=65.0,
        trigger_price=45.50,
        support_price=43.00,
        invalid_price=42.00,
        need_deep_ta=True,
        trade_date="2026-05-30",
        tier="A",
        ta_budget_priority=10,
        tier_reason="综合分高",
        composite_score=78.5,
        tradeflow_data_completeness=0.85,
        missing_data_fields=["fund_flow"],
        game_balance="favorable",
        bull_case="业绩增长",
        bear_case="估值偏高",
        policy_case="政策支持",
        fund_flow_case="主力净流入",
        why_deep_ta="VCP突破形态确认",
        why_not_deep_ta="",
        observe_state="WAITING",
        observe_trigger_count=0,
        deep_ta_status="PENDING",
        deep_ta_dispatch_reason="tier A candidate",
        evidence={"VCP": {"score": 65, "reason": "VCP形态"}},
        candidate_type="POLICY_AMBUSH",
        mandate_score_component=72.5,
        ambush_score=68.0,
        mandate_topic="低空经济",
        company_role="CORE_SUPPLIER",
        beneficiary_path=["空管系统", "核心设备"],
        candidate_type_reason="政策连续性强且公司受益路径明确",
        deep_ta_route="MIDLINE_RESEARCH",
        ambush_reasons=["政策连续性强", "未明显过热"],
        ambush_evidence_refs=[{"title": "低空经济政策支持", "source": "MINISTRY"}],
        mandate_evidence_refs=[{"title": "低空经济政策支持", "source": "MINISTRY"}],
        research_queue="MIDLINE_POLICY",
        research_intent="policy_validation",
        research_route_reason="左侧政策候选进入中线研究队列",
        watchlist_note="用户原备注",
        watchlist_note_suggested="低空经济｜利好8.8｜共识86｜窗口1月｜缺口:订单",
        watchlist_topic="低空经济",
        watchlist_benefit_score=8.8,
        watchlist_consensus_score=86.0,
        watchlist_evidence_gap=["订单"],
    )
    c.signals = [CandidateSignal(strategy_tag="VCP", score=65.0, reason="VCP形态确认")]
    return c


def test_default_tradeflow_db_path_points_to_project_root():
    assert _get_tradeflow_db_path().endswith("TradingAgents-AShare/tradeflow.db")
    assert not _get_tradeflow_db_path().endswith("TradingAgents-AShare/api/tradeflow.db")


def test_legacy_tradeflow_candidate_schema_does_not_500(tmp_path):
    db_path = str(tmp_path / "legacy_tradeflow.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE tradeflow_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            name TEXT DEFAULT '',
            source TEXT DEFAULT 'manual',
            strategy_tags_json TEXT DEFAULT '[]',
            score REAL DEFAULT 0.0,
            status TEXT DEFAULT 'active',
            trigger_price REAL,
            support_price REAL,
            invalid_price REAL,
            need_deep_ta INTEGER DEFAULT 0,
            evidence_json TEXT DEFAULT '{}',
            risk_flags_json TEXT DEFAULT '[]',
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO tradeflow_candidates (
            trade_date, symbol, name, strategy_tags_json, score, trigger_price, need_deep_ta, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("2026-05-31", "002353.SZ", "杰瑞股份", '["VCP"]', 55.0, 36.77, 1, "2026-05-31", "2026-05-31"),
    )
    conn.commit()
    conn.close()

    candidates = get_candidates("2026-05-31", tf_db_path=db_path)
    observe = get_observe("2026-05-31", tf_db_path=db_path)
    queue = get_ta_queue("2026-05-31", tf_db_path=db_path)

    assert candidates["status"] == "ok"
    assert candidates["candidates"][0]["symbol"] == "002353.SZ"
    assert candidates["candidates"][0]["composite_score"] == 0.0
    assert candidates["candidates"][0]["score"] == 55.0
    assert observe["status"] == "ok"
    assert observe["observe_items"][0]["symbol"] == "002353.SZ"
    assert queue["status"] == "ok"
    assert queue["queue"][0]["symbol"] == "002353.SZ"


@pytest.fixture
def populated_db(tf_db, sample_candidate):
    save_candidate(sample_candidate, tf_db)
    c2 = Candidate(
        symbol="603256.SH",
        name="宏和科技",
        strategy_tags=["EVENT_CATALYST"],
        primary_strategy="EVENT_CATALYST",
        score=50.0,
        trigger_price=30.00,
        invalid_price=28.00,
        need_deep_ta=False,
        trade_date="2026-05-30",
        tier="B",
        composite_score=55.0,
        tradeflow_data_completeness=0.6,
        observe_state="TRIGGERED",
        observe_trigger_count=1,
        deep_ta_status="",
    )
    save_candidate(c2, tf_db)

    plan = DailyPlan(
        trade_date="2026-05-30",
        mode="pre_market",
        summary="共2只候选",
        candidates=[
            {
                "symbol": "002353.SZ",
                "name": "杰瑞股份",
                "action": "NEED_DEEP_TA",
                "tier": "A",
                "composite_score": 78.5,
                "need_deep_ta": True,
                "trigger_price": 45.50,
                "invalid_price": 42.00,
                "observe_state": "WAITING",
                "strategy_tags": ["VCP"],
            },
            {
                "symbol": "603256.SH",
                "name": "宏和科技",
                "action": "WAIT_TRIGGER",
                "tier": "B",
                "composite_score": 55.0,
                "need_deep_ta": False,
                "trigger_price": 30.00,
                "observe_state": "TRIGGERED",
                "strategy_tags": ["EVENT_CATALYST"],
            },
        ],
        metadata={"universe_size": 2},
    )
    save_plan(plan, tf_db)
    return tf_db


class TestDailyPlanEmptyData:
    def test_no_db_returns_no_data(self):
        result = get_daily_plan("2026-05-30", tf_db_path="/nonexistent/path.db")
        assert result["status"] == "no_data"
        assert result["trade_date"] == "2026-05-30"
        assert result.get("candidates") is None or result.get("candidates", []) == []

    def test_no_plan_for_date(self, tf_db):
        result = get_daily_plan("2020-01-01", tf_db_path=tf_db)
        assert result["status"] == "no_data"


class TestDailyPlanWithData:
    def test_returns_plan(self, populated_db):
        result = get_daily_plan("2026-05-30", tf_db_path=populated_db)
        assert result["status"] == "ok"
        assert result["trade_date"] == "2026-05-30"
        assert result["mode"] == "pre_market"
        assert len(result["candidates"]) == 2

    def test_summary_agg(self, populated_db):
        result = get_daily_plan("2026-05-30", tf_db_path=populated_db)
        agg = result["summary_agg"]
        assert agg["total_candidates"] == 2
        assert agg["tier_a_count"] == 1
        assert agg["tier_b_count"] == 1

    def test_metadata_present(self, populated_db):
        result = get_daily_plan("2026-05-30", tf_db_path=populated_db)
        assert "metadata" in result
        assert result["metadata"].get("universe_size") == 2


class TestCandidatesEmptyData:
    def test_no_db_returns_no_data(self):
        result = get_candidates("2026-05-30", tf_db_path="/nonexistent/path.db")
        assert result["status"] == "no_data"


class TestCandidatesWithData:
    def test_returns_all_candidates(self, populated_db):
        result = get_candidates("2026-05-30", tf_db_path=populated_db)
        assert result["status"] == "ok"
        assert len(result["candidates"]) == 2

    def test_filter_by_tier(self, populated_db):
        result = get_candidates("2026-05-30", tier="A", tf_db_path=populated_db)
        assert result["status"] == "ok"
        assert len(result["candidates"]) == 1
        assert result["candidates"][0]["symbol"] == "002353.SZ"

    def test_filter_by_need_deep_ta(self, populated_db):
        result = get_candidates("2026-05-30", need_deep_ta=True, tf_db_path=populated_db)
        assert result["status"] == "ok"
        assert len(result["candidates"]) == 1
        assert result["candidates"][0]["need_deep_ta"] is True

    def test_summary_agg(self, populated_db):
        result = get_candidates("2026-05-30", tf_db_path=populated_db)
        agg = result["summary_agg"]
        assert agg["total_candidates"] == 2
        assert agg["need_deep_ta_count"] == 1

    def test_action_field_populated(self, populated_db):
        result = get_candidates("2026-05-30", tf_db_path=populated_db)
        for c in result["candidates"]:
            assert "action" in c
            assert c["action"] in {"OBSERVE", "WAIT_TRIGGER", "NEED_DEEP_TA", "REMOVE_FROM_WATCH"}

    def test_mandate_fields_populated(self, populated_db):
        result = get_candidates("2026-05-30", tf_db_path=populated_db)
        c = next(item for item in result["candidates"] if item["symbol"] == "002353.SZ")
        assert c["candidate_type"] == "POLICY_AMBUSH"
        assert c["mandate_score"] == 72.5
        assert c["ambush_score"] == 68.0
        assert c["mandate_topic"] == "低空经济"
        assert c["company_role"] == "CORE_SUPPLIER"
        assert c["beneficiary_path"] == ["空管系统", "核心设备"]
        assert c["research_queue"] == "MIDLINE_POLICY"
        assert c["research_intent"] == "policy_validation"
        assert c["watchlist_note"] == "用户原备注"
        assert c["watchlist_note_suggested"].startswith("低空经济")
        assert c["watchlist_evidence_gap"] == ["订单"]

    def test_filter_by_candidate_type(self, populated_db):
        result = get_candidates("2026-05-30", candidate_type="POLICY_AMBUSH", tf_db_path=populated_db)
        assert result["status"] == "ok"
        assert [c["symbol"] for c in result["candidates"]] == ["002353.SZ"]

        empty = get_candidates("2026-05-30", candidate_type="TECH_TRADE", tf_db_path=populated_db)
        assert empty["status"] == "ok"
        assert empty["candidates"] == []


class TestRunDiscoveryScan:
    def test_discovery_scan_persists_candidates(self, tf_db, monkeypatch):
        c = Candidate(
            symbol="002353.SZ",
            name="杰瑞股份",
            strategy_tags=["VCP"],
            primary_strategy="VCP",
            score=60.0,
            composite_score=70.0,
            trigger_price=36.77,
            invalid_price=33.91,
            need_deep_ta=True,
            trade_date="2026-05-30",
            tier="A",
            tradeflow_data_completeness=0.8,
        )
        c.signals = [CandidateSignal(strategy_tag="VCP", score=60.0, reason="缩量整理")]

        def fake_evaluate_symbol(**kwargs):
            return c, ""

        # Mock date_semantics so effective_trade_date == trade_date
        import tradingagents.tradeflow.date_semantics as dt_mod
        monkeypatch.setattr(dt_mod, "resolve_effective_trade_date", lambda d: d)
        monkeypatch.setattr(dt_mod, "resolve_observe_date", lambda d: d)
        monkeypatch.setattr("tradingagents.tradeflow.discovery.evaluate_symbol", fake_evaluate_symbol)

        result = run_discovery_scan(
            trade_date="2026-05-30",
            symbols=["002353.SZ"],
            top_n=10,
            include_holdings=False,
            include_watchlist=False,
            tf_db_path=tf_db,
            prod_db_path="/nonexistent/tradingagents.db",
        )

        assert result["status"] == "ok"
        assert result["candidate_count"] == 1
        saved = get_candidates("2026-05-30", tf_db_path=tf_db)
        assert saved["status"] == "ok"
        assert saved["candidates"][0]["symbol"] == "002353.SZ"


class TestCandidateDetail:
    def test_no_db_returns_no_data(self):
        result = get_candidate_detail("002353.SZ", "2026-05-30", tf_db_path="/nonexistent/path.db")
        assert result["status"] == "no_data"

    def test_not_found(self, populated_db):
        result = get_candidate_detail("999999.SZ", "2026-05-30", tf_db_path=populated_db)
        assert result["status"] == "no_data"

    def test_returns_detail(self, populated_db):
        result = get_candidate_detail("002353.SZ", "2026-05-30", tf_db_path=populated_db)
        assert result["status"] == "ok"
        c = result["candidate"]
        assert c["symbol"] == "002353.SZ"
        assert c["name"] == "杰瑞股份"
        assert c["tier"] == "A"
        assert c["composite_score"] == 78.5
        assert c["trigger_price"] == 45.50
        assert c["need_deep_ta"] is True
        assert c["game_balance"] == "favorable"
        assert c["bull_case"] == "业绩增长"
        assert c["bear_case"] == "估值偏高"
        assert isinstance(c["evidence"], dict)
        assert "action" in c
        assert c["action"] == "NEED_DEEP_TA"
        assert c["ambush_reasons"] == ["政策连续性强", "未明显过热"]
        assert c["ambush_evidence_refs"][0]["source"] == "MINISTRY"
        assert c["mandate_evidence_refs"][0]["title"] == "低空经济政策支持"


class TestObserve:
    def test_no_db_returns_no_data(self):
        result = get_observe("2026-05-30", tf_db_path="/nonexistent/path.db")
        assert result["status"] == "no_data"

    def test_returns_observe_items(self, populated_db):
        result = get_observe("2026-05-30", tf_db_path=populated_db)
        assert result["status"] == "ok"
        assert len(result["observe_items"]) == 2
        assert result["triggered_count"] == 1
        assert result["waiting_count"] == 1

    def test_observe_item_fields(self, populated_db):
        result = get_observe("2026-05-30", tf_db_path=populated_db)
        item = result["observe_items"][0]
        assert "symbol" in item
        assert "observe_state" in item
        assert "trigger_price" in item
        assert "strategy_tags" in item

    def test_observe_item_uses_signal_for_same_trade_date(self, populated_db):
        conn = sqlite3.connect(populated_db)
        conn.execute(
            """
            INSERT INTO tradeflow_signals (
                signal_time, symbol, signal_type, signal_level, source, evidence_json, action_hint, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-05-30T10:30:00",
                "002353.SZ",
                "observe_triggered",
                "warning",
                "observe_runner",
                json.dumps({"trade_date": "2026-05-30", "current_price": 45.6, "trigger_reason": "放量突破触发价"}),
                "OBSERVE",
                "new",
                "2026-05-30T10:30:00",
            ),
        )
        conn.commit()
        conn.close()

        result = get_observe("2026-05-30", tf_db_path=populated_db)
        item = next(i for i in result["observe_items"] if i["symbol"] == "002353.SZ")
        assert item["current_price"] == 45.6
        assert item["trigger_reason"] == "放量突破触发价"

    def test_observe_item_ignores_signal_from_other_trade_date(self, populated_db):
        conn = sqlite3.connect(populated_db)
        conn.execute(
            """
            INSERT INTO tradeflow_signals (
                signal_time, symbol, signal_type, signal_level, source, evidence_json, action_hint, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-05-31T10:30:00",
                "002353.SZ",
                "observe_triggered",
                "warning",
                "observe_runner",
                json.dumps({"trade_date": "2026-05-31", "current_price": 99.9, "trigger_reason": "其他日期信号"}),
                "OBSERVE",
                "new",
                "2026-05-31T10:30:00",
            ),
        )
        conn.commit()
        conn.close()

        result = get_observe("2026-05-30", tf_db_path=populated_db)
        item = next(i for i in result["observe_items"] if i["symbol"] == "002353.SZ")
        assert item["current_price"] is None
        assert item["trigger_reason"] == ""


class TestObserveRun:
    def test_run_observe_check_returns_runner_summary(self, tf_db, monkeypatch):
        class DummyResult:
            checked = 2
            triggered = 1
            invalidated = 0
            waiting = 1
            skipped = 0
            signals_written = 2
            errors = []
            skipped_reason = ""
            run_time = "2026-05-30T10:30:00"
            details = [{"symbol": "002353.SZ", "observe_state": "TRIGGERED"}]

        def fake_run_observe(trade_date, db_path):
            assert trade_date == "2026-05-30"
            assert db_path == tf_db
            return DummyResult()

        monkeypatch.setattr("tradingagents.tradeflow.observe_runner.run_observe", fake_run_observe)

        result = run_observe_check("2026-05-30", tf_db_path=tf_db)
        assert result["status"] == "ok"
        assert result["checked"] == 2
        assert result["triggered"] == 1
        assert result["signals_written"] == 2

    def test_run_observe_check_surfaces_skipped_status(self, tf_db, monkeypatch):
        class DummyResult:
            checked = 0
            triggered = 0
            invalidated = 0
            waiting = 0
            skipped = 0
            signals_written = 0
            errors = []
            skipped_reason = "2026-05-31 非交易日，跳过实时观察"
            run_time = "2026-05-31T10:30:00"
            details = []

        monkeypatch.setattr("tradingagents.tradeflow.observe_runner.run_observe", lambda trade_date, db_path: DummyResult())

        result = run_observe_check("2026-05-31", tf_db_path=tf_db)
        assert result["status"] == "skipped"
        assert result["skipped_reason"] == "2026-05-31 非交易日，跳过实时观察"


class TestTAQueue:
    def test_no_db_returns_no_data(self):
        result = get_ta_queue("2026-05-30", tf_db_path="/nonexistent/path.db")
        assert result["status"] == "no_data"

    def test_returns_queue(self, populated_db):
        result = get_ta_queue("2026-05-30", tf_db_path=populated_db)
        assert result["status"] == "ok"
        assert result["total_in_queue"] == 1
        assert result["pending_count"] == 1
        item = result["queue"][0]
        assert item["symbol"] == "002353.SZ"
        assert item["need_deep_ta"] is True
        assert item["deep_ta_status"] == "PENDING"

    def test_no_deep_ta_candidates(self, tf_db):
        c = Candidate(symbol="603256.SH", name="测试", trade_date="2026-05-30", need_deep_ta=False, tier="C")
        save_candidate(c, tf_db)
        result = get_ta_queue("2026-05-30", tf_db_path=tf_db)
        assert result["status"] == "ok"
        assert result["total_in_queue"] == 0


class TestReview:
    def test_no_db_returns_no_data(self):
        result = get_review("2026-05-30", tf_db_path="/nonexistent/path.db")
        assert result["status"] == "no_data"

    def test_returns_review(self, populated_db):
        result = get_review("2026-05-30", tf_db_path=populated_db)
        assert result["status"] == "ok"
        assert len(result["results"]) == 2
        assert result["reviewed_at"] != ""
        assert result["summary_agg"]["total_candidates"] == 2

    def test_review_item_fields(self, populated_db):
        result = get_review("2026-05-30", tf_db_path=populated_db)
        item = result["results"][0]
        assert "symbol" in item
        assert "plan_action" in item
        assert "keep_observing" in item
        assert "reason" in item

    def test_remove_from_watch(self, populated_db):
        conn = sqlite3.connect(populated_db)
        conn.execute(
            "UPDATE tradeflow_daily_plans SET candidates_json = ? WHERE trade_date = '2026-05-30'",
            (
                json.dumps(
                    [
                        {
                            "symbol": "002353.SZ",
                            "action": "REMOVE_FROM_WATCH",
                            "tier": "C",
                            "observe_state": "EXPIRED",
                            "strategy_tags": ["VCP"],
                        }
                    ]
                ),
            ),
        )
        conn.commit()
        conn.close()

        result = get_review("2026-05-30", tf_db_path=populated_db)
        assert result["status"] == "ok"
        assert result["results"][0]["keep_observing"] is False
        assert "移除" in result["results"][0]["reason"]


class TestDataHealth:
    def test_no_db(self):
        result = get_data_health(tf_db_path="/nonexistent/path.db")
        assert result["status"] == "ok"
        assert result["tradeflow_db_available"] is False
        assert result["sources"][0]["status"] == "FAILED"

    def test_with_db(self, populated_db):
        result = get_data_health(tf_db_path=populated_db)
        assert result["status"] == "ok"
        assert result["tradeflow_db_available"] is True
        assert result["evidence_contract_available"] is True
        assert len(result["sources"]) >= 3
        source_names = [s["name"] for s in result["sources"]]
        assert "tradeflow_daily_plans" in source_names
        assert "tradeflow_candidates" in source_names
        assert "tradeflow_signals" in source_names
        assert all(s["status"] == "OK" for s in result["sources"])
        assert all("fallback_vendor" in s for s in result["sources"])

    def test_latest_dates(self, populated_db):
        result = get_data_health(tf_db_path=populated_db)
        assert result["latest_plan_date"] == "2026-05-30"
        assert result["latest_candidates_date"] == "2026-05-30"

    def test_signal_times_are_exposed_and_schema_preserves_them(self, populated_db):
        conn = sqlite3.connect(populated_db)
        conn.execute(
            """
            INSERT INTO tradeflow_signals (
                signal_time, symbol, signal_type, signal_level, source, evidence_json, action_hint, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-05-30T10:30:00",
                "002353.SZ",
                "observe_check",
                "info",
                "observe_runner",
                json.dumps({"trade_date": "2026-05-30", "current_price": 44.2}),
                "OBSERVE",
                "new",
                "2026-05-30T10:30:00",
            ),
        )
        conn.commit()
        conn.close()

        result = get_data_health(tf_db_path=populated_db)
        assert result["latest_observe_check_time"] == "2026-05-30T10:30:00"
        assert result["latest_signal_time"] == "2026-05-30T10:30:00"

        model = TradeFlowDataHealthResponse(**result)
        assert model.latest_observe_check_time == "2026-05-30T10:30:00"
        assert model.latest_signal_time == "2026-05-30T10:30:00"


class TestNoForbiddenWords:
    def test_action_values_only_allowed(self, populated_db):
        from tradingagents.tradeflow.schemas import ALLOWED_ACTIONS, FORBIDDEN_WORDS

        result = get_candidates("2026-05-30", tf_db_path=populated_db)
        for c in result["candidates"]:
            assert c["action"] in ALLOWED_ACTIONS

    def test_no_strong_buy_sell_words(self, populated_db):
        from tradingagents.tradeflow.schemas import FORBIDDEN_WORDS

        result = get_candidates("2026-05-30", tf_db_path=populated_db)
        for c in result["candidates"]:
            for word in FORBIDDEN_WORDS:
                assert word not in c.get("reason", "")


# [UI-007] tradeflow_filtered_trace
class TestFilteredSymbols:
    def test_save_and_retrieve_filtered(self, tf_db):
        filtered = [
            {"symbol": "000001.SZ", "name": "平安银行", "source": "manual", "reason": "流动性差(avg_amount=0.01亿)"},
            {"symbol": "000002.SZ", "name": "万科A", "source": "watchlist", "reason": "数据缺失"},
            {"symbol": "600000.SH", "name": "浦发银行", "source": "holding", "reason": "无策略命中"},
        ]
        count = save_filtered_symbols(filtered, "2026-05-31", "run001", tf_db)
        assert count == 3

        results = get_filtered_symbols("2026-05-31", tf_db)
        assert len(results) == 3
        symbols = {r["symbol"] for r in results}
        assert symbols == {"000001.SZ", "000002.SZ", "600000.SH"}
        for r in results:
            assert "reason" in r
            assert "source" in r
            assert r["run_id"] == "run001"

    def test_query_by_date(self, tf_db):
        save_filtered_symbols(
            [{"symbol": "000001.SZ", "name": "", "source": "manual", "reason": "test"}],
            "2026-05-31", "run1", tf_db,
        )
        save_filtered_symbols(
            [{"symbol": "000002.SZ", "name": "", "source": "manual", "reason": "test"}],
            "2026-06-01", "run2", tf_db,
        )
        assert len(get_filtered_symbols("2026-05-31", tf_db)) == 1
        assert len(get_filtered_symbols("2026-06-01", tf_db)) == 1

    def test_overwrite_same_date(self, tf_db):
        save_filtered_symbols(
            [{"symbol": "000001.SZ", "name": "", "source": "manual", "reason": "old"}],
            "2026-05-31", "run1", tf_db,
        )
        assert len(get_filtered_symbols("2026-05-31", tf_db)) == 1

        save_filtered_symbols(
            [
                {"symbol": "000002.SZ", "name": "", "source": "manual", "reason": "new1"},
                {"symbol": "000003.SZ", "name": "", "source": "manual", "reason": "new2"},
            ],
            "2026-05-31", "run2", tf_db,
        )
        results = get_filtered_symbols("2026-05-31", tf_db)
        assert len(results) == 2
        symbols = {r["symbol"] for r in results}
        assert symbols == {"000002.SZ", "000003.SZ"}

    def test_empty_data(self, tf_db):
        results = get_filtered_symbols("2026-05-31", tf_db)
        assert results == []

    def test_empty_filtered_list(self, tf_db):
        count = save_filtered_symbols([], "2026-05-31", "run1", tf_db)
        assert count == 0


class TestFilteredAPI:
    def test_no_db_returns_ok_empty(self):
        result = get_filtered("2026-05-31", tf_db_path="/nonexistent/path.db")
        assert result["status"] == "ok"
        assert result["filtered"] == []
        assert result["filter_breakdown"] == {}

    def test_returns_filtered_with_breakdown(self, tf_db):
        filtered = [
            {"symbol": "000001.SZ", "name": "平安银行", "source": "manual", "reason": "流动性差(avg_amount=0.01亿)"},
            {"symbol": "000002.SZ", "name": "万科A", "source": "watchlist", "reason": "数据不足(需至少40日)"},
            {"symbol": "600000.SH", "name": "浦发银行", "source": "holding", "reason": "无策略命中"},
            {"symbol": "601398.SH", "name": "工商银行", "source": "manual", "reason": "流动性差(avg_amount=0.02亿)"},
        ]
        save_filtered_symbols(filtered, "2026-05-31", "run001", tf_db)

        result = get_filtered("2026-05-31", tf_db_path=tf_db)
        assert result["status"] == "ok"
        assert len(result["filtered"]) == 4
        assert result["filter_breakdown"]["流动性差"] == 2
        assert result["filter_breakdown"]["数据缺失"] == 1
        assert result["filter_breakdown"]["无策略命中"] == 1

    def test_empty_date(self, tf_db):
        result = get_filtered("2020-01-01", tf_db_path=tf_db)
        assert result["status"] == "ok"
        assert result["filtered"] == []


class TestDiscoveryPersistsFiltered:
    def test_discovery_persists_filtered_symbols(self, tf_db, monkeypatch):
        def fake_evaluate_symbol(**kwargs):
            sym = kwargs.get("symbol", "")
            if sym == "000001.SZ":
                c = Candidate(
                    symbol="000001.SZ", name="平安银行", source="manual",
                    strategy_tags=["VCP"], score=60.0, composite_score=70.0,
                    trade_date="2026-05-31", tier="B",
                )
                c.signals = [CandidateSignal(strategy_tag="VCP", score=60.0, reason="test")]
                return c, ""
            if sym == "000002.SZ":
                return None, "流动性差(avg_amount=0.01亿)"
            if sym == "600000.SH":
                return None, "无策略命中"
            return None, "数据缺失"

        monkeypatch.setattr("tradingagents.tradeflow.discovery.evaluate_symbol", fake_evaluate_symbol)

        result = run_discovery_scan(
            trade_date="2026-05-31",
            symbols=["000001.SZ", "000002.SZ", "600000.SH"],
            top_n=10,
            include_holdings=False,
            include_watchlist=False,
            tf_db_path=tf_db,
            prod_db_path="/nonexistent/tradingagents.db",
        )

        assert result["status"] == "ok"
        assert result["candidate_count"] == 1
        assert result["filtered_count"] == 2

        filtered_result = get_filtered("2026-05-31", tf_db_path=tf_db)
        assert filtered_result["status"] == "ok"
        assert len(filtered_result["filtered"]) == 2
        symbols = {f["symbol"] for f in filtered_result["filtered"]}
        assert "000002.SZ" in symbols
        assert "600000.SH" in symbols

    def test_second_discovery_overwrites_filtered(self, tf_db, monkeypatch):
        def fake_evaluate_v1(**kwargs):
            sym = kwargs.get("symbol", "")
            if sym == "000001.SZ":
                c = Candidate(symbol="000001.SZ", name="test", strategy_tags=["VCP"], score=60.0, composite_score=70.0, trade_date="2026-05-31")
                c.signals = [CandidateSignal(strategy_tag="VCP", score=60.0, reason="test")]
                return c, ""
            return None, "流动性差"

        monkeypatch.setattr("tradingagents.tradeflow.discovery.evaluate_symbol", fake_evaluate_v1)
        run_discovery_scan(
            trade_date="2026-05-31", symbols=["000001.SZ", "000002.SZ"],
            top_n=10, include_holdings=False, include_watchlist=False,
            tf_db_path=tf_db, prod_db_path="/nonexistent/tradingagents.db",
        )
        assert len(get_filtered("2026-05-31", tf_db_path=tf_db)["filtered"]) == 1

        def fake_evaluate_v2(**kwargs):
            return None, "数据缺失"

        monkeypatch.setattr("tradingagents.tradeflow.discovery.evaluate_symbol", fake_evaluate_v2)
        run_discovery_scan(
            trade_date="2026-05-31", symbols=["000001.SZ", "000002.SZ", "000003.SZ"],
            top_n=10, include_holdings=False, include_watchlist=False,
            tf_db_path=tf_db, prod_db_path="/nonexistent/tradingagents.db",
        )

        filtered_result = get_filtered("2026-05-31", tf_db_path=tf_db)
        assert len(filtered_result["filtered"]) == 3
        assert all(f["reason"] == "数据缺失" for f in filtered_result["filtered"])
