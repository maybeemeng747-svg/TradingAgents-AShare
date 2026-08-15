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
    ConfidenceLevel,
)
from tradingagents.agents.managers.risk_manager import (
    create_risk_manager,
    _format_visible_execution_summary,
    _STRONG_BUY_KEYWORDS,
    _STRONG_SELL_KEYWORDS,
)
from tradingagents.agents.utils.trade_setup import build_trade_quality_check


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


def test_readiness_block_uses_explicit_no_position_policy():
    score = generate_readiness_score(
        100,
        ConfidenceLevel.MEDIUM,
        blockers=["fundamental_semantic_gate"],
    )

    block = format_readiness_score(
        score,
        position_status="no_position",
        entry_gate_blocked=True,
    )

    assert "中等质量报告 · 未持仓 · 入场门禁未通过" in block
    assert "持仓状态未知" not in block
    assert "允许动作：观察" in block
    assert "条件试仓" in block.split("- 禁止动作：", 1)[1]
    assert "未持仓且入场门禁未通过，仅允许观察" in block


def test_readiness_no_position_buy_level_one_allows_observation_only():
    score = generate_readiness_score(100, ConfidenceLevel.HIGH)

    block = format_readiness_score(
        score,
        position_status="no_position",
        buy_level=1,
    )

    assert "允许动作：观察\n" in block
    forbidden = block.split("- 禁止动作：", 1)[1]
    assert "条件试仓" in forbidden
    assert "确认建仓" in forbidden


def test_readiness_no_position_buy_level_two_allows_conditional_trial():
    score = generate_readiness_score(100, ConfidenceLevel.HIGH)

    block = format_readiness_score(
        score,
        position_status="no_position",
        buy_level=2,
    )

    assert "允许动作：观察, 条件试仓" in block
    assert "确认建仓" in block.split("- 禁止动作：", 1)[1]


def test_readiness_unknown_position_buy_level_two_never_allows_direct_entry():
    score = generate_readiness_score(100, ConfidenceLevel.HIGH)

    block = format_readiness_score(
        score,
        position_status="unknown",
        buy_level=2,
    )

    allowed_line = next(
        line for line in block.splitlines() if line.startswith("- 允许动作：")
    )
    forbidden_line = next(
        line for line in block.splitlines() if line.startswith("- 禁止动作：")
    )
    assert "条件试仓" in allowed_line
    assert "买入" not in allowed_line
    assert "加仓" not in allowed_line
    assert "买入" in forbidden_line
    assert "加仓" in forbidden_line


def test_readiness_existing_position_buy_level_two_does_not_allow_adding():
    score = generate_readiness_score(100, ConfidenceLevel.HIGH)

    block = format_readiness_score(
        score,
        position_status="has_position",
        buy_level=2,
    )

    allowed_line = next(
        line for line in block.splitlines() if line.startswith("- 允许动作：")
    )
    forbidden_line = next(
        line for line in block.splitlines() if line.startswith("- 禁止动作：")
    )
    assert "加仓" not in allowed_line
    assert "加仓" in forbidden_line


def test_readiness_existing_position_buy_level_three_can_allow_adding():
    score = generate_readiness_score(100, ConfidenceLevel.HIGH)

    block = format_readiness_score(
        score,
        position_status="has_position",
        buy_level=3,
    )

    allowed_line = next(
        line for line in block.splitlines() if line.startswith("- 允许动作：")
    )
    assert "加仓" in allowed_line


def test_readiness_failed_final_gate_overrides_buy_level_two():
    score = generate_readiness_score(100, ConfidenceLevel.HIGH)

    block = format_readiness_score(
        score,
        position_status="no_position",
        entry_gate_blocked=True,
        buy_level=2,
    )

    assert "允许动作：观察\n" in block
    assert "条件试仓" in block.split("- 禁止动作：", 1)[1]
    assert "未持仓且入场门禁未通过，仅允许观察" in block


def test_readiness_risk_level_one_overrides_no_position_buy_level_two():
    score = generate_readiness_score(100, ConfidenceLevel.HIGH)

    block = format_readiness_score(
        score,
        position_status="no_position",
        buy_level=2,
        risk_level=1,
    )

    assert "允许动作：观察\n" in block
    assert "条件试仓" in block.split("- 禁止动作：", 1)[1]


def test_readiness_exit_risk_blocks_add_position_permission():
    score = generate_readiness_score(100, ConfidenceLevel.HIGH)

    block = format_readiness_score(
        score,
        position_status="has_position",
        buy_level=3,
        risk_level=4,
    )

    allowed_line = next(
        line for line in block.splitlines() if line.startswith("- 允许动作：")
    )
    assert "加仓" not in allowed_line


@pytest.mark.parametrize(
    "text, forbidden",
    (
        ("当前操作：条件试仓。", "条件试仓"),
        ("确认建仓。", "确认建仓"),
        ("建议轻仓试错。", "轻仓试错"),
    ),
)
def test_generic_failed_gate_sanitizes_all_entry_actions(text, forbidden):
    sanitized, changes = sanitize_forbidden_strong_actions(
        text,
        gate={"passed": False, "failures": ["evidence_coverage=62% < 70%"]},
        position_status="no_position",
        buy_level=2,
        risk_level=1,
    )

    body = sanitized.split("⚠️ [D-002]", 1)[0]
    assert forbidden not in body
    assert "等待门禁条件满足" in body
    assert changes


def test_generic_failed_gate_preserves_historical_entry_analysis():
    text = "回测建议买入时胜率60%。"
    sanitized, changes = sanitize_forbidden_strong_actions(
        text,
        gate={"passed": False, "failures": ["source_coverage=62% < 70%"]},
        position_status="no_position",
        buy_level=2,
        risk_level=1,
    )

    assert text in sanitized
    assert "等待门禁条件满足" not in sanitized
    assert changes == []


def test_risk_manager_passes_final_gate_failure_to_readiness(monkeypatch):
    from tradingagents.agents.managers import risk_manager as risk_manager_module

    captured = {}
    original_formatter = risk_manager_module.format_readiness_score

    def _capture_formatter(score, **kwargs):
        captured.update(kwargs)
        return original_formatter(score, **kwargs)

    monkeypatch.setattr(
        risk_manager_module,
        "get_strong_action_gate",
        lambda **_kwargs: {
            "passed": False,
            "failures": ["fund_flow_unit_unverified"],
        },
    )
    monkeypatch.setattr(
        risk_manager_module,
        "format_readiness_score",
        _capture_formatter,
    )

    node = risk_manager_module.create_risk_manager(_FakeLLM(), _Memory())
    asyncio.run(node(_base_state()))

    assert captured["entry_gate_blocked"] is True


def test_readiness_event_risk_block_forbids_no_position_entry():
    score = generate_readiness_score(
        100,
        ConfidenceLevel.MEDIUM,
        blockers=["event_risk_block_open"],
    )

    block = format_readiness_score(
        score,
        position_status="no_position",
        entry_gate_blocked=True,
    )

    assert "允许动作：观察" in block
    assert "条件试仓" in block.split("- 禁止动作：", 1)[1]
    assert "置信度降级原因：event_risk_block_open" in block


@pytest.mark.parametrize(
    "position_status, forbidden_allowed",
    (("has_position", "加仓"), ("unknown", "买入")),
)
def test_readiness_entry_gate_removes_entry_for_other_position_states(
    position_status,
    forbidden_allowed,
):
    score = generate_readiness_score(
        100,
        ConfidenceLevel.HIGH,
        blockers=["event_risk_block_open"],
    )

    block = format_readiness_score(
        score,
        position_status=position_status,
        entry_gate_blocked=True,
    )

    allowed_line = next(line for line in block.splitlines() if line.startswith("- 允许动作："))
    forbidden_line = next(line for line in block.splitlines() if line.startswith("- 禁止动作："))
    assert forbidden_allowed not in allowed_line
    assert forbidden_allowed in forbidden_line
    assert "持仓风控动作仍按 Risk Level 执行" in block


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
        text, gate, "has_position", 3, 0,
    )
    assert "重仓买入" in result
    assert "立即清仓" not in result
    assert "等待触发条件，暂不执行强清仓" in result
    assert changes


def test_existing_position_risk_level_two_blocks_add_in_visible_body():
    sanitized, changes = sanitize_forbidden_strong_actions(
        "建议重仓买入，持有等待。",
        gate={"passed": True, "failures": []},
        position_status="has_position",
        buy_level=3,
        risk_level=2,
    )

    body = sanitized.split("⚠️ [D-002]", 1)[0]
    assert "重仓买入" not in body
    assert "保持原仓位，等待加仓条件确认" in body
    assert "持有等待" in body
    assert changes


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


def test_sanitize_preserves_position_and_fundamental_system_blocks():
    llm_body = "当前建议买入并设置止损。"
    text = (
        llm_body + "\n\n"
        "⚠️ [C-001] 未持仓状态，以下持仓动作不适用：EXIT/REDUCE、HOLD、止损。"
        "已自动降级为观察（WAIT）。\n\n"
        "【基本面语义门禁：NEEDS_REVIEW】\n"
        "- DERIVATION_CONFLICT: 禁止以全年累计值替代单季度值"
    )
    gate = {"passed": False, "failures": ["fundamental_semantic_gate"]}

    result, changes = sanitize_forbidden_strong_actions(
        text, gate, "no_position", 0, 1,
        trusted_system_offset=len(llm_body),
    )

    assert "当前建议买入" not in result
    assert "EXIT/REDUCE、HOLD、止损" in result
    assert "DERIVATION_CONFLICT" in result
    assert changes


def test_sanitize_deduplicates_downgrade_audit_entries():
    text = "建议买入。建议买入。建议买入。"
    gate = {"passed": False, "failures": ["fundamental_semantic_gate"]}

    result, changes = sanitize_forbidden_strong_actions(
        text, gate, "no_position", 0, 1,
    )

    assert changes == ["入场门禁买入动作已降级为「等待基本面证据复核」"]
    audit = result.split("⚠️ [D-002]", 1)[1]
    assert audit.count("入场门禁买入动作已降级") == 1


def test_sanitize_preserves_action_enums_and_future_risk_nouns():
    text = (
        "分类标签：Buy/Sell/Hold。"
        "若未来已建仓，止损失效风险需要复核，目标价附近止盈仅作参考。"
    )
    gate = {"passed": False, "failures": ["fundamental_semantic_gate"]}

    result, changes = sanitize_forbidden_strong_actions(
        text, gate, "no_position", 0, 1,
    )

    assert "Buy/Sell/Hold" in result
    assert "未来已建仓" in result
    assert "止损失效风险" in result
    assert "止盈仅作参考" in result
    assert changes == []


def test_sanitize_only_preserves_explicit_action_enum_references():
    gate = {"passed": False, "failures": ["event_risk_block_open"]}

    classification, classification_changes = sanitize_forbidden_strong_actions(
        "分类标签：Buy/Sell/Hold。动作枚举：ENTER/WAIT/HOLD/REDUCE/EXIT。",
        gate,
        "has_position",
        0,
        1,
    )
    assert "Buy/Sell/Hold" in classification
    assert "ENTER/WAIT/HOLD/REDUCE/EXIT" in classification
    assert classification_changes == []

    for text in (
        "最终交易建议：BUY/HOLD",
        "Action: ENTER/WAIT",
        "Recommendation: BUY/SELL/HOLD",
    ):
        result, changes = sanitize_forbidden_strong_actions(
            text,
            gate,
            "has_position",
            0,
            1,
        )
        assert result != text
        assert "等待风险解除" in result
        assert changes


def test_entry_gates_filter_modifier_prefixed_actions():
    cases = (
        "建议先买入再观察。",
        "最终交易建议：先建仓。",
        "当前应优先买入。",
        "可直接买入。",
        "先低吸再观察。",
        "建议谨慎买入。",
        "应趁机买入。",
        "结论是谨慎买入。",
        "适合逐步买入。",
    )
    for failure, replacement in (
        ("fundamental_semantic_gate", "等待基本面证据复核"),
        ("event_risk_block_open", "等待风险解除"),
        ("估值基准价不可用(valuation_price_unavailable)", "等待可执行价格确认"),
    ):
        for text in cases:
            result, changes = sanitize_forbidden_strong_actions(
                text,
                {"passed": False, "failures": [failure]},
                "no_position",
                0,
                1,
            )
            body = result.split("⚠️ [D-002]", 1)[0]
            assert replacement in body
            assert text.strip("。") not in body
            assert changes


def test_no_position_filters_modifier_prefixed_holding_actions():
    for text in (
        "建议先减仓再观察。",
        "当前应优先清仓。",
        "风险较高，先止损再说。",
        "最终交易建议：先减仓。",
        "建议谨慎减仓。",
        "应趁机清仓。",
        "结论是谨慎止损。",
    ):
        result, changes = sanitize_forbidden_strong_actions(
            text,
            {"passed": True, "failures": []},
            "no_position",
            0,
            1,
        )
        body = result.split("⚠️ [D-002]", 1)[0]
        assert "该持仓动作不适用" in body
        assert text.strip("。") not in body
        assert changes


def test_sanitize_does_not_rewrite_c005_audit_history():
    llm_body = "最终结论：观望。"
    text = (
        llm_body + "\n\n"
        "⚠️ [C-005] 同股票结论翻转警告\n"
        "上一版结论：建议买入。\n本版结论：观望。\n\n"
        "📋 [C-008] 执行就绪度评分\n- 数据完整度：50%"
    )

    result, changes = sanitize_forbidden_strong_actions(
        text,
        {"passed": False, "failures": ["fundamental_semantic_gate"]},
        "no_position",
        0,
        1,
        trusted_system_offset=len(llm_body),
    )

    assert "上一版结论：建议买入" in result
    assert "⚠️ [D-002]" not in result
    assert changes == []


def test_sanitize_filters_explicit_decision_labels_and_decide_verbs():
    fundamental_gate = {"passed": False, "failures": ["fundamental_semantic_gate"]}
    for text in (
        "决策类型：买入",
        "最终交易决策：建仓",
        "本次决定买入。",
        "**最终交易建议**：**买入**",
        "**最终交易建议：** **买入**",
        "最终交易建议：**买入**",
        "**决策类型**：**买入**",
        "最终结论为买入",
    ):
        result, changes = sanitize_forbidden_strong_actions(
            text, fundamental_gate, "no_position", 0, 1,
        )
        assert text not in result
        assert "等待基本面证据复核" in result
        assert changes

    for text in (
        "决策类型：减仓",
        "最终交易决策：清仓",
        "本次决定卖出。",
        "**当前操作**：**减仓**",
        "**当前操作：** **减仓**",
        "最终结论为减仓",
    ):
        result, changes = sanitize_forbidden_strong_actions(
            text, {"passed": True, "failures": []}, "no_position", 0, 1,
        )
        assert text not in result
        assert "该持仓动作不适用" in result
        assert changes


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
    llm_body = "建议重仓买入。"
    text = llm_body + "📊 数据源可用性：\n  ✅ 市场"
    gate = {"passed": False, "failures": ["test"]}
    result, changes = sanitize_forbidden_strong_actions(
        text, gate, "has_position", 0, 0,
        trusted_system_offset=len(llm_body),
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


def test_fundamental_veto_filters_embedded_entry_conclusions():
    gate = {"passed": False, "failures": ["fundamental_semantic_gate"]}

    for phrase in (
        "综合判断以买入为主。",
        "策略上倾向买入。",
        "结论偏向建仓。",
        "首选增持。",
    ):
        sanitized, changes = sanitize_forbidden_strong_actions(
            phrase,
            gate=gate,
            position_status="no_position",
            buy_level=0,
            risk_level=1,
        )
        body = sanitized.split("⚠️ [D-002]", 1)[0]
        assert not any(action in body for action in ("买入", "建仓", "增持"))
        assert "等待基本面证据复核" in body
        assert changes


def test_entry_gates_filter_conditional_entry_instructions():
    cases = (
        "若突破18元买入。",
        "突破18元后建仓。",
        "价格站稳后加仓。",
    )
    for failure, replacement in (
        ("fundamental_semantic_gate", "等待基本面证据复核"),
        ("event_risk_block_open", "等待风险解除"),
        ("估值基准价不可用(valuation_price_unavailable)", "等待可执行价格确认"),
    ):
        for phrase in cases:
            sanitized, changes = sanitize_forbidden_strong_actions(
                phrase,
                gate={"passed": False, "failures": [failure]},
                position_status="no_position",
                buy_level=0,
                risk_level=1,
            )
            body = sanitized.split("⚠️ [D-002]", 1)[0]
            assert replacement in body
            assert not any(action in body for action in ("买入", "建仓", "加仓"))
            assert changes


def test_embedded_negative_preference_actions_are_preserved():
    entry_gate = {"passed": False, "failures": ["fundamental_semantic_gate"]}
    for phrase in (
        "并不首选买入。",
        "不首选建仓。",
        "从风险角度不优先增持。",
    ):
        sanitized, changes = sanitize_forbidden_strong_actions(
            phrase, entry_gate, "no_position", 0, 1,
        )
        assert phrase in sanitized
        assert changes == []

    for phrase in ("并不首选减仓。", "不优先清仓。"):
        sanitized, changes = sanitize_forbidden_strong_actions(
            phrase, {"passed": True, "failures": []}, "no_position", 0, 1,
        )
        assert phrase in sanitized
        assert changes == []


def test_no_position_filters_embedded_holding_only_conclusions():
    gate = {"passed": True, "failures": []}

    for phrase in (
        "仓位方面以减仓为主。",
        "风险应对以清仓为主。",
        "当前仓位宜止损。",
        "结论偏向卖出。",
    ):
        sanitized, changes = sanitize_forbidden_strong_actions(
            phrase,
            gate=gate,
            position_status="no_position",
            buy_level=0,
            risk_level=1,
        )
        body = sanitized.split("⚠️ [D-002]", 1)[0]
        assert not any(action in body for action in ("减仓", "清仓", "止损", "卖出"))
        assert "该持仓动作不适用，保持观察" in body
        assert changes


def test_action_enum_references_are_protected_before_all_sanitizers():
    enum_text = "分类标签：BUY/SELL/HOLD。动作枚举：ENTER/WAIT/HOLD/REDUCE/EXIT。"

    for gate in (
        {"passed": True, "failures": []},
        {"passed": False, "failures": ["fundamental_semantic_gate"]},
    ):
        sanitized, changes = sanitize_forbidden_strong_actions(
            enum_text,
            gate=gate,
            position_status="no_position",
            buy_level=0,
            risk_level=1,
        )
        assert enum_text in sanitized
        assert changes == []


def test_markdown_and_inline_action_enum_references_are_preserved():
    enum_texts = (
        "**分类标签**：**BUY/SELL/HOLD**",
        "- **动作枚举**：ENTER/WAIT/HOLD/REDUCE/EXIT",
        "若按 Buy/Sell/Hold 三分类标签映射，对应 Hold。",
    )
    for enum_text in enum_texts:
        sanitized, changes = sanitize_forbidden_strong_actions(
            enum_text,
            gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
            position_status="no_position",
            buy_level=0,
            risk_level=1,
        )
        assert enum_text in sanitized
        assert changes == []


def test_failed_gates_filter_mandatory_and_numbered_entry_actions():
    gate = {"passed": False, "failures": ["fundamental_semantic_gate"]}
    for phrase in (
        "最终交易建议：必须买入",
        "当前操作：请买入",
        "坚决买入。",
        "1. 买入",
        "（1）建仓",
    ):
        sanitized, changes = sanitize_forbidden_strong_actions(
            phrase, gate, "no_position", 0, 1,
        )
        body = sanitized.split("⚠️ [D-002]", 1)[0]
        assert not any(action in body for action in ("买入", "建仓"))
        assert "等待基本面证据复核" in body
        assert changes


def test_failed_gates_filter_location_qualified_entry_actions():
    gate = {"passed": False, "failures": ["fundamental_semantic_gate"]}
    for phrase in (
        "建议在18元附近买入。",
        "建议逢低逐步买入。",
        "需在回调时买入。",
        "需要在18元附近建仓。",
        "请在确认突破后入场。",
    ):
        sanitized, changes = sanitize_forbidden_strong_actions(
            phrase, gate, "no_position", 0, 1,
        )
        body = sanitized.split("⚠️ [D-002]", 1)[0]
        assert "买入" not in body
        assert "等待基本面证据复核" in body
        assert changes


def test_no_position_filters_mandatory_and_numbered_holding_actions():
    for phrase in (
        "最终交易决策：必须减仓",
        "当前操作：无条件清仓",
        "请清仓。",
        "1. 减仓",
        "(1) 止损",
    ):
        sanitized, changes = sanitize_forbidden_strong_actions(
            phrase,
            gate={"passed": True, "failures": []},
            position_status="no_position",
            buy_level=0,
            risk_level=1,
        )
        body = sanitized.split("⚠️ [D-002]", 1)[0]
        assert not any(action in body for action in ("减仓", "清仓", "止损"))
        assert "该持仓动作不适用，保持观察" in body
        assert changes


def test_no_position_filters_location_qualified_holding_actions():
    for phrase in (
        "投资者可在反弹高位减仓。",
        "风险控制方面可在高位卖出。",
        "需在高位卖出。",
        "需要在跌破支撑后止损。",
        "请在冲高时减仓。",
    ):
        sanitized, changes = sanitize_forbidden_strong_actions(
            phrase,
            gate={"passed": True, "failures": []},
            position_status="no_position",
            buy_level=0,
            risk_level=1,
        )
        body = sanitized.split("⚠️ [D-002]", 1)[0]
        assert not any(action in body for action in ("减仓", "卖出"))
        assert "该持仓动作不适用，保持观察" in body
        assert changes


def test_failed_entry_gate_filters_current_planned_entry_actions():
    gate = {"passed": False, "failures": ["fundamental_semantic_gate"]}
    for phrase in (
        "计划买入。",
        "准备建仓。",
        "打算入场。",
        "拟增持。",
        "将买入。",
    ):
        sanitized, changes = sanitize_forbidden_strong_actions(
            phrase, gate, "no_position", 0, 1,
        )
        body = sanitized.split("⚠️ [D-002]", 1)[0]
        assert phrase not in body
        assert "等待基本面证据复核" in body
        assert changes


def test_no_position_filters_current_planned_holding_actions():
    for phrase in (
        "投资者计划减仓。",
        "准备清仓。",
        "打算卖出。",
        "拟止盈。",
        "将止损。",
    ):
        sanitized, changes = sanitize_forbidden_strong_actions(
            phrase,
            gate={"passed": True, "failures": []},
            position_status="no_position",
            buy_level=0,
            risk_level=1,
        )
        body = sanitized.split("⚠️ [D-002]", 1)[0]
        assert phrase not in body
        assert "该持仓动作不适用，保持观察" in body
        assert changes


def test_failed_entry_gate_filters_opinion_form_entry_actions():
    gate = {"passed": False, "failures": ["fundamental_semantic_gate"]}
    for phrase in (
        "我认为买入是最佳选择。",
        "我的观点是买入。",
        "本基金认为应买入。",
        "考虑后买入。",
    ):
        sanitized, changes = sanitize_forbidden_strong_actions(
            phrase, gate, "no_position", 0, 1,
        )
        body = sanitized.split("⚠️ [D-002]", 1)[0]
        assert "买入" not in body
        assert "等待基本面证据复核" in body
        assert changes


def test_no_position_filters_opinion_form_holding_actions():
    for phrase in (
        "我认为减仓更合适。",
        "我的观点是清仓。",
        "本基金认为应卖出。",
        "考虑后止损。",
    ):
        sanitized, changes = sanitize_forbidden_strong_actions(
            phrase,
            gate={"passed": True, "failures": []},
            position_status="no_position",
            buy_level=0,
            risk_level=1,
        )
        body = sanitized.split("⚠️ [D-002]", 1)[0]
        assert not any(action in body for action in ("减仓", "清仓", "卖出", "止损"))
        assert "该持仓动作不适用，保持观察" in body
        assert changes


def test_failed_entry_gate_filters_conditional_subject_actions():
    gate = {"passed": False, "failures": ["fundamental_semantic_gate"]}
    for phrase in (
        "趋势确认后我会买入。",
        "财报发布后我们将建仓。",
        "风险解除时本基金计划加仓。",
    ):
        sanitized, changes = sanitize_forbidden_strong_actions(
            phrase, gate, "no_position", 0, 1,
        )
        body = sanitized.split("⚠️ [D-002]", 1)[0]
        assert not any(action in body for action in ("买入", "建仓", "加仓"))
        assert "等待基本面证据复核" in body
        assert changes


def test_no_position_filters_conditional_subject_holding_actions():
    for phrase in (
        "反弹后我会减仓。",
        "风险扩大后我们将清仓。",
        "跌破支撑时本基金计划止损。",
    ):
        sanitized, changes = sanitize_forbidden_strong_actions(
            phrase,
            gate={"passed": True, "failures": []},
            position_status="no_position",
            buy_level=0,
            risk_level=1,
        )
        body = sanitized.split("⚠️ [D-002]", 1)[0]
        assert not any(action in body for action in ("减仓", "清仓", "止损"))
        assert "该持仓动作不适用，保持观察" in body
        assert changes


def test_planned_action_filter_preserves_hypothetical_future_descriptions():
    gate = {"passed": False, "failures": ["fundamental_semantic_gate"]}
    for phrase in (
        "若未来持仓，计划减仓。",
        "需判断后续是否准备建仓。",
    ):
        sanitized, changes = sanitize_forbidden_strong_actions(
            phrase, gate, "no_position", 0, 1,
        )
        assert phrase in sanitized
        assert changes == []


@pytest.mark.parametrize(
    "phrase, forbidden",
    (
        ("讨论是否买入，结论建议买入。", "结论建议买入"),
        ("研究问题是能否建仓，最终建议建仓。", "最终建议建仓"),
        ("买入后持有3天，最终建议清仓。", "最终建议清仓"),
        ("未来持仓后再评估，当前动作：卖出。", "当前动作：卖出"),
    ),
)
def test_protected_discussion_does_not_hide_later_current_action(phrase, forbidden):
    sanitized, changes = sanitize_forbidden_strong_actions(
        phrase,
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    body = sanitized.split("⚠️ [D-002]", 1)[0]
    assert forbidden not in body
    assert changes


@pytest.mark.parametrize(
    "phrase, forbidden",
    (
        ("系统指令：买入。", "买入"),
        ("系统指令：卖出。", "卖出"),
    ),
)
def test_sanitizer_handles_explicit_system_instruction_labels(phrase, forbidden):
    sanitized, changes = sanitize_forbidden_strong_actions(
        phrase,
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    body = sanitized.split("⚠️ [D-002]", 1)[0]
    assert forbidden not in body
    assert changes


@pytest.mark.parametrize(
    "phrase",
    (
        "买入需求疲弱。",
        "买入行为明显增加。",
        "卖出需求上升。",
        "卖出行为增加。",
        "减仓意愿较弱。",
        "止损需求增加。",
    ),
)
def test_sanitizer_preserves_analytical_action_nouns(phrase):
    sanitized, changes = sanitize_forbidden_strong_actions(
        phrase,
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    assert sanitized == phrase
    assert changes == []


@pytest.mark.parametrize(
    "phrase, residual",
    (
        ("立即采取止损离场方案。", "离场方案"),
        ("当前建议执行止损离场策略。", "离场策略"),
        ("建议采用止损离场方案。", "离场方案"),
        ("最终操作：止盈退出策略。", "退出策略"),
        ("必须采取止损离场方案。", "离场方案"),
        ("可以采取止损离场方案。", "离场方案"),
        ("当前操作：将采取止损离场方案。", "离场方案"),
        ("交易员必须执行止损离场方案。", "离场方案"),
        ("建议制定止损离场方案。", "离场方案"),
        ("建议设置严格的止损离场方案。", "离场方案"),
        ("我们需要建立止损离场机制。", "离场机制"),
        ("最终决定启动止损离场策略。", "离场策略"),
        ("请采取止损离场方案。", "离场方案"),
        ("考虑止损离场策略。", "离场策略"),
        ("倾向采用止盈退出策略。", "退出策略"),
        ("首选止损离场方案。", "离场方案"),
        ("后续可采取止损离场策略。", "离场策略"),
        ("采取止损离场方案。", "离场方案"),
        ("采用止损离场策略。", "离场策略"),
        ("建立严格的止损离场机制。", "离场机制"),
        ("启动止盈退出策略。", "退出策略"),
        ("现阶段采取止损离场方案。", "离场方案"),
        ("保守起见采用止损离场策略。", "离场策略"),
        ("风险过高，因此采取止损离场方案。", "离场方案"),
        ("分析认为止损离场方案应立即执行。", "离场方案"),
        ("模型指出止损离场策略必须启动。", "离场策略"),
    ),
)
def test_imperative_exit_plan_is_not_protected_as_analytical_noun(phrase, residual):
    sanitized, changes = sanitize_forbidden_strong_actions(
        phrase,
        gate={"passed": False, "failures": ["evidence_coverage=50% < 70%"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    body = sanitized.split("⚠️ [D-002]", 1)[0]
    assert residual not in body
    assert "该持仓动作不适用，保持观察" in body
    assert changes


@pytest.mark.parametrize(
    "phrase",
    (
        "若未来建仓，跌破9元则清仓。",
        "如果以后买入，跌破9元就止损。",
        "建仓成功的情况下，跌破9元清仓。",
    ),
)
def test_no_position_preserves_common_future_entry_exit_plans(phrase):
    sanitized, changes = sanitize_forbidden_strong_actions(
        phrase,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    assert sanitized == phrase
    assert changes == []


@pytest.mark.parametrize(
    "phrase",
    ("风险解除前计划买入。", "基本面门禁解除前准备建仓。"),
)
def test_failed_gate_sanitizes_entry_plan_deferred_until_resolution(phrase):
    sanitized, changes = sanitize_forbidden_strong_actions(
        phrase,
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    body = sanitized.split("⚠️ [D-002]", 1)[0]
    assert not any(action in body for action in ("买入", "建仓"))
    assert "等待基本面证据复核" in body
    assert changes


@pytest.mark.parametrize(
    "phrase, action",
    (
        ("系统将买入。", "买入"),
        ("交易员将建仓。", "建仓"),
        ("系统将卖出。", "卖出"),
    ),
)
def test_sanitizer_handles_system_and_agent_subjects(phrase, action):
    sanitized, changes = sanitize_forbidden_strong_actions(
        phrase,
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    body = sanitized.split("⚠️ [D-002]", 1)[0]
    assert action not in body
    assert changes


@pytest.mark.parametrize(
    "phrase, label",
    (
        ("交易计划：买入。", "交易计划："),
        ("执行方案：建仓。", "执行方案："),
        ("仓位计划：减仓。", "仓位计划："),
        ("交易员动作：卖出。", "交易员动作："),
    ),
)
def test_sanitizer_preserves_action_labels(phrase, label):
    sanitized, changes = sanitize_forbidden_strong_actions(
        phrase,
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    body = sanitized.split("⚠️ [D-002]", 1)[0]
    assert label in body
    assert changes


def test_no_position_preserves_post_entry_protective_exit_plan():
    phrase = "若站稳10元则买入；买入后计划在跌破9元时止损。"
    sanitized, _changes = sanitize_forbidden_strong_actions(
        phrase,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=2,
        risk_level=0,
    )

    assert "买入后计划在跌破9元时止损" in sanitized
    assert "该持仓动作不适用" not in sanitized


@pytest.mark.parametrize("phrase", ("当前不考虑买入。", "当前不准备建仓。"))
def test_failed_entry_gate_preserves_modal_negated_entry(phrase):
    sanitized, changes = sanitize_forbidden_strong_actions(
        phrase,
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    assert sanitized == phrase
    assert changes == []


def test_fundamental_gate_preserves_non_executable_entry_discussion():
    gate = {"passed": False, "failures": ["fundamental_semantic_gate"]}
    phrases = (
        "不确定突破后是否买入。",
        "需判断突破后能否买入。",
        "讨论突破后买入的可行性。",
        "尚不能确定突破后买入是否合适。",
        "研究问题是突破后何时买入。",
        "若突破后不买入。",
    )
    for phrase in phrases:
        sanitized, changes = sanitize_forbidden_strong_actions(
            phrase, gate, "no_position", 0, 1,
        )
        assert phrase in sanitized
        assert changes == []


def test_failed_entry_gate_does_not_mask_resolved_discussion_as_a_question():
    phrases = (
        "不确定性下降，因此建议买入。",
        "不确定因素已经消除，因此可以买入。",
        "需判断的风险已解除，建议建仓。",
        "讨论结束后，建议买入。",
        "研究问题已解决，可以入场。",
    )
    for failure, replacement in (
        ("fundamental_semantic_gate", "等待基本面证据复核"),
        ("event_risk_block_open", "等待风险解除"),
    ):
        for phrase in phrases:
            sanitized, changes = sanitize_forbidden_strong_actions(
                phrase,
                {"passed": False, "failures": [failure]},
                "no_position",
                0,
                1,
            )
            body = sanitized.split("⚠️ [D-002]", 1)[0]
            assert replacement in body, (failure, phrase, body)
            assert not any(
                action in body
                for action in ("建议买入", "可以买入", "建议建仓", "可以入场")
            )
            assert changes


def test_fundamental_gate_preserves_analytical_buy_facts():
    gate = {"passed": False, "failures": ["fundamental_semantic_gate"]}
    for phrase in (
        "买入评级机构共有5家。",
        "买入评级维持不变。",
        "买入成交额为1000万元。",
        "买入资金占比为20%。",
        "买入意愿较弱。",
        "买入盘较强。",
        "买入逻辑尚不充分。",
    ):
        sanitized, changes = sanitize_forbidden_strong_actions(
            phrase, gate, "no_position", 0, 1,
        )
        assert phrase in sanitized
        assert changes == []


@pytest.mark.parametrize(
    "phrase",
    (
        "交易方向：买入信号增强。",
        "最终结论：买入逻辑尚不充分。",
        "交易员动作：买入条件仍未满足。",
    ),
)
def test_fundamental_gate_preserves_labeled_analytical_buy_facts(phrase):
    sanitized, changes = sanitize_forbidden_strong_actions(
        phrase,
        {"passed": False, "failures": ["fundamental_semantic_gate"]},
        "no_position",
        0,
        1,
    )

    assert sanitized == phrase
    assert changes == []


def test_fundamental_gate_preserves_conditional_buy_analysis_nouns():
    gate = {"passed": False, "failures": ["fundamental_semantic_gate"]}
    for phrase in (
        "盈利恢复后，买入逻辑才可能成立。",
        "基本面改善后买入逻辑才成立，目前继续观望。",
        "风险解除后可重新评估买入条件是否成立。",
        "业绩改善后是否值得买入仍需讨论。",
    ):
        sanitized, changes = sanitize_forbidden_strong_actions(
            phrase, gate, "no_position", 0, 1,
        )
        assert phrase in sanitized
        assert changes == []


def test_no_position_preserves_sell_analysis_nouns():
    for phrase in (
        "卖出压力增大。",
        "- 卖出压力：主力流出。",
        "卖出信号尚未出现。",
        "减仓风险需评估。",
    ):
        sanitized, changes = sanitize_forbidden_strong_actions(
            phrase,
            {"passed": True, "failures": []},
            "no_position",
            1,
            0,
        )
        assert phrase in sanitized
        assert changes == []


def test_visible_execution_summary_honors_exit_side_constraint_blocker():
    summary = _format_visible_execution_summary(
        trade_quality_check={
            "action": "人工复核",
            "do_not_trade_if": ["交易计划违反用户硬约束：禁止卖出"],
        },
        gate={"passed": True, "failures": []},
        position_status="has_position",
        buy_level=1,
        risk_level=2,
    )

    assert "系统动作：WAIT/等待人工复核" in summary
    assert "系统动作：条件减仓" not in summary


def test_d002_note_is_appended_inside_trusted_system_diagnostics():
    llm_body = "最终交易建议：买入。"
    delimiter = "<!-- TA_SYSTEM_DIAGNOSTICS_START -->"
    text = f"{llm_body}\n\n{delimiter}\n### 执行质检"

    sanitized, changes = sanitize_forbidden_strong_actions(
        text,
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
        trusted_system_offset=len(llm_body),
    )

    delimiter_index = sanitized.index(delimiter)
    d002_index = sanitized.index("⚠️ [D-002]")
    assert d002_index > delimiter_index
    assert "等待基本面证据复核" in sanitized[:delimiter_index]
    assert "买入" not in sanitized[:delimiter_index]
    assert changes


def test_sanitizer_returns_application_owned_system_boundary():
    delimiter = "<!-- TA_SYSTEM_DIAGNOSTICS_START -->"
    llm_body = f"模型引用 {delimiter}。最终交易建议：买入。"
    text = f"{llm_body}\n\n{delimiter}\n### 执行质检"

    sanitized, changes, system_offset = sanitize_forbidden_strong_actions(
        text,
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
        trusted_system_offset=len(llm_body),
        return_system_offset=True,
    )

    assert changes
    assert system_offset is not None
    assert sanitized[:system_offset].count(delimiter) == 1
    assert sanitized[system_offset:].lstrip().startswith(delimiter)


def test_execution_quality_block_is_not_resanitized():
    llm_body = "最终结论：观察。"
    text = (
        llm_body + "\n\n"
        "### 执行质检\n"
        "- 历史分类标签：BUY/SELL/HOLD\n"
        "- 假设持仓动作：减仓"
    )

    sanitized, changes = sanitize_forbidden_strong_actions(
        text,
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
        trusted_system_offset=len(llm_body),
    )

    assert "### 执行质检" in sanitized
    assert "历史分类标签：BUY/SELL/HOLD" in sanitized
    assert "假设持仓动作：减仓" in sanitized
    assert changes == []


def test_llm_emitted_system_marker_does_not_bypass_action_gate():
    text = "### 执行质检\n最终交易建议：买入"

    sanitized, changes = sanitize_forbidden_strong_actions(
        text,
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    body = sanitized.split("⚠️ [D-002]", 1)[0]
    assert "最终交易建议：买入" not in body
    assert "等待基本面证据复核" in body
    assert changes


@pytest.mark.parametrize(
    "phrase",
    (
        "我们倾向于买入。",
        "宜买入。",
        "主张买入。",
        "首选方案为买入。",
        "继续买入。",
        "维持买入。",
        "建议高抛低吸。",
        "操作以买入为主。",
    ),
)
def test_fundamental_gate_catches_inline_entry_recommendations(phrase):
    sanitized, changes = sanitize_forbidden_strong_actions(
        phrase,
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    body = sanitized.split("⚠️ [D-002]", 1)[0]
    assert phrase.rstrip("。") not in body
    assert "等待基本面证据复核" in body
    assert changes


@pytest.mark.parametrize(
    "phrase",
    (
        "建议客户买入。",
        "建议持币者建仓。",
        "推荐大家加仓。",
        "可供投资者买入。",
        "我的选择：买入。",
    ),
)
def test_fundamental_gate_catches_recipient_qualified_entry_actions(phrase):
    sanitized, changes = sanitize_forbidden_strong_actions(
        phrase,
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    body = sanitized.split("⚠️ [D-002]", 1)[0]
    assert phrase.rstrip("。") not in body
    assert "等待基本面证据复核" in body
    assert changes


@pytest.mark.parametrize(
    "phrase",
    (
        "建议逢高减仓。",
        "建议反弹卖出。",
        "逢高减仓。",
        "反弹卖出。",
        "倾向于减仓。",
        "继续减仓。",
        "维持减仓。",
        "主张卖出。",
        "当前宜止损。",
        "建议高抛。",
        "仓位应减至半仓。",
    ),
)
def test_no_position_catches_qualified_holding_actions(phrase):
    sanitized, changes = sanitize_forbidden_strong_actions(
        phrase,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    body = sanitized.split("⚠️ [D-002]", 1)[0]
    assert phrase.rstrip("。") not in body
    assert "该持仓动作不适用，保持观察" in body
    assert changes


@pytest.mark.parametrize(
    "phrase",
    (
        "建议投资者减仓。",
        "建议用户卖出。",
        "建议客户止损。",
        "推荐持币者清仓。",
        "我的选择：卖出。",
    ),
)
def test_no_position_catches_recipient_qualified_holding_actions(phrase):
    sanitized, changes = sanitize_forbidden_strong_actions(
        phrase,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    body = sanitized.split("⚠️ [D-002]", 1)[0]
    assert phrase.rstrip("。") not in body
    assert "该持仓动作不适用，保持观察" in body
    assert changes


def test_no_position_preserves_future_holding_plan_after_new_directives():
    phrase = "若未来持仓，计划在反弹高位减仓。"
    sanitized, changes = sanitize_forbidden_strong_actions(
        phrase,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    assert phrase in sanitized
    assert changes == []


@pytest.mark.parametrize(
    "phrase",
    (
        "风险较高，因此减仓。",
        "所以止损。",
        "据此建议清仓。",
        "我的决定是卖出。",
    ),
)
def test_no_position_catches_causal_and_first_person_holding_actions(phrase):
    sanitized, changes = sanitize_forbidden_strong_actions(
        phrase,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    assert phrase.rstrip("。") not in sanitized
    assert "该持仓动作不适用，保持观察" in sanitized
    assert changes


@pytest.mark.parametrize(
    "phrase",
    (
        "风险收益比合理，因此买入。",
        "所以建仓。",
        "据此建议入场。",
        "我的决定是买入。",
        "该股值得买入。",
    ),
)
def test_entry_veto_catches_causal_and_first_person_buy_actions(phrase):
    sanitized, changes = sanitize_forbidden_strong_actions(
        phrase,
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    body = sanitized.split("⚠️ [D-002]", 1)[0]
    assert phrase.rstrip("。") not in body
    assert "等待基本面证据复核" in body
    assert changes


@pytest.mark.parametrize(
    "phrase",
    (
        "值得买入。",
        "最好买入。",
        "不妨买入。",
        "不如建仓。",
        "最终意见：买入。",
        "我的意见是买入。",
        "交易意见：建仓。",
        "处理意见：入场。",
    ),
)
def test_entry_veto_catches_evaluative_and_opinion_buy_actions(phrase):
    sanitized, changes = sanitize_forbidden_strong_actions(
        phrase,
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    body = sanitized.split("⚠️ [D-002]", 1)[0]
    assert phrase.rstrip("。") not in body
    assert "等待基本面证据复核" in body
    assert changes


@pytest.mark.parametrize(
    "phrase",
    (
        "不值得买入。",
        "是否值得买入仍需讨论。",
        "该股是否值得买入仍待评估。",
    ),
)
def test_entry_veto_preserves_negated_or_analytical_worth_questions(phrase):
    sanitized, changes = sanitize_forbidden_strong_actions(
        phrase,
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    assert phrase in sanitized
    assert changes == []


@pytest.mark.parametrize(
    "phrase",
    (
        "交易方向定为买入。",
        "下一步选买入。",
        "我的动作选买入。",
        "这意味着买入。",
        "结论支持买入。",
        "我投票买入。",
        "偏向买入。",
        "明天择机介入。",
        "这个位置买入即可。",
        "不排除买入。",
    ),
)
def test_entry_veto_catches_embedded_directive_forms(phrase):
    sanitized, changes = sanitize_forbidden_strong_actions(
        phrase,
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    body = sanitized.split("⚠️ [D-002]", 1)[0]
    assert phrase.rstrip("。") not in body
    assert "等待基本面证据复核" in body
    assert changes


@pytest.mark.parametrize(
    "phrase",
    ("我赞成买入。", "我支持买入。", "我同意买入。", "我认可建仓。"),
)
def test_entry_veto_catches_assent_form_entry_directives(phrase):
    sanitized, changes = sanitize_forbidden_strong_actions(
        phrase,
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    body = sanitized.split("⚠️ [D-002]", 1)[0]
    assert phrase.rstrip("。") not in body
    assert "等待基本面证据复核" in body
    assert changes


@pytest.mark.parametrize(
    "phrase, expected",
    (
        ("最终买入。", "最终等待基本面证据复核。"),
        ("若站稳10元后买入。", "若站稳10元后等待基本面证据复核。"),
        ("触发后建议买入。", "触发后等待基本面证据复核。"),
    ),
)
def test_entry_veto_preserves_directive_prefixes(phrase, expected):
    sanitized, changes = sanitize_forbidden_strong_actions(
        phrase,
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    body = sanitized.split("⚠️ [D-002]", 1)[0].strip()
    assert body == expected
    assert changes


@pytest.mark.parametrize(
    "phrase",
    ("我不支持买入。", "机构支持买入评级。", "机构认可买入评级。"),
)
def test_entry_veto_preserves_negated_or_analytical_assent_text(phrase):
    sanitized, changes = sanitize_forbidden_strong_actions(
        phrase,
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    assert phrase in sanitized
    assert changes == []


@pytest.mark.parametrize(
    "phrase",
    (
        "交易方向定为清仓。",
        "下一步选卖出。",
        "我的动作选减仓。",
        "这意味着止损。",
        "结论支持卖出。",
        "我投票清仓。",
        "偏向减仓。",
        "明天择机卖出。",
    ),
)
def test_no_position_gate_catches_embedded_exit_directives(phrase):
    sanitized, changes = sanitize_forbidden_strong_actions(
        phrase,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    body = sanitized.split("⚠️ [D-002]", 1)[0]
    assert phrase.rstrip("。") not in body
    assert "该持仓动作不适用，保持观察" in body
    assert changes


@pytest.mark.parametrize(
    "phrase",
    ("我赞成卖出。", "我支持清仓。", "我同意减仓。", "我认可止损。"),
)
def test_no_position_gate_catches_assent_form_exit_directives(phrase):
    sanitized, changes = sanitize_forbidden_strong_actions(
        phrase,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    body = sanitized.split("⚠️ [D-002]", 1)[0]
    assert phrase.rstrip("。") not in body
    assert "该持仓动作不适用，保持观察" in body
    assert changes


@pytest.mark.parametrize(
    "phrase",
    (
        "买入回报预期偏低。",
        "买入订单数量上升。",
        "买入成本过高。",
        "买入评级机构共有5家。",
    ),
)
def test_entry_veto_preserves_analytical_action_nouns(phrase):
    sanitized, changes = sanitize_forbidden_strong_actions(
        phrase,
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    assert phrase in sanitized
    assert changes == []


@pytest.mark.parametrize(
    "text, forbidden",
    (
        ("系统指令：创建买入订单。", "买入订单"),
        ("最终建议：提交买入订单100股。", "买入订单"),
        ("执行买入订单数量100股。", "买入订单"),
        ("提交买入订单金额1万元。", "买入订单"),
    ),
)
def test_executable_buy_orders_are_downgraded_when_gate_fails(text, forbidden):
    sanitized, changes = sanitize_forbidden_strong_actions(
        text,
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    assert forbidden not in sanitized
    assert "等待" in sanitized
    assert changes


@pytest.mark.parametrize(
    "text, forbidden",
    (
        ("最终建议：提交卖出订单。", "卖出订单"),
        ("系统指令：执行清仓订单。", "清仓订单"),
        ("下一步创建减仓订单。", "减仓订单"),
        ("执行卖出订单数量100股。", "卖出订单"),
    ),
)
def test_no_position_executable_sell_orders_are_downgraded(text, forbidden):
    sanitized, changes = sanitize_forbidden_strong_actions(
        text,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    assert forbidden not in sanitized
    assert "该持仓动作不适用" in sanitized
    assert changes


@pytest.mark.parametrize(
    "text, forbidden",
    (
        ("未来买入后设置止损，最终裁决：清仓。", "最终裁决：清仓"),
        ("未来建仓后控制风险，风控结论：卖出。", "风控结论：卖出"),
        ("若未来持仓，交易员动作：减仓。", "交易员动作：减仓"),
    ),
)
def test_current_action_labels_end_hypothetical_holding_mask(text, forbidden):
    sanitized, changes = sanitize_forbidden_strong_actions(
        text,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    assert forbidden not in sanitized
    assert "该持仓动作不适用" in sanitized
    assert changes


@pytest.mark.parametrize(
    "text",
    (
        "未来持仓风险较高，但现在建议清仓。",
        "未来持仓成本未知，所以目前应减仓。",
        "买入后风险很高，现在立刻卖出。",
    ),
)
def test_current_directive_ends_hypothetical_holding_mask(text):
    sanitized, changes = sanitize_forbidden_strong_actions(
        text,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    assert "该持仓动作不适用，保持观察" in sanitized
    assert changes


@pytest.mark.parametrize(
    "text",
    (
        "明日我们将买入。",
        "今天投资者应当建仓。",
        "下一交易日用户计划入场。",
    ),
)
def test_entry_gate_catches_actor_qualified_timed_actions(text):
    sanitized, changes = sanitize_forbidden_strong_actions(
        text,
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    assert text.rstrip("。") not in sanitized
    assert "等待基本面证据复核" in sanitized
    assert changes


@pytest.mark.parametrize(
    "text",
    (
        "明日我们将清仓。",
        "今天投资者应当卖出。",
        "下一交易日用户计划减仓。",
    ),
)
def test_no_position_catches_actor_qualified_timed_exits(text):
    sanitized, changes = sanitize_forbidden_strong_actions(
        text,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    assert text.rstrip("。") not in sanitized
    assert "该持仓动作不适用，保持观察" in sanitized
    assert changes


def test_completed_order_quantity_remains_an_analytical_fact():
    phrase = "已执行买入订单数量100股。"
    sanitized, changes = sanitize_forbidden_strong_actions(
        phrase,
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    assert sanitized == phrase
    assert changes == []


@pytest.mark.parametrize(
    "phrase",
    (
        "条件满足后买入。",
        "等待风险解除后再买入。",
        "确认无误后投资者买入。",
    ),
)
def test_entry_veto_catches_generic_conditional_buy_actions(phrase):
    sanitized, changes = sanitize_forbidden_strong_actions(
        phrase,
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    body = sanitized.split("⚠️ [D-002]", 1)[0]
    assert phrase.rstrip("。") not in body
    assert "等待基本面证据复核" in body
    assert changes


@pytest.mark.parametrize(
    "phrase",
    (
        "公司董事会决定增持股份，以彰显长期信心。",
        "控股股东决定买入公司股票。",
        "北向资金今日买入1000万元。",
        "高管计划增持100万股。",
        "员工持股计划拟增持公司股份。",
        "董监高拟增持公司股票。",
    ),
)
def test_entry_veto_preserves_third_party_entry_facts(phrase):
    sanitized, changes = sanitize_forbidden_strong_actions(
        phrase,
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    assert phrase in sanitized
    assert changes == []


@pytest.mark.parametrize(
    "phrase",
    (
        "买入订单已成交。",
        "今日买入订单已全部成交。",
        "卖出订单已经成交。",
    ),
)
def test_action_sanitizer_preserves_completed_order_facts(phrase):
    sanitized, changes = sanitize_forbidden_strong_actions(
        phrase,
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    assert phrase in sanitized
    assert changes == []


@pytest.mark.parametrize(
    "phrase",
    (
        "主力资金连续流入后可轻仓买入。",
        "机构资金回流后建议建仓。",
        "公司基本面改善后可以买入。",
    ),
)
def test_entry_veto_does_not_exempt_catalyst_based_recommendations(phrase):
    sanitized, changes = sanitize_forbidden_strong_actions(
        phrase,
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    body = sanitized.split("⚠️ [D-002]", 1)[0]
    assert phrase.rstrip("。") not in body
    assert "等待基本面证据复核" in body
    assert changes


@pytest.mark.parametrize(
    "phrase",
    (
        "主力资金今日流入后建议买入。",
        "机构资金今日回流后可买入。",
        "公司今日发布利好后建议买入。",
    ),
)
def test_entry_veto_does_not_mask_timed_catalyst_recommendations(phrase):
    sanitized, changes = sanitize_forbidden_strong_actions(
        phrase,
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    body = sanitized.split("⚠️ [D-002]", 1)[0]
    assert phrase.rstrip("。") not in body
    assert "等待基本面证据复核" in body
    assert changes


@pytest.mark.parametrize(
    "phrase",
    (
        "最终结论：以买入为主。",
        "建议以买入为主。",
        "当前最好买入为主。",
    ),
)
def test_entry_veto_catches_linked_buy_recommendations(phrase):
    sanitized, changes = sanitize_forbidden_strong_actions(
        phrase,
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    body = sanitized.split("⚠️ [D-002]", 1)[0]
    assert phrase.rstrip("。") not in body
    assert "等待基本面证据复核" in body
    assert changes


@pytest.mark.parametrize(
    "phrase",
    (
        "若回调则择机买入。",
        "当前策略建议维持买入。",
        "短期可做买入处理。",
        "风险收益较好，可予以买入。",
        "主张在10元附近买入。",
        "宜于10元附近买入。",
        "适宜在10元附近买入。",
        "倾向于在10元附近买入。",
        "后续宜在10元附近建仓。",
    ),
)
def test_entry_veto_catches_general_executable_buy_recommendations(phrase):
    sanitized, changes = sanitize_forbidden_strong_actions(
        phrase,
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    body = sanitized.split("⚠️ [D-002]", 1)[0]
    assert phrase.rstrip("。") not in body
    assert "等待基本面证据复核" in body
    assert changes


@pytest.mark.parametrize(
    "phrase",
    (
        "最终结论：以减仓为主。",
        "建议以减仓为主。",
        "当前最好减仓为主。",
    ),
)
def test_no_position_catches_linked_holding_recommendations(phrase):
    sanitized, changes = sanitize_forbidden_strong_actions(
        phrase,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    body = sanitized.split("⚠️ [D-002]", 1)[0]
    assert phrase.rstrip("。") not in body
    assert "该持仓动作不适用，保持观察" in body
    assert changes


@pytest.mark.parametrize(
    "phrase",
    (
        "当前策略建议维持减仓。",
        "短期可做减仓处理。",
        "风险较高，应予以减仓。",
        "主张在高位卖出。",
        "宜在高位减仓。",
        "倾向于高位清仓。",
        "后续宜在高位止盈。",
    ),
)
def test_no_position_catches_general_holding_recommendations(phrase):
    sanitized, changes = sanitize_forbidden_strong_actions(
        phrase,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    body = sanitized.split("⚠️ [D-002]", 1)[0]
    assert phrase.rstrip("。") not in body
    assert "该持仓动作不适用，保持观察" in body
    assert changes


@pytest.mark.parametrize(
    "phrase",
    (
        "风险扩大后再清仓。",
        "触发信号后投资者减仓。",
        "条件满足时客户卖出。",
    ),
)
def test_no_position_catches_conditional_holding_actions(phrase):
    sanitized, changes = sanitize_forbidden_strong_actions(
        phrase,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    assert phrase.rstrip("。") not in sanitized
    assert "该持仓动作不适用，保持观察" in sanitized
    assert changes


@pytest.mark.parametrize("risk_level", (0, 1, 2, 3))
def test_entry_only_veto_downgrades_unsupported_forced_exit(risk_level):
    sanitized, changes = sanitize_forbidden_strong_actions(
        "最终结论：建议立即清仓。",
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="has_position",
        buy_level=0,
        risk_level=risk_level,
    )

    assert "建议立即清仓" not in sanitized
    assert "等待触发条件，暂不执行强清仓" in sanitized
    assert changes


def test_entry_only_veto_preserves_level_four_forced_exit():
    text = "最终结论：建议立即清仓。"
    sanitized, changes = sanitize_forbidden_strong_actions(
        text,
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="has_position",
        buy_level=0,
        risk_level=4,
    )

    assert text in sanitized
    assert changes == []


def test_visible_execution_summary_keeps_authoritative_wait_in_main_report():
    summary = _format_visible_execution_summary(
        trade_quality_check={"action": "等待人工复核"},
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    assert "系统动作：WAIT/观察" in summary
    assert "强动作门禁：未通过" in summary
    assert "Buy Level：0" in summary
    assert "Risk Level：1" in summary


def test_visible_execution_summary_preserves_authorized_conditional_entry():
    summary = _format_visible_execution_summary(
        trade_quality_check={
            "action": "等待触发",
            "execution_mode": "条件触发",
            "entry_intent": True,
            "conditional_entry_intent": True,
            "trigger_price": 42.5,
            "stop_loss_price": 40.0,
            "do_not_trade_if": [],
        },
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=2,
        risk_level=0,
    )

    assert "系统动作：ENTER/条件入场" in summary
    assert "强动作门禁：通过" in summary


def test_visible_execution_summary_honors_risk_level_one_opening_veto():
    summary = _format_visible_execution_summary(
        trade_quality_check={
            "action": "等待触发",
            "execution_mode": "条件触发",
            "entry_intent": True,
            "conditional_entry_intent": True,
            "trigger_price": 42.5,
            "stop_loss_price": 40.0,
            "do_not_trade_if": [],
        },
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=2,
        risk_level=1,
    )

    assert "系统动作：WAIT/观察" in summary
    assert "Risk Level：1" in summary


def test_visible_execution_summary_does_not_promote_watch_only_price_condition():
    summary = _format_visible_execution_summary(
        trade_quality_check={
            "action": "等待触发",
            "execution_mode": "条件触发",
            "entry_intent": False,
            "conditional_entry_intent": False,
            "trigger_price": 42.5,
            "stop_loss_price": 40.0,
            "do_not_trade_if": [],
        },
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=2,
        risk_level=1,
    )

    assert "系统动作：WAIT/观察" in summary


def test_visible_execution_summary_requires_stop_for_conditional_entry():
    summary = _format_visible_execution_summary(
        trade_quality_check={
            "action": "等待触发",
            "execution_mode": "条件触发",
            "entry_intent": True,
            "conditional_entry_intent": True,
            "trigger_price": 42.5,
            "stop_loss_price": None,
            "do_not_trade_if": [],
        },
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=2,
        risk_level=1,
    )

    assert "系统动作：WAIT/观察" in summary


def test_visible_execution_summary_waits_when_conditional_entry_has_blocker():
    summary = _format_visible_execution_summary(
        trade_quality_check={
            "action": "等待触发",
            "execution_mode": "条件触发",
            "entry_intent": True,
            "conditional_entry_intent": True,
            "trigger_price": 42.5,
            "do_not_trade_if": ["无法解析止损价"],
        },
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=2,
        risk_level=1,
    )

    assert "系统动作：WAIT/观察" in summary


def test_visible_execution_summary_accepts_authorized_entry_range():
    summary = _format_visible_execution_summary(
        trade_quality_check={
            "action": "等待触发",
            "execution_mode": "条件触发",
            "entry_intent": True,
            "conditional_entry_intent": True,
            "entry_range": "28.0-30.0",
            "trigger_price": None,
            "stop_loss_price": 26.5,
            "do_not_trade_if": [],
        },
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=2,
        risk_level=0,
    )

    assert "系统动作：ENTER/条件入场" in summary


def test_visible_execution_summary_rejects_immediate_entry_with_future_stop():
    check = build_trade_quality_check(
        final_decision="立即买入；若买入后跌破9元则止损。触发价：10元。",
        user_context={"current_position": 0},
    )
    summary = _format_visible_execution_summary(
        trade_quality_check=check,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=2,
        risk_level=1,
    )

    assert "系统动作：WAIT/观察" in summary


def test_visible_execution_summary_prefers_holding_risk_level_when_position_exists():
    summary = _format_visible_execution_summary(
        trade_quality_check={"action": "等待人工复核"},
        gate={"passed": True},
        position_status="has_position",
        buy_level=1,
        risk_level=2,
    )

    assert "系统动作：条件减仓" in summary
    assert "强动作门禁：通过" in summary


def test_visible_execution_summary_emits_parsable_low_risk_hold():
    summary = _format_visible_execution_summary(
        trade_quality_check={"action": "可执行但需核对盘面", "do_not_trade_if": []},
        gate={"passed": True},
        position_status="has_position",
        buy_level=1,
        risk_level=0,
    )

    assert "系统动作：HOLD/持有" in summary


def test_visible_execution_summary_does_not_invert_explicit_exit_to_hold():
    summary = _format_visible_execution_summary(
        trade_quality_check={
            "action": "人工复核",
            "exit_intent": True,
            "do_not_trade_if": [],
        },
        gate={"passed": True, "failures": []},
        position_status="has_position",
        buy_level=0,
        risk_level=1,
    )

    assert "系统动作：WAIT/等待人工复核" in summary
    assert "系统动作：HOLD/持有" not in summary


def test_visible_execution_summary_does_not_reuse_pre_gate_executable_action():
    summary = _format_visible_execution_summary(
        trade_quality_check={"action": "可执行但需核对盘面"},
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="has_position",
        buy_level=0,
        risk_level=0,
    )

    assert "系统动作：WAIT/等待人工复核" in summary
    assert "可执行但需核对盘面" not in summary


def test_visible_execution_summary_preserves_stop_for_buy_only_gate_failure():
    summary = _format_visible_execution_summary(
        trade_quality_check={"action": "可执行但需核对盘面"},
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="has_position",
        buy_level=0,
        risk_level=3,
    )

    assert "系统动作：触发止损" in summary
    assert "系统动作：WAIT/等待人工复核" not in summary


def test_visible_execution_summary_preserves_exit_for_buy_only_gate_failure():
    summary = _format_visible_execution_summary(
        trade_quality_check={"action": "立即清仓"},
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="has_position",
        buy_level=0,
        risk_level=4,
    )

    assert "系统动作：立即清仓" in summary
    assert "系统动作：WAIT/等待人工复核" not in summary


def test_visible_execution_summary_level_four_ignores_only_missing_stop_price():
    summary = _format_visible_execution_summary(
        trade_quality_check={
            "action": "立即清仓",
            "do_not_trade_if": ["无法从报告中解析出明确止损价。"],
        },
        gate={"passed": True, "failures": []},
        position_status="has_position",
        buy_level=0,
        risk_level=4,
    )

    assert "系统动作：立即清仓" in summary
    assert "系统动作：WAIT/等待人工复核" not in summary


def test_visible_execution_summary_level_four_still_honors_exit_constraint():
    summary = _format_visible_execution_summary(
        trade_quality_check={
            "action": "立即清仓",
            "do_not_trade_if": [
                "无法从报告中解析出明确止损价。",
                "交易计划违反用户硬约束：禁止卖出",
            ],
        },
        gate={"passed": True, "failures": []},
        position_status="has_position",
        buy_level=0,
        risk_level=4,
    )

    assert "系统动作：WAIT/等待人工复核" in summary
    assert "系统动作：立即清仓" not in summary


def test_visible_execution_summary_level_four_keeps_user_stop_constraint():
    summary = _format_visible_execution_summary(
        trade_quality_check={
            "action": "立即清仓",
            "do_not_trade_if": [
                "交易计划违反用户硬约束："
                "无法自动验证用户硬约束：止损价必须达到8元后才允许清仓"
            ],
        },
        gate={"passed": True, "failures": []},
        position_status="has_position",
        buy_level=0,
        risk_level=4,
    )

    assert "系统动作：WAIT/等待人工复核" in summary
    assert "系统动作：立即清仓" not in summary


@pytest.mark.parametrize("risk_level", (2, 3, 4))
def test_visible_execution_summary_rechecks_user_exit_constraint(risk_level):
    summary = _format_visible_execution_summary(
        trade_quality_check={
            "action": "人工复核",
            "do_not_trade_if": [],
            "user_constraints": ["禁止卖出"],
        },
        gate={"passed": True, "failures": []},
        position_status="has_position",
        buy_level=0,
        risk_level=risk_level,
    )

    assert "系统动作：WAIT/等待人工复核" in summary
    assert "系统动作：立即清仓" not in summary
    assert "系统动作：触发止损" not in summary
    assert "系统动作：条件减仓" not in summary


def test_visible_execution_summary_level_four_ignores_entry_constraint_blocker():
    summary = _format_visible_execution_summary(
        trade_quality_check={
            "action": "立即清仓",
            "do_not_trade_if": ["交易计划违反用户硬约束：不融资"],
        },
        gate={"passed": True, "failures": []},
        position_status="has_position",
        buy_level=0,
        risk_level=4,
    )

    assert "系统动作：立即清仓" in summary
    assert "系统动作：WAIT/等待人工复核" not in summary


@pytest.mark.parametrize(
    ("risk_level", "expected_action"),
    ((2, "条件减仓"), (3, "触发止损")),
)
def test_visible_execution_summary_de_risk_ignores_entry_only_blocker(
    risk_level,
    expected_action,
):
    summary = _format_visible_execution_summary(
        trade_quality_check={
            "action": "人工复核",
            "do_not_trade_if": ["交易计划违反用户硬约束：不融资"],
        },
        gate={"passed": True, "failures": []},
        position_status="has_position",
        buy_level=0,
        risk_level=risk_level,
    )

    assert f"系统动作：{expected_action}" in summary
    assert "系统动作：WAIT/等待人工复核" not in summary


def test_visible_execution_summary_level_four_preserves_mixed_exit_constraint():
    summary = _format_visible_execution_summary(
        trade_quality_check={
            "action": "立即清仓",
            "do_not_trade_if": [
                "交易计划违反用户硬约束：不融资；禁止清仓"
            ],
        },
        gate={"passed": True, "failures": []},
        position_status="has_position",
        buy_level=0,
        risk_level=4,
    )

    assert "系统动作：WAIT/等待人工复核" in summary
    assert "系统动作：立即清仓" not in summary


@pytest.mark.parametrize("constraint", ("至少持有30天", "锁定期内不交易"))
def test_visible_execution_summary_level_four_keeps_unknown_constraint_fail_closed(
    constraint,
):
    summary = _format_visible_execution_summary(
        trade_quality_check={
            "action": "立即清仓",
            "do_not_trade_if": [
                "交易计划违反用户硬约束："
                f"无法自动验证用户硬约束：{constraint}"
            ],
        },
        gate={"passed": True, "failures": []},
        position_status="has_position",
        buy_level=0,
        risk_level=4,
    )

    assert "系统动作：WAIT/等待人工复核" in summary
    assert "系统动作：立即清仓" not in summary


def test_visible_execution_summary_downgrades_risk_action_for_mixed_gate_failure():
    summary = _format_visible_execution_summary(
        trade_quality_check={"action": "立即清仓"},
        gate={
            "passed": False,
            "failures": ["fundamental_semantic_gate", "资金流单位未校验"],
        },
        position_status="has_position",
        buy_level=0,
        risk_level=4,
    )

    assert "系统动作：WAIT/等待人工复核" in summary
    assert "系统动作：立即清仓" not in summary


def test_sanitize_filters_markdown_action_headings():
    fundamental_gate = {"passed": False, "failures": ["fundamental_semantic_gate"]}
    for text in ("### 买入", "### 建仓", "> 建议买入"):
        result, changes = sanitize_forbidden_strong_actions(
            text, fundamental_gate, "no_position", 0, 1,
        )
        assert text not in result
        assert "等待基本面证据复核" in result
        assert changes

    for text in ("### 清仓", "### 止损", "> 建议减仓"):
        result, changes = sanitize_forbidden_strong_actions(
            text, {"passed": True, "failures": []}, "no_position", 0, 1,
        )
        assert text not in result
        assert "该持仓动作不适用" in result
        assert changes


def test_sanitize_filters_qualified_action_directives():
    fundamental_gate = {"passed": False, "failures": ["fundamental_semantic_gate"]}
    for text in (
        "建议择机买入。",
        "建议适时买入。",
        "后续逢低买入。",
        "建议等待回调后买入。",
    ):
        result, changes = sanitize_forbidden_strong_actions(
            text, fundamental_gate, "no_position", 0, 1,
        )
        assert text not in result
        assert "等待基本面证据复核" in result
        assert changes

    for text in ("建议择机减仓。", "建议适时卖出。"):
        result, changes = sanitize_forbidden_strong_actions(
            text, {"passed": True, "failures": []}, "no_position", 0, 1,
        )
        assert text not in result
        assert "该持仓动作不适用" in result
        assert changes


@pytest.mark.parametrize(
    "text",
    (
        "买入成交额明显放大后建议买入。",
        "买入资金占比上升时可以买入。",
        "买入意愿增强后应当建仓。",
    ),
)
def test_entry_gate_does_not_mask_recommendations_appended_to_metrics(text):
    result, changes = sanitize_forbidden_strong_actions(
        text,
        {"passed": False, "failures": ["fundamental_semantic_gate"]},
        "no_position",
        0,
        1,
    )

    body = result.split("⚠️ [D-002]", 1)[0]
    assert text not in body
    assert "等待基本面证据复核" in body
    assert changes


@pytest.mark.parametrize(
    "text",
    (
        "我将买入。",
        "本人会建仓。",
        "我们会买入。",
        "本基金将加仓。",
        "本账户会入场。",
    ),
)
def test_sanitize_filters_subject_prefixed_entry_actions(text):
    result, changes = sanitize_forbidden_strong_actions(
        text,
        {"passed": False, "failures": ["fundamental_semantic_gate"]},
        "has_position",
        0,
        1,
    )

    body = result.split("⚠️ [D-002]", 1)[0]
    assert text not in body
    assert "等待基本面证据复核" in body
    assert changes


@pytest.mark.parametrize(
    "text",
    (
        "最终买入。",
        "最终建议：买入。",
        "明日买入。",
        "由观望转为买入。",
        "转为建仓。",
        "只增持。",
    ),
)
def test_entry_gate_catches_explicit_verdict_and_timed_buy_actions(text):
    result, changes = sanitize_forbidden_strong_actions(
        text,
        {"passed": False, "failures": ["fundamental_semantic_gate"]},
        "no_position",
        0,
        1,
    )

    body = result.split("⚠️ [D-002]", 1)[0]
    assert text.rstrip("。") not in body
    assert "等待基本面证据复核" in body
    assert changes


@pytest.mark.parametrize(
    "text",
    (
        "最终清仓。",
        "最终建议：减仓。",
        "明日卖出。",
        "由持有转为清仓。",
        "转为减仓。",
        "只止盈。",
    ),
)
def test_no_position_catches_explicit_verdict_and_timed_exit_actions(text):
    result, changes = sanitize_forbidden_strong_actions(
        text,
        {"passed": True, "failures": []},
        "no_position",
        0,
        1,
    )

    body = result.split("⚠️ [D-002]", 1)[0]
    assert text.rstrip("。") not in body
    assert "该持仓动作不适用，保持观察" in body
    assert changes


@pytest.mark.parametrize(
    "text",
    (
        "最终买入评级维持不变。",
        "明日买入信号需要确认。",
        "最终止损价为9元。",
        "减仓条件尚未触发。",
    ),
)
def test_explicit_action_prefixes_preserve_analytical_fields(text):
    result, changes = sanitize_forbidden_strong_actions(
        text,
        {"passed": False, "failures": ["fundamental_semantic_gate"]},
        "no_position",
        0,
        1,
    )

    body = result.split("⚠️ [D-002]", 1)[0]
    assert text in body
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


@pytest.mark.parametrize(
    "blocker",
    (
        "交易计划违反用户硬约束：账户冻结，当前不可交易",
        "交易计划违反用户硬约束：锁定期内禁止交易",
        "交易计划违反用户硬约束：当前券商不允许下单",
        "交易计划违反用户硬约束：卖出通道故障",
    ),
)
def test_visible_execution_summary_level_four_honors_generic_execution_blocker(blocker):
    summary = _format_visible_execution_summary(
        trade_quality_check={"action": "立即清仓", "do_not_trade_if": [blocker]},
        gate={"passed": True, "failures": []},
        position_status="has_position",
        buy_level=0,
        risk_level=4,
    )

    assert "系统动作：WAIT/等待人工复核" in summary
    assert "系统动作：立即清仓" not in summary


@pytest.mark.parametrize(
    "phrase",
    (
        "全部卖出。",
        "全数清仓。",
        "仓位全部卖出。",
        "剩余仓位清仓。",
        "持仓全部清仓。",
        "股票全部卖出。",
        "止盈离场。",
    ),
)
def test_no_position_sanitizes_quantity_prefixed_exit_phrases(phrase):
    sanitized, changes = sanitize_forbidden_strong_actions(
        phrase,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    body = sanitized.split("⚠️ [D-002]", 1)[0]
    assert phrase.rstrip("。") not in body
    assert "该持仓动作不适用，保持观察" in body
    assert changes


@pytest.mark.parametrize("phrase", ("因此我会买入。", "所以我们将建仓。"))
def test_fundamental_gate_sanitizes_subject_bearing_causal_entry(phrase):
    sanitized, changes = sanitize_forbidden_strong_actions(
        phrase,
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    body = sanitized.split("⚠️ [D-002]", 1)[0]
    assert phrase.rstrip("。") not in body
    assert "等待基本面证据复核" in body
    assert changes


@pytest.mark.parametrize("phrase", ("因此我会卖出。", "所以我们将清仓。"))
def test_no_position_sanitizes_subject_bearing_causal_exit(phrase):
    sanitized, changes = sanitize_forbidden_strong_actions(
        phrase,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    body = sanitized.split("⚠️ [D-002]", 1)[0]
    assert phrase.rstrip("。") not in body
    assert "该持仓动作不适用，保持观察" in body
    assert changes


@pytest.mark.parametrize(
    "fact",
    (
        "公司明日买入100万股用于回购。",
        "员工持股计划明日买入公司股票。",
        "高管将于明日增持公司股票。",
        "主力资金今日加仓，净流入1亿元。",
    ),
)
def test_entry_gate_preserves_dated_third_party_transactions(fact):
    sanitized, changes = sanitize_forbidden_strong_actions(
        fact,
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    assert sanitized == fact
    assert changes == []


@pytest.mark.parametrize(
    "fact",
    (
        "大股东将于明日卖出100万股。",
        "北向资金今日卖出1000万元。",
        "主力资金今日减仓，净流出1亿元。",
        "高管披露减持计划，拟明日减持10万股。",
    ),
)
def test_no_position_preserves_dated_third_party_exit_transactions(fact):
    sanitized, changes = sanitize_forbidden_strong_actions(
        fact,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    assert sanitized == fact
    assert changes == []


@pytest.mark.parametrize(
    "directive",
    (
        "建议买入。",
        "当前操作：建仓。",
        "建议轻仓买入。",
        "当前操作：条件试仓。",
    ),
)
def test_generic_gate_failure_removes_ordinary_entry_directives(directive):
    sanitized, changes = sanitize_forbidden_strong_actions(
        directive,
        gate={"passed": False, "failures": ["source_coverage=60% < 70%"]},
        position_status="no_position",
        buy_level=2,
        risk_level=0,
    )

    body = sanitized.split("⚠️ [D-002]", 1)[0]
    assert "等待门禁条件满足" in body
    assert directive.rstrip("。") not in body
    assert changes


@pytest.mark.parametrize(
    "guardrail_or_fact",
    (
        "当前不能确认建仓。",
        "历史上条件试仓策略失败。",
        "员工持股计划采用试探性轻仓策略。",
    ),
)
def test_generic_gate_failure_preserves_non_executable_entry_text(
    guardrail_or_fact,
):
    sanitized, changes = sanitize_forbidden_strong_actions(
        guardrail_or_fact,
        gate={"passed": False, "failures": ["source_coverage=60% < 70%"]},
        position_status="no_position",
        buy_level=2,
        risk_level=0,
    )

    assert sanitized == guardrail_or_fact
    assert changes == []


@pytest.mark.parametrize(
    "directive",
    (
        "当前操作：条件试仓。",
        "建议轻仓买入。",
        "重仓买入。",
    ),
)
def test_computed_buy_level_one_removes_entry_from_visible_body(directive):
    sanitized, changes = sanitize_forbidden_strong_actions(
        directive,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=1,
        risk_level=0,
    )

    body = sanitized.split("⚠️ [D-002]", 1)[0]
    assert "保持观察，等待入场条件确认" in body
    assert directive.rstrip("。") not in body
    assert changes


def test_no_position_risk_level_one_vetoes_level_two_entry_in_visible_body():
    sanitized, changes = sanitize_forbidden_strong_actions(
        "当前操作：条件试仓；建议轻仓买入。",
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=2,
        risk_level=1,
    )

    body = sanitized.split("⚠️ [D-002]", 1)[0]
    assert "条件试仓" not in body
    assert "轻仓买入" not in body
    assert "保持观察，等待入场条件确认" in body
    assert changes


@pytest.mark.parametrize(
    "directive",
    (
        "建议买入。",
        "立即买入。",
        "当前操作：确认建仓。",
    ),
)
def test_buy_level_two_rejects_direct_entry_but_keeps_trial_scope(directive):
    sanitized, changes = sanitize_forbidden_strong_actions(
        directive,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=2,
        risk_level=0,
    )

    body = sanitized.split("⚠️ [D-002]", 1)[0]
    assert directive.rstrip("。") not in body
    assert "仅限条件试仓，等待明确触发" in body
    assert changes


@pytest.mark.parametrize(
    "conditional_trial",
    (
        "当前操作：条件试仓。",
        "建议轻仓试错。",
        "若突破10元后可轻仓买入。",
    ),
)
def test_buy_level_two_preserves_authorized_conditional_trials(conditional_trial):
    sanitized, changes = sanitize_forbidden_strong_actions(
        conditional_trial,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=2,
        risk_level=0,
    )

    assert sanitized == conditional_trial
    assert changes == []


@pytest.mark.parametrize(
    "directive",
    (
        "止损离场方案应尽快执行。",
        "止损离场方案现已启动。",
        "止损离场规则今日生效。",
    ),
)
def test_no_position_replaces_full_imperative_exit_plan(directive):
    sanitized, changes = sanitize_forbidden_strong_actions(
        directive,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    body = sanitized.split("⚠️ [D-002]", 1)[0]
    assert body.strip() == "该持仓动作不适用，保持观察。"
    assert "离场方案" not in body
    assert changes


@pytest.mark.parametrize(
    "future_plan",
    (
        "### 右侧持仓：若跌破149，止损离场。",
        "#### 左侧持仓：若跌破144.25，减半仓；若跌破143，止损。",
    ),
)
def test_no_position_preserves_markdown_heading_future_holding_plan(future_plan):
    sanitized, changes = sanitize_forbidden_strong_actions(
        future_plan,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    assert sanitized == future_plan
    assert changes == []


@pytest.mark.parametrize(
    "directive, expected",
    (
        ("条件试仓或加仓。", "条件试仓。"),
        ("先条件试仓再加仓。", "先条件试仓。"),
        ("条件试仓/加仓。", "条件试仓。"),
        ("条件试仓然后重仓布局。", "条件试仓。"),
        ("若突破10元后可轻仓买入并加仓。", "若突破10元后可轻仓买入。"),
    ),
)
def test_buy_level_two_removes_chained_stronger_actions(directive, expected):
    sanitized, changes = sanitize_forbidden_strong_actions(
        directive,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=2,
        risk_level=0,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert body == expected
    assert all(term not in body for term in ("加仓", "重仓布局"))
    assert changes == ["Buy Level 2 串联强动作已移除"]


def test_no_position_preserves_completed_historical_exit_execution():
    fact = "回测中止损离场策略执行了5次。"

    sanitized, changes = sanitize_forbidden_strong_actions(
        fact,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    assert sanitized == fact
    assert changes == []


@pytest.mark.parametrize(
    "gate",
    (
        {"passed": True, "failures": []},
        {"passed": False, "failures": ["evidence_coverage=50% < 70%"]},
    ),
)
def test_no_position_does_not_mask_entry_directive_as_future_holding_plan(gate):
    text = "- 右侧持仓：建议买入后若跌破149元立即清仓。"

    sanitized, changes = sanitize_forbidden_strong_actions(
        text,
        gate=gate,
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    assert text.rstrip("。") not in sanitized
    assert "建议买入" not in sanitized
    assert changes


@pytest.mark.parametrize(
    "fact",
    (
        "历史回测显示，触发条件后立即清仓可将最大回撤降低5%。",
        "历史上立即清仓的方案胜率较低。",
        "策略回测中，全部卖出离场后回撤下降。",
        "昨日已立即清仓一半，剩余仓位继续观察。",
        "模型将“立即清仓”定义为 Risk Level 4 动作。",
    ),
)
def test_low_risk_position_preserves_historical_and_analytical_exit_facts(fact):
    sanitized, changes = sanitize_forbidden_strong_actions(
        fact,
        gate={"passed": True, "failures": []},
        position_status="has_position",
        buy_level=2,
        risk_level=2,
    )

    assert sanitized == fact
    assert changes == []


@pytest.mark.parametrize(
    "directive",
    (
        "系统建议采用“立即清仓”作为当前动作。",
        "系统指令：“立即清仓”作为最终动作。",
        "模型决定使用“立即清仓”作为当前动作。",
    ),
)
def test_low_risk_position_does_not_mask_quoted_current_exit_directives(directive):
    """Quoted wording is not a definition when it adopts a current action."""
    sanitized, changes = sanitize_forbidden_strong_actions(
        directive,
        gate={"passed": True, "failures": []},
        position_status="has_position",
        buy_level=2,
        risk_level=2,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert "立即清仓" not in body
    assert "等待触发条件，暂不执行强清仓" in body
    assert changes


@pytest.mark.parametrize(
    "directive",
    (
        "此前已完成复盘，目前建议立即清仓。",
        "昨日已经减仓，今日建议立即清仓剩余仓位。",
        "昨天已经观察，下一步立即清仓。",
    ),
)
def test_low_risk_position_does_not_mask_current_exit_after_historical_preface(directive):
    sanitized, changes = sanitize_forbidden_strong_actions(
        directive,
        gate={"passed": True, "failures": []},
        position_status="has_position",
        buy_level=2,
        risk_level=2,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert "立即清仓" not in body
    assert "等待触发条件，暂不执行强清仓" in body
    assert changes


@pytest.mark.parametrize(
    "directive",
    (
        "Final action: EXIT",
        "Final action: SELL",
        "Current action: REDUCE",
        "最终建议：EXIT",
        "当前操作：SELL",
    ),
)
def test_low_risk_position_downgrades_explicit_english_exit_labels(directive):
    sanitized, changes = sanitize_forbidden_strong_actions(
        directive,
        gate={"passed": True, "failures": []},
        position_status="has_position",
        buy_level=1,
        risk_level=1,
    )

    assert directive not in sanitized
    assert "等待触发条件，暂不执行强清仓" in sanitized
    assert changes


@pytest.mark.parametrize(
    "fact",
    (
        "历史上建议买入后持有20日。",
        "过往样本在突破后建仓。",
        "回测采用轻仓试错并持有。",
        "策略回测：确认建仓后持有5天。",
    ),
)
def test_failed_gate_preserves_historical_entry_narratives(fact):
    sanitized, changes = sanitize_forbidden_strong_actions(
        fact,
        gate={"passed": False, "failures": ["source_coverage=60% < 70%"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    assert sanitized == fact
    assert changes == []


@pytest.mark.parametrize(
    "future_plan",
    (
        "建仓成功后若跌破149则减仓。",
        "右侧持仓：未来建仓后若跌破149清仓。",
    ),
)
def test_failed_gate_preserves_hypothetical_future_holding_plan(future_plan):
    sanitized, changes = sanitize_forbidden_strong_actions(
        future_plan,
        gate={"passed": False, "failures": ["source_coverage=60% < 70%"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    assert sanitized == future_plan
    assert changes == []


@pytest.mark.parametrize(
    "directive",
    (
        "强买。",
        "强烈买入。",
        "满仓买入。",
        "重仓布局。",
        "强力买入。",
    ),
)
def test_low_buy_level_removes_strong_entry_even_when_gate_passes(directive):
    sanitized, changes = sanitize_forbidden_strong_actions(
        directive,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=1,
        risk_level=0,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert body != directive
    assert sum(
        body.count(replacement)
        for replacement in (
            "保持观察，等待入场条件确认",
            "暂不执行强买入，等待条件确认",
        )
    ) == 1
    assert changes


@pytest.mark.parametrize(
    "directive",
    (
        "建议重仓条件试仓。",
        "满仓条件试仓。",
        "立即条件试仓。",
        "积极条件试仓。",
    ),
)
def test_buy_level_two_rejects_strongly_modified_trial(directive):
    sanitized, changes = sanitize_forbidden_strong_actions(
        directive,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=2,
        risk_level=0,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert directive.rstrip("。") not in body
    assert "暂不执行强买入，等待条件确认" in body
    assert changes


@pytest.mark.parametrize(
    "directive",
    (
        "历史回测显示策略有效当前建议买入。",
        "回测结果优秀因此最终建议买入。",
        "过往样本表现较好所以现在买入。",
    ),
)
def test_failed_gate_does_not_mask_current_entry_after_backtest_preface(directive):
    sanitized, changes = sanitize_forbidden_strong_actions(
        directive,
        gate={"passed": False, "failures": ["evidence_coverage=60% < 70%"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert directive.rstrip("。") not in body
    assert "等待门禁条件满足" in body
    assert changes


def test_failed_gate_preserves_multiline_future_position_risk_plans():
    text = (
        "风险预案如下：\n"
        "- 右侧持仓：若跌破149，立即清仓。\n"
        "- 左侧持仓：若跌破144.25，清仓离场。"
    )

    sanitized, changes = sanitize_forbidden_strong_actions(
        text,
        gate={"passed": False, "failures": ["source_coverage=60% < 70%"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    assert sanitized == text
    assert changes == []


def test_failed_gate_downgrades_held_position_future_strong_exit():
    sanitized, changes = sanitize_forbidden_strong_actions(
        "右侧持仓：若跌破149，立即清仓。",
        gate={"passed": False, "failures": ["evidence_coverage=60% < 70%"]},
        position_status="has_position",
        buy_level=2,
        risk_level=2,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert "立即清仓" not in body
    assert "等待触发条件，暂不执行强清仓" in body
    assert changes


@pytest.mark.parametrize(
    "directive",
    (
        "条件试仓，随后逐步加仓。",
        "条件试仓；最终加仓。",
        "若突破10元后可轻仓买入，然后确认建仓。",
    ),
)
def test_buy_level_two_removes_punctuated_stronger_action_chain(directive):
    sanitized, changes = sanitize_forbidden_strong_actions(
        directive,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=2,
        risk_level=0,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert "加仓" not in body
    assert "确认建仓" not in body
    assert "条件试仓" in body or "轻仓买入" in body
    assert changes == ["Buy Level 2 串联强动作已移除"]


@pytest.mark.parametrize(
    "text",
    (
        "并非建议离场。",
        "历史回测中建议离场策略收益较低。",
        "模型推荐离场信号尚未出现。",
        "报告讨论是否应该离场。",
    ),
)
def test_no_position_preserves_negated_and_analytical_exit_text(text):
    sanitized, changes = sanitize_forbidden_strong_actions(
        text,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    assert sanitized == text
    assert changes == []


def test_buy_level_two_replacement_is_not_recursively_rewritten():
    sanitized, changes = sanitize_forbidden_strong_actions(
        "当前操作：确认建仓。",
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=2,
        risk_level=0,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert body == "当前操作：仅限条件试仓，等待明确触发。"
    assert "仅限仅限" not in body
    assert all("__TA_" not in change for change in changes)
    assert changes


@pytest.mark.parametrize(
    "directive",
    (
        "历史回测表现优秀建议买入。",
        "历史上策略有效推荐建仓。",
        "过往样本表现较好可以入场。",
    ),
)
def test_failed_gate_does_not_hide_unpunctuated_current_recommendation(directive):
    sanitized, changes = sanitize_forbidden_strong_actions(
        directive,
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert directive.rstrip("。") not in body
    assert "等待基本面证据复核" in body
    assert changes


@pytest.mark.parametrize(
    "directive",
    (
        "风险分析：止损离场策略，建议立即执行。",
        "分析止损离场方案，并建议立即执行。",
        "评估止损离场策略；结论是立即执行。",
    ),
)
def test_no_position_downgrades_execution_after_analytical_exit_plan(directive):
    sanitized, changes = sanitize_forbidden_strong_actions(
        directive,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert "立即执行" not in body
    assert "该持仓动作不适用，保持观察" in body
    assert changes


@pytest.mark.parametrize(
    "analysis_text",
    (
        "条件试仓；随后买入评级上调。",
        "条件试仓，并且买入信号增强。",
        "条件试仓，随后建仓成本下降。",
    ),
)
def test_buy_level_two_preserves_analytical_nouns_after_trial(analysis_text):
    sanitized, changes = sanitize_forbidden_strong_actions(
        analysis_text,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=2,
        risk_level=0,
    )

    assert sanitized == analysis_text
    assert changes == []


@pytest.mark.parametrize(
    "analysis_text",
    (
        "分析立即条件试仓的风险。",
        "“重仓试仓”属于不合理术语。",
        "评估直接试仓策略的风险。",
        "立即轻仓试错的历史胜率为20%。",
    ),
)
def test_strong_trial_sanitizer_preserves_analytical_terms(analysis_text):
    sanitized, changes = sanitize_forbidden_strong_actions(
        analysis_text,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=2,
        risk_level=0,
    )

    assert sanitized == analysis_text
    assert changes == []


@pytest.mark.parametrize(
    "directive",
    (
        "历史回测建议买入后持有最终建议买入。",
        "历史回测建议买入并持有明日买入。",
        "过往样本建议建仓后持有目前可以建仓。",
    ),
)
def test_historical_sequence_does_not_mask_following_current_entry(directive):
    sanitized, changes = sanitize_forbidden_strong_actions(
        directive,
        gate={"passed": False, "failures": ["source_coverage=60% < 70%"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert body != directive
    assert "等待门禁条件满足" in body
    assert changes


@pytest.mark.parametrize(
    "position_status, buy_level, expected",
    (
        ("no_position", 1, "保持观察，等待入场条件确认"),
        ("has_position", 2, "保持原仓位，等待加仓条件确认"),
    ),
)
def test_buy_level_authority_blocks_standalone_replenishment(
    position_status, buy_level, expected
):
    sanitized, changes = sanitize_forbidden_strong_actions(
        "补仓。",
        gate={"passed": True, "failures": []},
        position_status=position_status,
        buy_level=buy_level,
        risk_level=0,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert body == f"{expected}。"
    assert changes


@pytest.mark.parametrize(
    "directive",
    (
        "当前操作：“重仓试仓”。",
        "最终建议：“立即条件试仓”。",
        '交易计划："满仓试仓"。',
        "建议执行“重仓试仓”。",
    ),
)
def test_quoted_strong_trial_is_not_masked_in_directive_context(directive):
    sanitized, changes = sanitize_forbidden_strong_actions(
        directive,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=1,
        risk_level=0,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert body != directive
    assert (
        "暂不执行强买入，等待条件确认" in body
        or "保持观察，等待入场条件确认" in body
    )
    assert changes


@pytest.mark.parametrize("directive", ("离场。", "退出。", "出局。", "降险。"))
def test_no_position_blocks_standalone_exit_synonyms(directive):
    sanitized, changes = sanitize_forbidden_strong_actions(
        directive,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert body == "该持仓动作不适用，保持观察。"
    assert changes


def test_entry_gate_preserves_negated_replenishment_guardrail():
    text = "不建议补仓。"
    sanitized, changes = sanitize_forbidden_strong_actions(
        text,
        gate={"passed": False, "failures": ["source_coverage=60% < 70%"]},
        position_status="has_position",
        buy_level=2,
        risk_level=0,
    )

    assert sanitized == text
    assert changes == []


@pytest.mark.parametrize(
    "directive",
    (
        "回测结果：立即买入。",
        "历史回测结论为强烈买入。",
        "策略回测建议重仓建仓。",
    ),
)
def test_historical_preface_does_not_protect_explicit_strong_entry(directive):
    sanitized, changes = sanitize_forbidden_strong_actions(
        directive,
        gate={"passed": False, "failures": ["source_coverage=60% < 70%"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert all(term not in body for term in ("立即买入", "强烈买入", "重仓建仓"))
    assert changes


@pytest.mark.parametrize(
    "analysis_text",
    (
        "加仓是否合适仍需评估。",
        "加仓的可行性仍需研究。",
        "加仓策略需要回测。",
    ),
)
def test_level_two_trial_preserves_following_entry_analysis(analysis_text):
    text = f"条件试仓。{analysis_text}"
    sanitized, changes = sanitize_forbidden_strong_actions(
        text,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=2,
        risk_level=0,
    )

    assert sanitized == text
    assert changes == []


def test_failed_gate_sanitizes_direct_entry_before_future_stop_plan():
    text = "立即建仓后若跌破149则止损。"
    sanitized, changes = sanitize_forbidden_strong_actions(
        text,
        gate={"passed": False, "failures": ["fundamental_semantic_gate"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert "立即建仓" not in body
    assert "等待基本面证据复核" in body
    assert "若跌破149则止损" in body
    assert changes


@pytest.mark.parametrize(
    "directive",
    (
        "历史回测表现优秀现在立即买入后持有。",
        "过往样本很好下一步立即买入后持有。",
        "历史上策略有效最终建议立即买入后持有。",
    ),
)
def test_historical_sequence_rejects_embedded_current_markers(directive):
    sanitized, changes = sanitize_forbidden_strong_actions(
        directive,
        gate={"passed": False, "failures": ["source_coverage=60% < 70%"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert "立即买入" not in body
    assert changes


@pytest.mark.parametrize(
    "directive",
    (
        "条件试仓后逐步加仓。",
        "条件试仓之后分批加仓。",
        "轻仓试错以后重仓加仓。",
    ),
)
def test_level_two_blocks_after_delimited_stronger_actions(directive):
    sanitized, changes = sanitize_forbidden_strong_actions(
        directive,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=2,
        risk_level=0,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert all(term not in body for term in ("逐步加仓", "分批加仓", "重仓加仓"))
    assert changes


def test_no_position_preserves_observed_threshold_future_holding_plan():
    text = "右侧持仓：若观察到价格跌破149，清仓。"
    sanitized, changes = sanitize_forbidden_strong_actions(
        text,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    assert sanitized == text
    assert changes == []


@pytest.mark.parametrize(
    "directive",
    (
        "结论如下：“立即条件试仓”。",
        "最终结果是“立即条件试仓”。",
        "交易计划如下：“重仓试仓”。",
    ),
)
def test_quoted_strong_trial_after_extended_action_label_is_sanitized(directive):
    sanitized, changes = sanitize_forbidden_strong_actions(
        directive,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=1,
        risk_level=0,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert "立即条件试仓" not in body
    assert "重仓试仓" not in body
    assert changes


def test_unknown_position_elevated_risk_vetoes_level_two_trial_everywhere():
    score = {
        "data_completeness": 90,
        "confidence": ConfidenceLevel.HIGH.value,
        "summary": "数据完整，置信度高。",
        "blockers": [],
    }
    block = format_readiness_score(
        score,
        position_status="unknown",
        buy_level=2,
        risk_level=3,
    )
    sanitized, changes = sanitize_forbidden_strong_actions(
        "若站稳10元后，轻仓买入。",
        gate={"passed": True, "failures": []},
        position_status="unknown",
        buy_level=2,
        risk_level=3,
    )

    allowed_line = next(
        line for line in block.splitlines() if line.startswith("- 允许动作：")
    )
    forbidden_line = next(
        line for line in block.splitlines() if line.startswith("- 禁止动作：")
    )
    assert "条件试仓" not in allowed_line
    assert "条件试仓" in forbidden_line
    assert "轻仓买入" not in sanitized.split("\n\n⚠️", 1)[0]
    assert changes


@pytest.mark.parametrize(
    "directive",
    (
        "满仓执行条件试仓。",
        "重仓进行条件试仓。",
        "立即开展轻仓试错。",
    ),
)
def test_level_two_rejects_strong_modifier_before_trial_directive(directive):
    sanitized, changes = sanitize_forbidden_strong_actions(
        directive,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=2,
        risk_level=0,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert directive.rstrip("。") not in body
    assert "暂不执行强买入，等待条件确认" in body
    assert changes


@pytest.mark.parametrize(
    "directive",
    (
        "建议以满仓方式执行条件试仓。",
        "建议用重仓方式进行轻仓试错。",
        "计划投入全部仓位做条件试仓。",
        "建议按50%仓位条件试仓。",
        "建议半仓条件试仓。",
        "建议大仓位分批执行条件试仓。",
    ),
)
def test_level_two_rejects_non_adjacent_or_explicit_sizing(directive):
    sanitized, changes = sanitize_forbidden_strong_actions(
        directive,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=2,
        risk_level=0,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert directive.rstrip("。") not in body
    assert "暂不执行强买入，等待条件确认" in body
    assert changes


@pytest.mark.parametrize(
    "directive",
    (
        "满仓，执行条件试仓。",
        "建议满仓，条件试仓。",
        "投入50%仓位，条件试仓。",
        "当前操作：满仓；随后条件试仓。",
        "用全部仓位，执行轻仓试错。",
    ),
)
def test_level_two_rejects_punctuated_full_size_trial_directives(directive):
    sanitized, changes = sanitize_forbidden_strong_actions(
        directive,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=2,
        risk_level=0,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert body != directive
    assert "暂不执行强买入，等待条件确认" in body
    assert changes


@pytest.mark.parametrize(
    "directive",
    (
        "若买入后继续持有，但当前建议立即清仓。",
        "如果买入后计划观察，当前建议立即清仓。",
        "如果建仓后可持有，否则当前建议立即清仓。",
    ),
)
def test_no_position_keeps_current_exit_outside_future_position_mask(directive):
    sanitized, changes = sanitize_forbidden_strong_actions(
        directive,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert "立即清仓" not in body
    assert "该持仓动作不适用，保持观察" in body
    assert changes


@pytest.mark.parametrize(
    "directive",
    (
        "当前操作：建立仓位。",
        "建议买进。",
        "下一步：建立底仓。",
        "当前操作：分批布局。",
        "建议布局10%仓位。",
        "当前操作：拿先手。",
    ),
)
def test_generic_failed_gate_sanitizes_standard_entry_synonyms(directive):
    sanitized, changes = sanitize_forbidden_strong_actions(
        directive,
        gate={"passed": False, "failures": ["source_coverage=60% < 70%"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert directive.rstrip("。") not in body
    assert "等待门禁条件满足" in body
    assert changes


@pytest.mark.parametrize(
    "directive",
    (
        "建议配置仓位。",
        "建议申购该股。",
        "建议增配该股。",
        "建议加码该股。",
        "建议上车。",
        "建议入市。",
    ),
)
def test_generic_failed_gate_sanitizes_unlabeled_entry_synonyms(directive):
    sanitized, changes = sanitize_forbidden_strong_actions(
        directive,
        gate={"passed": False, "failures": ["source_coverage=60% < 70%"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert directive.rstrip("。") not in body
    assert "等待门禁条件满足" in body
    assert changes


@pytest.mark.parametrize(
    "fact",
    (
        "历史回测显示增配该股后持有5天平均收益8%。",
        "机构今日已增配该股。",
        "报告讨论是否应该配置仓位。",
    ),
)
def test_failed_gate_preserves_non_executable_entry_synonym_facts(fact):
    sanitized, changes = sanitize_forbidden_strong_actions(
        fact,
        gate={"passed": False, "failures": ["source_coverage=60% < 70%"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    assert sanitized == fact
    assert changes == []


@pytest.mark.parametrize("risk_level", (0, 1, 2, 3))
def test_passing_gate_does_not_authorize_immediate_exit_below_level_four(
    risk_level,
):
    sanitized, changes = sanitize_forbidden_strong_actions(
        "最终建议：立即清仓。",
        gate={"passed": True, "failures": []},
        position_status="has_position",
        buy_level=4,
        risk_level=risk_level,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert "立即清仓" not in body
    assert "等待触发条件，暂不执行强清仓" in body
    assert changes


@pytest.mark.parametrize(
    "directive",
    (
        "马上清仓。",
        "建议立即全部清仓。",
        "建议清仓。",
        "立即全部卖出。",
        "现在全部卖出。",
        "务必清仓。",
        "今日清仓。",
        "下一步清仓。",
        "全部清仓。",
        "果断清仓。",
    ),
)
def test_passing_gate_blocks_common_immediate_exits_below_level_four(directive):
    sanitized, changes = sanitize_forbidden_strong_actions(
        directive,
        gate={"passed": True, "failures": []},
        position_status="has_position",
        buy_level=2,
        risk_level=3,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert directive.rstrip("。") not in body
    assert "等待触发条件，暂不执行强清仓" in body
    assert changes


@pytest.mark.parametrize(
    "guardrail",
    (
        "不建议立即清仓。",
        "当前不应立即清仓。",
        "禁止立即清仓。",
        "不能全部卖出离场。",
    ),
)
def test_low_risk_level_preserves_negated_immediate_exit_guardrails(guardrail):
    sanitized, changes = sanitize_forbidden_strong_actions(
        guardrail,
        gate={"passed": True, "failures": []},
        position_status="has_position",
        buy_level=3,
        risk_level=3,
    )

    assert sanitized == guardrail
    assert changes == []


@pytest.mark.parametrize(
    "directive",
    (
        "建议采用“重仓试仓”。",
        "建议使用“满仓试仓”。",
        "我们将采用“重仓试仓”。",
    ),
)
def test_low_buy_level_blocks_quoted_trial_after_adoption_verbs(directive):
    sanitized, changes = sanitize_forbidden_strong_actions(
        directive,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=1,
        risk_level=0,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert "重仓试仓" not in body
    assert "满仓试仓" not in body
    assert changes


def test_buy_level_one_blocks_colloquial_direct_buy():
    sanitized, changes = sanitize_forbidden_strong_actions(
        "现在就买。",
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=1,
        risk_level=0,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert "现在就买" not in body
    assert "保持观察，等待入场条件确认" in body
    assert changes


@pytest.mark.parametrize(
    "directive",
    (
        "当前操作：试仓。",
        "若站稳10元则配置仓位。",
        "最终建议：试探性建仓。",
    ),
)
def test_low_buy_level_blocks_direct_trial_and_entry_synonyms(directive):
    sanitized, changes = sanitize_forbidden_strong_actions(
        directive,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=1,
        risk_level=0,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert "保持观察，等待入场条件确认" in body
    assert changes


@pytest.mark.parametrize(
    "directive",
    (
        "建议八成仓位执行条件试仓。",
        "建议８０％仓位执行条件试仓。",
        "条件试仓，投入八成仓位。",
    ),
)
def test_buy_level_two_rejects_non_ascii_or_chinese_large_trial_sizes(directive):
    sanitized, changes = sanitize_forbidden_strong_actions(
        directive,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=2,
        risk_level=0,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert "暂不执行强买入，等待条件确认" in body
    assert changes


@pytest.mark.parametrize(
    "statement",
    (
        "此前已完成复盘，建议立即清仓。",
        "此前已清仓，目前建议立即清仓。",
    ),
)
def test_low_risk_position_does_not_mask_current_exit_after_historical_preamble(statement):
    sanitized, changes = sanitize_forbidden_strong_actions(
        statement,
        gate={"passed": True, "failures": []},
        position_status="has_position",
        buy_level=4,
        risk_level=3,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert "立即清仓" not in body
    assert "等待触发条件，暂不执行强清仓" in body
    assert changes


@pytest.mark.parametrize(
    "directive",
    (
        "建议布局该股。",
        "当前操作：布局该股。",
        "下一步布局该股。",
    ),
)
def test_blocked_entry_sanitizes_unsized_layout_directives(directive):
    """布局 is executable entry wording even when no position size is stated."""
    sanitized, changes = sanitize_forbidden_strong_actions(
        directive,
        gate={"passed": False, "failures": ["source_coverage=60% < 70%"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert "布局该股" not in body
    assert "等待门禁条件满足" in body
    assert changes


@pytest.mark.parametrize(
    "directive",
    (
        "建议立即平仓。",
        "下一步平仓。",
        "当前建议务必割肉。",
        "最终建议：全部抛售。",
    ),
)
def test_low_risk_position_sanitizes_immediate_exit_synonyms(directive):
    """Only Risk Level 4 may expose an immediate flat-position command."""
    sanitized, changes = sanitize_forbidden_strong_actions(
        directive,
        gate={"passed": True, "failures": []},
        position_status="has_position",
        buy_level=2,
        risk_level=2,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert "等待触发条件，暂不执行强清仓" in body
    assert changes


@pytest.mark.parametrize(
    "directive",
    (
        "当前动作：清仓。",
        "最终建议：清仓。",
        "当前动作：止损离场。",
        "最终操作：卖出。",
    ),
)
def test_low_risk_position_sanitizes_chinese_labeled_exit_directives(directive):
    """Chinese action labels must not bypass Risk Level 4 authority."""
    sanitized, changes = sanitize_forbidden_strong_actions(
        directive,
        gate={"passed": True, "failures": []},
        position_status="has_position",
        buy_level=2,
        risk_level=2,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert "等待触发条件，暂不执行强清仓" in body
    assert changes


@pytest.mark.parametrize(
    "directive",
    (
        "当前操作：条件试仓，仓位50%。",
        "建议条件试仓50%仓位。",
        "条件试仓，投入半仓。",
        "条件试仓，投入30%的仓位。",
        "条件试仓，随后满仓。",
    ),
)
def test_buy_level_two_rejects_trailing_oversized_trial_allocations(directive):
    """A Level 2 trial cannot carry a trailing full-size allocation."""
    sanitized, changes = sanitize_forbidden_strong_actions(
        directive,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=2,
        risk_level=0,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert "暂不执行强买入，等待条件确认" in body
    assert all(term not in body for term in ("50%仓位", "半仓", "30%的仓位", "满仓"))
    assert changes


def test_no_position_preserves_conditional_modal_future_exit_plan():
    text = "若买入后建议止损离场。"
    sanitized, changes = sanitize_forbidden_strong_actions(
        text,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    assert sanitized == text
    assert changes == []


def test_no_position_preserves_simultaneous_conditional_exit_plan():
    text = "右侧持仓：若跌破149则同时减仓并清仓。"
    sanitized, changes = sanitize_forbidden_strong_actions(
        text,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    assert sanitized == text
    assert changes == []


def test_risk_level_four_preserves_immediate_exit_when_gate_passes():
    text = "最终建议：立即清仓。"
    sanitized, changes = sanitize_forbidden_strong_actions(
        text,
        gate={"passed": True, "failures": []},
        position_status="has_position",
        buy_level=4,
        risk_level=4,
    )

    assert sanitized == text
    assert changes == []


def test_risk_level_three_preserves_conditional_stop_not_immediate_clear():
    text = "若跌破10元，建议止损离场。现在就清仓。"
    sanitized, changes = sanitize_forbidden_strong_actions(
        text,
        gate={"passed": True, "failures": []},
        position_status="has_position",
        buy_level=3,
        risk_level=3,
    )

    assert "若跌破10元，建议止损离场" in sanitized
    assert "现在就清仓" not in sanitized
    assert "等待触发条件，暂不执行强清仓" in sanitized
    assert changes


@pytest.mark.parametrize(
    "directive",
    (
        "条件试仓：买入50%仓位。",
        "条件试仓：直接买入。",
    ),
)
def test_buy_level_two_removes_colon_delimited_follow_on_buys(directive):
    sanitized, changes = sanitize_forbidden_strong_actions(
        directive,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=2,
        risk_level=0,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert body == "条件试仓。"
    assert all(term not in body for term in ("买入", "50%仓位", "直接"))
    assert changes


@pytest.mark.parametrize(
    "directive",
    (
        "全仓建仓。",
        "当前动作：止损。",
        "立即减仓。",
    ),
)
def test_low_execution_levels_remove_full_entry_and_unauthorized_exit(directive):
    sanitized, changes = sanitize_forbidden_strong_actions(
        directive,
        gate={"passed": True, "failures": []},
        position_status="no_position" if directive == "全仓建仓。" else "has_position",
        buy_level=1 if directive == "全仓建仓。" else 2,
        risk_level=0,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert directive.rstrip("。").replace("：", "") not in body.replace("：", "")
    assert changes


@pytest.mark.parametrize(
    "directive",
    (
        "目标仓位50%，执行条件试仓。",
        "满仓作为目标，执行条件试仓。",
        "条件试仓，最终增至满仓。",
    ),
)
def test_buy_level_two_rejects_sentence_level_target_sizing(directive):
    sanitized, changes = sanitize_forbidden_strong_actions(
        directive,
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=2,
        risk_level=0,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert "暂不执行强买入，等待条件确认" in body
    assert all(term not in body for term in ("50%", "满仓", "目标仓位", "最终增至"))
    assert changes


def test_low_risk_position_does_not_mask_current_exit_after_long_backtest():
    text = (
        "历史回测显示该策略在过去十年不同市场和多轮牛熊周期以及极端行情中的"
        "风险控制效果稳定，当前建议立即清仓，策略收益改善。"
    )
    sanitized, changes = sanitize_forbidden_strong_actions(
        text,
        gate={"passed": True, "failures": []},
        position_status="has_position",
        buy_level=4,
        risk_level=3,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert "当前建议立即清仓" not in body
    assert "等待触发条件，暂不执行强清仓" in body
    assert changes


def test_risk_level_entry_veto_is_not_rendered_as_gate_failure():
    score = generate_readiness_score(100, ConfidenceLevel.HIGH)
    block = format_readiness_score(
        score,
        position_status="no_position",
        buy_level=2,
        risk_level=1,
    )

    assert "风险等级限制开仓" in block
    assert "入场门禁未通过" not in block


@pytest.mark.parametrize(
    "failure",
    (
        "fundamental_semantic_gate",
        "event_risk_block_open",
        "估值基准价不可用(valuation_price_unavailable)",
    ),
)
def test_failed_entry_gate_does_not_hide_buy_as_future_holding_plan(failure):
    sanitized, changes = sanitize_forbidden_strong_actions(
        "买入后建议卖出。",
        gate={"passed": False, "failures": [failure]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert not body.startswith("买入后")
    assert changes


@pytest.mark.parametrize(
    "directive",
    (
        "下一步买入。",
        "下一步建立仓位。",
        "立即介入。",
        "将配置仓位。",
        "增配该股。",
    ),
)
def test_generic_failed_gate_sanitizes_bare_current_entry_directives(directive):
    sanitized, changes = sanitize_forbidden_strong_actions(
        directive,
        gate={"passed": False, "failures": ["source_coverage=60% < 70%"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert directive.rstrip("。") not in body
    assert "等待门禁条件满足" in body
    assert changes


@pytest.mark.parametrize(
    "fact",
    (
        "分析立即买入的风险。",
        "“立即买入”只是术语。",
        "评估重仓建仓策略。",
    ),
)
def test_level_authority_preserves_analytical_strong_buy_terms(fact):
    sanitized, changes = sanitize_forbidden_strong_actions(
        fact,
        gate={"passed": True, "failures": []},
        position_status="has_position",
        buy_level=2,
        risk_level=2,
    )

    assert sanitized == fact
    assert changes == []


@pytest.mark.parametrize(
    "directive",
    (
        "最终建议：满仓加仓。",
        "最终建议：强烈建仓。",
        "最终建议：确认买入。",
        "当前操作：建议配置仓位。",
        "下一步：建立头寸。",
    ),
)
def test_blocked_entry_rejects_modifier_prefixed_directives(directive):
    sanitized, changes = sanitize_forbidden_strong_actions(
        directive,
        gate={"passed": False, "failures": ["source_coverage=60% < 70%"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert directive.rstrip("。") not in body
    assert "等待门禁条件满足" in body or "暂不执行强买入" in body
    assert changes


@pytest.mark.parametrize(
    "fact",
    (
        "策略回测：积极建仓后持有5天，平均收益8%。",
        "历史回测：满仓买入后持有5天，平均收益8%。",
        "回测中强烈建仓后持有5天的胜率为70%。",
    ),
)
def test_failed_gate_preserves_aggressive_entry_terms_in_backtest_facts(fact):
    sanitized, changes = sanitize_forbidden_strong_actions(
        fact,
        gate={"passed": False, "failures": ["source_coverage=60% < 70%"]},
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    assert sanitized == fact
    assert changes == []


@pytest.mark.parametrize(
    "gate",
    (
        {"passed": True, "failures": []},
        {"passed": False, "failures": ["evidence_coverage=60% < 70%"]},
    ),
)
def test_current_buy_before_future_exit_plan_cannot_bypass_entry_gate(gate):
    """A current buy before a hypothetical stop remains subject to Buy Level."""
    sanitized, changes = sanitize_forbidden_strong_actions(
        "- 右侧持仓：现在买入后若跌破149则清仓。",
        gate=gate,
        position_status="no_position",
        buy_level=0,
        risk_level=1,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert "现在买入" not in body
    assert "保持观察，等待入场条件确认" in body or "等待门禁条件满足" in body
    assert "确认后若" not in body
    assert "；后续持仓若跌破149则清仓" in body
    assert changes


def test_low_buy_level_sanitizes_immediate_entry_synonym():
    sanitized, changes = sanitize_forbidden_strong_actions(
        "立即进场。",
        gate={"passed": True, "failures": []},
        position_status="no_position",
        buy_level=1,
        risk_level=0,
    )

    body = sanitized.split("\n\n⚠️", 1)[0]
    assert "立即进场" not in body
    assert "保持观察，等待入场条件确认" in body
    assert changes


@pytest.mark.parametrize(
    "directive",
    (
        "若跌破支撑，建议减仓。",
        "若主力持续流出，则条件减仓。",
    ),
)
def test_risk_level_two_preserves_authorized_conditional_reductions(directive):
    sanitized, changes = sanitize_forbidden_strong_actions(
        directive,
        gate={"passed": True, "failures": []},
        position_status="has_position",
        buy_level=2,
        risk_level=2,
    )

    assert sanitized == directive
    assert changes == []
