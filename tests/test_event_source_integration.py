"""[N-001] Integration tests: event_source → candidate scanning pipeline.

Tests that fetch_daily_events() integrates correctly with the plan runner
and universe builder so that event-discovered symbols enter the candidate pool
and each symbol only receives its own event titles.

Updated for [T-006]: plan_runner now uses fetch_daily_events_detailed().
"""

import sys
import os
import tempfile
from unittest.mock import patch, MagicMock, call

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.tradeflow.schemas import Candidate, CandidateSignal
from tradingagents.tradeflow.plan_runner import generate_daily_plan
from tradingagents.tradeflow.universe import build_universe
from tradingagents.tradeflow.candidate_engine import init_db
from tradingagents.tradeflow.event_source import EventItem, EventSourceResult, EventSourceStatus


MOCK_EVENTS = {
    "002138": ["回购进展公告"],
    "600519": ["减持评级"],
}

MOCK_ITEMS_BY_SYMBOL = {
    "002138": [EventItem(symbol="002138", title="回购进展公告", event_type="buyback", direction="bullish", source="eastmoney")],
    "600519": [EventItem(symbol="600519", title="减持评级", event_type="rating", direction="bearish", source="cninfo")],
}


def _make_event_result(events_map=None, items_by_symbol=None):
    ev_map = events_map or {}
    items = items_by_symbol or {}
    return EventSourceResult(
        status=EventSourceStatus.OK,
        events_map=ev_map,
        items_by_symbol=items,
        event_count=sum(len(v) for v in items.values()),
        symbols_count=len(items),
    )


def _mock_candidate(symbol, name="", score=60.0):
    c = Candidate(
        symbol=symbol,
        name=name,
        source="event_source",
        strategy_tags=["EVENT_CATALYST"],
        score=score,
        trade_date="2026-05-28",
    )
    c.signals = [
        CandidateSignal(strategy_tag="EVENT_CATALYST", score=score, reason="事件驱动")
    ]
    c.primary_strategy = "EVENT_CATALYST"
    return c


class TestUniverseEventSymbols:
    def test_event_symbols_enter_universe(self):
        """Event-discovered symbols appear in universe even if not in holdings/watchlist."""
        universe = build_universe(
            symbols=[],
            event_symbols=MOCK_EVENTS,
        )
        syms = {item["symbol"] for item in universe}
        assert "002138" in syms
        assert "600519" in syms

    def test_event_symbols_source_tag(self):
        """Event-discovered symbols have source='event_source'."""
        universe = build_universe(
            symbols=[],
            event_symbols=MOCK_EVENTS,
        )
        by_sym = {item["symbol"]: item for item in universe}
        assert by_sym["002138"]["source"] == "event_source"
        assert by_sym["600519"]["source"] == "event_source"

    def test_event_symbols_no_duplicate_with_manual(self):
        """If symbol already in universe via manual list, don't duplicate."""
        universe = build_universe(
            symbols=["002138"],
            event_symbols=MOCK_EVENTS,
        )
        count = sum(1 for item in universe if item["symbol"] == "002138")
        assert count == 1

    def test_event_symbols_none_default(self):
        """Passing None (default) doesn't break anything."""
        universe = build_universe(
            symbols=["002138"],
            event_symbols=None,
        )
        assert len(universe) == 1
        assert universe[0]["symbol"] == "002138"

    def test_event_symbols_empty_dict(self):
        """Empty event_symbols dict doesn't break anything."""
        universe = build_universe(
            symbols=["002138"],
            event_symbols={},
        )
        assert len(universe) == 1

    def test_event_symbols_no_overlap_with_holdings(self):
        """Manual symbol keeps its original source, event symbol adds new."""
        universe = build_universe(
            symbols=["002138"],
            event_symbols={"600519": ["评级"]},
        )
        by_sym = {item["symbol"]: item for item in universe}
        assert by_sym["002138"]["source"] == "manual"
        assert by_sym["600519"]["source"] == "event_source"


class TestPlanRunnerEventSource:
    def test_use_event_source_false_does_not_call_fetch(self):
        """Default use_event_source=False should NOT call fetch_daily_events_detailed."""
        with patch("tradingagents.tradeflow.event_source.fetch_daily_events_detailed") as mock_fetch:
            with patch("tradingagents.tradeflow.plan_runner.evaluate_symbol") as mock_eval:
                mock_eval.return_value = (None, "无策略命中")
                plan = generate_daily_plan(
                    trade_date="2026-05-28",
                    symbols=["002138"],
                    candidates=None,
                )
            mock_fetch.assert_not_called()

    def test_use_event_source_true_calls_fetch(self):
        """use_event_source=True should call fetch_daily_events_detailed with YYYYMMDD."""
        with patch("tradingagents.tradeflow.event_source.fetch_daily_events_detailed") as mock_fetch:
            mock_fetch.return_value = _make_event_result(MOCK_EVENTS, MOCK_ITEMS_BY_SYMBOL)
            with patch("tradingagents.tradeflow.plan_runner.evaluate_symbol") as mock_eval:
                mock_eval.return_value = (None, "无策略命中")
                plan = generate_daily_plan(
                    trade_date="2026-05-28",
                    symbols=[],
                    candidates=None,
                    use_event_source=True,
                )
            mock_fetch.assert_called_once_with("20260528")

    def test_event_symbols_in_universe_when_enabled(self):
        """When use_event_source=True, event symbols enter the universe."""
        with patch("tradingagents.tradeflow.event_source.fetch_daily_events_detailed") as mock_fetch:
            mock_fetch.return_value = _make_event_result(MOCK_EVENTS, MOCK_ITEMS_BY_SYMBOL)
            with patch("tradingagents.tradeflow.plan_runner.evaluate_symbol") as mock_eval:
                mock_eval.return_value = (None, "无策略命中")
                plan = generate_daily_plan(
                    trade_date="2026-05-28",
                    symbols=[],
                    candidates=None,
                    use_event_source=True,
                )
            evaluated_symbols = [c.kwargs["symbol"] for c in mock_eval.call_args_list]
            assert "002138" in evaluated_symbols
            assert "600519" in evaluated_symbols

    def test_each_symbol_gets_own_events_only(self):
        """Each symbol should only receive its own event titles — no cross-contamination."""
        with patch("tradingagents.tradeflow.event_source.fetch_daily_events_detailed") as mock_fetch:
            mock_fetch.return_value = _make_event_result(MOCK_EVENTS, MOCK_ITEMS_BY_SYMBOL)
            with patch("tradingagents.tradeflow.plan_runner.evaluate_symbol") as mock_eval:
                mock_eval.return_value = (None, "无策略命中")
                plan = generate_daily_plan(
                    trade_date="2026-05-28",
                    symbols=[],
                    candidates=None,
                    use_event_source=True,
                )
            for call_item in mock_eval.call_args_list:
                sym = call_item.kwargs.get("symbol", call_item.args[0] if call_item.args else "")
                news = call_item.kwargs.get("news_texts")
                if sym == "002138":
                    assert news == ["回购进展公告"]
                elif sym == "600519":
                    assert news == ["减持评级"]

    def test_empty_events_map_does_not_break(self):
        """Empty event map should not break the flow."""
        with patch("tradingagents.tradeflow.event_source.fetch_daily_events_detailed") as mock_fetch:
            mock_fetch.return_value = _make_event_result()
            with patch("tradingagents.tradeflow.plan_runner.evaluate_symbol") as mock_eval:
                mock_eval.return_value = (None, "无策略命中")
                plan = generate_daily_plan(
                    trade_date="2026-05-28",
                    symbols=["002138"],
                    candidates=None,
                    use_event_source=True,
                )
            assert plan is not None
            assert plan.trade_date == "2026-05-28"

    def test_event_source_candidates_appear_in_plan(self):
        """Event-discovered symbols that pass evaluation appear in the plan."""
        with patch("tradingagents.tradeflow.event_source.fetch_daily_events_detailed") as mock_fetch:
            mock_fetch.return_value = _make_event_result(MOCK_EVENTS, MOCK_ITEMS_BY_SYMBOL)
            with patch("tradingagents.tradeflow.plan_runner.evaluate_symbol") as mock_eval:
                def fake_eval(symbol, **kwargs):
                    if symbol in ("002138", "600519"):
                        c = _mock_candidate(symbol, score=65.0)
                        return c, ""
                    return None, "无策略命中"
                mock_eval.side_effect = fake_eval
                plan = generate_daily_plan(
                    trade_date="2026-05-28",
                    symbols=[],
                    candidates=None,
                    use_event_source=True,
                )
            plan_symbols = {e["symbol"] for e in plan.candidates}
            assert "002138" in plan_symbols
            assert "600519" in plan_symbols

    def test_manual_symbols_still_evaluated_with_events(self):
        """Manual symbols still get evaluated, and also receive their events if present."""
        mixed_events = {"002138": ["回购进展公告"], "300999": ["新股事件"]}
        mixed_items = {
            "002138": [EventItem(symbol="002138", title="回购进展公告", event_type="buyback", direction="bullish", source="eastmoney")],
            "300999": [EventItem(symbol="300999", title="新股事件", event_type="notice", direction="neutral", source="eastmoney")],
        }
        with patch("tradingagents.tradeflow.event_source.fetch_daily_events_detailed") as mock_fetch:
            mock_fetch.return_value = _make_event_result(mixed_events, mixed_items)
            with patch("tradingagents.tradeflow.plan_runner.evaluate_symbol") as mock_eval:
                def fake_eval(symbol, **kwargs):
                    c = _mock_candidate(symbol, score=55.0)
                    return c, ""
                mock_eval.side_effect = fake_eval
                plan = generate_daily_plan(
                    trade_date="2026-05-28",
                    symbols=["002138", "300999"],
                    candidates=None,
                    use_event_source=True,
                )
            for call_item in mock_eval.call_args_list:
                sym = call_item.kwargs.get("symbol", call_item.args[0] if call_item.args else "")
                news = call_item.kwargs.get("news_texts")
                if sym == "002138":
                    assert news == ["回购进展公告"]
                elif sym == "300999":
                    assert news == ["新股事件"]

    def test_symbol_without_events_gets_none_news(self):
        """Symbol in universe without events in the map gets no event news_texts."""
        events_only_one = {"002138": ["回购进展公告"]}
        items_only_one = {
            "002138": [EventItem(symbol="002138", title="回购进展公告", event_type="buyback", direction="bullish", source="eastmoney")],
        }
        with patch("tradingagents.tradeflow.event_source.fetch_daily_events_detailed") as mock_fetch:
            mock_fetch.return_value = _make_event_result(events_only_one, items_only_one)
            with patch("tradingagents.tradeflow.plan_runner.evaluate_symbol") as mock_eval:
                mock_eval.return_value = (None, "无策略命中")
                plan = generate_daily_plan(
                    trade_date="2026-05-28",
                    symbols=["002138", "600519"],
                    candidates=None,
                    use_event_source=True,
                )
            for call_item in mock_eval.call_args_list:
                sym = call_item.kwargs.get("symbol", call_item.args[0] if call_item.args else "")
                news = call_item.kwargs.get("news_texts")
                if sym == "600519":
                    assert news is None
