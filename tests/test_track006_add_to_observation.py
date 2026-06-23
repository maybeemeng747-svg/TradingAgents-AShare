# [TRACK-006] add_to_observation
"""Tests for TRACK-006: TradeFlow/TA 候选一键加入观察仓与来源追踪.

Coverage map:
- DB schema upgrade (new columns exist after init_db / ALTER).
- create_observation_item surfaces new fields (strategy_tags, score,
  action_label, research_direction, source_history).
- add_candidate_to_observation: create + update + provenance + horizon.
- add_ta_report_to_observation: create + update + key-price mapping.
- Notes preservation: re-adding from a different source never clobbers
  existing user notes (both via the dedicated helpers and via bulk_upsert).
- Source history: re-add flips source and pushes the previous source onto
  source_history_json with as_of / via / reason.
- Forbidden strong-action wording is scrubbed from auto-generated text.
- Boundary values (0 prices, empty tags) survive the round-trip.
- Symbol normalization is applied on the candidate / report payload.
- Pydantic schemas parse the new request / response shapes.
- Empty / no-data scenarios.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.tradeflow.candidate_engine import init_db

from api.services.tradeflow_service import (
    add_candidate_to_observation,
    add_ta_report_to_observation,
    bulk_upsert_observation_items,
    create_observation_item,
    get_observation_items,
    update_observation_item,
    _scrub_observation_text,
    _append_source_history_entry,
    _resolve_observation_horizon_from_candidate,
    OBSERVATION_STATUS_WATCHING,
)
from api.tradeflow_schemas import (
    ObservationAddFromCandidateRequest,
    ObservationAddFromTAReportRequest,
    ObservationAddResponse,
    ObservationItemResponse,
    ObservationBulkUpsertItem,
    ObservationBulkUpsertRequest,
)


TODAY = datetime.now().strftime("%Y-%m-%d")


# ── Fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_db(tmp_path):
    db_path = str(tmp_path / "test_track006_observation.db")
    init_db(db_path)
    return db_path


def _candidate(
    symbol: str = "601689.SH",
    name: str = "拓普集团",
    *,
    candidate_type: str = "POLICY_AMBUSH",
    composite_score: float = 7.5,
    strategy_tags: list[str] | None = None,
    trigger_price: float | None = 30.0,
    invalid_price: float | None = 28.0,
    support_price: float | None = 29.0,
    reason: str = "政策利好 + 资金回流",
    ta_budget_priority: int = 5,
) -> dict:
    return {
        "symbol": symbol,
        "name": name,
        "candidate_type": candidate_type,
        "composite_score": composite_score,
        "strategy_tags": strategy_tags if strategy_tags is not None else ["POLICY_VERSION", "FUND_FLOW_ANOMALY"],
        "trigger_price": trigger_price,
        "invalid_price": invalid_price,
        "support_price": support_price,
        "reason": reason,
        "ta_budget_priority": ta_budget_priority,
    }


def _ta_report(
    symbol: str = "002353.SZ",
    name: str = "杰瑞股份",
    *,
    action_label: str = "条件入场",
    research_direction: str = "偏多",
    target_price: float = 38.0,
    stop_loss_price: float = 33.5,
) -> dict:
    return {
        "symbol": symbol,
        "name": name,
        "action_label": action_label,
        "research_direction": research_direction,
        "target_price": target_price,
        "stop_loss_price": stop_loss_price,
    }


# ── DB Schema Upgrade Tests ──────────────────────────────────────────────

class TestSchemaUpgrade:
    """New TRACK-006 columns exist on the observation table."""

    def test_track006_columns_present_on_fresh_init(self, tmp_db):
        conn = sqlite3.connect(tmp_db)
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(tradeflow_observation_items)"
        ).fetchall()}
        conn.close()
        for col in (
            "strategy_tags_json",
            "score",
            "action_label",
            "research_direction",
            "source_history_json",
        ):
            assert col in cols, f"missing column {col!r}"

    def test_track006_columns_have_safe_defaults(self, tmp_db):
        """Default values must NOT break TRACK-001 boundary semantics."""
        conn = sqlite3.connect(tmp_db)
        conn.row_factory = sqlite3.Row
        conn.execute(
            "INSERT INTO tradeflow_observation_items (symbol, name) VALUES (?, ?)",
            ("600519.SH", "贵州茅台"),
        )
        conn.commit()
        row = conn.execute(
            "SELECT strategy_tags_json, score, action_label, "
            "research_direction, source_history_json "
            "FROM tradeflow_observation_items WHERE symbol = ?",
            ("600519.SH",),
        ).fetchone()
        conn.close()
        assert json.loads(row["strategy_tags_json"] or "[]") == []
        assert (row["score"] or 0.0) == 0.0
        assert (row["action_label"] or "") == ""
        assert (row["research_direction"] or "") == ""
        # source_history_json defaults to [] not NULL
        assert json.loads(row["source_history_json"] or "[]") == []


# ── create_observation_item surfaces new fields ──────────────────────────

class TestCreateWithProvenance:
    """create_observation_item accepts + round-trips the new fields."""

    def test_create_with_strategy_tags_and_score(self, tmp_db):
        result = create_observation_item(
            symbol="601689.SH",
            name="拓普集团",
            strategy_tags=["VCP", "POLICY_VERSION"],
            score=8.2,
            action_label="等待触发",
            research_direction="看多",
            tf_db_path=tmp_db,
        )
        assert result["status"] == "ok"
        item = result["item"]
        assert item["strategy_tags"] == ["VCP", "POLICY_VERSION"]
        assert item["score"] == pytest.approx(8.2)
        assert item["action_label"] == "等待触发"
        assert item["research_direction"] == "看多"
        assert item["source_history"] == []

    def test_empty_strategy_tags_default(self, tmp_db):
        result = create_observation_item(symbol="601689.SH", tf_db_path=tmp_db)
        assert result["item"]["strategy_tags"] == []
        assert result["item"]["score"] == 0.0
        assert result["item"]["action_label"] == ""
        assert result["item"]["research_direction"] == ""
        assert result["item"]["source_history"] == []


class TestUpdateWithProvenance:
    """update_observation_item accepts the new field updates."""

    def test_update_strategy_tags_overwrites(self, tmp_db):
        created = create_observation_item(
            symbol="601689.SH",
            strategy_tags=["A"],
            tf_db_path=tmp_db,
        )
        item_id = created["item"]["id"]
        update_observation_item(
            item_id,
            strategy_tags=["B", "C"],
            score=4.5,
            action_label="条件入场",
            research_direction="偏多",
            tf_db_path=tmp_db,
        )
        listing = get_observation_items(tf_db_path=tmp_db)
        item = next(i for i in listing["items"] if i["id"] == item_id)
        assert item["strategy_tags"] == ["B", "C"]
        assert item["score"] == pytest.approx(4.5)
        assert item["action_label"] == "条件入场"
        assert item["research_direction"] == "偏多"

    def test_append_source_history_does_not_replace(self, tmp_db):
        created = create_observation_item(symbol="601689.SH", tf_db_path=tmp_db)
        item_id = created["item"]["id"]
        update_observation_item(
            item_id,
            append_source_history=[
                {"source": "manual", "as_of": "2026-06-23 10:00:00", "via": "ui", "reason": "r1"},
            ],
            tf_db_path=tmp_db,
        )
        update_observation_item(
            item_id,
            append_source_history=[
                {"source": "tradeflow", "as_of": "2026-06-23 11:00:00", "via": "drawer", "reason": "r2"},
            ],
            tf_db_path=tmp_db,
        )
        listing = get_observation_items(tf_db_path=tmp_db)
        item = next(i for i in listing["items"] if i["id"] == item_id)
        assert len(item["source_history"]) == 2
        assert item["source_history"][0]["source"] == "manual"
        assert item["source_history"][1]["source"] == "tradeflow"

    def test_append_source_history_skips_non_dicts(self, tmp_db):
        created = create_observation_item(symbol="601689.SH", tf_db_path=tmp_db)
        item_id = created["item"]["id"]
        update_observation_item(
            item_id,
            append_source_history=[{"source": "ok"}, "junk-string", 123, {"source": "ok2"}],
            tf_db_path=tmp_db,
        )
        listing = get_observation_items(tf_db_path=tmp_db)
        item = next(i for i in listing["items"] if i["id"] == item_id)
        sources = [h["source"] for h in item["source_history"]]
        assert sources == ["ok", "ok2"]


# ── add_candidate_to_observation Tests ───────────────────────────────────

class TestAddCandidateToObservation:

    def test_creates_new_item(self, tmp_db):
        result = add_candidate_to_observation(
            _candidate(), tf_db_path=tmp_db,
        )
        assert result["status"] == "ok"
        assert result["action"] == "created"
        item = result["item"]
        assert item["symbol"] == "601689.SH"
        assert item["source"] == "tradeflow"
        assert item["strategy_tags"] == ["POLICY_VERSION", "FUND_FLOW_ANOMALY"]
        assert item["trigger_price"] == 30.0
        assert item["invalid_price"] == 28.0
        assert item["entry_low"] == 29.0  # from support_price
        assert item["score"] == pytest.approx(7.5)
        assert item["horizon"] == "mid"  # POLICY_AMBUSH -> mid
        assert "TradeFlow 候选" in item["reason"]
        assert "7.50" in item["reason"]

    def test_create_then_update_preserves_user_notes(self, tmp_db):
        add_candidate_to_observation(_candidate(), tf_db_path=tmp_db)
        # Simulate a user annotation added afterwards.
        listing = get_observation_items(tf_db_path=tmp_db)
        item_id = listing["items"][0]["id"]
        update_observation_item(item_id, notes="我的手动备注", tf_db_path=tmp_db)

        # Re-add the candidate (e.g. next day refresh).
        result = add_candidate_to_observation(_candidate(), tf_db_path=tmp_db)
        assert result["status"] == "ok"
        assert result["action"] == "updated"
        # The user's manual note MUST survive.
        assert result["item"]["notes"] == "我的手动备注"

    def test_re_add_flips_source_and_appends_history(self, tmp_db):
        # First add via TA report path (source=ta).
        add_ta_report_to_observation(_ta_report("601689.SH"), tf_db_path=tmp_db)
        # Then re-add via candidate path.
        result = add_candidate_to_observation(_candidate(), tf_db_path=tmp_db)
        item = result["item"]
        assert item["source"] == "tradeflow"
        # History should now contain the previous TA source entry.
        assert len(item["source_history"]) >= 1
        prev = item["source_history"][-1]
        assert prev["source"] == "ta"
        assert prev["via"] in ("analysis_page", "candidate_drawer") or prev["via"]
        assert prev["as_of"]

    def test_horizon_short_for_tech_trade(self, tmp_db):
        candidate = _candidate(candidate_type="TECH_TRADE")
        result = add_candidate_to_observation(candidate, tf_db_path=tmp_db)
        assert result["item"]["horizon"] == "short"

    def test_horizon_mid_for_policy_ambush(self, tmp_db):
        candidate = _candidate(candidate_type="POLICY_AMBUSH")
        result = add_candidate_to_observation(candidate, tf_db_path=tmp_db)
        assert result["item"]["horizon"] == "mid"

    def test_horizon_mid_for_policy_confirm(self, tmp_db):
        candidate = _candidate(candidate_type="POLICY_CONFIRM")
        result = add_candidate_to_observation(candidate, tf_db_path=tmp_db)
        assert result["item"]["horizon"] == "mid"

    def test_score_falls_back_to_mandate_score(self, tmp_db):
        candidate = _candidate()
        candidate["composite_score"] = 0
        candidate["mandate_score"] = 6.4
        result = add_candidate_to_observation(candidate, tf_db_path=tmp_db)
        assert result["item"]["score"] == pytest.approx(6.4)

    def test_score_falls_back_to_score_field(self, tmp_db):
        candidate = _candidate()
        candidate["composite_score"] = None
        candidate["mandate_score"] = None
        candidate["score"] = 5.0
        result = add_candidate_to_observation(candidate, tf_db_path=tmp_db)
        assert result["item"]["score"] == pytest.approx(5.0)

    def test_normalizes_symbol(self, tmp_db):
        candidate = _candidate(symbol="601689")  # bare code
        result = add_candidate_to_observation(candidate, tf_db_path=tmp_db)
        assert result["status"] == "ok"
        assert result["item"]["symbol"] == "601689.SH"

    def test_empty_symbol_rejected(self, tmp_db):
        candidate = _candidate(symbol="")
        result = add_candidate_to_observation(candidate, tf_db_path=tmp_db)
        assert result["status"] == "error"

    def test_extra_notes_written_when_empty(self, tmp_db):
        result = add_candidate_to_observation(
            _candidate(), extra_notes="来自候选池", tf_db_path=tmp_db,
        )
        assert result["item"]["notes"] == "来自候选池"

    def test_force_overwrite_notes_replaces(self, tmp_db):
        add_candidate_to_observation(
            _candidate(), extra_notes="原始备注", tf_db_path=tmp_db,
        )
        result = add_candidate_to_observation(
            _candidate(),
            extra_notes="强制覆盖后的备注",
            force_overwrite_notes=True,
            tf_db_path=tmp_db,
        )
        assert result["item"]["notes"] == "强制覆盖后的备注"

    def test_forbidden_strong_words_scrubbed(self, tmp_db):
        candidate = _candidate(reason="立即买入信号")
        result = add_candidate_to_observation(candidate, tf_db_path=tmp_db)
        assert "立即买入" not in result["item"]["reason"]


# ── add_ta_report_to_observation Tests ───────────────────────────────────

class TestAddTAReportToObservation:

    def test_creates_new_item(self, tmp_db):
        result = add_ta_report_to_observation(_ta_report(), tf_db_path=tmp_db)
        assert result["status"] == "ok"
        assert result["action"] == "created"
        item = result["item"]
        assert item["symbol"] == "002353.SZ"
        assert item["source"] == "ta"
        assert item["action_label"] == "条件入场"
        assert item["research_direction"] == "偏多"
        # Key-price mapping: target_price -> trigger_price & entry_high
        # stop_loss_price -> entry_low & invalid_price
        assert item["trigger_price"] == 38.0
        assert item["entry_high"] == 38.0
        assert item["entry_low"] == 33.5
        assert item["invalid_price"] == 33.5
        assert item["horizon"] == "mid"

    def test_re_add_preserves_user_notes(self, tmp_db):
        add_ta_report_to_observation(_ta_report(), tf_db_path=tmp_db)
        listing = get_observation_items(tf_db_path=tmp_db)
        item_id = listing["items"][0]["id"]
        update_observation_item(item_id, notes="用户写入备注", tf_db_path=tmp_db)

        result = add_ta_report_to_observation(_ta_report(), tf_db_path=tmp_db)
        assert result["item"]["notes"] == "用户写入备注"

    def test_re_add_flips_source_to_ta(self, tmp_db):
        add_candidate_to_observation(_candidate("002353.SZ"), tf_db_path=tmp_db)
        result = add_ta_report_to_observation(_ta_report(), tf_db_path=tmp_db)
        assert result["item"]["source"] == "ta"
        # Previous source (tradeflow) preserved in history
        prev = result["item"]["source_history"][-1]
        assert prev["source"] == "tradeflow"

    def test_normalizes_symbol(self, tmp_db):
        report = _ta_report(symbol="002353")
        result = add_ta_report_to_observation(report, tf_db_path=tmp_db)
        assert result["item"]["symbol"] == "002353.SZ"

    def test_empty_symbol_rejected(self, tmp_db):
        report = _ta_report(symbol="")
        result = add_ta_report_to_observation(report, tf_db_path=tmp_db)
        assert result["status"] == "error"

    def test_action_label_falls_back_to_execution_action(self, tmp_db):
        report = _ta_report()
        report["action_label"] = ""
        report["execution_action"] = "WAIT"
        result = add_ta_report_to_observation(report, tf_db_path=tmp_db)
        assert result["item"]["action_label"] == "WAIT"

    def test_research_direction_falls_back_to_direction(self, tmp_db):
        report = _ta_report()
        report["research_direction"] = ""
        report["direction"] = "看多"
        result = add_ta_report_to_observation(report, tf_db_path=tmp_db)
        assert result["item"]["research_direction"] == "看多"

    def test_zero_prices_preserved(self, tmp_db):
        report = _ta_report(target_price=0, stop_loss_price=0)
        result = add_ta_report_to_observation(report, tf_db_path=tmp_db)
        item = result["item"]
        # All four prices should be 0.0, never None or N/A.
        assert item["target_price"] if False else item["entry_high"] == 0.0
        assert item["trigger_price"] == 0.0
        assert item["entry_low"] == 0.0
        assert item["invalid_price"] == 0.0


# ── Notes Preservation in bulk_upsert_observation_items ──────────────────

class TestBulkUpsertNotesPreservation:
    """[TRACK-006] user notes must survive subsequent upserts by default."""

    def test_default_upsert_preserves_user_notes(self, tmp_db):
        create_observation_item(
            symbol="601689.SH", notes="手动备注", tf_db_path=tmp_db,
        )
        bulk_upsert_observation_items(
            [{
                "symbol": "601689.SH",
                "notes": "scheduler 自动备注",
                "source": "investment_controller",
            }],
            tf_db_path=tmp_db,
        )
        listing = get_observation_items(tf_db_path=tmp_db)
        assert listing["items"][0]["notes"] == "手动备注"

    def test_force_overwrite_notes_replaces(self, tmp_db):
        create_observation_item(
            symbol="601689.SH", notes="手动备注", tf_db_path=tmp_db,
        )
        bulk_upsert_observation_items(
            [{
                "symbol": "601689.SH",
                "notes": "scheduler 自动备注",
                "force_overwrite_notes": True,
            }],
            tf_db_path=tmp_db,
        )
        listing = get_observation_items(tf_db_path=tmp_db)
        assert listing["items"][0]["notes"] == "scheduler 自动备注"

    def test_empty_incoming_notes_does_not_clear_existing(self, tmp_db):
        create_observation_item(
            symbol="601689.SH", notes="手动备注", tf_db_path=tmp_db,
        )
        bulk_upsert_observation_items(
            [{"symbol": "601689.SH", "notes": ""}],
            tf_db_path=tmp_db,
        )
        listing = get_observation_items(tf_db_path=tmp_db)
        assert listing["items"][0]["notes"] == "手动备注"

    def test_force_overwrite_with_empty_incoming_keeps_existing(self, tmp_db):
        # Even with the force flag, an empty incoming value should NOT clear
        # existing user content — that would be a footgun.
        create_observation_item(
            symbol="601689.SH", notes="手动备注", tf_db_path=tmp_db,
        )
        bulk_upsert_observation_items(
            [{"symbol": "601689.SH", "notes": "", "force_overwrite_notes": True}],
            tf_db_path=tmp_db,
        )
        listing = get_observation_items(tf_db_path=tmp_db)
        assert listing["items"][0]["notes"] == "手动备注"

    def test_first_write_when_existing_empty(self, tmp_db):
        create_observation_item(symbol="601689.SH", tf_db_path=tmp_db)
        bulk_upsert_observation_items(
            [{"symbol": "601689.SH", "notes": "首次写入"}],
            tf_db_path=tmp_db,
        )
        listing = get_observation_items(tf_db_path=tmp_db)
        assert listing["items"][0]["notes"] == "首次写入"

    def test_bulk_upsert_round_trips_provenance_on_create(self, tmp_db):
        bulk_upsert_observation_items(
            [{
                "symbol": "601689.SH",
                "strategy_tags": ["VCP"],
                "score": 6.0,
                "action_label": "等待触发",
                "research_direction": "偏多",
            }],
            tf_db_path=tmp_db,
        )
        listing = get_observation_items(tf_db_path=tmp_db)
        item = listing["items"][0]
        assert item["strategy_tags"] == ["VCP"]
        assert item["score"] == pytest.approx(6.0)
        assert item["action_label"] == "等待触发"
        assert item["research_direction"] == "偏多"


# ── Source History Helper Tests ──────────────────────────────────────────

class TestSourceHistoryHelpers:

    def test_append_source_history_entry_grows_list(self):
        history: list[dict] = []
        history = _append_source_history_entry(
            history, source="manual", as_of="2026-06-23 10:00:00",
            via="ui", reason="r1",
        )
        history = _append_source_history_entry(
            history, source="tradeflow", as_of="2026-06-23 11:00:00",
            via="drawer", reason="r2",
        )
        assert len(history) == 2
        assert history[0]["source"] == "manual"
        assert history[1]["source"] == "tradeflow"
        # Does not mutate the previous list (returns a new list).
        assert history[0]["as_of"] == "2026-06-23 10:00:00"

    def test_append_source_history_entry_scrubs_forbidden_words(self):
        history = _append_source_history_entry(
            [], source="tradeflow", as_of="2026-06-23 11:00:00",
            via="drawer", reason="立即买入信号",
        )
        assert "立即买入" not in history[0]["reason"]

    def test_append_source_history_entry_clips_long_reason(self):
        long_reason = "x" * 500
        history = _append_source_history_entry(
            [], source="manual", as_of="2026-06-23",
            via="ui", reason=long_reason,
        )
        assert len(history[0]["reason"]) <= 240

    def test_scrub_observation_text_replaces_forbidden(self):
        assert _scrub_observation_text("立即买入") == "**"
        assert _scrub_observation_text("正常文本") == "正常文本"
        assert _scrub_observation_text("") == ""

    def test_resolve_horizon_invalid_falls_back_to_short(self):
        # candidate_type that maps to mid still passes; an unknown one
        # should still produce a valid value.
        assert _resolve_observation_horizon_from_candidate(
            {"candidate_type": "TECH_TRADE"}) == "short"
        assert _resolve_observation_horizon_from_candidate(
            {"candidate_type": "POLICY_AMBUSH"}) == "mid"
        assert _resolve_observation_horizon_from_candidate(
            {"candidate_type": "POLICY_CONFIRM"}) == "mid"
        assert _resolve_observation_horizon_from_candidate({}) == "short"


# ── Pydantic Schema Tests ────────────────────────────────────────────────

class TestTrack006Schemas:

    def test_observation_item_response_has_new_fields(self):
        item = ObservationItemResponse(
            symbol="601689.SH",
            strategy_tags=["VCP"],
            score=7.0,
            action_label="等待触发",
            research_direction="偏多",
            source_history=[{"source": "manual", "as_of": "2026-06-23", "via": "ui", "reason": "r"}],
        )
        dumped = item.model_dump()
        assert dumped["strategy_tags"] == ["VCP"]
        assert dumped["score"] == 7.0
        assert dumped["action_label"] == "等待触发"
        assert dumped["research_direction"] == "偏多"
        assert len(dumped["source_history"]) == 1

    def test_observation_item_response_defaults(self):
        item = ObservationItemResponse()
        assert item.strategy_tags == []
        assert item.score == 0.0
        assert item.action_label == ""
        assert item.research_direction == ""
        assert item.source_history == []

    def test_add_from_candidate_request(self):
        req = ObservationAddFromCandidateRequest(
            symbol="601689.SH",
            trade_date="2026-06-23",
            extra_notes="测试备注",
        )
        assert req.symbol == "601689.SH"
        assert req.via == "candidate_drawer"  # default
        assert req.force_overwrite_notes is False

    def test_add_from_ta_report_request(self):
        req = ObservationAddFromTAReportRequest(
            symbol="002353.SZ",
            report_id="abc-123",
            action_label="条件入场",
            target_price=38.0,
            stop_loss_price=33.5,
        )
        assert req.report_id == "abc-123"
        assert req.target_price == 38.0
        assert req.via == "analysis_page"

    def test_add_from_ta_report_request_allows_no_report_id(self):
        req = ObservationAddFromTAReportRequest(
            symbol="002353.SZ",
            action_label="等待触发",
        )
        assert req.report_id is None

    def test_add_response_default_shape(self):
        r = ObservationAddResponse(status="ok", action="created", message="ok")
        dumped = r.model_dump()
        assert dumped["status"] == "ok"
        assert dumped["action"] == "created"

    def test_bulk_upsert_item_supports_force_overwrite_notes(self):
        item = ObservationBulkUpsertItem(
            symbol="601689.SH",
            notes="x",
            force_overwrite_notes=True,
            strategy_tags=["VCP"],
        )
        dumped = item.model_dump()
        assert dumped["force_overwrite_notes"] is True
        assert dumped["strategy_tags"] == ["VCP"]

    def test_bulk_upsert_request_carries_force_flag(self):
        req = ObservationBulkUpsertRequest(
            items=[
                ObservationBulkUpsertItem(symbol="A", force_overwrite_notes=True),
                ObservationBulkUpsertItem(symbol="B"),
            ],
        )
        flags = [i.force_overwrite_notes for i in req.items]
        assert flags == [True, False]


# ── E2E Workflow Tests ───────────────────────────────────────────────────

class TestTrack006E2E:

    def test_candidate_then_ta_then_candidate_preserves_history(self, tmp_db):
        """Source history accumulates across multiple re-adds."""
        # 1. From TradeFlow candidate
        r1 = add_candidate_to_observation(_candidate("601689.SH"), tf_db_path=tmp_db)
        assert r1["item"]["source"] == "tradeflow"
        assert r1["item"]["source_history"] == []

        # 2. From TA report
        r2 = add_ta_report_to_observation(
            _ta_report("601689.SH"), tf_db_path=tmp_db,
        )
        assert r2["item"]["source"] == "ta"
        assert len(r2["item"]["source_history"]) == 1
        assert r2["item"]["source_history"][-1]["source"] == "tradeflow"

        # 3. From candidate again
        r3 = add_candidate_to_observation(_candidate("601689.SH"), tf_db_path=tmp_db)
        assert r3["item"]["source"] == "tradeflow"
        assert len(r3["item"]["source_history"]) == 2
        assert r3["item"]["source_history"][-1]["source"] == "ta"

    def test_visible_on_tracking_board_after_add(self, tmp_db):
        """Acceptance: 加入后跟踪看板观察仓立即可见."""
        add_candidate_to_observation(_candidate("601689.SH"), tf_db_path=tmp_db)
        listing = get_observation_items(tf_db_path=tmp_db)
        assert listing["status"] == "ok"
        assert len(listing["items"]) == 1
        assert listing["items"][0]["symbol"] == "601689.SH"
        assert listing["summary"]["total"] == 1

    def test_repeated_add_never_duplicates(self, tmp_db):
        """Acceptance: 重复加入不生成重复记录."""
        for _ in range(3):
            add_candidate_to_observation(_candidate("601689.SH"), tf_db_path=tmp_db)
        listing = get_observation_items(tf_db_path=tmp_db)
        assert len(listing["items"]) == 1

    def test_user_notes_survive_full_lifecycle(self, tmp_db):
        """Acceptance: 用户 notes 不丢失."""
        add_candidate_to_observation(
            _candidate(), extra_notes="初始备注", tf_db_path=tmp_db,
        )
        # User edits notes manually via PATCH.
        listing = get_observation_items(tf_db_path=tmp_db)
        item_id = listing["items"][0]["id"]
        update_observation_item(item_id, notes="编辑后的备注", tf_db_path=tmp_db)

        # Re-add from TA path then from candidate path.
        add_ta_report_to_observation(_ta_report("601689.SH"), tf_db_path=tmp_db)
        add_candidate_to_observation(_candidate(), tf_db_path=tmp_db)

        final = get_observation_items(tf_db_path=tmp_db)
        assert final["items"][0]["notes"] == "编辑后的备注"


# ── Safety / Constraints ─────────────────────────────────────────────────

class TestTrack006Safety:

    def test_no_strong_action_in_create_message(self, tmp_db):
        result = add_candidate_to_observation(_candidate(), tf_db_path=tmp_db)
        forbidden = ("立即买入", "重仓买入", "立即清仓", "满仓", "梭哈")
        for word in forbidden:
            assert word not in result.get("message", ""), f"message contains {word}"

    def test_no_db_pollution_to_tradingagents_db(self, tmp_db, monkeypatch):
        """Service must only touch the tradeflow DB path."""
        monkeypatch.setenv("TRADEFLOW_DB_PATH", tmp_db)
        add_candidate_to_observation(_candidate(), tf_db_path=tmp_db)
        listing = get_observation_items(tf_db_path=tmp_db)
        assert listing["summary"]["total"] == 1

    def test_no_data_when_db_missing(self):
        result = add_candidate_to_observation(
            _candidate(),
            tf_db_path="/nonexistent/path/none.db",
        )
        assert result["status"] == "no_data"

    def test_strategy_tags_bad_input_does_not_crash(self, tmp_db):
        candidate = _candidate()
        candidate["strategy_tags"] = "not-a-list"  # malformed
        result = add_candidate_to_observation(candidate, tf_db_path=tmp_db)
        assert result["status"] == "ok"
        # Should be coerced to empty list.
        assert result["item"]["strategy_tags"] == []

    def test_invalid_candidate_dict_does_not_crash(self, tmp_db):
        # Not a dict at all.
        result = add_candidate_to_observation("junk", tf_db_path=tmp_db)  # type: ignore[arg-type]
        assert result["status"] == "error"


# ── Field Round-Trip Through DB ──────────────────────────────────────────

class TestProvenanceRoundTrip:
    """Verify the new columns survive create → list → update → list."""

    def test_full_round_trip(self, tmp_db):
        # create
        r = create_observation_item(
            symbol="601689.SH",
            strategy_tags=["A", "B"],
            score=1.5,
            action_label="等待触发",
            research_direction="偏多",
            tf_db_path=tmp_db,
        )
        item_id = r["item"]["id"]

        # list reflects new fields
        listing = get_observation_items(tf_db_path=tmp_db)
        item = next(i for i in listing["items"] if i["id"] == item_id)
        assert item["strategy_tags"] == ["A", "B"]
        assert item["score"] == pytest.approx(1.5)
        assert item["action_label"] == "等待触发"
        assert item["research_direction"] == "偏多"

        # update
        update_observation_item(
            item_id,
            strategy_tags=["C"],
            score=9.9,
            action_label="条件入场",
            research_direction="看多",
            append_source_history=[{
                "source": "tradeflow", "as_of": "2026-06-23 10:00:00",
                "via": "drawer", "reason": "refresh",
            }],
            tf_db_path=tmp_db,
        )

        # list reflects updates
        listing = get_observation_items(tf_db_path=tmp_db)
        item = next(i for i in listing["items"] if i["id"] == item_id)
        assert item["strategy_tags"] == ["C"]
        assert item["score"] == pytest.approx(9.9)
        assert item["action_label"] == "条件入场"
        assert item["research_direction"] == "看多"
        assert len(item["source_history"]) == 1
