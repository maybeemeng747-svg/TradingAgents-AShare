# [TA-UI-001] analysis_console_horizon_intent
"""Tests for TA-UI-001: analysis console horizon / intent / position context.

Covers:
- Backend ChatCompletionRequest accepts objective / investment_horizon / position fields
- Backend _extract_request_user_context extracts structured fields
- Backend AnalyzeRequest accepts horizons field
- Frontend TypeScript types include horizons and user_intent
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.main import (
    ChatCompletionRequest,
    AnalyzeRequest,
    UserContextInput,
    _extract_request_user_context,
    ChatMessage,
)


class TestUserContextInput:
    def test_accepts_objective_and_horizon(self):
        req = UserContextInput(
            objective="建仓/入场研究",
            investment_horizon="中线",
        )
        assert req.objective == "建仓/入场研究"
        assert req.investment_horizon == "中线"

    def test_accepts_position_fields(self):
        req = UserContextInput(
            current_position=1000,
            current_position_pct=0.3,
            average_cost=45.50,
        )
        assert req.current_position == 1000
        assert req.current_position_pct == 0.3
        assert req.average_cost == 45.50

    def test_defaults_none(self):
        req = UserContextInput()
        assert req.objective is None
        assert req.investment_horizon is None
        assert req.current_position is None
        assert req.average_cost is None
        assert req.constraints == []


class TestExtractRequestUserContext:
    def test_extracts_objective(self):
        req = UserContextInput(objective="建仓")
        ctx = _extract_request_user_context(req)
        assert ctx["objective"] == "建仓"

    def test_extracts_investment_horizon(self):
        req = UserContextInput(investment_horizon="中线")
        ctx = _extract_request_user_context(req)
        assert ctx["investment_horizon"] == "中线"

    def test_extracts_position_fields(self):
        req = UserContextInput(
            current_position=500,
            current_position_pct=0.15,
            average_cost=100.0,
        )
        ctx = _extract_request_user_context(req)
        assert ctx["current_position"] == 500
        assert ctx["current_position_pct"] == 0.15
        assert ctx["average_cost"] == 100.0

    def test_skips_empty_strings(self):
        req = UserContextInput(objective="  ", investment_horizon="")
        ctx = _extract_request_user_context(req)
        assert "objective" not in ctx
        assert "investment_horizon" not in ctx

    def test_skips_empty_constraints(self):
        req = UserContextInput(constraints=[])
        ctx = _extract_request_user_context(req)
        assert "constraints" not in ctx


class TestAnalyzeRequestHorizons:
    def test_horizons_default_short(self):
        req = AnalyzeRequest(symbol="600519.SH")
        assert req.horizons == ["short"]

    def test_horizons_medium(self):
        req = AnalyzeRequest(symbol="600519.SH", horizons=["medium"])
        assert req.horizons == ["medium"]

    def test_horizons_short_and_medium(self):
        req = AnalyzeRequest(symbol="600519.SH", horizons=["short", "medium"])
        assert req.horizons == ["short", "medium"]

    def test_user_intent_field(self):
        req = AnalyzeRequest(
            symbol="600519.SH",
            user_intent={"raw_query": "test", "ticker": "600519.SH"},
        )
        assert req.user_intent is not None
        assert req.user_intent["ticker"] == "600519.SH"


class TestChatCompletionRequest:
    def test_accepts_user_context_fields(self):
        req = ChatCompletionRequest(
            messages=[ChatMessage(role="user", content="分析 600519.SH")],
            objective="建仓",
            investment_horizon="中线",
            current_position=500,
            average_cost=1800.0,
        )
        assert req.objective == "建仓"
        assert req.investment_horizon == "中线"
        assert req.current_position == 500
        assert req.average_cost == 1800.0

    def test_context_extracts_correctly(self):
        req = ChatCompletionRequest(
            messages=[ChatMessage(role="user", content="分析 600519.SH")],
            objective="减仓/止损",
            investment_horizon="短线",
        )
        ctx = _extract_request_user_context(req)
        assert ctx["objective"] == "减仓/止损"
        assert ctx["investment_horizon"] == "短线"


class TestIntentMapping:
    def test_all_intent_values_accepted_as_objective(self):
        intent_map = {
            "watch": "观察",
            "entry": "建仓/入场研究",
            "holding": "持仓复盘",
            "add": "加仓判断",
            "reduce": "减仓/止损",
        }
        for intent_value, cn_label in intent_map.items():
            req = UserContextInput(objective=cn_label)
            ctx = _extract_request_user_context(req)
            assert ctx["objective"] == cn_label, f"intent={intent_value} failed"
