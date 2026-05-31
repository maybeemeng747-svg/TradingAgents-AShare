# [UI-008] tradeflow_field_normalization
"""Tests for TradeFlow symbol normalization and name resolution.

Covers:
- normalize_tradeflow_symbol(): suffix rules for SH/SZ/BJ
- symbol_bare_code(): stripping suffix
- resolve_tradeflow_name(): fallback chain with mock
- Integration: bare event codes normalized in universe/candidate/API pipeline
"""

from __future__ import annotations

import os
import sys
import sqlite3
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.tradeflow.symbol_utils import (
    normalize_tradeflow_symbol,
    symbol_bare_code,
    resolve_tradeflow_name,
    StockNameResolver,
)
from tradingagents.tradeflow.universe import UniverseManager, UniverseSource
from tradingagents.tradeflow.candidate_engine import init_db, save_candidate
from tradingagents.tradeflow.schemas import Candidate, CandidateSignal
from api.services.tradeflow_service import (
    get_candidates,
    get_daily_plan,
    _row_to_candidate_item,
)


class TestNormalizeSymbol:
    def test_bare_sh_code(self):
        assert normalize_tradeflow_symbol("600519") == "600519.SH"

    def test_bare_sz_code(self):
        assert normalize_tradeflow_symbol("002600") == "002600.SZ"

    def test_bare_sh_688(self):
        assert normalize_tradeflow_symbol("688617") == "688617.SH"

    def test_bare_sh_9xx(self):
        assert normalize_tradeflow_symbol("900901") == "900901.SH"

    def test_bare_sz_0xx(self):
        assert normalize_tradeflow_symbol("000338") == "000338.SZ"

    def test_bare_sz_3xx(self):
        assert normalize_tradeflow_symbol("300750") == "300750.SZ"

    def test_bare_bj_4xx(self):
        assert normalize_tradeflow_symbol("430047") == "430047.BJ"

    def test_bare_bj_8xx(self):
        assert normalize_tradeflow_symbol("830799") == "830799.BJ"

    def test_already_suffixed_sh(self):
        assert normalize_tradeflow_symbol("600519.SH") == "600519.SH"

    def test_already_suffixed_sz(self):
        assert normalize_tradeflow_symbol("002600.SZ") == "002600.SZ"

    def test_ss_normalized_to_sh(self):
        assert normalize_tradeflow_symbol("600519.SS") == "600519.SH"

    def test_lowercase_normalized(self):
        assert normalize_tradeflow_symbol("600519.sh") == "600519.SH"

    def test_no_double_suffix(self):
        assert normalize_tradeflow_symbol("600519.SH") != "600519.SH.SH"

    def test_whitespace_stripped(self):
        assert normalize_tradeflow_symbol("  002600  ") == "002600.SZ"

    def test_empty_returns_empty(self):
        assert normalize_tradeflow_symbol("") == ""

    def test_non_cn_ticker_passthrough(self):
        assert normalize_tradeflow_symbol("AAPL") == "AAPL"

    def test_non_six_digit_passthrough(self):
        assert normalize_tradeflow_symbol("123") == "123"


class TestSymbolBareCode:
    def test_strip_sh(self):
        assert symbol_bare_code("600519.SH") == "600519"

    def test_strip_sz(self):
        assert symbol_bare_code("002600.SZ") == "002600"

    def test_strip_bj(self):
        assert symbol_bare_code("430047.BJ") == "430047"

    def test_bare_stays(self):
        assert symbol_bare_code("600519") == "600519"

    def test_case_insensitive(self):
        assert symbol_bare_code("600519.sh") == "600519"


class TestResolveName:
    def test_supplied_name_used(self):
        assert resolve_tradeflow_name("600519.SH", "贵州茅台") == "贵州茅台"

    def test_supplied_name_strips_whitespace(self):
        assert resolve_tradeflow_name("600519.SH", "  贵州茅台  ") == "贵州茅台"

    def test_empty_name_returns_placeholder_without_cache(self):
        resolver = StockNameResolver()
        resolver._reverse_map = {}
        resolver._loaded_at = 9999999999.0
        assert resolver.resolve("999999.SH", "") == "--"

    def test_cache_hit(self):
        resolver = StockNameResolver()
        resolver._reverse_map = {"600519.SH": "贵州茅台"}
        resolver._loaded_at = 9999999999.0
        assert resolver.resolve("600519.SH", "") == "贵州茅台"

    def test_bare_code_fallback(self):
        resolver = StockNameResolver()
        resolver._reverse_map = {"600519.SH": "贵州茅台"}
        resolver._loaded_at = 9999999999.0
        assert resolver.resolve("600519", "") == "贵州茅台"

    def test_warm_merges(self):
        resolver = StockNameResolver()
        resolver.warm({"002600.SZ": "顺络电子"})
        resolver._loaded_at = 9999999999.0
        assert resolver.resolve("002600.SZ", "") == "顺络电子"


class TestUniverseNormalization:
    def test_add_symbol_normalizes_bare_code(self):
        mgr = UniverseManager()
        entry = mgr.add_symbol("002600", UniverseSource.MANUAL, reason="test")
        assert entry.symbol == "002600.SZ"

    def test_add_symbol_no_double_suffix(self):
        mgr = UniverseManager()
        entry = mgr.add_symbol("600519.SH", UniverseSource.MANUAL, reason="test")
        assert entry.symbol == "600519.SH"
        assert not entry.symbol.endswith(".SH.SH")

    def test_add_from_events_normalizes(self):
        mgr = UniverseManager()
        count = mgr.add_from_events(event_symbols={"688617": ["回购计划"]})
        assert count == 1
        assert mgr.symbols == ["688617.SH"]

    def test_add_from_events_overrides_normalizes(self):
        mgr = UniverseManager()
        count = mgr.add_from_events(event_overrides=[{"symbol": "000338", "title": "重大事项"}])
        assert count == 1
        assert mgr.symbols == ["000338.SZ"]

    def test_add_manual_normalizes(self):
        mgr = UniverseManager()
        mgr.add_manual(["002600", "688617"])
        assert "002600.SZ" in mgr.symbols
        assert "688617.SH" in mgr.symbols

    def test_dedup_after_normalization(self):
        mgr = UniverseManager()
        mgr.add_symbol("002600", UniverseSource.MANUAL, reason="a")
        mgr.add_symbol("002600.SZ", UniverseSource.EVENT_SOURCE, reason="b")
        assert mgr.size == 1

    def test_name_resolved_on_add(self):
        resolver = StockNameResolver()
        resolver._reverse_map = {"600519.SH": "贵州茅台"}
        resolver._loaded_at = 9999999999.0
        with patch("tradingagents.tradeflow.symbol_utils.StockNameResolver.shared", return_value=resolver):
            mgr = UniverseManager()
            entry = mgr.add_symbol("600519", UniverseSource.MANUAL)
            assert entry.name == "贵州茅台"


class TestCandidateEngineNormalization:
    def test_save_candidate_normalizes_symbol(self, tmp_path):
        db_path = str(tmp_path / "test_norm.db")
        init_db(db_path)

        c = Candidate(
            symbol="002600",
            name="",
            source="manual",
            strategy_tags=["VCP"],
            primary_strategy="VCP",
            score=50.0,
            trade_date="2026-05-30",
            tier="B",
            composite_score=60.0,
            tradeflow_data_completeness=0.5,
        )
        c.signals = [CandidateSignal(strategy_tag="VCP", score=50.0, reason="test")]

        resolver = StockNameResolver()
        resolver._reverse_map = {}
        resolver._loaded_at = 9999999999.0
        with patch("tradingagents.tradeflow.candidate_engine.resolve_tradeflow_name", return_value="--"):
            with patch("tradingagents.tradeflow.candidate_engine.normalize_tradeflow_symbol", side_effect=normalize_tradeflow_symbol):
                save_candidate(c, db_path)

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT symbol, name FROM tradeflow_candidates WHERE trade_date = '2026-05-30'"
        ).fetchone()
        conn.close()
        assert row["symbol"] == "002600.SZ"


class TestAPILayerNormalization:
    def test_row_to_candidate_item_normalizes_symbol(self, tmp_path):
        db_path = str(tmp_path / "test_api_norm.db")
        init_db(db_path)

        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO tradeflow_candidates (trade_date, symbol, name, source) "
            "VALUES ('2026-05-30', '002600', '', 'manual')"
        )
        conn.commit()
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM tradeflow_candidates WHERE trade_date = '2026-05-30'"
        ).fetchone()
        conn.close()

        resolver = StockNameResolver()
        resolver._reverse_map = {}
        resolver._loaded_at = 9999999999.0
        with patch("api.services.tradeflow_service.resolve_tradeflow_name", return_value="--"):
            item = _row_to_candidate_item(row)

        assert item["symbol"] == "002600.SZ"
        assert item["name"] == "--"

    def test_get_candidates_normalizes_symbols(self, tmp_path):
        db_path = str(tmp_path / "test_api_norm2.db")
        init_db(db_path)

        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO tradeflow_candidates (trade_date, symbol, name, source) "
            "VALUES ('2026-05-30', '688617', '', 'event_source')"
        )
        conn.commit()
        conn.close()

        resolver = StockNameResolver()
        resolver._reverse_map = {}
        resolver._loaded_at = 9999999999.0
        with patch("api.services.tradeflow_service.resolve_tradeflow_name", return_value="--"):
            result = get_candidates("2026-05-30", tf_db_path=db_path)

        assert result["status"] == "ok"
        assert len(result["candidates"]) == 1
        assert result["candidates"][0]["symbol"] == "688617.SH"

    def test_daily_plan_normalizes_symbols(self, tmp_path):
        db_path = str(tmp_path / "test_api_plan.db")
        init_db(db_path)

        conn = sqlite3.connect(db_path)
        import json
        candidates_json = json.dumps([{
            "symbol": "000338",
            "name": "",
            "tier": "B",
            "composite_score": 55.0,
            "score": 50.0,
            "strategy_tags": ["VCP"],
            "primary_strategy": "VCP",
            "action": "OBSERVE",
            "reason": "test",
            "source": "manual",
        }])
        conn.execute(
            "INSERT INTO tradeflow_daily_plans (trade_date, mode, candidates_json, metadata_json) "
            "VALUES ('2026-05-30', 'pre_market', ?, '{}')",
            (candidates_json,),
        )
        conn.commit()
        conn.close()

        resolver = StockNameResolver()
        resolver._reverse_map = {}
        resolver._loaded_at = 9999999999.0
        with patch("api.services.tradeflow_service.resolve_tradeflow_name", return_value="--"):
            result = get_daily_plan("2026-05-30", tf_db_path=db_path)

        assert result["status"] == "ok"
        assert result["candidates"][0]["symbol"] == "000338.SZ"
        assert result["candidates"][0]["name"] == "--"
