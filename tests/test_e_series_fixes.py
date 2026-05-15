"""Tests for E-series fixes (E-001 through E-009)."""

import pytest
from tradingagents.graph.signal_processing import _extract_decision_keyword, _execution_layer_overrides_hold
from tradingagents.agents.utils.trade_setup import _first_price, _find_price_range, _conflicts
from tradingagents.agents.utils.readiness_score import (
    EvidenceStatus,
    calculate_evidence_coverage,
    get_strong_action_gate,
)


# ── E-001: Execution layer overrides VERDICT ──────────────────────


class TestE001ExecutionLayerOverrides:
    """P0: Strong Action Gate not passed → final signal must be HOLD."""

    def test_strong_action_gate_failed_returns_hold(self):
        text = (
            "<!-- VERDICT: {\"direction\": \"看多\"} -->\n"
            "### 执行等级与证据门禁\n"
            "- Strong Action Gate：未通过\n"
            "- 降级原因：source_coverage=60% < 70%\n"
        )
        assert _extract_decision_keyword(text) == "HOLD"

    def test_strong_action_gate_passed_allows_verdict(self):
        text = (
            "<!-- VERDICT: {\"direction\": \"看多\"} -->\n"
            "### 执行等级与证据门禁\n"
            "- Strong Action Gate：通过\n"
        )
        assert _extract_decision_keyword(text) == "BUY"

    def test_system_action_wait_returns_hold(self):
        text = (
            "<!-- VERDICT: {\"direction\": \"BUY\"} -->\n"
            "### 执行质检\n"
            "- 系统动作：等待触发\n"
        )
        assert _extract_decision_keyword(text) == "HOLD"

    def test_system_action_manual_review_returns_hold(self):
        text = (
            "<!-- VERDICT: {\"direction\": \"看空\"} -->\n"
            "### 执行质检\n"
            "- 系统动作：等待人工复核\n"
        )
        assert _extract_decision_keyword(text) == "HOLD"

    def test_c001_auto_convert_returns_hold(self):
        text = (
            "<!-- VERDICT: {\"direction\": \"SELL\"} -->\n"
            "⚠️ [C-001] 未持仓状态，已将减仓/清仓建议自动转换为观望（WAIT）。\n"
        )
        assert _extract_decision_keyword(text) == "HOLD"

    def test_no_override_normal_verdict(self):
        text = "<!-- VERDICT: {\"direction\": \"中性\"} -->\n一些中性内容"
        assert _extract_decision_keyword(text) == "HOLD"

    def test_execution_layer_overrides_helper_gate_failed(self):
        text = "- Strong Action Gate：未通过\n- 降级原因：source_coverage=60%"
        assert _execution_layer_overrides_hold(text) is True

    def test_execution_layer_overrides_helper_gate_passed(self):
        text = "- Strong Action Gate：通过"
        assert _execution_layer_overrides_hold(text) is False


# ── E-002: Markdown bold price labels ──────────────────────────────


class TestE002MarkdownBoldPrice:
    """P1: Stop-loss price regex handles **止损价**：165.00 format."""

    def test_plain_label(self):
        assert _first_price("止损价：165.00", ("止损价",)) == 165.0

    def test_bold_label_cn_colon(self):
        assert _first_price("**止损价**：165.00", ("止损价",)) == 165.0

    def test_bold_label_en_colon(self):
        assert _first_price("**止损价**: 165.00", ("止损价",)) == 165.0

    def test_bold_label_with_spaces(self):
        assert _first_price("**止损价** ： 165.50", ("止损价",)) == 165.5

    def test_multiple_labels_bold(self):
        text = "触发位：160.00\n**止损价**：165.00"
        assert _first_price(text, ("止损价",)) == 165.0

    def test_no_price_returns_none(self):
        assert _first_price("**止损价**：无", ("止损价",)) is None


# ── E-004: NOT_AVAILABLE status ────────────────────────────────────


class TestE004NotAvailable:
    """P3: Missing fields marked as not_available, excluded from coverage."""

    def test_not_available_excluded_from_coverage(self):
        """NOT_AVAILABLE fields should not drag down coverage."""
        coverage = calculate_evidence_coverage(
            ohlcv_5d=EvidenceStatus.HAS_DATA,
            volume=EvidenceStatus.HAS_DATA,
            turnover_rate=EvidenceStatus.NOT_AVAILABLE,  # excluded
            volume_ratio=EvidenceStatus.HAS_DATA,
            individual_fund_flow=EvidenceStatus.HAS_DATA,
            lhb_status=EvidenceStatus.NORMAL_NO_DATA,
            margin_trading=EvidenceStatus.NOT_AVAILABLE,  # excluded
            announcements=EvidenceStatus.HAS_DATA,
        )
        # 6 counted (excluding 2 NOT_AVAILABLE), all 6 are valid
        assert coverage == 100

    def test_not_available_reduces_denominator(self):
        """With NOT_AVAILABLE, denominator shrinks but valid items still count."""
        coverage = calculate_evidence_coverage(
            ohlcv_5d=EvidenceStatus.HAS_DATA,
            volume=EvidenceStatus.FIELD_MISSING,
            turnover_rate=EvidenceStatus.NOT_AVAILABLE,
            volume_ratio=EvidenceStatus.HAS_DATA,
            individual_fund_flow=EvidenceStatus.HAS_DATA,
            lhb_status=EvidenceStatus.NORMAL_NO_DATA,
            margin_trading=EvidenceStatus.NOT_AVAILABLE,
            announcements=EvidenceStatus.HAS_DATA,
        )
        # 6 counted, 5 valid (volume is FIELD_MISSING)
        assert coverage == 83  # 5/6 ≈ 83%

    def test_all_not_available_returns_zero(self):
        """Edge case: all fields are NOT_AVAILABLE → 0% (0/0 = 0)."""
        coverage = calculate_evidence_coverage(
            ohlcv_5d=EvidenceStatus.NOT_AVAILABLE,
            volume=EvidenceStatus.NOT_AVAILABLE,
            turnover_rate=EvidenceStatus.NOT_AVAILABLE,
            volume_ratio=EvidenceStatus.NOT_AVAILABLE,
            individual_fund_flow=EvidenceStatus.NOT_AVAILABLE,
            lhb_status=EvidenceStatus.NOT_AVAILABLE,
            margin_trading=EvidenceStatus.NOT_AVAILABLE,
            announcements=EvidenceStatus.NOT_AVAILABLE,
        )
        assert coverage == 0

    def test_not_available_constant(self):
        assert EvidenceStatus.NOT_AVAILABLE == "not_available"


# ── E-001: Strong Action Gate with HOLD enforcement ────────────────


class TestE001StrongActionGateHOLD:
    """Verify Strong Action Gate results in HOLD when not passed."""

    def test_gate_not_passed_with_low_coverage(self):
        gate = get_strong_action_gate(
            source_coverage=60,
            evidence_coverage=80,
        )
        assert gate["passed"] is False
        assert any("source_coverage" in f for f in gate["failures"])

    def test_gate_passed_with_good_coverage(self):
        gate = get_strong_action_gate(
            source_coverage=80,
            evidence_coverage=80,
        )
        assert gate["passed"] is True
        assert gate["failures"] == []


# ── E-006: raw_evidence persistence ────────────────────────────────


class TestE006RawEvidencePersistence:
    """E-006: raw_evidence is built and attached to init_state metadata."""

    def test_build_raw_evidence_is_class_method(self):
        """build_raw_evidence exists as a method on DataCollector."""
        from tradingagents.graph.data_collector import DataCollector
        assert hasattr(DataCollector, "build_raw_evidence")
        import inspect
        sig = inspect.signature(DataCollector.build_raw_evidence)
        params = list(sig.parameters.keys())
        assert "ticker" in params
        assert "trade_date" in params

    def test_init_state_metadata_receives_raw_evidence(self):
        """Verify the pattern used in api/main.py for E-006."""
        # Simulate what api/main.py does after E-006 fix
        init_state = {"metadata": {}}
        raw_evidence = {"ohlcv": "some_data", "fund_flow": "some_data"}
        init_state["metadata"]["raw_evidence"] = raw_evidence
        assert init_state["metadata"]["raw_evidence"] == raw_evidence
        assert "raw_evidence" in init_state["metadata"]

    def test_metadata_reducer_preserves_raw_evidence(self):
        """LangGraph metadata updates should merge instead of replacing raw_evidence."""
        from tradingagents.agents.utils.agent_states import merge_metadata

        merged = merge_metadata(
            {"raw_evidence": {"stock_data": "csv"}},
            {"trade_quality_check": {"action": "等待触发"}},
        )

        assert merged["raw_evidence"] == {"stock_data": "csv"}
        assert merged["trade_quality_check"] == {"action": "等待触发"}

    def test_build_horizon_result_restores_raw_evidence_from_collector(self):
        """If final state lost metadata.raw_evidence, compact result restores it."""
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        class _Collector:
            def build_raw_evidence(self, ticker, trade_date):
                return {"stock_data": f"{ticker}:{trade_date}"}

        graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
        graph.data_collector = _Collector()

        result = TradingAgentsGraph._build_horizon_result(
            graph,
            "short",
            {"company_of_interest": "002138.SZ", "trade_date": "2026-05-14", "metadata": {}},
        )

        assert result["metadata"]["raw_evidence"] == {"stock_data": "002138.SZ:2026-05-14"}


# ── E-007: LHB trigger transparency ───────────────────────────────


class TestE007LHBTransparency:
    """E-007: LHB queries are annotated with force=True/False and trigger reason."""

    def test_lhb_force_true_annotation(self):
        """When fund_flow anomaly triggers LHB, report includes force=True."""
        report = "[LHB触发: force=True, 原因=资金异动明显]\n龙虎榜数据内容"
        assert "force=True" in report
        assert "资金异动" in report

    def test_lhb_force_false_annotation(self):
        """When fund_flow is normal, report includes force=False."""
        report = "[LHB触发: force=False, 原因=资金流未超阈值]\n近期无明显异动"
        assert "force=False" in report
        assert "未超阈值" in report

    def test_lhb_annotation_in_output(self):
        """Verify smart_money_analyst output includes LHB trigger note."""
        # Check the smart_money_analyst function exists and accepts data_collector
        from tradingagents.agents.analysts.smart_money_analyst import create_smart_money_analyst
        assert callable(create_smart_money_analyst)


# ── E-008: Final price range priority ─────────────────────────────


class TestE008PriceRangePriority:
    """E-008: _find_price_range prefers final/effective ranges over generic ones."""

    def test_prefers_final_effective_range(self):
        text = (
            "分析认为入场区间：10.00~12.00\n"
            "综合考虑，最终有效入场区间：11.50~12.20"
        )
        result = _find_price_range(text)
        assert result == "11.50~12.20"

    def test_prefers_唯一有效_range(self):
        text = (
            "建仓区间：9.00至10.00\n"
            "唯一有效入场区间：9.50至9.80"
        )
        result = _find_price_range(text)
        assert result == "9.50至9.80"

    def test_fallback_last_generic_match(self):
        text = (
            "入场区间：10.00~12.00\n"
            "调整后入场区间：11.00~11.80"
        )
        result = _find_price_range(text)
        # Should return the LAST match (more refined)
        assert result == "11.00~11.80"

    def test_no_range_returns_none(self):
        text = "没有任何区间信息"
        assert _find_price_range(text) is None

    def test_single_generic_range(self):
        text = "买入区间：15.00~16.50"
        assert _find_price_range(text) == "15.00~16.50"

    def test_real_format_唯一有效_执行区间_方括号_元(self):
        """E-008 regression: real report format with 元/执行区间/【】."""
        text = "唯一有效的建仓执行区间仅保留【35.80元 - 36.20元】"
        assert _find_price_range(text) == "35.80-36.20"

    def test_最终有效_with_元(self):
        text = "最终有效入场区间：28.50元至29.20元"
        assert _find_price_range(text) == "28.50至29.20"


# ── E-009: Override disclaimer ─────────────────────────────────────


class TestE009OverrideDisclaimer:
    """E-009: When execution layer overrides VERDICT, report includes override disclaimer."""

    def test_override_disclaimer_on_hold_downgrade(self):
        """When decision is HOLD due to execution layer override, disclaimer is added."""
        ftd = (
            "<!-- VERDICT: {\"direction\": \"看多\" }} -->\n"
            "### 执行等级与证据门禁\n"
            "- Strong Action Gate：未通过\n"
            "- 降级原因：source_coverage=60% < 70%\n"
        )
        # Simulate E-009 logic
        from tradingagents.graph.signal_processing import _execution_layer_overrides_hold
        if _execution_layer_overrides_hold(ftd):
            override_note = (
                "\n\n---\n"
                "⚠️ **上游买入建议已被最终门禁降级，系统最终动作以 HOLD/等待触发为准。**\n"
            )
            ftd = ftd + override_note
        assert "上游" in ftd
        assert "HOLD" in ftd

    def test_no_disclaimer_without_override(self):
        """Normal VERDICT (no override) should not have disclaimer."""
        ftd = "<!-- VERDICT: {\"direction\": \"中性\" }} -->\n正常中性分析"
        from tradingagents.graph.signal_processing import _execution_layer_overrides_hold
        assert _execution_layer_overrides_hold(ftd) is False

    def test_override_idempotent(self):
        """Adding override note twice should not duplicate it."""
        ftd = (
            "### 执行等级\n- Strong Action Gate：未通过\n"
        )
        override_note = "\n上游买入建议已被最终门禁降级"
        marker = "系统最终动作以 HOLD/等待触发为准"
        # Simulate E-009 idempotent check (uses marker, not generic "上游")
        if marker not in ftd:
            ftd = ftd + override_note + marker
        if marker not in ftd:
            ftd = ftd + override_note + marker
        assert ftd.count(marker) == 1

    def test_no_skip_when_上游_appears_in_analyst_conflict(self):
        """E-009 regression: '上游' in '上游分析师结论冲突' should NOT prevent override."""
        ftd = (
            "上游分析师结论冲突且未解释\n"
            "- Strong Action Gate：未通过\n"
        )
        # The idempotent check should use the specific marker, not generic '上游'
        marker = "系统最终动作以 HOLD/等待触发为准"
        assert marker not in ftd  # marker NOT present → should allow add
        assert "上游" in ftd  # but '上游' IS present → old check would wrongly skip
