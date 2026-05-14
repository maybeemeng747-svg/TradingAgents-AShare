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
        lhb_status=EvidenceStatus.NORMAL_NO_DATA,
        margin_trading=EvidenceStatus.HAS_DATA,
        announcements=EvidenceStatus.HAS_DATA,
    )
    assert ev == 100


def test_normal_no_data_counts_as_valid():
    ev = calculate_evidence_coverage(
        ohlcv_5d=EvidenceStatus.NORMAL_NO_DATA,
        volume=EvidenceStatus.NORMAL_NO_DATA,
        turnover_rate=EvidenceStatus.NORMAL_NO_DATA,
        volume_ratio=EvidenceStatus.NORMAL_NO_DATA,
        individual_fund_flow=EvidenceStatus.NORMAL_NO_DATA,
        lhb_status=EvidenceStatus.NORMAL_NO_DATA,
        margin_trading=EvidenceStatus.NORMAL_NO_DATA,
        announcements=EvidenceStatus.NORMAL_NO_DATA,
    )
    assert ev == 100


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
    )
    assert ev == 87


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
        position_status="no_position",
    )
    assert result["level"] == 4


def test_buy_level_4_blocked_by_negative_announcement():
    result = calculate_buy_level(
        source_coverage=85, evidence_coverage=85,
        trend_confirmed=True,
        main_capital_inflow_days=2,
        volume_healthy_expansion=True,
        has_major_negative_announcement=True,
        position_status="no_position",
    )
    assert result["level"] < 4


def test_buy_level_4_blocked_by_low_coverage():
    result = calculate_buy_level(
        source_coverage=70, evidence_coverage=85,
        trend_confirmed=True,
        main_capital_inflow_days=2,
        volume_healthy_expansion=True,
        position_status="no_position",
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


def test_evidence_margin_trading_always_not_queried():
    statuses = infer_evidence_statuses({
        "market_report": "some report",
        "smart_money_report": "some report",
    })
    assert statuses["margin_trading"] == EvidenceStatus.NOT_AVAILABLE


def test_evidence_no_report_means_not_queried():
    statuses = infer_evidence_statuses({})
    assert statuses["ohlcv_5d"] == EvidenceStatus.NOT_QUERIED
    assert statuses["volume"] == EvidenceStatus.NOT_QUERIED
    assert statuses["margin_trading"] == EvidenceStatus.NOT_AVAILABLE


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

def test_buy_level_3_with_positive_signals():
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
    assert result["level"] >= 3


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
    assert statuses["margin_trading"] == EvidenceStatus.NOT_AVAILABLE


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


# ══════════════════════════════════════════════════════════════════════════════
# LHB 未触发 & fund_flow anomaly fix
# ══════════════════════════════════════════════════════════════════════════════

def test_raw_evidence_lhb_not_triggered():
    """LHB force=False 未触发应算 NORMAL_NO_DATA，不是 HAS_DATA."""
    statuses = infer_evidence_statuses(
        {"smart_money_report": "report"},
        raw_evidence={"lhb": "龙虎榜查询未触发（force=False）"},
    )
    assert statuses["lhb_status"] == EvidenceStatus.NORMAL_NO_DATA


def test_raw_evidence_lhb_not_triggered_short():
    """短文本 '未触发' 也应识别."""
    statuses = infer_evidence_statuses(
        {"smart_money_report": "report"},
        raw_evidence={"lhb": "未触发"},
    )
    assert statuses["lhb_status"] == EvidenceStatus.NORMAL_NO_DATA


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
