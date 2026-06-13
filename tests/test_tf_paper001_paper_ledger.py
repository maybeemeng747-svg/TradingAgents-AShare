# [TF-PAPER-001] paper_trading_ledger
"""Tests for TF-PAPER-001: 5000 元试跑模拟账户与候选跟踪账本.

Covers:
- DB table creation in init_db.
- Default principal 5000 and auto-creation of ledger row.
- Add candidate to paper tracking (dedup, max amount cap).
- Remove candidate (blocked if open position).
- Confirm buy (insufficient cash guard, status transitions).
- Confirm sell (P&L calculation, cash balance update).
- Observe state sync (TRIGGERED → pending, INVALIDATED → invalidated).
- Post-market review aggregation (realized P&L, false triggers, etc).
- Pydantic schema validation.
- Runtime tier classification (FAST_RADAR).
- No-data scenarios (DB unavailable).
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
    get_paper_ledger,
    add_paper_candidate,
    remove_paper_candidate,
    confirm_paper_action,
    update_paper_observe_state,
    get_paper_review,
)
from api.tradeflow_schemas import (
    PaperLedgerResponse,
    PaperTradeItem,
    PaperLedgerSummary,
    PaperActionRequest,
    PaperAddCandidateRequest,
    PaperActionResponse,
    PaperReviewResponse,
)
from api.runtime_tier import tradeflow_endpoint_tier, RuntimeTier


TODAY = datetime.now().strftime("%Y-%m-%d")


# ── Fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_db(tmp_path):
    db_path = str(tmp_path / "test_paper_ledger.db")
    init_db(db_path)
    return db_path


@pytest.fixture
def db_with_candidates(tmp_db):
    """Create a temp DB and add two candidates to paper tracking."""
    add_paper_candidate(
        symbol="601689.SH",
        name="拓普集团",
        trade_date=TODAY,
        trigger_price=30.0,
        invalid_price=28.0,
        planned_amount=1000,
        candidate_type="POLICY_AMBUSH",
        tf_db_path=tmp_db,
    )
    add_paper_candidate(
        symbol="002353.SZ",
        name="杰瑞股份",
        trade_date=TODAY,
        trigger_price=35.0,
        invalid_price=33.0,
        planned_amount=1000,
        candidate_type="TECH_TRADE",
        tf_db_path=tmp_db,
    )
    return tmp_db


# ── DB Schema Tests ──────────────────────────────────────────────────────

class TestPaperLedgerDBSchema:
    """Verify DB tables are created by init_db."""

    def test_paper_ledger_table_exists(self, tmp_db):
        conn = sqlite3.connect(tmp_db)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        conn.close()
        assert "tradeflow_paper_ledger" in tables

    def test_paper_trades_table_exists(self, tmp_db):
        conn = sqlite3.connect(tmp_db)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        conn.close()
        assert "tradeflow_paper_trades" in tables

    def test_paper_trades_columns(self, tmp_db):
        conn = sqlite3.connect(tmp_db)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(tradeflow_paper_trades)").fetchall()}
        conn.close()
        expected = {
            "id", "symbol", "name", "trade_date", "plan_date",
            "candidate_type", "trigger_price", "invalid_price",
            "planned_amount", "status", "action_type", "action_price",
            "action_date", "confirmed", "note", "pnl", "pnl_pct",
            "observe_state", "close_price", "close_date", "close_reason",
            "created_at", "updated_at",
        }
        assert expected.issubset(cols), f"Missing columns: {expected - cols}"

    def test_paper_ledger_columns(self, tmp_db):
        conn = sqlite3.connect(tmp_db)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(tradeflow_paper_ledger)").fetchall()}
        conn.close()
        expected = {"id", "principal", "cash_balance", "config_json", "created_at", "updated_at"}
        assert expected.issubset(cols)


# ── Ledger Init / Get Tests ─────────────────────────────────────────────

class TestPaperLedgerInit:

    def test_default_principal_5000(self, tmp_db):
        result = get_paper_ledger(tf_db_path=tmp_db)
        assert result["status"] == "ok"
        assert result["principal"] == 5000.0
        assert result["cash_balance"] == 5000.0

    def test_ledger_auto_created_on_first_access(self, tmp_db):
        conn = sqlite3.connect(tmp_db)
        count_before = conn.execute("SELECT COUNT(*) FROM tradeflow_paper_ledger").fetchone()[0]
        conn.close()
        assert count_before == 0
        get_paper_ledger(tf_db_path=tmp_db)
        conn = sqlite3.connect(tmp_db)
        count_after = conn.execute("SELECT COUNT(*) FROM tradeflow_paper_ledger").fetchone()[0]
        conn.close()
        assert count_after == 1

    def test_ledger_only_one_row(self, tmp_db):
        get_paper_ledger(tf_db_path=tmp_db)
        get_paper_ledger(tf_db_path=tmp_db)
        conn = sqlite3.connect(tmp_db)
        count = conn.execute("SELECT COUNT(*) FROM tradeflow_paper_ledger").fetchone()[0]
        conn.close()
        assert count == 1

    def test_empty_trades_initially(self, tmp_db):
        result = get_paper_ledger(tf_db_path=tmp_db)
        assert result["trades"] == []
        assert result["summary"]["total_trades"] == 0

    def test_no_data_when_db_missing(self):
        result = get_paper_ledger(tf_db_path="/nonexistent/path/none.db")
        assert result["status"] == "no_data"
        assert result["principal"] == 5000.0


# ── Add Candidate Tests ─────────────────────────────────────────────────

class TestAddPaperCandidate:

    def test_add_candidate_basic(self, tmp_db):
        result = add_paper_candidate(
            symbol="601689.SH",
            name="拓普集团",
            trade_date=TODAY,
            trigger_price=30.0,
            invalid_price=28.0,
            planned_amount=1000,
            tf_db_path=tmp_db,
        )
        assert result["status"] == "ok"
        assert "trade_id" in result
        assert result["planned_amount"] == 1000

    def test_add_candidate_default_amount(self, tmp_db):
        result = add_paper_candidate(
            symbol="002353.SZ",
            name="杰瑞股份",
            trade_date=TODAY,
            trigger_price=35.0,
            tf_db_path=tmp_db,
        )
        assert result["status"] == "ok"
        assert result["planned_amount"] == 1000  # default

    def test_add_duplicate_blocked(self, tmp_db):
        add_paper_candidate(
            symbol="601689.SH", name="拓普集团", trade_date=TODAY,
            trigger_price=30.0, tf_db_path=tmp_db,
        )
        result = add_paper_candidate(
            symbol="601689.SH", name="拓普集团", trade_date=TODAY,
            trigger_price=31.0, tf_db_path=tmp_db,
        )
        assert result["status"] == "duplicate"

    def test_add_two_candidates(self, db_with_candidates):
        result = get_paper_ledger(tf_db_path=db_with_candidates)
        assert result["summary"]["total_trades"] == 2
        assert result["summary"]["tracking_count"] == 2

    def test_candidate_type_stored(self, db_with_candidates):
        result = get_paper_ledger(tf_db_path=db_with_candidates)
        types = {t["candidate_type"] for t in result["trades"]}
        assert "POLICY_AMBUSH" in types
        assert "TECH_TRADE" in types

    def test_trigger_and_invalidation_price_stored(self, db_with_candidates):
        result = get_paper_ledger(tf_db_path=db_with_candidates)
        trade = next(t for t in result["trades"] if t["symbol"] == "601689.SH")
        assert trade["trigger_price"] == 30.0
        assert trade["invalid_price"] == 28.0


# ── Remove Candidate Tests ──────────────────────────────────────────────

class TestRemovePaperCandidate:

    def test_remove_tracking_candidate(self, db_with_candidates):
        ledger = get_paper_ledger(tf_db_path=db_with_candidates)
        trade_id = ledger["trades"][0]["id"]
        result = remove_paper_candidate(trade_id, tf_db_path=db_with_candidates)
        assert result["status"] == "ok"
        ledger2 = get_paper_ledger(tf_db_path=db_with_candidates)
        assert ledger2["summary"]["total_trades"] == 1

    def test_remove_nonexistent(self, tmp_db):
        result = remove_paper_candidate(99999, tf_db_path=tmp_db)
        assert result["status"] == "not_found"

    def test_remove_open_position_blocked(self, db_with_candidates):
        ledger = get_paper_ledger(tf_db_path=db_with_candidates)
        trade_id = ledger["trades"][0]["id"]
        confirm_paper_action(trade_id, "buy", 30.5, tf_db_path=db_with_candidates)
        result = remove_paper_candidate(trade_id, tf_db_path=db_with_candidates)
        assert result["status"] == "blocked"


# ── Confirm Action (Buy) Tests ──────────────────────────────────────────

class TestConfirmPaperBuy:

    def test_buy_transitions_to_open(self, db_with_candidates):
        ledger = get_paper_ledger(tf_db_path=db_with_candidates)
        trade_id = ledger["trades"][0]["id"]
        result = confirm_paper_action(trade_id, "buy", 30.5, tf_db_path=db_with_candidates)
        assert result["status"] == "ok"
        ledger2 = get_paper_ledger(tf_db_path=db_with_candidates)
        trade = next(t for t in ledger2["trades"] if t["id"] == trade_id)
        assert trade["status"] == "open"
        assert trade["action_type"] == "buy"
        assert trade["action_price"] == 30.5
        assert trade["confirmed"] is True

    def test_buy_deducts_cash(self, db_with_candidates):
        ledger = get_paper_ledger(tf_db_path=db_with_candidates)
        trade_id = ledger["trades"][0]["id"]
        confirm_paper_action(trade_id, "buy", 30.5, tf_db_path=db_with_candidates)
        ledger2 = get_paper_ledger(tf_db_path=db_with_candidates)
        assert ledger2["cash_balance"] == pytest.approx(4000.0, abs=0.01)

    def test_buy_invalid_action_type(self, db_with_candidates):
        ledger = get_paper_ledger(tf_db_path=db_with_candidates)
        trade_id = ledger["trades"][0]["id"]
        result = confirm_paper_action(trade_id, "hold", 30.0, tf_db_path=db_with_candidates)
        assert result["status"] == "error"

    def test_buy_blocked_if_already_open(self, db_with_candidates):
        ledger = get_paper_ledger(tf_db_path=db_with_candidates)
        trade_id = ledger["trades"][0]["id"]
        confirm_paper_action(trade_id, "buy", 30.5, tf_db_path=db_with_candidates)
        result = confirm_paper_action(trade_id, "buy", 31.0, tf_db_path=db_with_candidates)
        assert result["status"] == "blocked"

    def test_buy_blocked_if_insufficient_cash(self, tmp_db):
        """Add 3 candidates × 2000 each, buy 2 (4000), 3rd would exceed remaining 1000."""
        for i in range(3):
            add_paper_candidate(
                symbol=f"00000{i}.SZ", name=f"Test {i}", trade_date=TODAY,
                planned_amount=2000, trigger_price=10.0, tf_db_path=tmp_db,
            )
        ledger = get_paper_ledger(tf_db_path=tmp_db)
        # Buy first two (deduct 4000, leaving 1000)
        confirm_paper_action(ledger["trades"][2]["id"], "buy", 10.0, tf_db_path=tmp_db)
        confirm_paper_action(ledger["trades"][1]["id"], "buy", 10.0, tf_db_path=tmp_db)
        # Third one: planned 2000 > remaining 1000
        result = confirm_paper_action(ledger["trades"][0]["id"], "buy", 10.0, tf_db_path=tmp_db)
        assert result["status"] == "insufficient_cash"


# ── Confirm Action (Sell) Tests ─────────────────────────────────────────

class TestConfirmPaperSell:

    def test_sell_transitions_to_closed(self, db_with_candidates):
        ledger = get_paper_ledger(tf_db_path=db_with_candidates)
        trade_id = ledger["trades"][0]["id"]
        confirm_paper_action(trade_id, "buy", 30.0, tf_db_path=db_with_candidates)
        result = confirm_paper_action(trade_id, "sell", 33.0, tf_db_path=db_with_candidates)
        assert result["status"] == "ok"
        ledger2 = get_paper_ledger(tf_db_path=db_with_candidates)
        trade = next(t for t in ledger2["trades"] if t["id"] == trade_id)
        assert trade["status"] == "closed"
        assert trade["action_type"] == "sell"

    def test_sell_calculates_pnl_positive(self, db_with_candidates):
        ledger = get_paper_ledger(tf_db_path=db_with_candidates)
        trade_id = ledger["trades"][0]["id"]
        confirm_paper_action(trade_id, "buy", 30.0, tf_db_path=db_with_candidates)
        confirm_paper_action(trade_id, "sell", 33.0, tf_db_path=db_with_candidates)
        ledger2 = get_paper_ledger(tf_db_path=db_with_candidates)
        trade = next(t for t in ledger2["trades"] if t["id"] == trade_id)
        # P&L = (33-30) * (1000/30) ≈ 100
        assert trade["pnl"] > 0
        assert trade["pnl_pct"] == pytest.approx(10.0, abs=0.1)

    def test_sell_calculates_pnl_negative(self, db_with_candidates):
        ledger = get_paper_ledger(tf_db_path=db_with_candidates)
        trade_id = ledger["trades"][0]["id"]
        confirm_paper_action(trade_id, "buy", 30.0, tf_db_path=db_with_candidates)
        confirm_paper_action(trade_id, "sell", 27.0, tf_db_path=db_with_candidates)
        ledger2 = get_paper_ledger(tf_db_path=db_with_candidates)
        trade = next(t for t in ledger2["trades"] if t["id"] == trade_id)
        assert trade["pnl"] < 0
        assert trade["pnl_pct"] == pytest.approx(-10.0, abs=0.1)

    def test_sell_adds_cash_back(self, db_with_candidates):
        ledger = get_paper_ledger(tf_db_path=db_with_candidates)
        trade_id = ledger["trades"][0]["id"]
        confirm_paper_action(trade_id, "buy", 30.0, tf_db_path=db_with_candidates)
        confirm_paper_action(trade_id, "sell", 33.0, tf_db_path=db_with_candidates)
        ledger2 = get_paper_ledger(tf_db_path=db_with_candidates)
        # After buy (1000 deducted), sell (1000 + pnl returned)
        # Started 5000, should be 5000 + pnl
        assert ledger2["cash_balance"] > 4900  # recovered most
        assert ledger2["summary"]["realized_pnl"] > 0

    def test_sell_blocked_if_not_open(self, db_with_candidates):
        ledger = get_paper_ledger(tf_db_path=db_with_candidates)
        trade_id = ledger["trades"][0]["id"]
        result = confirm_paper_action(trade_id, "sell", 33.0, tf_db_path=db_with_candidates)
        assert result["status"] == "blocked"


# ── Observe State Sync Tests ────────────────────────────────────────────

class TestPaperObserveStateSync:

    def test_triggered_transitions_to_pending(self, db_with_candidates):
        update_paper_observe_state("601689.SH", "TRIGGERED", tf_db_path=db_with_candidates)
        ledger = get_paper_ledger(tf_db_path=db_with_candidates)
        trade = next(t for t in ledger["trades"] if t["symbol"] == "601689.SH")
        assert trade["status"] == "pending"
        assert trade["observe_state"] == "TRIGGERED"

    def test_invalidated_transitions_status(self, db_with_candidates):
        update_paper_observe_state("601689.SH", "INVALIDATED", tf_db_path=db_with_candidates)
        ledger = get_paper_ledger(tf_db_path=db_with_candidates)
        trade = next(t for t in ledger["trades"] if t["symbol"] == "601689.SH")
        assert trade["status"] == "invalidated"
        assert trade["observe_state"] == "INVALIDATED"

    def test_waiting_keeps_tracking(self, db_with_candidates):
        update_paper_observe_state("601689.SH", "WAITING", tf_db_path=db_with_candidates)
        ledger = get_paper_ledger(tf_db_path=db_with_candidates)
        trade = next(t for t in ledger["trades"] if t["symbol"] == "601689.SH")
        assert trade["status"] == "tracking"
        assert trade["observe_state"] == "WAITING"

    def test_update_nonexistent_symbol_no_crash(self, db_with_candidates):
        result = update_paper_observe_state("999999.SH", "TRIGGERED", tf_db_path=db_with_candidates)
        assert result["status"] == "ok"
        assert result["updated"] == 0


# ── Post-Market Review Tests ────────────────────────────────────────────

class TestPaperReview:

    def test_review_empty_ledger(self, tmp_db):
        result = get_paper_review(TODAY, tf_db_path=tmp_db)
        assert result["status"] == "ok"
        assert result["review"]["total"] == 0
        assert "暂无" in result["review"]["review_note"]

    def test_review_all_tracking(self, db_with_candidates):
        result = get_paper_review(TODAY, tf_db_path=db_with_candidates)
        assert result["status"] == "ok"
        assert result["review"]["untriggered_count"] == 2
        assert "等待触发" in result["review"]["review_note"]

    def test_review_with_closed_trade(self, db_with_candidates):
        ledger = get_paper_ledger(tf_db_path=db_with_candidates)
        trade_id = ledger["trades"][0]["id"]
        confirm_paper_action(trade_id, "buy", 30.0, tf_db_path=db_with_candidates)
        confirm_paper_action(trade_id, "sell", 27.0, tf_db_path=db_with_candidates)
        result = get_paper_review(TODAY, tf_db_path=db_with_candidates)
        assert result["review"]["closed"] == 1
        assert result["review"]["false_trigger_count"] == 1
        assert result["review"]["realized_pnl"] < 0

    def test_review_with_open_position(self, db_with_candidates):
        ledger = get_paper_ledger(tf_db_path=db_with_candidates)
        trade_id = ledger["trades"][0]["id"]
        confirm_paper_action(trade_id, "buy", 30.0, tf_db_path=db_with_candidates)
        result = get_paper_review(TODAY, tf_db_path=db_with_candidates)
        assert result["review"]["open"] == 1
        assert result["review"]["untriggered_count"] == 1

    def test_review_with_invalidated(self, db_with_candidates):
        update_paper_observe_state("601689.SH", "INVALIDATED", tf_db_path=db_with_candidates)
        result = get_paper_review(TODAY, tf_db_path=db_with_candidates)
        assert result["review"]["invalidated_count"] == 1
        assert result["review"]["untriggered_count"] == 1

    def test_review_no_data_when_db_missing(self):
        result = get_paper_review(TODAY, tf_db_path="/nonexistent/path.db")
        assert result["status"] == "no_data"
        assert result["review"]["total"] == 0

    def test_review_principal_and_pnl_pct(self, db_with_candidates):
        result = get_paper_review(TODAY, tf_db_path=db_with_candidates)
        assert result["review"]["principal"] == 5000.0
        assert result["review"]["total_pnl_pct"] == 0.0


# ── Pydantic Schema Tests ───────────────────────────────────────────────

class TestPaperLedgerSchemas:

    def test_paper_trade_item_defaults(self):
        item = PaperTradeItem()
        assert item.symbol == ""
        assert item.status == "tracking"
        assert item.planned_amount == 0.0
        assert item.confirmed is False

    def test_paper_ledger_summary_defaults(self):
        s = PaperLedgerSummary()
        assert s.total_trades == 0
        assert s.tracking_count == 0
        assert s.realized_pnl == 0.0

    def test_paper_ledger_response_defaults(self):
        r = PaperLedgerResponse()
        assert r.principal == 5000.0
        assert r.cash_balance == 5000.0
        assert r.trades == []

    def test_paper_action_request_validation(self):
        req = PaperActionRequest(trade_id=1, action_type="buy", price=30.0)
        assert req.trade_id == 1
        assert req.action_type == "buy"

    def test_paper_add_candidate_request(self):
        req = PaperAddCandidateRequest(symbol="601689.SH", trade_date="2026-06-14")
        assert req.symbol == "601689.SH"
        assert req.planned_amount == 0.0

    def test_paper_action_response(self):
        r = PaperActionResponse(status="ok", message="success")
        assert r.status == "ok"

    def test_paper_review_response(self):
        r = PaperReviewResponse()
        assert r.status == "ok"
        assert r.trade_date == ""

    def test_ledger_response_from_service_dict(self, db_with_candidates):
        data = get_paper_ledger(tf_db_path=db_with_candidates)
        model = PaperLedgerResponse(**data)
        assert model.principal == 5000.0
        assert len(model.trades) == 2
        assert model.summary.total_trades == 2


# ── Runtime Tier Tests ──────────────────────────────────────────────────

class TestPaperLedgerRuntimeTier:

    def test_paper_ledger_is_fast_radar(self):
        tier = tradeflow_endpoint_tier("tradeflow_paper_ledger")
        assert tier == RuntimeTier.FAST_RADAR

    def test_paper_review_is_fast_radar(self):
        tier = tradeflow_endpoint_tier("tradeflow_paper_review")
        assert tier == RuntimeTier.FAST_RADAR

    def test_no_llm_allowed(self):
        from api.runtime_tier import tradeflow_meta
        meta = tradeflow_meta("tradeflow_paper_ledger")
        assert meta["llm_allowed"] is False
        assert meta["requires_confirmation"] is False


# ── Full Workflow / E2E Tests ───────────────────────────────────────────

class TestPaperLedgerE2E:

    def test_full_workflow_buy_sell_close(self, tmp_db):
        """Simulate: add → buy → sell → review."""
        # 1. Add candidate
        add_paper_candidate(
            symbol="601689.SH", name="拓普集团", trade_date=TODAY,
            trigger_price=30.0, invalid_price=28.0,
            planned_amount=1000, tf_db_path=tmp_db,
        )
        ledger = get_paper_ledger(tf_db_path=tmp_db)
        assert ledger["summary"]["tracking_count"] == 1

        # 2. Observe trigger → pending
        update_paper_observe_state("601689.SH", "TRIGGERED", tf_db_path=tmp_db)
        ledger = get_paper_ledger(tf_db_path=tmp_db)
        assert ledger["summary"]["pending_count"] == 1

        # 3. Confirm buy
        trade_id = ledger["trades"][0]["id"]
        confirm_paper_action(trade_id, "buy", 30.0, tf_db_path=tmp_db)
        ledger = get_paper_ledger(tf_db_path=tmp_db)
        assert ledger["summary"]["open_count"] == 1
        assert ledger["cash_balance"] == pytest.approx(4000.0, abs=0.01)

        # 4. Confirm sell
        confirm_paper_action(trade_id, "sell", 33.0, tf_db_path=tmp_db)
        ledger = get_paper_ledger(tf_db_path=tmp_db)
        assert ledger["summary"]["closed_count"] == 1
        assert ledger["summary"]["realized_pnl"] > 0
        assert ledger["cash_balance"] > 5000.0  # Profit

        # 5. Review
        review = get_paper_review(TODAY, tf_db_path=tmp_db)
        assert review["review"]["closed"] == 1
        assert review["review"]["false_trigger_count"] == 0  # profit, not false

    def test_full_workflow_invalidation(self, tmp_db):
        """Simulate: add → observe invalidated → review."""
        add_paper_candidate(
            symbol="002353.SZ", name="杰瑞股份", trade_date=TODAY,
            trigger_price=35.0, invalid_price=33.0,
            planned_amount=1000, tf_db_path=tmp_db,
        )
        update_paper_observe_state("002353.SZ", "INVALIDATED", tf_db_path=tmp_db)
        review = get_paper_review(TODAY, tf_db_path=tmp_db)
        assert review["review"]["invalidated_count"] == 1
        assert review["review"]["untriggered_count"] == 0

    def test_full_workflow_untriggered(self, tmp_db):
        """Simulate: add → no trigger → review shows untriggered."""
        add_paper_candidate(
            symbol="601689.SH", name="拓普集团", trade_date=TODAY,
            trigger_price=30.0, invalid_price=28.0,
            planned_amount=1000, tf_db_path=tmp_db,
        )
        review = get_paper_review(TODAY, tf_db_path=tmp_db)
        assert review["review"]["untriggered_count"] == 1
        assert "等待触发" in review["review"]["review_note"]

    def test_no_auto_trade_without_confirmation(self, tmp_db):
        """Verify that adding a candidate does NOT auto-create a buy."""
        add_paper_candidate(
            symbol="601689.SH", name="拓普集团", trade_date=TODAY,
            trigger_price=30.0, planned_amount=1000, tf_db_path=tmp_db,
        )
        ledger = get_paper_ledger(tf_db_path=tmp_db)
        trade = ledger["trades"][0]
        assert trade["status"] == "tracking"
        assert trade["confirmed"] is False
        assert trade["action_price"] is None
        assert ledger["cash_balance"] == 5000.0  # No deduction


# ── Constraint / Safety Tests ───────────────────────────────────────────

class TestPaperLedgerSafety:

    def test_no_strong_action_output(self, tmp_db):
        """Adding a candidate should NOT produce 'immediate buy' or 'clear' action."""
        result = add_paper_candidate(
            symbol="601689.SH", name="拓普集团", trade_date=TODAY,
            trigger_price=30.0, tf_db_path=tmp_db,
        )
        assert "status" in result
        assert "trade_id" in result
        # Message should be informational, not prescriptive
        assert "加入" in result["message"] or "已在" in result["message"]

    def test_max_amount_cap_not_exceeding_balance(self, tmp_db):
        """Planned amount should not exceed reasonable defaults."""
        add_paper_candidate(
            symbol="601689.SH", name="拓普集团", trade_date=TODAY,
            planned_amount=99999, trigger_price=30.0, tf_db_path=tmp_db,
        )
        ledger = get_paper_ledger(tf_db_path=tmp_db)
        trade = ledger["trades"][0]
        # Should be capped at max_per_candidate (2000 by default)
        assert trade["planned_amount"] <= 2000

    def test_multiple_candidates_cash_management(self, tmp_db):
        """Add 5 candidates × 1000 each, verify cash stays >= 0."""
        for i in range(5):
            add_paper_candidate(
                symbol=f"00000{i}.SZ", name=f"Test {i}", trade_date=TODAY,
                planned_amount=1000, trigger_price=10.0, tf_db_path=tmp_db,
            )
        ledger = get_paper_ledger(tf_db_path=tmp_db)
        assert ledger["summary"]["tracking_count"] == 5
        # Buy 3 of them
        for t in ledger["trades"][:3]:
            confirm_paper_action(t["id"], "buy", 10.0, tf_db_path=tmp_db)
        ledger2 = get_paper_ledger(tf_db_path=tmp_db)
        assert ledger2["cash_balance"] == pytest.approx(2000.0, abs=0.01)
        assert ledger2["summary"]["open_count"] == 3
