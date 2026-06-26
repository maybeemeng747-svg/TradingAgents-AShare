# [TRACK-008] observation_bulk_import_export
"""Tests for TRACK-008: 观察仓批量导入/导出 CSV 与去重合并.

Covers:
- CSV parse (Chinese headers, English headers, tab-separated).
- Simple whitespace text parse (quick-paste watchlist).
- Combined "入场区" cell split (e.g. "29.0-30.5").
- Import → bulk upsert (create + update).
- Repeat import does NOT create duplicates (dedup by normalized symbol).
- Existing user notes are preserved on re-import (keep strategy).
- force_overwrite_notes replaces notes when incoming notes non-empty.
- Price boundary 0.0 round-trips (never coerced to None/N/A).
- Export → import round-trip reproduces the item set.
- Error handling: invalid status / empty symbol collected, not fatal.
- Symbol normalization on import (bare 6-digit code, lower-case suffix).
- Forbidden strong-action words scrubbed from imported reason.
- Schemas validate.
- Runtime tier is FAST_RADAR (no LLM).
- Holdings isolation: import never writes to tradingagents.db.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.tradeflow.candidate_engine import init_db

from api.services.tradeflow_service import (
    parse_observation_csv,
    import_observation_csv,
    export_observation_csv,
    get_observation_items,
    create_observation_item,
    update_observation_item,
    _looks_like_observation_csv,
)
from api.tradeflow_schemas import (
    ObservationImportRequest,
    ObservationImportResponse,
    ObservationExportResponse,
)
from api.runtime_tier import tradeflow_endpoint_tier, RuntimeTier


# ── Fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_db(tmp_path):
    db_path = str(tmp_path / "test_track008.db")
    init_db(db_path)
    return db_path


# ── CSV Detection / Parse ────────────────────────────────────────────────

class TestCsvDetection:

    def test_detects_csv_with_chinese_header(self):
        assert _looks_like_observation_csv("代码,名称,入场下沿\n601689,拓普,29") is True

    def test_detects_csv_with_english_header(self):
        assert _looks_like_observation_csv("symbol,name\nA,B") is True

    def test_detects_tab_separated(self):
        assert _looks_like_observation_csv("代码\t名称\n601689\t拓普") is True

    def test_rejects_plain_text_watchlist(self):
        # Single column per line, no commas → not CSV.
        assert _looks_like_observation_csv("601689.SH 拓普集团\n002353 杰瑞") is False

    def test_rejects_empty(self):
        assert _looks_like_observation_csv("") is False
        assert _looks_like_observation_csv("# comment only") is False


class TestParseCsv:

    def test_parse_chinese_headers(self):
        csv = "代码,名称,入场下沿,入场上沿,失效价,理由,备注,优先级\n601689.SH,拓普集团,29.0,30.5,28.0,接近买点,等突破,5\n"
        items = parse_observation_csv(csv)
        assert len(items) == 1
        it = items[0]
        assert it["symbol"] == "601689.SH"
        assert it["name"] == "拓普集团"
        assert it["entry_low"] == "29.0"
        assert it["entry_high"] == "30.5"
        assert it["invalid_price"] == "28.0"
        assert it["notes"] == "等突破"
        assert it["priority"] == "5"

    def test_parse_english_headers(self):
        csv = "symbol,name,entry_low,entry_high\n002353.SZ,杰瑞,34,35.5\n"
        items = parse_observation_csv(csv)
        assert len(items) == 1
        assert items[0]["symbol"] == "002353.SZ"
        assert items[0]["entry_low"] == "34"

    def test_parse_tab_separated(self):
        csv = "代码\t名称\t入场区\n601689.SH\t拓普\t29-30\n"
        items = parse_observation_csv(csv)
        assert len(items) == 1
        assert items[0]["symbol"] == "601689.SH"
        assert items[0]["entry_low"] == 29.0
        assert items[0]["entry_high"] == 30.0

    def test_parse_entry_zone_cell_dash(self):
        csv = "代码,入场区\n600519.SH,1680-1700\n"
        items = parse_observation_csv(csv)
        assert items[0]["entry_low"] == 1680.0
        assert items[0]["entry_high"] == 1700.0

    def test_parse_entry_zone_cell_tilde(self):
        csv = "代码,入场区\n600519.SH,1680~1700\n"
        items = parse_observation_csv(csv)
        assert items[0]["entry_low"] == 1680.0
        assert items[0]["entry_high"] == 1700.0

    def test_parse_entry_zone_single_number(self):
        csv = "代码,入场区\n600519.SH,1680\n"
        items = parse_observation_csv(csv)
        assert items[0]["entry_low"] == 1680.0
        assert items[0]["entry_high"] == 1680.0

    def test_parse_explicit_columns_override_zone(self):
        csv = "代码,入场区,入场下沿,入场上沿\n600519.SH,100-200,1680,1700\n"
        items = parse_observation_csv(csv)
        assert items[0]["entry_low"] == "1680"
        assert items[0]["entry_high"] == "1700"

    def test_parse_strategy_tags(self):
        csv = '代码,主题\n601689.SH,"消费,新能源"\n'
        items = parse_observation_csv(csv)
        assert items[0]["strategy_tags"] == ["消费", "新能源"]

    def test_parse_strategy_tags_pipe(self):
        csv = "代码,tags\n601689.SH,消费|新能源\n"
        items = parse_observation_csv(csv)
        assert items[0]["strategy_tags"] == ["消费", "新能源"]

    def test_parse_skips_empty_symbol_rows(self):
        csv = "代码,名称\n,无代码\n601689.SH,拓普\n"
        items = parse_observation_csv(csv)
        assert len(items) == 1
        assert items[0]["symbol"] == "601689.SH"

    def test_parse_strips_bom(self):
        csv = "\ufeff代码,名称\n601689.SH,拓普\n"
        items = parse_observation_csv(csv)
        assert len(items) == 1
        assert items[0]["symbol"] == "601689.SH"

    def test_parse_empty_returns_empty(self):
        assert parse_observation_csv("") == []
        assert parse_observation_csv("   \n  ") == []


class TestParseTextWatchlist:

    def test_parse_whitespace_text(self):
        text = "601689.SH 拓普集团\n002353 杰瑞股份\n"
        items = parse_observation_csv(text)
        assert len(items) == 2
        assert items[0]["symbol"] == "601689.SH"
        assert items[0]["name"] == "拓普集团"
        assert items[1]["symbol"] == "002353.SZ"  # normalized bare code
        assert items[1]["name"] == "杰瑞股份"

    def test_parse_text_symbol_only(self):
        text = "601689.SH\n002353.SZ\n"
        items = parse_observation_csv(text)
        assert len(items) == 2
        assert items[0]["name"] == ""

    def test_parse_text_skips_comments(self):
        text = "# 这是备注\n601689.SH 拓普\n"
        items = parse_observation_csv(text)
        assert len(items) == 1


class TestParseDedupWithinBlob:

    def test_dedup_same_symbol_merges_notes(self):
        csv = "代码,备注\n601689.SH,备注A\n601689,备注B\n"
        items = parse_observation_csv(csv)
        # Both rows normalize to 601689.SH → merged into one item.
        assert len(items) == 1
        assert "备注A" in items[0]["notes"]
        assert "备注B" in items[0]["notes"]

    def test_dedup_same_symbol_unions_tags(self):
        csv = "代码,主题\n601689.SH,消费\n601689.SH,新能源\n"
        items = parse_observation_csv(csv)
        assert len(items) == 1
        assert set(items[0]["strategy_tags"]) == {"消费", "新能源"}

    def test_dedup_last_non_empty_wins_for_scalar(self):
        csv = "代码,优先级\n601689.SH,1\n601689.SH,9\n"
        items = parse_observation_csv(csv)
        assert len(items) == 1
        assert items[0]["priority"] == "9"


# ── Import ───────────────────────────────────────────────────────────────

class TestImport:

    def test_import_csv_creates_items(self, tmp_db):
        csv = (
            "代码,名称,入场下沿,入场上沿,失效价,理由,备注,优先级\n"
            "601689.SH,拓普集团,29.0,30.5,28.0,接近买点,等突破,5\n"
            "002353.SZ,杰瑞股份,34.0,35.5,33.0,政策利好,等回调,3\n"
        )
        result = import_observation_csv(csv, tf_db_path=tmp_db)
        assert result["status"] == "ok"
        assert result["created_count"] == 2
        assert result["updated_count"] == 0
        assert result["parsed_count"] == 2

        items = get_observation_items(tf_db_path=tmp_db)["items"]
        by_sym = {i["symbol"]: i for i in items}
        assert by_sym["601689.SH"]["entry_low"] == 29.0
        assert by_sym["601689.SH"]["entry_high"] == 30.5
        assert by_sym["601689.SH"]["notes"] == "等突破"
        assert by_sym["002353.SZ"]["priority"] == 3

    def test_import_text_watchlist(self, tmp_db):
        result = import_observation_csv("601689.SH 拓普集团\n002353 杰瑞\n", tf_db_path=tmp_db)
        assert result["status"] == "ok"
        assert result["created_count"] == 2
        items = get_observation_items(tf_db_path=tmp_db)["items"]
        syms = {i["symbol"] for i in items}
        assert "601689.SH" in syms
        assert "002353.SZ" in syms

    def test_import_normalizes_symbols(self, tmp_db):
        csv = "代码,名称\n601689,拓普\n002353.sz,杰瑞\n"
        import_observation_csv(csv, tf_db_path=tmp_db)
        syms = {i["symbol"] for i in get_observation_items(tf_db_path=tmp_db)["items"]}
        assert "601689.SH" in syms
        assert "002353.SZ" in syms

    def test_repeat_import_no_duplicates(self, tmp_db):
        csv = "代码,名称,备注\n601689.SH,拓普,原始备注\n"
        r1 = import_observation_csv(csv, tf_db_path=tmp_db)
        assert r1["created_count"] == 1
        r2 = import_observation_csv(csv, tf_db_path=tmp_db)
        # Second import: symbol already exists → updated, NOT a new row.
        assert r2["created_count"] == 0
        assert r2["updated_count"] == 1
        items = get_observation_items(tf_db_path=tmp_db)["items"]
        assert len(items) == 1  # still only one row

    def test_import_preserves_existing_notes_by_default(self, tmp_db):
        create_observation_item(
            symbol="601689.SH", notes="手动备注", tf_db_path=tmp_db,
        )
        # Re-import without notes → keeps existing.
        import_observation_csv("601689.SH\n", tf_db_path=tmp_db)
        items = get_observation_items(tf_db_path=tmp_db)["items"]
        notes_by_sym = {i["symbol"]: i["notes"] for i in items}
        assert notes_by_sym.get("601689.SH") == "手动备注"

    def test_import_empty_notes_does_not_clear(self, tmp_db):
        create_observation_item(
            symbol="601689.SH", notes="保留我", tf_db_path=tmp_db,
        )
        import_observation_csv("代码,备注\n601689.SH,\n", tf_db_path=tmp_db)
        items = get_observation_items(tf_db_path=tmp_db)["items"]
        assert items[0]["notes"] == "保留我"

    def test_import_force_overwrite_replaces_notes(self, tmp_db):
        create_observation_item(
            symbol="601689.SH", notes="旧备注", tf_db_path=tmp_db,
        )
        import_observation_csv(
            "代码,备注\n601689.SH,新备注\n",
            force_overwrite_notes=True,
            tf_db_path=tmp_db,
        )
        items = get_observation_items(tf_db_path=tmp_db)["items"]
        assert items[0]["notes"] == "新备注"

    def test_import_force_overwrite_with_empty_keeps_existing(self, tmp_db):
        create_observation_item(
            symbol="601689.SH", notes="保留我", tf_db_path=tmp_db,
        )
        import_observation_csv(
            "代码,备注\n601689.SH,\n",
            force_overwrite_notes=True,
            tf_db_path=tmp_db,
        )
        items = get_observation_items(tf_db_path=tmp_db)["items"]
        assert items[0]["notes"] == "保留我"

    def test_import_preserves_zero_boundary(self, tmp_db):
        csv = "代码,入场下沿,入场上沿\n601689.SH,0,0\n"
        import_observation_csv(csv, tf_db_path=tmp_db)
        item = get_observation_items(tf_db_path=tmp_db)["items"][0]
        assert item["entry_low"] == 0.0
        assert item["entry_high"] == 0.0

    def test_import_collects_errors_for_invalid_status(self, tmp_db):
        csv = "代码,状态\n601689.SH,BOGUS\n002353.SZ,watching\n"
        result = import_observation_csv(csv, tf_db_path=tmp_db)
        assert result["errored_count"] == 1
        assert result["created_count"] == 1
        err = result["errored"][0]
        assert err["symbol"] in ("601689.SH", "601689")

    def test_import_collects_errors_for_empty_symbol(self, tmp_db):
        csv = "代码,名称\n,无代码\n002353.SZ,杰瑞\n"
        result = import_observation_csv(csv, tf_db_path=tmp_db)
        # Empty symbol row is dropped at parse → not counted as error,
        # but the valid row still imports.
        assert result["created_count"] == 1

    def test_import_empty_text_returns_ok(self, tmp_db):
        result = import_observation_csv("", tf_db_path=tmp_db)
        assert result["status"] == "ok"
        assert result["created_count"] == 0
        assert result["parsed_count"] == 0

    def test_import_non_string_returns_error(self, tmp_db):
        result = import_observation_csv(123, tf_db_path=tmp_db)  # type: ignore[arg-type]
        assert result["status"] == "error"

    def test_import_scrubs_forbidden_words_in_reason(self, tmp_db):
        csv = "代码,理由\n601689.SH,立即买入这只票\n"
        import_observation_csv(csv, tf_db_path=tmp_db)
        item = get_observation_items(tf_db_path=tmp_db)["items"][0]
        assert "立即买入" not in item["reason"]
        assert "**" in item["reason"]


# ── Export ───────────────────────────────────────────────────────────────

class TestExport:

    def test_export_empty(self, tmp_db):
        result = export_observation_csv(tf_db_path=tmp_db)
        assert result["status"] == "ok"
        assert result["count"] == 0
        # Header row present even when empty.
        assert "代码" in result["csv_text"]
        assert "名称" in result["csv_text"]

    def test_export_has_chinese_headers(self, tmp_db):
        create_observation_item(symbol="601689.SH", name="拓普集团", tf_db_path=tmp_db)
        result = export_observation_csv(tf_db_path=tmp_db)
        headers = result["csv_text"].splitlines()[0].lstrip("\ufeff").split(",")
        for h in ["代码", "名称", "状态", "入场下沿", "入场上沿", "失效价", "备注"]:
            assert h in headers

    def test_export_has_utf8_bom(self, tmp_db):
        create_observation_item(symbol="601689.SH", name="拓普集团", tf_db_path=tmp_db)
        result = export_observation_csv(tf_db_path=tmp_db)
        assert result["csv_text"].startswith("\ufeff")

    def test_export_preserves_zero_prices(self, tmp_db):
        create_observation_item(symbol="601689.SH", entry_low=0, entry_high=0, tf_db_path=tmp_db)
        result = export_observation_csv(tf_db_path=tmp_db)
        # The row should contain "0" for prices, not "N/A" or empty.
        lines = result["csv_text"].splitlines()
        row = lines[1].split(",")
        # entry_low and entry_high positions (after BOM strip on header):
        # 代码,名称,状态,入场下沿,入场上沿,触发价,失效价,...
        assert row[3] == "0"  # entry_low
        assert row[4] == "0"  # entry_high

    def test_export_includes_all_items(self, tmp_db):
        for s in ["601689.SH", "002353.SZ", "600519.SH"]:
            create_observation_item(symbol=s, tf_db_path=tmp_db)
        result = export_observation_csv(tf_db_path=tmp_db)
        assert result["count"] == 3


# ── Round-trip ───────────────────────────────────────────────────────────

class TestRoundTrip:

    def test_export_import_roundtrip(self, tmp_db):
        # Seed items with rich data.
        create_observation_item(
            symbol="601689.SH", name="拓普集团", entry_low=29.0,
            entry_high=30.5, invalid_price=28.0, reason="接近买点",
            notes="等突破", priority=5, tf_db_path=tmp_db,
        )
        create_observation_item(
            symbol="002353.SZ", name="杰瑞股份", entry_low=34.0,
            entry_high=35.5, invalid_price=33.0, reason="政策利好",
            notes="等回调", priority=3, tf_db_path=tmp_db,
        )
        exported = export_observation_csv(tf_db_path=tmp_db)
        assert exported["count"] == 2

        # Clear DB and reimport.
        import sqlite3
        from api.services.tradeflow_service import _connect
        conn = _connect(tmp_db)
        conn.execute("DELETE FROM tradeflow_observation_items")
        conn.commit()
        conn.close()

        reimport = import_observation_csv(exported["csv_text"], tf_db_path=tmp_db)
        assert reimport["status"] == "ok"
        assert reimport["created_count"] == 2

        items = get_observation_items(tf_db_path=tmp_db)["items"]
        by_sym = {i["symbol"]: i for i in items}
        assert by_sym["601689.SH"]["entry_low"] == 29.0
        assert by_sym["601689.SH"]["entry_high"] == 30.5
        assert by_sym["601689.SH"]["invalid_price"] == 28.0
        assert by_sym["601689.SH"]["notes"] == "等突破"
        assert by_sym["601689.SH"]["priority"] == 5
        assert by_sym["002353.SZ"]["notes"] == "等回调"

    def test_export_import_idempotent(self, tmp_db):
        """Exporting then re-importing into the same DB (no clear) produces
        updates, not duplicates."""
        create_observation_item(
            symbol="601689.SH", name="拓普集团", notes="备注", tf_db_path=tmp_db,
        )
        exported = export_observation_csv(tf_db_path=tmp_db)
        import_observation_csv(exported["csv_text"], tf_db_path=tmp_db)
        items = get_observation_items(tf_db_path=tmp_db)["items"]
        assert len(items) == 1  # no duplicate
        assert items[0]["notes"] == "备注"  # notes preserved


# ── Schemas ──────────────────────────────────────────────────────────────

class TestSchemas:

    def test_import_request_defaults(self):
        req = ObservationImportRequest(csv_text="601689.SH\n")
        assert req.csv_text == "601689.SH\n"
        assert req.force_overwrite_notes is False

    def test_import_request_requires_csv_text(self):
        with pytest.raises(Exception):
            ObservationImportRequest()  # type: ignore[call-arg]

    def test_import_response_defaults(self):
        resp = ObservationImportResponse()
        assert resp.parsed_count == 0
        assert resp.created == []
        assert resp.errored == []

    def test_export_response_defaults(self):
        resp = ObservationExportResponse()
        assert resp.csv_text == ""
        assert resp.count == 0


# ── Runtime Tier ─────────────────────────────────────────────────────────

class TestRuntimeTier:

    def test_import_is_fast_radar(self):
        assert tradeflow_endpoint_tier("tradeflow_observation_import") == RuntimeTier.FAST_RADAR

    def test_export_is_fast_radar(self):
        assert tradeflow_endpoint_tier("tradeflow_observation_export") == RuntimeTier.FAST_RADAR


# ── Holdings Isolation ───────────────────────────────────────────────────

class TestHoldingsIsolation:

    def test_import_does_not_touch_tradingagents_db(self, tmp_db, monkeypatch):
        """Import must only write to tradeflow.db, never tradingagents.db."""
        import api.services.tradeflow_service as svc
        called: list[str] = []
        original = svc._connect

        def spy(path: str = ""):
            called.append(path)
            return original(path)

        monkeypatch.setattr(svc, "_connect", spy)
        import_observation_csv("601689.SH\n", tf_db_path=tmp_db)
        # Every connection opened must target the tradeflow tmp DB, not the
        # production tradingagents.db path.
        for p in called:
            assert p == tmp_db, f"unexpected DB path: {p}"

    def test_no_strong_action_words_in_export(self, tmp_db):
        create_observation_item(
            symbol="601689.SH", reason="正常理由", tf_db_path=tmp_db,
        )
        result = export_observation_csv(tf_db_path=tmp_db)
        for forbidden in ("立即买入", "重仓买入", "满仓", "梭哈"):
            assert forbidden not in result["csv_text"]
