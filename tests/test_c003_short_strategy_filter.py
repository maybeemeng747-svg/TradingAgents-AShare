# [C-003] 禁止做空策略输出 — 测试
"""Tests for C-003: when can_short=False, short-selling strategies are
filtered out at generation stage and do not enter the candidate pool.

[C-003-R1] Adds tests for the three round-1 audit findings:
  1. Legitimate long-side exits (卖出/减仓/止损/EXIT/SELL) must not be filtered.
  2. Raw short-selling tokens must never reach SSE/frontend before cleaning
     (true server-side blocking via ShortSellingStreamFilter).
  3. English short patterns must match case-insensitively (OPEN SHORT / Short
     Selling).
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from tradingagents.agents.utils.readiness_score import (
    _sanitize_short_selling_text,
    _SHORT_SELLING_PATTERNS,
    filter_short_strategy,
    # [C-003-R1]
    ShortSellingStreamFilter,
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

    def test_mixed_long_exit_does_not_mask_short_position_increase(self):
        text = "建议减仓，禁止空头加仓"
        result = filter_short_strategy(text, can_short=False)
        assert "建议减仓" in result["text"]
        assert "空头加仓" not in result["text"]
        assert result["filtered"] is True


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


# ═══════════════════════════════════════════════════════════════
# [C-003-R1] Streaming server-side blocking + case-insensitive + SELL/EXIT
# ═══════════════════════════════════════════════════════════════

class TestCaseInsensitiveEnglish:
    """[C-003-R1] English short filters must match case-insensitively."""

    @pytest.mark.parametrize("text", [
        "OPEN SHORT position",
        "Short Selling opportunity",
        "OPEN A SHORT position",
        "Take a SHORT POSITION",
        "Short sel now",
    ])
    def test_uppercase_and_mixed_case_caught(self, text):
        result, changes = _sanitize_short_selling_text(text)
        assert "short" not in result.lower(), (
            f"case-insensitive match failed for '{text}': got '{result}'"
        )
        assert len(changes) > 0

    def test_filter_short_strategy_catches_uppercase(self):
        result = filter_short_strategy("OPEN A SHORT now", can_short=False)
        assert "short" not in result["text"].lower()
        assert result["filtered"] is True


class TestStreamFilterCrossChunk:
    """[C-003-R1] ShortSellingStreamFilter catches keywords split across
    chunks so no raw short-selling language is ever emitted."""

    def test_keyword_split_across_chunks(self):
        """做空策略 split as ['做', '空策略'] must be caught before emission."""
        f = ShortSellingStreamFilter(can_short=False)
        emitted = []
        for ch in ["建议", "做", "空策略", "以获利"]:
            emitted.append(f.feed(ch))
        emitted.append(f.finalize())
        joined = "".join(emitted)
        assert "做空策略" not in joined
        assert "做空" not in joined
        assert len(f.changes) > 0

    def test_no_raw_chunk_contains_full_keyword(self):
        """No single emitted chunk should contain a complete short keyword."""
        f = ShortSellingStreamFilter(can_short=False)
        chunks = ["反手", "做空", "该股，", "考虑", "试空"]
        emitted = []
        for ch in chunks:
            piece = f.feed(ch)
            # A piece may legitimately be empty (buffered tail), but a piece
            # must never itself contain a raw short keyword.
            for kw in ("做空", "试空", "融券卖出", "空头开仓"):
                assert kw not in piece, f"raw keyword '{kw}' leaked in chunk '{piece}'"
            emitted.append(piece)
        emitted.append(f.finalize())
        joined = "".join(emitted)
        assert "做空" not in joined
        assert "试空" not in joined

    def test_passthrough_when_can_short_true(self):
        f = ShortSellingStreamFilter(can_short=True)
        out = []
        for ch in ["建议", "做空策略", "，反手做空"]:
            out.append(f.feed(ch))
        out.append(f.finalize())
        joined = "".join(out)
        assert joined == "建议做空策略，反手做空"
        assert f.changes == []

    def test_empty_chunks(self):
        f = ShortSellingStreamFilter(can_short=False)
        assert f.feed("") == ""
        assert f.finalize() == ""

    def test_finalize_flushes_clean_tail(self):
        """A short keyword sitting entirely in the held-back tail is still
        sanitized when finalize() is called."""
        f = ShortSellingStreamFilter(can_short=False)
        out = [f.feed("建议做空策略")]
        out.append(f.finalize())
        joined = "".join(out)
        assert "做空策略" not in joined
        assert len(f.changes) > 0

    @pytest.mark.parametrize(
        "text",
        [
            "甲乙丙丁short position甲乙丙丁ABCDE12345",
            "甲乙丙丁融券卖出甲乙丙丁ABCDE12345",
        ],
    )
    def test_one_character_chunks_do_not_duplicate_prefix(self, text):
        expected = filter_short_strategy(text, can_short=False)["text"]
        f = ShortSellingStreamFilter(can_short=False)
        emitted = [f.feed(ch) for ch in text]
        emitted.append(f.finalize())

        assert "".join(emitted) == expected

    @pytest.mark.parametrize(
        ("text", "split"),
        [
            ("abc short position def", 17),
            ("abcdefghijklmnopOPEN SHORT def", 23),
        ],
    )
    def test_shorter_replacement_does_not_rewind_stream(self, text, split):
        expected = filter_short_strategy(text, can_short=False)["text"]
        f = ShortSellingStreamFilter(can_short=False)
        emitted = f.feed(text[:split]) + f.feed(text[split:]) + f.finalize()
        assert emitted == expected

    def test_unbounded_whitespace_phrase_stays_buffered(self):
        text = "prefix open" + (" " * 100) + "short suffix"
        expected = filter_short_strategy(text, can_short=False)["text"]
        f = ShortSellingStreamFilter(can_short=False)
        emitted = (
            f.feed("prefix open")
            + f.feed(" " * 100)
            + f.feed("short suffix")
            + f.finalize()
        )
        assert emitted == expected
        assert "open" not in emitted.lower()


class TestLegitLongExitsPreserved:
    """[C-003-R1] Legitimate long-side exits must NOT be filtered."""

    @pytest.mark.parametrize("kw", [
        "卖出", "减仓", "止损", "止盈", "清仓", "EXIT", "SELL", "REDUCE",
    ])
    def test_legit_exit_not_stripped(self, kw):
        f = ShortSellingStreamFilter(can_short=False)
        out = [f.feed(f"建议{kw}离场")]
        out.append(f.finalize())
        joined = "".join(out)
        assert kw in joined, f"legit exit '{kw}' was incorrectly filtered out"
        assert f.changes == []

    def test_legit_exit_in_sanitize_text(self):
        text = "已持仓，建议卖出、减仓、止损，可EXIT离场。"
        result, changes = _sanitize_short_selling_text(text)
        for kw in ("卖出", "减仓", "止损", "EXIT"):
            assert kw in result
        assert changes == []


class TestResearchManagerStreamNoLeak:
    """[C-003-R1] Research Manager must not stream raw short-selling tokens.

    The audit found that emit_debate_token/emit_debate_message fired *before*
    the post-processing filter ran. Now each token is sanitized via
    ShortSellingStreamFilter before it reaches the tracker.
    """

    def _make_tracker(self):
        class _Tracker:
            def __init__(self):
                self.tokens = []
                self.messages = []
                self.messages = []

            def _emit_token(self, agent, report_type, token):
                self.tokens.append(token)

            def emit_debate_token(self, *, debate, agent, round_num, token):
                self.tokens.append(token)

            def emit_debate_message(self, *, debate, agent, round_num,
                                    content, is_verdict=False):
                self.messages.append(content)

        return _Tracker()

    def test_no_raw_short_token_reaches_tracker(self):
        from tradingagents.agents.managers.research_manager import create_research_manager

        class _FakeLLM:
            async def astream(self, _prompt):
                # Emit short-selling language that spans several chunks.
                for piece in ["建议", "做空策略", "，", "反手做空", "，short position"]:
                    yield MagicMock(content=piece)

        class _Memory:
            def get_memories(self, _situation, n_matches=2):
                return []

        state = {
            "market_report": "技术面", "sentiment_report": "情绪",
            "news_report": "新闻", "fundamentals_report": "基本面",
            "smart_money_report": "", "volume_price_report": "",
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
        tracker = self._make_tracker()

        with patch(
            "tradingagents.agents.managers.research_manager.get_config",
            return_value={"account_capability": {"can_short": False}},
        ), patch(
            "tradingagents.agents.managers.research_manager.current_tracker_var"
        ) as ct:
            ct.get.return_value = tracker
            node = create_research_manager(_FakeLLM(), _Memory())
            result = asyncio.run(node(state))

        # No individual streamed token carries a raw short keyword.
        for tok in tracker.tokens:
            low = tok.lower()
            assert "做空" not in tok
            assert "short" not in low
        # The aggregated verdict message is also clean.
        verdict = tracker.messages[-1] if tracker.messages else ""
        assert "做空" not in verdict
        assert "short" not in verdict.lower()
        # Final plan is clean.
        plan = result["investment_plan"]
        assert "做空" not in plan
        assert "short" not in plan.lower()

    def test_research_manager_preserves_sell_exit_for_long(self):
        """When can_short=False but the user holds a long, SELL/EXIT must
        survive — these are valid long-side exits, not short strategies."""
        from tradingagents.agents.managers.research_manager import create_research_manager

        class _FakeLLM:
            async def astream(self, _prompt):
                yield MagicMock(
                    content="已持仓，建议卖出/减仓/止损，可EXIT离场，SELL平仓。"
                )

        class _Memory:
            def get_memories(self, _situation, n_matches=2):
                return []

        state = {
            "market_report": "技术面", "sentiment_report": "情绪",
            "news_report": "新闻", "fundamentals_report": "基本面",
            "smart_money_report": "", "volume_price_report": "",
            "investment_debate_state": {
                "history": "", "bear_history": "", "bull_history": "",
                "current_speaker": "", "current_response": "",
                "count": 0, "claims": [], "focus_claim_ids": [],
                "open_claim_ids": [], "resolved_claim_ids": [],
                "unresolved_claim_ids": [], "round_summary": "",
                "round_goal": "", "claim_counter": 0,
            },
            "user_context": {"current_position": 100},
        }

        with patch(
            "tradingagents.agents.managers.research_manager.get_config",
            return_value={"account_capability": {"can_short": False}},
        ):
            node = create_research_manager(_FakeLLM(), _Memory())
            result = asyncio.run(node(state))

        plan = result["investment_plan"]
        for kw in ("卖出", "减仓", "止损", "EXIT", "SELL"):
            assert kw in plan, f"legit exit '{kw}' was wrongly filtered"


class TestRiskManagerStreamNoLeak:
    """[C-003-R1] Risk Manager must not stream raw short-selling tokens."""

    def test_no_raw_short_token_reaches_tracker(self):
        from tradingagents.agents.managers.risk_manager import create_risk_manager

        class _Tracker:
            def __init__(self):
                self.tokens = []
                self.messages = []

            def emit_debate_token(self, *, debate, agent, round_num, token):
                self.tokens.append(token)

            def emit_debate_message(self, *, debate, agent, round_num,
                                    content, is_verdict=False):
                self.messages.append(content)

        class _FakeLLM:
            async def astream(self, _prompt):
                for piece in ["建议", "做空策略", "，short position", "。"]:
                    yield MagicMock(content=piece)
                yield MagicMock(content="假设约25元，建议买入。")
                yield MagicMock(
                    content=(
                        "\n<!-- RISK_JUDGE: {"
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
            "trade_date": "2026-08-04",
            "market_report": "技术面", "sentiment_report": "情绪",
            "news_report": "新闻", "fundamentals_report": "基本面",
            "investment_plan": "方案", "trader_investment_plan": "交易计划",
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
        tracker = _Tracker()

        with patch(
            "tradingagents.agents.managers.risk_manager.get_config",
            return_value={"account_capability": {"can_short": False}},
        ), patch(
            "tradingagents.agents.managers.risk_manager.current_tracker_var"
        ) as ct:
            ct.get.return_value = tracker
            node = create_risk_manager(_FakeLLM(), _Memory())
            result = asyncio.run(node(state))

        for tok in tracker.tokens:
            low = tok.lower()
            assert "做空" not in tok
            assert "short" not in low
        assert tracker.tokens == []
        assert tracker.messages
        assert "建议买入" not in tracker.messages[-1]
        assert "做空" not in result["final_trade_decision"]
        assert "short" not in result["final_trade_decision"].lower()
        assert "建议买入" not in result["final_trade_decision"]


class TestBearResearcherStreamNoLeak:
    """[C-003-R1] Bear Researcher must not stream raw short-selling tokens and
    must keep legitimate SELL/EXIT for an already-held long."""

    def test_prompt_no_longer_bans_sell_exit(self):
        """[P1 fix] The appended constraint must NOT tell the agent to avoid
        SELL/EXIT (those are valid long exits). It must only forbid opening a
        short position."""
        from tradingagents.agents.researchers.bear_researcher import create_bear_researcher

        captured = []

        class _FakeLLM:
            async def astream(self, prompt):
                captured.append(prompt)
                yield MagicMock(content="看空分析")

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
            "volume_price_report": "", "horizon": "medium", "user_intent": {},
        }

        with patch(
            "tradingagents.agents.researchers.bear_researcher.get_config",
            return_value={"account_capability": {"can_short": False}},
        ):
            node = create_bear_researcher(_FakeLLM(), _Memory())
            asyncio.run(node(state))

        prompt = captured[0]
        # Must NOT blanket-ban SELL/EXIT (P1 regression guard).
        assert "不要使用 SHORT、SELL、EXIT" not in prompt
        # Must explicitly permit legit long exits.
        assert "卖出" in prompt or "EXIT" in prompt

    def test_no_raw_short_token_and_keep_exits(self):
        from tradingagents.agents.researchers.bear_researcher import create_bear_researcher

        class _Tracker:
            def __init__(self):
                self.tokens = []

            def _emit_token(self, agent, report_type, token):
                self.tokens.append(token)

            def emit_debate_token(self, *, debate, agent, round_num, token):
                self.tokens.append(token)

            def emit_debate_message(self, *, debate, agent, round_num,
                                    content, is_verdict=False):
                pass

        class _FakeLLM:
            async def astream(self, _prompt):
                # Mix short-selling language with a legit long exit.
                for piece in ["看空，", "建议做空策略", "；已持仓可卖出", "，short position"]:
                    yield MagicMock(content=piece)

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
            "volume_price_report": "", "horizon": "medium", "user_intent": {},
        }
        tracker = _Tracker()

        with patch(
            "tradingagents.agents.researchers.bear_researcher.get_config",
            return_value={"account_capability": {"can_short": False}},
        ), patch(
            "tradingagents.agents.researchers.bear_researcher.current_tracker_var"
        ) as ct:
            ct.get.return_value = tracker
            node = create_bear_researcher(_FakeLLM(), _Memory())
            result = asyncio.run(node(state))

        # No raw short keyword in any streamed token.
        for tok in tracker.tokens:
            assert "做空" not in tok
            assert "short" not in tok.lower()
        # Legit exit survived in the debate state.
        raw = result["investment_debate_state"].get("current_response", "")
        assert "卖出" in raw
        assert "做空" not in raw
        assert "short" not in raw.lower()
