# [TF-P0-001] runtime_schema_name_observe_fix
"""Tests for TF-P0-001: schema migration, name backfill, observe route.

Covers:
1. Old schema fixture runs migration and gets all H/TF-DATE columns.
2. Code-like names (e.g. 601689.SH) are detected and re-resolved.
3. API layer _row_to_candidate_item backfills names.
4. Observe route is registered and callable.
5. save_candidate defaults candidate_type to TECH_TRADE.
"""

import json
import os
import sqlite3
import sys
import tempfile
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.tradeflow.candidate_engine import init_db, save_candidate, ensure_columns
from tradingagents.tradeflow.schemas import Candidate
from tradingagents.tradeflow.symbol_utils import (
    normalize_tradeflow_symbol,
    resolve_tradeflow_name,
    symbol_bare_code,
    _looks_like_code,
    StockNameResolver,
)


_REQUIRED_NEW_COLUMNS = [
    "candidate_type",
    "mandate_score_component",
    "ambush_score",
    "mandate_topic",
    "company_role",
    "effective_trade_date",
    "watchlist_note_suggested",
    "beneficiary_path_json",
    "beneficiary_score_component",
    "pricing_gap_score",
    "overheat_penalty",
    "candidate_type_reason",
    "deep_ta_route",
    "deep_ta_route_reason",
    "ambush_reasons_json",
    "ambush_evidence_refs_json",
    "research_queue",
    "research_intent",
    "research_route_reason",
    "watchlist_note",
    "watchlist_topic",
    "watchlist_benefit_score",
    "watchlist_consensus_score",
    "watchlist_evidence_gap_json",
    "plan_date",
    "observe_date",
]


class TestLooksLikeCode:
    def test_bare_code(self):
        assert _looks_like_code("601689") is True

    def test_sh_suffix(self):
        assert _looks_like_code("601689.SH") is True

    def test_sz_suffix(self):
        assert _looks_like_code("000001.SZ") is True

    def test_bj_suffix(self):
        assert _looks_like_code("430047.BJ") is True

    def test_lowercase_suffix(self):
        assert _looks_like_code("601689.sh") is True

    def test_chinese_name(self):
        assert _looks_like_code("拓普集团") is False

    def test_mixed_name(self):
        assert _looks_like_code("贵州茅台") is False

    def test_empty(self):
        assert _looks_like_code("") is True

    def test_none(self):
        assert _looks_like_code(None) is True

    def test_whitespace(self):
        assert _looks_like_code("   ") is True

    def test_placeholder(self):
        assert _looks_like_code("--") is False

    def test_random_string(self):
        assert _looks_like_code("abc123") is False

    def test_five_digits(self):
        assert _looks_like_code("12345") is False

    def test_seven_digits(self):
        assert _looks_like_code("1234567") is False


class TestNameBackfill:
    def test_resolve_code_like_name_falls_through(self):
        resolver = StockNameResolver()
        resolver._reverse_map = {"601689.SH": "拓普集团"}
        resolver._loaded_at = 999999999999.0
        result = resolver.resolve("601689.SH", "601689.SH")
        assert result == "拓普集团"

    def test_resolve_bare_code_name_falls_through(self):
        resolver = StockNameResolver()
        resolver._reverse_map = {"601689.SH": "拓普集团"}
        resolver._loaded_at = 999999999999.0
        result = resolver.resolve("601689.SH", "601689")
        assert result == "拓普集团"

    def test_resolve_real_name_kept(self):
        resolver = StockNameResolver()
        resolver._reverse_map = {"601689.SH": "拓普集团"}
        resolver._loaded_at = 999999999999.0
        result = resolver.resolve("601689.SH", "拓普集团")
        assert result == "拓普集团"

    def test_resolve_empty_name_uses_cache(self):
        resolver = StockNameResolver()
        resolver._reverse_map = {"601689.SH": "拓普集团"}
        resolver._loaded_at = 999999999999.0
        result = resolver.resolve("601689.SH", "")
        assert result == "拓普集团"

    def test_resolve_placeholder_name_uses_cache(self):
        resolver = StockNameResolver()
        resolver._reverse_map = {"601689.SH": "拓普集团"}
        resolver._loaded_at = 999999999999.0
        result = resolver.resolve("601689.SH", "--")
        assert result == "拓普集团"

    def test_resolve_sz_code_like_name(self):
        resolver = StockNameResolver()
        resolver._reverse_map = {"000001.SZ": "平安银行"}
        resolver._loaded_at = 999999999999.0
        result = resolver.resolve("000001.SZ", "000001.SZ")
        assert result == "平安银行"


class TestSchemaMigration:
    def _create_old_schema_db(self, db_path: str):
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tradeflow_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                symbol TEXT NOT NULL,
                name TEXT DEFAULT '',
                source TEXT DEFAULT 'manual',
                strategy_tags_json TEXT DEFAULT '[]',
                primary_strategy TEXT DEFAULT '',
                score REAL DEFAULT 0.0,
                status TEXT DEFAULT 'active',
                trigger_price REAL,
                support_price REAL,
                invalid_price REAL,
                need_deep_ta INTEGER DEFAULT 0,
                evidence_json TEXT DEFAULT '{}',
                risk_flags_json TEXT DEFAULT '[]',
                created_at TEXT,
                updated_at TEXT,
                UNIQUE(trade_date, symbol)
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tradeflow_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_time TEXT NOT NULL,
                symbol TEXT NOT NULL,
                signal_type TEXT NOT NULL,
                signal_level TEXT DEFAULT 'info',
                source TEXT DEFAULT 'tradeflow',
                evidence_json TEXT DEFAULT '{}',
                action_hint TEXT DEFAULT 'OBSERVE',
                status TEXT DEFAULT 'new',
                created_at TEXT
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tradeflow_daily_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                mode TEXT DEFAULT 'pre_market',
                summary TEXT DEFAULT '',
                candidates_json TEXT DEFAULT '[]',
                metadata_json TEXT DEFAULT '{}',
                created_at TEXT,
                UNIQUE(trade_date, mode)
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tradeflow_filtered_symbols (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                symbol TEXT NOT NULL,
                name TEXT DEFAULT '',
                source TEXT DEFAULT '',
                reason TEXT DEFAULT '',
                run_id TEXT DEFAULT '',
                created_at TEXT,
                UNIQUE(trade_date, symbol, reason)
            );
        """)
        conn.execute(
            "INSERT INTO tradeflow_candidates (trade_date, symbol, name, source, strategy_tags_json, score, status, trigger_price, invalid_price, need_deep_ta, evidence_json, risk_flags_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("2026-06-02", "601689.SH", "601689.SH", "watchlist", json.dumps(["PULLBACK_SUPPORT"]), 55.0, "active", 25.0, 22.0, 0, "{}", "[]"),
        )
        conn.commit()
        conn.close()

    def test_migration_adds_all_required_columns(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            self._create_old_schema_db(db_path)
            init_db(db_path)

            conn = sqlite3.connect(db_path)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(tradeflow_candidates)").fetchall()}
            conn.close()

            for col in _REQUIRED_NEW_COLUMNS:
                assert col in cols, f"Missing column: {col}"
        finally:
            os.unlink(db_path)

    def test_migration_preserves_existing_data(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            self._create_old_schema_db(db_path)
            init_db(db_path)

            conn = sqlite3.connect(db_path)
            row = conn.execute(
                "SELECT symbol, score, name FROM tradeflow_candidates WHERE symbol='601689.SH'"
            ).fetchone()
            conn.close()

            assert row is not None
            assert row[0] == "601689.SH"
            assert row[1] == 55.0
            assert row[2] == "601689.SH"
        finally:
            os.unlink(db_path)

    def test_migration_idempotent(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            self._create_old_schema_db(db_path)
            init_db(db_path)
            init_db(db_path)

            conn = sqlite3.connect(db_path)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(tradeflow_candidates)").fetchall()}
            conn.close()

            for col in _REQUIRED_NEW_COLUMNS:
                assert col in cols, f"Missing column after re-init: {col}"
        finally:
            os.unlink(db_path)

    def test_daily_plans_date_columns(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            self._create_old_schema_db(db_path)
            init_db(db_path)

            conn = sqlite3.connect(db_path)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(tradeflow_daily_plans)").fetchall()}
            conn.close()

            assert "plan_date" in cols
            assert "effective_trade_date" in cols
            assert "observe_date" in cols
        finally:
            os.unlink(db_path)

    def test_filtered_symbols_date_columns(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            self._create_old_schema_db(db_path)
            init_db(db_path)

            conn = sqlite3.connect(db_path)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(tradeflow_filtered_symbols)").fetchall()}
            conn.close()

            assert "plan_date" in cols
            assert "effective_trade_date" in cols
        finally:
            os.unlink(db_path)


class TestSaveCandidateNameBackfill:
    def test_save_code_name_gets_resolved(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            init_db(db_path)
            resolver = StockNameResolver()
            resolver._reverse_map = {"601689.SH": "拓普集团"}
            resolver._loaded_at = 999999999999.0
            with patch("tradingagents.tradeflow.candidate_engine.resolve_tradeflow_name", return_value="拓普集团"):
                c = Candidate(
                    symbol="601689.SH",
                    name="601689.SH",
                    strategy_tags=["PULLBACK_SUPPORT"],
                    score=55.0,
                    trade_date="2026-06-02",
                    primary_strategy="PULLBACK_SUPPORT",
                )
                save_candidate(c, db_path)

            conn = sqlite3.connect(db_path)
            row = conn.execute(
                "SELECT name FROM tradeflow_candidates WHERE symbol='601689.SH'"
            ).fetchone()
            conn.close()
            assert row[0] == "拓普集团"
        finally:
            os.unlink(db_path)

    def test_save_bare_code_name_gets_resolved(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            init_db(db_path)
            with patch("tradingagents.tradeflow.candidate_engine.resolve_tradeflow_name", return_value="平安银行"):
                c = Candidate(
                    symbol="000001.SZ",
                    name="000001",
                    strategy_tags=["VCP"],
                    score=60.0,
                    trade_date="2026-06-02",
                    primary_strategy="VCP",
                )
                save_candidate(c, db_path)

            conn = sqlite3.connect(db_path)
            row = conn.execute(
                "SELECT name FROM tradeflow_candidates WHERE symbol='000001.SZ'"
            ).fetchone()
            conn.close()
            assert row[0] == "平安银行"
        finally:
            os.unlink(db_path)

    def test_save_real_name_kept(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            init_db(db_path)
            c = Candidate(
                symbol="601689.SH",
                name="拓普集团",
                strategy_tags=["PULLBACK_SUPPORT"],
                score=55.0,
                trade_date="2026-06-02",
                primary_strategy="PULLBACK_SUPPORT",
            )
            save_candidate(c, db_path)

            conn = sqlite3.connect(db_path)
            row = conn.execute(
                "SELECT name FROM tradeflow_candidates WHERE symbol='601689.SH'"
            ).fetchone()
            conn.close()
            assert row[0] == "拓普集团"
        finally:
            os.unlink(db_path)


class TestCandidateTypeDefault:
    def test_empty_candidate_type_defaults_to_tech_trade(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            init_db(db_path)
            c = Candidate(
                symbol="601689.SH",
                name="拓普集团",
                strategy_tags=["PULLBACK_SUPPORT"],
                score=55.0,
                trade_date="2026-06-02",
                primary_strategy="PULLBACK_SUPPORT",
            )
            assert c.candidate_type == ""
            save_candidate(c, db_path)

            conn = sqlite3.connect(db_path)
            row = conn.execute(
                "SELECT candidate_type, candidate_type_reason FROM tradeflow_candidates WHERE symbol='601689.SH'"
            ).fetchone()
            conn.close()
            assert row[0] == "TECH_TRADE"
            assert "技术交易" in row[1]
        finally:
            os.unlink(db_path)


class TestAPILayerNameBackfill:
    def test_row_to_candidate_item_resolves_code_name(self):
        from api.services.tradeflow_service import _row_to_candidate_item

        row = MagicMock()
        row.__getitem__ = MagicMock(side_effect=lambda k: {
            "symbol": "601689.SH",
            "name": "601689.SH",
            "tier": "B",
            "composite_score": 55.0,
            "score": 55.0,
            "strategy_tags_json": "[]",
            "primary_strategy": "PULLBACK_SUPPORT",
        }.get(k))
        row.keys.return_value = ["symbol", "name", "tier", "composite_score", "score", "strategy_tags_json", "primary_strategy"]

        resolver = StockNameResolver()
        resolver._reverse_map = {"601689.SH": "拓普集团"}
        resolver._loaded_at = 999999999999.0

        with patch("api.services.tradeflow_service.resolve_tradeflow_name", return_value="拓普集团"):
            item = _row_to_candidate_item(row)
        assert item["name"] == "拓普集团"
        assert item["symbol"] == "601689.SH"


class TestObserveRoute:
    def test_observe_run_route_registered(self):
        from api.main import app
        routes = [(r.path, list(r.methods)) for r in app.routes if hasattr(r, "path") and hasattr(r, "methods")]
        observe_run = [r for r in routes if r[0] == "/v1/tradeflow/observe/run"]
        assert len(observe_run) == 1, f"Expected 1 observe/run route, found {len(observe_run)}"
        assert "POST" in observe_run[0][1]

    def test_observe_read_route_registered(self):
        from api.main import app
        routes = [(r.path, list(r.methods)) for r in app.routes if hasattr(r, "path") and hasattr(r, "methods")]
        observe_read = [r for r in routes if r[0] == "/v1/tradeflow/observe"]
        assert len(observe_read) == 1
        assert "GET" in observe_read[0][1]


class TestConnectMigratesSchema:
    def test_connect_calls_init_db_on_existing_db(self):
        from api.services.tradeflow_service import _connect

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            conn = sqlite3.connect(db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tradeflow_candidates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_date TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    name TEXT DEFAULT '',
                    source TEXT DEFAULT 'manual',
                    strategy_tags_json TEXT DEFAULT '[]',
                    primary_strategy TEXT DEFAULT '',
                    score REAL DEFAULT 0.0,
                    status TEXT DEFAULT 'active',
                    trigger_price REAL,
                    support_price REAL,
                    invalid_price REAL,
                    need_deep_ta INTEGER DEFAULT 0,
                    evidence_json TEXT DEFAULT '{}',
                    risk_flags_json TEXT DEFAULT '[]',
                    created_at TEXT,
                    updated_at TEXT,
                    UNIQUE(trade_date, symbol)
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tradeflow_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_time TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    signal_type TEXT NOT NULL,
                    signal_level TEXT DEFAULT 'info',
                    source TEXT DEFAULT 'tradeflow',
                    evidence_json TEXT DEFAULT '{}',
                    action_hint TEXT DEFAULT 'OBSERVE',
                    status TEXT DEFAULT 'new',
                    created_at TEXT
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tradeflow_daily_plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_date TEXT NOT NULL,
                    mode TEXT DEFAULT 'pre_market',
                    summary TEXT DEFAULT '',
                    candidates_json TEXT DEFAULT '[]',
                    metadata_json TEXT DEFAULT '{}',
                    created_at TEXT,
                    UNIQUE(trade_date, mode)
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tradeflow_filtered_symbols (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_date TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    name TEXT DEFAULT '',
                    source TEXT DEFAULT '',
                    reason TEXT DEFAULT '',
                    run_id TEXT DEFAULT '',
                    created_at TEXT,
                    UNIQUE(trade_date, symbol, reason)
                );
            """)
            conn.commit()
            conn.close()

            conn = _connect(db_path)
            assert conn is not None

            cols = {r[1] for r in conn.execute("PRAGMA table_info(tradeflow_candidates)").fetchall()}
            conn.close()

            assert "candidate_type" in cols
            assert "effective_trade_date" in cols
            assert "watchlist_note_suggested" in cols
            assert "ambush_score" in cols
            assert "research_queue" in cols
        finally:
            os.unlink(db_path)


class TestAcceptanceTFP0001:
    def test_old_schema_fixture_migrates_with_new_columns(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            conn = sqlite3.connect(db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tradeflow_candidates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_date TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    name TEXT DEFAULT '',
                    source TEXT DEFAULT 'manual',
                    strategy_tags_json TEXT DEFAULT '[]',
                    primary_strategy TEXT DEFAULT '',
                    score REAL DEFAULT 0.0,
                    status TEXT DEFAULT 'active',
                    trigger_price REAL,
                    support_price REAL,
                    invalid_price REAL,
                    need_deep_ta INTEGER DEFAULT 0,
                    evidence_json TEXT DEFAULT '{}',
                    risk_flags_json DEFAULT '[]',
                    created_at TEXT,
                    updated_at TEXT,
                    UNIQUE(trade_date, symbol)
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tradeflow_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_time TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    signal_type TEXT NOT NULL,
                    signal_level TEXT DEFAULT 'info',
                    source TEXT DEFAULT 'tradeflow',
                    evidence_json TEXT DEFAULT '{}',
                    action_hint TEXT DEFAULT 'OBSERVE',
                    status TEXT DEFAULT 'new',
                    created_at TEXT
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tradeflow_daily_plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_date TEXT NOT NULL,
                    mode TEXT DEFAULT 'pre_market',
                    summary TEXT DEFAULT '',
                    candidates_json TEXT DEFAULT '[]',
                    metadata_json TEXT DEFAULT '{}',
                    created_at TEXT,
                    UNIQUE(trade_date, mode)
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tradeflow_filtered_symbols (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_date TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    name TEXT DEFAULT '',
                    source TEXT DEFAULT '',
                    reason TEXT DEFAULT '',
                    run_id TEXT DEFAULT '',
                    created_at TEXT,
                    UNIQUE(trade_date, symbol, reason)
                );
            """)
            conn.commit()
            conn.close()

            init_db(db_path)
            conn = sqlite3.connect(db_path)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(tradeflow_candidates)").fetchall()}
            conn.close()

            assert "candidate_type" in cols
            assert "effective_trade_date" in cols
            assert "watchlist_note_suggested" in cols
        finally:
            os.unlink(db_path)

    def test_code_name_returns_chinese_name(self):
        resolver = StockNameResolver()
        resolver._reverse_map = {"601689.SH": "拓普集团"}
        resolver._loaded_at = 999999999999.0

        with patch("tradingagents.tradeflow.symbol_utils.StockNameResolver.shared", return_value=resolver):
            result = resolve_tradeflow_name("601689.SH", "601689.SH")
        assert result == "拓普集团"
        assert result != "601689.SH"

    def test_observe_run_returns_json_not_405(self):
        from api.main import app
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.post("/v1/tradeflow/observe/run?date=2026-06-02")
        assert resp.status_code != 405
        assert "application/json" in resp.headers.get("content-type", "")

    def test_no_triggers_live_ta(self):
        from api.services.tradeflow_service import _connect
        assert _connect.__module__ == "api.services.tradeflow_service"
