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
)


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
            trade_date="2026-05-31",
            tier="A",
            tradeflow_data_completeness=0.8,
        )
        c.signals = [CandidateSignal(strategy_tag="VCP", score=60.0, reason="缩量整理")]

        def fake_evaluate_symbol(**kwargs):
            return c, ""

        monkeypatch.setattr("tradingagents.tradeflow.discovery.evaluate_symbol", fake_evaluate_symbol)

        result = run_discovery_scan(
            trade_date="2026-05-31",
            symbols=["002353.SZ"],
            top_n=10,
            include_holdings=False,
            include_watchlist=False,
            tf_db_path=tf_db,
            prod_db_path="/nonexistent/tradingagents.db",
        )

        assert result["status"] == "ok"
        assert result["candidate_count"] == 1
        saved = get_candidates("2026-05-31", tf_db_path=tf_db)
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
