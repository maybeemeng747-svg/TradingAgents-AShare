"""Tests for execution_schema — N-005."""  # [N-005] execution_schema

import pytest

from tradingagents.agents.utils.execution_schema import (
    Action,
    BuyLevel,
    RiskLevel,
    ExecutionSchema,
    build_execution_schema,
)


class TestExecutionSchemaToDict:
    def test_to_dict_returns_all_fields(self):
        s = ExecutionSchema(action="HOLD")
        d = s.to_dict()
        assert d["action"] == "HOLD"
        assert "buy_level" in d
        assert "risk_level" in d
        assert "gate_failures" in d
        assert "data_quality_flags" in d
        assert isinstance(d["gate_failures"], list)
        assert isinstance(d["data_quality_flags"], list)


class TestActionDerivation:
    def test_no_position_always_wait(self):
        for intent in ("watch", "entry", "add", "reduce", "stop_loss"):
            s = build_execution_schema(
                source_coverage=80,
                evidence_coverage=80,
                confidence="高",
                opportunity_score=70,
                risk_level=3,
                buy_level=4,
                position_status="no_position",
                analysis_intent=intent,
            )
            assert s.action == "WAIT", f"intent={intent} should still be WAIT"

    def test_stop_loss_gives_exit(self):
        s = build_execution_schema(
            source_coverage=80,
            evidence_coverage=80,
            confidence="高",
            opportunity_score=30,
            risk_level=1,
            buy_level=2,
            position_status="has_position",
            analysis_intent="stop_loss",
        )
        assert s.action == "EXIT"

    def test_reduce_gives_reduce(self):
        s = build_execution_schema(
            source_coverage=80,
            evidence_coverage=80,
            confidence="中",
            opportunity_score=40,
            risk_level=2,
            buy_level=2,
            position_status="has_position",
            analysis_intent="reduce",
        )
        assert s.action == "REDUCE"

    def test_entry_with_high_buy_level_gives_enter(self):
        s = build_execution_schema(
            source_coverage=80,
            evidence_coverage=80,
            confidence="高",
            opportunity_score=75,
            risk_level=3,
            buy_level=3,
            position_status="has_position",
            analysis_intent="entry",
        )
        assert s.action == "ENTER"

    def test_add_with_high_buy_level_gives_enter(self):
        s = build_execution_schema(
            source_coverage=80,
            evidence_coverage=80,
            confidence="高",
            opportunity_score=75,
            risk_level=3,
            buy_level=4,
            position_status="has_position",
            analysis_intent="add",
        )
        assert s.action == "ENTER"

    def test_entry_with_low_buy_level_gives_hold(self):
        s = build_execution_schema(
            source_coverage=80,
            evidence_coverage=80,
            confidence="中",
            opportunity_score=50,
            risk_level=2,
            buy_level=2,
            position_status="has_position",
            analysis_intent="entry",
        )
        assert s.action == "HOLD"

    def test_watch_with_has_position_gives_hold(self):
        s = build_execution_schema(
            source_coverage=80,
            evidence_coverage=80,
            confidence="中",
            opportunity_score=50,
            risk_level=3,
            buy_level=2,
            position_status="has_position",
            analysis_intent="watch",
        )
        assert s.action == "HOLD"

    def test_unknown_position_watch_gives_hold(self):
        s = build_execution_schema(
            source_coverage=60,
            evidence_coverage=60,
            confidence="中",
            opportunity_score=40,
            risk_level=2,
            buy_level=1,
            position_status="unknown",
            analysis_intent="watch",
        )
        assert s.action == "HOLD"


class TestValidation:
    def test_valid_schema_no_issues(self):
        s = ExecutionSchema(
            action="HOLD",
            buy_level=2,
            risk_level=3,
            position_status="has_position",
            source_coverage=80,
            evidence_coverage=80,
        )
        issues = s.validate()
        assert issues == []

    def test_no_position_with_enter_fails(self):
        s = ExecutionSchema(
            action="ENTER",
            buy_level=3,
            position_status="no_position",
            source_coverage=80,
            evidence_coverage=80,
        )
        issues = s.validate()
        assert any("no_position" in i for i in issues)

    def test_invalid_action_fails(self):
        s = ExecutionSchema(
            action="BUY_NOW",
            position_status="has_position",
            source_coverage=80,
            evidence_coverage=80,
        )
        issues = s.validate()
        assert any("Invalid action" in i for i in issues)

    def test_enter_with_low_buy_level_fails(self):
        s = ExecutionSchema(
            action="ENTER",
            buy_level=1,
            position_status="has_position",
            source_coverage=80,
            evidence_coverage=80,
        )
        issues = s.validate()
        assert any("ENTER but buy_level" in i for i in issues)

    def test_reduce_without_position_fails(self):
        s = ExecutionSchema(
            action="REDUCE",
            position_status="no_position",
            source_coverage=80,
            evidence_coverage=80,
        )
        issues = s.validate()
        assert any("REDUCE but position_status" in i for i in issues)

    def test_exit_without_position_fails(self):
        s = ExecutionSchema(
            action="EXIT",
            position_status="unknown",
            source_coverage=80,
            evidence_coverage=80,
        )
        issues = s.validate()
        assert any("EXIT but position_status" in i for i in issues)

    def test_low_coverage_sets_data_quality_flags(self):
        s = ExecutionSchema(
            action="HOLD",
            position_status="has_position",
            source_coverage=30,
            evidence_coverage=40,
        )
        s.validate()
        assert "low_evidence_coverage" in s.data_quality_flags
        assert "low_source_coverage" in s.data_quality_flags

    def test_high_coverage_no_quality_flags(self):
        s = ExecutionSchema(
            action="HOLD",
            position_status="has_position",
            source_coverage=80,
            evidence_coverage=80,
        )
        s.validate()
        assert "low_evidence_coverage" not in s.data_quality_flags
        assert "low_source_coverage" not in s.data_quality_flags


class TestBuildExecutionSchemaFields:
    def test_gate_failure_propagation(self):
        gate = {"passed": False, "failures": ["evidence_too_low", "confidence_insufficient"]}
        s = build_execution_schema(
            source_coverage=40,
            evidence_coverage=40,
            confidence="低",
            opportunity_score=30,
            risk_level=2,
            buy_level=1,
            position_status="has_position",
            strong_action_gate=gate,
        )
        assert s.strong_action_gate_passed is False
        assert s.gate_failures == ["evidence_too_low", "confidence_insufficient"]

    def test_valuation_mismatch_propagation(self):
        s = build_execution_schema(
            source_coverage=80,
            evidence_coverage=80,
            confidence="中",
            opportunity_score=50,
            risk_level=2,
            buy_level=2,
            valuation_mismatch=True,
        )
        assert s.valuation_mismatch is True

    def test_default_values(self):
        s = build_execution_schema(
            source_coverage=60,
            evidence_coverage=60,
            confidence="中",
            opportunity_score=50,
            risk_level=2,
            buy_level=2,
        )
        assert s.position_status == "unknown"
        assert s.analysis_intent == "watch"
        assert s.horizon == "short"
        assert s.valuation_mismatch is False
        assert s.strong_action_gate_passed is True
        assert s.gate_failures == []


class TestEnums:
    def test_action_values(self):
        assert Action.WAIT.value == "WAIT"
        assert Action.ENTER.value == "ENTER"
        assert Action.HOLD.value == "HOLD"
        assert Action.REDUCE.value == "REDUCE"
        assert Action.EXIT.value == "EXIT"

    def test_action_is_trade_action_alias(self):
        """[C-004] Action in execution_schema must be the same object as TradeAction."""
        from tradingagents.agents.utils.trade_actions import TradeAction
        assert Action is TradeAction

    def test_action_count_is_five(self):
        """[C-004] Exactly 5 action values, no more, no less."""
        assert len(Action) == 5

    def test_buy_level_values(self):
        assert BuyLevel.LEVEL_0.value == 0
        assert BuyLevel.LEVEL_4.value == 4

    def test_risk_level_values(self):
        assert RiskLevel.LEVEL_0.value == 0
        assert RiskLevel.LEVEL_4.value == 4
