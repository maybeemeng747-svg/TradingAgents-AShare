"""F-001: TA研报执行层修复 — 10 项专项测试。

覆盖 002837 英维克报告暴露的 10 个问题：
  P0-1  止损价解析
  P0-2  估值 sanity check
  P0-3  Opportunity Score 封顶
  P1-4  未持仓语义
  P1-5  Buy/Risk Level 冲突
  P1-6  舆情 VERDICT 矛盾
  P1-7  入场区间误识别
  P2-8  打法标签
  P2-9  A股做空措辞
  P2-10 Evidence Coverage 保守化
"""

import pytest

from tradingagents.agents.utils.trade_setup import (
    _extract_stop_loss,
    _find_price_range,
    _setup_type,
    build_trade_quality_check,
    format_trade_quality_check,
)
from tradingagents.agents.utils.readiness_score import (
    calculate_evidence_coverage,
    calculate_opportunity_score,
    calculate_buy_level,
    calculate_risk_level,
    check_valuation_mismatch,
    sanitize_forbidden_strong_actions,
    _sanitize_short_selling_text,
    get_strong_action_gate,
    assess_confidence,
    ConfidenceLevel,
    EvidenceStatus,
    get_position_status,
)
from tradingagents.agents.utils.agent_states import extract_verdict


# ═══════════════════════════════════════════════════════════════
# P0-1: 止损价解析
# ═══════════════════════════════════════════════════════════════

class TestFix1StopLossParsing:
    """止损价解析：优先取风控段、支持条件止损、支持 bold markdown。"""

    def test_risk_manager_section_takes_priority(self):
        text = (
            "交易员建议：止损价 34.00元\n"
            "## 风控委员会最终裁决\n"
            "止损价：36.00元\n"
            "目标价：45.00元\n"
        )
        price, label = _extract_stop_loss(text)
        assert price == 36.00
        assert label == ""

    def test_conditional_stop_loss_pattern(self):
        text = "若跌破35.50元止损清仓离场。"
        price, label = _extract_stop_loss(text)
        assert price == 35.50
        assert label == "条件止损价"

    def test_bold_markdown_price(self):
        text = "**止损价**：**36.00元**"
        price, label = _extract_stop_loss(text)
        assert price == 36.00

    def test_multi_price_on_same_line(self):
        text = "止损价：34.40元（试探仓）和36.00元（主仓）"
        price, _ = _extract_stop_loss(text)
        assert price == 36.00

    def test_no_stop_loss_returns_none(self):
        text = "当前建议观望，等待信号确认。"
        price, label = _extract_stop_loss(text)
        assert price is None

    def test_no_position_without_entry_no_error(self):
        """[P1-3] 未持仓 + 无条件入场 → 止损价缺失不报错。"""
        check = build_trade_quality_check(
            investment_plan="当前空仓观望。",
            trader_plan="不建议入场，止损价：不适用。",
            final_decision="目标价：—\n止损价：—",
            user_context={"current_position": 0},
        )
        assert check["stop_loss_price"] is None
        assert "无法从报告中解析出明确止损价。" not in check["do_not_trade_if"]


# ═══════════════════════════════════════════════════════════════
# P0-2: 估值 sanity check
# ═══════════════════════════════════════════════════════════════

class TestFix2ValuationSanityCheck:
    """估值段引用价格偏离当前价 >20% 时标记 mismatch。"""

    def test_old_price_pollution_detected(self):
        text = "假设约15.00元，PE仅12倍，严重低估，建议买入。"
        result = check_valuation_mismatch(25.50, text)
        assert result["mismatch"] is True
        assert result["valuation_price"] == 15.0
        assert result["deviation_pct"] > 20

    def test_close_prices_no_mismatch(self):
        text = "假设股价约25.30元，对应PE为20倍，估值合理。"
        result = check_valuation_mismatch(25.50, text)
        assert result["mismatch"] is False

    def test_none_current_price_no_mismatch(self):
        result = check_valuation_mismatch(None, "假设约15.00元")
        assert result["mismatch"] is False

    def test_empty_report_no_mismatch(self):
        result = check_valuation_mismatch(25.50, "")
        assert result["mismatch"] is False

    def test_estimated_price_pattern(self):
        text = "以约15.80元计算，公司市值约为50亿。"
        result = check_valuation_mismatch(25.50, text)
        assert result["mismatch"] is True

    def test_within_20_percent_threshold(self):
        text = "假设约24.00元，估值合理。"
        result = check_valuation_mismatch(25.50, text)
        assert result["mismatch"] is False


# ═══════════════════════════════════════════════════════════════
# P0-3: Opportunity Score 封顶
# ═══════════════════════════════════════════════════════════════

class TestFix3OpportunityScoreCap:
    """Opportunity Score 在多种条件下被封顶。"""

    def test_gate_not_passed_cap_60(self):
        score = calculate_opportunity_score(
            trend_confirmed=True,
            capital_resonance="strong",
            catalyst_strength="strong",
            risk_reward_ratio="favorable",
            entry_quality="good",
            gate_passed=False,
        )
        assert score <= 60

    def test_bearish_direction_cap_60(self):
        score = calculate_opportunity_score(
            trend_confirmed=True,
            capital_resonance="strong",
            catalyst_strength="strong",
            risk_reward_ratio="favorable",
            entry_quality="good",
            final_direction_bearish=True,
        )
        assert score <= 60

    def test_trader_disallows_buy_cap_50(self):
        score = calculate_opportunity_score(
            trend_confirmed=True,
            capital_resonance="strong",
            catalyst_strength="strong",
            risk_reward_ratio="favorable",
            entry_quality="good",
            trader_disallows_buy=True,
        )
        assert score <= 50

    def test_low_data_completeness_cap_70(self):
        score = calculate_opportunity_score(
            trend_confirmed=True,
            capital_resonance="strong",
            catalyst_strength="strong",
            risk_reward_ratio="favorable",
            entry_quality="good",
            data_completeness=50,
        )
        assert score <= 70

    def test_unknown_position_cap_70(self):
        score = calculate_opportunity_score(
            trend_confirmed=True,
            capital_resonance="strong",
            catalyst_strength="strong",
            risk_reward_ratio="favorable",
            entry_quality="good",
            position_status="unknown",
        )
        assert score <= 70

    def test_multiple_caps_take_lowest(self):
        score = calculate_opportunity_score(
            trend_confirmed=True,
            capital_resonance="strong",
            catalyst_strength="strong",
            risk_reward_ratio="favorable",
            entry_quality="good",
            gate_passed=False,          # cap 60
            trader_disallows_buy=True,  # cap 50
            position_status="unknown",  # cap 70
        )
        assert score <= 50

    def test_all_conditions_good_no_cap(self):
        score = calculate_opportunity_score(
            trend_confirmed=True,
            capital_resonance="strong",
            catalyst_strength="strong",
            risk_reward_ratio="favorable",
            entry_quality="good",
            gate_passed=True,
            final_direction_bearish=False,
            trader_disallows_buy=False,
            data_completeness=100,
            position_status="has_position",
        )
        assert score == 100


# ═══════════════════════════════════════════════════════════════
# P1-4: 未持仓语义
# ═══════════════════════════════════════════════════════════════

class TestFix4NoPositionSemantics:
    """未持仓时，减仓/清仓/止损/HOLD 等持仓动作被替换。"""

    def test_reduce_actions_replaced(self):
        text = "建议减仓，止损离场。"
        gate = {"passed": True, "failures": []}
        result, changes = sanitize_forbidden_strong_actions(
            text, gate, "no_position", 1, 1
        )
        assert "该持仓动作不适用，保持观察" in result
        assert len(changes) > 0

    def test_hold_replaced_with_wait(self):
        # Standalone HOLD without wait/observe/conditional context gets replaced
        # Note: \b word boundary requires HOLD to be at word boundary (e.g. start of text)
        text = "HOLD"
        gate = {"passed": True, "failures": []}
        result, _ = sanitize_forbidden_strong_actions(
            text, gate, "no_position", 1, 1
        )
        assert "WAIT" in result or "观察" in result

    def test_hold_slash_wait_replaced(self):
        # HOLD/等待触发 → WAIT/等待触发
        text = "HOLD/等待触发"
        gate = {"passed": True, "failures": []}
        result, _ = sanitize_forbidden_strong_actions(
            text, gate, "no_position", 1, 1
        )
        assert "WAIT" in result

    def test_has_position_preserves_actions(self):
        text = "建议减仓，止损离场。"
        gate = {"passed": True, "failures": []}
        result, changes = sanitize_forbidden_strong_actions(
            text, gate, "has_position", 3, 3
        )
        # Should NOT replace for has_position
        assert "该持仓动作不适用" not in result

    def test_stop_loss_field_name_preserved(self):
        """止损价/止损位等字段名不应被替换。"""
        text = "止损价：36.00元，建议减仓。"
        gate = {"passed": True, "failures": []}
        result, _ = sanitize_forbidden_strong_actions(
            text, gate, "no_position", 1, 1
        )
        assert "止损价" in result
        assert "36.00" in result

    def test_format_execution_block_no_position(self):
        from tradingagents.agents.utils.readiness_score import format_execution_block
        block = format_execution_block(
            source_coverage=80,
            evidence_coverage=75,
            confidence="中",
            opportunity_score=50,
            risk_level=1,
            buy_level=1,
            position_status="no_position",
        )
        assert "WAIT" in block or "观察" in block


# ═══════════════════════════════════════════════════════════════
# P1-5: Buy/Risk Level 冲突
# ═══════════════════════════════════════════════════════════════

class TestFix5BuyRiskLevelConflict:
    """未持仓时 Buy Level 和 Risk Level 有上限约束。"""

    def test_no_position_risk_level_cap_at_1(self):
        result = calculate_risk_level(
            source_coverage=80,
            evidence_coverage=80,
            broke_support=True,
            main_capital_outflow_days=3,
            volume_breakdown=True,
            position_status="no_position",
        )
        assert result["level"] <= 1
        assert "未持仓" in result["note"]

    def test_no_position_buy_level_cap_at_1_without_entry_conditions(self):
        result = calculate_buy_level(
            source_coverage=80,
            evidence_coverage=80,
            trend_confirmed=False,
            main_capital_inflow_days=0,
            position_status="no_position",
        )
        assert result["level"] <= 1

    def test_no_position_buy_level_0_when_bearish(self):
        result = calculate_buy_level(
            source_coverage=80,
            evidence_coverage=80,
            position_status="no_position",
            research_bearish=True,
        )
        assert result["level"] == 0
        assert "偏空" in result["note"] or "禁止买入" in result["note"]

    def test_no_position_buy_level_2_when_entry_ready(self):
        result = calculate_buy_level(
            source_coverage=80,
            evidence_coverage=80,
            trend_confirmed=True,
            main_capital_inflow_days=1,
            no_execution_conflict=True,
            no_unresolved_analyst_conflict=True,
            has_major_negative_announcement=False,
            position_status="no_position",
        )
        # Entry ready: no position is still only a conditional trial.
        assert result["level"] == 2
        assert "入场条件已确认" in result["note"]

    def test_no_position_perfect_signals_never_reach_level_3(self):
        result = calculate_buy_level(
            source_coverage=100,
            evidence_coverage=100,
            trend_confirmed=True,
            main_capital_inflow_days=3,
            volume_healthy_expansion=True,
            no_execution_conflict=True,
            no_unresolved_analyst_conflict=True,
            has_major_negative_announcement=False,
            position_status="no_position",
        )
        assert result["level"] == 2
        assert "上限为 2" in result["note"]

    def test_has_position_perfect_signals_can_reach_level_4(self):
        result = calculate_buy_level(
            source_coverage=100,
            evidence_coverage=100,
            trend_confirmed=True,
            main_capital_inflow_days=3,
            volume_healthy_expansion=True,
            no_execution_conflict=True,
            no_unresolved_analyst_conflict=True,
            has_major_negative_announcement=False,
            position_status="has_position",
        )
        assert result["level"] == 4

    def test_has_position_risk_level_can_reach_4(self):
        result = calculate_risk_level(
            source_coverage=90,
            evidence_coverage=90,
            broke_support=True,
            main_capital_outflow_days=2,
            volume_breakdown=True,
            has_major_positive_announcement=False,
            position_status="has_position",
        )
        assert result["level"] == 4


# ═══════════════════════════════════════════════════════════════
# P1-6: 舆情 VERDICT 矛盾
# ═══════════════════════════════════════════════════════════════

class TestFix6SentimentVerdictContradiction:
    """VERDICT 标签提取与冲突检测。"""

    def test_extract_verdict_basic(self):
        text = '一些分析文本\n<!-- VERDICT: {"direction": "偏多", "reason": "业绩超预期"} -->'
        direction, confidence = extract_verdict(text)
        assert direction == "偏多"
        assert confidence == "中"

    def test_extract_verdict_bearish(self):
        text = '分析结论\n<!-- VERDICT: {"direction": "偏空", "reason": "业绩下滑"} -->'
        direction, _ = extract_verdict(text)
        assert direction == "偏空"

    def test_extract_verdict_neutral(self):
        text = '中性分析\n<!-- VERDICT: {"direction": "中性", "reason": "信号不明确"} -->'
        direction, _ = extract_verdict(text)
        assert direction == "中性"

    def test_no_verdict_returns_neutral(self):
        text = "普通分析文本，无VERDICT标签。"
        direction, confidence = extract_verdict(text)
        assert direction == "中性"
        assert confidence == "低"

    def test_analyst_conflict_detection_in_signals(self):
        from tradingagents.agents.utils.readiness_score import extract_execution_signals
        state = {}
        reports = {"market_report": "分析师存在分歧，多空双方对立。"}
        signals = extract_execution_signals(state, "", reports)
        assert signals["no_unresolved_analyst_conflict"] is False


# ═══════════════════════════════════════════════════════════════
# P1-7: 入场区间误识别
# ═══════════════════════════════════════════════════════════════

class TestFix7EntryZoneMisidentification:
    """入场区间只接受明确入场标签，排除震荡/观察/阻力区间。"""

    def test_explicit_entry_range_extracted(self):
        text = "入场区间：35.50-36.50元"
        result = _find_price_range(text)
        assert result == "35.50-36.50"

    def test_buy_range_extracted(self):
        text = "买入区间：28.00-30.00元"
        result = _find_price_range(text)
        assert result == "28.00-30.00"

    def test_no_entry_statement_returns_none(self):
        text = "不建议入场，当前估值偏高。"
        result = _find_price_range(text)
        assert result is None

    def test_no_entry_zone_statement_returns_none(self):
        text = "无入场区间，暂观望。"
        result = _find_price_range(text)
        assert result is None

    def test_oscillation_range_not_treated_as_entry(self):
        """震荡区间不应被误识别为入场区间。"""
        text = "股价处于35.00-38.00元震荡区间，观察等待方向。"
        result = _find_price_range(text)
        # 震荡区间 without entry labels should return None
        assert result is None

    def test_support_zone_not_treated_as_entry(self):
        text = "支撑区：30.00元附近，阻力区：40.00元附近。"
        result = _find_price_range(text)
        assert result is None

    def test_entry_condition_with_range(self):
        text = "入场条件：股价回调至35.50-36.50元区间可以买入"
        result = _find_price_range(text)
        assert result is not None
        assert "35" in result

    def test_inline_entry_pattern(self):
        text = "建议在35.50至36.50元区间建仓"
        result = _find_price_range(text)
        assert result is not None

    def test_priority_final_range(self):
        text = (
            "初步入场区间：35.00-38.00元\n"
            "最终有效入场区间：36.00-37.00元"
        )
        result = _find_price_range(text)
        assert result == "36.00-37.00"


# ═══════════════════════════════════════════════════════════════
# P2-8: 打法标签
# ═══════════════════════════════════════════════════════════════

class TestFix8SetupTypeTags:
    """打法标签从事实事件派生，不从看多模板生成。"""

    def test_performance_pressure_tag(self):
        text = "公司业绩不及预期，净利润大幅下降。"
        tag = _setup_type(text)
        assert "业绩承压" in tag

    def test_performance_beat_tag(self):
        text = "公司业绩超预期，净利润大幅增长。"
        tag = _setup_type(text)
        assert "业绩超预期" in tag

    def test_theme_expectation_tag(self):
        text = "受AI算力政策利好，国产替代板块受益。"
        tag = _setup_type(text)
        assert "题材预期" in tag

    def test_event_driven_tag(self):
        text = "公司公告回购计划，解禁在即。"
        tag = _setup_type(text)
        assert "事件驱动" in tag

    def test_volume_price_tag(self):
        text = "缩量下跌后放量反弹，主力吸筹迹象明显。"
        tag = _setup_type(text)
        assert "量价低吸" in tag

    def test_trend_breakout_tag(self):
        text = "股价突破新高，均线多头排列。"
        tag = _setup_type(text)
        assert "趋势突破" in tag

    def test_no_match_returns_unidentified(self):
        text = "普通分析文本，无特定事件。"
        tag = _setup_type(text)
        assert tag == "未识别"

    def test_max_three_tags(self):
        text = "业绩超预期，AI算力政策利好，解禁公告，缩量下跌放量反弹，突破新高，持仓成本附近。"
        tag = _setup_type(text)
        assert len(tag.split(" + ")) <= 3

    def test_performance_direction_unclear(self):
        text = "公司业绩同比略有变化，净利润和营收数据需关注。"
        tag = _setup_type(text)
        assert "业绩关注" in tag

    def test_quality_check_includes_setup_type(self):
        check = build_trade_quality_check(
            investment_plan="公司业绩超预期，AI政策利好。",
            trader_plan="入场区间：35-38元，止损价：33元。",
            final_decision="建议条件买入。",
        )
        assert check["setup_type"] != "未识别"
        formatted = format_trade_quality_check(check)
        assert "打法标签" in formatted


# ═══════════════════════════════════════════════════════════════
# P2-9: A股做空措辞
# ═══════════════════════════════════════════════════════════════

class TestFix9ShortSellingWording:
    """A股默认不能做空，做空措辞应被替换为适当表达。"""

    def test_short_strategy_replaced(self):
        text = "建议采用做空策略。"
        result, changes = _sanitize_short_selling_text(text)
        assert "做空策略" not in result
        assert "看空信号" in result
        assert len(changes) > 0

    def test_short_open_replaced(self):
        text = "空头开仓信号出现。"
        result, _ = _sanitize_short_selling_text(text)
        assert "空头开仓" not in result
        assert "离场信号" in result

    def test_consider_short_replaced(self):
        text = "考虑做空。"
        result, _ = _sanitize_short_selling_text(text)
        assert "做空" not in result

    def test_suggest_short_replaced(self):
        text = "建议做空该股。"
        result, _ = _sanitize_short_selling_text(text)
        assert "建议做空" not in result

    def test_can_short_replaced(self):
        text = "可以做空。"
        result, _ = _sanitize_short_selling_text(text)
        assert "可以做空" not in result

    def test_no_short_selling_no_changes(self):
        text = "建议观望，等待确认信号。"
        result, changes = _sanitize_short_selling_text(text)
        assert result == text
        assert len(changes) == 0

    def test_risk_manager_integrates_short_filter(self):
        """验证 risk_manager 集成了做空措辞过滤。"""
        from tradingagents.agents.managers.risk_manager import create_risk_manager
        import asyncio
        from unittest.mock import MagicMock

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
        node = create_risk_manager(_FakeLLM(), _Memory())
        result = asyncio.run(node(state))
        # 做空策略 should have been replaced
        assert "做空策略" not in result["final_trade_decision"]
        assert "看空信号" in result["final_trade_decision"]


# ═══════════════════════════════════════════════════════════════
# P2-10: Evidence Coverage 保守化
# ═══════════════════════════════════════════════════════════════

class TestFix10EvidenceCoverageConservative:
    """Evidence Coverage 加权评分：HAS_DATA=1.0, NORMAL_NO_DATA=0.5。"""

    def test_all_has_data_gives_100(self):
        cov = calculate_evidence_coverage(
            ohlcv_5d=EvidenceStatus.HAS_DATA,
            volume=EvidenceStatus.HAS_DATA,
            turnover_rate=EvidenceStatus.HAS_DATA,
            volume_ratio=EvidenceStatus.HAS_DATA,
            individual_fund_flow=EvidenceStatus.HAS_DATA,
            lhb_status=EvidenceStatus.HAS_DATA,
            margin_trading=EvidenceStatus.HAS_DATA,
            announcements=EvidenceStatus.HAS_DATA,
            research_report=EvidenceStatus.HAS_DATA,
        )
        assert cov == 100

    def test_normal_no_data_counts_half(self):
        """NORMAL_NO_DATA 不应算作完整覆盖。"""
        cov_all_has = calculate_evidence_coverage(
            ohlcv_5d=EvidenceStatus.HAS_DATA,
            volume=EvidenceStatus.HAS_DATA,
            turnover_rate=EvidenceStatus.HAS_DATA,
            volume_ratio=EvidenceStatus.HAS_DATA,
            individual_fund_flow=EvidenceStatus.HAS_DATA,
            lhb_status=EvidenceStatus.HAS_DATA,
            margin_trading=EvidenceStatus.HAS_DATA,
            announcements=EvidenceStatus.HAS_DATA,
            research_report=EvidenceStatus.HAS_DATA,
        )
        cov_mixed = calculate_evidence_coverage(
            ohlcv_5d=EvidenceStatus.HAS_DATA,
            volume=EvidenceStatus.HAS_DATA,
            turnover_rate=EvidenceStatus.HAS_DATA,
            volume_ratio=EvidenceStatus.HAS_DATA,
            individual_fund_flow=EvidenceStatus.HAS_DATA,
            lhb_status=EvidenceStatus.HAS_DATA,
            margin_trading=EvidenceStatus.HAS_DATA,
            announcements=EvidenceStatus.HAS_DATA,
            research_report=EvidenceStatus.NORMAL_NO_DATA,
        )
        assert cov_mixed < cov_all_has
        # 8 HAS_DATA * 1.0 + 1 NORMAL_NO_DATA * 0.5 = 8.5 / 9 ≈ 94
        assert cov_mixed == 94

    def test_all_normal_no_data_gives_50(self):
        cov = calculate_evidence_coverage(
            ohlcv_5d=EvidenceStatus.NORMAL_NO_DATA,
            volume=EvidenceStatus.NORMAL_NO_DATA,
            turnover_rate=EvidenceStatus.NORMAL_NO_DATA,
            volume_ratio=EvidenceStatus.NORMAL_NO_DATA,
            individual_fund_flow=EvidenceStatus.NORMAL_NO_DATA,
            lhb_status=EvidenceStatus.NORMAL_NO_DATA,
            margin_trading=EvidenceStatus.NORMAL_NO_DATA,
            announcements=EvidenceStatus.NORMAL_NO_DATA,
            research_report=EvidenceStatus.NORMAL_NO_DATA,
        )
        assert cov == 50

    def test_not_available_excluded_from_denominator(self):
        cov_with_ratings = calculate_evidence_coverage(
            ohlcv_5d=EvidenceStatus.HAS_DATA,
            volume=EvidenceStatus.HAS_DATA,
            turnover_rate=EvidenceStatus.HAS_DATA,
            volume_ratio=EvidenceStatus.HAS_DATA,
            individual_fund_flow=EvidenceStatus.HAS_DATA,
            lhb_status=EvidenceStatus.HAS_DATA,
            margin_trading=EvidenceStatus.HAS_DATA,
            announcements=EvidenceStatus.HAS_DATA,
            research_report=EvidenceStatus.HAS_DATA,
            ratings=EvidenceStatus.NOT_AVAILABLE,
            buybacks=EvidenceStatus.NOT_AVAILABLE,
        )
        assert cov_with_ratings == 100

    def test_low_coverage_caps_confidence(self):
        """evidence_coverage < 70 时置信度不能为 HIGH。"""
        conf = assess_confidence(
            data_completeness=80,
            evidence_coverage=50,
        )
        assert conf != ConfidenceLevel.HIGH

    def test_query_failed_counts_zero(self):
        cov = calculate_evidence_coverage(
            ohlcv_5d=EvidenceStatus.HAS_DATA,
            volume=EvidenceStatus.QUERY_FAILED,
            turnover_rate=EvidenceStatus.QUERY_FAILED,
            volume_ratio=EvidenceStatus.QUERY_FAILED,
            individual_fund_flow=EvidenceStatus.QUERY_FAILED,
            lhb_status=EvidenceStatus.QUERY_FAILED,
            margin_trading=EvidenceStatus.QUERY_FAILED,
            announcements=EvidenceStatus.QUERY_FAILED,
            research_report=EvidenceStatus.QUERY_FAILED,
        )
        # 1 * 1.0 + 8 * 0 = 1/9 ≈ 11
        assert cov < 20

    def test_gate_fails_below_70(self):
        gate = get_strong_action_gate(
            source_coverage=80,
            evidence_coverage=50,
        )
        assert gate["passed"] is False
        assert any("evidence_coverage" in f for f in gate["failures"])


# ═══════════════════════════════════════════════════════════════
# 综合集成测试
# ═══════════════════════════════════════════════════════════════

class TestF001Integration:
    """端到端验证：多个修复协同工作。"""

    def test_full_quality_check_output(self):
        """验证执行质检区块包含所有必要字段。"""
        check = build_trade_quality_check(
            investment_plan="公司业绩承压，AI算力政策利好，解禁公告。",
            trader_plan="入场区间：35.50-36.50元，止损价：33.00元，触发价：37.00元。",
            final_decision="条件触发买入，跌破33元止损。",
            user_context={"current_position": 0, "constraints": []},
        )
        formatted = format_trade_quality_check(check)

        assert "打法标签" in formatted
        assert "入场区间" in formatted
        assert "止损价" in formatted
        assert "触发价" in formatted
        assert "执行质检" in formatted
        assert check["entry_range"] is not None
        assert check["stop_loss_price"] is not None

    def test_position_status_detection(self):
        assert get_position_status({"current_position": 100}) == "has_position"
        assert get_position_status({"current_position": 0}) == "no_position"
        assert get_position_status({}) == "unknown"
