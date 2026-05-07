import asyncio
from types import SimpleNamespace

from tradingagents.agents.managers.risk_manager import create_risk_manager
from tradingagents.agents.utils.trade_setup import (
    build_trade_quality_check,
    format_trade_quality_check,
)


def test_trade_quality_check_detects_event_timing_conflict():
    check = build_trade_quality_check(
        investment_plan="业绩超预期，事件前低吸买入。",
        trader_plan="入场区间：28.0-30.0，止损价：26.5。",
        final_decision="只有解禁日平稳度过后再执行买入。",
        user_context={"constraints": ["不追高"]},
    )

    assert "业绩超预期" in check["setup_type"]
    assert "事件驱动" in check["setup_type"]
    assert check["action"] == "等待人工复核"
    assert check["entry_range"] == "28.0-30.0"
    assert check["stop_loss_price"] == 26.5
    assert check["conflicts"]
    assert "执行时点冲突" in check["conflicts"][0]
    assert "执行质检" in format_trade_quality_check(check)


class _FakeLLM:
    async def astream(self, _prompt):
        yield SimpleNamespace(
            content=(
                "最终建议：条件型买入。事件前第一笔建仓，止损价：26.5。"
                "但必须等解禁日平稳度过后再执行。"
                "\n<!-- RISK_JUDGE: {"
                "\"verdict\":\"pass\","
                "\"hard_constraints\":[],"
                "\"soft_constraints\":[],"
                "\"execution_preconditions\":[\"等待触发\"],"
                "\"de_risk_triggers\":[],"
                "\"revision_reason\":\"\""
                "} -->"
            )
        )


class _Memory:
    def get_memories(self, _situation, n_matches=2):
        return []


def _risk_state():
    return {
        "company_of_interest": "301200.SZ",
        "market_report": "技术面报告",
        "sentiment_report": "情绪面报告",
        "news_report": "新闻报告",
        "fundamentals_report": "基本面报告",
        "investment_plan": "多头方案：业绩超预期，事件前低吸。",
        "trader_investment_plan": "入场区间：28.0-30.0，买入；止损价：26.5。",
        "user_context": {
            "objective": "短线波段",
            "constraints": ["不追高"],
        },
        "risk_feedback_state": {"retry_count": 0, "max_retries": 1},
        "risk_debate_state": {
            "history": "",
            "aggressive_history": "",
            "conservative_history": "",
            "neutral_history": "",
            "current_aggressive_response": "",
            "current_conservative_response": "",
            "current_neutral_response": "",
            "count": 0,
            "claims": [],
            "focus_claim_ids": [],
            "open_claim_ids": [],
            "resolved_claim_ids": [],
            "unresolved_claim_ids": [],
            "round_summary": "",
            "round_goal": "",
            "claim_counter": 0,
        },
    }


def test_risk_manager_appends_trade_quality_check_metadata():
    node = create_risk_manager(_FakeLLM(), _Memory())
    result = asyncio.run(node(_risk_state()))

    assert "### 执行质检" in result["final_trade_decision"]
    check = result["metadata"]["trade_quality_check"]
    assert check["action"] == "等待人工复核"
    assert check["conflicts"]
