from unittest.mock import MagicMock
from tradingagents.graph.intent_parser import (
    parse_intent, build_horizon_context,
    _infer_analysis_intent, _infer_position_context,
)


def test_parse_intent_returns_defaults():
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = MagicMock(
        content='{"ticker": "600519", "horizons": ["short", "medium"], "focus_areas": [], "specific_questions": []}'
    )
    result = parse_intent("分析600519", mock_llm)
    assert result["ticker"] == "600519"
    assert result["horizons"] == ["short"]
    assert result["focus_areas"] == []
    assert "analysis_intent" in result
    assert "position_context" in result


def test_parse_intent_fallback_on_invalid_json():
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = MagicMock(content="这不是JSON")
    result = parse_intent("600519", mock_llm, fallback_ticker="600519")
    assert result["ticker"] == "600519"
    assert result["horizons"] == ["short"]
    assert result["focus_areas"] == []
    assert "analysis_intent" in result
    assert "position_context" in result


def test_build_horizon_context_short_contains_label():
    ctx = build_horizon_context("short", ["量价关系"], ["能否突破"])
    assert "短线" in ctx
    assert "量价关系" in ctx
    assert "能否突破" in ctx


def test_build_horizon_context_medium_has_label():
    ctx = build_horizon_context("medium", [], [], agent_type="fundamentals")
    assert "中线" in ctx


def test_build_horizon_context_short_fundamentals_has_downweight_hint():
    ctx = build_horizon_context("short", [], [], agent_type="fundamentals")
    assert "次要" in ctx


# ── G-001 P0: analysis_intent 推断测试 ──

class TestInferAnalysisIntent:
    def test_entry_intent(self):
        intent, horizon = _infer_analysis_intent("600519短线买点在哪里")
        assert intent == "entry"
        assert horizon == "short"

    def test_entry_intent_generic(self):
        intent, horizon = _infer_analysis_intent("能不能买入600519")
        assert intent == "entry"

    def test_common_purchase_questions_are_entry_intent(self):
        for query in (
            "600519值得买吗",
            "600519值不值得买",
            "600519什么时候买",
            "600519何时适合买",
            "600519买多少",
            "600519买多少仓位合适",
        ):
            assert _infer_analysis_intent(query) == ("entry", "short")

    def test_holding_intent(self):
        intent, horizon = _infer_analysis_intent("继续拿着600519")
        assert intent == "holding"
        assert horizon == "short"

    def test_add_intent(self):
        intent, horizon = _infer_analysis_intent("想加仓600519")
        assert intent == "add"

    def test_modified_action_questions_are_not_treated_as_negations(self):
        assert _infer_analysis_intent("要不要继续加仓600519") == (
            "add",
            "short",
        )
        assert _infer_analysis_intent("要不要继续减仓600519") == (
            "reduce",
            "short",
        )
        assert _infer_analysis_intent("要不要继续止损600519") == (
            "stop_loss",
            "short",
        )

    def test_add_phrases_outrank_generic_buy_rule(self):
        for query in (
            "我想加仓买入600519",
            "考虑补仓买入600519",
            "想买入更多600519",
        ):
            assert _infer_analysis_intent(query) == ("add", "short")

    def test_reduce_intent(self):
        intent, horizon = _infer_analysis_intent("考虑减仓600519")
        assert intent == "reduce"

    def test_stop_loss_intent(self):
        intent, horizon = _infer_analysis_intent("600519要不要止损")
        assert intent == "stop_loss"

    def test_watch_intent(self):
        intent, horizon = _infer_analysis_intent("先观察一下600519")
        assert intent == "watch"

    def test_default_watch(self):
        intent, horizon = _infer_analysis_intent("600519怎么样")
        assert intent == "watch"
        assert horizon == "short"

    def test_medium_holding_intent(self):
        intent, horizon = _infer_analysis_intent("中线拿不拿600519")
        assert intent == "holding"
        assert horizon == "medium"

    def test_medium_entry_intent(self):
        intent, horizon = _infer_analysis_intent("想中线建仓600519")
        assert intent == "entry"
        assert horizon == "medium"

    def test_buyer_research_terms_are_not_entry_intent(self):
        for query in (
            "分析主要买方是谁",
            "分析600519需要买方数据",
            "分析主要买盘力量",
        ):
            assert _infer_analysis_intent(query) == ("watch", "short")

    def test_denied_buy_wording_remains_watch(self):
        for query in (
            "不是说现在可以买，而是继续观察",
            "不代表可以买入，先观望",
        ):
            assert _infer_analysis_intent(query) == ("watch", "short")

    def test_compound_negated_actions_do_not_override_following_holding(self):
        for query, expected in (
            ("不建议卖出止盈，继续持有", "holding"),
            ("无需卖出止盈，继续持有", "holding"),
            ("不建议买入建仓，只观察", "watch"),
        ):
            assert _infer_analysis_intent(query) == (expected, "short")

    def test_affirmative_double_negation_routes_to_requested_action(self):
        for query, expected in (
            ("不得不减仓", "reduce"),
            ("不能不加仓", "add"),
            ("并非不能买入", "entry"),
        ):
            assert _infer_analysis_intent(query) == (expected, "short")

    def test_coordinated_action_vetoes_cover_every_action(self):
        for query, expected in (
            ("不建议买入或建仓，只观察", "watch"),
            ("不要加仓和补仓，继续持有", "holding"),
        ):
            assert _infer_analysis_intent(query) == (expected, "short")

    def test_third_party_trades_are_research_not_user_actions(self):
        for query in (
            "分析北向资金买入情况",
            "研究机构买入数据",
            "看看主力买入了多少",
            "大股东减仓了吗",
        ):
            assert _infer_analysis_intent(query) == ("watch", "short")

    def test_modified_third_party_purchases_remain_research(self):
        for query in (
            "北向资金昨天买入贵州茅台，后市如何",
            "大股东昨日以集中竞价方式买入100万股，怎么看",
            "基金经理在回调时买入该股的逻辑是什么",
            "公司回购买入股份对股价有什么影响",
        ):
            assert _infer_analysis_intent(query) == ("watch", "short")
            assert _infer_position_context(query, {})["has_position"] is False

    def test_third_party_buy_questions_remain_research_intent(self):
        for query in (
            "主力是否买入600519",
            "机构有没有买入600519",
            "北向资金有无买入600519",
        ):
            assert _infer_analysis_intent(query) == ("watch", "short")

    def test_compound_market_roles_are_third_party_actions(self):
        expected = {
            "基金经理建议买入，我只想观察": "watch",
            "券商分析师推荐加仓，我继续持有": "holding",
            "首席分析师建议建仓，只做研究": "watch",
        }
        for query, intent in expected.items():
            assert _infer_analysis_intent(query) == (intent, "short")

    def test_analyzed_company_is_not_mistaken_for_action_actor(self):
        for query in (
            "这家公司能买吗",
            "公司回购后可以买入吗",
        ):
            assert _infer_analysis_intent(query) == ("entry", "short")

        assert _infer_analysis_intent("公司计划买入股份") == ("watch", "short")

    def test_analyst_recommendations_are_not_user_actions(self):
        for query, expected in (
            ("分析师建议买入，我只想观察", "watch"),
            ("专家推荐加仓，我继续持有", "holding"),
            ("研报建议建仓，只做研究", "watch"),
            ("券商给出买入评级，分析600519", "watch"),
            ("分析师给了买入建议，分析600519", "watch"),
            ("研报维持买入评级，分析600519", "watch"),
        ):
            assert _infer_analysis_intent(query) == (expected, "short")

    def test_flat_buy_negations_and_fund_object_questions(self):
        for query in (
            "我还没买入，只想观察",
            "我还未买入，只想观察",
            "我尚未买入，只想观察",
        ):
            assert _infer_analysis_intent(query) == ("watch", "short")

        assert _infer_analysis_intent("这只基金能不能买") == ("entry", "short")

    def test_completed_purchase_is_not_a_new_entry_request(self):
        for query in (
            "我已经买入600519，后续怎么看",
            "我已经买入这只股票，后续怎么看",
            "我刚买了贵州茅台，后续怎么看",
            "我昨天买入了100股AAPL，现在怎么办",
            "昨天我买入了100股AAPL，现在怎么办",
            "上周三我买入100股AAPL，现在怎么办",
            "我上周买了100股AAPL，现在怎么办",
        ):
            assert _infer_analysis_intent(query) == ("watch", "short")

        assert _infer_analysis_intent("我已经买入600519，现在是否加仓") == (
            "add",
            "short",
        )


class TestInferPositionContext:
    def test_no_position(self):
        ctx = _infer_position_context("分析600519", {})
        assert ctx["has_position"] is False
        assert ctx["position_status_explicit"] is False

    def test_not_bought_variants_are_explicitly_flat(self):
        for query in ("我还没买入", "我还未买入", "我尚未买入"):
            context = _infer_position_context(query, {})
            assert context["has_position"] is False
            assert context["position_status_explicit"] is True

    def test_no_holding_assertion_before_contrast_is_explicitly_flat(self):
        context = _infer_position_context("我不持有但想买600519", {})

        assert context["has_position"] is False
        assert context["position_status_explicit"] is True

    def test_named_instrument_after_no_holding_is_explicitly_flat(self):
        for query in (
            "我现在不持有贵州茅台，能买吗",
            "我未持有600519，想买",
            "我不再持有宁德时代，后市如何",
        ):
            context = _infer_position_context(
                query,
                {"current_position": 100, "average_cost": 50},
            )

            assert context["has_position"] is False
            assert context["position_status_explicit"] is True
            assert context["shares"] is None
            assert context["avg_cost"] is None

    def test_negated_opinion_is_not_flat_position_assertion(self):
        context = _infer_position_context(
            "我不持有乐观观点，但现在持仓100股",
            {"current_position": 100, "average_cost": 50},
        )

        assert context["has_position"] is True
        assert context["shares"] == 100

    def test_position_caps_are_not_existing_holdings(self):
        for query in (
            "最大仓位20%，现在可以买入吗",
            "仓位上限20%，现在可以买入吗",
            "单票仓位控制在20%，现在可以买入吗",
            "风险预算仓位20%，现在可以买入吗",
        ):
            context = _infer_position_context(query, {})
            assert context["has_position"] is False
            assert context["position_status_explicit"] is False

    def test_entry_stop_loss_fields_are_not_existing_holdings(self):
        for query in (
            "我想建仓，止损位设为10元",
            "准备买入，止损条件怎么设",
            "未持仓，买入后止损价设多少",
        ):
            context = _infer_position_context(query, {})
            assert context["has_position"] is False

    def test_negated_buy_plan_does_not_erase_explicit_holding(self):
        for query in ("我没有买入计划", "我还没买入信号"):
            context = _infer_position_context(
                query,
                {"current_position": 100, "average_cost": 50},
            )
            assert context["has_position"] is True
            assert context["shares"] == 100

    def test_has_position_from_user_context(self):
        ctx = _infer_position_context("600519", {"current_position": 1000})
        assert ctx["has_position"] is True
        assert ctx["shares"] == 1000
        assert ctx["position_status_explicit"] is True

    def test_position_from_pct(self):
        ctx = _infer_position_context("600519", {"current_position_pct": 30})
        assert ctx["has_position"] is True
        assert ctx["position_pct"] == 30

    def test_inferred_from_keywords(self):
        ctx = _infer_position_context("600519加仓", {})
        assert ctx["has_position"] is True

    def test_possessive_position_risk_is_an_explicit_holding(self):
        for query in (
            "评估我的持仓风险，要不要减仓",
            "我目前的持仓风险如何",
        ):
            assert _infer_position_context(query, {})["has_position"] is True

    def test_completed_purchase_establishes_holding_without_share_unit(self):
        for query in (
            "我已经买入600519，后续怎么看",
            "我已经买入这只股票，后续怎么看",
            "我刚买了贵州茅台，后续怎么看",
            "我已买入AAPL，后续怎么看",
        ):
            assert _infer_position_context(query, {})["has_position"] is True

    def test_dated_completed_purchase_establishes_holding(self):
        for query in (
            "我昨天买入了100股AAPL，现在怎么办",
            "昨天我买入了100股AAPL，现在怎么办",
            "上周三我买入100股AAPL，现在怎么办",
            "我上周买了100股AAPL，现在怎么办",
            "我三天前买入AAPL，现在怎么办",
        ):
            context = _infer_position_context(query, {})
            assert context["has_position"] is True
            assert context["position_status_explicit"] is True

    def test_failed_or_unfilled_purchase_does_not_create_position(self):
        for query in (
            "昨天买入失败，今天能买吗",
            "昨天买入委托未成交，今天再买吗",
            "昨天买入100股后撤单，今天能买吗",
        ):
            context = _infer_position_context(query, {})
            assert context["has_position"] is False

    def test_failed_exit_or_later_holding_overrides_historical_exit(self):
        cases = (
            ("昨天清仓失败，现在仍持有100股", {"current_position": 100}),
            ("昨天清仓委托未成交，现在仍持有100股", {"current_position": 100}),
            ("昨天清仓，今天又买入100股", {}),
            ("上周全部卖出，今天已买入200股", {}),
        )
        for query, user_context in cases:
            context = _infer_position_context(query, user_context)
            assert context["has_position"] is True

    def test_entry_question_after_completed_exit_does_not_restore_position(self):
        context = _infer_position_context(
            "昨天卖出全部持仓，今天能买吗",
            {"current_position": 100},
        )
        assert context["has_position"] is False
        assert context["position_status_explicit"] is True

    def test_third_party_completed_purchase_does_not_create_user_holding(self):
        assert (
            _infer_position_context("机构已经买入600519，我先观察", {})[
                "has_position"
            ]
            is False
        )

    def test_third_party_actions_do_not_create_user_holding(self):
        for query in (
            "大股东减仓了吗，我只想观察",
            "机构建议加仓，我只想观察",
        ):
            assert _infer_position_context(query, {})["has_position"] is False

    def test_non_assertive_position_mentions_do_not_create_holding(self):
        for query in (
            "这只股票有持仓价值吗？",
            "是否有持仓风险？",
            "如果有持仓，跌破支撑是否需要止损？",
            "我有持仓吗？",
        ):
            assert _infer_position_context(query, {})["has_position"] is False

    def test_avg_cost(self):
        ctx = _infer_position_context("600519", {"average_cost": 50.5})
        assert ctx["avg_cost"] == 50.5

    def test_third_party_flat_statement_does_not_override_user_holding(self):
        for query in (
            "北向资金目前无持仓该股，我该减仓吗",
            "北向资金数据显示目前无持仓该股，我该减仓吗",
            "机构报告称目前无持仓该股，我该减仓吗",
        ):
            ctx = _infer_position_context(
                query,
                {
                    "current_position": 100,
                    "current_position_pct": 35,
                    "average_cost": 160,
                },
            )

            assert ctx["has_position"] is True
            assert ctx["shares"] == 100
            assert ctx["position_pct"] == 35
            assert ctx["avg_cost"] == 160

    def test_third_party_flat_statement_does_not_create_user_holding(self):
        for query in (
            "北向资金目前无持仓该股，我只想观察",
            "基金经理目前无持仓该股，我只想观察",
        ):
            assert _infer_position_context(query, {})["has_position"] is False

    def test_future_flat_goal_preserves_current_holding(self):
        ctx = _infer_position_context(
            "目标是明天清仓后持仓为0",
            {
                "current_position": 100,
                "current_position_pct": 20,
                "average_cost": 150,
            },
        )

        assert ctx["has_position"] is True
        assert ctx["shares"] == 100
        assert ctx["position_pct"] == 20
        assert ctx["avg_cost"] == 150

    def test_flat_position_alternative_does_not_erase_known_holding(self):
        ctx = _infer_position_context(
            "现在空仓还是继续持有？",
            {
                "current_position": 100,
                "current_position_pct": 35,
                "average_cost": 160,
            },
        )

        assert ctx["has_position"] is True
        assert ctx["shares"] == 100
        assert ctx["position_pct"] == 35
        assert ctx["avg_cost"] == 160

    def test_flat_position_alternative_without_snapshot_is_not_an_assertion(self):
        ctx = _infer_position_context("我现在是空仓还是持仓？", {})

        assert ctx["has_position"] is False
        assert ctx["position_status_explicit"] is False

    def test_present_flat_assertion_wins_in_new_clause(self):
        ctx = _infer_position_context(
            "明天再做计划，但现在持仓为0",
            {"current_position": 100, "average_cost": 150},
        )

        assert ctx["has_position"] is False
        assert ctx["shares"] is None
        assert ctx["avg_cost"] is None


def test_parse_intent_auto_upgrade_watch_to_holding():
    """未持仓 query 但 user_context 有持仓 → watch 升级为 holding"""
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = MagicMock(
        content='{"ticker": "600519", "horizons": ["short"], "focus_areas": [], "specific_questions": [], "user_context": {"current_position": 500}}'
    )
    result = parse_intent("分析600519", mock_llm)
    assert result["analysis_intent"] == "holding"  # auto-upgraded from watch
    assert result["position_context"]["has_position"] is True


def test_build_horizon_context_includes_intent_and_position():
    """G-001: build_horizon_context 输出交易目的和持仓状态"""
    ctx = build_horizon_context(
        "short", [], [],
        analysis_intent="entry",
        position_context={"has_position": False},
    )
    assert "寻找入场机会" in ctx
    assert "未持仓" in ctx


def test_build_horizon_context_shows_position_details():
    ctx = build_horizon_context(
        "short", [], [],
        analysis_intent="holding",
        position_context={"has_position": True, "avg_cost": 50.5, "shares": 1000, "position_pct": 30},
    )
    assert "已持仓" in ctx
    assert "成本 50.5" in ctx
    assert "持仓 1000" in ctx
