# [C-003] 禁止做空策略输出 — 测试
"""Tests for C-003: when can_short=False, short-selling strategies are
filtered out at generation stage and do not enter the candidate pool."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from tradingagents.agents.utils.readiness_score import (
    _sanitize_short_selling_text,
    _SHORT_SELLING_PATTERNS,
    filter_short_strategy,
)


# ═══════════════════════════════════════════════════════════════
# 1. Expanded pattern coverage
# ═══════════════════════════════════════════════════════════════

class TestExpandedPatterns:
    """Verify the expanded _SHORT_SELLING_PATTERNS catch additional variants."""

    def test_patterns_include_new_variants(self):
        new_keywords = [
            "反手做空", "开空仓", "融券做空", "融券卖出",
            "逢高做空", "试空", "平空", "做空仓位",
        ]
        pattern_src = " ".join(p for p, _ in _SHORT_SELLING_PATTERNS)
        for kw in new_keywords:
            assert kw in pattern_src or any(
                kw in p for p, _ in _SHORT_SELLING_PATTERNS
            ), f"Pattern for '{kw}' not found in _SHORT_SELLING_PATTERNS"

    @pytest.mark.parametrize("text,absent", [
        ("反手做空该股", "反手做空"),
        ("开空仓信号出现", "开空仓"),
        ("融券做空获利", "融券做空"),
        ("融券卖出操作", "融券卖出"),
        ("逢高做空时机", "逢高做空"),
        ("空头加仓至50%", "空头加仓"),
        ("考虑试空", "试空"),
        ("平空后观望", "平空"),
        ("做空仓位已平", "做空仓位"),
    ])
    def test_new_pattern_replacement(self, text, absent):
        result, changes = _sanitize_short_selling_text(text)
        assert absent not in result, f"'{absent}' should be replaced in '{result}'"
        assert len(changes) > 0

    def test_english_short_patterns(self):
        result, changes = _sanitize_short_selling_text("open a short position")
        assert "short" not in result.lower() or "回避" in result
        assert len(changes) > 0

    def test_no_false_positive_on_bearish_direction(self):
        """Bearish direction terms (看空/偏空) are valid analysis, not short-selling."""
        text = "分析结论：看空。偏空信号明显。"
        result, changes = _sanitize_short_selling_text(text)
        # 看空/偏空 should NOT be replaced — they are direction labels
        assert "看空" in result
        assert "偏空" in result


# ═══════════════════════════════════════════════════════════════
# 2. filter_short_strategy convenience function
# ═══════════════════════════════════════════════════════════════

class TestFilterShortStrategy:
    """Test the filter_short_strategy convenience wrapper."""

    def test_passes_through_when_can_short_true(self):
        text = "建议做空策略，反手做空。"
        result = filter_short_strategy(text, can_short=True)
        assert result["text"] == text
        assert result["filtered"] is False
        assert result["changes"] == []

    def test_filters_when_can_short_false(self):
        text = "建议做空策略，考虑做空。"
        result = filter_short_strategy(text, can_short=False)
        assert "做空策略" not in result["text"]
        assert result["filtered"] is True
        assert len(result["changes"]) > 0

    def test_no_changes_for_clean_text(self):
        text = "建议观望，等待确认信号。"
        result = filter_short_strategy(text, can_short=False)
        assert result["text"] == text
        assert result["filtered"] is False
        assert result["changes"] == []

    def test_empty_text(self):
        result = filter_short_strategy("", can_short=False)
        assert result["text"] == ""
        assert result["filtered"] is False

    def test_default_can_short_is_false(self):
        """Default behavior: can_short=False (A-share default)."""
        text = "建议做空。"
        result = filter_short_strategy(text)
        assert "做空" not in result["text"]
        assert result["filtered"] is True

    def test_multiple_patterns_replaced(self):
        text = "建议做空策略，考虑做空，反手做空。"
        result = filter_short_strategy(text, can_short=False)
        assert "做空" not in result["text"]
        assert len(result["changes"]) >= 2

    def test_preserves_bearish_direction(self):
        text = "看空信号明显，建议回避。"
        result = filter_short_strategy(text, can_short=False)
        assert "看空" in result["text"]
        assert result["filtered"] is False


# ═══════════════════════════════════════════════════════════════
# 3. Research Manager integration
# ═══════════════════════════════════════════════════════════════

class TestResearchManagerShortFilter:
    """Verify research_manager applies short strategy filter."""

    def test_research_manager_filters_short_output(self):
        from tradingagents.agents.managers.research_manager import create_research_manager
        import asyncio

        class _FakeLLM:
            async def astream(self, _prompt):
                yield MagicMock(
                    content=(
                        "投资方案：建议做空策略，反手做空该股。\n"
                        "<!-- VERDICT: {\"direction\": \"看空\", \"reason\": \"技术破位\"} -->"
                    )
                )

        class _Memory:
            def get_memories(self, _situation, n_matches=2):
                return []

        state = {
            "market_report": "技术面报告",
            "sentiment_report": "情绪面报告",
            "news_report": "新闻报告",
            "fundamentals_report": "基本面报告",
            "smart_money_report": "",
            "volume_price_report": "",
            "investment_debate_state": {
                "history": "", "bear_history": "", "bull_history": "",
                "current_speaker": "", "current_response": "",
                "count": 0, "claims": [], "focus_claim_ids": [],
                "open_claim_ids": [], "resolved_claim_ids": [],
                "unresolved_claim_ids": [], "round_summary": "",
                "round_goal": "", "claim_counter": 0,
            },
            "user_context": {"current_position": 0},
        }

        with patch(
            "tradingagents.agents.managers.research_manager.get_config",
            return_value={"account_capability": {"can_short": False}},
        ):
            node = create_research_manager(_FakeLLM(), _Memory())
            result = asyncio.run(node(state))

        plan = result["investment_plan"]
        assert "做空策略" not in plan
        assert "反手做空" not in plan
        assert "看空信号" in plan or "回避" in plan

    def test_research_manager_preserves_when_can_short_true(self):
        from tradingagents.agents.managers.research_manager import create_research_manager
        import asyncio

        class _FakeLLM:
            async def astream(self, _prompt):
                yield MagicMock(
                    content=(
                        "投资方案：建议做空策略。\n"
                        "<!-- VERDICT: {\"direction\": \"看空\", \"reason\": \"技术破位\"} -->"
                    )
                )

        class _Memory:
            def get_memories(self, _situation, n_matches=2):
                return []

        state = {
            "market_report": "技术面报告",
            "sentiment_report": "情绪面报告",
            "news_report": "新闻报告",
            "fundamentals_report": "基本面报告",
            "smart_money_report": "",
            "volume_price_report": "",
            "investment_debate_state": {
                "history": "", "bear_history": "", "bull_history": "",
                "current_speaker": "", "current_response": "",
                "count": 0, "claims": [], "focus_claim_ids": [],
                "open_claim_ids": [], "resolved_claim_ids": [],
                "unresolved_claim_ids": [], "round_summary": "",
                "round_goal": "", "claim_counter": 0,
            },
            "user_context": {"current_position": 0},
        }

        with patch(
            "tradingagents.agents.managers.research_manager.get_config",
            return_value={"account_capability": {"can_short": True}},
        ):
            node = create_research_manager(_FakeLLM(), _Memory())
            result = asyncio.run(node(state))

        plan = result["investment_plan"]
        # When can_short=True, text should not be filtered
        assert "做空策略" in plan


# ═══════════════════════════════════════════════════════════════
# 4. Bear researcher integration (existing behavior preserved)
# ═══════════════════════════════════════════════════════════════

class TestBearResearcherShortFilter:
    """Verify bear_researcher already has can_short prompt constraint."""

    def test_bear_researcher_reads_can_short(self):
        from tradingagents.agents.researchers.bear_researcher import create_bear_researcher
        import asyncio

        captured_prompt = []

        class _FakeLLM:
            async def astream(self, prompt):
                captured_prompt.append(prompt)
                yield MagicMock(content="看空分析结果")

        class _Memory:
            def get_memories(self, _situation, n_matches=2):
                return []

        state = {
            "investment_debate_state": {
                "history": "", "current_response": "",
                "claims": [], "focus_claim_ids": [],
                "unresolved_claim_ids": [], "round_summary": "",
                "round_goal": "", "count": 0,
            },
            "market_report": "", "sentiment_report": "",
            "news_report": "", "fundamentals_report": "",
            "volume_price_report": "",
            "horizon": "medium",
            "user_intent": {},
        }

        with patch(
            "tradingagents.agents.researchers.bear_researcher.get_config",
            return_value={"account_capability": {"can_short": False}},
        ):
            node = create_bear_researcher(_FakeLLM(), _Memory())
            asyncio.run(node(state))

        prompt = captured_prompt[0]
        assert "不允许做空" in prompt


# ═══════════════════════════════════════════════════════════════
# 5. Risk Manager integration (existing behavior preserved)
# ═══════════════════════════════════════════════════════════════

class TestRiskManagerShortFilter:
    """Verify risk_manager still applies _sanitize_short_selling_text."""

    def test_risk_manager_filters_short_text(self):
        from tradingagents.agents.managers.risk_manager import create_risk_manager
        import asyncio

        class _FakeLLM:
            async def astream(self, _prompt):
                yield MagicMock(
                    content=(
                        "建议做空策略，考虑做空。\n"
                        "<!-- RISK_JUDGE: {"
                        "\"verdict\":\"pass\","
                        "\"hard_constraints\":[],"
                        "\"soft_constraints\":[],"
                        "\"execution_preconditions\":[],"
                        "\"de_risk_triggers\":[],"
                        "\"revision_reason\":\"\""
                        "} -->"
                    )
                )

        class _Memory:
            def get_memories(self, _situation, n_matches=2):
                return []

        state = {
            "company_of_interest": "002837.SZ",
            "market_report": "技术面报告",
            "sentiment_report": "情绪面报告",
            "news_report": "新闻报告",
            "fundamentals_report": "基本面报告",
            "investment_plan": "方案",
            "trader_investment_plan": "交易计划",
            "user_context": {"current_position": 100},
            "risk_feedback_state": {"retry_count": 0, "max_retries": 1},
            "risk_debate_state": {
                "history": "", "aggressive_history": "",
                "conservative_history": "", "neutral_history": "",
                "current_aggressive_response": "",
                "current_conservative_response": "",
                "current_neutral_response": "",
                "count": 0, "claims": [], "focus_claim_ids": [],
                "open_claim_ids": [], "resolved_claim_ids": [],
                "unresolved_claim_ids": [], "round_summary": "",
                "round_goal": "", "claim_counter": 0,
            },
        }

        with patch(
            "tradingagents.agents.managers.risk_manager.get_config",
            return_value={"account_capability": {"can_short": False}},
        ):
            node = create_risk_manager(_FakeLLM(), _Memory())
            result = asyncio.run(node(state))

        assert "做空策略" not in result["final_trade_decision"]
        assert "看空信号" in result["final_trade_decision"]
