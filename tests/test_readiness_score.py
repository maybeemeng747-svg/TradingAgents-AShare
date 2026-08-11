"""Tests for readiness_score: D-001 ~ D-004."""
import asyncio
from types import SimpleNamespace

import pytest

from tradingagents.agents.utils.readiness_score import (
    calculate_data_completeness,
    calculate_source_coverage,
    calculate_evidence_coverage,
    EvidenceStatus,
    assess_confidence,
    generate_readiness_score,
    get_position_status,
    get_strong_action_gate,
    calculate_risk_level,
    calculate_buy_level,
    calculate_opportunity_score,
    format_execution_block,
    format_readiness_score,
    sanitize_forbidden_strong_actions,
    infer_evidence_statuses,
    extract_execution_signals,
    validate_stock_name,
    _split_llm_body_and_system_blocks,
    ConfidenceLevel,
)
from tradingagents.agents.managers.risk_manager import (
    create_risk_manager,
    _STRONG_BUY_KEYWORDS,
    _STRONG_SELL_KEYWORDS,
)


# ── Test 1: Only 4 basic reports should give 50%, not be mis-capped ──

def test_four_basic_reports_give_50_percent():
    score = calculate_data_completeness(
        has_market_data=True,
        has_sentiment_data=True,
        has_news_data=True,
        has_fundamentals_data=True,
    )
    assert score == 50


def test_all_eight_sources_give_100_percent():
    score = calculate_data_completeness(
        has_market_data=True,
        has_sentiment_data=True,
        has_news_data=True,
        has_fundamentals_data=True,
        has_smart_money_data=True,
        has_volume_price_data=True,
        has_user_context=True,
        has_position_data=True,
    )
    assert score == 100


# ── Test 2: source_coverage equals data_completeness ──

def test_source_coverage_equals_data_completeness():
    kwargs = dict(
        has_market_data=True,
        has_sentiment_data=True,
        has_news_data=True,
        has_fundamentals_data=True,
        has_smart_money_data=True,
        has_volume_price_data=False,
        has_user_context=False,
        has_position_data=False,
    )
    assert calculate_source_coverage(**kwargs) == calculate_data_completeness(**kwargs)


# ── Test 3: Missing critical evidence → evidence_coverage < 70 ──

def test_missing_evidence_below_70():
    ev = calculate_evidence_coverage(
        ohlcv_5d=EvidenceStatus.HAS_DATA,
        volume=EvidenceStatus.HAS_DATA,
        turnover_rate=EvidenceStatus.NOT_QUERIED,
        volume_ratio=EvidenceStatus.NOT_QUERIED,
        individual_fund_flow=EvidenceStatus.NOT_QUERIED,
        lhb_status=EvidenceStatus.NOT_QUERIED,
        margin_trading=EvidenceStatus.NOT_QUERIED,
        announcements=EvidenceStatus.NOT_QUERIED,
    )
    assert ev < 70


def test_full_evidence_coverage_100():
    ev = calculate_evidence_coverage(
        ohlcv_5d=EvidenceStatus.HAS_DATA,
        volume=EvidenceStatus.HAS_DATA,
        turnover_rate=EvidenceStatus.HAS_DATA,
        volume_ratio=EvidenceStatus.HAS_DATA,
        individual_fund_flow=EvidenceStatus.HAS_DATA,
        lhb_status=EvidenceStatus.HAS_DATA,
        margin_trading=EvidenceStatus.HAS_DATA,
        announcements=EvidenceStatus.HAS_DATA,
        research_report=EvidenceStatus.HAS_DATA,
        ratings=EvidenceStatus.HAS_DATA,
        buybacks=EvidenceStatus.HAS_DATA,
    )
    assert ev == 100


def test_normal_no_data_counts_as_half():
    """NORMAL_NO_DATA counts as 0.5 weight (F-001 fix)."""
    ev = calculate_evidence_coverage(
        ohlcv_5d=EvidenceStatus.NORMAL_NO_DATA,
        volume=EvidenceStatus.NORMAL_NO_DATA,
        turnover_rate=EvidenceStatus.NORMAL_NO_DATA,
        volume_ratio=EvidenceStatus.NORMAL_NO_DATA,
        individual_fund_flow=EvidenceStatus.NORMAL_NO_DATA,
        lhb_status=EvidenceStatus.NORMAL_NO_DATA,
        margin_trading=EvidenceStatus.NORMAL_NO_DATA,
        announcements=EvidenceStatus.NORMAL_NO_DATA,
        research_report=EvidenceStatus.NORMAL_NO_DATA,
        ratings=EvidenceStatus.NORMAL_NO_DATA,
        buybacks=EvidenceStatus.NORMAL_NO_DATA,
    )
    assert ev == 50  # 11 fields * 0.5 weight / 11 = 50%


def test_query_failed_not_counted_as_valid():
    ev = calculate_evidence_coverage(
        ohlcv_5d=EvidenceStatus.HAS_DATA,
        volume=EvidenceStatus.HAS_DATA,
        turnover_rate=EvidenceStatus.HAS_DATA,
        volume_ratio=EvidenceStatus.HAS_DATA,
        individual_fund_flow=EvidenceStatus.HAS_DATA,
        lhb_status=EvidenceStatus.QUERY_FAILED,
        margin_trading=EvidenceStatus.HAS_DATA,
        announcements=EvidenceStatus.HAS_DATA,
        research_report=EvidenceStatus.HAS_DATA,
        ratings=EvidenceStatus.HAS_DATA,
        buybacks=EvidenceStatus.HAS_DATA,
    )
    assert ev == 90  # 10*1.0 + 1*0.0 / 11 = 90.9% → int = 90%


# ── Test 4: source_coverage or evidence_coverage < 70 → strong action blocked ──

def test_strong_action_gate_blocks_on_low_source_coverage():
    gate = get_strong_action_gate(source_coverage=50, evidence_coverage=80)
    assert gate["passed"] is False
    assert any("source_coverage" in f for f in gate["failures"])


def test_strong_action_gate_blocks_on_low_evidence_coverage():
    gate = get_strong_action_gate(source_coverage=80, evidence_coverage=50)
    assert gate["passed"] is False
    assert any("evidence_coverage" in f for f in gate["failures"])


def test_strong_action_gate_blocks_on_unknown_position():
    gate = get_strong_action_gate(
        source_coverage=80, evidence_coverage=80,
        position_status="unknown",
        contains_strong_action=True,
    )
    assert gate["passed"] is False


def test_strong_action_gate_passes_unknown_position_no_strong_action():
    gate = get_strong_action_gate(
        source_coverage=80, evidence_coverage=80,
        position_status="unknown",
        contains_strong_action=False,
    )
    assert gate["passed"] is True


def test_strong_action_gate_passes_when_all_good():
    gate = get_strong_action_gate(
        source_coverage=80, evidence_coverage=80,
        position_status="no_position",
    )
    assert gate["passed"] is True
    assert gate["failures"] == []


def test_strong_action_gate_blocks_on_conflict():
    gate = get_strong_action_gate(
        source_coverage=80, evidence_coverage=80,
        no_unresolved_analyst_conflict=False,
        position_status="has_position",
    )
    assert gate["passed"] is False


# ── Test 5: Risk Level 4 requires all conditions ──

def test_risk_level_4_requires_all_conditions():
    result = calculate_risk_level(
        source_coverage=85, evidence_coverage=85,
        broke_support=True,
        main_capital_outflow_days=2,
        volume_breakdown=True,
        has_major_positive_announcement=False,
        position_status="has_position",
    )
    assert result["level"] == 4


def test_risk_level_4_blocked_by_insufficient_coverage():
    result = calculate_risk_level(
        source_coverage=60, evidence_coverage=85,
        broke_support=True,
        main_capital_outflow_days=2,
        volume_breakdown=True,
        position_status="has_position",
    )
    assert result["level"] < 4
    assert "Risk Level 4 条件证据不足" in result["note"]


def test_risk_level_4_blocked_by_positive_announcement():
    result = calculate_risk_level(
        source_coverage=85, evidence_coverage=85,
        broke_support=True,
        main_capital_outflow_days=2,
        volume_breakdown=True,
        has_major_positive_announcement=True,
        position_status="has_position",
    )
    assert result["level"] < 4


def test_risk_level_capped_at_3_when_position_unknown():
    result = calculate_risk_level(
        source_coverage=85, evidence_coverage=85,
        broke_support=True,
        main_capital_outflow_days=2,
        volume_breakdown=True,
        position_status="unknown",
    )
    assert result["level"] <= 3


def test_risk_level_defaults_to_0():
    result = calculate_risk_level(
        source_coverage=50, evidence_coverage=50,
        position_status="has_position",
    )
    assert result["level"] == 0


# ── Test 6: Buy Level 4 requires all conditions ──

def test_buy_level_4_requires_all_conditions():
    result = calculate_buy_level(
        source_coverage=85, evidence_coverage=85,
        trend_confirmed=True,
        main_capital_inflow_days=2,
        volume_healthy_expansion=True,
        has_major_negative_announcement=False,
        no_execution_conflict=True,
        no_unresolved_analyst_conflict=True,
        position_status="has_position",
    )
    assert result["level"] == 4


def test_buy_level_4_blocked_by_negative_announcement():
    result = calculate_buy_level(
        source_coverage=85, evidence_coverage=85,
        trend_confirmed=True,
        main_capital_inflow_days=2,
        volume_healthy_expansion=True,
        has_major_negative_announcement=True,
        position_status="has_position",
    )
    assert result["level"] < 4


def test_buy_level_4_blocked_by_low_coverage():
    result = calculate_buy_level(
        source_coverage=70, evidence_coverage=85,
        trend_confirmed=True,
        main_capital_inflow_days=2,
        volume_healthy_expansion=True,
        position_status="has_position",
    )
    assert result["level"] < 4


def test_buy_level_max_2_when_position_unknown():
    result = calculate_buy_level(
        source_coverage=85, evidence_coverage=85,
        trend_confirmed=True,
        main_capital_inflow_days=2,
        volume_healthy_expansion=True,
        position_status="unknown",
    )
    assert result["level"] <= 2
    assert "上限为 2" in result["note"]


def test_buy_level_capped_at_observation_without_execution_price():
    result = calculate_buy_level(
        source_coverage=95,
        evidence_coverage=95,
        trend_confirmed=True,
        main_capital_inflow_days=3,
        volume_healthy_expansion=True,
        position_status="has_position",
        execution_price_available=False,
    )

    assert result["level"] == 1
    assert "可执行价格不可用" in result["note"]


def test_buy_level_0_when_coverage_very_low():
    result = calculate_buy_level(
        source_coverage=20, evidence_coverage=20,
        position_status="no_position",
    )
    assert result["level"] == 0


# ── Test 7: No position → no reduce/exit as final action ──

class _FakeLLM:
    async def astream(self, _prompt):
        yield SimpleNamespace(
            content=(
                "建议减仓，止损价：26.5。"
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


def _base_state():
    return {
        "company_of_interest": "301200.SZ",
        "market_report": "技术面报告",
        "sentiment_report": "情绪面报告",
        "news_report": "新闻报告",
        "fundamentals_report": "基本面报告",
        "smart_money_report": "主力资金报告",
        "volume_price_report": "量价报告",
        "investment_plan": "方案",
        "trader_investment_plan": "交易计划",
        "user_context": {
            "objective": "短线",
            "current_position": 0,
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


def test_no_position_blocks_reduce_keywords():
    node = create_risk_manager(_FakeLLM(), _Memory())
    result = asyncio.run(node(_base_state()))
    assert "[C-001]" in result["final_trade_decision"]
    assert result["metadata"]["buy_level"] >= 0


# ── Test 8: Position unknown → Buy Level max 2 ──

def test_position_unknown_caps_buy_level_at_2():
    state = _base_state()
    state["user_context"] = {"objective": "短线"}
    node = create_risk_manager(_FakeLLM(), _Memory())
    result = asyncio.run(node(state))
    assert result["metadata"]["buy_level"] <= 2


# ── Position status helper tests ──

def test_get_position_status_unknown():
    assert get_position_status(None) == "unknown"
    assert get_position_status({}) == "unknown"
    assert get_position_status({"objective": "短线"}) == "unknown"


def test_get_position_status_no_position():
    assert get_position_status({"current_position": 0}) == "no_position"
    assert get_position_status({"current_position": 0.0}) == "no_position"


def test_get_position_status_has_position():
    assert get_position_status({"current_position": 100}) == "has_position"
    assert get_position_status({"current_position": 0.5}) == "has_position"


def test_get_position_status_supports_percentage_only_positions():
    assert get_position_status({"current_position_pct": 30}) == "has_position"
    assert get_position_status({"current_position_pct": 0}) == "no_position"


def test_get_position_status_falls_back_to_position_context():
    assert get_position_status({}, {"has_position": True}) == "has_position"
    assert get_position_status(None, {"has_position": True}) == "has_position"
    assert get_position_status({}, {"has_position": False}) == "no_position"
    assert get_position_status(
        {},
        {"has_position": False, "position_status_explicit": False},
    ) == "unknown"


# ── Opportunity score tests ──

def test_opportunity_score_no_signals():
    assert calculate_opportunity_score() == 8


def test_opportunity_score_all_positive():
    score = calculate_opportunity_score(
        trend_confirmed=True,
        capital_resonance="strong",
        catalyst_strength="strong",
        risk_reward_ratio="favorable",
        entry_quality="good",
        position_status="held",
        data_completeness=100,
    )
    assert score == 100


def test_opportunity_score_event_risk_penalty():
    score = calculate_opportunity_score(
        trend_confirmed=True,
        capital_resonance="strong",
        event_risk_active=True,
    )
    assert score < 50


# ── Format execution block test ──

def test_format_execution_block():
    block = format_execution_block(
        source_coverage=80,
        evidence_coverage=75,
        confidence="高",
        opportunity_score=65,
        risk_level=1,
        buy_level=2,
        risk_level_note="",
        buy_level_note="",
        strong_action_gate={"passed": True, "failures": []},
        position_status="has_position",
    )
    assert "Source Coverage：80%" in block
    assert "Evidence Coverage：75%" in block
    assert "Buy Level：Buy Level 2" in block
    assert "Risk Level：Risk Level 1" in block
    assert "Strong Action Gate：通过" in block


def test_format_execution_block_with_failures():
    gate = {"passed": False, "failures": ["source_coverage=60% < 70%"]}
    block = format_execution_block(
        source_coverage=60,
        evidence_coverage=80,
        confidence="中",
        opportunity_score=40,
        risk_level=0,
        buy_level=1,
        strong_action_gate=gate,
        position_status="unknown",
    )
    assert "Strong Action Gate：未通过" in block
    assert "source_coverage=60% < 70%" in block
    assert "持仓状态未知" in block


def test_fundamental_integrity_gate_caps_confidence_at_medium():
    conf = assess_confidence(
        data_completeness=95,
        evidence_coverage=95,
        fundamental_integrity_valid=False,
    )

    assert conf == ConfidenceLevel.MEDIUM


def test_readiness_blocker_downgrades_action_policy():
    score = generate_readiness_score(
        87,
        ConfidenceLevel.MEDIUM,
        blockers=["fundamental_semantic_gate"],
    )

    block = format_readiness_score(score)

    assert "数据完整度：87%" in block
    assert "置信度：中" in block
    assert "中等质量报告" in block
    assert "禁止动作：加仓, 追涨" in block
    assert "置信度降级原因：fundamental_semantic_gate" in block


def test_execution_block_marks_fundamental_review_state():
    block = format_execution_block(
        source_coverage=87,
        evidence_coverage=94,
        confidence="中",
        opportunity_score=60,
        risk_level=1,
        buy_level=1,
        strong_action_gate={
            "passed": False,
            "failures": ["fundamental_semantic_gate"],
        },
        fundamental_integrity_valid=False,
    )

    assert "基本面语义门禁未通过，中线判断仅供人工复核" in block
    assert "数据支撑充分，中线判断可信" not in block


def test_fundamental_veto_removes_ordinary_entry_but_preserves_exit():
    sanitized, changes = sanitize_forbidden_strong_actions(
        "建议买入并建立试探仓，条件试仓或采用试探性轻仓策略；"
        "也不能逢低买入或低吸建仓；已有持仓可减仓退出。",
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="has_position",
        buy_level=0,
        risk_level=2,
    )

    assert "建议买入" not in sanitized
    assert "建立试探仓" not in sanitized
    assert "条件试仓" not in sanitized
    assert "试探性轻仓" not in sanitized
    assert "不能逢低买入或低吸建仓" in sanitized
    assert "等待基本面证据复核" in sanitized
    assert "减仓退出" in sanitized
    assert changes


def test_fundamental_veto_preserves_negative_guardrail_text():
    guardrail = (
        "📋 执行就绪度评分\n"
        "- 禁止动作：买入, 加仓, 追涨\n"
        "- 总结：禁止买入/加仓/追涨。建议继续观察。"
    )

    sanitized, changes = sanitize_forbidden_strong_actions(
        guardrail,
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="no_position",
        buy_level=0,
        risk_level=0,
    )

    assert "禁止动作：买入, 加仓, 追涨" in sanitized
    assert "禁止买入/加仓/追涨" in sanitized
    assert "禁止等待基本面证据复核" not in sanitized
    assert not changes


def test_fundamental_veto_only_rewrites_positive_clause_on_mixed_line():
    sanitized, changes = sanitize_forbidden_strong_actions(
        "禁止追涨，但可以轻仓试多。",
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="no_position",
        buy_level=0,
        risk_level=0,
    )

    assert "禁止追涨" in sanitized
    assert "轻仓试多" not in sanitized
    assert "等待基本面证据复核" in sanitized
    assert changes


def test_fundamental_veto_preserves_size_modified_negated_actions():
    original = (
        "不允许重仓买入。禁止加仓买入。不得追涨买入。"
        "不允许提高买入额度。禁止设置加仓比例。"
    )
    sanitized, _ = sanitize_forbidden_strong_actions(
        original,
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="no_position",
        buy_level=0,
        risk_level=0,
    )

    assert original in sanitized


def test_fundamental_veto_preserves_location_modified_negated_actions():
    original = (
        "不建议在当前位置重仓买入。"
        "禁止在这个价位立即建仓。"
        "不适合以当前价格加仓。"
    )
    sanitized, changes = sanitize_forbidden_strong_actions(
        original,
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="no_position",
        buy_level=0,
        risk_level=0,
    )

    assert original in sanitized
    assert changes == []


# ══════════════════════════════════════════════════════════════════════════════
# P1: sanitize_forbidden_strong_actions tests
# ══════════════════════════════════════════════════════════════════════════════

def test_sanitize_removes_liquidate_when_gate_fails():
    text = "建议立即清仓，避免进一步亏损。### 执行等级与证据门禁\n- foo"
    gate = {"passed": False, "failures": ["source_coverage=60% < 70%"]}
    result, changes = sanitize_forbidden_strong_actions(
        text, gate, "has_position", 0, 3,
    )
    assert "立即清仓" not in result
    assert "等待触发条件" in result
    assert len(changes) > 0


def test_sanitize_removes_strong_buy_when_gate_fails():
    text = "建议重仓买入，建议追涨。### 执行等级与证据门禁\n- foo"
    gate = {"passed": False, "failures": ["evidence_coverage=50% < 70%"]}
    result, changes = sanitize_forbidden_strong_actions(
        text, gate, "no_position", 1, 0,
    )
    assert "重仓买入" not in result
    assert "建议追涨" not in result
    assert "暂不执行强买入" in result


def test_sanitize_no_position_removes_sell_actions():
    text = "建议减仓、清仓、止盈、止损、卖出。### 执行等级与证据门禁\n- foo"
    gate = {"passed": False, "failures": ["持仓状态未知"]}
    result, changes = sanitize_forbidden_strong_actions(
        text, gate, "no_position", 0, 0,
    )
    for kw in ["减仓", "清仓", "止盈", "止损", "卖出"]:
        assert kw not in result, f"未移除: {kw}"
    assert "未持仓" in result


def test_sanitize_noop_when_gate_passes():
    text = "建议重仓买入，立即清仓。### 执行等级与证据门禁\n- foo"
    gate = {"passed": True, "failures": []}
    result, changes = sanitize_forbidden_strong_actions(
        text, gate, "has_position", 3, 2,
    )
    assert "重仓买入" in result
    assert "立即清仓" in result
    assert changes == []


def test_sanitize_warning_not_coexisting_with_original_strong():
    text = "建议立即清仓。### 执行等级与证据门禁\n- foo"
    gate = {"passed": False, "failures": ["source_coverage=60% < 70%"]}
    result, changes = sanitize_forbidden_strong_actions(
        text, gate, "has_position", 0, 3,
    )
    assert "立即清仓" not in result
    assert "[D-002]" in result


def test_sanitize_preserves_execution_block():
    text = "建议立即清仓。### 执行等级与证据门禁\n- Source Coverage：60%"
    gate = {"passed": False, "failures": ["test"]}
    result, _ = sanitize_forbidden_strong_actions(
        text, gate, "has_position", 0, 3,
    )
    assert "### 执行等级与证据门禁" in result
    assert "Source Coverage：60%" in result


# ══════════════════════════════════════════════════════════════════════════════
# P1: infer_evidence_statuses tests
# ══════════════════════════════════════════════════════════════════════════════

def test_evidence_plain_text_no_ohlc_is_field_missing():
    statuses = infer_evidence_statuses({
        "market_report": "今天市场整体表现一般，没有具体数据。",
    })
    assert statuses["ohlcv_5d"] == EvidenceStatus.FIELD_MISSING


def test_evidence_ohlc_table_gives_has_data():
    statuses = infer_evidence_statuses({
        "market_report": "最近5日数据：\n日期       | 开盘价 | 最高价 | 最低价 | 收盘价\n2026-05-09 | 25.30  | 26.10  | 25.00  | 25.80",
    })
    assert statuses["ohlcv_5d"] == EvidenceStatus.HAS_DATA


def test_evidence_lhb_normal_no_trigger():
    statuses = infer_evidence_statuses({
        "market_report": "龙虎榜无触发，今日未上榜属正常。",
        "smart_money_report": "主力资金报告",
    })
    assert statuses["lhb_status"] == EvidenceStatus.NORMAL_NO_DATA


def test_evidence_lhb_query_failed():
    statuses = infer_evidence_statuses({
        "market_report": "龙虎榜获取失败，API错误。",
        "smart_money_report": "主力资金报告",
    })
    assert statuses["lhb_status"] == EvidenceStatus.QUERY_FAILED


def test_evidence_margin_trading_not_queried_by_default():
    statuses = infer_evidence_statuses({
        "market_report": "some report",
        "smart_money_report": "some report",
    })
    assert statuses["margin_trading"] == EvidenceStatus.NOT_QUERIED


def test_evidence_no_report_means_not_queried():
    statuses = infer_evidence_statuses({})
    assert statuses["ohlcv_5d"] == EvidenceStatus.NOT_QUERIED
    assert statuses["volume"] == EvidenceStatus.NOT_QUERIED
    assert statuses["margin_trading"] == EvidenceStatus.NOT_QUERIED


def test_evidence_coverage_not_inflated_by_report_existence():
    statuses = infer_evidence_statuses({
        "market_report": "市场报告文字",
        "volume_price_report": "量价报告文字",
        "smart_money_report": "主力资金报告文字",
        "news_report": "新闻报告文字",
    })
    ev = calculate_evidence_coverage(**statuses)
    assert ev < 70


def test_evidence_announcement_with_date_is_has_data():
    statuses = infer_evidence_statuses({
        "news_report": "公告：2026-05-10 发布重大事项公告，原文内容如下...",
    })
    assert statuses["announcements"] == EvidenceStatus.HAS_DATA


# ══════════════════════════════════════════════════════════════════════════════
# P1: extract_execution_signals + Buy/Risk/Opportunity integration tests
# ══════════════════════════════════════════════════════════════════════════════

def test_no_position_positive_signals_cap_at_buy_level_2():
    signals = extract_execution_signals(
        {},
        "",
        {
            "market_report": "趋势确认，多头排列，量能健康放大。",
            "smart_money_report": "连续3日主力净流入，大单持续买入。",
        },
    )
    result = calculate_buy_level(
        source_coverage=85,
        evidence_coverage=85,
        trend_confirmed=signals["trend_confirmed"],
        main_capital_inflow_days=signals["main_capital_inflow_days"],
        volume_healthy_expansion=signals["volume_healthy_expansion"],
        position_status="no_position",
    )
    assert result["level"] == 2


def test_risk_level_4_with_negative_signals():
    signals = extract_execution_signals(
        {},
        "",
        {
            "market_report": "跌破关键支撑位，放量破位下挫。",
            "smart_money_report": "连续2日主力净流出，资金大幅撤离。",
        },
    )
    result = calculate_risk_level(
        source_coverage=85,
        evidence_coverage=85,
        broke_support=signals["broke_support"],
        main_capital_outflow_days=signals["main_capital_outflow_days"],
        volume_breakdown=signals["volume_breakdown"],
        position_status="has_position",
    )
    assert result["level"] >= 3


def test_strong_gate_fails_on_execution_conflict():
    signals = extract_execution_signals(
        {},
        "入场价高于止盈价，止损价缺失，字段冲突。",
        {"market_report": "报告"},
    )
    gate = get_strong_action_gate(
        source_coverage=85,
        evidence_coverage=85,
        no_execution_field_conflict=signals["no_execution_conflict"],
        position_status="no_position",
    )
    assert gate["passed"] is False


def test_opportunity_score_higher_with_positive_signals():
    signals = extract_execution_signals(
        {},
        "",
        {
            "market_report": "趋势确认，多头排列，量能健康放大。",
            "smart_money_report": "主力资金连续流入，多维度共振强。",
        },
    )
    score = calculate_opportunity_score(
        trend_confirmed=signals["trend_confirmed"],
        capital_resonance=signals["capital_resonance"],
        catalyst_strength=signals["catalyst_strength"],
    )
    assert score > 25


def test_opportunity_score_event_risk_penalty():
    signals = extract_execution_signals(
        {},
        "",
        {"market_report": "趋势确认"},
    )
    score_normal = calculate_opportunity_score(
        trend_confirmed=signals["trend_confirmed"],
        event_risk_active=False,
    )
    score_risk = calculate_opportunity_score(
        trend_confirmed=signals["trend_confirmed"],
        event_risk_active=True,
    )
    assert score_risk < score_normal


# ══════════════════════════════════════════════════════════════════════════════
# P2: Strong Action Gate unknown position semantic tests
# ══════════════════════════════════════════════════════════════════════════════

def test_gate_passes_unknown_position_without_strong_action():
    gate = get_strong_action_gate(
        source_coverage=80, evidence_coverage=80,
        position_status="unknown",
        contains_strong_action=False,
    )
    assert gate["passed"] is True


def test_gate_blocks_unknown_position_with_strong_buy():
    gate = get_strong_action_gate(
        source_coverage=80, evidence_coverage=80,
        position_status="unknown",
        contains_strong_action=True,
    )
    assert gate["passed"] is False
    assert any("持仓状态未知" in f for f in gate["failures"])


def test_buy_level_capped_at_2_when_unknown():
    result = calculate_buy_level(
        source_coverage=85, evidence_coverage=85,
        trend_confirmed=True,
        main_capital_inflow_days=3,
        volume_healthy_expansion=True,
        position_status="unknown",
    )
    assert result["level"] <= 2


def test_risk_level_capped_at_3_when_unknown():
    result = calculate_risk_level(
        source_coverage=85, evidence_coverage=85,
        broke_support=True,
        main_capital_outflow_days=3,
        volume_breakdown=True,
        position_status="unknown",
    )
    assert result["level"] <= 3


# ══════════════════════════════════════════════════════════════════════════════
# FIX2-1: sanitizer 不误改"禁止加仓" + 系统区块保护
# ══════════════════════════════════════════════════════════════════════════════

def test_sanitize_preserves_禁止加仓():
    text = "当前市场不稳定，禁止加仓，保持观望。📊 数据源可用性：\n  ✅ 市场"
    gate = {"passed": False, "failures": ["test"]}
    result, changes = sanitize_forbidden_strong_actions(
        text, gate, "has_position", 0, 0,
    )
    assert "禁止加仓" in result
    assert "暂不执行强买入" not in result


def test_sanitize_replaces_建议加仓():
    text = "当前趋势向好，建议加仓。📊 数据源可用性：\n  ✅ 市场"
    gate = {"passed": False, "failures": ["test"]}
    result, changes = sanitize_forbidden_strong_actions(
        text, gate, "has_position", 0, 0,
    )
    assert "建议加仓" not in result
    assert "暂不执行强买入" in result


def test_sanitize_preserves_system_block_加仓():
    text = "LLM body here。📊 数据源可用性：\n禁止加仓信息"
    gate = {"passed": False, "failures": ["test"]}
    result, changes = sanitize_forbidden_strong_actions(
        text, gate, "has_position", 0, 0,
    )
    assert "📊 数据源可用性：" in result


def test_contains_strong_禁止加仓_is_false():
    assert not any(kw in "禁止加仓" for kw in _STRONG_BUY_KEYWORDS)


def test_contains_strong_建议加仓_is_true():
    assert any(kw in "建议加仓" for kw in _STRONG_BUY_KEYWORDS)


# ══════════════════════════════════════════════════════════════════════════════
# FIX2-2: 强动作同义词扩展测试
# ══════════════════════════════════════════════════════════════════════════════

def test_sanitize_replaces_立刻清仓():
    text = "建议立刻清仓。📊 数据源可用性：\n  ✅ 市场"
    gate = {"passed": False, "failures": ["test"]}
    result, changes = sanitize_forbidden_strong_actions(
        text, gate, "has_position", 0, 0,
    )
    assert "立刻清仓" not in result
    assert "等待触发条件" in result


def test_sanitize_replaces_清仓离场():
    text = "建议清仓离场。📊 数据源可用性：\n  ✅ 市场"
    gate = {"passed": False, "failures": ["test"]}
    result, changes = sanitize_forbidden_strong_actions(
        text, gate, "has_position", 0, 0,
    )
    assert "清仓离场" not in result
    assert "等待触发条件" in result


def test_sanitize_replaces_满仓买入():
    text = "建议满仓买入。📊 数据源可用性：\n  ✅ 市场"
    gate = {"passed": False, "failures": ["test"]}
    result, changes = sanitize_forbidden_strong_actions(
        text, gate, "has_position", 0, 0,
    )
    assert "满仓买入" not in result
    assert "暂不执行强买入" in result


def test_sanitize_replaces_重仓布局():
    text = "建议重仓布局。📊 数据源可用性：\n  ✅ 市场"
    gate = {"passed": False, "failures": ["test"]}
    result, changes = sanitize_forbidden_strong_actions(
        text, gate, "has_position", 0, 0,
    )
    assert "重仓布局" not in result
    assert "暂不执行强买入" in result


def test_sanitize_preserves_减仓():
    text = "建议适度减仓，控制风险。📊 数据源可用性：\n  ✅ 市场"
    gate = {"passed": False, "failures": ["test"]}
    result, changes = sanitize_forbidden_strong_actions(
        text, gate, "has_position", 0, 0,
    )
    assert "减仓" in result


def test_sanitize_preserves_卖出():
    text = "可考虑分批卖出。📊 数据源可用性：\n  ✅ 市场"
    gate = {"passed": False, "failures": ["test"]}
    result, changes = sanitize_forbidden_strong_actions(
        text, gate, "has_position", 0, 0,
    )
    assert "卖出" in result


# ══════════════════════════════════════════════════════════════════════════════
# FIX2-3: 龙虎榜状态判断顺序修复测试
# ══════════════════════════════════════════════════════════════════════════════

def test_lhb_query_failed_with_no_data_returns_query_failed():
    statuses = infer_evidence_statuses({
        "market_report": "龙虎榜查询失败，无数据。",
        "smart_money_report": "主力资金报告",
    })
    assert statuses["lhb_status"] == EvidenceStatus.QUERY_FAILED


def test_lhb_normal_no_trigger_still_works():
    statuses = infer_evidence_statuses({
        "market_report": "龙虎榜无触发，属正常。",
        "smart_money_report": "主力资金报告",
    })
    assert statuses["lhb_status"] == EvidenceStatus.NORMAL_NO_DATA


# ══════════════════════════════════════════════════════════════════════════════
# FIX3-1: 未持仓动作清理独立于 gate
# ══════════════════════════════════════════════════════════════════════════════

def test_no_position_减仓_replaced_when_gate_passes():
    text = "建议适度减仓控制风险。📊 数据源可用性：\n  ✅ 市场"
    gate = {"passed": True, "failures": []}
    result, changes = sanitize_forbidden_strong_actions(
        text, gate, "no_position", 0, 0,
    )
    assert "减仓" not in result
    assert "未持仓" in result
    assert len(changes) > 0


def test_no_position_止损_replaced_when_gate_passes():
    text = "建议止损离场。📊 数据源可用性：\n  ✅ 市场"
    gate = {"passed": True, "failures": []}
    result, changes = sanitize_forbidden_strong_actions(
        text, gate, "no_position", 0, 0,
    )
    assert "止损" not in result
    assert "未持仓" in result
    assert len(changes) > 0


def test_no_position_sanitizer_does_not_consume_risk_clause_prefix():
    text = (
        "左侧挂单止损条件失效时，仅记录风险，不代表已有持仓。"
        "未来减仓上限仅适用于建仓后的情景。"
    )
    gate = {"passed": True, "failures": []}

    result, _ = sanitize_forbidden_strong_actions(
        text, gate, "no_position", 0, 0,
    )

    assert "左侧挂单止损条件失效" in result
    assert "未来减仓上限" in result
    assert "左侧挂单该持仓动作" not in result


def test_no_position_sanitizer_preserves_negated_action_guardrails():
    for text in (
        "建议不要减仓，继续观察",
        "暂不减仓，等待确认",
        "当前不需要减仓",
        "不要立即买入，先观察",
        "暂不立即买入，先观察",
    ):
        gate = {"passed": False, "failures": ["source_coverage=50% < 70%"]}

        result, changes = sanitize_forbidden_strong_actions(
            text, gate, "no_position", 0, 0,
        )

        assert text in result
        assert "不要该持仓动作" not in result
        assert "暂不该持仓动作" not in result
        assert "暂不暂不执行" not in result
        assert changes == []


def test_no_position_sanitizer_removes_modified_and_conditional_sell_actions():
    text = (
        "建议小幅减仓控制风险。跌破支撑则减仓。"
        "跌破支撑后小幅减仓。可以小幅减仓。"
        "跌破支撑就减仓。考虑减仓。未来减仓上限为20%。"
    )
    gate = {"passed": True, "failures": []}

    result, changes = sanitize_forbidden_strong_actions(
        text, gate, "no_position", 0, 0,
    )

    assert "建议小幅减仓" not in result
    assert "则减仓" not in result
    assert "后小幅减仓" not in result
    assert "可以小幅减仓" not in result
    assert "就减仓" not in result
    assert "考虑减仓" not in result
    assert "跌破支撑" in result
    assert "未来减仓上限" in result
    assert changes


def test_fundamental_sanitizer_preserves_limits_and_unlock_language():
    text = (
        "未来加仓上限为10%。解除加仓禁令前不得执行买入。"
        "若证据补齐，建议建仓。"
    )
    gate = {"passed": False, "failures": ["fundamental_semantic_gate"]}

    result, _ = sanitize_forbidden_strong_actions(
        text, gate, "has_position", 0, 0,
    )

    assert "未来加仓上限为10%" in result
    assert "解除加仓禁令前不得执行买入" in result
    assert "建议建仓" not in result
    assert "等待基本面证据复核" in result


def test_fundamental_sanitizer_filters_buy_after_unlock_clause():
    text = "解除加仓禁令后建议买入。维持加仓上限为10%。"
    gate = {"passed": False, "failures": ["fundamental_semantic_gate"]}

    result, changes = sanitize_forbidden_strong_actions(
        text, gate, "no_position", 0, 0,
    )

    assert "建议买入" not in result
    assert "解除加仓禁令后" in result
    assert "维持加仓上限" in result
    assert "等待基本面证据复核" in result
    assert changes


def test_fundamental_sanitizer_filters_executable_buy_size_but_keeps_field():
    text = (
        "执行买入额度10万元。可买入比例不超过10%。"
        "买入额度字段用于记录约束。"
    )
    gate = {"passed": False, "failures": ["fundamental_semantic_gate"]}

    result, changes = sanitize_forbidden_strong_actions(
        text, gate, "no_position", 0, 0,
    )

    assert "执行买入额度" not in result
    assert "可买入比例" not in result
    assert "买入额度字段用于记录约束" in result
    assert changes


def test_fundamental_sanitizer_filters_unprefixed_and_recommended_buy_sizes():
    text = (
        "推荐买入比例为10%。建仓额度10万元。"
        "买入比例字段用于记录约束。建仓额度字段不得作为动作。"
    )
    gate = {"passed": False, "failures": ["fundamental_semantic_gate"]}

    result, changes = sanitize_forbidden_strong_actions(
        text, gate, "no_position", 0, 0,
    )

    assert "推荐买入比例为10%" not in result
    assert "建仓额度10万元" not in result
    assert "买入比例字段用于记录约束" in result
    assert "建仓额度字段不得作为动作" in result
    assert changes


def test_fundamental_sanitizer_preserves_negated_buy_constraints():
    text = "不允许买入比例超过10%。不推荐建仓额度。"
    gate = {"passed": False, "failures": ["fundamental_semantic_gate"]}

    result, changes = sanitize_forbidden_strong_actions(
        text, gate, "no_position", 0, 0,
    )

    assert "不允许买入比例超过10%" in result
    assert "不推荐建仓额度" in result
    assert "不等待" not in result
    assert changes == []


def test_fundamental_sanitizer_scopes_negation_across_contrast_clause():
    text = "不推荐追涨但建议建仓。不允许加仓但可买入。"
    gate = {"passed": False, "failures": ["fundamental_semantic_gate"]}

    result, changes = sanitize_forbidden_strong_actions(
        text, gate, "no_position", 0, 0,
    )

    assert "不推荐追涨" in result
    assert "不允许加仓" in result
    assert "建议建仓" not in result
    assert "可买入" not in result
    body = result.split("⚠️ [D-002]", 1)[0]
    assert body.count("等待基本面证据复核") == 2
    assert changes


def test_fundamental_sanitizer_scopes_negation_across_er_contrast():
    text = "未允许加仓而后续建议买入。"
    gate = {"passed": False, "failures": ["fundamental_semantic_gate"]}

    result, changes = sanitize_forbidden_strong_actions(
        text, gate, "no_position", 0, 0,
    )

    assert "未允许加仓" in result
    assert "建议买入" not in result
    assert "等待基本面证据复核" in result
    assert changes


def test_fundamental_sanitizer_scopes_negation_across_bare_and():
    text = "不推荐追涨并建议建仓。未允许加仓并建议买入。"
    gate = {"passed": False, "failures": ["fundamental_semantic_gate"]}

    result, changes = sanitize_forbidden_strong_actions(
        text, gate, "no_position", 0, 0,
    )

    assert "不推荐追涨" in result
    assert "未允许加仓" in result
    assert "建议建仓" not in result
    assert "建议买入" not in result
    body = result.split("⚠️ [D-002]", 1)[0]
    assert body.count("等待基本面证据复核") == 2
    assert changes


def test_fundamental_sanitizer_scopes_negation_across_choice_delimiters():
    gate = {"passed": False, "failures": ["fundamental_semantic_gate"]}
    cases = (
        ("不允许追涨、建议逢低建仓。", "建议逢低建仓"),
        ("未推荐加仓、建议买入。", "建议买入"),
        ("不推荐追涨或建议建仓。", "建议建仓"),
        ("禁止加仓、可以买入。", "可以买入"),
    )

    for text, forbidden in cases:
        result, changes = sanitize_forbidden_strong_actions(
            text, gate, "no_position", 0, 0,
        )
        assert changes
        assert forbidden not in result
        assert "等待基本面证据复核" in result


def test_strong_buy_sanitizer_scans_after_negated_clause():
    gate = {"passed": False, "failures": ["source_coverage_below_70"]}

    result, changes = sanitize_forbidden_strong_actions(
        "不建议追涨所以建议加仓。", gate, "no_position", 0, 0,
    )

    assert "不建议追涨" in result
    assert "建议加仓" not in result
    assert changes


def test_no_position_sanitizer_scans_after_negated_clause():
    gate = {"passed": True, "failures": []}

    result, changes = sanitize_forbidden_strong_actions(
        "尚未买入因此建议卖出。", gate, "no_position", 0, 0,
    )

    assert "尚未买入" in result
    assert "建议卖出" not in result
    assert changes


def test_entry_sanitizer_preserves_fully_negated_buy():
    gate = {
        "passed": False,
        "failures": ["估值基准价不可用(valuation_price_unavailable)"],
    }

    for text in ("并非建议买入。", "不是建议买入。"):
        result, changes = sanitize_forbidden_strong_actions(
            text, gate, "no_position", 0, 0,
        )
        assert text.strip("。") in result
        assert "并非等待" not in result
        assert "不是等待" not in result
        assert changes == []


def test_entry_and_no_position_sanitizers_filter_minimum_position_sizes():
    entry_gate = {
        "passed": False,
        "failures": ["fundamental_semantic_gate"],
    }
    entry_result, entry_changes = sanitize_forbidden_strong_actions(
        "买入比例至少50%。", entry_gate, "no_position", 0, 0,
    )
    sell_result, sell_changes = sanitize_forbidden_strong_actions(
        "减仓比例不低于三成。", {"passed": True, "failures": []},
        "no_position", 0, 0,
    )

    assert "买入比例至少50%" not in entry_result
    assert "减仓比例不低于三成" not in sell_result
    assert entry_changes
    assert sell_changes


def test_sanitizers_filter_positive_action_before_later_negation():
    gate = {"passed": False, "failures": ["source_coverage_below_70"]}

    result, changes = sanitize_forbidden_strong_actions(
        "建议立即买入（不建议追涨）。", gate, "no_position", 0, 0,
    )

    assert "建议立即买入" not in result
    assert "不建议追涨" in result
    assert changes


def test_fundamental_sanitizer_filters_mediated_buy_sizing():
    text = "建议将买入比例提高至10%。建议把建仓额度提高到10万元。"
    gate = {"passed": False, "failures": ["fundamental_semantic_gate"]}

    result, changes = sanitize_forbidden_strong_actions(
        text, gate, "no_position", 0, 0,
    )

    body = result.split("⚠️ [D-002]", 1)[0]
    assert "建议将买入比例提高至10%" not in body
    assert "建议把建仓额度提高到10万元" not in body
    assert body.count("等待基本面证据复核") == 2
    assert changes


def test_fundamental_sanitizer_filters_chinese_buy_sizing():
    text = "建议将买入比例提高至一成。买入比例控制在半仓。"
    gate = {"passed": False, "failures": ["fundamental_semantic_gate"]}

    result, changes = sanitize_forbidden_strong_actions(
        text, gate, "no_position", 0, 0,
    )

    body = result.split("⚠️ [D-002]", 1)[0]
    assert "买入比例提高至一成" not in body
    assert "买入比例控制在半仓" not in body
    assert "等待基本面证据复核成" not in body
    assert "等待基本面证据复核仓" not in body
    assert body.count("等待基本面证据复核") == 2
    assert changes


def test_fundamental_sanitizer_filters_unsized_adjusted_buy_allocation():
    text = "建议调整加仓比例，待风险解除后执行。"
    gate = {"passed": False, "failures": ["fundamental_semantic_gate"]}

    result, changes = sanitize_forbidden_strong_actions(
        text, gate, "no_position", 0, 0,
    )

    body = result.split("⚠️ [D-002]", 1)[0]
    assert "调整加仓比例" not in body
    assert "等待基本面证据复核" in body
    assert changes


def test_fundamental_sanitizer_filters_noun_first_buy_allocation():
    text = "买入比例建议为50%。建仓比例建议控制在20%。"
    gate = {"passed": False, "failures": ["fundamental_semantic_gate"]}

    result, changes = sanitize_forbidden_strong_actions(
        text, gate, "no_position", 0, 0,
    )

    body = result.split("⚠️ [D-002]", 1)[0]
    assert "买入比例建议为50%" not in body
    assert "建仓比例建议控制在20%" not in body
    assert body.count("等待基本面证据复核") == 2
    assert changes


def test_no_position_sanitizer_consumes_executable_sell_size():
    text = "建议减仓比例为50%。可卖出比例为30%。"
    gate = {"passed": True, "failures": []}

    result, changes = sanitize_forbidden_strong_actions(
        text, gate, "no_position", 0, 0,
    )

    assert "减仓比例" not in result
    assert "卖出比例" not in result
    assert "比例为50%" not in result
    assert "比例为30%" not in result
    assert changes


def test_no_position_sanitizer_filters_unsized_adjusted_sell_allocation():
    text = "建议动态调整卖出比例以控制风险。"
    gate = {"passed": True, "failures": []}

    result, changes = sanitize_forbidden_strong_actions(
        text, gate, "no_position", 0, 0,
    )

    assert "调整卖出比例" not in result
    assert "该持仓动作不适用" in result
    assert changes


def test_no_position_sanitizer_filters_noun_first_sell_allocation():
    text = "卖出比例建议为50%。减仓比例建议控制在20%。"
    gate = {"passed": True, "failures": []}

    result, changes = sanitize_forbidden_strong_actions(
        text, gate, "no_position", 0, 0,
    )

    assert "卖出比例建议为50%" not in result
    assert "减仓比例建议控制在20%" not in result
    body = result.split("⚠️ [D-002]", 1)[0]
    assert body.count("该持仓动作不适用") == 2
    assert changes


def test_no_position_sanitizer_preserves_negated_sell_guardrails():
    gate = {"passed": True, "failures": []}
    cases = (
        "不建议减仓，继续观察。",
        "禁止卖出比例超过30%。",
        "不允许清仓，等待进一步确认。",
        "无需止损，仅记录假设止损位。",
    )

    for text in cases:
        result, changes = sanitize_forbidden_strong_actions(
            text, gate, "no_position", 0, 0,
        )
        assert result == text
        assert changes == []


def test_no_position_sanitizer_downgrades_positive_double_negation_actions():
    gate = {"passed": True, "failures": []}
    for text in (
        "不是不能减仓",
        "并不是不建议清仓",
        "不是不需要清仓",
        "并非不适合卖出",
        "不是无需止损",
        "不能不卖出",
        "没有不止盈",
    ):
        result, changes = sanitize_forbidden_strong_actions(
            text, gate, "no_position", 1, 1,
        )
        assert "该持仓动作不适用，保持观察" in result
        assert changes


def test_strong_buy_sanitizer_downgrades_positive_double_negation_actions():
    gate = {"passed": False, "failures": ["data_completeness<70%"]}
    for text in (
        "不是不能重仓买入",
        "并非不建议加仓",
        "不是不需要重仓买入",
        "并非不适合立即买入",
        "不是无需加仓",
        "不能不立即买入",
    ):
        result, changes = sanitize_forbidden_strong_actions(
            text, gate, "no_position", 0, 0,
        )
        body = result.split("⚠️ [D-002]", 1)[0]
        assert body.strip() == "暂不执行强买入，等待条件确认"
        assert changes


def test_entry_gates_downgrade_positive_double_negation_actions():
    cases = (
        ("fundamental_semantic_gate", "等待基本面证据复核"),
        ("event_risk_block_open", "等待风险解除"),
        (
            "估值基准价不可用(valuation_price_unavailable)",
            "等待可执行价格确认",
        ),
    )
    actions = (
        "并非不能建仓",
        "不是不能买入",
        "并非不建议建仓",
        "不是不可以买入",
        "并非不适合入场",
        "不能不买入",
    )

    for failure, replacement in cases:
        for action in actions:
            result, changes = sanitize_forbidden_strong_actions(
                action,
                gate={"passed": False, "failures": [failure]},
                position_status="no_position",
                buy_level=0,
                risk_level=0,
            )
            body = result.split("⚠️ [D-002]", 1)[0]
            assert body.strip() == replacement
            assert changes


def test_no_position_sanitizer_scopes_negation_before_positive_sell_action():
    gate = {"passed": True, "failures": []}
    cases = (
        ("不建议减仓但建议卖出。", "不建议减仓", "建议卖出"),
        ("禁止清仓、可以减仓。", "禁止清仓", "可以减仓"),
        ("不允许止损或建议卖出。", "不允许止损", "建议卖出"),
    )

    for text, preserved, removed in cases:
        result, changes = sanitize_forbidden_strong_actions(
            text, gate, "no_position", 0, 0,
        )
        assert preserved in result
        assert removed not in result
        assert "该持仓动作不适用" in result
        assert changes


def test_no_position_sanitizer_consumes_mediated_sell_size():
    text = "建议将减仓比例提高至50%。建议把卖出额度调整到10万元。"
    gate = {"passed": True, "failures": []}

    result, changes = sanitize_forbidden_strong_actions(
        text, gate, "no_position", 0, 0,
    )

    assert "建议将减仓比例提高至50%" not in result
    assert "建议把卖出额度调整到10万元" not in result
    assert "比例提高至50%" not in result
    assert "额度调整到10万元" not in result
    assert changes


def test_no_position_sanitizer_consumes_chinese_sell_size():
    text = "建议将减仓比例提高至一半。卖出额度控制在十万元。"
    gate = {"passed": True, "failures": []}

    result, changes = sanitize_forbidden_strong_actions(
        text, gate, "no_position", 0, 0,
    )

    assert "减仓比例提高至一半" not in result
    assert "卖出额度控制在十万元" not in result
    assert "该持仓动作不适用，保持观察半" not in result
    assert "该持仓动作不适用，保持观察元" not in result
    assert result.count("该持仓动作不适用") >= 2
    assert changes


def test_no_position_sanitizer_consumes_unprefixed_sell_size():
    text = (
        "减仓比例为50%。卖出额度10万元。清仓比例设置为100%。"
        "减仓比例字段用于记录约束。"
    )
    gate = {"passed": True, "failures": []}

    result, changes = sanitize_forbidden_strong_actions(
        text, gate, "no_position", 0, 0,
    )

    assert "减仓比例为50%" not in result
    assert "卖出额度10万元" not in result
    assert "清仓比例设置为100%" not in result
    assert "减仓比例字段用于记录约束" in result
    assert changes


def test_has_position_减仓_preserved_when_gate_passes():
    text = "建议适度减仓控制风险。📊 数据源可用性：\n  ✅ 市场"
    gate = {"passed": True, "failures": []}
    result, changes = sanitize_forbidden_strong_actions(
        text, gate, "has_position", 0, 0,
    )
    assert "减仓" in result
    assert changes == []


# ══════════════════════════════════════════════════════════════════════════════
# FIX3-2: 降级说明与系统区块之间有换行
# ══════════════════════════════════════════════════════════════════════════════

def test_downgrade_note_newline_before_system_blocks():
    text = "建议重仓买入。📊 数据源可用性：\n  ✅ 市场"
    gate = {"passed": False, "failures": ["test"]}
    result, changes = sanitize_forbidden_strong_actions(
        text, gate, "has_position", 0, 0,
    )
    assert "📊 数据源可用性" in result
    assert "\n📊 数据源可用性" in result or "\n\n📊 数据源可用性" in result


# ══════════════════════════════════════════════════════════════════════════════
# FIX3-3: 强买同义词补全
# ══════════════════════════════════════════════════════════════════════════════

def test_sanitize_replaces_强买():
    text = "建议强买该股票。📊 数据源可用性：\n  ✅ 市场"
    gate = {"passed": False, "failures": ["test"]}
    result, changes = sanitize_forbidden_strong_actions(
        text, gate, "has_position", 0, 0,
    )
    assert "强买该" not in result
    assert "暂不执行强买入" in result


def test_strong_buy_keywords_contains_强买():
    assert "强买" in _STRONG_BUY_KEYWORDS


# ══════════════════════════════════════════════════════════════════════════════
# E-002: name_mismatch gate + level downgrade
# ══════════════════════════════════════════════════════════════════════════════

def test_name_mismatch_blocks_strong_action_gate():
    gate = get_strong_action_gate(
        source_coverage=80, evidence_coverage=80,
        name_mismatch=True,
    )
    assert gate["passed"] is False
    assert any("name_mismatch" in f for f in gate["failures"])


def test_name_mismatch_caps_risk_level_at_3():
    result = calculate_risk_level(
        source_coverage=85, evidence_coverage=85,
        broke_support=True,
        main_capital_outflow_days=2,
        volume_breakdown=True,
        position_status="has_position",
        name_mismatch=True,
    )
    assert result["level"] <= 3


def test_name_mismatch_caps_buy_level_at_3():
    result = calculate_buy_level(
        source_coverage=85, evidence_coverage=85,
        trend_confirmed=True,
        main_capital_inflow_days=2,
        volume_healthy_expansion=True,
        position_status="no_position",
        name_mismatch=True,
    )
    assert result["level"] <= 3


def test_no_name_mismatch_allows_level_4():
    result = calculate_risk_level(
        source_coverage=85, evidence_coverage=85,
        broke_support=True,
        main_capital_outflow_days=2,
        volume_breakdown=True,
        position_status="has_position",
        name_mismatch=False,
    )
    assert result["level"] == 4


# ══════════════════════════════════════════════════════════════════════════════
# E-003: Expanded stop-loss keywords + execution zone conflict
# ══════════════════════════════════════════════════════════════════════════════

def test_stop_loss_red_line_detected():
    signals = extract_execution_signals({}, "止损红线 25.50", {})
    assert signals["has_stop_loss"] is True


def test_stop_loss_position_detected():
    signals = extract_execution_signals({}, "止损位：24.80", {})
    assert signals["has_stop_loss"] is True


def test_stop_loss_price_detected():
    signals = extract_execution_signals({}, "止损价 26.5", {})
    assert signals["has_stop_loss"] is True


def test_stop_loss_line_detected():
    signals = extract_execution_signals({}, "清仓线 20.00", {})
    assert signals["has_stop_loss"] is True


def test_entry_zone_detected():
    signals = extract_execution_signals({}, "入场区间 28-30", {})
    assert "entry_zone" in str(signals) or signals["execution_zone_conflict"] is False


def test_reduce_zone_detected():
    signals = extract_execution_signals({}, "减仓区间 32-35", {})
    assert signals["execution_zone_conflict"] is False


def test_entry_and_reduce_conflict():
    signals = extract_execution_signals(
        {},
        "入场区间 28-35，减仓区间 30-40",
        {},
    )
    assert signals["execution_zone_conflict"] is True


def test_stop_loss_higher_than_entry_conflict():
    signals = extract_execution_signals(
        {},
        "止损价 30.00，入场价 25.00",
        {},
    )
    assert signals["execution_zone_conflict"] is True


def test_entry_reduce_no_conflict_separate_contexts():
    signals = extract_execution_signals(
        {},
        "入场区间 28-30。建议已持仓者在减仓区间 35-38 分批止盈",
        {},
    )
    assert signals["execution_zone_conflict"] is False


def test_gate_blocks_on_execution_zone_conflict():
    gate = get_strong_action_gate(
        source_coverage=80, evidence_coverage=80,
        no_execution_zone_conflict=False,
    )
    assert gate["passed"] is False
    assert any("execution_zone_conflict" in f for f in gate["failures"])


def test_stop_loss_lower_than_entry_no_conflict():
    signals = extract_execution_signals(
        {},
        "止损价 25.00，入场价 28.00",
        {},
    )
    assert signals["execution_zone_conflict"] is False


def test_entry_only_no_reduce_no_conflict():
    signals = extract_execution_signals(
        {},
        "入场区间 28-30",
        {},
    )
    assert signals["execution_zone_conflict"] is False


def test_entry_reduce_overlap_conflict():
    signals = extract_execution_signals(
        {},
        "入场区间 28-35，减仓区间 30-40",
        {},
    )
    assert signals["execution_zone_conflict"] is True


# ══════════════════════════════════════════════════════════════════════════════
# E-002 extended: validate_stock_name with strong context extraction
# ══════════════════════════════════════════════════════════════════════════════

def test_validate_stock_name_mismatch_wrong_name():
    from unittest.mock import patch
    with patch("tradingagents.agents.utils.readiness_score._resolve_name_from_ticker", return_value="顺络电子"):
        result = validate_stock_name("002138.SZ", "标的：环旭电子\n分析报告")
    assert result["name_mismatch"] is True
    assert "环旭电子" in result["found_names"]


def test_validate_stock_name_match_correct():
    from unittest.mock import patch
    with patch("tradingagents.agents.utils.readiness_score._resolve_name_from_ticker", return_value="顺络电子"):
        result = validate_stock_name("002138.SZ", "标的：顺络电子\n分析报告")
    assert result["name_mismatch"] is False


def test_validate_stock_name_no_name_in_report():
    from unittest.mock import patch
    with patch("tradingagents.agents.utils.readiness_score._resolve_name_from_ticker", return_value="顺络电子"):
        result = validate_stock_name("002138.SZ", "这是一份分析报告，不包含股票名称")
    assert result["name_mismatch"] is False
    assert result["found_names"] == []


def test_validate_stock_name_industry_word_not_extracted():
    from unittest.mock import patch
    with patch("tradingagents.agents.utils.readiness_score._resolve_name_from_ticker", return_value="顺络电子"):
        result = validate_stock_name("002138.SZ", "电子行业整体向好，科技板块表现突出")
    assert result["name_mismatch"] is False
    assert result["found_names"] == []


def test_validate_stock_name_md_title_extracts_name():
    from unittest.mock import patch
    with patch("tradingagents.agents.utils.readiness_score._resolve_name_from_ticker", return_value="顺络电子"):
        result = validate_stock_name("002138.SZ", "# 002138.SZ 环旭电子 分析")
    assert result["name_mismatch"] is True
    assert "环旭电子" in result["found_names"]


def test_validate_stock_name_bj_title_extracts_mismatch():
    from unittest.mock import patch
    with patch("tradingagents.agents.utils.readiness_score._resolve_name_from_ticker", return_value="诺思兰德"):
        result = validate_stock_name("430047.BJ", "# 430047.BJ 利通电子 分析")
    assert result["name_mismatch"] is True
    assert "利通电子" in result["found_names"]


# ══════════════════════════════════════════════════════════════════════════════
# E-004: Raw evidence priority over text regex
# ══════════════════════════════════════════════════════════════════════════════

def test_raw_evidence_stock_data_overrides_text():
    statuses = infer_evidence_statuses(
        {"market_report": "no data here"},
        raw_evidence={"stock_data": "date,open,high,low,close,volume\n2026-05-01,10,11,9,10.5,1000000" + "x" * 60},
    )
    assert statuses["ohlcv_5d"] == EvidenceStatus.HAS_DATA
    assert statuses["volume"] == EvidenceStatus.HAS_DATA


def test_raw_evidence_stock_data_failed():
    statuses = infer_evidence_statuses(
        {"market_report": "report"},
        raw_evidence={"stock_data": "stock_data 调用失败"},
    )
    assert statuses["ohlcv_5d"] == EvidenceStatus.QUERY_FAILED


def test_raw_evidence_fund_flow_individual():
    statuses = infer_evidence_statuses(
        {"smart_money_report": "no regex match"},
        raw_evidence={"fund_flow_individual": "日期,主力净流入\n2026-05-01,1000万"},
    )
    assert statuses["individual_fund_flow"] == EvidenceStatus.HAS_DATA


def test_raw_evidence_fund_flow_failed():
    statuses = infer_evidence_statuses(
        {"smart_money_report": "report"},
        raw_evidence={"fund_flow_individual": "fund_flow_individual 调用失败：timeout"},
    )
    assert statuses["individual_fund_flow"] == EvidenceStatus.QUERY_FAILED


def test_raw_evidence_lhb_normal():
    statuses = infer_evidence_statuses(
        {"market_report": "report"},
        raw_evidence={"lhb": ""},
    )
    assert statuses["lhb_status"] == EvidenceStatus.NORMAL_NO_DATA


def test_raw_evidence_lhb_failed():
    statuses = infer_evidence_statuses(
        {"market_report": "report"},
        raw_evidence={"lhb": "lhb 调用失败：API Error"},
    )
    assert statuses["lhb_status"] == EvidenceStatus.QUERY_FAILED


def test_raw_evidence_lhb_has_data():
    statuses = infer_evidence_statuses(
        {"market_report": "report"},
        raw_evidence={"lhb": "龙虎榜：买入营业部..."},
    )
    assert statuses["lhb_status"] == EvidenceStatus.HAS_DATA


def test_raw_evidence_news_has_data():
    statuses = infer_evidence_statuses(
        {"news_report": "report"},
        raw_evidence={"news": "x" * 100},
    )
    assert statuses["announcements"] == EvidenceStatus.HAS_DATA


def test_raw_evidence_news_failed():
    statuses = infer_evidence_statuses(
        {"news_report": "report"},
        raw_evidence={"news": "news 调用失败"},
    )
    assert statuses["announcements"] == EvidenceStatus.QUERY_FAILED


def test_raw_evidence_turnover_rate_not_in_pool():
    statuses = infer_evidence_statuses(
        {"volume_price_report": "report"},
        raw_evidence={"stock_data": "x" * 100},
    )
    assert statuses["turnover_rate"] == EvidenceStatus.FIELD_MISSING


def test_raw_evidence_margin_trading_not_in_pool():
    statuses = infer_evidence_statuses(
        {},
        raw_evidence={"stock_data": "x" * 100},
    )
    assert statuses["margin_trading"] == EvidenceStatus.NOT_QUERIED


def test_raw_evidence_none_falls_back_to_text():
    statuses_no_raw = infer_evidence_statuses(
        {"market_report": "日期       | 开盘价 | 最高价 | 最低价 | 收盘价\n2026-05-09 | 25.30  | 26.10  | 25.00  | 25.80"},
        raw_evidence=None,
    )
    statuses_raw_empty = infer_evidence_statuses(
        {"market_report": "日期       | 开盘价 | 最高价 | 最低价 | 收盘价\n2026-05-09 | 25.30  | 26.10  | 25.00  | 25.80"},
        raw_evidence={},
    )
    assert statuses_no_raw["ohlcv_5d"] == EvidenceStatus.HAS_DATA
    assert statuses_raw_empty["ohlcv_5d"] == EvidenceStatus.HAS_DATA


def test_fundamental_gate_filters_control_style_buy_sizing():
    gate = {"passed": False, "failures": ["fundamental_semantic_gate"]}
    for text in ("买入比例控制在10%。", "建仓额度控制在10万元。"):
        sanitized, changes = sanitize_forbidden_strong_actions(
            text,
            gate=gate,
            position_status="no_position",
            buy_level=0,
            risk_level=0,
        )
        assert "控制在" not in sanitized
        assert "等待基本面证据复核" in sanitized
        assert changes


def test_valuation_price_gate_filters_ordinary_entry_actions():
    gate = {
        "passed": False,
        "failures": ["估值基准价不可用(valuation_price_unavailable)"],
    }
    for text in ("建议买入", "建议建仓", "可以入场"):
        sanitized, changes = sanitize_forbidden_strong_actions(
            text,
            gate=gate,
            position_status="no_position",
            buy_level=0,
            risk_level=0,
        )
        assert text not in sanitized
        assert "等待可执行价格确认" in sanitized
        assert changes


def test_valuation_price_gate_filters_prospective_buy_cost_instructions():
    gate = {
        "passed": False,
        "failures": ["估值基准价不可用(valuation_price_unavailable)"],
    }
    for text in ("建议买入成本为20元。", "计划买入成本控制在20元。"):
        sanitized, changes = sanitize_forbidden_strong_actions(
            text,
            gate=gate,
            position_status="no_position",
            buy_level=0,
            risk_level=0,
        )

        assert text not in sanitized
        assert "等待可执行价格确认" in sanitized
        assert changes


def test_valuation_price_gate_filters_bare_buy_recommendations():
    gate = {
        "passed": False,
        "failures": ["估值基准价不可用(valuation_price_unavailable)"],
    }
    for text in ("现在可以买", "建议买", "买一点", "操作：买", "买 100 股"):
        sanitized, changes = sanitize_forbidden_strong_actions(
            text,
            gate=gate,
            position_status="no_position",
            buy_level=0,
            risk_level=1,
        )

        assert text not in sanitized
        assert "等待可执行价格确认" in sanitized
        assert changes


def test_valuation_price_gate_filters_entry_synonyms():
    gate = {
        "passed": False,
        "failures": ["估值基准价不可用(valuation_price_unavailable)"],
    }

    for text in ("假设约10元，建议抄底", "可购入少量仓位"):
        sanitized, changes = sanitize_forbidden_strong_actions(
            text,
            gate=gate,
            position_status="no_position",
            buy_level=0,
            risk_level=1,
        )

        assert "抄底" not in sanitized
        assert "购入" not in sanitized
        assert "等待可执行价格确认" in sanitized
        assert changes


def test_valuation_price_gate_preserves_negated_entry_synonyms():
    gate = {
        "passed": False,
        "failures": ["估值基准价不可用(valuation_price_unavailable)"],
    }

    for text in ("不建议抄底。", "不可购入。"):
        sanitized, changes = sanitize_forbidden_strong_actions(
            text,
            gate=gate,
            position_status="no_position",
            buy_level=0,
            risk_level=1,
        )

        assert sanitized == text
        assert not changes


def test_valuation_price_gate_preserves_bare_buy_negations():
    gate = {
        "passed": False,
        "failures": ["估值基准价不可用(valuation_price_unavailable)"],
    }
    for text in ("还没买，继续观察", "不建议买", "禁止现在买"):
        sanitized, changes = sanitize_forbidden_strong_actions(
            text,
            gate=gate,
            position_status="no_position",
            buy_level=0,
            risk_level=1,
        )

        assert text in sanitized
        assert not changes


def test_entry_gate_preserves_negation_but_filters_later_positive_action():
    gate = {
        "passed": False,
        "failures": ["估值基准价不可用(valuation_price_unavailable)"],
    }
    for text, forbidden in (
        ("不建议追涨可逢低吸纳。", "逢低吸纳"),
        ("不建议买入但可低吸。", "可低吸"),
        ("暂不建议追涨可考虑低吸。", "考虑低吸"),
    ):
        sanitized, changes = sanitize_forbidden_strong_actions(
            text,
            gate=gate,
            position_status="no_position",
            buy_level=0,
            risk_level=0,
        )

        assert forbidden not in sanitized
        assert "等待可执行价格确认" in sanitized
        assert changes


def test_event_risk_gate_preserves_negated_buy_guardrails():
    gate = {"passed": False, "failures": ["event_risk_block_open"]}
    for text in (
        "不建议买入，继续观察。",
        "禁止建议建仓。",
        "不建议加仓买入。",
        "不得追涨买入。",
    ):
        sanitized, changes = sanitize_forbidden_strong_actions(
            text,
            gate=gate,
            position_status="has_position",
            buy_level=0,
            risk_level=1,
        )

        assert sanitized == text
        assert changes == []


def test_event_risk_gate_preserves_negation_and_filters_later_positive_action():
    sanitized, changes = sanitize_forbidden_strong_actions(
        "不建议买入但可低吸。",
        gate={"passed": False, "failures": ["event_risk_block_open"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    assert "不建议买入" in sanitized
    assert "可低吸" not in sanitized
    assert "等待风险解除" in sanitized
    assert changes


def test_entry_price_gate_filters_executable_entry_synonyms():
    gate = {
        "passed": False,
        "failures": ["估值基准价不可用(valuation_price_unavailable)"],
    }
    for text, forbidden in (
        ("当前价格可以介入。", "可以介入"),
        ("建议小仓位参与。", "小仓位参与"),
        ("当前可逢低配置仓位。", "逢低配置仓位"),
    ):
        sanitized, changes = sanitize_forbidden_strong_actions(
            text,
            gate=gate,
            position_status="no_position",
            buy_level=0,
            risk_level=1,
        )

        assert forbidden not in sanitized
        assert "等待可执行价格确认" in sanitized
        assert changes


def test_entry_price_gate_filters_bare_sized_entry_phrases():
    gate = {
        "passed": False,
        "failures": ["估值基准价不可用(valuation_price_unavailable)"],
    }
    for text, forbidden in (
        ("分批介入，仓位10%。", "分批介入"),
        ("轻仓介入该股。", "轻仓介入"),
        ("小仓位参与。", "小仓位参与"),
        ("逐步建立仓位。", "逐步建立仓位"),
        ("配置10%仓位。", "配置10%仓位"),
    ):
        sanitized, changes = sanitize_forbidden_strong_actions(
            text,
            gate=gate,
            position_status="no_position",
            buy_level=0,
            risk_level=1,
        )

        assert forbidden not in sanitized
        assert "等待可执行价格确认" in sanitized
        assert changes


def test_entry_price_gate_preserves_negated_entry_synonyms():
    gate = {
        "passed": False,
        "failures": ["估值基准价不可用(valuation_price_unavailable)"],
    }
    for text in (
        "不建议介入。",
        "禁止小仓位参与。",
        "不允许逢低配置仓位。",
    ):
        sanitized, changes = sanitize_forbidden_strong_actions(
            text,
            gate=gate,
            position_status="no_position",
            buy_level=0,
            risk_level=1,
        )

        assert sanitized == text
        assert changes == []


def test_valuation_gate_preserves_negated_strong_buy_language():
    gate = {
        "passed": False,
        "failures": ["估值基准价不可用(valuation_price_unavailable)"],
    }

    sanitized, changes = sanitize_forbidden_strong_actions(
        "不建议加仓，继续观察。",
        gate=gate,
        position_status="has_position",
        buy_level=0,
        risk_level=1,
    )

    assert "不建议加仓" in sanitized
    assert "不暂不执行强买入" not in sanitized
    assert not changes


def test_fundamental_gate_filters_executable_buy_limit_increase():
    gate = {"passed": False, "failures": ["fundamental_semantic_gate"]}
    text = "建议将买入上限提高至50%。未来买入上限为10%。"

    sanitized, changes = sanitize_forbidden_strong_actions(
        text,
        gate=gate,
        position_status="no_position",
        buy_level=0,
        risk_level=0,
    )

    body = sanitized.split("⚠️ [D-002]", 1)[0]
    assert "建议将买入上限提高至50%" not in body
    assert "未来买入上限为10%" in body
    assert changes


def test_fundamental_gate_filters_noun_first_buy_limit_increase():
    gate = {"passed": False, "failures": ["fundamental_semantic_gate"]}
    text = "买入比例提高至50%。未来买入上限为10%。"

    sanitized, changes = sanitize_forbidden_strong_actions(
        text,
        gate=gate,
        position_status="no_position",
        buy_level=0,
        risk_level=0,
    )

    body = sanitized.split("⚠️ [D-002]", 1)[0]
    assert "买入比例提高至50%" not in body
    assert "未来买入上限为10%" in body
    assert changes


def test_fundamental_gate_filters_set_to_buy_sizing():
    gate = {"passed": False, "failures": ["fundamental_semantic_gate"]}
    for executable in ("建议将买入比例设为50%", "建议将买入上限设成50%"):
        text = f"{executable}。未来买入上限为10%。"
        sanitized, changes = sanitize_forbidden_strong_actions(
            text,
            gate=gate,
            position_status="no_position",
            buy_level=0,
            risk_level=0,
        )

        body = sanitized.split("⚠️ [D-002]", 1)[0]
        assert executable not in body
        assert "未来买入上限为10%" in body
        assert changes


def test_failed_entry_gates_filter_approximate_buy_allocations():
    cases = (
        ("fundamental_semantic_gate", "等待基本面证据复核"),
        ("估值基准价不可用(valuation_price_unavailable)", "等待可执行价格确认"),
    )
    for failure, replacement in cases:
        for executable in (
            "可将买入比例调整到约20%",
            "建议将建仓额度提高到大约10万元",
            "可以把加仓比例设为大概一成",
        ):
            sanitized, changes = sanitize_forbidden_strong_actions(
                executable,
                gate={"passed": False, "failures": [failure]},
                position_status="has_position",
                buy_level=1,
                risk_level=1,
            )

            body = sanitized.split("⚠️ [D-002]", 1)[0]
            assert executable not in body
            assert replacement in body
            assert changes


def test_no_position_filters_control_style_sell_sizing():
    gate = {"passed": True, "failures": []}
    for text in ("减仓比例控制在50%。", "卖出额度控制在10万元。"):
        sanitized, changes = sanitize_forbidden_strong_actions(
            text,
            gate=gate,
            position_status="no_position",
            buy_level=0,
            risk_level=0,
        )
        assert "控制在" not in sanitized
        assert "该持仓动作不适用" in sanitized
        assert changes


def test_no_position_filters_executable_sell_limit_increase():
    gate = {"passed": True, "failures": []}
    text = "建议将减仓上限提高至50%。未来减仓上限为20%。"

    sanitized, changes = sanitize_forbidden_strong_actions(
        text,
        gate=gate,
        position_status="no_position",
        buy_level=0,
        risk_level=0,
    )

    body = sanitized.split("⚠️ [D-002]", 1)[0]
    assert "建议将减仓上限提高至50%" not in body
    assert "未来减仓上限为20%" in body
    assert changes


def test_no_position_filters_noun_first_sell_limit_increase():
    gate = {"passed": True, "failures": []}
    text = "减仓上限提高至50%。未来减仓上限为20%。"

    sanitized, changes = sanitize_forbidden_strong_actions(
        text,
        gate=gate,
        position_status="no_position",
        buy_level=0,
        risk_level=0,
    )

    body = sanitized.split("⚠️ [D-002]", 1)[0]
    assert "减仓上限提高至50%" not in body
    assert "未来减仓上限为20%" in body
    assert changes


def test_no_position_filters_set_to_sell_sizing():
    gate = {"passed": True, "failures": []}
    for executable in ("将减仓上限设为50%", "把卖出比例设成50%"):
        text = f"{executable}。未来减仓上限为20%。"
        sanitized, changes = sanitize_forbidden_strong_actions(
            text,
            gate=gate,
            position_status="no_position",
            buy_level=0,
            risk_level=0,
        )

        body = sanitized.split("⚠️ [D-002]", 1)[0]
        assert executable not in body
        assert "未来减仓上限为20%" in body
        assert changes


def test_no_position_filters_approximate_sell_allocations():
    for executable in (
        "建议将减仓比例调整到约50%",
        "可把清仓比例设置为大约一半",
    ):
        sanitized, changes = sanitize_forbidden_strong_actions(
            executable,
            gate={"passed": True, "failures": []},
            position_status="no_position",
            buy_level=0,
            risk_level=0,
        )

        body = sanitized.split("⚠️ [D-002]", 1)[0]
        assert executable not in body
        assert "该持仓动作不适用" in body
        assert changes


def test_fundamental_veto_filters_canonical_entry_verbs_but_preserves_negations():
    gate = {
        "passed": False,
        "failures": ["估值基准价不可用(valuation_price_unavailable)"],
    }
    for phrase in ("建议增持20%仓位", "建议做多", "建议开仓"):
        sanitized, changes = sanitize_forbidden_strong_actions(
            phrase,
            gate=gate,
            position_status="has_position",
            buy_level=1,
            risk_level=2,
        )
        body = sanitized.split("⚠️ [D-002]", 1)[0]
        assert phrase not in body
        assert not any(action in body for action in ("增持", "做多", "开仓"))
        assert changes

    for phrase in ("不建议增持", "暂不做多", "不要开仓"):
        sanitized, changes = sanitize_forbidden_strong_actions(
            phrase,
            gate=gate,
            position_status="has_position",
            buy_level=1,
            risk_level=2,
        )
        assert phrase in sanitized
        assert changes == []


# ══════════════════════════════════════════════════════════════════════════════
# LHB 未触发 & fund_flow anomaly fix
# ══════════════════════════════════════════════════════════════════════════════

def test_raw_evidence_lhb_not_triggered():
    """[G-007] LHB force=False 未触发应算 NOT_QUERIED（未实际查询）."""
    statuses = infer_evidence_statuses(
        {"smart_money_report": "report"},
        raw_evidence={"lhb": "龙虎榜查询未触发（force=False）"},
    )
    assert statuses["lhb_status"] == EvidenceStatus.NOT_QUERIED


def test_raw_evidence_lhb_not_triggered_short():
    """短文本 '未触发' 也应识别为 NOT_QUERIED."""
    statuses = infer_evidence_statuses(
        {"smart_money_report": "report"},
        raw_evidence={"lhb": "未触发"},
    )
    assert statuses["lhb_status"] == EvidenceStatus.NOT_QUERIED


def test_fund_flow_anomaly_no_false_positive_on_dates():
    """日期/金额等普通数字不应误触发 LHB."""
    from tradingagents.agents.analysts.smart_money_analyst import _check_fund_flow_anomaly
    text = "日期       主力净流入    成交额\n2026-05-09  1234.56万  50000万\n2026-05-08  -800万     45000万"
    assert _check_fund_flow_anomaly(text) is False


def test_fund_flow_anomaly_triggers_on_pct():
    """带 % 的占比超过 5% 应触发 LHB."""
    from tradingagents.agents.analysts.smart_money_analyst import _check_fund_flow_anomaly
    text = "日期       主力净流入占比\n2026-05-09  +6.2%\n2026-05-08  -3.1%"
    assert _check_fund_flow_anomaly(text) is True


def test_fund_flow_anomaly_no_trigger_small_pct():
    """占比 < 5% 不应触发."""
    from tradingagents.agents.analysts.smart_money_analyst import _check_fund_flow_anomaly
    text = "日期       主力净流入占比\n2026-05-09  +2.1%\n2026-05-08  -1.3%"
    assert _check_fund_flow_anomaly(text) is False


def test_fund_flow_anomaly_unsigned_pct():
    """无符号百分比 6.2% 也应触发."""
    from tradingagents.agents.analysts.smart_money_analyst import _check_fund_flow_anomaly
    text = "2026-05-09  6.2%"
    assert _check_fund_flow_anomaly(text) is True


def test_fund_flow_anomaly_table_ratio_col():
    """AKShare 表格 header 含 '净占比' 列，数据行 6.2 应触发（即使无 %）."""
    from tradingagents.agents.analysts.smart_money_analyst import _check_fund_flow_anomaly
    text = "日期       主力净流入-净占比  成交额\n2026-05-09  6.2  50000万\n2026-05-08  -3.1  45000万"
    assert _check_fund_flow_anomaly(text) is True


def test_fund_flow_anomaly_table_amount_no_trigger():
    """表格中无占比列时，普通金额不触发."""
    from tradingagents.agents.analysts.smart_money_analyst import _check_fund_flow_anomaly
    text = "日期       主力净流入    成交额\n2026-05-09  1234.56万  50000万\n2026-05-08  -800万     45000万"
    assert _check_fund_flow_anomaly(text) is False


def test_price_gate_preserves_completed_purchase_facts():
    gate = {
        "passed": False,
        "failures": ["估值基准价不可用(valuation_price_unavailable)"],
    }
    original = "用户已在20元买入100股，当前建议持有。买入成本为20元。"

    sanitized, _changes = sanitize_forbidden_strong_actions(
        original,
        gate=gate,
        position_status="has_position",
        buy_level=1,
        risk_level=2,
    )

    body = sanitized.split("⚠️ [D-002]", 1)[0]
    assert "用户已在20元买入100股" in body
    assert "买入成本为20元" in body


def test_price_gate_preserves_date_first_completed_purchase_facts():
    gate = {
        "passed": False,
        "failures": ["估值基准价不可用(valuation_price_unavailable)"],
    }

    for original in ("昨天我买入了100股。", "上周三我买入100股。"):
        sanitized, _changes = sanitize_forbidden_strong_actions(
            original,
            gate=gate,
            position_status="has_position",
            buy_level=1,
            risk_level=2,
        )

        body = sanitized.split("⚠️ [D-002]", 1)[0]
        assert original in body


def test_price_gate_does_not_preserve_dated_actionable_buy_recommendations():
    gate = {
        "passed": False,
        "failures": ["估值基准价不可用(valuation_price_unavailable)"],
    }

    sanitized, changes = sanitize_forbidden_strong_actions(
        "昨日买入建议仍然有效。",
        gate=gate,
        position_status="no_position",
        buy_level=1,
        risk_level=0,
    )

    body = sanitized.split("⚠️ [D-002]", 1)[0]
    assert "买入建议仍然有效" not in body
    assert "等待可执行价格确认" in body
    assert changes


def test_price_gate_still_sanitizes_prospective_priced_purchase():
    gate = {
        "passed": False,
        "failures": ["估值基准价不可用(valuation_price_unavailable)"],
    }

    sanitized, changes = sanitize_forbidden_strong_actions(
        "建议用户在20元买入100股。",
        gate=gate,
        position_status="no_position",
        buy_level=1,
        risk_level=1,
    )

    body = sanitized.split("⚠️ [D-002]", 1)[0]
    assert "建议用户在20元买入100股" not in body
    assert "等待可执行价格确认" in body
    assert changes


def test_percentage_only_holding_counts_as_position_data():
    from tradingagents.agents.managers.risk_manager import _has_position_data

    assert _has_position_data({"current_position_pct": 35}) is True
    assert _has_position_data({"current_position_pct": 0}) is True
    assert _has_position_data({"current_position": 0}) is True
    assert _has_position_data({"average_cost": 30}) is False
