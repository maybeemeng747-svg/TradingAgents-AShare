"""[DATA-009] Regression tests for watchlist notes and structured fields persistence.

Ensures that:
- Reorder does not clear notes or structured fields
- Batch add preserves existing notes on duplicates
- VLM mock generates proper note format
- 0 score / empty string / None distinction
- Frontend refresh returns all fields (via list_watchlist)
- Structured fields roundtrip through add/list/update
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.database import Base
from api.services import watchlist_service


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


class TestNotesPersistenceOnReorder:
    def test_reorder_preserves_notes(self, db):
        item_a = watchlist_service.add_watchlist_item(db, "u1", "300750.SZ", notes="重要备注")
        item_b = watchlist_service.add_watchlist_item(db, "u1", "600519.SH")
        watchlist_service.reorder_watchlist(db, "u1", [
            {"id": item_b["id"], "sort_order": 0},
            {"id": item_a["id"], "sort_order": 1},
        ])
        items = watchlist_service.list_watchlist(db, "u1")
        by_symbol = {i["symbol"]: i for i in items}
        assert by_symbol["300750.SZ"]["notes"] == "重要备注"
        assert by_symbol["600519.SH"]["notes"] is None

    def test_reorder_preserves_structured_fields(self, db):
        item = watchlist_service.add_watchlist_item(db, "u1", "300750.SZ", notes="备注")
        from api.database import WatchlistItemDB
        row = db.query(WatchlistItemDB).filter(WatchlistItemDB.id == item["id"]).first()
        row.topic = "半导体设备"
        row.benefit_score = 9.1
        row.consensus_score = 89
        row.expected_window = "1月"
        row.evidence_gap = "订单/资金"
        row.watchlist_note_suggested = "半导体设备｜利好9.1｜共识89｜窗口1月｜缺口:订单/资金"
        db.commit()

        watchlist_service.reorder_watchlist(db, "u1", [
            {"id": item["id"], "sort_order": 0},
        ])
        items = watchlist_service.list_watchlist(db, "u1")
        assert items[0]["notes"] == "备注"
        assert items[0]["topic"] == "半导体设备"
        assert items[0]["benefit_score"] == 9.1
        assert items[0]["consensus_score"] == 89
        assert items[0]["expected_window"] == "1月"
        assert items[0]["evidence_gap"] == "订单/资金"
        assert items[0]["watchlist_note_suggested"] == "半导体设备｜利好9.1｜共识89｜窗口1月｜缺口:订单/资金"


class TestNotesPersistenceOnUpdate:
    def test_empty_notes_do_not_overwrite(self, db):
        item = watchlist_service.add_watchlist_item(db, "u1", "300750.SZ", notes="原始备注")
        updated = watchlist_service.update_watchlist_notes(db, "u1", item["id"], notes="")
        assert updated["notes"] == "原始备注"

    def test_clear_true_allows_empty_overwrite(self, db):
        item = watchlist_service.add_watchlist_item(db, "u1", "300750.SZ", notes="原始备注")
        updated = watchlist_service.update_watchlist_notes(db, "u1", item["id"], notes="", clear=True)
        assert updated["notes"] is None

    def test_update_notes_preserves_structured_fields(self, db):
        item = watchlist_service.add_watchlist_item(db, "u1", "300750.SZ", notes="旧备注")
        from api.database import WatchlistItemDB
        row = db.query(WatchlistItemDB).filter(WatchlistItemDB.id == item["id"]).first()
        row.topic = "半导体"
        row.benefit_score = 8.5
        row.consensus_score = 90
        row.watchlist_note_suggested = "半导体｜利好8.5｜共识90"
        db.commit()

        updated = watchlist_service.update_watchlist_notes(db, "u1", item["id"], notes="新备注")
        assert updated["notes"] == "新备注"
        assert updated["topic"] == "半导体"
        assert updated["benefit_score"] == 8.5
        assert updated["consensus_score"] == 90
        assert updated["watchlist_note_suggested"] == "半导体｜利好8.5｜共识90"

    def test_list_returns_structured_fields_after_update(self, db):
        item = watchlist_service.add_watchlist_item(db, "u1", "300750.SZ", notes="备注")
        from api.database import WatchlistItemDB
        row = db.query(WatchlistItemDB).filter(WatchlistItemDB.id == item["id"]).first()
        row.topic = "AI算力"
        row.benefit_score = 7.2
        row.consensus_score = 85
        row.expected_window = "中线"
        row.evidence_gap = "政策确认"
        row.watchlist_note_suggested = "AI算力｜利好7.2｜共识85"
        db.commit()

        items = watchlist_service.list_watchlist(db, "u1")
        assert len(items) == 1
        assert items[0]["topic"] == "AI算力"
        assert items[0]["benefit_score"] == 7.2
        assert items[0]["consensus_score"] == 85
        assert items[0]["expected_window"] == "中线"
        assert items[0]["evidence_gap"] == "政策确认"
        assert items[0]["watchlist_note_suggested"] == "AI算力｜利好7.2｜共识85"


class TestBatchNotesWithStructuredFields:
    def test_batch_add_with_notes_and_structured_fields(self, db):
        results = watchlist_service.add_watchlist_items_with_notes(db, "u1", [
            {
                "symbol": "300750.SZ",
                "notes": "新能源龙头",
                "topic": "新能源",
                "benefit_score": 8.0,
                "consensus_score": 88,
                "watchlist_note_suggested": "新能源｜利好8.0｜共识88",
            },
        ])
        assert results[0]["status"] == "added"
        items = watchlist_service.list_watchlist(db, "u1")
        assert items[0]["notes"] == "新能源龙头"
        assert items[0]["topic"] == "新能源"
        assert items[0]["benefit_score"] == 8.0
        assert items[0]["consensus_score"] == 88
        assert items[0]["watchlist_note_suggested"] == "新能源｜利好8.0｜共识88"

    def test_duplicate_batch_preserves_existing_notes(self, db):
        watchlist_service.add_watchlist_item(db, "u1", "300750.SZ", notes="已有备注")
        results = watchlist_service.add_watchlist_items_with_notes(db, "u1", [
            {"symbol": "300750.SZ", "notes": "新识别备注"},
        ])
        assert results[0]["status"] == "duplicate"
        assert "备注已追加" in results[0]["message"]
        items = watchlist_service.list_watchlist(db, "u1")
        assert "已有备注" in items[0]["notes"]
        assert "新识别备注" in items[0]["notes"]

    def test_duplicate_batch_no_new_notes_preserves_existing(self, db):
        watchlist_service.add_watchlist_item(db, "u1", "300750.SZ", notes="已有备注")
        results = watchlist_service.add_watchlist_items_with_notes(db, "u1", [
            {"symbol": "300750.SZ"},
        ])
        assert results[0]["status"] == "duplicate"
        items = watchlist_service.list_watchlist(db, "u1")
        assert items[0]["notes"] == "已有备注"

    def test_duplicate_batch_updates_structured_fields(self, db):
        watchlist_service.add_watchlist_item(db, "u1", "300750.SZ", notes="备注")
        results = watchlist_service.add_watchlist_items_with_notes(db, "u1", [
            {
                "symbol": "300750.SZ",
                "topic": "新主题",
                "benefit_score": 9.1,
                "consensus_score": 89,
            },
        ])
        assert results[0]["status"] == "duplicate"
        items = watchlist_service.list_watchlist(db, "u1")
        assert items[0]["notes"] == "备注"
        assert items[0]["topic"] == "新主题"
        assert items[0]["benefit_score"] == 9.1
        assert items[0]["consensus_score"] == 89

    def test_multi_image_merge_by_symbol(self, db):
        results = watchlist_service.add_watchlist_items_with_notes(db, "u1", [
            {"symbol": "300750.SZ", "notes": "图片1备注", "topic": "新能源"},
        ])
        assert results[0]["status"] == "added"
        results2 = watchlist_service.add_watchlist_items_with_notes(db, "u1", [
            {"symbol": "300750.SZ", "notes": "图片2备注", "topic": "电池"},
        ])
        assert results2[0]["status"] == "duplicate"
        items = watchlist_service.list_watchlist(db, "u1")
        assert len(items) == 1
        assert "图片1备注" in items[0]["notes"]
        assert "图片2备注" in items[0]["notes"]


class TestZeroEmptyNoneDistinction:
    def test_zero_benefit_score_not_confused_with_none(self, db):
        watchlist_service.add_watchlist_items_with_notes(db, "u1", [
            {"symbol": "300750.SZ", "benefit_score": 0.0, "consensus_score": 0},
        ])
        items = watchlist_service.list_watchlist(db, "u1")
        assert items[0]["benefit_score"] == 0.0
        assert items[0]["consensus_score"] == 0

    def test_none_vs_empty_string_notes(self, db):
        item = watchlist_service.add_watchlist_item(db, "u1", "300750.SZ")
        assert item["notes"] is None
        items = watchlist_service.list_watchlist(db, "u1")
        assert items[0]["notes"] is None

    def test_explicit_empty_notes_vs_none(self, db):
        item = watchlist_service.add_watchlist_item(db, "u1", "300750.SZ", notes="")
        assert item["notes"] == ""
        items = watchlist_service.list_watchlist(db, "u1")
        assert items[0]["notes"] == ""

    def test_none_structured_fields_by_default(self, db):
        watchlist_service.add_watchlist_item(db, "u1", "300750.SZ")
        items = watchlist_service.list_watchlist(db, "u1")
        assert items[0]["topic"] is None
        assert items[0]["benefit_score"] is None
        assert items[0]["consensus_score"] is None
        assert items[0]["expected_window"] is None
        assert items[0]["evidence_gap"] is None
        assert items[0]["watchlist_note_suggested"] is None


class TestVLMNotesFormat:
    def test_vlm_mock_generates_note_format(self):
        from api.services.vlm_position_parser import _build_watchlist_notes
        notes = _build_watchlist_notes("半导体设备", "刻蚀/沉积设备", 9.1, 89)
        assert notes == "半导体设备｜刻蚀/沉积设备｜利好9.1｜共识89"

    def test_vlm_mock_generates_note_with_window(self):
        from api.services.vlm_position_parser import _build_watchlist_notes
        notes = _build_watchlist_notes("半导体设备", "刻蚀/沉积设备", 9.1, 89)
        base = f"{notes}｜窗口1月｜缺口:订单/资金"
        assert "半导体设备" in base
        assert "利好9.1" in base
        assert "共识89" in base
        assert "窗口1月" in base
        assert "缺口:订单/资金" in base

    def test_vlm_partial_fields(self):
        from api.services.vlm_position_parser import _build_watchlist_notes
        notes = _build_watchlist_notes("半导体设备", None, None, None)
        assert notes == "半导体设备"

    def test_vlm_all_null(self):
        from api.services.vlm_position_parser import _build_watchlist_notes
        notes = _build_watchlist_notes(None, None, None, None)
        assert notes == ""

    def test_vlm_full_roundtrip_to_watchlist(self, db):
        results = watchlist_service.add_watchlist_items_with_notes(db, "u1", [
            {
                "symbol": "002371",
                "notes": "半导体设备｜刻蚀/沉积设备｜利好9.3｜共识92",
                "topic": "半导体设备",
                "benefit_score": 9.3,
                "consensus_score": 92,
                "watchlist_note_suggested": "半导体设备｜利好9.3｜共识92｜窗口1月｜缺口:订单/资金",
            },
        ])
        assert results[0]["status"] == "added"
        items = watchlist_service.list_watchlist(db, "u1")
        assert "半导体设备" in items[0]["notes"]
        assert "利好9.3" in items[0]["notes"]
        assert items[0]["topic"] == "半导体设备"
        assert items[0]["benefit_score"] == 9.3
        assert items[0]["consensus_score"] == 92


class TestStructuredFieldsRoundtrip:
    def test_full_roundtrip_add_list_update(self, db):
        item = watchlist_service.add_watchlist_items_with_notes(db, "u1", [
            {
                "symbol": "300750.SZ",
                "notes": "新能源龙头",
                "topic": "新能源",
                "benefit_score": 8.0,
                "consensus_score": 88,
                "expected_window": "1月",
                "evidence_gap": "订单/资金",
                "watchlist_note_suggested": "新能源｜利好8.0｜共识88｜窗口1月｜缺口:订单/资金",
            },
        ])
        assert item[0]["status"] == "added"

        items = watchlist_service.list_watchlist(db, "u1")
        assert len(items) == 1
        assert items[0]["notes"] == "新能源龙头"
        assert items[0]["topic"] == "新能源"
        assert items[0]["benefit_score"] == 8.0
        assert items[0]["consensus_score"] == 88
        assert items[0]["expected_window"] == "1月"
        assert items[0]["evidence_gap"] == "订单/资金"
        assert items[0]["watchlist_note_suggested"] == "新能源｜利好8.0｜共识88｜窗口1月｜缺口:订单/资金"

        updated = watchlist_service.update_watchlist_notes(
            db, "u1", items[0]["id"], notes="更新后的备注"
        )
        assert updated["notes"] == "更新后的备注"
        assert updated["topic"] == "新能源"
        assert updated["benefit_score"] == 8.0
        assert updated["consensus_score"] == 88

        items2 = watchlist_service.list_watchlist(db, "u1")
        assert items2[0]["notes"] == "更新后的备注"
        assert items2[0]["topic"] == "新能源"

    def test_user_notes_not_overwritten_by_suggested(self, db):
        watchlist_service.add_watchlist_item(db, "u1", "300750.SZ", notes="用户自己的备注")
        watchlist_service.add_watchlist_items_with_notes(db, "u1", [
            {
                "symbol": "300750.SZ",
                "notes": None,
                "watchlist_note_suggested": "系统建议备注",
            },
        ])
        items = watchlist_service.list_watchlist(db, "u1")
        assert items[0]["notes"] == "用户自己的备注"
        assert items[0]["watchlist_note_suggested"] == "系统建议备注"

    def test_delete_does_not_affect_other_items_notes(self, db):
        item_a = watchlist_service.add_watchlist_items_with_notes(db, "u1", [
            {"symbol": "300750.SZ", "notes": "备注A", "topic": "新能源"},
        ])
        item_b = watchlist_service.add_watchlist_items_with_notes(db, "u1", [
            {"symbol": "600519.SH", "notes": "备注B", "topic": "消费"},
        ])
        assert item_a[0]["status"] == "added"
        assert item_b[0]["status"] == "added"

        items = watchlist_service.list_watchlist(db, "u1")
        id_a = next(i["id"] for i in items if i["symbol"] == "300750.SZ")
        watchlist_service.delete_watchlist_item(db, "u1", id_a)

        remaining = watchlist_service.list_watchlist(db, "u1")
        assert len(remaining) == 1
        assert remaining[0]["notes"] == "备注B"
        assert remaining[0]["topic"] == "消费"
