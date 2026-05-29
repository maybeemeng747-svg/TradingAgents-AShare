# [M-006] gated_deep_ta_dispatch — tests
"""Tests for gated deep TA dispatch module.

Covers:
- DeepTAStatus enum
- DeepTARecord dataclass
- DeepTADispatcher initialization and config
- check_deep_ta_gate: all blocking conditions
- record_deep_ta_dispatch: state tracking
- can_retry_deep_ta: retry logic
- Integration with Candidate schemas
- DB persistence of deep TA fields
- Full workflow: observe → gate → dispatch
"""

from __future__ import annotations

import sqlite3
import tempfile
import os
from datetime import datetime

import pytest

from tradingagents.tradeflow.gated_deep_ta import (
    DeepTAStatus,
    DeepTARecord,
    DeepTADecision,
    DeepTADispatcher,
    check_deep_ta_gate,
    record_deep_ta_dispatch,
    can_retry_deep_ta,
)
from tradingagents.tradeflow.schemas import Candidate
from tradingagents.tradeflow.candidate_engine import init_db, save_candidate
from tradingagents.tradeflow.strategy_config import StrategyConfig, DEFAULT_STRATEGY_CONFIG


# ── DeepTAStatus ──

class TestDeepTAStatus:
    def test_enum_values(self):
        assert DeepTAStatus.PENDING == "PENDING"
        assert DeepTAStatus.BLOCKED == "BLOCKED"
        assert DeepTAStatus.DISPATCHED == "DISPATCHED"
        assert DeepTAStatus.SUCCESS == "SUCCESS"
        assert DeepTAStatus.FAILED == "FAILED"

    def test_all_statuses(self):
        expected = {"PENDING", "BLOCKED", "DISPATCHED", "SUCCESS", "FAILED"}
        assert set(s.value for s in DeepTAStatus) == expected


# ── DeepTARecord ──

class TestDeepTARecord:
    def test_default_values(self):
        r = DeepTARecord(symbol="002138.SZ")
        assert r.status == DeepTAStatus.PENDING
        assert r.dispatch_reason == ""
        assert r.model == ""
        assert r.retry_count == 0
        assert r.trade_date == datetime.now().strftime("%Y-%m-%d")

    def test_custom_values(self):
        r = DeepTARecord(
            symbol="600519.SH",
            status=DeepTAStatus.DISPATCHED,
            model="gpt-4o",
            duration_sec=12.5,
            report_path="/tmp/report.md",
            position_context="holding",
            composite_score=75.0,
            completeness=0.8,
        )
        assert r.symbol == "600519.SH"
        assert r.status == DeepTAStatus.DISPATCHED
        assert r.model == "gpt-4o"
        assert r.duration_sec == 12.5
        assert r.report_path == "/tmp/report.md"
        assert r.position_context == "holding"
        assert r.composite_score == 75.0
        assert r.completeness == 0.8

    def test_auto_trade_date(self):
        r = DeepTARecord(symbol="000001.SZ")
        assert r.trade_date == datetime.now().strftime("%Y-%m-%d")

    def test_explicit_trade_date(self):
        r = DeepTARecord(symbol="000001.SZ", trade_date="2026-05-29")
        assert r.trade_date == "2026-05-29"


# ── DeepTADispatcher ──

class TestDeepTADispatcher:
    def test_default_init(self):
        d = DeepTADispatcher()
        assert d.daily_count == 0
        assert d.daily_limit == 3
        assert d.blocked_models == ("deepseek",)
        assert d.default_model == ""
        assert d.max_retries == 1
        assert d.min_composite_score == 40.0
        assert d.min_completeness == 0.5
        assert d.require_observe_triggered is True
        assert d.records == {}
        assert d.failure_counts == {}

    def test_from_config_default(self):
        d = DeepTADispatcher.from_config()
        assert d.daily_limit == 3
        assert d.blocked_models == ("deepseek",)
        assert d.default_model == ""
        assert d.max_retries == 1

    def test_from_config_custom(self):
        cfg = StrategyConfig(
            deep_ta_daily_limit=5,
            deep_ta_default_model="gpt-4o",
            deep_ta_blocked_models=("deepseek", "doubao"),
            deep_ta_max_retries=2,
            deep_ta_min_composite_score=50.0,
            deep_ta_min_completeness=0.6,
            deep_ta_require_observe_triggered=False,
        )
        d = DeepTADispatcher.from_config(cfg)
        assert d.daily_limit == 5
        assert d.default_model == "gpt-4o"
        assert d.blocked_models == ("deepseek", "doubao")
        assert d.max_retries == 2
        assert d.min_composite_score == 50.0
        assert d.min_completeness == 0.6
        assert d.require_observe_triggered is False

    def test_auto_trade_date(self):
        d = DeepTADispatcher()
        assert d.trade_date == datetime.now().strftime("%Y-%m-%d")

    def test_explicit_trade_date(self):
        d = DeepTADispatcher(trade_date="2026-05-29")
        assert d.trade_date == "2026-05-29"


# ── check_deep_ta_gate ──

class TestCheckDeepTAGate:
    def setup_method(self):
        self.dispatcher = DeepTADispatcher(daily_limit=3)

    def test_need_deep_ta_false_blocks(self):
        decision = check_deep_ta_gate(
            dispatcher=self.dispatcher,
            symbol="002138.SZ",
            need_deep_ta=False,
            observe_state="TRIGGERED",
            composite_score=80.0,
            completeness=0.9,
        )
        assert decision.allowed is False
        assert "need_deep_ta=False" in decision.reason
        assert decision.record is not None
        assert decision.record.status == DeepTAStatus.BLOCKED

    def test_daily_limit_reached_blocks(self):
        self.dispatcher.daily_count = 3
        decision = check_deep_ta_gate(
            dispatcher=self.dispatcher,
            symbol="002138.SZ",
            need_deep_ta=True,
            observe_state="TRIGGERED",
            composite_score=80.0,
            completeness=0.9,
        )
        assert decision.allowed is False
        assert "上限" in decision.reason
        assert decision.record.status == DeepTAStatus.BLOCKED

    def test_daily_limit_not_reached_allows(self):
        self.dispatcher.daily_count = 2
        decision = check_deep_ta_gate(
            dispatcher=self.dispatcher,
            symbol="002138.SZ",
            need_deep_ta=True,
            observe_state="TRIGGERED",
            composite_score=80.0,
            completeness=0.9,
            model="gpt-4o",
        )
        assert decision.allowed is True

    def test_observe_not_triggered_blocks(self):
        decision = check_deep_ta_gate(
            dispatcher=self.dispatcher,
            symbol="002138.SZ",
            need_deep_ta=True,
            observe_state="WAITING",
            composite_score=80.0,
            completeness=0.9,
        )
        assert decision.allowed is False
        assert "WAITING" in decision.reason
        assert decision.record.status == DeepTAStatus.BLOCKED

    def test_observe_invalidated_blocks(self):
        decision = check_deep_ta_gate(
            dispatcher=self.dispatcher,
            symbol="002138.SZ",
            need_deep_ta=True,
            observe_state="INVALIDATED",
            composite_score=80.0,
            completeness=0.9,
        )
        assert decision.allowed is False
        assert "INVALIDATED" in decision.reason

    def test_observe_triggered_allows(self):
        decision = check_deep_ta_gate(
            dispatcher=self.dispatcher,
            symbol="002138.SZ",
            need_deep_ta=True,
            observe_state="TRIGGERED",
            composite_score=80.0,
            completeness=0.9,
            model="gpt-4o",
        )
        assert decision.allowed is True

    def test_require_observe_triggered_disabled(self):
        d = DeepTADispatcher(require_observe_triggered=False, default_model="gpt-4o")
        decision = check_deep_ta_gate(
            dispatcher=d,
            symbol="002138.SZ",
            need_deep_ta=True,
            observe_state="WAITING",
            composite_score=80.0,
            completeness=0.9,
        )
        assert decision.allowed is True

    def test_low_composite_score_blocks(self):
        decision = check_deep_ta_gate(
            dispatcher=self.dispatcher,
            symbol="002138.SZ",
            need_deep_ta=True,
            observe_state="TRIGGERED",
            composite_score=30.0,
            completeness=0.9,
        )
        assert decision.allowed is False
        assert "综合分" in decision.reason
        assert decision.record.status == DeepTAStatus.BLOCKED

    def test_composite_score_at_threshold_allows(self):
        decision = check_deep_ta_gate(
            dispatcher=self.dispatcher,
            symbol="002138.SZ",
            need_deep_ta=True,
            observe_state="TRIGGERED",
            composite_score=40.0,
            completeness=0.9,
            model="gpt-4o",
        )
        assert decision.allowed is True

    def test_low_completeness_blocks(self):
        decision = check_deep_ta_gate(
            dispatcher=self.dispatcher,
            symbol="002138.SZ",
            need_deep_ta=True,
            observe_state="TRIGGERED",
            composite_score=80.0,
            completeness=0.3,
        )
        assert decision.allowed is False
        assert "完整度" in decision.reason
        assert decision.record.status == DeepTAStatus.BLOCKED

    def test_completeness_at_threshold_allows(self):
        decision = check_deep_ta_gate(
            dispatcher=self.dispatcher,
            symbol="002138.SZ",
            need_deep_ta=True,
            observe_state="TRIGGERED",
            composite_score=80.0,
            completeness=0.5,
            model="gpt-4o",
        )
        assert decision.allowed is True

    def test_blocked_model_deepseek(self):
        decision = check_deep_ta_gate(
            dispatcher=self.dispatcher,
            symbol="002138.SZ",
            need_deep_ta=True,
            observe_state="TRIGGERED",
            composite_score=80.0,
            completeness=0.9,
            model="deepseek-chat",
        )
        assert decision.allowed is False
        assert "deepseek" in decision.reason.lower()
        assert decision.record.status == DeepTAStatus.BLOCKED

    def test_blocked_model_default_model_deepseek(self):
        self.dispatcher.default_model = "deepseek-v3"
        decision = check_deep_ta_gate(
            dispatcher=self.dispatcher,
            symbol="002138.SZ",
            need_deep_ta=True,
            observe_state="TRIGGERED",
            composite_score=80.0,
            completeness=0.9,
        )
        assert decision.allowed is False
        assert "deepseek" in decision.reason.lower()

    def test_allowed_model(self):
        decision = check_deep_ta_gate(
            dispatcher=self.dispatcher,
            symbol="002138.SZ",
            need_deep_ta=True,
            observe_state="TRIGGERED",
            composite_score=80.0,
            completeness=0.9,
            model="gpt-4o",
        )
        assert decision.allowed is True
        assert decision.model == "gpt-4o"

    def test_default_model_used_when_no_model(self):
        self.dispatcher.default_model = "glm-4-plus"
        decision = check_deep_ta_gate(
            dispatcher=self.dispatcher,
            symbol="002138.SZ",
            need_deep_ta=True,
            observe_state="TRIGGERED",
            composite_score=80.0,
            completeness=0.9,
        )
        assert decision.allowed is True
        assert decision.model == "glm-4-plus"

    def test_exceed_max_retries_blocks(self):
        self.dispatcher.failure_counts["002138.SZ"] = 2
        decision = check_deep_ta_gate(
            dispatcher=self.dispatcher,
            symbol="002138.SZ",
            need_deep_ta=True,
            observe_state="TRIGGERED",
            composite_score=80.0,
            completeness=0.9,
            model="gpt-4o",
            retry_count=1,
        )
        assert decision.allowed is False
        assert "重试" in decision.reason

    def test_within_max_retries_allows(self):
        self.dispatcher.failure_counts["002138.SZ"] = 0
        decision = check_deep_ta_gate(
            dispatcher=self.dispatcher,
            symbol="002138.SZ",
            need_deep_ta=True,
            observe_state="TRIGGERED",
            composite_score=80.0,
            completeness=0.9,
            model="gpt-4o",
            retry_count=1,
        )
        assert decision.allowed is True

    def test_all_gates_pass(self):
        decision = check_deep_ta_gate(
            dispatcher=self.dispatcher,
            symbol="002138.SZ",
            need_deep_ta=True,
            observe_state="TRIGGERED",
            composite_score=75.0,
            completeness=0.8,
            position_context="holding",
            tier="A",
            model="gpt-4o",
        )
        assert decision.allowed is True
        assert decision.record.status == DeepTAStatus.PENDING
        assert "综合分75.0" in decision.reason
        assert "完整度80%" in decision.reason
        assert "TRIGGERED" in decision.reason
        assert "holding" in decision.reason
        assert "A" in decision.reason

    def test_unknown_position_context(self):
        decision = check_deep_ta_gate(
            dispatcher=self.dispatcher,
            symbol="002138.SZ",
            need_deep_ta=True,
            observe_state="TRIGGERED",
            composite_score=80.0,
            completeness=0.9,
            position_context="unknown",
            model="gpt-4o",
        )
        assert decision.allowed is True
        assert decision.record.position_context == "unknown"

    def test_empty_blocked_models_tuple(self):
        d = DeepTADispatcher(blocked_models=())
        decision = check_deep_ta_gate(
            dispatcher=d,
            symbol="002138.SZ",
            need_deep_ta=True,
            observe_state="TRIGGERED",
            composite_score=80.0,
            completeness=0.9,
            model="deepseek-chat",
        )
        assert decision.allowed is True

    def test_no_model_blocks(self):
        decision = check_deep_ta_gate(
            dispatcher=self.dispatcher,
            symbol="002138.SZ",
            need_deep_ta=True,
            observe_state="TRIGGERED",
            composite_score=80.0,
            completeness=0.9,
        )
        assert decision.allowed is False
        assert "模型" in decision.reason
        assert decision.record.status == DeepTAStatus.BLOCKED

    def test_no_model_uses_default(self):
        self.dispatcher.default_model = "glm-4-plus"
        decision = check_deep_ta_gate(
            dispatcher=self.dispatcher,
            symbol="002138.SZ",
            need_deep_ta=True,
            observe_state="TRIGGERED",
            composite_score=80.0,
            completeness=0.9,
        )
        assert decision.allowed is True
        assert decision.model == "glm-4-plus"


# ── record_deep_ta_dispatch ──

class TestRecordDeepTADispatch:
    def setup_method(self):
        self.dispatcher = DeepTADispatcher(daily_limit=3)

    def test_record_dispatched_increments_count(self):
        assert self.dispatcher.daily_count == 0
        record = record_deep_ta_dispatch(
            dispatcher=self.dispatcher,
            symbol="002138.SZ",
            status=DeepTAStatus.DISPATCHED,
            model="gpt-4o",
            position_context="holding",
        )
        assert self.dispatcher.daily_count == 1
        assert record.status == DeepTAStatus.DISPATCHED
        assert record.model == "gpt-4o"
        assert record.position_context == "holding"
        assert record.dispatch_time != ""

    def test_record_failed_increments_failure(self):
        assert self.dispatcher.failure_counts.get("002138.SZ", 0) == 0
        record = record_deep_ta_dispatch(
            dispatcher=self.dispatcher,
            symbol="002138.SZ",
            status=DeepTAStatus.FAILED,
            model="gpt-4o",
        )
        assert self.dispatcher.failure_counts["002138.SZ"] == 1
        assert record.status == DeepTAStatus.FAILED
        assert self.dispatcher.daily_count == 0

    def test_record_success_no_count_change(self):
        self.dispatcher.daily_count = 1
        record = record_deep_ta_dispatch(
            dispatcher=self.dispatcher,
            symbol="002138.SZ",
            status=DeepTAStatus.SUCCESS,
            report_path="/tmp/report.md",
            duration_sec=10.5,
        )
        assert self.dispatcher.daily_count == 1
        assert record.status == DeepTAStatus.SUCCESS
        assert record.report_path == "/tmp/report.md"
        assert record.duration_sec == 10.5

    def test_record_blocked_no_count_change(self):
        record = record_deep_ta_dispatch(
            dispatcher=self.dispatcher,
            symbol="002138.SZ",
            status=DeepTAStatus.BLOCKED,
            dispatch_reason="need_deep_ta=False",
        )
        assert self.dispatcher.daily_count == 0
        assert record.status == DeepTAStatus.BLOCKED

    def test_multiple_dispatches_respect_limit(self):
        for i in range(3):
            record_deep_ta_dispatch(
                dispatcher=self.dispatcher,
                symbol=f"SYM{i:03d}.SZ",
                status=DeepTAStatus.DISPATCHED,
            )
        assert self.dispatcher.daily_count == 3
        decision = check_deep_ta_gate(
            dispatcher=self.dispatcher,
            symbol="SYM003.SZ",
            need_deep_ta=True,
            observe_state="TRIGGERED",
            composite_score=80.0,
            completeness=0.9,
        )
        assert decision.allowed is False
        assert "上限" in decision.reason

    def test_update_existing_record(self):
        record_deep_ta_dispatch(
            dispatcher=self.dispatcher,
            symbol="002138.SZ",
            status=DeepTAStatus.DISPATCHED,
            model="gpt-4o",
        )
        record = record_deep_ta_dispatch(
            dispatcher=self.dispatcher,
            symbol="002138.SZ",
            status=DeepTAStatus.SUCCESS,
            report_path="/tmp/report_002138.md",
            duration_sec=15.0,
        )
        assert record.status == DeepTAStatus.SUCCESS
        assert record.report_path == "/tmp/report_002138.md"
        assert record.model == "gpt-4o"
        assert self.dispatcher.daily_count == 1

    def test_record_with_all_fields(self):
        record = record_deep_ta_dispatch(
            dispatcher=self.dispatcher,
            symbol="600519.SH",
            status=DeepTAStatus.DISPATCHED,
            model="gpt-4o",
            duration_sec=20.5,
            report_path="/tmp/report_600519.md",
            dispatch_reason="综合分85.0; 完整度90%; 持仓=holding",
            position_context="holding",
        )
        assert record.symbol == "600519.SH"
        assert record.model == "gpt-4o"
        assert record.duration_sec == 20.5
        assert record.report_path == "/tmp/report_600519.md"
        assert "综合分" in record.dispatch_reason
        assert record.position_context == "holding"
        assert record.dispatch_time != ""
        assert "600519.SH" in self.dispatcher.records


# ── can_retry_deep_ta ──

class TestCanRetryDeepTA:
    def test_no_failures_can_retry(self):
        d = DeepTADispatcher(max_retries=1)
        assert can_retry_deep_ta(d, "002138.SZ") is True

    def test_at_max_failures_no_retry(self):
        d = DeepTADispatcher(max_retries=1)
        d.failure_counts["002138.SZ"] = 2
        assert can_retry_deep_ta(d, "002138.SZ") is False

    def test_below_max_failures_can_retry(self):
        d = DeepTADispatcher(max_retries=2)
        d.failure_counts["002138.SZ"] = 1
        assert can_retry_deep_ta(d, "002138.SZ") is True

    def test_different_symbols_independent(self):
        d = DeepTADispatcher(max_retries=1)
        d.failure_counts["002138.SZ"] = 1
        assert can_retry_deep_ta(d, "002138.SZ") is True
        assert can_retry_deep_ta(d, "600519.SH") is True
        assert can_retry_deep_ta(d, "002138.SZ", retry_count=1) is False


# ── Candidate Schema Integration ──

class TestCandidateDeepTAFields:
    def test_default_values(self):
        c = Candidate(symbol="002138.SZ")
        assert c.deep_ta_status == ""
        assert c.deep_ta_dispatch_reason == ""
        assert c.deep_ta_model == ""
        assert c.deep_ta_report_path == ""
        assert c.deep_ta_dispatch_time == ""
        assert c.deep_ta_position_context == ""

    def test_to_db_row(self):
        c = Candidate(
            symbol="002138.SZ",
            deep_ta_status="DISPATCHED",
            deep_ta_dispatch_reason="综合分75.0",
            deep_ta_model="gpt-4o",
            deep_ta_report_path="/tmp/report.md",
            deep_ta_dispatch_time="2026-05-30T10:00:00",
            deep_ta_position_context="holding",
        )
        row = c.to_db_row()
        assert row["deep_ta_status"] == "DISPATCHED"
        assert row["deep_ta_dispatch_reason"] == "综合分75.0"
        assert row["deep_ta_model"] == "gpt-4o"
        assert row["deep_ta_report_path"] == "/tmp/report.md"
        assert row["deep_ta_dispatch_time"] == "2026-05-30T10:00:00"
        assert row["deep_ta_position_context"] == "holding"

    def test_from_db_row(self):
        row = {
            "symbol": "002138.SZ",
            "name": "顺络电子",
            "source": "manual",
            "strategy_tags_json": "[]",
            "primary_strategy": "",
            "score": 0.0,
            "status": "active",
            "trigger_price": None,
            "support_price": None,
            "invalid_price": None,
            "need_deep_ta": 0,
            "evidence_json": "{}",
            "risk_flags_json": "[]",
            "trade_date": "2026-05-30",
            "created_at": "",
            "updated_at": "",
            "deep_ta_status": "BLOCKED",
            "deep_ta_dispatch_reason": "need_deep_ta=False",
            "deep_ta_model": "",
            "deep_ta_report_path": "",
            "deep_ta_dispatch_time": "",
            "deep_ta_position_context": "unknown",
        }
        c = Candidate.from_db_row(row)
        assert c.deep_ta_status == "BLOCKED"
        assert c.deep_ta_dispatch_reason == "need_deep_ta=False"
        assert c.deep_ta_model == ""
        assert c.deep_ta_position_context == "unknown"

    def test_from_db_row_defaults(self):
        row = {"symbol": "002138.SZ"}
        c = Candidate.from_db_row(row)
        assert c.deep_ta_status == ""
        assert c.deep_ta_dispatch_reason == ""
        assert c.deep_ta_model == ""
        assert c.deep_ta_report_path == ""
        assert c.deep_ta_dispatch_time == ""
        assert c.deep_ta_position_context == ""


# ── DB Persistence ──

class TestDeepTADbPersistence:
    def test_save_and_read_deep_ta_fields(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            init_db(db_path)
            c = Candidate(
                symbol="002138.SZ",
                name="顺络电子",
                trade_date="2026-05-30",
                deep_ta_status="DISPATCHED",
                deep_ta_dispatch_reason="综合分75.0; 完整度80%",
                deep_ta_model="gpt-4o",
                deep_ta_report_path="/tmp/report_002138.md",
                deep_ta_dispatch_time="2026-05-30T10:00:00",
                deep_ta_position_context="holding",
            )
            save_candidate(c, db_path)

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM tradeflow_candidates WHERE symbol='002138.SZ'"
            ).fetchone()
            conn.close()

            assert row is not None
            assert row["deep_ta_status"] == "DISPATCHED"
            assert row["deep_ta_dispatch_reason"] == "综合分75.0; 完整度80%"
            assert row["deep_ta_model"] == "gpt-4o"
            assert row["deep_ta_report_path"] == "/tmp/report_002138.md"
            assert row["deep_ta_dispatch_time"] == "2026-05-30T10:00:00"
            assert row["deep_ta_position_context"] == "holding"
        finally:
            os.unlink(db_path)

    def test_upsert_deep_ta_fields(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            init_db(db_path)
            c1 = Candidate(
                symbol="002138.SZ",
                trade_date="2026-05-30",
                deep_ta_status="PENDING",
                deep_ta_model="gpt-4o",
            )
            save_candidate(c1, db_path)

            c2 = Candidate(
                symbol="002138.SZ",
                trade_date="2026-05-30",
                deep_ta_status="SUCCESS",
                deep_ta_model="gpt-4o",
                deep_ta_report_path="/tmp/report.md",
                deep_ta_position_context="holding",
            )
            save_candidate(c2, db_path)

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM tradeflow_candidates WHERE symbol='002138.SZ'"
            ).fetchone()
            conn.close()

            assert row["deep_ta_status"] == "SUCCESS"
            assert row["deep_ta_report_path"] == "/tmp/report.md"
            assert row["deep_ta_position_context"] == "holding"
        finally:
            os.unlink(db_path)


# ── StrategyConfig Deep TA Fields ──

class TestStrategyConfigDeepTA:
    def test_default_values(self):
        cfg = StrategyConfig()
        assert cfg.deep_ta_daily_limit == 3
        assert cfg.deep_ta_default_model == ""
        assert cfg.deep_ta_blocked_models == ("deepseek",)
        assert cfg.deep_ta_max_retries == 1
        assert cfg.deep_ta_min_composite_score == 40.0
        assert cfg.deep_ta_min_completeness == 0.5
        assert cfg.deep_ta_require_observe_triggered is True

    def test_custom_values(self):
        cfg = StrategyConfig(
            deep_ta_daily_limit=5,
            deep_ta_default_model="gpt-4o",
            deep_ta_blocked_models=("deepseek", "doubao"),
            deep_ta_max_retries=3,
            deep_ta_min_composite_score=60.0,
            deep_ta_min_completeness=0.7,
            deep_ta_require_observe_triggered=False,
        )
        assert cfg.deep_ta_daily_limit == 5
        assert cfg.deep_ta_default_model == "gpt-4o"
        assert cfg.deep_ta_blocked_models == ("deepseek", "doubao")
        assert cfg.deep_ta_max_retries == 3
        assert cfg.deep_ta_min_composite_score == 60.0
        assert cfg.deep_ta_min_completeness == 0.7
        assert cfg.deep_ta_require_observe_triggered is False

    def test_to_dict_includes_deep_ta_fields(self):
        cfg = StrategyConfig()
        d = cfg.to_dict()
        assert "deep_ta_daily_limit" in d
        assert "deep_ta_default_model" in d
        assert "deep_ta_blocked_models" in d
        assert "deep_ta_max_retries" in d
        assert "deep_ta_min_composite_score" in d
        assert "deep_ta_min_completeness" in d
        assert "deep_ta_require_observe_triggered" in d


# ── Full Workflow: Observe → Gate → Dispatch ──

class TestFullDeepTAWorkflow:
    def test_triggered_candidate_gets_deep_ta(self):
        dispatcher = DeepTADispatcher.from_config()
        decision = check_deep_ta_gate(
            dispatcher=dispatcher,
            symbol="002138.SZ",
            need_deep_ta=True,
            observe_state="TRIGGERED",
            composite_score=75.0,
            completeness=0.8,
            position_context="holding",
            tier="A",
            model="gpt-4o",
        )
        assert decision.allowed is True
        record = record_deep_ta_dispatch(
            dispatcher=dispatcher,
            symbol="002138.SZ",
            status=DeepTAStatus.DISPATCHED,
            model="gpt-4o",
            position_context="holding",
            dispatch_reason=decision.reason,
        )
        assert record.status == DeepTAStatus.DISPATCHED
        assert dispatcher.daily_count == 1

        record = record_deep_ta_dispatch(
            dispatcher=dispatcher,
            symbol="002138.SZ",
            status=DeepTAStatus.SUCCESS,
            report_path="/tmp/002138_report.md",
            duration_sec=12.0,
        )
        assert record.status == DeepTAStatus.SUCCESS
        assert record.report_path == "/tmp/002138_report.md"

    def test_not_triggered_candidate_blocked(self):
        dispatcher = DeepTADispatcher.from_config()
        decision = check_deep_ta_gate(
            dispatcher=dispatcher,
            symbol="600519.SH",
            need_deep_ta=True,
            observe_state="WAITING",
            composite_score=80.0,
            completeness=0.9,
        )
        assert decision.allowed is False

    def test_daily_limit_exhaustion(self):
        dispatcher = DeepTADispatcher(daily_limit=2)
        for i in range(2):
            decision = check_deep_ta_gate(
                dispatcher=dispatcher,
                symbol=f"SYM{i:03d}.SZ",
                need_deep_ta=True,
                observe_state="TRIGGERED",
                composite_score=80.0,
                completeness=0.9,
                model="gpt-4o",
            )
            assert decision.allowed is True
            record_deep_ta_dispatch(
                dispatcher=dispatcher,
                symbol=f"SYM{i:03d}.SZ",
                status=DeepTAStatus.DISPATCHED,
            )

        decision = check_deep_ta_gate(
            dispatcher=dispatcher,
            symbol="SYM002.SZ",
            need_deep_ta=True,
            observe_state="TRIGGERED",
            composite_score=80.0,
            completeness=0.9,
        )
        assert decision.allowed is False
        assert "上限" in decision.reason

    def test_failure_then_retry_then_success(self):
        dispatcher = DeepTADispatcher(max_retries=1)

        decision = check_deep_ta_gate(
            dispatcher=dispatcher,
            symbol="002138.SZ",
            need_deep_ta=True,
            observe_state="TRIGGERED",
            composite_score=75.0,
            completeness=0.8,
            model="gpt-4o",
        )
        assert decision.allowed is True

        record_deep_ta_dispatch(
            dispatcher=dispatcher,
            symbol="002138.SZ",
            status=DeepTAStatus.FAILED,
            model="gpt-4o",
        )
        assert dispatcher.failure_counts["002138.SZ"] == 1

        decision2 = check_deep_ta_gate(
            dispatcher=dispatcher,
            symbol="002138.SZ",
            need_deep_ta=True,
            observe_state="TRIGGERED",
            composite_score=75.0,
            completeness=0.8,
            model="gpt-4o",
        )
        assert decision2.allowed is True

        record_deep_ta_dispatch(
            dispatcher=dispatcher,
            symbol="002138.SZ",
            status=DeepTAStatus.FAILED,
            model="gpt-4o",
        )
        assert dispatcher.failure_counts["002138.SZ"] == 2

        decision3 = check_deep_ta_gate(
            dispatcher=dispatcher,
            symbol="002138.SZ",
            need_deep_ta=True,
            observe_state="TRIGGERED",
            composite_score=75.0,
            completeness=0.8,
            model="gpt-4o",
        )
        assert decision3.allowed is False
        assert "重试" in decision3.reason

    def test_deepseek_always_blocked_by_default(self):
        dispatcher = DeepTADispatcher.from_config()
        decision = check_deep_ta_gate(
            dispatcher=dispatcher,
            symbol="002138.SZ",
            need_deep_ta=True,
            observe_state="TRIGGERED",
            composite_score=90.0,
            completeness=0.95,
            model="deepseek-chat",
        )
        assert decision.allowed is False

    def test_multiple_symbols_independent(self):
        dispatcher = DeepTADispatcher(daily_limit=2)

        decision1 = check_deep_ta_gate(
            dispatcher=dispatcher,
            symbol="002138.SZ",
            need_deep_ta=True,
            observe_state="TRIGGERED",
            composite_score=75.0,
            completeness=0.8,
            model="gpt-4o",
        )
        assert decision1.allowed is True
        record_deep_ta_dispatch(dispatcher, "002138.SZ", DeepTAStatus.DISPATCHED)

        decision2 = check_deep_ta_gate(
            dispatcher=dispatcher,
            symbol="600519.SH",
            need_deep_ta=True,
            observe_state="TRIGGERED",
            composite_score=80.0,
            completeness=0.9,
            model="gpt-4o",
        )
        assert decision2.allowed is True
        record_deep_ta_dispatch(dispatcher, "600519.SH", DeepTAStatus.DISPATCHED)

        record_deep_ta_dispatch(dispatcher, "002138.SZ", DeepTAStatus.FAILED)

        assert dispatcher.failure_counts.get("002138.SZ") == 1
        assert dispatcher.failure_counts.get("600519.SH") is None
        assert dispatcher.daily_count == 2

        assert can_retry_deep_ta(dispatcher, "002138.SZ") is True
        assert can_retry_deep_ta(dispatcher, "002138.SZ", retry_count=1) is False
        assert can_retry_deep_ta(dispatcher, "600519.SH") is True

    def test_no_infinite_retry_loop(self):
        dispatcher = DeepTADispatcher(max_retries=1)
        record_deep_ta_dispatch(dispatcher, "002138.SZ", DeepTAStatus.FAILED)
        record_deep_ta_dispatch(dispatcher, "002138.SZ", DeepTAStatus.FAILED)

        for _ in range(10):
            decision = check_deep_ta_gate(
                dispatcher=dispatcher,
                symbol="002138.SZ",
                need_deep_ta=True,
                observe_state="TRIGGERED",
                composite_score=80.0,
                completeness=0.9,
                model="gpt-4o",
            )
            assert decision.allowed is False
