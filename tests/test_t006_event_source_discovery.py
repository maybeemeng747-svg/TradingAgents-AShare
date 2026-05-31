"""[T-006] Integration tests: event source auto-integration with Discovery / Daily Plan.

Tests that:
1. fetch_daily_events_detailed() returns EventSourceResult with status tracking
2. Event source failure is tracked as FAILED status, system still processes manual symbols
3. Event items generate event_overrides passed to candidate evaluation
4. Daily Plan and Discovery outputs include event metadata (source, type, title, dedup)
5. Symbols are isolated — no cross-contamination of event data
6. Event source metadata appears in plan/discovery result metadata

[T-007] event_source_failure_status extended tests:
7. Sub-fetch failure is visible in source_statuses and failed_sources
8. All sub-fetches fail → FAILED; partial → PARTIAL; all succeed → OK
9. Discovery / Daily Plan metadata shows per-source failure info
10. Sensitive info (API keys) in error messages is sanitized
"""

import sys
import os
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.tradeflow.event_source import (
    EventItem,
    EventSourceStatus,
    EventSourceResult,
    fetch_daily_events_detailed,
)
from tradingagents.tradeflow.schemas import Candidate, CandidateSignal
from tradingagents.tradeflow.plan_runner import generate_daily_plan
from tradingagents.tradeflow.discovery import run_discovery


MOCK_EVENT_ITEMS = {
    "002138.SZ": [
        EventItem(
            symbol="002138.SZ",
            name="顺络电子",
            event_type="buyback",
            title="回购进展公告",
            direction="bullish",
            date="2026-05-28",
            source="eastmoney",
        ),
        EventItem(
            symbol="002138.SZ",
            name="顺络电子",
            event_type="notice",
            title="关于回购实施进展",
            direction="neutral",
            date="2026-05-28",
            source="eastmoney",
        ),
    ],
    "600519.SH": [
        EventItem(
            symbol="600519.SH",
            name="贵州茅台",
            event_type="rating",
            title="减持评级",
            direction="bearish",
            date="2026-05-28",
            source="cninfo",
        ),
    ],
}


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


def _make_event_result(status=EventSourceStatus.OK, error_message=""):
    return EventSourceResult(
        status=status,
        events_map={
            sym: [it.title for it in items]
            for sym, items in MOCK_EVENT_ITEMS.items()
        },
        items_by_symbol=MOCK_EVENT_ITEMS,
        error_message=error_message,
        event_count=sum(len(v) for v in MOCK_EVENT_ITEMS.values()),
        symbols_count=len(MOCK_EVENT_ITEMS),
    )


class TestEventSourceResult:
    def test_default_status_ok(self):
        r = EventSourceResult()
        assert r.status == EventSourceStatus.OK
        assert r.events_map == {}
        assert r.items_by_symbol == {}
        assert r.event_count == 0
        assert r.symbols_count == 0

    def test_failed_status(self):
        r = EventSourceResult(
            status=EventSourceStatus.FAILED,
            error_message="connection timeout",
        )
        assert r.status == EventSourceStatus.FAILED
        assert r.error_message == "connection timeout"

    def test_event_counts(self):
        r = _make_event_result()
        assert r.event_count == 3
        assert r.symbols_count == 2


class TestFetchDailyEventsDetailed:
    def test_success_returns_ok(self):
        with patch("tradingagents.tradeflow.event_source._fetch_notice_events_raw", return_value=[]):
            with patch("tradingagents.tradeflow.event_source._fetch_buyback_events_raw", return_value=[]):
                with patch("tradingagents.tradeflow.event_source._fetch_rating_events_raw", return_value=[]):
                    result = fetch_daily_events_detailed("20260528")
        assert result.status == EventSourceStatus.OK
        assert result.event_count == 0

    def test_failure_returns_failed(self):
        """When ALL sub-fetches fail, status should be FAILED."""
        with patch("tradingagents.tradeflow.event_source._fetch_notice_events_raw", side_effect=Exception("timeout")):
            with patch("tradingagents.tradeflow.event_source._fetch_buyback_events_raw", side_effect=Exception("conn_refused")):
                with patch("tradingagents.tradeflow.event_source._fetch_rating_events_raw", side_effect=Exception("dns_error")):
                    result = fetch_daily_events_detailed("20260528")
        assert result.status == EventSourceStatus.FAILED
        assert "timeout" in result.error_message
        assert len(result.failed_sources) == 3

    def test_partial_failure_returns_partial(self):  # [T-007]
        """When only some sub-fetches fail, status should be PARTIAL."""
        with patch("tradingagents.tradeflow.event_source._fetch_notice_events_raw", side_effect=Exception("timeout")):
            with patch("tradingagents.tradeflow.event_source._fetch_buyback_events_raw", return_value=[]):
                with patch("tradingagents.tradeflow.event_source._fetch_rating_events_raw", return_value=[]):
                    result = fetch_daily_events_detailed("20260528")
        assert result.status == EventSourceStatus.PARTIAL
        assert result.failed_sources == ["notice"]
        assert result.source_statuses["notice"] == "FAILED: timeout"
        assert result.source_statuses["buyback"] == "OK"
        assert result.source_statuses["rating"] == "OK"

    def test_sub_fetch_swallows_exception_gives_ok(self):  # [T-007]
        """When sub-fetch catches its own exception and returns [], status is still OK.
        This tests that the old behavior of sub-fetches swallowing exceptions
        no longer causes the detailed function to show pure OK when a source failed."""
        with patch("tradingagents.tradeflow.event_source._fetch_notice_events_raw", return_value=[]):
            with patch("tradingagents.tradeflow.event_source._fetch_buyback_events_raw", return_value=[]):
                with patch("tradingagents.tradeflow.event_source._fetch_rating_events_raw", return_value=[]):
                    result = fetch_daily_events_detailed("20260528")
        assert result.status == EventSourceStatus.OK
        assert result.event_count == 0
        assert len(result.failed_sources) == 0

    def test_dedup_by_title(self):
        items = [
            EventItem(symbol="002138.SZ", title="回购进展", event_type="buyback", source="eastmoney"),
            EventItem(symbol="002138.SZ", title="回购进展", event_type="buyback", source="eastmoney"),
            EventItem(symbol="002138.SZ", title="季报披露", event_type="notice", source="eastmoney"),
        ]
        with patch("tradingagents.tradeflow.event_source._fetch_notice_events_raw", return_value=items):
            with patch("tradingagents.tradeflow.event_source._fetch_buyback_events_raw", return_value=[]):
                with patch("tradingagents.tradeflow.event_source._fetch_rating_events_raw", return_value=[]):
                    result = fetch_daily_events_detailed("20260528")
        assert result.status == EventSourceStatus.OK
        assert len(result.items_by_symbol["002138.SZ"]) == 2
        assert result.events_map["002138.SZ"] == ["回购进展", "季报披露"]

    def test_items_grouped_by_symbol(self):
        items = [
            EventItem(symbol="002138.SZ", title="回购", event_type="buyback", source="eastmoney"),
            EventItem(symbol="600519.SH", title="评级", event_type="rating", source="cninfo"),
        ]
        with patch("tradingagents.tradeflow.event_source._fetch_notice_events_raw", return_value=items):
            with patch("tradingagents.tradeflow.event_source._fetch_buyback_events_raw", return_value=[]):
                with patch("tradingagents.tradeflow.event_source._fetch_rating_events_raw", return_value=[]):
                    result = fetch_daily_events_detailed("20260528")
        assert set(result.items_by_symbol.keys()) == {"002138.SZ", "600519.SH"}
        assert result.symbols_count == 2


class TestPlanRunnerEventSourceFailure:
    def test_event_source_failure_still_processes_manual_symbols(self):
        """When event source fails, system still evaluates manual symbols."""
        with patch("tradingagents.tradeflow.event_source.fetch_daily_events_detailed") as mock_fetch:
            mock_fetch.return_value = EventSourceResult(
                status=EventSourceStatus.FAILED,
                error_message="network error",
            )
            with patch("tradingagents.tradeflow.plan_runner.evaluate_symbol") as mock_eval:
                mock_eval.return_value = (None, "无策略命中")
                plan = generate_daily_plan(
                    trade_date="2026-05-28",
                    symbols=["002138.SZ"],
                    candidates=None,
                    use_event_source=True,
                )
        assert plan is not None
        assert plan.metadata["event_source"]["status"] == "FAILED"
        assert plan.metadata["event_source"]["error_message"] == "network error"

    def test_event_source_failure_manual_candidates_still_appear(self):
        """Manual symbols produce candidates even when event source failed."""
        with patch("tradingagents.tradeflow.event_source.fetch_daily_events_detailed") as mock_fetch:
            mock_fetch.return_value = EventSourceResult(
                status=EventSourceStatus.FAILED,
                error_message="timeout",
            )
            with patch("tradingagents.tradeflow.plan_runner.evaluate_symbol") as mock_eval:
                mock_eval.return_value = (_mock_candidate("002138.SZ", score=70.0), "")
                plan = generate_daily_plan(
                    trade_date="2026-05-28",
                    symbols=["002138.SZ"],
                    candidates=None,
                    use_event_source=True,
                )
        plan_symbols = {e["symbol"] for e in plan.candidates}
        assert "002138.SZ" in plan_symbols

    def test_no_event_source_status_not_queried(self):
        """When use_event_source=False, status should be NOT_QUERIED."""
        with patch("tradingagents.tradeflow.plan_runner.evaluate_symbol") as mock_eval:
            mock_eval.return_value = (None, "无策略命中")
            plan = generate_daily_plan(
                trade_date="2026-05-28",
                symbols=["002138.SZ"],
                candidates=None,
                use_event_source=False,
            )
        assert plan.metadata["event_source"]["status"] == "NOT_QUERIED"


class TestPlanRunnerEventMetadata:
    def test_event_metadata_in_plan_entries(self):
        """Plan entries for event-discovered symbols include event metadata."""
        with patch("tradingagents.tradeflow.event_source.fetch_daily_events_detailed") as mock_fetch:
            mock_fetch.return_value = _make_event_result()
            with patch("tradingagents.tradeflow.plan_runner.evaluate_symbol") as mock_eval:
                def fake_eval(symbol, **kwargs):
                    if symbol in MOCK_EVENT_ITEMS:
                        return _mock_candidate(symbol, score=65.0), ""
                    return None, "无策略命中"
                mock_eval.side_effect = fake_eval
                plan = generate_daily_plan(
                    trade_date="2026-05-28",
                    symbols=[],
                    candidates=None,
                    use_event_source=True,
                )
        entries_by_sym = {e["symbol"]: e for e in plan.candidates}
        entry_002138 = entries_by_sym.get("002138.SZ")
        assert entry_002138 is not None
        assert "event_titles" in entry_002138
        assert "回购进展公告" in entry_002138["event_titles"]
        assert "event_types" in entry_002138
        assert "buyback" in entry_002138["event_types"]
        assert "event_sources" in entry_002138
        assert "eastmoney" in entry_002138["event_sources"]
        assert entry_002138["event_count"] == 2

    def test_event_details_in_plan_entry(self):
        """Plan entry contains full event_details with direction/source/date."""
        with patch("tradingagents.tradeflow.event_source.fetch_daily_events_detailed") as mock_fetch:
            mock_fetch.return_value = _make_event_result()
            with patch("tradingagents.tradeflow.plan_runner.evaluate_symbol") as mock_eval:
                def fake_eval(symbol, **kwargs):
                    if symbol == "600519.SH":
                        return _mock_candidate(symbol, score=55.0), ""
                    return None, "无策略命中"
                mock_eval.side_effect = fake_eval
                plan = generate_daily_plan(
                    trade_date="2026-05-28",
                    symbols=[],
                    candidates=None,
                    use_event_source=True,
                )
        entries_by_sym = {e["symbol"]: e for e in plan.candidates}
        entry = entries_by_sym.get("600519.SH")
        assert entry is not None
        details = entry["event_details"]
        assert len(details) == 1
        assert details[0]["direction"] == "bearish"
        assert details[0]["source"] == "cninfo"
        assert details[0]["event_type"] == "rating"

    def test_no_event_metadata_without_events(self):
        """Symbols without events don't get event metadata fields."""
        with patch("tradingagents.tradeflow.event_source.fetch_daily_events_detailed") as mock_fetch:
            mock_fetch.return_value = _make_event_result()
            with patch("tradingagents.tradeflow.plan_runner.evaluate_symbol") as mock_eval:
                def fake_eval(symbol, **kwargs):
                    return _mock_candidate(symbol, score=50.0), ""
                mock_eval.side_effect = fake_eval
                plan = generate_daily_plan(
                    trade_date="2026-05-28",
                    symbols=["300999.SZ"],
                    candidates=None,
                    use_event_source=True,
                )
        entries_by_sym = {e["symbol"]: e for e in plan.candidates}
        entry = entries_by_sym.get("300999.SZ")
        assert entry is not None
        assert "event_titles" not in entry
        assert "event_count" not in entry

    def test_event_source_metadata_in_plan(self):
        """Plan metadata includes event source status and counts."""
        with patch("tradingagents.tradeflow.event_source.fetch_daily_events_detailed") as mock_fetch:
            mock_fetch.return_value = _make_event_result()
            with patch("tradingagents.tradeflow.plan_runner.evaluate_symbol") as mock_eval:
                mock_eval.return_value = (None, "无策略命中")
                plan = generate_daily_plan(
                    trade_date="2026-05-28",
                    symbols=[],
                    candidates=None,
                    use_event_source=True,
                )
        assert plan.metadata["event_source"]["status"] == "OK"
        assert plan.metadata["event_source"]["event_count"] == 3
        assert plan.metadata["event_source"]["symbols_count"] == 2


class TestPlanRunnerEventOverrides:
    def test_event_items_become_overrides(self):
        """Event items are converted to event_overrides and passed to evaluate_symbol."""
        with patch("tradingagents.tradeflow.event_source.fetch_daily_events_detailed") as mock_fetch:
            mock_fetch.return_value = _make_event_result()
            with patch("tradingagents.tradeflow.plan_runner.evaluate_symbol") as mock_eval:
                mock_eval.return_value = (None, "无策略命中")
                plan = generate_daily_plan(
                    trade_date="2026-05-28",
                    symbols=[],
                    candidates=None,
                    use_event_source=True,
                )
            for call_item in mock_eval.call_args_list:
                sym = call_item.kwargs.get("symbol", "")
                ev_overrides = call_item.kwargs.get("event_overrides")
                if sym == "002138.SZ":
                    assert ev_overrides is not None
                    titles = [ev["title"] for ev in ev_overrides]
                    assert "回购进展公告" in titles
                    types = [ev["event_type"] for ev in ev_overrides]
                    assert "buyback" in types
                elif sym == "600519.SH":
                    assert ev_overrides is not None
                    assert any(ev["direction"] == "bearish" for ev in ev_overrides)

    def test_symbol_isolation_in_overrides(self):
        """Each symbol only gets its own event overrides — no cross-contamination."""
        with patch("tradingagents.tradeflow.event_source.fetch_daily_events_detailed") as mock_fetch:
            mock_fetch.return_value = _make_event_result()
            with patch("tradingagents.tradeflow.plan_runner.evaluate_symbol") as mock_eval:
                mock_eval.return_value = (None, "无策略命中")
                plan = generate_daily_plan(
                    trade_date="2026-05-28",
                    symbols=["002138.SZ", "600519.SH"],
                    candidates=None,
                    use_event_source=True,
                )
            for call_item in mock_eval.call_args_list:
                sym = call_item.kwargs.get("symbol", "")
                ev_overrides = call_item.kwargs.get("event_overrides") or []
                override_syms = {ev.get("symbol", "") for ev in ev_overrides}
                if sym in ("002138.SZ", "600519.SH"):
                    assert override_syms == {sym}, f"{sym} has cross-contamination: {override_syms}"


class TestDiscoveryEventSourceFailure:
    def test_event_source_failure_discovery_still_works(self):
        """Discovery still works when event source fails."""
        with patch("tradingagents.tradeflow.event_source.fetch_daily_events_detailed") as mock_fetch:
            mock_fetch.return_value = EventSourceResult(
                status=EventSourceStatus.FAILED,
                error_message="api error",
            )
            with patch("tradingagents.tradeflow.discovery.evaluate_symbol") as mock_eval:
                mock_eval.return_value = (None, "无策略命中")
                result = run_discovery(
                    trade_date="2026-05-28",
                    symbols=["002138.SZ"],
                    use_event_source=True,
                )
        assert result is not None
        assert result.metadata["event_source"]["status"] == "FAILED"

    def test_event_source_ok_in_discovery_metadata(self):
        """Discovery metadata includes OK status when event source succeeds."""
        with patch("tradingagents.tradeflow.event_source.fetch_daily_events_detailed") as mock_fetch:
            mock_fetch.return_value = _make_event_result()
            with patch("tradingagents.tradeflow.discovery.evaluate_symbol") as mock_eval:
                mock_eval.return_value = (None, "无策略命中")
                result = run_discovery(
                    trade_date="2026-05-28",
                    symbols=[],
                    use_event_source=True,
                )
        assert result.metadata["event_source"]["status"] == "OK"
        assert result.metadata["event_source"]["event_count"] == 3

    def test_discovery_entry_has_event_metadata(self):
        """Discovery entries include event metadata for event-discovered symbols."""
        with patch("tradingagents.tradeflow.event_source.fetch_daily_events_detailed") as mock_fetch:
            mock_fetch.return_value = _make_event_result()
            with patch("tradingagents.tradeflow.discovery.evaluate_symbol") as mock_eval:
                def fake_eval(symbol, **kwargs):
                    if symbol == "002138.SZ":
                        return _mock_candidate(symbol, score=65.0), ""
                    return None, "无策略命中"
                mock_eval.side_effect = fake_eval
                result = run_discovery(
                    trade_date="2026-05-28",
                    symbols=[],
                    use_event_source=True,
                )
        entries_by_sym = {e["symbol"]: e for e in result.candidates}
        entry = entries_by_sym.get("002138.SZ")
        assert entry is not None
        assert "event_titles" in entry
        assert len(entry["event_titles"]) == 2
        assert "buyback" in entry["event_types"]
        assert "eastmoney" in entry["event_sources"]

    def test_discovery_no_event_source_not_queried(self):
        """When use_event_source=False, discovery metadata shows NOT_QUERIED."""
        with patch("tradingagents.tradeflow.discovery.evaluate_symbol") as mock_eval:
            mock_eval.return_value = (None, "无策略命中")
            result = run_discovery(
                trade_date="2026-05-28",
                symbols=["002138.SZ"],
                use_event_source=False,
            )
        assert result.metadata["event_source"]["status"] == "NOT_QUERIED"


class TestDiscoveryEventOverrides:
    def test_discovery_event_items_become_overrides(self):
        """In discovery, event items generate event_overrides for candidate evaluation."""
        with patch("tradingagents.tradeflow.event_source.fetch_daily_events_detailed") as mock_fetch:
            mock_fetch.return_value = _make_event_result()
            with patch("tradingagents.tradeflow.discovery.evaluate_symbol") as mock_eval:
                mock_eval.return_value = (None, "无策略命中")
                result = run_discovery(
                    trade_date="2026-05-28",
                    symbols=[],
                    use_event_source=True,
                )
            for call_item in mock_eval.call_args_list:
                sym = call_item.kwargs.get("symbol", "")
                ev_overrides = call_item.kwargs.get("event_overrides")
                if sym == "600519.SH" and ev_overrides:
                    assert any(ev["direction"] == "bearish" for ev in ev_overrides)
                    assert any(ev["event_type"] == "rating" for ev in ev_overrides)

    def test_discovery_symbol_isolation(self):
        """In discovery, symbols only get their own event overrides."""
        with patch("tradingagents.tradeflow.event_source.fetch_daily_events_detailed") as mock_fetch:
            mock_fetch.return_value = _make_event_result()
            with patch("tradingagents.tradeflow.discovery.evaluate_symbol") as mock_eval:
                mock_eval.return_value = (None, "无策略命中")
                result = run_discovery(
                    trade_date="2026-05-28",
                    symbols=["002138.SZ", "600519.SH"],
                    use_event_source=True,
                )
            for call_item in mock_eval.call_args_list:
                sym = call_item.kwargs.get("symbol", "")
                ev_overrides = call_item.kwargs.get("event_overrides") or []
                override_syms = {ev.get("symbol", "") for ev in ev_overrides}
                if sym in ("002138.SZ", "600519.SH"):
                    assert override_syms == {sym}


# [T-007] event_source_failure_status — sub-fetch failure observability tests


def _make_partial_result():
    """Event source result with partial failure (notice failed, buyback/rating OK)."""
    items = [
        EventItem(symbol="002138.SZ", title="回购进展", event_type="buyback",
                  direction="bullish", source="eastmoney", date="2026-05-28"),
    ]
    return EventSourceResult(
        status=EventSourceStatus.PARTIAL,
        events_map={"002138.SZ": ["回购进展"]},
        items_by_symbol={"002138.SZ": items},
        error_message="notice: timeout",
        event_count=1,
        symbols_count=1,
        source_statuses={"notice": "FAILED: timeout", "buyback": "OK", "rating": "OK"},
        failed_sources=["notice"],
    )


def _make_all_failed_result():
    """Event source result where all sub-fetches failed."""
    return EventSourceResult(
        status=EventSourceStatus.FAILED,
        error_message="notice: timeout; buyback: conn_refused; rating: dns_error",
        source_statuses={
            "notice": "FAILED: timeout",
            "buyback": "FAILED: conn_refused",
            "rating": "FAILED: dns_error",
        },
        failed_sources=["notice", "buyback", "rating"],
    )


class TestT007SubFetchFailureStatus:
    """Tests for T-007: sub-fetch failure status tracking."""

    def test_all_sub_fetch_fail_returns_failed(self):
        """When all 3 sub-fetches throw exceptions, status is FAILED."""
        with patch("tradingagents.tradeflow.event_source._fetch_notice_events_raw", side_effect=Exception("timeout")):
            with patch("tradingagents.tradeflow.event_source._fetch_buyback_events_raw", side_effect=Exception("conn_refused")):
                with patch("tradingagents.tradeflow.event_source._fetch_rating_events_raw", side_effect=Exception("dns_error")):
                    result = fetch_daily_events_detailed("20260528")
        assert result.status == EventSourceStatus.FAILED
        assert len(result.failed_sources) == 3
        assert set(result.failed_sources) == {"notice", "buyback", "rating"}
        assert "FAILED" in result.source_statuses["notice"]
        assert "FAILED" in result.source_statuses["buyback"]
        assert "FAILED" in result.source_statuses["rating"]

    def test_one_sub_fetch_fail_returns_partial(self):
        """When only notice fails, status is PARTIAL and failed_sources lists it."""
        with patch("tradingagents.tradeflow.event_source._fetch_notice_events_raw", side_effect=Exception("timeout")):
            with patch("tradingagents.tradeflow.event_source._fetch_buyback_events_raw", return_value=[]):
                with patch("tradingagents.tradeflow.event_source._fetch_rating_events_raw", return_value=[]):
                    result = fetch_daily_events_detailed("20260528")
        assert result.status == EventSourceStatus.PARTIAL
        assert result.failed_sources == ["notice"]
        assert "timeout" in result.source_statuses["notice"]
        assert result.source_statuses["buyback"] == "OK"
        assert result.source_statuses["rating"] == "OK"

    def test_two_sub_fetch_fail_returns_partial(self):
        """When 2 of 3 sub-fetches fail, status is PARTIAL."""
        with patch("tradingagents.tradeflow.event_source._fetch_notice_events_raw", side_effect=Exception("e1")):
            with patch("tradingagents.tradeflow.event_source._fetch_buyback_events_raw", side_effect=Exception("e2")):
                with patch("tradingagents.tradeflow.event_source._fetch_rating_events_raw", return_value=[]):
                    result = fetch_daily_events_detailed("20260528")
        assert result.status == EventSourceStatus.PARTIAL
        assert set(result.failed_sources) == {"notice", "buyback"}
        assert result.source_statuses["rating"] == "OK"

    def test_all_succeed_empty_events_returns_ok(self):
        """All sub-fetches succeed but return no events → OK with count 0."""
        with patch("tradingagents.tradeflow.event_source._fetch_notice_events_raw", return_value=[]):
            with patch("tradingagents.tradeflow.event_source._fetch_buyback_events_raw", return_value=[]):
                with patch("tradingagents.tradeflow.event_source._fetch_rating_events_raw", return_value=[]):
                    result = fetch_daily_events_detailed("20260528")
        assert result.status == EventSourceStatus.OK
        assert result.event_count == 0
        assert result.failed_sources == []
        assert all(v == "OK" for v in result.source_statuses.values())

    def test_all_succeed_with_events_returns_ok(self):
        """All sub-fetches succeed and return events → OK with events."""
        items = [
            EventItem(symbol="002138.SZ", title="回购", event_type="buyback",
                      direction="bullish", source="eastmoney"),
            EventItem(symbol="600519.SH", title="评级", event_type="rating",
                      direction="bearish", source="cninfo"),
        ]
        with patch("tradingagents.tradeflow.event_source._fetch_notice_events_raw", return_value=items):
            with patch("tradingagents.tradeflow.event_source._fetch_buyback_events_raw", return_value=[]):
                with patch("tradingagents.tradeflow.event_source._fetch_rating_events_raw", return_value=[]):
                    result = fetch_daily_events_detailed("20260528")
        assert result.status == EventSourceStatus.OK
        assert result.event_count == 2
        assert result.failed_sources == []

    def test_failed_sub_fetch_items_still_included(self):
        """Items from successful sub-fetches are still included even when some fail."""
        notice_items = [
            EventItem(symbol="002138.SZ", title="公告", event_type="notice",
                      direction="neutral", source="eastmoney"),
        ]
        with patch("tradingagents.tradeflow.event_source._fetch_notice_events_raw", return_value=notice_items):
            with patch("tradingagents.tradeflow.event_source._fetch_buyback_events_raw", side_effect=Exception("err")):
                with patch("tradingagents.tradeflow.event_source._fetch_rating_events_raw", return_value=[]):
                    result = fetch_daily_events_detailed("20260528")
        assert result.status == EventSourceStatus.PARTIAL
        assert "002138.SZ" in result.items_by_symbol
        assert result.event_count == 1

    def test_source_statuses_always_has_three_entries(self):
        """source_statuses always has entries for notice, buyback, rating."""
        with patch("tradingagents.tradeflow.event_source._fetch_notice_events_raw", side_effect=Exception("e")):
            with patch("tradingagents.tradeflow.event_source._fetch_buyback_events_raw", return_value=[]):
                with patch("tradingagents.tradeflow.event_source._fetch_rating_events_raw", return_value=[]):
                    result = fetch_daily_events_detailed("20260528")
        assert set(result.source_statuses.keys()) == {"notice", "buyback", "rating"}

    def test_error_message_sanitize_api_key(self):
        """Error messages should not contain API keys."""
        with patch("tradingagents.tradeflow.event_source._fetch_notice_events_raw",
                    side_effect=Exception("api_key=sk-abc123xyz")):
            with patch("tradingagents.tradeflow.event_source._fetch_buyback_events_raw", return_value=[]):
                with patch("tradingagents.tradeflow.event_source._fetch_rating_events_raw", return_value=[]):
                    result = fetch_daily_events_detailed("20260528")
        assert "sk-abc123xyz" not in result.source_statuses["notice"]
        assert "[REDACTED]" in result.source_statuses["notice"]


class TestT007PlanRunnerMetadata:
    """Tests for T-007: plan_runner metadata includes per-source failure info."""

    def test_partial_failure_in_plan_metadata(self):
        """Plan metadata shows PARTIAL status and failed_sources."""
        with patch("tradingagents.tradeflow.event_source.fetch_daily_events_detailed") as mock_fetch:
            mock_fetch.return_value = _make_partial_result()
            with patch("tradingagents.tradeflow.plan_runner.evaluate_symbol") as mock_eval:
                mock_eval.return_value = (None, "无策略命中")
                plan = generate_daily_plan(
                    trade_date="2026-05-28",
                    symbols=["002138.SZ"],
                    candidates=None,
                    use_event_source=True,
                )
        es = plan.metadata["event_source"]
        assert es["status"] == "PARTIAL"
        assert es["failed_sources"] == ["notice"]
        assert "notice" in es["source_statuses"]
        assert "FAILED" in es["source_statuses"]["notice"]
        assert es["source_statuses"]["buyback"] == "OK"

    def test_all_failed_in_plan_metadata(self):
        """Plan metadata shows FAILED status when all sources fail."""
        with patch("tradingagents.tradeflow.event_source.fetch_daily_events_detailed") as mock_fetch:
            mock_fetch.return_value = _make_all_failed_result()
            with patch("tradingagents.tradeflow.plan_runner.evaluate_symbol") as mock_eval:
                mock_eval.return_value = (None, "无策略命中")
                plan = generate_daily_plan(
                    trade_date="2026-05-28",
                    symbols=["002138.SZ"],
                    candidates=None,
                    use_event_source=True,
                )
        es = plan.metadata["event_source"]
        assert es["status"] == "FAILED"
        assert len(es["failed_sources"]) == 3

    def test_ok_no_failed_sources_in_plan_metadata(self):
        """Plan metadata shows OK with empty failed_sources."""
        with patch("tradingagents.tradeflow.event_source.fetch_daily_events_detailed") as mock_fetch:
            mock_fetch.return_value = _make_event_result()
            with patch("tradingagents.tradeflow.plan_runner.evaluate_symbol") as mock_eval:
                mock_eval.return_value = (None, "无策略命中")
                plan = generate_daily_plan(
                    trade_date="2026-05-28",
                    symbols=[],
                    candidates=None,
                    use_event_source=True,
                )
        es = plan.metadata["event_source"]
        assert es["status"] == "OK"
        assert es["failed_sources"] == []
        assert all(v == "OK" for v in es["source_statuses"].values())

    def test_not_queried_empty_failed_sources(self):
        """When use_event_source=False, failed_sources is empty."""
        with patch("tradingagents.tradeflow.plan_runner.evaluate_symbol") as mock_eval:
            mock_eval.return_value = (None, "无策略命中")
            plan = generate_daily_plan(
                trade_date="2026-05-28",
                symbols=["002138.SZ"],
                candidates=None,
                use_event_source=False,
            )
        es = plan.metadata["event_source"]
        assert es["status"] == "NOT_QUERIED"
        assert es["failed_sources"] == []
        assert es["source_statuses"] == {}


class TestT007DiscoveryMetadata:
    """Tests for T-007: discovery metadata includes per-source failure info."""

    def test_partial_failure_in_discovery_metadata(self):
        """Discovery metadata shows PARTIAL status and failed_sources."""
        with patch("tradingagents.tradeflow.event_source.fetch_daily_events_detailed") as mock_fetch:
            mock_fetch.return_value = _make_partial_result()
            with patch("tradingagents.tradeflow.discovery.evaluate_symbol") as mock_eval:
                mock_eval.return_value = (None, "无策略命中")
                result = run_discovery(
                    trade_date="2026-05-28",
                    symbols=["002138.SZ"],
                    use_event_source=True,
                )
        es = result.metadata["event_source"]
        assert es["status"] == "PARTIAL"
        assert es["failed_sources"] == ["notice"]
        assert "notice" in es["source_statuses"]

    def test_all_failed_in_discovery_metadata(self):
        """Discovery metadata shows FAILED when all sources fail."""
        with patch("tradingagents.tradeflow.event_source.fetch_daily_events_detailed") as mock_fetch:
            mock_fetch.return_value = _make_all_failed_result()
            with patch("tradingagents.tradeflow.discovery.evaluate_symbol") as mock_eval:
                mock_eval.return_value = (None, "无策略命中")
                result = run_discovery(
                    trade_date="2026-05-28",
                    symbols=["002138.SZ"],
                    use_event_source=True,
                )
        es = result.metadata["event_source"]
        assert es["status"] == "FAILED"
        assert len(es["failed_sources"]) == 3

    def test_ok_no_failed_sources_in_discovery_metadata(self):
        """Discovery metadata shows OK with empty failed_sources."""
        with patch("tradingagents.tradeflow.event_source.fetch_daily_events_detailed") as mock_fetch:
            mock_fetch.return_value = _make_event_result()
            with patch("tradingagents.tradeflow.discovery.evaluate_symbol") as mock_eval:
                mock_eval.return_value = (None, "无策略命中")
                result = run_discovery(
                    trade_date="2026-05-28",
                    symbols=[],
                    use_event_source=True,
                )
        es = result.metadata["event_source"]
        assert es["status"] == "OK"
        assert es["failed_sources"] == []
        assert all(v == "OK" for v in es["source_statuses"].values())

    def test_not_queried_empty_failed_sources(self):
        """When use_event_source=False, discovery failed_sources is empty."""
        with patch("tradingagents.tradeflow.discovery.evaluate_symbol") as mock_eval:
            mock_eval.return_value = (None, "无策略命中")
            result = run_discovery(
                trade_date="2026-05-28",
                symbols=["002138.SZ"],
                use_event_source=False,
            )
        es = result.metadata["event_source"]
        assert es["status"] == "NOT_QUERIED"
        assert es["failed_sources"] == []
        assert es["source_statuses"] == {}


class TestT007EventSourceStatusEnum:
    """Verify EventSourceStatus enum has all expected values."""

    def test_partial_exists(self):
        assert EventSourceStatus.PARTIAL.value == "PARTIAL"

    def test_all_statuses(self):
        values = {s.value for s in EventSourceStatus}
        assert values == {"OK", "FAILED", "STALE", "PARTIAL"}
