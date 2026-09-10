"""DECISION-001: Final action semantic layering tests.

Covers 6 key scenarios where HOLD was previously overused, now differentiated
into structured research_direction / execution_action / action_label.
"""

import pytest
from types import SimpleNamespace
from tradingagents.graph.signal_processing import (
    _extract_decision_semantics,
    _derive_action_label,
    _extract_decision_keyword,
    _execution_layer_overrides_hold,
)


class TestDecisionSemanticsSixScenarios:
    """6 core scenarios from DECISION-001 spec."""

    def test_no_position_bullish_no_trigger(self):
        text = (
            "<!-- VERDICT: {\"direction\": \"偏多\"} -->\n"
            "### 建议\n"
            "偏多方向，但当前价格尚未到理想买点，建议等待。\n"
            "系统动作：等待触发\n"
        )
        result = _extract_decision_semantics(text, has_position=False, trigger_price=None)
        assert result.research_direction == "偏多"
        assert result.execution_action == "WAIT"
        assert result.action_label == "等待触发"

    def test_no_position_strong_bullish_with_trigger(self):
        text = (
            "<!-- VERDICT: {\"direction\": \"看多\"} -->\n"
            "### 建议\n"
            "看多方向，放量突破50.00元可建仓。\n"
        )
        result = _extract_decision_semantics(text, has_position=False, trigger_price=50.0)
        assert result.research_direction == "看多"
        assert result.execution_action == "ENTER"
        assert result.action_label == "条件入场"

    def test_no_position_bearish(self):
        text = (
            "<!-- VERDICT: {\"direction\": \"偏空\"} -->\n"
            "### 建议\n"
            "偏空方向，不建议介入。\n"
        )
        result = _extract_decision_semantics(text, has_position=False)
        assert result.research_direction == "偏空"
        assert result.execution_action == "WAIT"
        assert result.action_label == "回避"

    def test_has_position_neutral(self):
        text = (
            "<!-- VERDICT: {\"direction\": \"中性\"} -->\n"
            "### 建议\n"
            "中性方向，已持仓建议继续持有。\n"
        )
        result = _extract_decision_semantics(text, has_position=True)
        assert result.research_direction == "中性"
        assert result.execution_action == "HOLD"
        assert result.action_label == "持有"

    def test_has_position_risk_rising(self):
        text = (
            "<!-- VERDICT: {\"direction\": \"偏空\"} -->\n"
            "### 建议\n"
            "风险升高，建议减仓至半仓，止损价31.00元。\n"
        )
        result = _extract_decision_semantics(text, has_position=True, trigger_price=31.0)
        assert result.research_direction == "偏空"
        assert result.execution_action == "REDUCE"
        assert result.action_label == "条件减仓"

    def test_data_insufficient(self):
        text = (
            "<!-- VERDICT: {\"direction\": \"中性\"} -->\n"
            "### 建议\n"
            "数据不足，无法给出明确判断，建议继续观察。\n"
        )
        result = _extract_decision_semantics(text, has_position=None)
        assert result.research_direction == "中性"
        assert result.execution_action == "WAIT"
        assert result.action_label == "数据不足观察"

    def test_data_insufficient_bullish_keeps_waiting_trigger_label(self):
        text = (
            "<!-- VERDICT: {\"direction\": \"偏多\"} -->\n"
            "### 执行质检\n"
            "- 系统动作：等待触发\n"
            "- 数据完整度：75%\n"
            "- Evidence Coverage：57%\n"
            "⚠️ 数据完整度不足，中线判断仅供参考\n"
        )
        result = _extract_decision_semantics(text, has_position=False)
        assert result.research_direction == "偏多"
        assert result.execution_action == "WAIT"
        assert result.action_label == "等待触发"

    def test_data_insufficient_bearish_keeps_avoid_label(self):
        text = (
            "<!-- VERDICT: {\"direction\": \"偏空\"} -->\n"
            "### 执行质检\n"
            "- 系统动作：等待触发\n"
            "- Evidence Coverage：57%\n"
            "⚠️ 数据完整度不足，中线判断仅供参考\n"
        )
        result = _extract_decision_semantics(text, has_position=False)
        assert result.research_direction == "偏空"
        assert result.execution_action == "WAIT"
        assert result.action_label == "回避"


class TestDeriveActionLabel:
    """Unit tests for _derive_action_label."""

    def test_wait_bullish_no_trigger(self):
        label = _derive_action_label(False, "偏多", "WAIT")
        assert label == "等待触发"

    def test_enter_with_trigger(self):
        label = _derive_action_label(False, "看多", "ENTER", trigger_price=50.0)
        assert label == "条件入场"

    def test_wait_bearish_no_position(self):
        label = _derive_action_label(False, "偏空", "WAIT")
        assert label == "回避"

    def test_hold_with_position(self):
        label = _derive_action_label(True, "中性", "HOLD")
        assert label == "持有"

    def test_reduce_with_position(self):
        label = _derive_action_label(True, "偏空", "REDUCE")
        assert label == "条件减仓"

    def test_exit_with_position(self):
        label = _derive_action_label(True, "看空", "EXIT")
        assert label == "条件清仓"


class TestExtractDecisionKeywordBackwardCompat:
    """Verify _extract_decision_keyword still works after refactor."""

    def test_verdict_buy(self):
        text = '<!-- VERDICT: {"direction": "看多"} -->\n建议买入'
        assert _extract_decision_keyword(text) == "BUY"

    def test_verdict_sell(self):
        text = '<!-- VERDICT: {"direction": "看空"} -->\n建议卖出'
        assert _extract_decision_keyword(text) == "SELL"

    def test_verdict_hold(self):
        text = '<!-- VERDICT: {"direction": "中性"} -->\n建议持有'
        assert _extract_decision_keyword(text) == "HOLD"

    def test_gate_failed_returns_hold(self):
        text = (
            '<!-- VERDICT: {"direction": "看多"} -->\n'
            "- Strong Action Gate：未通过\n"
        )
        assert _extract_decision_keyword(text) == "HOLD"


class TestExecutionLayerOverridesBackwardCompat:
    """Verify _execution_layer_overrides_hold still returns bool."""

    def test_gate_failed(self):
        text = "- Strong Action Gate：未通过\n"
        assert _execution_layer_overrides_hold(text) is True

    def test_no_override(self):
        text = "<!-- VERDICT: {\"direction\": \"中性\"} -->\n正常分析"
        assert _execution_layer_overrides_hold(text) is False


class TestExtractDecisionSemanticsGateBlocked:
    """When gate blocks, execution_action is WAIT regardless of direction."""

    def test_gate_blocked_bullish_becomes_wait(self):
        text = (
            "<!-- VERDICT: {\"direction\": \"看多\"} -->\n"
            "### 执行等级与证据门禁\n"
            "- Strong Action Gate：未通过\n"
        )
        result = _extract_decision_semantics(text, has_position=False, trigger_price=50.0)
        assert result.execution_action == "WAIT"
        assert result.decision == "HOLD"

    def test_uses_last_verdict_for_multi_agent_report(self):
        text = (
            "<!-- VERDICT: {\"direction\": \"偏多\", \"reason\": \"技术分析师早期结论\"} -->\n"
            "中间分析内容\n"
            "<!-- VERDICT: {\"direction\": \"看空\", \"reason\": \"最终风控结论\"} -->\n"
            "### 执行质检\n"
            "- 系统动作：等待触发\n"
            "- Evidence Coverage：57%\n"
        )
        result = _extract_decision_semantics(text, has_position=False)
        assert result.research_direction == "看空"
        assert result.execution_action == "WAIT"
        assert result.action_label == "回避"

    def test_ignores_historical_verdict_after_quality_section(self):
        text = (
            "<!-- VERDICT: {\"direction\": \"看空\", \"reason\": \"最终风控结论\"} -->\n"
            "### 执行质检\n"
            "- 系统动作：等待触发\n"
            "⚠️ [C-005] 同股票结论翻转警告\n"
            "上一版结论：<!-- VERDICT: {\"direction\": \"看多\", \"reason\": \"历史旧结论\"} -->\n"
        )
        result = _extract_decision_semantics(text, has_position=False)
        assert result.research_direction == "看空"
        assert result.execution_action == "WAIT"
        assert result.action_label == "回避"

    def test_without_current_verdict_does_not_use_historical_verdict_after_quality_section(self):
        text = (
            "最终建议：观望，等待更明确的量价确认。\n"
            "### 执行质检\n"
            "- 系统动作：等待触发\n"
            "⚠️ [C-005] 同股票结论翻转警告\n"
            "上一版结论：<!-- VERDICT: {\"direction\": \"看多\", \"reason\": \"历史旧结论\"} -->\n"
        )
        result = _extract_decision_semantics(text, has_position=False)
        assert result.research_direction == "中性"
        assert result.execution_action == "WAIT"
        assert result.action_label == "观望"


class TestC001OverrideNoise:
    """C-001 auto-conversion line should not pollute research direction."""

    def test_c001_clearance_in_suggestion_hold(self):
        text = (
            "建议持有。假设约25.30元\n\n"
            "### 执行质检\n"
            "- 系统动作：人工复核\n"
            "- 触发价：—\n\n"
            "⚠️ [C-001] 未持仓状态，已将减仓/清仓建议自动转换为观望（WAIT）。"
        )
        result = _extract_decision_semantics(text, has_position=False)
        assert result.research_direction == "中性"
        assert result.execution_action == "WAIT"
        assert result.action_label == "观望"


class TestGeneratedExecutionSummaryNoise:
    """Application-owned summary labels must not alter research direction."""

    marker = "<!-- TA_SYSTEM_DIAGNOSTICS_START -->\n"

    def test_application_summary_after_quality_section_does_not_create_direction(self):
        text = (
            "研究团队没有形成明确方向。\n\n"
            + self.marker
            +
            "### 执行质检\n"
            "- Strong Action Gate：未通过\n\n"
            "### 系统执行结论\n"
            "- 系统动作：WAIT/观察\n"
            "- 强动作门禁：未通过\n"
            "- Buy Level：0\n"
            "- Risk Level：1\n"
        )

        result = _extract_decision_semantics(text, has_position=False)

        assert result.research_direction == "中性"
        assert result.execution_action == "WAIT"

    def test_generated_holding_risk_action_controls_structured_semantics(self):
        text = (
            "最终结论：中性观望。\n\n"
            + self.marker
            +
            "### 执行质检\n"
            "- 系统动作：可执行但需核对盘面\n\n"
            "### 系统执行结论\n"
            "- 系统动作：触发止损\n"
            "- 强动作门禁：通过\n"
            "- Buy Level：1\n"
            "- Risk Level：3\n"
        )

        result = _extract_decision_semantics(text, has_position=True)

        assert result.research_direction == "中性"
        assert result.execution_action == "EXIT"
        assert result.action_label == "触发止损"
        assert result.decision == "SELL"
        assert _extract_decision_keyword(text, has_position=True) == "SELL"

    def test_generated_low_risk_hold_overrides_model_buy_text(self):
        text = (
            "最终建议：买入。\n\n"
            + self.marker
            + "### 执行质检\n"
            "- 系统动作：可执行但需核对盘面\n\n"
            "### 系统执行结论\n"
            "- 系统动作：HOLD/持有\n"
            "- 强动作门禁：通过\n"
            "- Buy Level：1\n"
            "- Risk Level：0\n"
        )

        result = _extract_decision_semantics(text, has_position=True)

        assert result.execution_action == "HOLD"
        assert result.action_label == "持有"
        assert result.decision == "HOLD"
        assert _extract_decision_keyword(text, has_position=True) == "HOLD"

    def test_generated_conditional_entry_controls_structured_semantics(self):
        text = (
            "最终建议：满足触发条件后试仓。\n\n"
            + self.marker
            + "### 执行质检\n"
            "- 系统动作：等待触发\n\n"
            "### 系统执行结论\n"
            "- 系统动作：ENTER/条件入场\n"
            "- 强动作门禁：通过\n"
            "- Buy Level：2\n"
            "- Risk Level：1\n"
        )

        result = _extract_decision_semantics(text, has_position=False)

        assert result.execution_action == "ENTER"
        assert result.action_label == "条件入场"
        assert result.decision == "BUY"
        assert _extract_decision_keyword(text, has_position=False) == "BUY"

    def test_last_generated_summary_wins_over_earlier_similar_heading(self):
        text = (
            "### 系统执行结论\n"
            "- 系统动作：买入\n\n"
            + self.marker
            +
            "### 执行质检\n"
            "- 系统动作：等待触发\n\n"
            "### 系统执行结论\n"
            "- 系统动作：WAIT/观察\n"
            "- 强动作门禁：未通过\n"
            "- Buy Level：0\n"
            "- Risk Level：1\n"
        )

        result = _extract_decision_semantics(text, has_position=False)

        assert result.execution_action == "WAIT"
        assert result.action_label == "观望"
        assert _extract_decision_keyword(text, has_position=False) == "HOLD"

    def test_truncated_c005_marker_copy_cannot_hide_generated_action(self):
        text = (
            "最终建议：满足触发条件后买入。\n\n"
            + self.marker
            + "### 执行质检\n"
            "- 系统动作：等待触发\n\n"
            "⚠️ [C-005] 同股票结论翻转警告\n"
            "上一版结论：旧文末尾"
            + self.marker
            + "截断的审计片段，没有完整质检段。\n\n"
            "### 系统执行结论\n"
            "- 系统动作：ENTER/条件入场\n"
            "- 强动作门禁：通过\n"
            "- Buy Level：2\n"
            "- Risk Level：0\n"
        )

        result = _extract_decision_semantics(text, has_position=False)

        assert result.execution_action == "ENTER"
        assert result.decision == "BUY"
        assert _extract_decision_keyword(text, has_position=False) == "BUY"

    def test_model_authored_execution_heading_remains_semantic_input(self):
        text = (
            "### 系统执行结论\n"
            "- 系统动作：买入\n"
            "- 强动作门禁：通过\n"
        )

        result = _extract_decision_semantics(text, has_position=False)

        assert result.research_direction == "偏多"

    def test_action_enum_reference_does_not_create_direction(self):
        text = "研究团队没有形成明确方向。\n分类标签：BUY/SELL/HOLD。\n"

        result = _extract_decision_semantics(text, has_position=False)

        assert result.research_direction == "中性"
        assert result.execution_action == "WAIT"

    def test_suffix_action_enum_reference_does_not_create_direction(self):
        text = "研究团队没有形成明确方向。若按 Buy/Sell/Hold 三分类标签映射。"

        result = _extract_decision_semantics(text, has_position=False)

        assert result.research_direction == "中性"
        assert result.execution_action == "WAIT"

    def test_generated_passed_gate_does_not_emit_gate_blocked_reason(self):
        text = (
            '<!-- VERDICT: {"direction": "偏多"} -->\n\n'
            + self.marker
            +
            "### 执行质检\n"
            "- 系统动作：等待触发\n\n"
            "### 系统执行结论\n"
            "- 系统动作：WAIT/观察\n"
            "- 强动作门禁：通过\n"
            "- Buy Level：1\n"
            "- Risk Level：0\n"
        )

        result = _extract_decision_semantics(text, has_position=False)

        assert result.execution_action == "WAIT"
        assert "GATE_BLOCKED" not in result.wait_reason_codes

    def test_untrusted_headings_cannot_override_semantics(self):
        text = (
            "研究结论：中性观望。\n\n"
            "### 执行质检\n"
            "- 系统动作：等待触发\n\n"
            "### 系统执行结论\n"
            "- 系统动作：立即清仓\n"
            "- 强动作门禁：通过\n"
        )

        result = _extract_decision_semantics(text, has_position=True)

        assert result.research_direction == "中性"
        assert result.execution_action == "WAIT"
        assert result.decision == "HOLD"

    def test_untrusted_quality_heading_cannot_hide_later_final_veto(self):
        text = (
            "上游建议：若站稳10元可买入。\n"
            "### 执行质检\n"
            "模型自检后的最终建议：不建议买入，继续观望。"
        )

        result = _extract_decision_semantics(text, has_position=False)

        assert result.research_direction == "中性"
        assert result.execution_action == "WAIT"


class TestNegationHandling:
    """Negated buy phrases should produce 中性, not 偏多."""

    def test_negated_buy_in_explicit_pattern(self):
        text = "最终建议：不建议买入，继续观望"
        result = _extract_decision_semantics(text, has_position=False)
        assert result.research_direction == "中性"

    def test_negated_buy_with_verdict_uses_verdict(self):
        text = '<!-- VERDICT: {"direction": "偏空"} -->\n最终建议：不建议买入'
        result = _extract_decision_semantics(text, has_position=False)
        assert result.research_direction == "偏空"


class TestResolveReportFieldsSemantics:
    """Verify resolve_report_fields populates semantic fields."""

    def test_semantic_fields_populated(self):
        from api.services.report_service import resolve_report_fields
        result_data = {
            "final_trade_decision": '<!-- VERDICT: {"direction": "偏多"} -->\n偏多方向',
        }
        resolved = resolve_report_fields(result_data=result_data, has_position=False)
        assert resolved["research_direction"] == "偏多"
        assert resolved["execution_action"] == "WAIT"
        assert resolved["action_label"] == "等待触发"

    def test_preserves_position_aware_semantics_from_result_data(self):
        from api.services.report_service import resolve_report_fields

        result_data = {
            "final_trade_decision": '<!-- VERDICT: {"direction": "看多"} -->\n放量突破触发价后条件入场',
            "research_direction": "看多",
            "execution_action": "ENTER",
            "action_label": "条件入场",
        }

        resolved = resolve_report_fields(result_data=result_data)

        assert resolved["research_direction"] == "看多"
        assert resolved["execution_action"] == "ENTER"
        assert resolved["action_label"] == "条件入场"

    def test_semantic_fields_none_without_final_trade_decision(self):
        from api.services.report_service import resolve_report_fields
        resolved = resolve_report_fields(result_data={})
        assert resolved["research_direction"] is None
        assert resolved["execution_action"] is None
        assert resolved["action_label"] is None


class TestNormalizeReportActionLabel:
    """Old persisted reports with directional WAIT should not display as generic data-insufficient watch."""

    def test_bearish_wait_stale_data_insufficient_becomes_avoid(self):
        from api.services.report_service import normalize_report_action_label

        report = SimpleNamespace(
            research_direction="看空",
            execution_action="WAIT",
            action_label="数据不足观察",
        )
        normalize_report_action_label(report)
        assert report.action_label == "回避"

    def test_result_data_stale_label_is_normalized_for_detail_card(self):
        from api.services.report_service import normalize_report_action_label

        report = SimpleNamespace(
            research_direction="偏多",
            execution_action="WAIT",
            action_label="数据不足观察",
            result_data={
                "research_direction": "偏多",
                "execution_action": "WAIT",
                "action_label": "数据不足观察",
            },
        )
        normalize_report_action_label(report)
        assert report.action_label == "等待触发"
        assert report.result_data["action_label"] == "等待触发"

    def test_summary_normalization_does_not_lazy_load_result_data(self):
        from sqlalchemy import create_engine, event
        from sqlalchemy.orm import sessionmaker, load_only
        from api.database import Base, ReportDB
        from api.services.report_service import REPORT_SUMMARY_COLUMNS, normalize_report_action_label

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine)
        db = SessionLocal()
        try:
            db.add(ReportDB(
                id="r1",
                user_id="u1",
                symbol="600519.SH",
                trade_date="2026-06-08",
                status="completed",
                research_direction="偏空",
                execution_action="WAIT",
                action_label="数据不足观察",
                result_data={"action_label": "数据不足观察"},
            ))
            db.commit()
            report = db.query(ReportDB).options(load_only(*REPORT_SUMMARY_COLUMNS)).first()
            query_count = {"n": 0}

            def _count_queries(*_args, **_kwargs):
                query_count["n"] += 1

            event.listen(engine, "before_cursor_execute", _count_queries)
            try:
                normalize_report_action_label(report)
            finally:
                event.remove(engine, "before_cursor_execute", _count_queries)

            assert query_count["n"] == 0
            assert report.action_label == "回避"
        finally:
            db.close()

    def test_bullish_wait_stale_data_insufficient_becomes_waiting_trigger(self):
        from api.services.report_service import normalize_report_action_label

        report = SimpleNamespace(
            research_direction="偏多",
            execution_action="WAIT",
            action_label="数据不足观察",
        )
        normalize_report_action_label(report)
        assert report.action_label == "等待触发"

    def test_neutral_data_insufficient_kept(self):
        from api.services.report_service import normalize_report_action_label

        report = SimpleNamespace(
            research_direction="中性",
            execution_action="WAIT",
            action_label="数据不足观察",
        )
        normalize_report_action_label(report)
        assert report.action_label == "数据不足观察"


class TestC001R1HoldWaitConversion:
    """[C-001-R1] 空仓 HOLD 经门禁警告转 WAIT；已持仓 HOLD 保留。

    门禁产出 [C-001] 警告后，结构层 _has_gate_failure 命中 GATE_BLOCKED，
    execution_action 必须是 WAIT；正文带"等待"字样不得改变这一转换。
    """

    HOLD_WAIT_BODY = "建议 HOLD，等待回调后再考虑。"
    GATE_WARNING = (
        "\n\n⚠️ [C-001] 未持仓状态，以下持仓动作不适用：HOLD。"
        "已自动降级为观察（WAIT）。"
    )

    def test_flat_hold_with_wait_text_converts_to_wait(self):
        result = _extract_decision_semantics(
            self.HOLD_WAIT_BODY + self.GATE_WARNING,
            has_position=False,
        )
        assert result.execution_action == "WAIT"
        assert "GATE_BLOCKED" in result.wait_reason_codes

    def test_held_hold_with_wait_text_preserved(self):
        result = _extract_decision_semantics(
            self.HOLD_WAIT_BODY,
            has_position=True,
        )
        assert result.execution_action == "HOLD"

    def test_unknown_position_gate_warning_still_forces_wait(self):
        # 未知持仓时门警文本同样触发 GATE_BLOCKED → WAIT（保守降级）
        result = _extract_decision_semantics(
            self.HOLD_WAIT_BODY + self.GATE_WARNING,
            has_position=None,
        )
        assert result.execution_action == "WAIT"
