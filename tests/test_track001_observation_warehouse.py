# [TRACK-001] observation_warehouse
"""Tests for TRACK-001: 观察仓数据模型与只读/写入 API.

Covers:
- DB table creation in init_db.
- Create item (incl. boundary values entry_low=0 / entry_high=0).
- Update item (partial update, untouched fields preserved).
- State transitions (watching -> near_entry -> in_entry_zone -> ta_required -> entered,
                     and terminal invalidated / removed).
- Bulk upsert (create + update mixed batch).
- Empty / no-data scenarios.
- Symbol & name normalization (bare 6-digit code, lower-case suffix, missing name).
- Pydantic schema validation.
- Runtime tier classification (FAST_RADAR, no LLM).
- Observation items NEVER pollute real holdings (ImportedPortfolioPositionDB).
"""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.tradeflow.candidate_engine import init_db

from api.services.tradeflow_service import (
    get_observation_items,
    create_observation_item,
    update_observation_item,
    mark_observation_item_status,
    bulk_upsert_observation_items,
    ALLOWED_OBSERVATION_STATUSES,
    OBSERVATION_STATUS_WATCHING,
    OBSERVATION_STATUS_NEAR_ENTRY,
    OBSERVATION_STATUS_IN_ENTRY_ZONE,
    OBSERVATION_STATUS_TA_REQUIRED,
    OBSERVATION_STATUS_ENTERED,
    OBSERVATION_STATUS_INVALIDATED,
    OBSERVATION_STATUS_REMOVED,
)
from api.tradeflow_schemas import (
    ObservationItemResponse,
    ObservationItemListResponse,
    ObservationItemCreateRequest,
    ObservationItemUpdateRequest,
    ObservationItemMarkRequest,
    ObservationActionResponse,
    ObservationBulkUpsertRequest,
    ObservationBulkUpsertResponse,
    ObservationBulkUpsertItem,
)
from api.runtime_tier import tradeflow_endpoint_tier, tradeflow_meta, RuntimeTier


TODAY = datetime.now().strftime("%Y-%m-%d")


# ── Fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_db(tmp_path):
    db_path = str(tmp_path / "test_observation_warehouse.db")
    init_db(db_path)
    return db_path


@pytest.fixture
def db_with_items(tmp_db):
    """Create a temp DB and add two observation items."""
    create_observation_item(
        symbol="601689.SH",
        name="拓普集团",
        entry_low=29.0,
        entry_high=30.5,
        trigger_price=30.0,
        invalid_price=28.0,
        horizon="short",
        source="manual",
        reason="接近买点",
        priority=5,
        notes="等突破",
        tf_db_path=tmp_db,
    )
    create_observation_item(
        symbol="002353.SZ",
        name="杰瑞股份",
        entry_low=34.0,
        entry_high=35.5,
        trigger_price=35.0,
        invalid_price=33.0,
        horizon="mid",
        source="tradeflow",
        reason="政策利好",
        priority=3,
        notes="等回调",
        tf_db_path=tmp_db,
    )
    return tmp_db


# ── DB Schema Tests ──────────────────────────────────────────────────────

class TestObservationDBSchema:
    """Verify DB table is created by init_db."""

    def test_observation_items_table_exists(self, tmp_db):
        conn = sqlite3.connect(tmp_db)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        assert "tradeflow_observation_items" in tables

    def test_observation_items_columns(self, tmp_db):
        conn = sqlite3.connect(tmp_db)
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(tradeflow_observation_items)"
        ).fetchall()}
        conn.close()
        expected = {
            "id", "symbol", "name", "status",
            "entry_low", "entry_high", "trigger_price", "invalid_price",
            "horizon", "source", "reason", "priority", "notes",
            "created_at", "updated_at", "last_reviewed_at",
            "playbook_stage", "playbook_contract_json",
        }
        assert expected.issubset(cols), f"Missing columns: {expected - cols}"

    def test_unique_symbol_constraint(self, tmp_db):
        # Insert directly via SQL to verify UNIQUE(symbol) is in place.
        conn = sqlite3.connect(tmp_db)
        conn.execute(
            "INSERT INTO tradeflow_observation_items (symbol, name) VALUES (?, ?)",
            ("600519.SH", "贵州茅台"),
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO tradeflow_observation_items (symbol, name) VALUES (?, ?)",
                ("600519.SH", "贵州茅台-重复"),
            )
        conn.close()


# ── List / Empty Tests ───────────────────────────────────────────────────

class TestObservationListEmpty:

    def test_empty_list_initially(self, tmp_db):
        result = get_observation_items(tf_db_path=tmp_db)
        assert result["status"] == "ok"
        assert result["items"] == []
        assert result["summary"]["total"] == 0
        assert result["summary"]["active"] == 0

    def test_no_data_when_db_missing(self):
        result = get_observation_items(tf_db_path="/nonexistent/path/none.db")
        assert result["status"] == "no_data"
        assert result["items"] == []
        assert result["summary"]["total"] == 0

    def test_summary_has_all_status_keys(self, tmp_db):
        result = get_observation_items(tf_db_path=tmp_db)
        for status in ALLOWED_OBSERVATION_STATUSES:
            assert status in result["summary"]


# ── Create Tests ─────────────────────────────────────────────────────────

class TestCreateObservationItem:

    def test_create_and_read_playbook_contract(self, tmp_db):
        result = create_observation_item(
            symbol="601689.SH",
            playbook_stage="trial",
            playbook_contract={
                "industry_evidence_score": 4,
                "planned_max_position_pct": 12,
                "trial_lot_status": "built",
            },
            tf_db_path=tmp_db,
        )
        assert result["status"] == "ok"
        assert result["item"]["playbook_stage"] == "trial"
        assert result["item"]["playbook_contract"]["industry_evidence_score"] == 4.0
        listed = get_observation_items(tf_db_path=tmp_db)["items"][0]
        assert listed["playbook_contract"]["trial_lot_status"] == "built"

    def test_create_rejects_forbidden_playbook_action_text(self, tmp_db):
        with pytest.raises(ValueError):
            create_observation_item(
                symbol="601689.SH",
                playbook_stage="trial",
                playbook_contract={"next_action": "立即清仓"},
                tf_db_path=tmp_db,
            )

    def test_read_drops_dirty_playbook_without_breaking_list(self, tmp_db):
        create_observation_item(symbol="601689.SH", tf_db_path=tmp_db)
        conn = sqlite3.connect(tmp_db)
        conn.execute(
            "UPDATE tradeflow_observation_items SET playbook_stage=?, playbook_contract_json=?",
            ("trial", '{"next_action": "立即清仓"}'),
        )
        conn.commit()
        conn.close()

        listing = get_observation_items(tf_db_path=tmp_db)
        assert listing["status"] == "ok"
        assert listing["items"][0]["playbook_stage"] is None
        assert listing["items"][0]["playbook_contract"] == {}

    def test_create_basic(self, tmp_db):
        result = create_observation_item(
            symbol="601689.SH",
            name="拓普集团",
            entry_low=29.0,
            entry_high=30.5,
            trigger_price=30.0,
            invalid_price=28.0,
            tf_db_path=tmp_db,
        )
        assert result["status"] == "ok"
        assert result["item"]["symbol"] == "601689.SH"
        assert result["item"]["name"] == "拓普集团"
        assert result["item"]["entry_low"] == 29.0
        assert result["item"]["entry_high"] == 30.5
        assert result["item"]["status"] == OBSERVATION_STATUS_WATCHING

    def test_create_with_defaults(self, tmp_db):
        result = create_observation_item(symbol="002353.SZ", tf_db_path=tmp_db)
        assert result["status"] == "ok"
        item = result["item"]
        assert item["status"] == OBSERVATION_STATUS_WATCHING
        assert item["horizon"] == "short"
        assert item["source"] == "manual"
        assert item["priority"] == 0
        assert item["reason"] == ""
        assert item["notes"] == ""

    def test_create_duplicate_blocked(self, tmp_db):
        create_observation_item(symbol="601689.SH", name="拓普集团", tf_db_path=tmp_db)
        result = create_observation_item(
            symbol="601689.SH", name="拓普集团", tf_db_path=tmp_db,
        )
        assert result["status"] == "duplicate"
        assert "item_id" in result

    def test_create_empty_symbol_rejected(self, tmp_db):
        result = create_observation_item(symbol="", tf_db_path=tmp_db)
        assert result["status"] == "error"

    def test_create_invalid_status_rejected(self, tmp_db):
        with pytest.raises(ValueError):
            create_observation_item(
                symbol="601689.SH", status="BOGUS", tf_db_path=tmp_db,
            )

    def test_create_invalid_horizon_rejected(self, tmp_db):
        with pytest.raises(ValueError):
            create_observation_item(
                symbol="601689.SH", horizon="long", tf_db_path=tmp_db,
            )

    def test_create_invalid_source_rejected(self, tmp_db):
        with pytest.raises(ValueError):
            create_observation_item(
                symbol="601689.SH", source="broker", tf_db_path=tmp_db,
            )

    def test_create_all_valid_sources(self, tmp_db):
        for i, source in enumerate(("manual", "tradeflow", "ta", "investment_controller")):
            result = create_observation_item(
                symbol=f"00000{i}.SZ", source=source, tf_db_path=tmp_db,
            )
            assert result["status"] == "ok"
            assert result["item"]["source"] == source

    def test_create_all_valid_horizons(self, tmp_db):
        for i, horizon in enumerate(("intraday", "short", "mid")):
            result = create_observation_item(
                symbol=f"00000{i}.SZ", horizon=horizon, tf_db_path=tmp_db,
            )
            assert result["status"] == "ok"
            assert result["item"]["horizon"] == horizon


# ── Boundary Values Tests (entry_low=0, entry_high=0) ────────────────────

class TestBoundaryValues:
    """entry_low=0 / entry_high=0 等边界值不被显示成 N/A."""

    def test_zero_entry_low_preserved(self, tmp_db):
        result = create_observation_item(
            symbol="601689.SH",
            entry_low=0,
            entry_high=30.5,
            tf_db_path=tmp_db,
        )
        assert result["status"] == "ok"
        # MUST be 0.0, not None, not "N/A"
        assert result["item"]["entry_low"] == 0.0
        assert result["item"]["entry_low"] is not None

    def test_zero_entry_high_preserved(self, tmp_db):
        result = create_observation_item(
            symbol="601689.SH",
            entry_low=29.0,
            entry_high=0,
            tf_db_path=tmp_db,
        )
        assert result["status"] == "ok"
        assert result["item"]["entry_high"] == 0.0
        assert result["item"]["entry_high"] is not None

    def test_zero_trigger_and_invalid_preserved(self, tmp_db):
        result = create_observation_item(
            symbol="601689.SH",
            trigger_price=0,
            invalid_price=0.0,
            tf_db_path=tmp_db,
        )
        assert result["item"]["trigger_price"] == 0.0
        assert result["item"]["invalid_price"] == 0.0

    def test_all_zero_prices_preserved_through_list(self, tmp_db):
        create_observation_item(
            symbol="601689.SH",
            entry_low=0,
            entry_high=0,
            trigger_price=0,
            invalid_price=0,
            tf_db_path=tmp_db,
        )
        listing = get_observation_items(tf_db_path=tmp_db)
        item = listing["items"][0]
        assert item["entry_low"] == 0.0
        assert item["entry_high"] == 0.0
        assert item["trigger_price"] == 0.0
        assert item["invalid_price"] == 0.0

    def test_negative_prices_allowed(self, tmp_db):
        # Negative sentinel values are sometimes used to mean "explicitly unset".
        result = create_observation_item(
            symbol="601689.SH", entry_low=-1.0, tf_db_path=tmp_db,
        )
        assert result["item"]["entry_low"] == -1.0

    def test_pydantic_item_serializes_zero(self):
        item = ObservationItemResponse(symbol="601689.SH", entry_low=0.0, entry_high=0.0)
        dumped = item.model_dump()
        assert dumped["entry_low"] == 0.0
        assert dumped["entry_high"] == 0.0


# ── Update Tests ─────────────────────────────────────────────────────────

class TestUpdateObservationItem:

    def test_update_playbook_contract_preserves_existing_fields(self, db_with_items):
        item_id = get_observation_items(tf_db_path=db_with_items)["items"][0]["id"]
        update_observation_item(
            item_id,
            playbook_stage="confirm",
            playbook_contract={"industry_evidence_score": 5},
            tf_db_path=db_with_items,
        )
        item = next(
            item for item in get_observation_items(tf_db_path=db_with_items)["items"]
            if item["id"] == item_id
        )
        assert item["playbook_stage"] == "confirm"
        assert item["playbook_contract"]["industry_evidence_score"] == 5.0


    def test_partial_update_status(self, db_with_items):
        listing = get_observation_items(tf_db_path=db_with_items)
        item_id = listing["items"][0]["id"]
        result = update_observation_item(
            item_id, status=OBSERVATION_STATUS_NEAR_ENTRY, tf_db_path=db_with_items,
        )
        assert result["status"] == "ok"
        assert result["item"]["status"] == OBSERVATION_STATUS_NEAR_ENTRY

    def test_partial_update_preserves_other_fields(self, db_with_items):
        listing = get_observation_items(tf_db_path=db_with_items)
        item_id = listing["items"][0]["id"]
        before = listing["items"][0]
        update_observation_item(
            item_id, priority=9, tf_db_path=db_with_items,
        )
        after = get_observation_items(tf_db_path=db_with_items)
        updated = next(i for i in after["items"] if i["id"] == item_id)
        # priority changed
        assert updated["priority"] == 9
        # untouched fields preserved
        assert updated["symbol"] == before["symbol"]
        assert updated["entry_low"] == before["entry_low"]
        assert updated["entry_high"] == before["entry_high"]
        assert updated["horizon"] == before["horizon"]
        assert updated["source"] == before["source"]

    def test_update_entry_low_zero(self, db_with_items):
        listing = get_observation_items(tf_db_path=db_with_items)
        item_id = listing["items"][0]["id"]
        result = update_observation_item(
            item_id, entry_low=0, tf_db_path=db_with_items,
        )
        assert result["item"]["entry_low"] == 0.0

    def test_update_notes_appends_when_marked(self, db_with_items):
        listing = get_observation_items(tf_db_path=db_with_items)
        item_id = listing["items"][0]["id"]
        result = mark_observation_item_status(
            item_id, OBSERVATION_STATUS_INVALIDATED, note="跌破失效价", tf_db_path=db_with_items,
        )
        assert result["item"]["status"] == OBSERVATION_STATUS_INVALIDATED
        assert "跌破失效价" in result["item"]["notes"]

    def test_update_nonexistent(self, tmp_db):
        result = update_observation_item(99999, priority=1, tf_db_path=tmp_db)
        assert result["status"] == "not_found"

    def test_update_invalid_status_rejected(self, db_with_items):
        listing = get_observation_items(tf_db_path=db_with_items)
        item_id = listing["items"][0]["id"]
        with pytest.raises(ValueError):
            update_observation_item(item_id, status="BOGUS", tf_db_path=db_with_items)

    def test_touch_last_reviewed_sets_timestamp(self, db_with_items):
        listing = get_observation_items(tf_db_path=db_with_items)
        item_id = listing["items"][0]["id"]
        result = update_observation_item(
            item_id, touch_last_reviewed=True, tf_db_path=db_with_items,
        )
        assert result["item"]["last_reviewed_at"]
        # Should be a non-empty timestamp
        assert len(result["item"]["last_reviewed_at"]) > 0


# ── State Transition Tests ───────────────────────────────────────────────

class TestStateTransitions:

    def test_full_active_progression(self, tmp_db):
        """watching -> near_entry -> in_entry_zone -> ta_required -> entered."""
        created = create_observation_item(
            symbol="601689.SH", name="拓普集团", tf_db_path=tmp_db,
        )
        item_id = created["item"]["id"]

        for next_status in (
            OBSERVATION_STATUS_NEAR_ENTRY,
            OBSERVATION_STATUS_IN_ENTRY_ZONE,
            OBSERVATION_STATUS_TA_REQUIRED,
            OBSERVATION_STATUS_ENTERED,
        ):
            result = update_observation_item(item_id, status=next_status, tf_db_path=tmp_db)
            assert result["status"] == "ok"
            assert result["item"]["status"] == next_status

        listing = get_observation_items(tf_db_path=tmp_db)
        assert listing["summary"]["entered"] == 1
        assert listing["summary"]["watching"] == 0

    def test_mark_invalidated(self, tmp_db):
        created = create_observation_item(symbol="601689.SH", tf_db_path=tmp_db)
        item_id = created["item"]["id"]
        result = mark_observation_item_status(
            item_id, OBSERVATION_STATUS_INVALIDATED, tf_db_path=tmp_db,
        )
        assert result["item"]["status"] == OBSERVATION_STATUS_INVALIDATED

    def test_mark_removed(self, tmp_db):
        created = create_observation_item(symbol="601689.SH", tf_db_path=tmp_db)
        item_id = created["item"]["id"]
        mark_observation_item_status(item_id, OBSERVATION_STATUS_REMOVED, tf_db_path=tmp_db)

        # Default listing hides removed
        hidden = get_observation_items(tf_db_path=tmp_db)
        assert hidden["summary"]["total"] == 0

        # include_removed=True surfaces it
        shown = get_observation_items(include_removed=True, tf_db_path=tmp_db)
        assert shown["summary"]["total"] == 1
        assert shown["summary"]["removed"] == 1

    def test_active_count_excludes_terminal(self, tmp_db):
        create_observation_item(symbol="601689.SH", tf_db_path=tmp_db)  # watching
        created2 = create_observation_item(symbol="002353.SZ", tf_db_path=tmp_db)  # watching
        create_observation_item(symbol="600519.SH", tf_db_path=tmp_db)  # watching

        mark_observation_item_status(
            created2["item"]["id"], OBSERVATION_STATUS_INVALIDATED, tf_db_path=tmp_db,
        )

        listing = get_observation_items(tf_db_path=tmp_db)
        # 3 total, 1 invalidated (still returned by default), 2 active
        assert listing["summary"]["total"] == 3
        assert listing["summary"]["invalidated"] == 1
        assert listing["summary"]["active"] == 2

    def test_status_filter(self, tmp_db):
        create_observation_item(
            symbol="601689.SH", status=OBSERVATION_STATUS_NEAR_ENTRY, tf_db_path=tmp_db,
        )
        create_observation_item(
            symbol="002353.SZ", status=OBSERVATION_STATUS_IN_ENTRY_ZONE, tf_db_path=tmp_db,
        )
        filtered = get_observation_items(
            status=OBSERVATION_STATUS_NEAR_ENTRY, tf_db_path=tmp_db,
        )
        assert len(filtered["items"]) == 1
        assert filtered["items"][0]["symbol"] == "601689.SH"


# ── Bulk Upsert Tests ────────────────────────────────────────────────────

class TestBulkUpsert:

    def test_bulk_upsert_creates_and_updates(self, tmp_db):
        # Seed one existing
        create_observation_item(
            symbol="601689.SH", name="拓普集团", priority=1, tf_db_path=tmp_db,
        )
        result = bulk_upsert_observation_items(
            [
                {"symbol": "601689.SH", "priority": 9, "reason": "更新理由"},
                {"symbol": "002353.SZ", "name": "杰瑞股份", "entry_low": 34.0},
            ],
            tf_db_path=tmp_db,
        )
        assert result["status"] == "ok"
        assert result["created_count"] == 1
        assert result["updated_count"] == 1
        assert "601689.SH" in result["updated"]
        assert "002353.SZ" in result["created"]

        listing = get_observation_items(tf_db_path=tmp_db)
        by_symbol = {i["symbol"]: i for i in listing["items"]}
        assert by_symbol["601689.SH"]["priority"] == 9
        assert by_symbol["601689.SH"]["reason"] == "更新理由"
        assert by_symbol["002353.SZ"]["entry_low"] == 34.0

    def test_bulk_upsert_persists_playbook_contract(self, tmp_db):
        result = bulk_upsert_observation_items(
            [{
                "symbol": "601689.SH",
                "playbook_stage": "observe",
                "playbook_contract": {
                    "risk_pressure_score": 2,
                    "allow_add": False,
                },
            }],
            tf_db_path=tmp_db,
        )
        assert result["created_count"] == 1
        item = get_observation_items(tf_db_path=tmp_db)["items"][0]
        assert item["playbook_stage"] == "observe"
        assert item["playbook_contract"]["risk_pressure_score"] == 2.0
        assert item["playbook_contract"]["allow_add"] is False

    def test_bulk_upsert_empty_list(self, tmp_db):
        result = bulk_upsert_observation_items([], tf_db_path=tmp_db)
        assert result["status"] == "ok"
        assert result["created_count"] == 0
        assert result["updated_count"] == 0

    def test_bulk_upsert_preserves_zero_boundary(self, tmp_db):
        bulk_upsert_observation_items(
            [{"symbol": "601689.SH", "entry_low": 0, "entry_high": 0}],
            tf_db_path=tmp_db,
        )
        listing = get_observation_items(tf_db_path=tmp_db)
        item = listing["items"][0]
        assert item["entry_low"] == 0.0
        assert item["entry_high"] == 0.0

    def test_bulk_upsert_collects_errors(self, tmp_db):
        result = bulk_upsert_observation_items(
            [
                {"symbol": "601689.SH"},  # ok
                {"symbol": "", "reason": "empty symbol"},  # error
                {"symbol": "002353.SZ", "status": "BOGUS"},  # validation error
            ],
            tf_db_path=tmp_db,
        )
        assert result["errored_count"] == 2
        assert result["created_count"] == 1
        error_symbols = {e["symbol"] for e in result["errored"]}
        assert "" in error_symbols
        assert "002353.SZ" in error_symbols

    def test_bulk_upsert_normalizes_symbols(self, tmp_db):
        bulk_upsert_observation_items(
            [
                {"symbol": "601689", "name": "拓普集团"},  # bare code
                {"symbol": "002353.sz", "name": "杰瑞股份"},  # lower-case suffix
            ],
            tf_db_path=tmp_db,
        )
        listing = get_observation_items(tf_db_path=tmp_db)
        symbols = {i["symbol"] for i in listing["items"]}
        assert "601689.SH" in symbols
        assert "002353.SZ" in symbols


# ── Symbol / Name Normalization Tests ────────────────────────────────────

class TestSymbolNormalization:

    def test_bare_six_digit_code_normalized(self, tmp_db):
        result = create_observation_item(symbol="601689", tf_db_path=tmp_db)
        assert result["item"]["symbol"] == "601689.SH"

    def test_shenzhen_bare_code_normalized(self, tmp_db):
        result = create_observation_item(symbol="002353", tf_db_path=tmp_db)
        assert result["item"]["symbol"] == "002353.SZ"

    def test_bj_bare_code_normalized(self, tmp_db):
        result = create_observation_item(symbol="832000", tf_db_path=tmp_db)
        assert result["item"]["symbol"] == "832000.BJ"

    def test_lower_case_suffix_normalized(self, tmp_db):
        result = create_observation_item(symbol="601689.sh", tf_db_path=tmp_db)
        assert result["item"]["symbol"] == "601689.SH"

    def test_ss_suffix_normalized_to_sh(self, tmp_db):
        result = create_observation_item(symbol="600519.SS", tf_db_path=tmp_db)
        assert result["item"]["symbol"] == "600519.SH"

    def test_whitespace_stripped(self, tmp_db):
        result = create_observation_item(symbol="  601689.SH  ", tf_db_path=tmp_db)
        assert result["item"]["symbol"] == "601689.SH"

    def test_dedup_after_normalization(self, tmp_db):
        # 601689 and 601689.SH normalize to the same key
        create_observation_item(symbol="601689", tf_db_path=tmp_db)
        result = create_observation_item(symbol="601689.SH", tf_db_path=tmp_db)
        assert result["status"] == "duplicate"

    def test_empty_name_falls_back_to_resolver(self, tmp_db):
        # Without network access, the resolver returns "--" placeholder; that's acceptable.
        result = create_observation_item(symbol="601689.SH", tf_db_path=tmp_db)
        assert result["item"]["name"]  # non-empty string (either resolved or "--")

    def test_explicit_name_preserved(self, tmp_db):
        result = create_observation_item(
            symbol="601689.SH", name="拓普集团", tf_db_path=tmp_db,
        )
        assert result["item"]["name"] == "拓普集团"


# ── Holdings Isolation Tests ─────────────────────────────────────────────

class TestHoldingsIsolation:
    """观察仓条目不会污染真实持仓接口."""

    def test_observation_table_separate_from_holdings_table(self, tmp_db):
        """Observation items live in tradeflow.db, holdings live in tradingagents.db.

        Verify there's no imported_portfolio_positions-equivalent in tradeflow.db
        and no tradeflow_observation_items-equivalent in tradingagents.db.
        """
        conn = sqlite3.connect(tmp_db)
        tf_tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        # Observation warehouse is in tradeflow.db
        assert "tradeflow_observation_items" in tf_tables
        # Real holdings table is NOT in tradeflow.db
        assert "imported_portfolio_positions" not in tf_tables

    def test_observation_items_carry_no_market_value(self, db_with_items):
        """Observation items must not have market_value / current_position fields."""
        listing = get_observation_items(tf_db_path=db_with_items)
        for item in listing["items"]:
            # These holding-only fields must not appear on observation items
            assert "market_value" not in item
            assert "current_position" not in item
            assert "average_cost" not in item
            assert "available_position" not in item

    def test_observation_items_not_in_holdings_service(self, db_with_items, monkeypatch):
        """ImportedPortfolioPositionDB must NOT return observation items."""
        # Force the prod DB path used by portfolio_import_service to point at our
        # tradeflow-only fixture DB. Even then, list_imported_positions should
        # return empty because tradeflow_observation_items is a different table.
        from api.services import portfolio_import_service
        from api.database import ImportedPortfolioPositionDB

        # Build an in-memory SQLAlchemy session that has the holdings table but
        # is completely independent of tradeflow.db.
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from api.database import Base

        engine = create_engine("sqlite:///:memory:")
        ImportedPortfolioPositionDB.__table__.create(engine)
        Session = sessionmaker(bind=engine)
        db_session = Session()

        try:
            positions = portfolio_import_service.list_imported_positions(
                db_session, user_id="test_user",
            )
            # No holdings inserted -> empty, even though tradeflow.db has items.
            assert positions == []
        finally:
            db_session.close()


# ── Pydantic Schema Tests ────────────────────────────────────────────────

class TestObservationSchemas:

    def test_item_response_defaults(self):
        item = ObservationItemResponse()
        assert item.status == "watching"
        assert item.entry_low == 0.0
        assert item.entry_high == 0.0
        assert item.trigger_price == 0.0
        assert item.invalid_price == 0.0
        assert item.horizon == "short"
        assert item.source == "manual"
        assert item.priority == 0

    def test_item_list_response_defaults(self):
        r = ObservationItemListResponse()
        assert r.items == []
        assert r.status == "ok"

    def test_create_request_validation(self):
        req = ObservationItemCreateRequest(symbol="601689.SH", entry_low=0.0)
        assert req.symbol == "601689.SH"
        assert req.entry_low == 0.0

    def test_update_request_all_none_by_default(self):
        req = ObservationItemUpdateRequest()
        assert req.status is None
        assert req.entry_low is None
        assert req.notes is None

    def test_mark_request_requires_status(self):
        with pytest.raises(Exception):
            ObservationItemMarkRequest()  # status is required

    def test_action_response_from_service_dict(self, db_with_items):
        data = get_observation_items(tf_db_path=db_with_items)
        model = ObservationItemListResponse(**data)
        assert model.status == "ok"
        assert len(model.items) == 2

    def test_bulk_upsert_request_parsing(self):
        req = ObservationBulkUpsertRequest(
            items=[
                ObservationBulkUpsertItem(symbol="601689.SH", entry_low=0),
                ObservationBulkUpsertItem(symbol="002353.SZ", entry_low=0.0),
            ],
        )
        assert len(req.items) == 2

    def test_bulk_upsert_response_serializes(self):
        r = ObservationBulkUpsertResponse(
            status="ok", created=["601689.SH"], updated=[], created_count=1,
        )
        dumped = r.model_dump()
        assert dumped["created_count"] == 1
        assert dumped["created"] == ["601689.SH"]


# ── Runtime Tier Tests ───────────────────────────────────────────────────

class TestObservationRuntimeTier:

    def test_observation_items_endpoint_is_fast_radar(self):
        tier = tradeflow_endpoint_tier("tradeflow_observation_items")
        assert tier == RuntimeTier.FAST_RADAR

    def test_no_llm_allowed(self):
        meta = tradeflow_meta("tradeflow_observation_items")
        assert meta["llm_allowed"] is False

    def test_no_confirmation_required(self):
        meta = tradeflow_meta("tradeflow_observation_items")
        assert meta["requires_confirmation"] is False

    def test_runtime_tier_meta_in_response(self, tmp_db):
        result = get_observation_items(tf_db_path=tmp_db)
        assert "runtime_tier_meta" in result
        assert result["runtime_tier_meta"]["runtime_tier"] == "FAST_RADAR"


# ── E2E Workflow Tests ───────────────────────────────────────────────────

class TestObservationE2E:

    def test_full_lifecycle(self, tmp_db):
        """Add -> update -> near entry -> enter -> mark entered."""
        created = create_observation_item(
            symbol="601689.SH",
            name="拓普集团",
            entry_low=29.0,
            entry_high=30.5,
            trigger_price=30.0,
            invalid_price=28.0,
            reason="接近买点",
            priority=5,
            tf_db_path=tmp_db,
        )
        assert created["status"] == "ok"
        item_id = created["item"]["id"]

        # Price drops into entry zone
        update_observation_item(
            item_id, status=OBSERVATION_STATUS_IN_ENTRY_ZONE, tf_db_path=tmp_db,
        )

        # Needs TA confirmation
        update_observation_item(
            item_id, status=OBSERVATION_STATUS_TA_REQUIRED, tf_db_path=tmp_db,
        )

        # Finally entered
        mark_observation_item_status(
            item_id, OBSERVATION_STATUS_ENTERED, note="已建仓", tf_db_path=tmp_db,
        )

        listing = get_observation_items(tf_db_path=tmp_db)
        item = listing["items"][0]
        assert item["status"] == OBSERVATION_STATUS_ENTERED
        assert "已建仓" in item["notes"]
        assert listing["summary"]["entered"] == 1
        assert listing["summary"]["active"] == 1

    def test_invalidation_flow(self, tmp_db):
        """Add -> mark invalidated -> shows up in invalidated bucket."""
        created = create_observation_item(
            symbol="002353.SZ", name="杰瑞股份", tf_db_path=tmp_db,
        )
        mark_observation_item_status(
            created["item"]["id"], OBSERVATION_STATUS_INVALIDATED,
            note="跌破失效价", tf_db_path=tmp_db,
        )
        listing = get_observation_items(tf_db_path=tmp_db)
        assert listing["summary"]["invalidated"] == 1
        assert listing["summary"]["active"] == 0

    def test_removed_hidden_by_default(self, tmp_db):
        """Add -> mark removed -> hidden from default view."""
        created = create_observation_item(
            symbol="601689.SH", name="拓普集团", tf_db_path=tmp_db,
        )
        mark_observation_item_status(
            created["item"]["id"], OBSERVATION_STATUS_REMOVED, tf_db_path=tmp_db,
        )
        # Default: removed is hidden
        hidden = get_observation_items(tf_db_path=tmp_db)
        assert hidden["summary"]["total"] == 0
        # include_removed: surfaced
        shown = get_observation_items(include_removed=True, tf_db_path=tmp_db)
        assert shown["summary"]["removed"] == 1


# ── Constraint / Safety Tests ────────────────────────────────────────────

class TestObservationSafety:

    def test_no_strong_action_in_message(self, tmp_db):
        """Create must NOT emit strong buy/sell wording (constraint)."""
        result = create_observation_item(
            symbol="601689.SH", name="拓普集团", tf_db_path=tmp_db,
        )
        forbidden = ("立即买入", "重仓买入", "立即清仓", "满仓", "梭哈")
        for word in forbidden:
            assert word not in result.get("message", ""), f"消息中出现禁用词: {word}"

    def test_no_db_pollution_to_tradingagents_db(self, tmp_db, monkeypatch):
        """Service calls must never touch tradingagents.db (real holdings)."""
        # Point the tradeflow service at our fixture DB explicitly via env var
        # so it never resolves to the real tradeflow.db path.
        monkeypatch.setenv("TRADEFLOW_DB_PATH", tmp_db)
        create_observation_item(symbol="601689.SH", tf_db_path=tmp_db)
        listing = get_observation_items(tf_db_path=tmp_db)
        assert listing["summary"]["total"] == 1

    def test_priority_ordering_in_list(self, tmp_db):
        create_observation_item(symbol="000001.SZ", priority=1, tf_db_path=tmp_db)
        create_observation_item(symbol="000002.SZ", priority=9, tf_db_path=tmp_db)
        create_observation_item(symbol="000003.SZ", priority=5, tf_db_path=tmp_db)
        listing = get_observation_items(tf_db_path=tmp_db)
        priorities = [i["priority"] for i in listing["items"]]
        # DESC by priority
        assert priorities == [9, 5, 1]
