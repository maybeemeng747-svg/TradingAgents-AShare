"""Tests for TradeFlow plan runner — plan generation, validation, forbidden words."""

import sys
import os
import tempfile
from unittest.mock import patch, MagicMock
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.tradeflow.schemas import Candidate, CandidateSignal, DailyPlan, FORBIDDEN_WORDS
from tradingagents.tradeflow.plan_runner import generate_daily_plan, _action_for_candidate, _build_plan_entry
from tradingagents.tradeflow.candidate_engine import init_db


def _make_candidate(symbol="002353.SZ", strategies=None, score=55.0, need_deep_ta=False):
    c = Candidate(symbol=symbol, name="测试", strategy_tags=strategies or ["VCP"],
                  score=score, need_deep_ta=need_deep_ta,
                  trigger_price=100.0, invalid_price=90.0)
    c.signals = [CandidateSignal(strategy_tag=s, score=score, reason="测试原因")
                 for s in (strategies or ["VCP"])]
    return c


class TestActionForCandidate:
    def test_deep_ta_action(self):
        c = _make_candidate(need_deep_ta=True)
        assert _action_for_candidate(c) == "NEED_DEEP_TA"

    def test_observe_action(self):
        c = _make_candidate(need_deep_ta=False)
        # Default should be OBSERVE unless trigger is close
        action = _action_for_candidate(c)
        assert action in {"OBSERVE", "WAIT_TRIGGER"}


class TestBuildPlanEntry:
    def test_entry_structure(self):
        c = _make_candidate(strategies=["VCP", "PULLBACK_SUPPORT"], score=65.0)
        c.primary_strategy = "VCP"
        entry = _build_plan_entry(c)
        assert entry["symbol"] == "002353.SZ"
        assert entry["action"] in {"OBSERVE", "WAIT_TRIGGER", "NEED_DEEP_TA", "REMOVE_FROM_WATCH"}
        assert set(entry["strategies"]) == {"VCP", "PULLBACK_SUPPORT"}
        assert entry["primary_strategy"] == "VCP"

    def test_no_forbidden_words_in_reason(self):
        c = _make_candidate()
        c.signals[0].reason = "建议立即买入该标的"
        entry = _build_plan_entry(c)
        for word in FORBIDDEN_WORDS:
            assert word not in entry["reason"]


class TestGenerateDailyPlan:
    def test_with_provided_candidates(self):
        candidates = [
            _make_candidate("002353.SZ", ["VCP"], 55.0),
            _make_candidate("603256.SH", ["PULLBACK_SUPPORT"], 60.0, need_deep_ta=True),
        ]
        plan = generate_daily_plan(
            trade_date="2026-05-25",
            candidates=candidates,
        )
        assert plan.trade_date == "2026-05-25"
        assert len(plan.candidates) == 2
        # Sorted by score descending
        assert plan.candidates[0]["score"] >= plan.candidates[1]["score"]

    def test_plan_no_forbidden_words(self):
        candidates = [
            _make_candidate("002353.SZ", ["VCP"], 55.0),
        ]
        plan = generate_daily_plan(
            trade_date="2026-05-25",
            candidates=candidates,
        )
        text = plan.render_text()
        for word in FORBIDDEN_WORDS:
            assert word not in text

    def test_plan_validates(self):
        candidates = [
            _make_candidate("002353.SZ", ["VCP"], 55.0),
        ]
        plan = generate_daily_plan(
            trade_date="2026-05-25",
            candidates=candidates,
        )
        issues = plan.validate()
        assert issues == []

    def test_empty_candidates(self):
        plan = generate_daily_plan(
            trade_date="2026-05-25",
            candidates=[],
        )
        assert len(plan.candidates) == 0
        assert "0只" in plan.summary or "共0只" in plan.summary

    def test_deep_ta_counted_in_summary(self):
        candidates = [
            _make_candidate("A.SZ", need_deep_ta=True),
            _make_candidate("B.SZ", need_deep_ta=False),
        ]
        plan = generate_daily_plan(
            trade_date="2026-05-25",
            candidates=candidates,
        )
        assert "1只需要深度TA" in plan.summary


class TestPlanPersistence:
    def test_save_and_read_plan(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            init_db(db_path)
            from tradingagents.tradeflow.plan_runner import save_plan

            plan = DailyPlan(
                trade_date="2026-05-25",
                mode="pre_market",
                summary="测试计划",
                candidates=[{"symbol": "002353.SZ", "action": "OBSERVE"}],
            )
            row_id = save_plan(plan, db_path)
            assert row_id > 0

            import sqlite3
            conn = sqlite3.connect(db_path)
            row = conn.execute(
                "SELECT * FROM tradeflow_daily_plans WHERE trade_date='2026-05-25'"
            ).fetchone()
            conn.close()
            assert row is not None
        finally:
            os.unlink(db_path)

    def test_save_candidates_persisted(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            init_db(db_path)
            candidates = [
                _make_candidate("002353.SZ", ["VCP"], 55.0),
            ]
            plan = generate_daily_plan(
                trade_date="2026-05-25",
                candidates=candidates,
                tf_db_path=db_path,
                save_candidates=True,
            )

            import sqlite3
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM tradeflow_candidates WHERE symbol='002353.SZ' AND trade_date='2026-05-25'"
            ).fetchone()
            conn.close()
            assert row is not None
            assert row["score"] == 55.0
        finally:
            os.unlink(db_path)

    def test_save_candidates_disabled(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            init_db(db_path)
            candidates = [
                _make_candidate("002353.SZ", ["VCP"], 55.0),
            ]
            plan = generate_daily_plan(
                trade_date="2026-05-25",
                candidates=candidates,
                tf_db_path=db_path,
                save_candidates=False,
            )

            import sqlite3
            conn = sqlite3.connect(db_path)
            count = conn.execute(
                "SELECT COUNT(*) FROM tradeflow_candidates WHERE symbol='002353.SZ'"
            ).fetchone()[0]
            conn.close()
            assert count == 0
        finally:
            os.unlink(db_path)


class TestEventOverridesInUniverse:
    def test_event_overrides_enter_universe(self):
        event_overrides = [
            {"symbol": "999999.SZ", "name": "事件股", "event_type": "buyback", "direction": "bullish", "title": "回购"},
        ]
        mock_candidate = Candidate(
            symbol="999999.SZ", name="事件股", source="event_catalyst",
            strategy_tags=["EVENT_CATALYST"], score=60.0, trade_date="2026-05-25",
        )
        mock_candidate.signals = [CandidateSignal(strategy_tag="EVENT_CATALYST", score=60.0, reason="test")]
        mock_candidate.primary_strategy = "EVENT_CATALYST"

        with patch("tradingagents.tradeflow.plan_runner.evaluate_symbol", return_value=(mock_candidate, "")):
            plan = generate_daily_plan(
                trade_date="2026-05-25",
                symbols=None,
                event_overrides=event_overrides,
                candidates=None,
            )
        symbols_in_plan = [e["symbol"] for e in plan.candidates]
        assert "999999.SZ" in symbols_in_plan

    def test_event_overrides_symbol_not_in_watchlist(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tf_db = f.name
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f2:
            prod_db = f2.name
        try:
            init_db(tf_db)
            event_overrides = [
                {"symbol": "888888.SZ", "name": "纯事件股", "event_type": "earnings", "direction": "bullish", "title": "业绩超预期"},
            ]
            mock_candidate = Candidate(
                symbol="888888.SZ", name="纯事件股", source="event_catalyst",
                strategy_tags=["EVENT_CATALYST"], score=70.0, trade_date="2026-05-25",
            )
            mock_candidate.signals = [CandidateSignal(strategy_tag="EVENT_CATALYST", score=70.0, reason="test")]
            mock_candidate.primary_strategy = "EVENT_CATALYST"

            with patch("tradingagents.tradeflow.plan_runner.evaluate_symbol", return_value=(mock_candidate, "")):
                plan = generate_daily_plan(
                    trade_date="2026-05-25",
                    symbols=None,
                    prod_db_path=prod_db,
                    tf_db_path=tf_db,
                    event_overrides=event_overrides,
                    candidates=None,
                )
            symbols_in_plan = [e["symbol"] for e in plan.candidates]
            assert "888888.SZ" in symbols_in_plan
        finally:
            os.unlink(tf_db)
            os.unlink(prod_db)

    def test_event_overrides_no_duplicate(self):
        event_overrides = [
            {"symbol": "002353.SZ", "name": "重复股", "event_type": "buyback", "direction": "bullish", "title": "回购"},
        ]
        mock_candidate = Candidate(
            symbol="002353.SZ", name="重复股", source="event_catalyst",
            strategy_tags=["EVENT_CATALYST"], score=65.0, trade_date="2026-05-25",
        )
        mock_candidate.signals = [CandidateSignal(strategy_tag="EVENT_CATALYST", score=65.0, reason="test")]
        mock_candidate.primary_strategy = "EVENT_CATALYST"

        with patch("tradingagents.tradeflow.plan_runner.evaluate_symbol", return_value=(mock_candidate, "")):
            plan = generate_daily_plan(
                trade_date="2026-05-25",
                symbols=["002353.SZ"],
                event_overrides=event_overrides,
                candidates=None,
            )
        count = sum(1 for e in plan.candidates if e["symbol"] == "002353.SZ")
        assert count == 1


class TestEventOverridesSymbolFiltering:
    def test_plan_multi_event_overrides_per_symbol(self):
        """Each candidate should only see its own event_overrides."""
        from unittest.mock import call

        overrides = [
            {"symbol": "AAA.SZ", "event_type": "buyback", "direction": "bullish", "title": "AAA回购"},
            {"symbol": "BBB.SZ", "event_type": "earnings", "direction": "bullish", "title": "BBB业绩"},
        ]

        def fake_evaluate(symbol, **kwargs):
            ev_list = kwargs.get("event_overrides") or []
            if symbol == "AAA.SZ":
                assert len(ev_list) == 1
                assert ev_list[0]["symbol"] == "AAA.SZ"
                c = Candidate(symbol="AAA.SZ", name="AAA", source="event_catalyst",
                              strategy_tags=["EVENT_CATALYST"], score=60.0, trade_date="2026-05-25")
                c.signals = [CandidateSignal(strategy_tag="EVENT_CATALYST", score=60.0, reason="test")]
                c.primary_strategy = "EVENT_CATALYST"
                return c, ""
            elif symbol == "BBB.SZ":
                assert len(ev_list) == 1
                assert ev_list[0]["symbol"] == "BBB.SZ"
                c = Candidate(symbol="BBB.SZ", name="BBB", source="event_catalyst",
                              strategy_tags=["EVENT_CATALYST"], score=55.0, trade_date="2026-05-25")
                c.signals = [CandidateSignal(strategy_tag="EVENT_CATALYST", score=55.0, reason="test")]
                c.primary_strategy = "EVENT_CATALYST"
                return c, ""
            return None, "无策略命中"

        with patch("tradingagents.tradeflow.plan_runner.evaluate_symbol", side_effect=fake_evaluate):
            with patch("tradingagents.tradeflow.plan_runner.build_universe", return_value=[
                {"symbol": "AAA.SZ", "name": "AAA", "source": "manual"},
                {"symbol": "BBB.SZ", "name": "BBB", "source": "manual"},
            ]):
                plan = generate_daily_plan(
                    trade_date="2026-05-25",
                    event_overrides=overrides,
                    candidates=None,
                )
        symbols_in_plan = {e["symbol"] for e in plan.candidates}
        assert symbols_in_plan == {"AAA.SZ", "BBB.SZ"}
