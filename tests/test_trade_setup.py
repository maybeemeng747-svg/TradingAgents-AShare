import asyncio
from types import SimpleNamespace

import pytest

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


def test_trade_quality_check_ignores_ma_period_and_extracts_trigger_stop_levels():
    check = build_trade_quality_check(
        investment_plan="短线先观察，若重新站稳5日线再看承接。",
        trader_plan="触发位：57.48 / 60.80；止损位：56.00 / 52.40；当前不追高。",
        final_decision="未持仓者观望，只有放量重新站稳60.80后才重新评估机会。",
    )

    assert check["trigger_price"] == 57.48
    assert check["stop_loss_price"] == 56.0
    assert "无法从报告中解析出明确止损价。" not in check["do_not_trade_if"]


@pytest.mark.parametrize("indicator", ("20日均线", "5周均线", "10月均线"))
def test_trade_quality_check_does_not_use_indicator_period_as_trigger(indicator):
    check = build_trade_quality_check(
        final_decision=f"若站稳{indicator}则买入。止损价：9元。",
        user_context={"current_position": 0},
    )

    assert check["entry_intent"] is True
    assert check["conditional_entry_intent"] is True
    assert check["trigger_price"] is None


def test_trade_quality_check_does_not_treat_target_as_entry_trigger():
    check = build_trade_quality_check(
        investment_plan="当前继续观望。",
        final_decision="最终建议：继续观望；目标价：42.00元。",
        user_context={"current_position": 0},
    )

    assert check["execution_mode"] == "人工复核"
    assert check["trigger_price"] is None
    assert check["entry_intent"] is False


def test_trade_quality_check_separates_watch_condition_from_entry_intent():
    check = build_trade_quality_check(
        final_decision="继续观望，若股价站稳10元则继续观察。止损价：9元。",
        user_context={"current_position": 0},
    )

    assert check["trigger_price"] == 10.0
    assert check["stop_loss_price"] == 9.0
    assert check["entry_intent"] is False


def test_trade_quality_check_recognizes_explicit_conditional_entry_intent():
    check = build_trade_quality_check(
        final_decision="若股价站稳10元，可轻仓买入。止损价：9元。",
        user_context={"current_position": 0},
    )

    assert check["entry_intent"] is True
    assert check["conditional_entry_intent"] is True


def test_future_stop_does_not_make_immediate_buy_a_conditional_entry():
    check = build_trade_quality_check(
        final_decision="立即买入；若买入后跌破9元则止损。触发价：10元。",
        user_context={"current_position": 0},
    )

    assert check["entry_intent"] is True
    assert check["conditional_entry_intent"] is False
    assert check["execution_mode"] == "立即执行"


def test_trade_quality_check_final_entry_veto_overrides_upstream_buy_plan():
    check = build_trade_quality_check(
        investment_plan="若股价站稳10元，可轻仓买入。",
        trader_plan="触发价：10元；止损价：9元。",
        final_decision="买入条件尚不成立，继续观望。",
        user_context={"current_position": 0},
    )

    assert check["entry_intent"] is False


@pytest.mark.parametrize(
    "verdict",
    (
        "最终建议：SELL。",
        "最终建议：清仓。",
        "最终裁决：看空。",
        "方向：偏空。",
    ),
)
def test_trade_quality_check_final_bearish_verdict_blocks_upstream_entry(verdict):
    check = build_trade_quality_check(
        trader_plan="若站稳10元，可轻仓买入。触发价：10元；止损价：9元。",
        final_decision=verdict,
        user_context={"current_position": 0},
    )

    assert check["entry_intent"] is False


def test_trade_quality_check_direct_no_buy_and_wait_verdicts_override_entry():
    verdicts = (
        "若股价站稳10元，最终建议：不买入。止损价：9元。",
        "若股价站稳10元，最终建议：不要买入。止损价：9元。",
        "若股价站稳10元，可以买入；最终裁决：HOLD。止损价：9元。",
        "若股价站稳10元，建议买入；最终结论：等待。止损价：9元。",
        "若股价站稳10元，建议买入；最终建议：观望。止损价：9元。",
    )
    for verdict in verdicts:
        check = build_trade_quality_check(
            final_decision=verdict,
            user_context={"current_position": 0},
        )
        assert check["entry_intent"] is False


@pytest.mark.parametrize(
    "label",
    ("交易建议", "操作建议", "交易员动作", "决策", "动作", "当前操作"),
)
def test_all_explicit_hold_labels_override_earlier_conditional_entry(label):
    check = build_trade_quality_check(
        final_decision=(
            "若股价站稳10元则买入。触发价：10元；止损价：9元。"
            f"{label}：HOLD。"
        ),
        user_context={"current_position": 0},
    )

    assert check["entry_intent"] is False
    assert check["conditional_entry_intent"] is False


def test_trade_quality_check_uses_last_directive_within_same_clause():
    blocked = build_trade_quality_check(
        final_decision="若股价站稳10元可轻仓买入但最终建议HOLD，止损价：9元。",
        user_context={"current_position": 0},
    )
    allowed = build_trade_quality_check(
        final_decision="最终建议HOLD但若股价站稳10元可轻仓买入，止损价：9元。",
        user_context={"current_position": 0},
    )

    assert blocked["entry_intent"] is False
    assert allowed["entry_intent"] is True


@pytest.mark.parametrize(
    "directive",
    (
        "若站稳10元则买入。触发价：10元；止损价：9元。",
        "站稳10元后买入。触发价：10元；止损价：9元。",
        "若回踩10元后再建仓。触发价：10元；止损价：9元。",
        "若站稳10元则条件试探仓。触发价：10元；止损价：9元。",
        "站稳10元后分批建立仓位。触发价：10元；止损价：9元。",
    ),
)
def test_trade_quality_check_recognizes_bare_conditional_entry_directives(directive):
    check = build_trade_quality_check(
        final_decision=directive,
        user_context={"current_position": 0},
    )

    assert check["entry_intent"] is True


def test_trade_quality_check_no_buy_verdict_does_not_create_long_setup_conflict():
    check = build_trade_quality_check(
        final_decision="最终建议：不买入。",
        user_context={"current_position": 0, "constraints": ["禁止买入"]},
    )

    assert check["entry_intent"] is False
    assert check["conflicts"] == []
    assert not any("做多方案" in item for item in check["do_not_trade_if"])


def test_trade_quality_check_later_conditional_entry_overrides_current_observe():
    check = build_trade_quality_check(
        final_decision="当前观望，若股价站稳10元，可轻仓买入。止损价：9元。",
        user_context={"current_position": 0},
    )

    assert check["entry_intent"] is True


def test_trade_quality_check_ignores_third_party_and_completed_buy_facts():
    for fact in (
        "北向资金今日买入1000万元。",
        "公司已买入100万股。",
        "用户昨日已买入100股。",
    ):
        check = build_trade_quality_check(
            investment_plan=fact,
            trader_plan="触发价：10元；止损价：9元。",
            final_decision="最终建议：WAIT，等待条件。",
            user_context={"current_position": 0},
        )
        assert check["entry_intent"] is False


@pytest.mark.parametrize(
    "fact",
    (
        "若北向资金买入1亿元，则市场情绪改善。",
        "如果机构增持股份，估值可能修复。",
        "若公司买入原材料，则毛利率改善。",
        "若股东增持股份，则形成催化。",
        "若主力资金买入，则信号转强。",
    ),
)
def test_trade_quality_check_does_not_promote_third_party_conditional_activity(fact):
    check = build_trade_quality_check(
        final_decision=f"{fact}触发价：10元；止损价：9元。",
        user_context={"current_position": 0},
    )

    assert check["entry_intent"] is False
    assert check["conditional_entry_intent"] is False


@pytest.mark.parametrize(
    "fact",
    (
        "北向资金在股价站稳10元后买入。",
        "机构在股价站稳10元后增持。",
        "控股股东承诺股价站稳10元后增持。",
    ),
)
def test_trade_quality_check_does_not_promote_conditional_third_party_trade(fact):
    check = build_trade_quality_check(
        final_decision=f"{fact}触发价：10元；止损价：9元。",
        user_context={"current_position": 0},
    )

    assert check["entry_intent"] is False
    assert check["conditional_entry_intent"] is False


@pytest.mark.parametrize(
    "fact",
    (
        "员工持股计划拟在股价站稳10元后买入。",
        "高管计划在股价站稳10元后增持。",
        "董监高拟在股价站稳10元后增持。",
    ),
)
def test_trade_quality_check_does_not_promote_employee_or_executive_purchase(fact):
    check = build_trade_quality_check(
        final_decision=f"{fact}触发价：10元；止损价：9元。",
        user_context={"current_position": 0},
    )

    assert check["entry_intent"] is False
    assert check["conditional_entry_intent"] is False


@pytest.mark.parametrize(
    "verdict",
    (
        "最终建议：暂不操作。",
        "最终建议：保持空仓。",
        "最终建议：不交易。",
    ),
)
def test_final_explicit_no_trade_verdict_vetoes_upstream_entry(verdict):
    check = build_trade_quality_check(
        trader_plan="若站稳10元则买入。触发价10元。止损价9元。",
        final_decision=verdict,
        user_context={"current_position": 0},
    )

    assert check["entry_intent"] is False
    assert check["conditional_entry_intent"] is False


def test_trade_quality_check_keeps_user_entry_after_third_party_activity():
    check = build_trade_quality_check(
        final_decision=(
            "若北向资金买入1亿元，则等待股价站稳10元后再轻仓买入。"
            "触发价：10元；止损价：9元。"
        ),
        user_context={"current_position": 0},
    )

    assert check["entry_intent"] is True
    assert check["conditional_entry_intent"] is True


def test_trade_quality_check_parses_directive_after_fact_like_prefix():
    for directive in (
        "主力资金连续流入后可轻仓买入。触发价：10元；止损价：9元。",
        "我已决定站稳10元后买入。触发价：10元；止损价：9元。",
        "北向资金今日买入1000万元后，若站稳10元可轻仓买入。止损价：9元。",
    ):
        check = build_trade_quality_check(
            final_decision=directive,
            user_context={"current_position": 0},
        )
        assert check["entry_intent"] is True


def test_trade_quality_check_recognizes_explicit_english_buy_intent():
    check = build_trade_quality_check(
        final_decision="若股价站稳10元，执行 BUY。止损价：9元。",
        user_context={"current_position": 0},
    )

    assert check["entry_intent"] is True


@pytest.mark.parametrize("verdict", ("SELL", "HOLD"))
def test_english_final_non_entry_verdict_blocks_upstream_entry(verdict):
    check = build_trade_quality_check(
        investment_plan="若站稳10元则买入。触发价：10元；止损价：9元。",
        final_decision=f"FINAL TRANSACTION PROPOSAL: **{verdict}**",
        user_context={"current_position": 0},
    )

    assert check["entry_intent"] is False
    assert check["conditional_entry_intent"] is False


def test_english_final_buy_contract_parses_trigger_stop_and_range():
    trigger_check = build_trade_quality_check(
        final_decision=(
            "FINAL TRANSACTION PROPOSAL: **BUY**\n"
            "Entry trigger: 10.00\n"
            "Stop-loss price: 9.00"
        ),
        user_context={"current_position": 0},
    )
    range_check = build_trade_quality_check(
        final_decision=(
            "FINAL TRANSACTION PROPOSAL: **BUY**\n"
            "Entry range: 10.00-11.00\n"
            "Stop-loss price: 9.00"
        ),
        user_context={"current_position": 0},
    )

    assert trigger_check["entry_intent"] is True
    assert trigger_check["conditional_entry_intent"] is True
    assert trigger_check["trigger_price"] == 10.0
    assert trigger_check["stop_loss_price"] == 9.0
    assert range_check["entry_range"] == "10.00-11.00"
    assert range_check["conditional_entry_intent"] is True


def test_trade_quality_check_recognizes_supported_entry_synonyms():
    for action in (
        "购入",
        "加仓",
        "增持",
        "介入",
        "参与",
        "配置仓位",
        "抄底",
        "追涨",
        "买一点",
    ):
        check = build_trade_quality_check(
            final_decision=f"若股价站稳10元，建议{action}。止损价：9元。",
            user_context={"current_position": 0},
        )
        assert check["entry_intent"] is True, action


def test_trade_quality_check_does_not_treat_non_trade_synonyms_as_entry():
    for text in (
        "建议参与讨论。",
        "建议配置参数。",
        "建议参与研究评估。",
        "若有需要可参与股东大会投票。触发价：10元；止损价：9元。",
        "若有需要可参与业绩说明会。触发价：10元；止损价：9元。",
        "若条件允许可参与公司治理。触发价：10元；止损价：9元。",
    ):
        check = build_trade_quality_check(
            final_decision=text,
            user_context={"current_position": 0},
        )
        assert check["entry_intent"] is False, text


def test_trade_quality_check_does_not_promote_analytical_buy_language():
    for text in (
        "最终建议：维持买入评级。触发条件：站稳10元。止损价：9元。",
        "最终建议：买入评级维持不变。触发条件：站稳10元。止损价：9元。",
        "最终建议：买入逻辑尚不充分。触发条件：站稳10元。止损价：9元。",
        "最终建议：买入信号未出现。触发条件：站稳10元。止损价：9元。",
    ):
        check = build_trade_quality_check(
            final_decision=text,
            user_context={"current_position": 0},
        )
        assert check["entry_intent"] is False, text


def test_trade_quality_check_does_not_treat_constraints_as_violations():
    check = build_trade_quality_check(
        final_decision="若股价回踩并站稳10元，可轻仓买入。止损价：9元。",
        user_context={"current_position": 0, "constraints": ["不追高"]},
    )

    assert check["user_constraints"] == ["不追高"]
    assert check["do_not_trade_if"] == []


def test_trade_quality_check_blocks_explicit_constraint_violations():
    check = build_trade_quality_check(
        final_decision="若股价站稳10元，可轻仓买入。止损价：9元。",
        user_context={
            "current_position": 0,
            "constraints": ["不追高"],
            "constraint_violations": ["当前方案属于追高"],
        },
    )

    assert "交易计划违反用户硬约束：当前方案属于追高" in check["do_not_trade_if"]


def test_trade_quality_check_blocks_api_visible_no_buy_constraint():
    check = build_trade_quality_check(
        final_decision="若股价站稳10元，可轻仓买入。止损价：9元。",
        user_context={"current_position": 0, "constraints": ["禁止买入"]},
    )

    assert "交易计划违反用户硬约束：禁止买入" in check["do_not_trade_if"]


def test_trade_quality_check_enforces_supported_api_constraints():
    cases = (
        ("不融资", "若站稳10元，建议融资买入。止损价：9元。"),
        ("不加杠杆", "若站稳10元，建议杠杆买入。止损价：9元。"),
        ("不能补仓", "若站稳10元，建议补仓。止损价：9元。"),
        ("不接受隔夜", "若站稳10元，可买入并持有一周。止损价：9元。"),
        ("只做T+1", "若站稳10元，可做T+0。止损价：9元。"),
    )
    for constraint, plan in cases:
        check = build_trade_quality_check(
            final_decision=plan,
            user_context={"current_position": 0, "constraints": [constraint]},
        )
        assert f"交易计划违反用户硬约束：{constraint}" in check["do_not_trade_if"]


@pytest.mark.parametrize(
    ("constraint", "plan"),
    (
        ("不融资", "若站稳10元，可用融资资金买入。止损价：9元。"),
        ("不加杠杆", "若站稳10元，可用杠杆资金买入。止损价：9元。"),
    ),
)
def test_trade_quality_check_blocks_common_financing_wording(constraint, plan):
    check = build_trade_quality_check(
        final_decision=plan,
        user_context={"current_position": 0, "constraints": [constraint]},
    )

    assert f"交易计划违反用户硬约束：{constraint}" in check["do_not_trade_if"]


def test_trade_quality_check_does_not_block_constraint_compliant_plan():
    check = build_trade_quality_check(
        final_decision="若回踩10元，可用自有资金买入，当日不卖出。止损价：9元。",
        user_context={
            "current_position": 0,
            "constraints": ["不融资", "不加杠杆", "只做T+1"],
        },
    )

    assert check["do_not_trade_if"] == []


def test_trade_quality_check_fails_closed_on_unstructured_hard_constraints():
    for constraint in ("最大仓位5%", "只买ETF", "不做创业板", "最大亏损2%"):
        check = build_trade_quality_check(
            final_decision="若站稳10元，可买入20%仓位。触发价：10元；止损价：9元。",
            user_context={"current_position": 0, "constraints": [constraint]},
        )
        assert (
            f"交易计划违反用户硬约束：无法自动验证用户硬约束：{constraint}"
            in check["do_not_trade_if"]
        )


def test_trade_quality_check_enforces_exit_side_hard_constraints():
    for constraint in ("禁止卖出", "不得减仓", "不能清仓", "暂不止损"):
        check = build_trade_quality_check(
            final_decision="最终建议：建议减仓。止损价：9元。",
            user_context={"current_position": 100, "constraints": [constraint]},
        )
        assert f"交易计划违反用户硬约束：{constraint}" in check["do_not_trade_if"]


@pytest.mark.parametrize(
    "directive",
    (
        "若跌破9元则减仓。止损价：9元。",
        "跌破9元后清仓。止损价：9元。",
        "触及止损价后卖出。止损价：9元。",
    ),
)
def test_exit_constraint_blocks_conditional_exit_directives(directive):
    check = build_trade_quality_check(
        final_decision=directive,
        user_context={"current_position": 100, "constraints": ["禁止卖出"]},
    )

    assert "交易计划违反用户硬约束：禁止卖出" in check["do_not_trade_if"]


def test_exit_constraint_allows_existing_holder_to_hold():
    check = build_trade_quality_check(
        final_decision="最终建议：持有，继续观察。止损价：9元。",
        user_context={"current_position": 100, "constraints": ["禁止卖出"]},
    )

    assert check["do_not_trade_if"] == []


@pytest.mark.parametrize(
    "final_decision",
    ("最终建议：持有。", "交易建议：持有。", "持有。", "继续持有。"),
)
def test_final_hold_vetoes_earlier_entry_plan(final_decision):
    check = build_trade_quality_check(
        investment_plan="若站稳10元则买入。触发价：10元；止损价：9元。",
        final_decision=final_decision,
        user_context={"current_position": 0},
    )

    assert check["entry_intent"] is False
    assert check["conditional_entry_intent"] is False


@pytest.mark.parametrize(
    "final_decision",
    (
        "事件后买入信号出现。触发价：10元；止损价：9元。",
        "业绩发布后买入风险下降。触发价：10元；止损价：9元。",
        "政策落地后买入意愿增强。触发价：10元；止损价：9元。",
    ),
)
def test_analytical_buy_nouns_do_not_create_entry_intent(final_decision):
    check = build_trade_quality_check(
        final_decision=final_decision,
        user_context={"current_position": 0},
    )

    assert check["entry_intent"] is False
    assert check["conditional_entry_intent"] is False


@pytest.mark.parametrize(
    "final_decision",
    (
        "最终意见：买入。买入区间：10-11元；止损价：9元。",
        "系统指令：买入。买入区间：10-11元；止损价：9元。",
        "趋势确认后我会买入。买入区间：10-11元；止损价：9元。",
    ),
)
def test_authoritative_and_first_person_entry_directives_are_recognized(final_decision):
    check = build_trade_quality_check(
        final_decision=final_decision,
        user_context={"current_position": 0},
    )

    assert check["entry_intent"] is True


@pytest.mark.parametrize(
    "final_decision",
    ("解禁后卖出压力上升。", "放量后卖出信号出现。"),
)
def test_analytical_sell_nouns_do_not_violate_exit_constraint(final_decision):
    check = build_trade_quality_check(
        final_decision=f"最终建议：持有。{final_decision}止损价：9元。",
        user_context={"current_position": 100, "constraints": ["禁止卖出"]},
    )

    assert check["do_not_trade_if"] == []


@pytest.mark.parametrize("phrase", ("止损无需调整。", "止损无变化。"))
def test_stop_maintenance_language_keeps_upstream_stop(phrase):
    check = build_trade_quality_check(
        trader_plan="止损价：9元。",
        final_decision=f"最终建议：持有；{phrase}",
        user_context={"current_position": 100},
    )

    assert check["stop_loss_price"] == 9.0
    assert "无法从报告中解析出明确止损价。" not in check["do_not_trade_if"]


@pytest.mark.parametrize(
    "phrase",
    ("止损价：暂无调整，仍为9元。", "止损价：未设置过宽，应调整为9元。"),
)
def test_qualified_stop_language_uses_final_numeric_value(phrase):
    check = build_trade_quality_check(
        trader_plan="止损价：8元。",
        final_decision=f"最终建议：持有；{phrase}",
        user_context={"current_position": 100},
    )

    assert check["stop_loss_price"] == 9.0
    assert "无法从报告中解析出明确止损价。" not in check["do_not_trade_if"]


def test_exit_constraint_allows_explicit_no_sell_recommendation():
    check = build_trade_quality_check(
        final_decision="最终建议：不建议卖出，继续持有。止损价：9元。",
        user_context={"current_position": 100, "constraints": ["禁止卖出"]},
    )

    assert check["do_not_trade_if"] == []


def test_exit_constraint_recognizes_percentage_only_holding():
    check = build_trade_quality_check(
        final_decision="最终建议：立即清仓。止损价：9元。",
        user_context={"current_position_pct": 30, "constraints": ["禁止卖出"]},
    )

    assert "交易计划违反用户硬约束：禁止卖出" in check["do_not_trade_if"]


def test_position_context_only_holding_enforces_exit_constraint_and_stop():
    check = build_trade_quality_check(
        final_decision="最终建议：立即清仓。",
        user_context={"constraints": ["禁止清仓"]},
        position_context={"has_position": True},
    )

    assert "交易计划违反用户硬约束：禁止清仓" in check["do_not_trade_if"]
    assert "无法从报告中解析出明确止损价。" in check["do_not_trade_if"]


@pytest.mark.parametrize(
    "stop_text",
    (
        "止损：2%。",
        "最大止损：2%。",
        "止损：2％。",
        "止损：2个百分点。",
        "止损：2个点。",
    ),
)
def test_percentage_risk_limit_is_not_parsed_as_stop_price(stop_text):
    check = build_trade_quality_check(
        final_decision=f"若股价站稳10元可轻仓买入。{stop_text}",
        user_context={"current_position": 0},
    )

    assert check["stop_loss_price"] is None
    assert "无法从报告中解析出明确止损价。" in check["do_not_trade_if"]


@pytest.mark.parametrize(
    "stop_text",
    (
        "若跌破2%则止损。",
        "跌破2%止损。",
        "止损条件为跌破2%。",
        "若跌破2％则止损。",
        "止损条件为跌破2个百分点。",
    ),
)
def test_conditional_percentage_stop_is_not_authorized_as_absolute_price(stop_text):
    check = build_trade_quality_check(
        final_decision=f"若站稳10元则买入。触发价：10元。{stop_text}",
        user_context={"current_position": 0},
    )

    assert check["entry_intent"] is True
    assert check["stop_loss_price"] is None
    assert "无法从报告中解析出明确止损价。" in check["do_not_trade_if"]


@pytest.mark.parametrize(
    "decision",
    (
        "若站稳10元则买入；买入后若跌破9元则止损。触发价：10元；止损价：9元。",
        "若站稳10元则买入；建仓后计划跌破9元止损。触发价：10元；止损价：9元。",
    ),
)
def test_future_post_entry_stop_does_not_cancel_entry_intent(decision):
    check = build_trade_quality_check(
        final_decision=decision,
        user_context={"current_position": 0},
    )

    assert check["entry_intent"] is True
    assert check["execution_mode"] == "条件触发"
    assert check["do_not_trade_if"] == []


def test_bare_after_condition_is_classified_as_conditional_entry():
    check = build_trade_quality_check(
        final_decision="站稳10元后买入。触发价：10元；止损价：9元。",
        user_context={"current_position": 0},
    )

    assert check["entry_intent"] is True
    assert check["execution_mode"] == "条件触发"
    assert check["action"] == "等待触发"


@pytest.mark.parametrize("connector", ("即", "即可", "就", "便"))
def test_suffix_connector_is_classified_as_conditional_entry(connector):
    check = build_trade_quality_check(
        final_decision=f"价格达到10元{connector}买入。触发价：10元；止损价：9元。",
        user_context={"current_position": 0},
    )

    assert check["entry_intent"] is True
    assert check["conditional_entry_intent"] is True
    assert check["execution_mode"] == "条件触发"
    assert check["action"] == "等待触发"


def test_today_entry_directive_is_not_treated_as_completed_purchase():
    check = build_trade_quality_check(
        final_decision="若站稳10元则今日买入。触发价：10元；止损价：9元。",
        user_context={"current_position": 0},
    )

    assert check["entry_intent"] is True
    assert check["execution_mode"] == "条件触发"


@pytest.mark.parametrize("final_wait", ("观望", "等待", "HOLD", "WAIT"))
def test_final_trade_advice_wait_vetoes_upstream_entry(final_wait):
    check = build_trade_quality_check(
        investment_plan="若站稳10元可轻仓买入。触发价：10元；止损价：9元。",
        final_decision=f"最终交易建议：{final_wait}。",
        user_context={"current_position": 0},
    )

    assert check["entry_intent"] is False


def test_suffix_action_enum_does_not_override_explicit_wait():
    check = build_trade_quality_check(
        investment_plan="建议在10-11元区间建仓，止损价9元。",
        final_decision="最终交易建议：观望。BUY / SELL / HOLD 三分类标签",
        user_context={"current_position": 0},
    )

    assert check["entry_intent"] is False
    assert check["conditional_entry_intent"] is False


def test_future_post_entry_protection_does_not_override_explicit_wait():
    check = build_trade_quality_check(
        investment_plan="建议在10-11元区间建仓，止损价9元。",
        final_decision="最终交易建议：观望。若未来买入后跌破9元则止损。",
        user_context={"current_position": 0},
    )

    assert check["entry_intent"] is False
    assert check["conditional_entry_intent"] is False


@pytest.mark.parametrize(
    "final_wait",
    ("观望。", "建议观望。", "综合判断后观望。", "等待。", "HOLD。", "WAIT。"),
)
def test_standalone_final_wait_vetoes_upstream_entry(final_wait):
    check = build_trade_quality_check(
        investment_plan="若站稳10元可轻仓买入。触发价：10元；止损价：9元。",
        final_decision=final_wait,
        user_context={"current_position": 0},
    )

    assert check["entry_intent"] is False


@pytest.mark.parametrize("negated_entry", ("不考虑买入", "不准备建仓", "不打算入场"))
def test_modal_negated_entry_is_not_executable(negated_entry):
    check = build_trade_quality_check(
        final_decision=f"若站稳10元，目前{negated_entry}。触发价：10元；止损价：9元。",
        user_context={"current_position": 0},
    )

    assert check["entry_intent"] is False


def test_bare_conditional_entry_without_connector_is_executable():
    check = build_trade_quality_check(
        final_decision="若股价站稳10元买入。触发价：10元；止损价：9元。",
        user_context={"current_position": 0},
    )

    assert check["entry_intent"] is True
    assert check["conditional_entry_intent"] is True


@pytest.mark.parametrize(
    "directive",
    (
        "站稳10元买入",
        "突破10元轻仓买入",
        "股价回踩10元附近分批建仓",
    ),
)
def test_price_qualified_entry_without_connector_is_conditional(directive):
    check = build_trade_quality_check(
        final_decision=f"最终交易建议：{directive}。触发价：10元；止损价：9元。",
        user_context={"current_position": 0},
    )

    assert check["entry_intent"] is True
    assert check["conditional_entry_intent"] is True
    assert check["execution_mode"] == "条件触发"


@pytest.mark.parametrize("negated_exit", ("当前不考虑卖出", "当前不准备清仓"))
def test_modal_negated_exit_does_not_violate_sell_constraint(negated_exit):
    check = build_trade_quality_check(
        final_decision=f"{negated_exit}，止损价：9元。",
        user_context={"current_position": 100, "constraints": ["禁止卖出"]},
    )

    assert not any("违反用户硬约束" in item for item in check["do_not_trade_if"])


@pytest.mark.parametrize("stop_price", (0, 10, 12))
def test_conditional_entry_rejects_nonpositive_or_nonprotective_stop(stop_price):
    check = build_trade_quality_check(
        final_decision=f"若站稳10元则买入。触发价：10元；止损价：{stop_price}元。",
        user_context={"current_position": 0},
    )

    assert check["do_not_trade_if"]
    assert any("止损价必须" in item for item in check["do_not_trade_if"])


def test_rightmost_positive_entry_overrides_earlier_negated_entry():
    check = build_trade_quality_check(
        final_decision=(
            "不建议追高买入但回调站稳10元后可轻仓买入。"
            "触发价：10元；止损价：9元。"
        ),
        user_context={"current_position": 0},
    )

    assert check["entry_intent"] is True
    assert check["execution_mode"] == "条件触发"


def test_trade_quality_check_does_not_treat_constraint_risk_discussion_as_use():
    cases = (
        ("不追高", "追高风险较大，若回踩10元可轻仓买入。止损价：9元。"),
        ("不融资", "融资风险较高，若回踩10元可用自有资金买入。止损价：9元。"),
        ("不加杠杆", "杠杆交易风险较高，若回踩10元可用自有资金买入。止损价：9元。"),
    )
    for constraint, plan in cases:
        check = build_trade_quality_check(
            final_decision=plan,
            user_context={"current_position": 0, "constraints": [constraint]},
        )
        assert f"交易计划违反用户硬约束：{constraint}" not in check["do_not_trade_if"]


def test_no_overnight_constraint_blocks_impossible_same_day_exit_plan():
    check = build_trade_quality_check(
        final_decision="隔夜风险较高，若回踩10元可买入并当日收盘前离场。止损价：9元。",
        user_context={"current_position": 0, "constraints": ["不接受隔夜"]},
    )

    assert "交易计划违反用户硬约束：不接受隔夜" in check["do_not_trade_if"]


def test_no_buy_constraint_does_not_block_existing_holder_hold():
    check = build_trade_quality_check(
        final_decision="最终建议：持有，继续观察。止损价：9元。",
        user_context={"current_position": 100, "constraints": ["禁止买入"]},
    )

    assert check["entry_intent"] is False
    assert check["do_not_trade_if"] == []


def test_trade_quality_check_does_not_parse_numbered_risk_list_as_stop_loss():
    check = build_trade_quality_check(
        investment_plan="当前空仓观望。",
        trader_plan="不建议入场，止损价：不适用（因不建议入场）。",
        final_decision=(
            "出现以下任一情况，应立即放弃任何入场计划，并对已持仓部分执行止损：\n"
            "1. RISK-1恶化：后续财报显示盈利增速进一步下滑。\n"
            "2. 技术面破位：股价放量跌破492.65元且无法收回。\n"
            "目标价：—\n止损价：—"
        ),
    )

    assert check["stop_loss_price"] is None
    # [P1-3] No position + no conditional entry → stop-loss missing is NOT an error
    assert "无法从报告中解析出明确止损价。" not in check["do_not_trade_if"]


def test_p13_no_position_conditional_entry_requires_stop_loss():
    """[P1-3] No position + conditional entry → stop-loss IS required."""
    check = build_trade_quality_check(
        investment_plan="未持仓，若突破可试仓。",
        trader_plan="若股价放量突破50.00元，可考虑建仓。",
        final_decision="触发条件：放量突破50.00元建仓买入。",
    )
    assert check["stop_loss_price"] is None
    assert "无法从报告中解析出明确止损价。" in check["do_not_trade_if"]


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


@pytest.mark.parametrize(
    "text",
    (
        "若股价站稳10元，建议参与本次业绩说明会。止损价9元。",
        "股价站稳10元时，建议参与即将召开的股东大会。止损价9元。",
        "若股价站稳10元，建议参与公司的电话会议。止损价9元。",
    ),
)
def test_qualified_non_trade_participation_does_not_create_entry(text):
    check = build_trade_quality_check(
        final_decision=text,
        user_context={"current_position": 0},
    )

    assert check["entry_intent"] is False
    assert check["conditional_entry_intent"] is False


def test_explicit_entry_range_is_a_conditional_entry_contract():
    check = build_trade_quality_check(
        final_decision="建议在10-11元区间买入。止损价9元。",
        user_context={"current_position": 0},
    )

    assert check["entry_range"] == "10-11"
    assert check["entry_intent"] is True
    assert check["conditional_entry_intent"] is True
    assert check["execution_mode"] == "条件触发"


@pytest.mark.parametrize(
    "text",
    (
        "综合分析建议股价站稳10元后买入。止损价9元。",
        "经分析，建议股价站稳10元后买入。止损价9元。",
        "风险分析后建议股价站稳10元后买入。止损价9元。",
    ),
)
def test_analytical_preface_does_not_suppress_entry_directive(text):
    check = build_trade_quality_check(
        final_decision=text,
        user_context={"current_position": 0},
    )

    assert check["entry_intent"] is True
    assert check["conditional_entry_intent"] is True


def test_action_enum_documentation_does_not_override_entry_directive():
    check = build_trade_quality_check(
        final_decision=(
            "若站稳10元则买入。止损价9元。"
            "说明：对应 BUY / SELL / HOLD。"
        ),
        user_context={"current_position": 0},
    )

    assert check["entry_intent"] is True
    assert check["conditional_entry_intent"] is True


def test_final_decision_invalidated_trigger_clears_upstream_level():
    check = build_trade_quality_check(
        trader_plan="触发价10元。止损价9元。",
        final_decision="最终交易决策：若条件满足则买入。触发价待定；止损价8元。",
        user_context={"current_position": 0},
    )

    assert check["entry_intent"] is True
    assert check["trigger_price"] is None
    assert check["entry_range"] is None
    assert "最终决策中的入场触发价或区间尚未确认。" in check["do_not_trade_if"]


def test_final_trigger_invalidation_without_repeated_entry_clears_upstream_level():
    check = build_trade_quality_check(
        trader_plan="若站稳10元则买入。触发价10元。止损价9元。",
        final_decision="最终交易决策：触发价待定。止损价8元。",
        user_context={"current_position": 0},
    )

    assert check["entry_intent"] is True
    assert check["trigger_price"] is None
    assert check["entry_range"] is None
    assert "最终决策中的入场触发价或区间尚未确认。" in check["do_not_trade_if"]


def test_final_decision_cancelled_stop_clears_upstream_level():
    check = build_trade_quality_check(
        trader_plan="入场区间10-11元。止损价9元。",
        final_decision="最终交易决策：若进入10-11元区间则买入。取消止损。",
        user_context={"current_position": 0},
    )

    assert check["entry_intent"] is True
    assert check["stop_loss_price"] is None
    assert "最终决策中的止损价已撤销或尚未确认。" in check["do_not_trade_if"]


@pytest.mark.parametrize(
    "withdrawal",
    (
        "当前不设置止损",
        "止损暂不设置",
        "最终决定不设止损",
        "止损价不再采用",
    ),
)
def test_final_explicit_no_stop_wording_clears_upstream_level(withdrawal):
    check = build_trade_quality_check(
        trader_plan="若站稳10元则买入。触发价10元；止损价9元。",
        final_decision=f"最终交易建议：若站稳10元则买入，但{withdrawal}。",
        user_context={"current_position": 0},
    )

    assert check["entry_intent"] is True
    assert check["stop_loss_price"] is None
    assert "最终决策中的止损价已撤销或尚未确认。" in check["do_not_trade_if"]


@pytest.mark.parametrize(
    "withdrawal",
    (
        "Stop-loss: not applicable",
        "Stop loss cancelled",
        "Stop-loss price: withdrawn",
    ),
)
def test_final_english_stop_withdrawal_clears_upstream_level(withdrawal):
    check = build_trade_quality_check(
        trader_plan="Entry range: 10-11. Stop-loss: 9.",
        final_decision=(
            "FINAL TRANSACTION PROPOSAL: BUY. Entry range: 10-11. "
            f"{withdrawal}."
        ),
        user_context={"current_position": 0},
    )

    assert check["entry_intent"] is True
    assert check["stop_loss_price"] is None
    assert "最终决策中的止损价已撤销或尚未确认。" in check["do_not_trade_if"]


def test_later_conditional_stop_supersedes_earlier_pending_stop():
    check = build_trade_quality_check(
        final_decision="初稿止损价：待定。最终建议：若跌破8元则止损。",
        user_context={"current_position": 100},
    )

    assert check["stop_loss_price"] == 8.0
    assert "无法从报告中解析出明确止损价。" not in check["do_not_trade_if"]


def test_percentage_only_multi_value_stop_is_not_a_price():
    check = build_trade_quality_check(
        final_decision="若站稳12元则买入。触发价：12元。止损价：2%和9%。",
        user_context={"current_position": 0},
    )

    assert check["stop_loss_price"] is None
    assert "无法从报告中解析出明确止损价。" in check["do_not_trade_if"]


def test_stop_inside_descending_entry_range_blocks_entry():
    check = build_trade_quality_check(
        final_decision="若回调则买入。买入区间：10-9元；止损价：9.5元。",
        user_context={"current_position": 0},
    )

    assert check["entry_range"] == "10-9"
    assert "止损价必须低于入场触发价。" in check["do_not_trade_if"]


def test_degenerate_entry_range_blocks_entry():
    check = build_trade_quality_check(
        final_decision="若回调则买入。买入区间：10-10元；止损价：9元。",
        user_context={"current_position": 0},
    )

    assert "入场区间必须包含两个不同的有效价格。" in check["do_not_trade_if"]


def test_meta_entry_discussion_does_not_hide_later_final_wait():
    check = build_trade_quality_check(
        investment_plan="若站稳10元则买入；止损价9元。",
        final_decision="讨论是否买入但最终建议观望。",
        user_context={"current_position": 0},
    )

    assert check["entry_intent"] is False
    assert check["conditional_entry_intent"] is False


@pytest.mark.parametrize(
    ("final_decision", "expected_stop"),
    (
        ("最终建议：持有；止损价：待定。", None),
        ("最终建议：持有；止损价：8元。", 8.0),
    ),
)
def test_held_position_final_stop_overrides_upstream(final_decision, expected_stop):
    check = build_trade_quality_check(
        investment_plan="止损价：9元。",
        final_decision=final_decision,
        user_context={"current_position": 100},
    )

    assert check["stop_loss_price"] == expected_stop
    if expected_stop is None:
        assert "无法从报告中解析出明确止损价。" in check["do_not_trade_if"]


@pytest.mark.parametrize(
    "final_decision",
    (
        "最终止损价暂未给出。",
        "最终止损价目前无法确定。",
        "最终止损价尚未设置。",
        "最终止损价仍待定。",
        "最终止损价暂时不适用。",
        "最终止损价取消执行。",
    ),
)
def test_qualified_final_stop_invalidation_clears_upstream(final_decision):
    check = build_trade_quality_check(
        investment_plan="止损价：9元。",
        final_decision=final_decision,
        user_context={"current_position": 100},
    )

    assert check["stop_loss_price"] is None


def test_same_clause_stop_replacement_wins_over_invalidation():
    check = build_trade_quality_check(
        investment_plan="止损价：8元。",
        final_decision="止损价取消，改为9元。",
        user_context={"current_position": 100},
    )

    assert check["stop_loss_price"] == 9.0


def test_later_valid_stop_overrides_earlier_invalidation():
    check = build_trade_quality_check(
        final_decision=(
            "初稿止损价：待定。最终交易建议：若站稳10元则买入；"
            "最终止损价：9元。"
        ),
        user_context={"current_position": 0},
    )

    assert check["stop_loss_price"] == 9.0
    assert "最终决策中的止损价已撤销或尚未确认。" not in check["do_not_trade_if"]


def test_qualified_later_stop_overrides_earlier_invalidation():
    check = build_trade_quality_check(
        final_decision="初步止损价：待定。最终止损价调整为9元。最终建议：持有。",
        user_context={"current_position": 100},
    )

    assert check["stop_loss_price"] == 9.0
    assert "最终决策中的止损价已撤销或尚未确认。" not in check["do_not_trade_if"]


@pytest.mark.parametrize(
    "final_decision",
    (
        "并非建议买入，入场区间10-11元，止损价9元。",
        "不是建议买入，入场区间10-11元，止损价9元。",
    ),
)
def test_copular_negation_vetoes_embedded_entry(final_decision):
    check = build_trade_quality_check(
        final_decision=final_decision,
        user_context={"current_position": 0},
    )

    assert check["entry_intent"] is False
    assert check["conditional_entry_intent"] is False
    assert check["action"] == "人工复核"


@pytest.mark.parametrize(
    "final_decision",
    (
        "最终意见：观望。",
        "风控结论：观望。",
        "系统指令：观望。",
        "交易意见：WAIT。",
        "处理意见：HOLD。",
    ),
)
def test_authoritative_wait_labels_override_upstream_entry(final_decision):
    check = build_trade_quality_check(
        investment_plan="若站稳10元则买入，止损价9元。",
        final_decision=final_decision,
        user_context={"current_position": 0},
    )

    assert check["entry_intent"] is False
    assert check["conditional_entry_intent"] is False


@pytest.mark.parametrize(
    "final_decision",
    (
        "### 执行质检\n最终建议：观望。",
        "模型自检如下：\n### 执行质检\n最终建议：不买入。",
    ),
)
def test_model_quality_heading_does_not_hide_later_entry_veto(final_decision):
    check = build_trade_quality_check(
        investment_plan="若站稳10元则买入，止损价9元。",
        final_decision=final_decision,
        user_context={"current_position": 0},
    )

    assert check["entry_intent"] is False
    assert check["conditional_entry_intent"] is False


@pytest.mark.parametrize("constraint", ("禁止增持", "禁止做多"))
def test_held_position_entry_only_constraint_does_not_block_exit(constraint):
    check = build_trade_quality_check(
        final_decision="最终建议：立即清仓。止损价：9元。",
        user_context={"current_position": 100, "constraints": [constraint]},
        position_context={"has_position": True},
    )

    assert check["exit_intent"] is True
    assert check["do_not_trade_if"] == []


def test_english_final_sell_sets_exit_intent():
    check = build_trade_quality_check(
        final_decision="FINAL TRANSACTION PROPOSAL: SELL\nStop-loss price: 9",
        user_context={"current_position": 100},
        position_context={"has_position": True},
    )

    assert check["entry_intent"] is False
    assert check["exit_intent"] is True


def test_later_final_hold_cancels_earlier_exit_intent():
    check = build_trade_quality_check(
        final_decision="建议先减仓；最终建议：HOLD。止损价：9元。",
        user_context={"current_position": 100},
        position_context={"has_position": True},
    )

    assert check["entry_intent"] is False
    assert check["exit_intent"] is False


def test_later_exit_supersedes_earlier_final_hold():
    check = build_trade_quality_check(
        final_decision="最终建议：HOLD；复核后建议减仓。止损价：9元。",
        user_context={"current_position": 100},
        position_context={"has_position": True},
    )

    assert check["exit_intent"] is True


def test_later_revised_trigger_is_used_for_stop_validation():
    check = build_trade_quality_check(
        final_decision=(
            "最终建议：触发价10元后买入，止损价9元；"
            "修订：最终触发位8元。"
        ),
        user_context={"current_position": 0},
    )

    assert check["trigger_price"] == 8.0
    assert "止损价必须低于入场触发价。" in check["do_not_trade_if"]


def test_later_revised_conditional_stop_is_authoritative():
    check = build_trade_quality_check(
        final_decision=(
            "最终建议：若站稳10元则买入，若跌破8元止损；"
            "修订：若跌破11元止损。"
        ),
        user_context={"current_position": 0},
    )

    assert check["stop_loss_price"] == 11.0
    assert "止损价必须低于入场触发价。" in check["do_not_trade_if"]


@pytest.mark.parametrize(
    "final_decision",
    (
        "止损价：9元，现已撤销。",
        "最终决定：止损价9元已取消。",
        "最终止损为9元，但该止损已取消。",
    ),
)
def test_stop_value_followed_by_cancellation_is_invalidated(final_decision):
    check = build_trade_quality_check(
        investment_plan="止损价：8元。",
        final_decision=final_decision,
        user_context={"current_position": 100},
    )

    assert check["stop_loss_price"] is None
    assert "最终决策中的止损价已撤销或尚未确认。" in check["do_not_trade_if"]
