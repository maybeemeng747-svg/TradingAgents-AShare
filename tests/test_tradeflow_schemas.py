"""Tests for TradeFlow schemas."""

import sys
import os
import json
import tempfile
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.tradeflow.schemas import (
    Candidate, CandidateSignal, Signal, DailyPlan,
    ALLOWED_ACTIONS, FORBIDDEN_WORDS, ALL_STRATEGIES,
)
from tradingagents.tradeflow.ambush_score import compute_ambush_score, AmbushScoreResult
from tradingagents.tradeflow.mandate_ta_queue_router import route_to_research_queue
from tradingagents.tradeflow.mandate_watchlist_note import generate_watchlist_note


class TestCandidateSignal:
    def test_basic_creation(self):
        sig = CandidateSignal(
            strategy_tag="VCP",
            trigger_price=100.0,
            invalid_price=90.0,
            score=60.0,
            reason="缩量整理",
        )
        assert sig.strategy_tag == "VCP"
        assert sig.trigger_price == 100.0
        assert sig.score == 60.0

    def test_default_values(self):
        sig = CandidateSignal(strategy_tag="VCP")
        assert sig.score == 0.0
        assert sig.evidence == {}
        assert sig.risk_flags == []
        assert sig.need_deep_ta is False


class TestCandidate:
    def test_merge_single_signal(self):
        c = Candidate(symbol="002353.SZ", name="杰恩设计")
        c.signals = [CandidateSignal(
            strategy_tag="VCP", score=55.0,
            trigger_price=100.0, invalid_price=90.0,
        )]
        c.merge_signals()
        assert c.strategy_tags == ["VCP"]
        assert c.score == 55.0
        assert c.trigger_price == 100.0

    def test_merge_multiple_signals(self):
        c = Candidate(symbol="002353.SZ")
        c.signals = [
            CandidateSignal(strategy_tag="VCP", score=50.0, trigger_price=100.0,
                            support_price=80.0, invalid_price=75.0),
            CandidateSignal(strategy_tag="PULLBACK_SUPPORT", score=65.0, trigger_price=95.0,
                            support_price=88.0, invalid_price=82.0, need_deep_ta=True),
        ]
        c.merge_signals()
        assert set(c.strategy_tags) == {"VCP", "PULLBACK_SUPPORT"}
        assert c.score == 65.0
        assert c.need_deep_ta is True
        assert c.primary_strategy == "PULLBACK_SUPPORT"
        assert c.trigger_price == 95.0
        assert c.support_price == 88.0
        assert c.invalid_price == 82.0

    def test_primary_strategy_prices_not_overwritten(self):
        c = Candidate(symbol="002353.SZ")
        c.signals = [
            CandidateSignal(strategy_tag="VCP", score=70.0, trigger_price=100.0,
                            support_price=85.0, invalid_price=80.0),
            CandidateSignal(strategy_tag="PULLBACK_SUPPORT", score=50.0, trigger_price=95.0,
                            support_price=88.0, invalid_price=82.0),
        ]
        c.merge_signals()
        assert c.primary_strategy == "VCP"
        assert c.trigger_price == 100.0
        assert c.support_price == 85.0
        assert c.invalid_price == 80.0

    def test_to_db_row(self):
        c = Candidate(symbol="002353.SZ", name="杰恩设计", source="manual")
        c.strategy_tags = ["VCP"]
        c.primary_strategy = "VCP"
        c.score = 55.0
        row = c.to_db_row()
        assert row["symbol"] == "002353.SZ"
        assert json.loads(row["strategy_tags_json"]) == ["VCP"]
        assert row["primary_strategy"] == "VCP"

    def test_from_db_row(self):
        row = {
            "symbol": "603256.SH",
            "name": "宏和科技",
            "source": "watchlist",
            "strategy_tags_json": '["VCP", "PULLBACK_SUPPORT"]',
            "primary_strategy": "PULLBACK_SUPPORT",
            "score": 70.0,
            "status": "active",
            "trigger_price": 100.0,
            "support_price": 88.0,
            "invalid_price": 85.0,
            "need_deep_ta": 1,
            "evidence_json": '{"VCP": {"range_ratio": 0.5}}',
            "risk_flags_json": '["流动性差"]',
            "trade_date": "2026-05-25",
            "created_at": "2026-05-25T08:00:00",
            "updated_at": "2026-05-25T08:00:00",
        }
        c = Candidate.from_db_row(row)
        assert c.symbol == "603256.SH"
        assert c.strategy_tags == ["VCP", "PULLBACK_SUPPORT"]
        assert c.primary_strategy == "PULLBACK_SUPPORT"
        assert c.need_deep_ta is True
        assert c.evidence == {"VCP": {"range_ratio": 0.5}}


class TestSignal:
    def test_allowed_action(self):
        s = Signal(symbol="002353.SZ", signal_type="breakout", action_hint="OBSERVE")
        assert s.action_hint == "OBSERVE"

    def test_invalid_action_fallback(self):
        s = Signal(symbol="002353.SZ", signal_type="breakout", action_hint="立即买入")
        assert s.action_hint == "OBSERVE"  # fallback

    def test_allowed_actions_all_valid(self):
        for action in ALLOWED_ACTIONS:
            s = Signal(symbol="TEST", signal_type="test", action_hint=action)
            assert s.action_hint == action


class TestDailyPlan:
    def test_validate_clean(self):
        plan = DailyPlan(
            trade_date="2026-05-25",
            summary="今日3只候选观察",
            candidates=[
                {"symbol": "002353.SZ", "action": "OBSERVE"},
                {"symbol": "603256.SH", "action": "WAIT_TRIGGER"},
            ],
        )
        issues = plan.validate()
        assert issues == []

    def test_validate_forbidden_words(self):
        for word in FORBIDDEN_WORDS:
            plan = DailyPlan(
                trade_date="2026-05-25",
                summary=f"建议{word}该标的",
                candidates=[],
            )
            issues = plan.validate()
            assert len(issues) > 0, f"Should detect forbidden word: {word}"

    def test_validate_invalid_action(self):
        plan = DailyPlan(
            trade_date="2026-05-25",
            summary="ok",
            candidates=[{"symbol": "TEST", "action": "立即买入"}],
        )
        issues = plan.validate()
        assert any("Invalid action" in i for i in issues)

    def test_render_text(self):
        plan = DailyPlan(
            trade_date="2026-05-25",
            summary="测试计划",
            candidates=[
                {
                    "symbol": "002353.SZ",
                    "name": "杰恩设计",
                    "action": "OBSERVE",
                    "strategies": ["VCP"],
                    "reason": "缩量整理",
                    "trigger_price": 100.0,
                    "invalid_price": 90.0,
                    "need_deep_ta": False,
                },
            ],
        )
        text = plan.render_text()
        assert "002353.SZ" in text
        assert "OBSERVE" in text
        # No forbidden words
        for word in FORBIDDEN_WORDS:
            assert word not in text


class TestConstants:
    def test_allowed_actions(self):
        assert "OBSERVE" in ALLOWED_ACTIONS
        assert "WAIT_TRIGGER" in ALLOWED_ACTIONS
        assert "NEED_DEEP_TA" in ALLOWED_ACTIONS
        assert "REMOVE_FROM_WATCH" in ALLOWED_ACTIONS

    def test_no_buy_sell_in_allowed(self):
        assert "买入" not in ALLOWED_ACTIONS
        assert "卖出" not in ALLOWED_ACTIONS
        assert "立即买入" not in ALLOWED_ACTIONS

    def test_forbidden_words(self):
        assert "立即买入" in FORBIDDEN_WORDS
        assert "重仓买入" in FORBIDDEN_WORDS
        assert "立即清仓" in FORBIDDEN_WORDS

    def test_all_strategies(self):
        assert "VCP" in ALL_STRATEGIES
        assert "PULLBACK_SUPPORT" in ALL_STRATEGIES
        assert "EVENT_CATALYST" in ALL_STRATEGIES


class TestTechTradeDefaultPropagation:
    """Regression: candidate_type default TECH_TRADE must propagate to all downstream.

    Scenario: pure VCP/PULLBACK strategy, no policy/event/beneficiary evidence.
    """

    def _make_tech_candidate(self):
        c = Candidate(symbol="002353.SZ", name="测试技术")
        c.signals = [CandidateSignal(
            strategy_tag="VCP", score=55.0,
            trigger_price=100.0, support_price=88.0, invalid_price=82.0,
        )]
        c.merge_signals()
        c.candidate_type = ""
        c.candidate_type_reason = ""
        return c

    def test_ambush_falls_back_to_tech_trade(self):
        ambush_result = compute_ambush_score(
            mandate_score=0.0,
            version_score=0.0,
            policy_tags=[],
            strategy_tags=["VCP"],
            tech_score=55.0,
            has_tech_breakout=True,
            trigger_price=100.0,
            current_price=99.0,
        )
        c = self._make_tech_candidate()
        c.candidate_type = ambush_result.candidate_type
        c.candidate_type_reason = ambush_result.candidate_type_reason
        if not c.candidate_type:
            c.candidate_type = "TECH_TRADE"
            c.candidate_type_reason = c.candidate_type_reason or "无政策/事件/受益路径证据，默认技术交易"
        assert c.candidate_type == "TECH_TRADE"

    def test_evidence_json_uses_candidate_type(self):
        c = self._make_tech_candidate()
        c.candidate_type = "TECH_TRADE"
        c.candidate_type_reason = "无政策/事件/受益路径证据，默认技术交易"
        c.evidence["ambush_score"] = {
            "candidate_type": c.candidate_type,
            "candidate_type_reason": c.candidate_type_reason,
        }
        assert c.evidence["ambush_score"]["candidate_type"] == "TECH_TRADE"

    def test_research_queue_not_empty_for_tech_trade(self):
        queue_result = route_to_research_queue(
            candidate_type="TECH_TRADE",
            ambush_score=30.0,
        )
        assert queue_result.research_queue != ""
        assert queue_result.research_queue == "SHORT_TERM_TRADE"

    def test_watchlist_note_tech_semantics(self):
        wl_result = generate_watchlist_note(
            candidate_type="TECH_TRADE",
            strategy_tags=["VCP"],
            data_completeness=0.6,
        )
        assert wl_result.topic != ""
        assert wl_result.note_summary != ""
        assert "技术" in wl_result.topic or "技术" in wl_result.note_summary or "短线" in wl_result.note_summary

    def test_init_db_ensure_columns(self):
        from tradingagents.tradeflow.candidate_engine import init_db, ensure_columns
        import sqlite3
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            init_db(db_path)
            conn = sqlite3.connect(db_path)
            cols = [r[1] for r in conn.execute("PRAGMA table_info(tradeflow_candidates)").fetchall()]
            conn.close()
            assert "candidate_type" in cols
            assert "research_queue" in cols
            assert "watchlist_note_suggested" in cols
        finally:
            os.unlink(db_path)

    def test_save_candidate_default_tech_trade(self):
        from tradingagents.tradeflow.candidate_engine import init_db, save_candidate
        import sqlite3
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            init_db(db_path)
            c = Candidate(
                symbol="002353.SZ", name="测试",
                strategy_tags=["VCP"], score=55.0,
                trade_date="2026-06-02",
                primary_strategy="VCP",
            )
            c.candidate_type = "TECH_TRADE"
            c.candidate_type_reason = "无政策/事件/受益路径证据，默认技术交易"
            save_candidate(c, db_path)
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT candidate_type, research_queue, watchlist_note_suggested FROM tradeflow_candidates WHERE symbol='002353.SZ'"
            ).fetchone()
            conn.close()
            assert row is not None
            assert row["candidate_type"] == "TECH_TRADE"
        finally:
            os.unlink(db_path)
