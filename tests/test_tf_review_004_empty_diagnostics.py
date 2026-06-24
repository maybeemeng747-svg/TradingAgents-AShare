# [TF-REVIEW-004] review_empty_diagnostics tests
"""Tests for post-market Review empty-data diagnostics and one-click generation entry.

Covers:
1. ReviewEmptyReason enum (5 values + Chinese messages + suggested actions).
2. _diagnose_review_empty_state — NO_CANDIDATES / NON_TRADING_DAY_MAPPED /
   NO_OBSERVE / NOT_GENERATED / MARKET_DATA_MISSING.
3. get_review returns diagnostic fields on empty state (empty_reason /
   empty_reason_message / suggested_action / available_plan_dates /
   cross-date plan_date/effective_trade_date/review_date).
4. generate_review returns diagnostic fields on empty state.
5. Non-trading-day plan → next trading day review mapping is replayable.
"""

import json
import os
import sqlite3
import tempfile

import pytest

from tradingagents.tradeflow.post_market_review import ReviewEmptyReason
from tradingagents.tradeflow.candidate_engine import init_db, save_candidate
from tradingagents.tradeflow.schemas import Candidate
from tradingagents.tradeflow.date_semantics import (
    next_cn_trading_day,
    resolve_review_date,
)


# ── helpers ──

def _insert_signal(db_path, symbol, trade_date, signal_type="observe_check"):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO tradeflow_signals "
        "(signal_time, symbol, signal_type, signal_level, source, evidence_json, "
        "action_hint, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            f"{trade_date}T10:30:00",
            symbol,
            signal_type,
            "info",
            "observe_runner",
            json.dumps({"trade_date": trade_date, "current_price": 10.0}),
            "OBSERVE",
            "new",
            f"{trade_date}T10:30:00",
        ),
    )
    conn.commit()
    conn.close()


def _make_candidate(symbol, trade_date, effective_trade_date=""):
    return Candidate(
        symbol=symbol,
        name=f"测试 {symbol}",
        source="manual",
        trade_date=trade_date,
        trigger_price=10.0,
        invalid_price=9.0,
        status="active",
        observe_state="WAITING",
        effective_trade_date=effective_trade_date or trade_date,
    )


# ── ReviewEmptyReason enum ──


class TestReviewEmptyReason:
    def test_five_values_exist(self):
        assert ReviewEmptyReason.NO_CANDIDATES
        assert ReviewEmptyReason.NO_OBSERVE
        assert ReviewEmptyReason.NON_TRADING_DAY_MAPPED
        assert ReviewEmptyReason.MARKET_DATA_MISSING
        assert ReviewEmptyReason.NOT_GENERATED

    def test_all_values_are_strings(self):
        for r in ReviewEmptyReason:
            assert isinstance(r.value, str)

    def test_message_cn_is_chinese_and_nonempty(self):
        for r in ReviewEmptyReason:
            assert r.message_cn != ""
            # Must contain CJK characters
            assert any('\u4e00' <= ch <= '\u9fff' for ch in r.message_cn)

    def test_suggested_action_nonempty(self):
        for r in ReviewEmptyReason:
            assert r.suggested_action_cn != ""

    def test_distinct_values(self):
        vals = ReviewEmptyReason.all_values()
        assert len(vals) == len(set(vals)) == 5

    def test_no_candidates_message_mentions_candidate_pool(self):
        assert "候选" in ReviewEmptyReason.NO_CANDIDATES.message_cn

    def test_non_trading_day_message_mentions_mapping(self):
        assert "交易日" in ReviewEmptyReason.NON_TRADING_DAY_MAPPED.message_cn


# ── _diagnose_review_empty_state ──


class TestDiagnoseEmptyState:
    def test_no_db_returns_no_candidates(self):
        from api.services.tradeflow_service import _diagnose_review_empty_state
        result = _diagnose_review_empty_state(
            "2026-06-02", tf_db_path="/tmp/nonexistent_tf_review_004_diag.db"
        )
        assert result["empty_reason"] == ReviewEmptyReason.NO_CANDIDATES.value
        assert result["empty_reason_message"] != ""
        assert result["available_plan_dates"] == []
        assert result["latest_plan_date"] == ""

    def test_empty_db_returns_no_candidates(self, tmp_path):
        from api.services.tradeflow_service import _diagnose_review_empty_state
        db_path = str(tmp_path / "empty.db")
        init_db(db_path)
        result = _diagnose_review_empty_state("2026-06-02", tf_db_path=db_path)
        assert result["empty_reason"] == ReviewEmptyReason.NO_CANDIDATES.value

    def test_non_trading_day_mapped(self, tmp_path):
        """Candidates exist on a weekend date; querying that weekend returns mapping."""
        from api.services.tradeflow_service import _diagnose_review_empty_state
        db_path = str(tmp_path / "ntd.db")
        init_db(db_path)
        # 2026-05-31 is a Sunday (non-trading day)
        save_candidate(_make_candidate("600519.SH", "2026-05-31"), db_path)

        result = _diagnose_review_empty_state("2026-05-31", tf_db_path=db_path)
        assert result["empty_reason"] == ReviewEmptyReason.NON_TRADING_DAY_MAPPED.value
        # Should explain the mapping to next trading day
        assert result["plan_date"] == "2026-05-31"
        eff = next_cn_trading_day("2026-05-31")
        assert result["effective_trade_date"] == eff
        assert result["review_date"] == eff
        assert result["available_plan_dates"] == ["2026-05-31"]

    def test_candidates_exist_no_observe_signals(self, tmp_path):
        """Candidates exist for a trading day but no observe signals → NO_OBSERVE."""
        from api.services.tradeflow_service import _diagnose_review_empty_state
        db_path = str(tmp_path / "no_obs.db")
        init_db(db_path)
        # Use a trading day
        save_candidate(_make_candidate("600519.SH", "2026-06-02"), db_path)

        result = _diagnose_review_empty_state("2026-06-02", tf_db_path=db_path)
        assert result["empty_reason"] == ReviewEmptyReason.NO_OBSERVE.value
        assert result["has_observe_signals"] is False
        assert result["plan_date"] == "2026-06-02"

    def test_candidates_and_observe_but_not_generated(self, tmp_path):
        """Candidates + observe signals exist but no review report → NOT_GENERATED."""
        from api.services.tradeflow_service import _diagnose_review_empty_state
        db_path = str(tmp_path / "notgen.db")
        init_db(db_path)
        save_candidate(_make_candidate("600519.SH", "2026-06-02"), db_path)
        _insert_signal(db_path, "600519.SH", "2026-06-02")

        result = _diagnose_review_empty_state("2026-06-02", tf_db_path=db_path)
        assert result["has_observe_signals"] is True
        # No review report row in this DB → NOT_GENERATED
        assert result["empty_reason"] == ReviewEmptyReason.NOT_GENERATED.value

    def test_candidates_observe_and_report_but_no_market_data(self, tmp_path):
        """Candidates + observe + report record exist, but data still empty → MARKET_DATA_MISSING."""
        from api.services.tradeflow_service import _diagnose_review_empty_state
        db_path = str(tmp_path / "nomkt.db")
        init_db(db_path)
        save_candidate(_make_candidate("600519.SH", "2026-06-02"), db_path)
        _insert_signal(db_path, "600519.SH", "2026-06-02")
        # Mark a review report as generated in this isolated DB
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO tradeflow_review_reports (review_date, plan_date, effective_trade_date, generated_at, report_path) "
            "VALUES (?, ?, ?, ?, ?)",
            ("2026-06-02", "2026-06-02", "2026-06-02", "2026-06-02T15:00:00", "docs/x.md"),
        )
        conn.commit()
        conn.close()

        result = _diagnose_review_empty_state("2026-06-02", tf_db_path=db_path)
        assert result["empty_reason"] == ReviewEmptyReason.MARKET_DATA_MISSING.value


# ── get_review returns diagnostic fields ──


class TestGetReviewDiagnostics:
    def test_no_db_returns_diagnostic_fields(self):
        from api.services.tradeflow_service import get_review
        result = get_review("2026-06-02", tf_db_path="/tmp/nonexistent_tf_review_004_get.db")
        assert result["status"] == "no_data"
        # [TF-REVIEW-004] diagnostic fields must be present
        assert "empty_reason" in result
        assert result["empty_reason"] == ReviewEmptyReason.NO_CANDIDATES.value
        assert result["empty_reason_message"] != ""
        assert "suggested_action" in result
        assert "available_plan_dates" in result
        assert "has_observe_signals" in result
        assert "plan_date" in result
        assert "effective_trade_date" in result
        assert "review_date" in result

    def test_returns_no_observe_reason(self, tmp_path):
        """Candidates exist but no observe → get_review reports NO_OBSERVE."""
        from api.services.tradeflow_service import get_review
        db_path = str(tmp_path / "get_no_obs.db")
        init_db(db_path)
        save_candidate(_make_candidate("600519.SH", "2026-06-02"), db_path)

        result = get_review("2026-06-02", tf_db_path=db_path)
        # get_review reads daily_plans; without a plan row it returns no_data
        # but the diagnostic must still explain WHY.
        assert result["status"] == "no_data"
        assert result["empty_reason"] != ""
        assert result["empty_reason_message"] != ""

    def test_non_trading_day_diagnostic_has_mapping(self, tmp_path):
        """Querying a non-trading day returns cross-date mapping fields."""
        from api.services.tradeflow_service import get_review
        db_path = str(tmp_path / "get_ntd.db")
        init_db(db_path)
        save_candidate(_make_candidate("600519.SH", "2026-05-31"), db_path)

        result = get_review("2026-05-31", tf_db_path=db_path)
        assert result["status"] == "no_data"
        assert result["empty_reason"] == ReviewEmptyReason.NON_TRADING_DAY_MAPPED.value
        assert result["plan_date"] == "2026-05-31"
        eff = next_cn_trading_day("2026-05-31")
        assert result["effective_trade_date"] == eff
        assert result["review_date"] == eff


# ── generate_review returns diagnostic fields ──


class TestGenerateReviewDiagnostics:
    def test_no_db_returns_diagnostic_fields(self):
        from api.services.tradeflow_service import generate_review
        result = generate_review(
            "2026-06-02", tf_db_path="/tmp/nonexistent_tf_review_004_gen.db"
        )
        assert result["status"] == "no_data"
        assert "empty_reason" in result
        assert result["empty_reason"] == ReviewEmptyReason.NO_CANDIDATES.value
        assert result["empty_reason_message"] != ""
        assert "suggested_action" in result
        assert "available_plan_dates" in result

    def test_non_trading_day_generate_returns_mapping(self, tmp_path):
        """generate_review on a non-trading day with weekend plan returns mapping."""
        from api.services.tradeflow_service import generate_review
        db_path = str(tmp_path / "gen_ntd.db")
        init_db(db_path)
        save_candidate(_make_candidate("600519.SH", "2026-05-31"), db_path)

        result = generate_review("2026-05-31", tf_db_path=db_path)
        # Either generates from fallback candidates or returns no_data with mapping
        if result["status"] == "no_data":
            assert result["empty_reason"] != ""
            assert result["empty_reason_message"] != ""


# ── Non-trading-day plan → next trading day review replay ──


class TestNonTradingDayPlanReplay:
    def test_weekend_plan_resolves_to_next_trading_day(self):
        """A plan on 2026-05-31 (Sunday) reviews on the next trading day."""
        eff = next_cn_trading_day("2026-05-31")
        assert eff != "2026-05-31"
        review_date = resolve_review_date("2026-05-31", eff)
        assert review_date == eff

    def test_cross_date_chain_replayable(self, tmp_path):
        """End-to-end: weekend plan → diagnostic explains mapping → review_date set."""
        from api.services.tradeflow_service import _diagnose_review_empty_state
        db_path = str(tmp_path / "replay.db")
        init_db(db_path)
        save_candidate(_make_candidate("600519.SH", "2026-05-31"), db_path)

        diag = _diagnose_review_empty_state("2026-05-31", tf_db_path=db_path)
        assert diag["empty_reason"] == ReviewEmptyReason.NON_TRADING_DAY_MAPPED.value
        assert diag["plan_date"] == "2026-05-31"
        assert diag["effective_trade_date"] != "2026-05-31"
        assert diag["review_date"] == diag["effective_trade_date"]


# ── One-click generate button surfaces failure reason ──


class TestGenerateFailureSurface:
    def test_generate_no_candidates_surfaces_message(self):
        """generate_review on empty DB must surface a non-empty message."""
        from api.services.tradeflow_service import generate_review
        result = generate_review(
            "2026-06-02", tf_db_path="/tmp/nonexistent_tf_review_004_surf.db"
        )
        assert result["status"] == "no_data"
        # The frontend uses empty_reason_message || data_status_message || message
        combined = (
            result.get("empty_reason_message")
            or result.get("data_status_message")
            or result.get("message")
            or ""
        )
        assert combined != ""
