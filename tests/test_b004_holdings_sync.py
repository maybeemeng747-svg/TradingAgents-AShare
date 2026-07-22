"""Tests for api.services.holdings_sync_service — B-004 持仓快照同步."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.database import Base, ImportedPortfolioPositionDB


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def tmp_holdings_file(tmp_path):
    """Return a path to a temp file for external holdings."""
    return str(tmp_path / "current_holdings.json")


def _seed_ta_positions(db, user_id, positions):
    """Helper to seed TA positions directly into the DB."""
    for p in positions:
        db.add(ImportedPortfolioPositionDB(
            id=uuid4().hex,
            user_id=user_id,
            source="manual",
            symbol=p["symbol"],
            security_name=p.get("name"),
            current_position=p.get("current_position"),
            available_position=p.get("available_position"),
            average_cost=p.get("average_cost"),
            market_value=p.get("market_value"),
            current_position_pct=p.get("current_position_pct"),
            last_imported_at=datetime.now(timezone.utc),
        ))
    db.commit()


def _write_external_json(file_path, holdings, meta=None):
    """Helper to write an external holdings JSON file."""
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1.0",
        "meta": meta or {"exported_at": "2026-07-23T12:00:00Z", "source": "test", "count": len(holdings)},
        "holdings": holdings,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


# ──────────────────────────────────────────────────────────────────────────────
# read_external_holdings
# ──────────────────────────────────────────────────────────────────────────────

class TestReadExternalHoldings:
    def test_file_not_found(self, tmp_holdings_file):
        from api.services import holdings_sync_service
        result = holdings_sync_service.read_external_holdings(tmp_holdings_file)
        assert result["error"] == "file_not_found"
        assert result["holdings"] == []

    def test_empty_file(self, tmp_holdings_file):
        Path(tmp_holdings_file).write_text("", encoding="utf-8")
        from api.services import holdings_sync_service
        result = holdings_sync_service.read_external_holdings(tmp_holdings_file)
        assert result["error"] == "empty_file"

    def test_invalid_json(self, tmp_holdings_file):
        Path(tmp_holdings_file).write_text("{invalid", encoding="utf-8")
        from api.services import holdings_sync_service
        result = holdings_sync_service.read_external_holdings(tmp_holdings_file)
        assert result["error"] and "json_parse_error" in result["error"]

    def test_valid_dict_with_holdings(self, tmp_holdings_file):
        holdings = [{"symbol": "600519.SH", "name": "贵州茅台", "current_position": 100}]
        _write_external_json(tmp_holdings_file, holdings)
        from api.services import holdings_sync_service
        result = holdings_sync_service.read_external_holdings(tmp_holdings_file)
        assert result["error"] is None
        assert len(result["holdings"]) == 1
        assert result["holdings"][0]["symbol"] == "600519.SH"
        assert result["meta"] is not None

    def test_valid_list_format(self, tmp_holdings_file):
        holdings = [{"symbol": "600519.SH"}]
        Path(tmp_holdings_file).write_text(json.dumps(holdings), encoding="utf-8")
        from api.services import holdings_sync_service
        result = holdings_sync_service.read_external_holdings(tmp_holdings_file)
        assert result["error"] is None
        assert len(result["holdings"]) == 1

    def test_holdings_not_list(self, tmp_holdings_file):
        Path(tmp_holdings_file).write_text(json.dumps({"holdings": "not_a_list"}), encoding="utf-8")
        from api.services import holdings_sync_service
        result = holdings_sync_service.read_external_holdings(tmp_holdings_file)
        assert result["error"] == "holdings_not_list"

    def test_unexpected_format(self, tmp_holdings_file):
        Path(tmp_holdings_file).write_text(json.dumps("just a string"), encoding="utf-8")
        from api.services import holdings_sync_service
        result = holdings_sync_service.read_external_holdings(tmp_holdings_file)
        assert result["error"] == "unexpected_format"


# ──────────────────────────────────────────────────────────────────────────────
# write_external_holdings
# ──────────────────────────────────────────────────────────────────────────────

class TestWriteExternalHoldings:
    def test_write_creates_file(self, tmp_holdings_file):
        from api.services import holdings_sync_service
        holdings = [{"symbol": "600519.SH", "name": "贵州茅台"}]
        result = holdings_sync_service.write_external_holdings(tmp_holdings_file, holdings)
        assert result["success"] is True
        assert result["count"] == 1
        assert Path(tmp_holdings_file).exists()

    def test_write_content_roundtrip(self, tmp_holdings_file):
        from api.services import holdings_sync_service
        holdings = [
            {"symbol": "600519.SH", "name": "贵州茅台", "current_position": 100, "average_cost": 1700.0},
            {"symbol": "300750.SZ", "name": "宁德时代", "current_position": 200},
        ]
        holdings_sync_service.write_external_holdings(tmp_holdings_file, holdings)
        result = holdings_sync_service.read_external_holdings(tmp_holdings_file)
        assert result["error"] is None
        assert len(result["holdings"]) == 2
        assert result["holdings"][0]["symbol"] == "600519.SH"

    def test_write_with_custom_meta(self, tmp_holdings_file):
        from api.services import holdings_sync_service
        meta = {"exported_at": "2026-01-01T00:00:00Z", "source": "custom", "count": 1}
        result = holdings_sync_service.write_external_holdings(
            tmp_holdings_file, [{"symbol": "600519.SH"}], meta=meta
        )
        assert result["success"] is True
        raw = json.loads(Path(tmp_holdings_file).read_text())
        assert raw["meta"]["source"] == "custom"

    def test_write_empty_holdings(self, tmp_holdings_file):
        from api.services import holdings_sync_service
        result = holdings_sync_service.write_external_holdings(tmp_holdings_file, [])
        assert result["success"] is True
        assert result["count"] == 0

    def test_write_creates_parent_dirs(self, tmp_path):
        from api.services import holdings_sync_service
        nested = str(tmp_path / "a" / "b" / "c" / "holdings.json")
        result = holdings_sync_service.write_external_holdings(nested, [{"symbol": "600519.SH"}])
        assert result["success"] is True
        assert Path(nested).exists()


# ──────────────────────────────────────────────────────────────────────────────
# ta_positions_to_external / external_to_ta_positions
# ──────────────────────────────────────────────────────────────────────────────

class TestPositionConversion:
    def test_ta_to_external_roundtrip(self):
        from api.services import holdings_sync_service
        ta = [
            {"symbol": "600519.SH", "name": "贵州茅台", "current_position": 100,
             "available_position": 80, "average_cost": 1700.0,
             "market_value": 170000.0, "current_position_pct": 30.0},
        ]
        ext = holdings_sync_service.ta_positions_to_external(ta)
        assert len(ext) == 1
        assert ext[0]["symbol"] == "600519.SH"
        assert ext[0]["current_position"] == 100

        back = holdings_sync_service.external_to_ta_positions(ext)
        assert len(back) == 1
        assert back[0]["symbol"] == "600519.SH"

    def test_handles_none_fields(self):
        from api.services import holdings_sync_service
        ta = [{"symbol": "600519.SH", "name": None, "current_position": None}]
        ext = holdings_sync_service.ta_positions_to_external(ta)
        assert ext[0]["name"] == ""
        assert ext[0]["current_position"] is None

    def test_external_with_extra_fields_ignored(self):
        from api.services import holdings_sync_service
        ext = [{"symbol": "600519.SH", "extra_field": "ignored"}]
        back = holdings_sync_service.external_to_ta_positions(ext)
        assert back[0]["symbol"] == "600519.SH"
        assert "extra_field" not in back[0]


# ──────────────────────────────────────────────────────────────────────────────
# compute_bidirectional_diff
# ──────────────────────────────────────────────────────────────────────────────

class TestComputeBidirectionalDiff:
    def test_empty_both(self):
        from api.services import holdings_sync_service
        diff = holdings_sync_service.compute_bidirectional_diff([], [])
        assert diff["ta_only_count"] == 0
        assert diff["external_only_count"] == 0
        assert diff["both_changed_count"] == 0
        assert diff["identical_count"] == 0

    def test_ta_only(self):
        from api.services import holdings_sync_service
        ta = [{"symbol": "600519.SH", "name": "贵州茅台", "current_position": 100}]
        diff = holdings_sync_service.compute_bidirectional_diff(ta, [])
        assert diff["ta_only_count"] == 1
        assert diff["ta_only"][0]["symbol"] == "600519.SH"

    def test_external_only(self):
        from api.services import holdings_sync_service
        ext = [{"symbol": "300750.SZ", "name": "宁德时代"}]
        diff = holdings_sync_service.compute_bidirectional_diff([], ext)
        assert diff["external_only_count"] == 1
        assert diff["external_only"][0]["symbol"] == "300750.SZ"

    def test_identical(self):
        from api.services import holdings_sync_service
        pos = [{"symbol": "600519.SH", "name": "贵州茅台", "current_position": 100,
                "available_position": None, "average_cost": 1700.0,
                "market_value": None, "current_position_pct": None}]
        diff = holdings_sync_service.compute_bidirectional_diff(pos, pos)
        assert diff["identical_count"] == 1
        assert diff["both_changed_count"] == 0

    def test_field_diff_detected(self):
        from api.services import holdings_sync_service
        ta = [{"symbol": "600519.SH", "name": "贵州茅台", "current_position": 100,
               "available_position": None, "average_cost": 1700.0,
               "market_value": None, "current_position_pct": None}]
        ext = [{"symbol": "600519.SH", "name": "贵州茅台", "current_position": 200,
                "available_position": None, "average_cost": 1700.0,
                "market_value": None, "current_position_pct": None}]
        diff = holdings_sync_service.compute_bidirectional_diff(ta, ext)
        assert diff["both_changed_count"] == 1
        assert "current_position" in diff["both_changed"][0]["diffs"]

    def test_mixed_scenarios(self):
        from api.services import holdings_sync_service
        ta = [
            {"symbol": "600519.SH", "name": "茅台", "current_position": 100,
             "available_position": None, "average_cost": None,
             "market_value": None, "current_position_pct": None},
            {"symbol": "000001.SZ", "name": "平安", "current_position": 50,
             "available_position": None, "average_cost": None,
             "market_value": None, "current_position_pct": None},
        ]
        ext = [
            {"symbol": "600519.SH", "name": "茅台", "current_position": 100,
             "available_position": None, "average_cost": None,
             "market_value": None, "current_position_pct": None},
            {"symbol": "300750.SZ", "name": "宁德", "current_position": 200,
             "available_position": None, "average_cost": None,
             "market_value": None, "current_position_pct": None},
        ]
        diff = holdings_sync_service.compute_bidirectional_diff(ta, ext)
        assert diff["ta_only_count"] == 1  # 000001.SZ
        assert diff["external_only_count"] == 1  # 300750.SZ
        assert diff["identical_count"] == 1  # 600519.SH

    def test_name_diff_not_counted(self):
        """Name differences should be tracked since name is in _TRACKED_FIELDS."""
        from api.services import holdings_sync_service
        ta = [{"symbol": "600519.SH", "name": "贵州茅台", "current_position": 100,
               "available_position": None, "average_cost": None,
               "market_value": None, "current_position_pct": None}]
        ext = [{"symbol": "600519.SH", "name": "茅台", "current_position": 100,
                "available_position": None, "average_cost": None,
                "market_value": None, "current_position_pct": None}]
        diff = holdings_sync_service.compute_bidirectional_diff(ta, ext)
        # name differs → both_changed
        assert diff["both_changed_count"] == 1


# ──────────────────────────────────────────────────────────────────────────────
# export_holdings
# ──────────────────────────────────────────────────────────────────────────────

class TestExportHoldings:
    def test_export_success(self, db, tmp_holdings_file):
        from api.services import holdings_sync_service
        _seed_ta_positions(db, "u1", [
            {"symbol": "600519.SH", "name": "贵州茅台", "current_position": 100, "average_cost": 1700.0},
        ])
        result = holdings_sync_service.export_holdings(db, "u1", tmp_holdings_file)
        assert result["success"] is True
        assert result["exported_count"] == 1
        assert Path(tmp_holdings_file).exists()

    def test_export_no_holdings(self, db, tmp_holdings_file):
        from api.services import holdings_sync_service
        result = holdings_sync_service.export_holdings(db, "u-empty", tmp_holdings_file)
        assert result["success"] is False
        assert result["error"] == "no_ta_holdings_to_export"

    def test_export_roundtrip(self, db, tmp_holdings_file):
        from api.services import holdings_sync_service
        _seed_ta_positions(db, "u1", [
            {"symbol": "600519.SH", "name": "贵州茅台", "current_position": 100, "average_cost": 1700.0},
            {"symbol": "300750.SZ", "name": "宁德时代", "current_position": 200, "average_cost": 205.5},
        ])
        holdings_sync_service.export_holdings(db, "u1", tmp_holdings_file)
        result = holdings_sync_service.read_external_holdings(tmp_holdings_file)
        assert result["error"] is None
        assert len(result["holdings"]) == 2


# ──────────────────────────────────────────────────────────────────────────────
# import_holdings
# ──────────────────────────────────────────────────────────────────────────────

class TestImportHoldings:
    def test_import_success(self, db, tmp_holdings_file):
        from api.services import holdings_sync_service
        _write_external_json(tmp_holdings_file, [
            {"symbol": "600519.SH", "name": "贵州茅台", "current_position": 100, "average_cost": 1700.0},
        ])
        result = holdings_sync_service.import_holdings(db, "u1", tmp_holdings_file)
        assert result["success"] is True
        assert result["state"]["summary"]["positions"] == 1

    def test_import_file_not_found(self, db, tmp_holdings_file):
        from api.services import holdings_sync_service
        result = holdings_sync_service.import_holdings(db, "u1", tmp_holdings_file)
        assert result["success"] is False
        assert result["error"] == "file_not_found"

    def test_import_empty_file(self, db, tmp_holdings_file):
        from api.services import holdings_sync_service
        _write_external_json(tmp_holdings_file, [])
        result = holdings_sync_service.import_holdings(db, "u1", tmp_holdings_file)
        assert result["success"] is False
        assert result["error"] == "external_file_empty"

    def test_import_source_tag(self, db, tmp_holdings_file):
        from api.services import holdings_sync_service
        from api.services.portfolio_import_service import list_imported_positions
        _write_external_json(tmp_holdings_file, [
            {"symbol": "600519.SH", "name": "贵州茅台", "current_position": 100},
        ])
        holdings_sync_service.import_holdings(db, "u1", tmp_holdings_file)
        positions = list_imported_positions(db, "u1")
        assert positions[0]["source"] == "investment_controller"


# ──────────────────────────────────────────────────────────────────────────────
# sync_holdings (bidirectional)
# ──────────────────────────────────────────────────────────────────────────────

class TestSyncHoldings:
    def test_export_when_external_missing(self, db, tmp_holdings_file):
        from api.services import holdings_sync_service
        _seed_ta_positions(db, "u1", [
            {"symbol": "600519.SH", "name": "贵州茅台", "current_position": 100},
        ])
        result = holdings_sync_service.sync_holdings(db, "u1", tmp_holdings_file, "bidirectional")
        assert result["success"] is True
        assert result["direction"] == "export"
        assert result["reason"] == "external_file_missing_or_empty"
        assert Path(tmp_holdings_file).exists()

    def test_import_when_ta_empty(self, db, tmp_holdings_file):
        from api.services import holdings_sync_service
        _write_external_json(tmp_holdings_file, [
            {"symbol": "600519.SH", "name": "贵州茅台", "current_position": 100},
        ])
        result = holdings_sync_service.sync_holdings(db, "u1", tmp_holdings_file, "bidirectional")
        assert result["success"] is True
        assert result["direction"] == "import"
        assert result["reason"] == "ta_empty_import_from_external"

    def test_both_empty(self, db, tmp_holdings_file):
        from api.services import holdings_sync_service
        _write_external_json(tmp_holdings_file, [])
        result = holdings_sync_service.sync_holdings(db, "u1", tmp_holdings_file, "bidirectional")
        # Empty external file → external_file_missing_or_empty, TA also empty → both_sides_empty
        assert result["success"] is True
        assert result["direction"] == "none"
        assert result["reason"] == "both_sides_empty"

    def test_bidirectional_merge(self, db, tmp_holdings_file):
        from api.services import holdings_sync_service
        # TA has 600519, external has 300750
        _seed_ta_positions(db, "u1", [
            {"symbol": "600519.SH", "name": "贵州茅台", "current_position": 100, "average_cost": 1700.0},
        ])
        _write_external_json(tmp_holdings_file, [
            {"symbol": "300750.SZ", "name": "宁德时代", "current_position": 200, "average_cost": 205.5},
        ])
        result = holdings_sync_service.sync_holdings(db, "u1", tmp_holdings_file, "bidirectional")
        assert result["success"] is True
        assert result["direction"] == "bidirectional"
        assert result["merged_count"] == 2

    def test_bidirectional_conflict_external_wins(self, db, tmp_holdings_file):
        from api.services import holdings_sync_service
        # Both have 600519 with different positions
        _seed_ta_positions(db, "u1", [
            {"symbol": "600519.SH", "name": "贵州茅台", "current_position": 100, "average_cost": 1700.0},
        ])
        _write_external_json(tmp_holdings_file, [
            {"symbol": "600519.SH", "name": "贵州茅台", "current_position": 500, "average_cost": 1650.0},
        ])
        result = holdings_sync_service.sync_holdings(db, "u1", tmp_holdings_file, "bidirectional")
        assert result["success"] is True
        assert result["diff"]["both_changed_count"] == 1
        # External should win — verify the file was updated with external values
        ext = holdings_sync_service.read_external_holdings(tmp_holdings_file)
        assert ext["holdings"][0]["current_position"] == 500

    def test_direction_export(self, db, tmp_holdings_file):
        from api.services import holdings_sync_service
        _seed_ta_positions(db, "u1", [
            {"symbol": "600519.SH", "name": "贵州茅台", "current_position": 100},
        ])
        result = holdings_sync_service.sync_holdings(db, "u1", tmp_holdings_file, "export")
        assert result["success"] is True
        assert result["exported_count"] == 1

    def test_direction_import(self, db, tmp_holdings_file):
        from api.services import holdings_sync_service
        _write_external_json(tmp_holdings_file, [
            {"symbol": "600519.SH", "name": "贵州茅台", "current_position": 100},
        ])
        result = holdings_sync_service.sync_holdings(db, "u1", tmp_holdings_file, "import")
        assert result["success"] is True
        assert result["state"]["summary"]["positions"] == 1


# ──────────────────────────────────────────────────────────────────────────────
# get_sync_status
# ──────────────────────────────────────────────────────────────────────────────

class TestGetSyncStatus:
    def test_no_ta_no_file(self, db, tmp_holdings_file):
        from api.services import holdings_sync_service
        status = holdings_sync_service.get_sync_status(db, "u1", tmp_holdings_file)
        assert status["file_exists"] is False
        assert status["ta_count"] == 0
        assert status["external_count"] == 0

    def test_ta_only(self, db, tmp_holdings_file):
        from api.services import holdings_sync_service
        _seed_ta_positions(db, "u1", [
            {"symbol": "600519.SH", "name": "贵州茅台", "current_position": 100},
        ])
        status = holdings_sync_service.get_sync_status(db, "u1", tmp_holdings_file)
        assert status["ta_count"] == 1
        assert status["external_count"] == 0
        assert status["diff"]["ta_only_count"] == 1

    def test_both_sides(self, db, tmp_holdings_file):
        from api.services import holdings_sync_service
        _seed_ta_positions(db, "u1", [
            {"symbol": "600519.SH", "name": "贵州茅台", "current_position": 100},
        ])
        _write_external_json(tmp_holdings_file, [
            {"symbol": "600519.SH", "name": "贵州茅台", "current_position": 100},
        ])
        status = holdings_sync_service.get_sync_status(db, "u1", tmp_holdings_file)
        assert status["ta_count"] == 1
        assert status["external_count"] == 1
        assert status["diff"]["identical_count"] == 1


# ──────────────────────────────────────────────────────────────────────────────
# get_default_sync_path
# ──────────────────────────────────────────────────────────────────────────────

class TestGetDefaultSyncPath:
    def test_default_path(self):
        from api.services import holdings_sync_service
        path = holdings_sync_service.get_default_sync_path()
        assert path.endswith("current_holdings.json")
        assert "investment-controller" in path

    def test_env_override(self, monkeypatch):
        from api.services import holdings_sync_service
        monkeypatch.setenv("INVESTMENT_CONTROLLER_HOLDINGS_PATH", "/custom/path/holdings.json")
        path = holdings_sync_service.get_default_sync_path()
        assert path == "/custom/path/holdings.json"


# ──────────────────────────────────────────────────────────────────────────────
# _merge_positions
# ──────────────────────────────────────────────────────────────────────────────

class TestMergePositions:
    def test_empty_both(self):
        from api.services.holdings_sync_service import _merge_positions
        result = _merge_positions([], [], None)
        assert result == []

    def test_ta_only(self):
        from api.services.holdings_sync_service import _merge_positions
        ta = [{"symbol": "600519.SH", "name": "茅台"}]
        result = _merge_positions(ta, [], None)
        assert len(result) == 1
        assert result[0]["symbol"] == "600519.SH"

    def test_external_only(self):
        from api.services.holdings_sync_service import _merge_positions
        ext = [{"symbol": "300750.SZ", "name": "宁德"}]
        result = _merge_positions([], ext, None)
        assert len(result) == 1
        assert result[0]["symbol"] == "300750.SZ"

    def test_both_external_wins(self):
        from api.services.holdings_sync_service import _merge_positions
        ta = [{"symbol": "600519.SH", "name": "茅台", "current_position": 100}]
        ext = [{"symbol": "600519.SH", "name": "茅台", "current_position": 500}]
        result = _merge_positions(ta, ext, None)
        assert len(result) == 1
        assert result[0]["current_position"] == 500

    def test_mixed_merge(self):
        from api.services.holdings_sync_service import _merge_positions
        ta = [{"symbol": "600519.SH", "name": "茅台"}]
        ext = [{"symbol": "300750.SZ", "name": "宁德"}]
        result = _merge_positions(ta, ext, None)
        symbols = {r["symbol"] for r in result}
        assert symbols == {"600519.SH", "300750.SZ"}


# ──────────────────────────────────────────────────────────────────────────────
# Edge cases
# ──────────────────────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_duplicate_symbols_in_external(self, tmp_holdings_file):
        from api.services import holdings_sync_service
        # Write file with duplicate symbols
        holdings = [
            {"symbol": "600519.SH", "name": "茅台", "current_position": 100},
            {"symbol": "600519.SH", "name": "茅台", "current_position": 200},
        ]
        _write_external_json(tmp_holdings_file, holdings)
        result = holdings_sync_service.read_external_holdings(tmp_holdings_file)
        # Both should be read (dedup happens at import level)
        assert len(result["holdings"]) == 2

    def test_zero_position_held(self, db, tmp_holdings_file):
        from api.services import holdings_sync_service
        _seed_ta_positions(db, "u1", [
            {"symbol": "600519.SH", "name": "贵州茅台", "current_position": 0, "average_cost": 1700.0},
        ])
        result = holdings_sync_service.export_holdings(db, "u1", tmp_holdings_file)
        assert result["success"] is True
        assert result["exported_count"] == 1

    def test_special_characters_in_name(self, db, tmp_holdings_file):
        from api.services import holdings_sync_service
        _seed_ta_positions(db, "u1", [
            {"symbol": "600519.SH", "name": "贵州茅台（白酒）", "current_position": 100},
        ])
        result = holdings_sync_service.export_holdings(db, "u1", tmp_holdings_file)
        assert result["success"] is True
        ext = holdings_sync_service.read_external_holdings(tmp_holdings_file)
        assert ext["holdings"][0]["name"] == "贵州茅台（白酒）"

    def test_sync_idempotent(self, db, tmp_holdings_file):
        from api.services import holdings_sync_service
        _seed_ta_positions(db, "u1", [
            {"symbol": "600519.SH", "name": "贵州茅台", "current_position": 100},
        ])
        r1 = holdings_sync_service.sync_holdings(db, "u1", tmp_holdings_file, "bidirectional")
        r2 = holdings_sync_service.sync_holdings(db, "u1", tmp_holdings_file, "bidirectional")
        assert r1["success"] is True
        assert r2["success"] is True

    def test_sequential_sync_preserves_data(self, db, tmp_holdings_file):
        from api.services import holdings_sync_service
        # First sync: export TA → file
        _seed_ta_positions(db, "u1", [
            {"symbol": "600519.SH", "name": "贵州茅台", "current_position": 100},
        ])
        holdings_sync_service.sync_holdings(db, "u1", tmp_holdings_file, "bidirectional")
        # Verify file has the data
        ext = holdings_sync_service.read_external_holdings(tmp_holdings_file)
        assert len(ext["holdings"]) == 1
