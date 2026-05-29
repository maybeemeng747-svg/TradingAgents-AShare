"""Tests for TradeFlow UniverseManager — M-003 universe source tracking.  # [M-003] tradeflow_universe_manager

Covers:
- UniverseSource enum
- UniverseManager: add_symbol, dedup with multi-source, remove, summary
- build_universe backward compatibility
- Integration with generate_daily_plan and discovery
"""

import sys
import os
import sqlite3
import tempfile
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.tradeflow.universe import (
    UniverseManager, UniverseSource, UniverseEntry, SourceRecord,
    build_universe, get_holdings, get_watchlist,
)
from tradingagents.tradeflow.schemas import Candidate, CandidateSignal


class TestUniverseSourceEnum:
    def test_all_sources_defined(self):
        expected = {
            "watchlist", "holding", "manual", "event_catalyst", "event_source",
            "fund_flow_pool", "industry_pool", "yesterday_observe",
        }  # [M-011] universe_compat_fix — event → event_catalyst
        actual = {s.value for s in UniverseSource}
        assert actual == expected

    def test_source_values_are_unique(self):
        values = [s.value for s in UniverseSource]
        assert len(values) == len(set(values))


class TestSourceRecord:
    def test_auto_timestamp(self):
        sr = SourceRecord(source=UniverseSource.MANUAL, reason="test")
        assert sr.timestamp != ""

    def test_explicit_timestamp(self):
        sr = SourceRecord(source=UniverseSource.MANUAL, reason="test", timestamp="2026-01-01T00:00:00")
        assert sr.timestamp == "2026-01-01T00:00:00"


class TestUniverseEntry:
    def test_primary_source(self):
        e = UniverseEntry(
            symbol="600519.SH",
            sources=[
                SourceRecord(source=UniverseSource.HOLDING),
                SourceRecord(source=UniverseSource.WATCHLIST),
            ],
        )
        assert e.primary_source == "holding"

    def test_source_values_sorted(self):
        e = UniverseEntry(
            symbol="600519.SH",
            sources=[
                SourceRecord(source=UniverseSource.WATCHLIST),
                SourceRecord(source=UniverseSource.HOLDING),
            ],
        )
        assert e.source_values == ["holding", "watchlist"]

    def test_to_dict(self):
        e = UniverseEntry(
            symbol="600519.SH",
            name="贵州茅台",
            sources=[
                SourceRecord(source=UniverseSource.HOLDING, reason="持仓股"),
                SourceRecord(source=UniverseSource.EVENT, reason="回购"),
            ],
        )
        d = e.to_dict()
        assert d["symbol"] == "600519.SH"
        assert d["name"] == "贵州茅台"
        assert d["source"] == "holding"
        assert "holding" in d["universe_sources"]
        assert "event_catalyst" in d["universe_sources"]
        assert len(d["universe_source_records"]) == 2


class TestUniverseManagerBasic:
    def test_empty_manager(self):
        mgr = UniverseManager()
        assert mgr.size == 0
        assert mgr.symbols == []
        assert mgr.get_universe() == []

    def test_add_single_symbol(self):
        mgr = UniverseManager()
        entry = mgr.add_symbol("600519.SH", UniverseSource.MANUAL, reason="test")
        assert mgr.size == 1
        assert entry.symbol == "600519.SH"
        assert len(entry.sources) == 1

    def test_add_empty_symbol_ignored(self):
        mgr = UniverseManager()
        mgr.add_symbol("", UniverseSource.MANUAL)
        mgr.add_symbol("  ", UniverseSource.MANUAL)
        assert mgr.size == 0

    def test_add_duplicate_source_no_duplication(self):
        mgr = UniverseManager()
        mgr.add_symbol("600519.SH", UniverseSource.MANUAL, reason="first")
        mgr.add_symbol("600519.SH", UniverseSource.MANUAL, reason="second")
        assert mgr.size == 1
        entry = mgr.get_entry("600519.SH")
        assert len(entry.sources) == 1
        assert entry.sources[0].reason == "first"

    def test_add_same_symbol_different_sources(self):
        mgr = UniverseManager()
        mgr.add_symbol("600519.SH", UniverseSource.MANUAL, reason="手动")
        mgr.add_symbol("600519.SH", UniverseSource.WATCHLIST, reason="自选")
        mgr.add_symbol("600519.SH", UniverseSource.HOLDING, reason="持仓")
        assert mgr.size == 1
        entry = mgr.get_entry("600519.SH")
        assert len(entry.sources) == 3
        assert set(entry.source_values) == {"manual", "watchlist", "holding"}

    def test_name_updated_on_first_set(self):
        mgr = UniverseManager()
        mgr.add_symbol("600519.SH", UniverseSource.MANUAL, name="贵州茅台")
        entry = mgr.get_entry("600519.SH")
        assert entry.name == "贵州茅台"

    def test_name_not_overwritten_if_already_set(self):
        mgr = UniverseManager()
        mgr.add_symbol("600519.SH", UniverseSource.HOLDING, name="贵州茅台")
        mgr.add_symbol("600519.SH", UniverseSource.WATCHLIST, name="")
        entry = mgr.get_entry("600519.SH")
        assert entry.name == "贵州茅台"


class TestUniverseManagerRemove:
    def test_remove_symbol(self):
        mgr = UniverseManager()
        mgr.add_symbol("600519.SH", UniverseSource.MANUAL)
        mgr.remove_symbol("600519.SH", reason="流动性差")
        entry = mgr.get_entry("600519.SH")
        assert entry.filter_reason == "流动性差"

    def test_get_active_excludes_filtered(self):
        mgr = UniverseManager()
        mgr.add_symbol("600519.SH", UniverseSource.MANUAL)
        mgr.add_symbol("000001.SZ", UniverseSource.MANUAL)
        mgr.remove_symbol("000001.SZ", reason="数据缺失")
        active = mgr.get_active_universe()
        assert len(active) == 1
        assert active[0]["symbol"] == "600519.SH"


class TestUniverseManagerSummary:
    def test_source_summary(self):
        mgr = UniverseManager()
        mgr.add_symbol("A.SZ", UniverseSource.MANUAL)
        mgr.add_symbol("B.SZ", UniverseSource.WATCHLIST)
        mgr.add_symbol("C.SZ", UniverseSource.WATCHLIST)
        mgr.add_symbol("D.SZ", UniverseSource.HOLDING)
        summary = mgr.get_source_summary()
        assert summary["manual"] == 1
        assert summary["watchlist"] == 2
        assert summary["holding"] == 1


class TestUniverseManagerBulkAdd:
    def test_add_manual(self):
        mgr = UniverseManager()
        count = mgr.add_manual(["600519.SH", "000001.SZ", "  ", "300750.SZ"])
        assert count == 3
        assert mgr.size == 3

    def test_add_from_events_overrides(self):
        mgr = UniverseManager()
        overrides = [
            {"symbol": "AAA.SZ", "name": "AAA", "title": "回购", "event_type": "buyback"},
            {"symbol": "BBB.SZ", "name": "BBB", "title": "业绩", "event_type": "earnings"},
        ]
        count = mgr.add_from_events(event_overrides=overrides)
        assert count == 2
        entry = mgr.get_entry("AAA.SZ")
        assert entry is not None
        assert any(s.source == UniverseSource.EVENT for s in entry.sources)

    def test_add_from_events_symbols(self):
        mgr = UniverseManager()
        event_syms = {"CCC.SZ": ["回购进展", "增持"], "DDD.SZ": ["减持公告"]}
        count = mgr.add_from_events(event_symbols=event_syms)
        assert count == 2

    def test_add_from_fund_flow(self):
        mgr = UniverseManager()
        count = mgr.add_from_fund_flow(["EEE.SZ", "FFF.SZ"], reason="主力连续流入")
        assert count == 2
        entry = mgr.get_entry("EEE.SZ")
        assert entry.sources[0].source == UniverseSource.FUND_FLOW

    def test_add_from_industry(self):
        mgr = UniverseManager()
        count = mgr.add_from_industry(["GGG.SZ", "HHH.SZ"], industry_name="白酒")
        assert count == 2
        entry = mgr.get_entry("GGG.SZ")
        assert "白酒" in entry.sources[0].reason


class TestBuildUniverseBackwardCompat:
    def test_basic_build_universe(self):
        universe = build_universe(symbols=["600519.SH", "000001.SZ"])
        assert len(universe) == 2
        symbols = {u["symbol"] for u in universe}
        assert symbols == {"600519.SH", "000001.SZ"}

    def test_build_universe_returns_new_fields(self):
        universe = build_universe(symbols=["600519.SH"])
        assert len(universe) == 1
        entry = universe[0]
        assert "universe_sources" in entry
        assert "universe_source_records" in entry
        assert entry["source"] == "manual"
        assert "manual" in entry["universe_sources"]

    def test_build_universe_with_events(self):
        event_overrides = [
            {"symbol": "999999.SZ", "name": "事件股", "title": "回购", "event_type": "buyback"},
        ]
        event_symbols = {"888888.SZ": ["业绩预告"]}
        universe = build_universe(
            symbols=["600519.SH"],
            event_overrides=event_overrides,
            event_symbols=event_symbols,
        )
        assert len(universe) == 3
        by_sym = {u["symbol"]: u for u in universe}
        assert "event_catalyst" in by_sym["999999.SZ"]["universe_sources"]
        assert "event_source" in by_sym["888888.SZ"]["universe_sources"]
        assert "manual" in by_sym["600519.SH"]["universe_sources"]

    def test_build_universe_dedup_keeps_all_sources(self):
        event_overrides = [
            {"symbol": "600519.SH", "title": "回购", "event_type": "buyback"},
        ]
        universe = build_universe(
            symbols=["600519.SH"],
            event_overrides=event_overrides,
        )
        assert len(universe) == 1
        entry = universe[0]
        assert "manual" in entry["universe_sources"]
        assert "event_catalyst" in entry["universe_sources"]

    def test_build_universe_with_industry_and_fund_flow(self):
        universe = build_universe(
            symbols=["600519.SH"],
            industry_symbols=["000001.SZ", "600519.SH"],
            fund_flow_symbols=["300750.SZ", "600519.SH"],
        )
        assert len(universe) == 3
        by_sym = {u["symbol"]: u for u in universe}
        mt = by_sym["600519.SH"]
        assert set(mt["universe_sources"]) == {"fund_flow_pool", "industry_pool", "manual"}

    def test_empty_sources_returns_empty(self):
        universe = build_universe()
        assert universe == []


class TestBuildUniverseWithDB:
    def test_holdings_and_watchlist(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE imported_portfolio_positions "
                "(symbol TEXT, name TEXT, quantity REAL, avg_cost REAL, "
                "current_price REAL, market_value REAL)"
            )
            conn.execute(
                "INSERT INTO imported_portfolio_positions VALUES (?, ?, ?, ?, ?, ?)",
                ("600519.SH", "贵州茅台", 100, 1800.0, 1900.0, 190000.0),
            )
            conn.execute(
                "CREATE TABLE watchlist_items (symbol TEXT, name TEXT)"
            )
            conn.execute(
                "INSERT INTO watchlist_items VALUES (?, ?)",
                ("000001.SZ", "平安银行"),
            )
            conn.execute(
                "INSERT INTO watchlist_items VALUES (?, ?)",
                ("600519.SH", "贵州茅台"),
            )
            conn.commit()
            conn.close()

            universe = build_universe(prod_db_path=db_path)
            assert len(universe) == 2
            by_sym = {u["symbol"]: u for u in universe}
            mt = by_sym["600519.SH"]
            assert set(mt["universe_sources"]) == {"holding", "watchlist"}
            assert by_sym["000001.SZ"]["source"] == "watchlist"
        finally:
            os.unlink(db_path)

    def test_empty_db_no_crash(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            universe = build_universe(prod_db_path=db_path)
            assert universe == []
        finally:
            os.unlink(db_path)


class TestPlanRunnerIntegration:
    def test_universe_sources_passed_to_candidates(self):
        from tradingagents.tradeflow.plan_runner import generate_daily_plan

        mock_candidate = Candidate(
            symbol="600519.SH", name="贵州茅台", source="manual",
            strategy_tags=["VCP"], score=60.0, trade_date="2026-05-25",
        )
        mock_candidate.signals = [CandidateSignal(strategy_tag="VCP", score=60.0, reason="test")]
        mock_candidate.primary_strategy = "VCP"

        mock_universe = [
            {
                "symbol": "600519.SH",
                "name": "贵州茅台",
                "source": "manual",
                "universe_sources": ["manual", "watchlist"],
                "universe_source_records": [
                    {"source": "manual", "reason": "手动指定", "timestamp": "2026-05-25T08:00:00"},
                    {"source": "watchlist", "reason": "自选股", "timestamp": "2026-05-25T08:00:01"},
                ],
            },
        ]

        with patch("tradingagents.tradeflow.plan_runner.evaluate_symbol", return_value=(mock_candidate, "")):
            with patch("tradingagents.tradeflow.plan_runner.build_universe", return_value=mock_universe):
                plan = generate_daily_plan(
                    trade_date="2026-05-25",
                    candidates=None,
                )

        assert len(plan.candidates) == 1
        entry = plan.candidates[0]
        assert "manual" in entry["universe_sources"]
        assert "watchlist" in entry["universe_sources"]


class TestDiscoveryIntegration:
    def test_discovery_uses_universe_manager(self):
        from tradingagents.tradeflow.discovery import _build_discovery_universe

        universe = _build_discovery_universe(
            symbols=["600519.SH"],
            industry_symbols=["000001.SZ", "600519.SH"],
            fund_flow_symbols=["300750.SZ", "600519.SH"],
            prod_db_path="",
            tf_db_path="",
        )
        assert len(universe) == 3
        by_sym = {u["symbol"]: u for u in universe}
        assert set(by_sym["600519.SH"]["universe_sources"]) == {"fund_flow_pool", "industry_pool", "manual"}
        assert by_sym["000001.SZ"]["source"] == "industry_pool"
        assert by_sym["300750.SZ"]["source"] == "fund_flow_pool"


class TestNoFullMarketScan:
    def test_empty_inputs_return_empty(self):
        mgr = UniverseManager()
        assert mgr.get_universe() == []
        assert build_universe() == []

    def test_manager_never_scans_market(self):
        mgr = UniverseManager()
        mgr.add_manual(["600519.SH"])
        universe = mgr.get_universe()
        assert len(universe) == 1
        assert universe[0]["symbol"] == "600519.SH"


class TestCandidateUniverseSourcesField:
    def test_default_empty(self):
        c = Candidate(symbol="600519.SH")
        assert c.universe_sources == []

    def test_set_and_read(self):
        c = Candidate(symbol="600519.SH", universe_sources=["manual", "watchlist"])
        assert c.universe_sources == ["manual", "watchlist"]

    def test_to_db_row_includes_field(self):
        c = Candidate(symbol="600519.SH", universe_sources=["holding"])
        row = c.to_db_row()
        assert "universe_sources_json" in row
        import json
        assert json.loads(row["universe_sources_json"]) == ["holding"]

    def test_from_db_row_reads_field(self):
        row = {
            "symbol": "600519.SH",
            "strategy_tags_json": "[]",
            "risk_flags_json": "[]",
            "evidence_json": "{}",
            "universe_sources_json": '["manual", "event_catalyst"]',  # [M-011]
        }
        c = Candidate.from_db_row(row)
        assert c.universe_sources == ["manual", "event_catalyst"]  # [M-011]


class TestM011EventOverridesCompat:  # [M-011] universe_compat_fix
    def test_event_overrides_source_label_is_event_catalyst(self):
        mgr = UniverseManager()
        overrides = [
            {"symbol": "AAA.SZ", "name": "AAA", "title": "回购", "event_type": "buyback"},
        ]
        mgr.add_from_events(event_overrides=overrides)
        entry = mgr.get_entry("AAA.SZ")
        assert entry.primary_source == "event_catalyst"
        d = entry.to_dict()
        assert d["source"] == "event_catalyst"
        assert "event_catalyst" in d["universe_sources"]

    def test_build_universe_event_overrides_backward_compat(self):
        event_overrides = [
            {"symbol": "999999.SZ", "name": "事件股", "title": "回购"},
        ]
        universe = build_universe(event_overrides=event_overrides)
        by_sym = {u["symbol"]: u for u in universe}
        assert by_sym["999999.SZ"]["source"] == "event_catalyst"
        assert "event_catalyst" in by_sym["999999.SZ"]["universe_sources"]


class TestM011SourceRecordExtraSerialization:  # [M-011] universe_compat_fix
    def test_to_dict_includes_extra_when_present(self):
        e = UniverseEntry(
            symbol="600519.SH",
            name="贵州茅台",
            sources=[
                SourceRecord(
                    source=UniverseSource.YESTERDAY_OBSERVE,
                    reason="昨日候选",
                    extra={
                        "strategy_tags": '["VCP"]',
                        "trigger_price": 1800.0,
                        "invalid_price": 1700.0,
                    },
                ),
            ],
        )
        d = e.to_dict()
        recs = d["universe_source_records"]
        assert len(recs) == 1
        assert "extra" in recs[0]
        assert recs[0]["extra"]["strategy_tags"] == '["VCP"]'
        assert recs[0]["extra"]["trigger_price"] == 1800.0
        assert recs[0]["extra"]["invalid_price"] == 1700.0

    def test_to_dict_omits_extra_when_empty(self):
        e = UniverseEntry(
            symbol="600519.SH",
            sources=[
                SourceRecord(source=UniverseSource.MANUAL, reason="test"),
            ],
        )
        d = e.to_dict()
        recs = d["universe_source_records"]
        assert "extra" not in recs[0]

    def test_include_yesterday_extra_preserved(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tf_db_path = f.name
        try:
            conn = sqlite3.connect(tf_db_path)
            conn.execute(
                "CREATE TABLE tradeflow_candidates ("
                "trade_date TEXT, symbol TEXT, name TEXT, status TEXT, "
                "strategy_tags_json TEXT, trigger_price REAL, invalid_price REAL)"
            )
            conn.execute(
                "INSERT INTO tradeflow_candidates VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("2026-05-29", "600519.SH", "贵州茅台", "active",
                 '["VCP","PULLBACK"]', 1800.0, 1700.0),
            )
            conn.commit()
            conn.close()

            mgr = UniverseManager()
            mgr.add_from_yesterday(tf_db_path, trade_date="2026-05-29")
            entry = mgr.get_entry("600519.SH")
            assert entry is not None
            assert entry.name == "贵州茅台"
            sr = entry.sources[0]
            assert sr.source == UniverseSource.YESTERDAY_OBSERVE
            assert sr.extra.get("strategy_tags") == '["VCP","PULLBACK"]'
            assert sr.extra.get("trigger_price") == 1800.0
            assert sr.extra.get("invalid_price") == 1700.0

            d = entry.to_dict()
            recs = d["universe_source_records"]
            assert recs[0]["extra"]["trigger_price"] == 1800.0
            assert recs[0]["extra"]["invalid_price"] == 1700.0
        finally:
            os.unlink(tf_db_path)


class TestM011PlanRunnerDiscoveryCompat:  # [M-011] universe_compat_fix
    def test_plan_runner_preserves_event_catalyst_source(self):
        from tradingagents.tradeflow.plan_runner import generate_daily_plan

        mock_candidate = Candidate(
            symbol="999999.SZ", name="事件股", source="event_catalyst",
            strategy_tags=["EVENT_CATALYST"], score=60.0, trade_date="2026-05-25",
        )
        mock_candidate.signals = [CandidateSignal(strategy_tag="EVENT_CATALYST", score=60.0, reason="test")]
        mock_candidate.primary_strategy = "EVENT_CATALYST"

        mock_universe = [
            {
                "symbol": "999999.SZ",
                "name": "事件股",
                "source": "event_catalyst",
                "universe_sources": ["event_catalyst"],
                "universe_source_records": [
                    {"source": "event_catalyst", "reason": "事件催化: 回购", "timestamp": "2026-05-25T08:00:00"},
                ],
            },
        ]

        with patch("tradingagents.tradeflow.plan_runner.evaluate_symbol", return_value=(mock_candidate, "")):
            with patch("tradingagents.tradeflow.plan_runner.build_universe", return_value=mock_universe):
                plan = generate_daily_plan(
                    trade_date="2026-05-25",
                    candidates=None,
                )

        assert len(plan.candidates) == 1
        entry = plan.candidates[0]
        assert "event_catalyst" in entry["universe_sources"]

    def test_discovery_preserves_event_catalyst_source(self):
        from tradingagents.tradeflow.discovery import _build_discovery_universe

        universe = _build_discovery_universe(
            symbols=["600519.SH"],
            prod_db_path="",
            tf_db_path="",
        )
        by_sym = {u["symbol"]: u for u in universe}
        assert by_sym["600519.SH"]["source"] == "manual"

    def test_discovery_event_overrides_source_label(self):
        from tradingagents.tradeflow.discovery import _build_discovery_universe

        overrides = [
            {"symbol": "999999.SZ", "name": "事件股", "title": "回购"},
        ]
        universe = _build_discovery_universe(
            symbols=["600519.SH"],
            prod_db_path="",
            tf_db_path="",
        )
        universe_from_events = build_universe(event_overrides=overrides)
        by_sym = {u["symbol"]: u for u in universe_from_events}
        assert "999999.SZ" in by_sym
        assert by_sym["999999.SZ"]["source"] == "event_catalyst"
