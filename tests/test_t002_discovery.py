"""Tests for TradeFlow Discovery — T-002 small-scope discovery.  # [T-002] discovery"""

import os
import sys
import tempfile
from dataclasses import asdict
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.tradeflow.schemas import Candidate, CandidateSignal, DailyPlan
from tradingagents.tradeflow.discovery import (
    run_discovery,
    render_discovery_text,
    _build_discovery_universe,
    _classify_filter_reasons,
    _build_discovery_entry,
    FilteredSymbol,
    DiscoveryResult,
    TOPN_DEFAULT,
    SOURCE_WATCHLIST,
    SOURCE_HOLDING,
    SOURCE_MANUAL,
    SOURCE_INDUSTRY,
    SOURCE_EVENT,
)
from tradingagents.tradeflow.candidate_engine import init_db


def _make_candidate(symbol="002353.SZ", name="测试", source="manual",
                    strategies=None, score=55.0, need_deep_ta=False,
                    risk_flags=None, game_balance=""):
    c = Candidate(
        symbol=symbol, name=name, source=source,
        strategy_tags=strategies or ["VCP"],
        score=score, need_deep_ta=need_deep_ta,
        trigger_price=100.0, invalid_price=90.0,
        risk_flags=risk_flags or [],
        game_balance=game_balance,
    )
    c.signals = [CandidateSignal(strategy_tag=s, score=score, reason="测试原因")
                 for s in (strategies or ["VCP"])]
    return c


class TestBuildDiscoveryUniverse:
    def test_industry_symbols_added(self):
        result = _build_discovery_universe(
            industry_symbols=["600519.SH", "000001.SZ"],
        )
        syms = {r["symbol"] for r in result}
        assert "600519.SH" in syms
        assert "000001.SZ" in syms

    def test_industry_symbols_dedup_with_manual(self):
        result = _build_discovery_universe(
            symbols=["600519.SH", "603256.SH"],
            industry_symbols=["600519.SH", "000001.SZ"],
        )
        count_600519 = sum(1 for r in result if r["symbol"] == "600519.SH")
        assert count_600519 == 1
        sources = {r["symbol"]: r["source"] for r in result}
        assert sources["600519.SH"] == "manual"

    def test_industry_source_label(self):
        result = _build_discovery_universe(
            industry_symbols=["999999.SZ"],
        )
        ind = [r for r in result if r["symbol"] == "999999.SZ"]
        assert len(ind) == 1
        assert ind[0]["source"] == SOURCE_INDUSTRY

    def test_empty_inputs(self):
        result = _build_discovery_universe()
        assert isinstance(result, list)

    @patch("tradingagents.tradeflow.discovery.build_universe")
    def test_event_symbols_passed_through(self, mock_build):
        mock_build.return_value = []
        _build_discovery_universe(
            event_symbols={"002138": ["回购进展"]},
        )
        mock_build.assert_called_once()
        call_kwargs = mock_build.call_args
        assert call_kwargs[1]["event_symbols"] == {"002138": ["回购进展"]}

    def test_include_yesterday_default_false(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tf_db = f.name
        try:
            init_db(tf_db)
            result = _build_discovery_universe(
                tf_db_path=tf_db,
                include_yesterday=False,
            )
            assert isinstance(result, list)
        finally:
            os.unlink(tf_db)


class TestClassifyFilterReasons:
    def test_liquidity_filter(self):
        filtered = [FilteredSymbol("A.SZ", "A", "manual", "流动性差(avg_amount=0.15亿)")]
        result = _classify_filter_reasons(filtered)
        assert result == {"流动性差": 1}

    def test_data_missing(self):
        filtered = [FilteredSymbol("B.SZ", "B", "manual", "数据缺失")]
        result = _classify_filter_reasons(filtered)
        assert result == {"数据缺失": 1}

    def test_no_strategy(self):
        filtered = [FilteredSymbol("C.SZ", "C", "manual", "无策略命中")]
        result = _classify_filter_reasons(filtered)
        assert result == {"无策略命中": 1}

    def test_mixed_reasons(self):
        filtered = [
            FilteredSymbol("A.SZ", "", "manual", "流动性差(xxx)"),
            FilteredSymbol("B.SZ", "", "manual", "数据缺失"),
            FilteredSymbol("C.SZ", "", "manual", "无策略命中"),
            FilteredSymbol("D.SZ", "", "manual", "数据不足(需至少40日)"),
        ]
        result = _classify_filter_reasons(filtered)
        assert result["数据缺失"] == 2
        assert result["流动性差"] == 1
        assert result["无策略命中"] == 1

    def test_empty_list(self):
        result = _classify_filter_reasons([])
        assert result == {}


class TestBuildDiscoveryEntry:
    def test_basic_entry(self):
        c = _make_candidate(strategies=["VCP", "EVENT_CATALYST"], score=70.0)
        c.primary_strategy = "VCP"
        entry = _build_discovery_entry(c)
        assert entry["symbol"] == "002353.SZ"
        assert entry["score"] == 70.0
        assert "VCP" in entry["strategies"]
        assert entry["action"] in {"OBSERVE", "NEED_DEEP_TA"}

    def test_deep_ta_entry(self):
        c = _make_candidate(need_deep_ta=True)
        entry = _build_discovery_entry(c)
        assert entry["action"] == "NEED_DEEP_TA"

    def test_no_forbidden_words(self):
        c = _make_candidate()
        c.signals[0].reason = "建议立即买入该标的"
        entry = _build_discovery_entry(c)
        assert "立即买入" not in entry["reason"]

    def test_source_preserved(self):
        c = _make_candidate(source="industry_pool")
        entry = _build_discovery_entry(c)
        assert entry["source"] == "industry_pool"

    def test_risk_flags_included(self):
        c = _make_candidate(risk_flags=["LOCKUP_RISK", "INQUIRY_RISK"])
        entry = _build_discovery_entry(c)
        assert "LOCKUP_RISK" in entry["risk_flags"]


class TestRunDiscovery:
    def test_basic_discovery_with_candidates(self):
        c1 = _make_candidate("A.SZ", score=65.0)
        c2 = _make_candidate("B.SZ", score=55.0)

        def fake_evaluate(symbol, **kwargs):
            if symbol == "A.SZ":
                return c1, ""
            elif symbol == "B.SZ":
                return c2, ""
            return None, "无策略命中"

        with patch("tradingagents.tradeflow.discovery.evaluate_symbol", side_effect=fake_evaluate):
            result = run_discovery(
                trade_date="2026-05-29",
                symbols=["A.SZ", "B.SZ"],
                include_holdings=False,
                include_watchlist=False,
            )
        assert result.trade_date == "2026-05-29"
        assert len(result.candidates) == 2
        assert result.candidates[0]["score"] >= result.candidates[1]["score"]

    def test_discovery_top_n(self):
        candidates = [_make_candidate(f"S{i}.SZ", score=float(i * 10)) for i in range(1, 11)]

        def fake_evaluate(symbol, **kwargs):
            for c in candidates:
                if c.symbol == symbol:
                    return c, ""
            return None, "无策略命中"

        with patch("tradingagents.tradeflow.discovery.evaluate_symbol", side_effect=fake_evaluate):
            result = run_discovery(
                trade_date="2026-05-29",
                symbols=[f"S{i}.SZ" for i in range(1, 11)],
                top_n=3,
                include_holdings=False,
                include_watchlist=False,
            )
        assert len(result.candidates) == 3
        assert result.candidates[0]["score"] == 100.0

    def test_discovery_with_filtered(self):
        def fake_evaluate(symbol, **kwargs):
            if symbol == "GOOD.SZ":
                return _make_candidate("GOOD.SZ", score=60.0), ""
            return None, "流动性差(avg_amount=0.10亿)"

        with patch("tradingagents.tradeflow.discovery.evaluate_symbol", side_effect=fake_evaluate):
            result = run_discovery(
                trade_date="2026-05-29",
                symbols=["GOOD.SZ", "BAD1.SZ", "BAD2.SZ"],
                include_holdings=False,
                include_watchlist=False,
            )
        assert len(result.candidates) == 1
        assert len(result.filtered) == 2
        assert result.metadata["filter_breakdown"]["流动性差"] == 2

    def test_discovery_filter_reason_categories(self):
        def fake_evaluate(symbol, **kwargs):
            if symbol == "A.SZ":
                return _make_candidate("A.SZ", score=60.0), ""
            if symbol == "B.SZ":
                return None, "数据缺失"
            if symbol == "C.SZ":
                return None, "无策略命中"
            return None, "流动性差(xxx)"

        with patch("tradingagents.tradeflow.discovery.evaluate_symbol", side_effect=fake_evaluate):
            result = run_discovery(
                trade_date="2026-05-29",
                symbols=["A.SZ", "B.SZ", "C.SZ", "D.SZ"],
                include_holdings=False,
                include_watchlist=False,
            )
        fb = result.metadata["filter_breakdown"]
        assert "数据缺失" in fb
        assert "无策略命中" in fb
        assert "流动性差" in fb

    def test_discovery_empty_universe(self):
        with patch("tradingagents.tradeflow.discovery._build_discovery_universe", return_value=[]):
            result = run_discovery(
                trade_date="2026-05-29",
                include_holdings=False,
                include_watchlist=False,
            )
        assert len(result.candidates) == 0
        assert result.universe_size == 0

    def test_discovery_summary_contains_counts(self):
        c = _make_candidate("A.SZ", score=60.0)

        def fake_evaluate(symbol, **kwargs):
            if symbol == "A.SZ":
                return c, ""
            return None, "无策略命中"

        with patch("tradingagents.tradeflow.discovery.evaluate_symbol", side_effect=fake_evaluate):
            result = run_discovery(
                trade_date="2026-05-29",
                symbols=["A.SZ", "B.SZ"],
                top_n=10,
                include_holdings=False,
                include_watchlist=False,
            )
        assert "共扫描" in result.summary
        assert "主候选" in result.summary
        assert "被过滤" in result.summary

    def test_discovery_no_forbidden_words(self):
        c = _make_candidate("A.SZ", score=60.0)
        with patch("tradingagents.tradeflow.discovery.evaluate_symbol", return_value=(c, "")):
            result = run_discovery(
                trade_date="2026-05-29",
                symbols=["A.SZ"],
                include_holdings=False,
                include_watchlist=False,
            )
        text = render_discovery_text(result)
        forbidden = {"立即买入", "重仓买入", "立即清仓", "满仓", "梭哈"}
        for word in forbidden:
            assert word not in text

    def test_discovery_sources_in_metadata(self):
        with patch("tradingagents.tradeflow.discovery._build_discovery_universe", return_value=[
            {"symbol": "A.SZ", "name": "", "source": "manual"},
            {"symbol": "B.SZ", "name": "", "source": "industry_pool"},
        ]):
            with patch("tradingagents.tradeflow.discovery.evaluate_symbol", return_value=(None, "无策略命中")):
                result = run_discovery(
                    trade_date="2026-05-29",
                    include_holdings=False,
                    include_watchlist=False,
                )
        assert "manual" in result.metadata["sources"]
        assert "industry_pool" in result.metadata["sources"]

    def test_discovery_default_top_n(self):
        assert TOPN_DEFAULT == 20


class TestRunDiscoveryWithIndustry:
    def test_industry_symbols_in_universe(self):
        c = _make_candidate("600519.SH", score=70.0)

        def fake_evaluate(symbol, **kwargs):
            if symbol == "600519.SH":
                return c, ""
            return None, "无策略命中"

        with patch("tradingagents.tradeflow.discovery.evaluate_symbol", side_effect=fake_evaluate):
            result = run_discovery(
                trade_date="2026-05-29",
                industry_symbols=["600519.SH", "000001.SZ"],
                include_holdings=False,
                include_watchlist=False,
            )
        symbols = {e["symbol"] for e in result.candidates}
        assert "600519.SH" in symbols

    def test_industry_source_tracked(self):
        c = _make_candidate("600519.SH", score=70.0, source="industry_pool")

        def fake_evaluate(symbol, **kwargs):
            if symbol == "600519.SH":
                return c, ""
            return None, "无策略命中"

        with patch("tradingagents.tradeflow.discovery.evaluate_symbol", side_effect=fake_evaluate):
            result = run_discovery(
                trade_date="2026-05-29",
                industry_symbols=["600519.SH"],
                include_holdings=False,
                include_watchlist=False,
            )
        entry = result.candidates[0]
        assert entry["source"] == "industry_pool"


class TestRunDiscoveryWithEvents:
    def test_event_source_integration(self):
        from tradingagents.tradeflow.event_source import EventItem, EventSourceResult, EventSourceStatus
        c = _make_candidate("002138.SZ", score=65.0)

        def fake_evaluate(symbol, **kwargs):
            news = kwargs.get("news_texts")
            if symbol == "002138.SZ" and news:
                return c, ""
            return None, "无策略命中"

        event_result = EventSourceResult(
            status=EventSourceStatus.OK,
            events_map={"002138": ["回购进展"]},
            items_by_symbol={"002138": [EventItem(symbol="002138", title="回购进展", event_type="buyback", direction="bullish", source="eastmoney")]},
            event_count=1, symbols_count=1,
        )
        with patch("tradingagents.tradeflow.event_source.fetch_daily_events_detailed",
                    return_value=event_result):
            with patch("tradingagents.tradeflow.discovery.evaluate_symbol", side_effect=fake_evaluate):
                result = run_discovery(
                    trade_date="2026-05-29",
                    use_event_source=True,
                    include_holdings=False,
                    include_watchlist=False,
                )
        assert len(result.candidates) >= 1

    def test_event_source_not_fetched_when_disabled(self):
        from tradingagents.tradeflow.event_source import EventSourceResult
        with patch("tradingagents.tradeflow.event_source.fetch_daily_events_detailed") as mock_fetch:
            mock_fetch.return_value = EventSourceResult()
            with patch("tradingagents.tradeflow.discovery.evaluate_symbol", return_value=(None, "无策略命中")):
                run_discovery(
                    trade_date="2026-05-29",
                    use_event_source=False,
                    include_holdings=False,
                    include_watchlist=False,
                )
        mock_fetch.assert_not_called()


class TestRunDiscoveryPersistence:
    def test_save_candidates(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tf_db = f.name
        try:
            init_db(tf_db)
            c = _make_candidate("A.SZ", score=60.0)

            with patch("tradingagents.tradeflow.discovery.evaluate_symbol", return_value=(c, "")):
                result = run_discovery(
                    trade_date="2026-05-29",
                    symbols=["A.SZ"],
                    tf_db_path=tf_db,
                    save_candidates=True,
                    include_holdings=False,
                    include_watchlist=False,
                )

            import sqlite3
            conn = sqlite3.connect(tf_db)
            row = conn.execute(
                "SELECT * FROM tradeflow_candidates WHERE symbol='A.SZ' AND trade_date='2026-05-29'"
            ).fetchone()
            conn.close()
            assert row is not None
        finally:
            os.unlink(tf_db)

    def test_no_save_when_disabled(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tf_db = f.name
        try:
            init_db(tf_db)
            c = _make_candidate("A.SZ", score=60.0)

            with patch("tradingagents.tradeflow.discovery.evaluate_symbol", return_value=(c, "")):
                result = run_discovery(
                    trade_date="2026-05-29",
                    symbols=["A.SZ"],
                    tf_db_path=tf_db,
                    save_candidates=False,
                    include_holdings=False,
                    include_watchlist=False,
                )

            import sqlite3
            conn = sqlite3.connect(tf_db)
            count = conn.execute(
                "SELECT COUNT(*) FROM tradeflow_candidates WHERE symbol='A.SZ'"
            ).fetchone()[0]
            conn.close()
            assert count == 0
        finally:
            os.unlink(tf_db)


class TestRenderDiscoveryText:
    def test_basic_render(self):
        result = DiscoveryResult(
            trade_date="2026-05-29",
            universe_size=5,
            candidates=[
                {"symbol": "A.SZ", "name": "A", "source": "manual",
                 "action": "OBSERVE", "score": 60, "strategies": ["VCP"],
                 "reason": "test", "need_deep_ta": False,
                 "risk_flags": [], "trigger_price": None, "invalid_price": None,
                 "game_balance": ""},
            ],
            filtered=[
                {"symbol": "B.SZ", "name": "B", "source": "manual", "reason": "无策略命中"},
            ],
            summary="共扫描5只，1只候选(Top20)，0只需要深度TA，1只被过滤，无策略命中1只。",
            metadata={"sources": ["manual"], "filter_breakdown": {"无策略命中": 1}},
        )
        text = render_discovery_text(result)
        assert "2026-05-29" in text
        assert "A.SZ" in text
        assert "被过滤" in text

    def test_render_empty_candidates(self):
        result = DiscoveryResult(trade_date="2026-05-29")
        text = render_discovery_text(result)
        assert "无候选" in text

    def test_render_filtered_truncation(self):
        filtered = [
            {"symbol": f"S{i}.SZ", "name": "", "source": "manual", "reason": "无策略命中"}
            for i in range(25)
        ]
        result = DiscoveryResult(
            trade_date="2026-05-29",
            filtered=filtered,
            metadata={"filter_breakdown": {"无策略命中": 25}},
        )
        text = render_discovery_text(result)
        assert "仅显示前20只" in text

    def test_render_deep_ta_warning(self):
        result = DiscoveryResult(
            trade_date="2026-05-29",
            candidates=[
                {"symbol": "A.SZ", "name": "", "source": "manual",
                 "action": "NEED_DEEP_TA", "score": 70, "strategies": [],
                 "reason": "", "need_deep_ta": True,
                 "risk_flags": [], "trigger_price": None, "invalid_price": None,
                 "game_balance": ""},
            ],
        )
        text = render_discovery_text(result)
        assert "需要深度 TA" in text

    def test_render_game_balance(self):
        result = DiscoveryResult(
            trade_date="2026-05-29",
            candidates=[
                {"symbol": "A.SZ", "name": "", "source": "manual",
                 "action": "OBSERVE", "score": 70, "strategies": [],
                 "reason": "", "need_deep_ta": False,
                 "risk_flags": [], "trigger_price": None, "invalid_price": None,
                 "game_balance": "favorable"},
            ],
        )
        text = render_discovery_text(result)
        assert "favorable" in text


class TestDiscoveryEventOverrides:
    def test_event_overrides_passed_per_symbol(self):
        overrides = [
            {"symbol": "AAA.SZ", "title": "AAA回购"},
            {"symbol": "BBB.SZ", "title": "BBB业绩"},
        ]

        def fake_evaluate(symbol, **kwargs):
            ev_list = kwargs.get("event_overrides") or []
            if symbol == "AAA.SZ":
                assert len(ev_list) == 1
                assert ev_list[0]["symbol"] == "AAA.SZ"
                return _make_candidate("AAA.SZ", score=60.0), ""
            elif symbol == "BBB.SZ":
                assert len(ev_list) == 1
                assert ev_list[0]["symbol"] == "BBB.SZ"
                return _make_candidate("BBB.SZ", score=55.0), ""
            return None, "无策略命中"

        with patch("tradingagents.tradeflow.discovery.evaluate_symbol", side_effect=fake_evaluate):
            result = run_discovery(
                trade_date="2026-05-29",
                symbols=["AAA.SZ", "BBB.SZ"],
                event_overrides=overrides,
                include_holdings=False,
                include_watchlist=False,
            )
        assert len(result.candidates) == 2


class TestDiscoveryDateHandling:
    def test_default_date_today(self):
        with patch("tradingagents.tradeflow.discovery.evaluate_symbol", return_value=(None, "无策略命中")):
            result = run_discovery(
                include_holdings=False,
                include_watchlist=False,
            )
        from datetime import datetime
        assert result.trade_date == datetime.now().strftime("%Y-%m-%d")

    def test_explicit_date(self):
        with patch("tradingagents.tradeflow.discovery.evaluate_symbol", return_value=(None, "无策略命中")):
            result = run_discovery(
                trade_date="2026-01-15",
                include_holdings=False,
                include_watchlist=False,
            )
        assert result.trade_date == "2026-01-15"
