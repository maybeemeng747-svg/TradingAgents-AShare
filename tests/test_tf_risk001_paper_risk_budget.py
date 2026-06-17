# [TF-RISK-001] paper_risk_budget
"""Tests for TF-RISK-001: 5000 元试跑风险预算与仓位纪律.

Covers:
- Hard rejection: missing trigger_price / invalid_price / low data_quality_score.
- per_ticket_max amount clamping.
- Observation downgrade: daily_new_max / max_concurrent_tracking / budget.
- Observation candidates cannot become pending/open (no auto-trade discipline).
- risk_exposure computation in ledger summary.
- Config override via update_paper_ledger_config.
- Default risk_budget seeded into config on first ledger creation.
- Pydantic schema additions.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.tradeflow.candidate_engine import init_db

from api.services.tradeflow_service import (
    add_paper_candidate,
    confirm_paper_action,
    get_paper_ledger,
    update_paper_observe_state,
    update_paper_ledger_config,
)
from api.tradeflow_schemas import (
    PaperActionResponse,
    PaperLedgerResponse,
    PaperLedgerSummary,
)


TODAY = datetime.now().strftime("%Y-%m-%d")


@pytest.fixture
def tmp_db(tmp_path):
    db_path = str(tmp_path / "test_risk_budget.db")
    init_db(db_path)
    return db_path


def _add(symbol: str, db: str, **kw):
    """Convenience wrapper for add_paper_candidate with sane defaults."""
    kw.setdefault("name", symbol)
    kw.setdefault("trade_date", TODAY)
    kw.setdefault("trigger_price", 30.0)
    kw.setdefault("invalid_price", 28.0)
    kw.setdefault("planned_amount", 1000)
    return add_paper_candidate(symbol=symbol, tf_db_path=db, **kw)


# ── Default config seeding ──────────────────────────────────────────────

class TestRiskBudgetDefaults:

    def test_risk_budget_seeded_on_creation(self, tmp_db):
        get_paper_ledger(tf_db_path=tmp_db)
        import sqlite3
        conn = sqlite3.connect(tmp_db)
        import json as _json
        cfg = _json.loads(conn.execute(
            "SELECT config_json FROM tradeflow_paper_ledger LIMIT 1"
        ).fetchone()[0])
        conn.close()
        rb = cfg["risk_budget"]
        assert rb["principal"] == 5000
        assert rb["per_ticket_max"] == 1500
        assert rb["per_ticket_min"] == 500
        assert rb["daily_new_max"] == 3
        assert rb["max_concurrent_tracking"] == 5
        assert rb["require_trigger_price"] is True
        assert rb["require_invalid_price"] is True
        assert rb["min_data_quality_score"] == 40

    def test_summary_has_empty_risk_exposure_when_no_data(self):
        result = get_paper_ledger(tf_db_path="/nonexistent/path/none.db")
        assert result["status"] == "no_data"
        re = result["summary"]["risk_exposure"]
        assert re["principal"] == 5000
        assert re["invested"] == 0.0
        assert re["remaining"] == 5000
        assert re["budget_utilization_pct"] == 0.0
        assert re["per_ticket_max"] == 1500


# ── Hard rejection rules ────────────────────────────────────────────────

class TestRiskBudgetHardReject:

    def test_reject_missing_trigger_price(self, tmp_db):
        result = add_paper_candidate(
            symbol="601689.SH", name="拓普集团", trade_date=TODAY,
            invalid_price=28.0, planned_amount=1000, tf_db_path=tmp_db,
        )
        assert result["status"] == "rejected"
        assert result["rejected"] is True
        assert result["rule"] == "require_trigger_price"
        assert "触发价" in result["reason"]
        # Nothing was added
        ledger = get_paper_ledger(tf_db_path=tmp_db)
        assert ledger["summary"]["total_trades"] == 0

    def test_reject_missing_invalid_price(self, tmp_db):
        result = add_paper_candidate(
            symbol="601689.SH", name="拓普集团", trade_date=TODAY,
            trigger_price=30.0, planned_amount=1000, tf_db_path=tmp_db,
        )
        assert result["status"] == "rejected"
        assert result["rejected"] is True
        assert result["rule"] == "require_invalid_price"
        assert "失效价" in result["reason"]
        ledger = get_paper_ledger(tf_db_path=tmp_db)
        assert ledger["summary"]["total_trades"] == 0

    def test_reject_low_data_quality(self, tmp_db):
        result = _add("601689.SH", tmp_db, data_quality_score=20)
        assert result["status"] == "rejected"
        assert result["rule"] == "min_data_quality_score"
        ledger = get_paper_ledger(tf_db_path=tmp_db)
        assert ledger["summary"]["total_trades"] == 0

    def test_accept_high_data_quality(self, tmp_db):
        result = _add("601689.SH", tmp_db, data_quality_score=60)
        assert result["status"] == "ok"

    def test_no_quality_score_is_allowed(self, tmp_db):
        # When no score is supplied, the quality gate is not enforced.
        result = _add("601689.SH", tmp_db)
        assert result["status"] == "ok"

    def test_reject_response_serializes_through_schema(self, tmp_db):
        result = add_paper_candidate(
            symbol="601689.SH", name="拓普集团", trade_date=TODAY,
            trigger_price=30.0, planned_amount=1000, tf_db_path=tmp_db,
        )
        model = PaperActionResponse(**result)
        assert model.status == "rejected"
        assert model.rejected is True
        assert model.rule == "require_invalid_price"


# ── Amount clamping ─────────────────────────────────────────────────────

class TestPerTicketCap:

    def test_planned_amount_capped_to_per_ticket_max(self, tmp_db):
        result = _add("601689.SH", tmp_db, planned_amount=99999)
        assert result["status"] == "ok"
        assert result["planned_amount"] == 1500  # default per_ticket_max

    def test_default_amount_within_cap(self, tmp_db):
        result = add_paper_candidate(
            symbol="601689.SH", name="拓普集团", trade_date=TODAY,
            trigger_price=30.0, invalid_price=28.0, tf_db_path=tmp_db,
        )
        assert result["planned_amount"] == 1000


# ── Observation downgrade ───────────────────────────────────────────────

class TestObservationDowngrade:

    def test_daily_new_max_downgrades_to_observation(self, tmp_db):
        # default daily_new_max = 3 → 4th today is observation
        for i in range(3):
            assert _add(f"00000{i}.SZ", tmp_db)["status"] == "ok"
        fourth = _add("000003.SZ", tmp_db)
        assert fourth["status"] == "ok"
        assert fourth["downgraded_to"] == "observation"
        assert "当日新增上限" in fourth["reason"]
        ledger = get_paper_ledger(tf_db_path=tmp_db)
        assert ledger["summary"]["tracking_count"] == 3
        assert ledger["summary"]["observation_count"] == 1

    def test_max_concurrent_tracking_downgrades(self, tmp_db):
        update_paper_ledger_config(
            {"risk_budget": {"daily_new_max": 10, "max_concurrent_tracking": 2}},
            tf_db_path=tmp_db,
        )
        assert _add("000000.SZ", tmp_db)["status"] == "ok"
        assert _add("000001.SZ", tmp_db)["status"] == "ok"
        third = _add("000002.SZ", tmp_db)
        assert third["downgraded_to"] == "observation"
        assert "并发跟踪数" in third["reason"]

    def test_observation_cannot_be_bought(self, tmp_db):
        update_paper_ledger_config(
            {"risk_budget": {"daily_new_max": 1, "max_concurrent_tracking": 5}},
            tf_db_path=tmp_db,
        )
        _add("000000.SZ", tmp_db)  # tracking
        obs = _add("000001.SZ", tmp_db)  # downgraded to observation
        assert obs["downgraded_to"] == "observation"
        result = confirm_paper_action(obs["trade_id"], "buy", 30.0, tf_db_path=tmp_db)
        assert result["status"] == "blocked"

    def test_observation_not_promoted_by_observe_trigger(self, tmp_db):
        update_paper_ledger_config(
            {"risk_budget": {"daily_new_max": 1, "max_concurrent_tracking": 5}},
            tf_db_path=tmp_db,
        )
        _add("000000.SZ", tmp_db)
        obs = _add("000001.SZ", tmp_db)
        update_paper_observe_state("000001.SZ", "TRIGGERED", tf_db_path=tmp_db)
        ledger = get_paper_ledger(tf_db_path=tmp_db)
        obs_trade = next(t for t in ledger["trades"] if t["id"] == obs["trade_id"])
        # Observation stays observation — never becomes pending.
        assert obs_trade["status"] == "observation"
        assert ledger["summary"]["pending_count"] == 0


# ── risk_exposure computation ───────────────────────────────────────────

class TestRiskExposure:

    def test_risk_exposure_fields(self, tmp_db):
        _add("601689.SH", tmp_db, planned_amount=1000)
        _add("002353.SZ", tmp_db, planned_amount=1000)
        ledger = get_paper_ledger(tf_db_path=tmp_db)
        re = ledger["summary"]["risk_exposure"]
        assert re["principal"] == 5000
        assert re["invested"] == 2000.0  # 2 tracking × 1000 reserved [TF-RISK-001]
        assert re["remaining"] == 3000.0
        assert re["per_ticket_max"] == 1500
        assert re["per_ticket_min"] == 500
        assert re["daily_new_max"] == 3
        assert re["max_concurrent_tracking"] == 5
        assert re["daily_new_today"] == 2
        assert re["tracking_count"] == 2  # tracking + pending
        assert re["budget_utilization_pct"] == 40.0

    def test_risk_exposure_after_buy(self, tmp_db):
        update_paper_ledger_config(
            {"risk_budget": {"per_ticket_max": 1500}}, tf_db_path=tmp_db,
        )
        _add("601689.SH", tmp_db, planned_amount=1000)
        ledger = get_paper_ledger(tf_db_path=tmp_db)
        confirm_paper_action(ledger["trades"][0]["id"], "buy", 30.0, tf_db_path=tmp_db)
        ledger = get_paper_ledger(tf_db_path=tmp_db)
        re = ledger["summary"]["risk_exposure"]
        assert re["invested"] == 1000.0
        assert re["remaining"] == 4000.0
        assert re["budget_utilization_pct"] == 20.0

    def test_risk_exposure_serializes_through_schema(self, tmp_db):
        _add("601689.SH", tmp_db)
        data = get_paper_ledger(tf_db_path=tmp_db)
        model = PaperLedgerResponse(**data)
        assert model.summary.risk_exposure["principal"] == 5000
        assert model.summary.observation_count == 0

    def test_tracking_pending_reserved_in_budget(self, tmp_db):
        """tracking/pending 的 planned_amount 应计入预算占用，remaining 要扣减。"""
        update_paper_ledger_config(
            {"risk_budget": {"per_ticket_max": 1500, "daily_new_max": 10, "max_concurrent_tracking": 10}},
            tf_db_path=tmp_db,
        )
        # 加入 3 只 tracking（不 confirm buy，保持 tracking 状态）
        _add("601689.SH", tmp_db, planned_amount=1500)
        _add("000001.SZ", tmp_db, planned_amount=1500)
        _add("000002.SZ", tmp_db, planned_amount=1500)

        ledger = get_paper_ledger(tf_db_path=tmp_db)
        re = ledger["summary"]["risk_exposure"]
        # 3 × 1500 = 4500 预留，remaining = 5000 - 4500 = 500
        assert re["invested"] == 4500.0
        assert re["remaining"] == 500.0

        # 第 4 只 1500 应该被降级为 observation（remaining 只有 500）
        result = _add("600000.SH", tmp_db, planned_amount=1500)
        assert result.get("downgraded_to") == "observation"
        assert "风险预算不足" in result.get("reason", "")

    def test_risk_exposure_mixed_statuses(self, tmp_db):
        """tracking + open 混合状态下，risk_exposure 应统计所有活跃占用。"""
        update_paper_ledger_config(
            {"risk_budget": {"per_ticket_max": 1500, "daily_new_max": 10, "max_concurrent_tracking": 10}},
            tf_db_path=tmp_db,
        )
        # 1 只 tracking
        _add("601689.SH", tmp_db, planned_amount=1000)
        # 1 只 buy → open
        _add("000001.SZ", tmp_db, planned_amount=1500)
        ledger = get_paper_ledger(tf_db_path=tmp_db)
        confirm_paper_action(ledger["trades"][0]["id"], "buy", 30.0, tf_db_path=tmp_db)

        ledger = get_paper_ledger(tf_db_path=tmp_db)
        re = ledger["summary"]["risk_exposure"]
        # open: 1500, tracking: 1000 → invested = 2500, remaining = 2500
        assert re["invested"] == 2500.0
        assert re["remaining"] == 2500.0


# ── Config override ─────────────────────────────────────────────────────

class TestConfigOverride:

    def test_per_ticket_max_override_caps_amount(self, tmp_db):
        update_paper_ledger_config(
            {"risk_budget": {"per_ticket_max": 800}}, tf_db_path=tmp_db,
        )
        result = _add("601689.SH", tmp_db, planned_amount=2000)
        assert result["planned_amount"] == 800

    def test_require_invalid_price_can_be_disabled(self, tmp_db):
        update_paper_ledger_config(
            {"risk_budget": {"require_invalid_price": False}}, tf_db_path=tmp_db,
        )
        result = add_paper_candidate(
            symbol="601689.SH", name="拓普集团", trade_date=TODAY,
            trigger_price=30.0, planned_amount=1000, tf_db_path=tmp_db,
        )
        assert result["status"] == "ok"

    def test_override_is_deep_merged(self, tmp_db):
        update_paper_ledger_config(
            {"risk_budget": {"daily_new_max": 8}}, tf_db_path=tmp_db,
        )
        ledger = get_paper_ledger(tf_db_path=tmp_db)
        rb = ledger["config"]["risk_budget"]
        assert rb["daily_new_max"] == 8
        # Other defaults preserved
        assert rb["per_ticket_max"] == 1500
        assert rb["max_concurrent_tracking"] == 5

    def test_legacy_max_per_candidate_honored(self, tmp_db):
        # Legacy top-level config key still caps amount when no per_ticket_max override.
        update_paper_ledger_config({"max_per_candidate": 1200}, tf_db_path=tmp_db)
        # Clear risk_budget.per_ticket_max to fall back to legacy.
        import sqlite3
        import json as _json
        conn = sqlite3.connect(tmp_db)
        row = conn.execute("SELECT config_json FROM tradeflow_paper_ledger LIMIT 1").fetchone()
        cfg = _json.loads(row[0])
        cfg["risk_budget"].pop("per_ticket_max", None)
        conn.execute(
            "UPDATE tradeflow_paper_ledger SET config_json = ?",
            (_json.dumps(cfg),),
        )
        conn.commit()
        conn.close()
        result = _add("601689.SH", tmp_db, planned_amount=9999)
        assert result["planned_amount"] == 1200


# ── Schema additions ────────────────────────────────────────────────────

class TestRiskBudgetSchemas:

    def test_summary_has_risk_exposure_field(self):
        s = PaperLedgerSummary()
        assert s.risk_exposure == {}
        assert s.observation_count == 0

    def test_action_response_has_reject_fields(self):
        r = PaperActionResponse(
            status="rejected", rejected=True,
            rule="require_invalid_price", reason="x",
        )
        assert r.rejected is True
        assert r.rule == "require_invalid_price"

    def test_action_response_has_downgrade_field(self):
        r = PaperActionResponse(status="ok", downgraded_to="observation")
        assert r.downgraded_to == "observation"
