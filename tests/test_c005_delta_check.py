"""C-005: Tests for same_symbol_delta_check.

Verifies that:
1. Direction extraction works correctly for bullish/bearish/neutral
2. Delta detection triggers on direction flip within 72h window
3. Delta detection does NOT trigger when:
   - Same direction
   - Neutral direction
   - Outside 72h window
   - No previous conclusion exists
4. New data source tracking works correctly
5. Possible noise flag is set when no new data sources
6. Action summary extraction works
7. Format warning output contains all required fields
8. Save/load conclusion round-trip works
"""

import json
import os
import tempfile
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from tradingagents.agents.utils.delta_check import (
    _extract_direction,
    _extract_action_summary,
    check_delta,
    format_delta_warning,
    load_last_conclusion,
    save_conclusion,
)


# ── _extract_direction: core direction detection ──


class TestExtractDirection:
    """方向提取测试"""

    def test_bullish_chinese_keywords(self):
        assert _extract_direction("看多，建议买入建仓") == "bullish"

    def test_bearish_chinese_keywords(self):
        assert _extract_direction("看空，建议卖出清仓") == "bearish"

    def test_bullish_english_keywords(self):
        assert _extract_direction("Recommend BUY and ENTER position") == "bullish"

    def test_bearish_english_keywords(self):
        assert _extract_direction("Recommend SELL and EXIT position") == "bearish"

    def test_trade_action_enter(self):
        assert _extract_direction("Action: ENTER") == "bullish"

    def test_trade_action_exit(self):
        assert _extract_direction("Action: EXIT") == "bearish"

    def test_trade_action_reduce(self):
        assert _extract_direction("Action: REDUCE") == "bearish"

    def test_neutral_text(self):
        assert _extract_direction("当前市场震荡，建议观望") == "neutral"

    def test_empty_text(self):
        assert _extract_direction("") == "neutral"

    def test_none_text(self):
        assert _extract_direction(None) == "neutral"

    def test_mixed_signals_bearish_dominant(self):
        """止损信号强于看多信号"""
        assert _extract_direction("虽然基本面看多，但建议止损离场") == "bearish"

    def test_mixed_signals_bullish_dominant(self):
        """强烈看多信号"""
        assert _extract_direction("强烈建议买入建仓") == "bullish"

    def test_mixed_signals_neutral(self):
        """看多看空信号强度相当"""
        result = _extract_direction("买入和卖出信号并存")
        assert result == "neutral"


# ── _extract_action_summary: action summary extraction ──


class TestExtractActionSummary:
    """动作摘要提取测试"""

    def test_enter_action(self):
        summary = _extract_action_summary("ENTER")
        assert "ENTER" in summary

    def test_exit_action(self):
        summary = _extract_action_summary("EXIT")
        assert "EXIT" in summary

    def test_chinese_buy(self):
        summary = _extract_action_summary("建议买入")
        assert "ENTER" in summary

    def test_chinese_sell(self):
        summary = _extract_action_summary("建议卖出")
        assert "EXIT" in summary

    def test_chinese_hold(self):
        summary = _extract_action_summary("建议持有")
        assert "HOLD" in summary

    def test_chinese_reduce(self):
        summary = _extract_action_summary("建议减仓")
        assert "REDUCE" in summary

    def test_chinese_wait(self):
        summary = _extract_action_summary("建议观望")
        assert "WAIT" in summary

    def test_mixed_actions(self):
        summary = _extract_action_summary("买入和卖出信号并存")
        assert "混合" in summary

    def test_no_clear_action(self):
        summary = _extract_action_summary("当前市场震荡")
        assert "无明确动作" in summary

    def test_empty_text(self):
        summary = _extract_action_summary("")
        assert "无明确动作" in summary


# ── save_conclusion / load_last_conclusion: round-trip ──


class TestSaveLoadConclusion:
    """保存/加载结论测试"""

    def test_save_and_load(self, tmp_path):
        """保存后加载，数据一致"""
        with patch("tradingagents.agents.utils.delta_check.DELTA_LOG_DIR", str(tmp_path)):
            save_conclusion("603629", "看多买入", "high", ["market", "news"])

            loaded = load_last_conclusion("603629")
            assert loaded is not None
            assert loaded["stock_code"] == "603629"
            assert loaded["conclusion"] == "看多买入"
            assert loaded["confidence"] == "high"
            assert loaded["data_sources"] == ["market", "news"]
            assert "timestamp" in loaded

    def test_load_nonexistent(self, tmp_path):
        """不存在的股票返回 None"""
        with patch("tradingagents.agents.utils.delta_check.DELTA_LOG_DIR", str(tmp_path)):
            assert load_last_conclusion("999999") is None

    def test_load_corrupted_json(self, tmp_path):
        """损坏的 JSON 文件返回 None"""
        with patch("tradingagents.agents.utils.delta_check.DELTA_LOG_DIR", str(tmp_path)):
            os.makedirs(tmp_path, exist_ok=True)
            path = tmp_path / "603629.json"
            path.write_text("not valid json", encoding="utf-8")
            assert load_last_conclusion("603629") is None


# ── check_delta: core delta detection logic ──


class TestCheckDelta:
    """结论翻转检测测试"""

    def _make_conclusion_file(self, tmp_path, stock_code, conclusion, data_sources, hours_ago=0):
        """辅助方法：创建结论文件"""
        os.makedirs(tmp_path, exist_ok=True)
        ts = datetime.now() - timedelta(hours=hours_ago)
        record = {
            "stock_code": stock_code,
            "conclusion": conclusion,
            "confidence": "medium",
            "data_sources": data_sources,
            "timestamp": ts.isoformat(),
        }
        path = tmp_path / f"{stock_code}.json"
        path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")

    def test_no_previous_conclusion(self, tmp_path):
        """没有历史结论时返回 None"""
        with patch("tradingagents.agents.utils.delta_check.DELTA_LOG_DIR", str(tmp_path)):
            result = check_delta("603629", "看多买入", ["market"])
            assert result is None

    def test_same_direction_no_flip(self, tmp_path):
        """同方向不触发翻转"""
        with patch("tradingagents.agents.utils.delta_check.DELTA_LOG_DIR", str(tmp_path)):
            self._make_conclusion_file(tmp_path, "603629", "看多买入", ["market"], hours_ago=10)
            result = check_delta("603629", "继续看多，建议买入", ["market"])
            assert result is None

    def test_bullish_to_bearish_flip(self, tmp_path):
        """看多→看空：触发翻转"""
        with patch("tradingagents.agents.utils.delta_check.DELTA_LOG_DIR", str(tmp_path)):
            self._make_conclusion_file(tmp_path, "603629", "看多买入", ["market"], hours_ago=10)
            result = check_delta("603629", "看空，建议卖出", ["market"])
            assert result is not None
            assert result["last_direction"] == "bullish"
            assert result["new_direction"] == "bearish"
            assert result["possible_noise"] is True  # 无新数据源

    def test_bearish_to_bullish_flip(self, tmp_path):
        """看空→看多：触发翻转"""
        with patch("tradingagents.agents.utils.delta_check.DELTA_LOG_DIR", str(tmp_path)):
            self._make_conclusion_file(tmp_path, "603629", "看空卖出", ["market"], hours_ago=10)
            result = check_delta("603629", "看多，建议买入", ["market"])
            assert result is not None
            assert result["last_direction"] == "bearish"
            assert result["new_direction"] == "bullish"

    def test_neutral_no_flip(self, tmp_path):
        """中性方向不触发翻转"""
        with patch("tradingagents.agents.utils.delta_check.DELTA_LOG_DIR", str(tmp_path)):
            self._make_conclusion_file(tmp_path, "603629", "当前市场震荡", ["market"], hours_ago=10)
            result = check_delta("603629", "看多买入", ["market"])
            assert result is None

    def test_outside_72h_window(self, tmp_path):
        """超过 72 小时不触发翻转"""
        with patch("tradingagents.agents.utils.delta_check.DELTA_LOG_DIR", str(tmp_path)):
            self._make_conclusion_file(tmp_path, "603629", "看多买入", ["market"], hours_ago=73)
            result = check_delta("603629", "看空卖出", ["market"])
            assert result is None

    def test_exactly_72h_window(self, tmp_path):
        """刚好 72 小时应该不触发（> 72 才不触发）"""
        with patch("tradingagents.agents.utils.delta_check.DELTA_LOG_DIR", str(tmp_path)):
            # 使用 71.9 小时，确保在窗口内
            self._make_conclusion_file(tmp_path, "603629", "看多买入", ["market"], hours_ago=71.9)
            result = check_delta("603629", "看空卖出", ["market"])
            assert result is not None

    def test_with_new_data_source(self, tmp_path):
        """有新数据源时，possible_noise=False"""
        with patch("tradingagents.agents.utils.delta_check.DELTA_LOG_DIR", str(tmp_path)):
            self._make_conclusion_file(tmp_path, "603629", "看多买入", ["market"], hours_ago=10)
            result = check_delta("603629", "看空卖出", ["market", "news", "fundamentals"])
            assert result is not None
            assert result["has_new_data"] is True
            assert "news" in result["new_data_added"]
            assert "fundamentals" in result["new_data_added"]
            assert result["possible_noise"] is False

    def test_no_new_data_source(self, tmp_path):
        """无新数据源时，possible_noise=True"""
        with patch("tradingagents.agents.utils.delta_check.DELTA_LOG_DIR", str(tmp_path)):
            self._make_conclusion_file(tmp_path, "603629", "看多买入", ["market", "news"], hours_ago=10)
            result = check_delta("603629", "看空卖出", ["market"])
            assert result is not None
            assert result["has_new_data"] is False
            assert result["possible_noise"] is True

    def test_result_contains_all_fields(self, tmp_path):
        """返回结果包含所有必要字段"""
        with patch("tradingagents.agents.utils.delta_check.DELTA_LOG_DIR", str(tmp_path)):
            self._make_conclusion_file(tmp_path, "603629", "看多买入", ["market"], hours_ago=10)
            result = check_delta("603629", "看空卖出", ["market", "news"])
            assert result is not None

            required_fields = [
                "stock_code", "last_conclusion", "new_conclusion",
                "last_direction", "new_direction",
                "last_action", "new_action",
                "last_timestamp", "new_timestamp",
                "hours_since_last",
                "has_new_data", "new_data_added", "possible_noise",
            ]
            for field in required_fields:
                assert field in result, f"Missing field: {field}"


# ── format_delta_warning: output format ──


class TestFormatDeltaWarning:
    """格式化警告输出测试"""

    def test_none_input(self):
        assert format_delta_warning(None) == ""

    def test_contains_stock_code(self):
        info = {
            "stock_code": "603629",
            "last_conclusion": "看多",
            "new_conclusion": "看空",
            "last_direction": "bullish",
            "new_direction": "bearish",
            "last_action": "ENTER (建仓)",
            "new_action": "EXIT (清仓)",
            "last_timestamp": "2026-07-22T10:00:00",
            "new_timestamp": "2026-07-23T10:00:00",
            "hours_since_last": 24.0,
            "has_new_data": False,
            "new_data_added": [],
            "possible_noise": True,
        }
        output = format_delta_warning(info)
        assert "603629" in output

    def test_contains_direction_change(self):
        info = {
            "stock_code": "603629",
            "last_conclusion": "看多",
            "new_conclusion": "看空",
            "last_direction": "bullish",
            "new_direction": "bearish",
            "last_action": "ENTER (建仓)",
            "new_action": "EXIT (清仓)",
            "last_timestamp": "2026-07-22T10:00:00",
            "new_timestamp": "2026-07-23T10:00:00",
            "hours_since_last": 24.0,
            "has_new_data": False,
            "new_data_added": [],
            "possible_noise": True,
        }
        output = format_delta_warning(info)
        assert "看多" in output
        assert "看空" in output

    def test_contains_action_change(self):
        info = {
            "stock_code": "603629",
            "last_conclusion": "看多",
            "new_conclusion": "看空",
            "last_direction": "bullish",
            "new_direction": "bearish",
            "last_action": "ENTER (建仓)",
            "new_action": "EXIT (清仓)",
            "last_timestamp": "2026-07-22T10:00:00",
            "new_timestamp": "2026-07-23T10:00:00",
            "hours_since_last": 24.0,
            "has_new_data": False,
            "new_data_added": [],
            "possible_noise": True,
        }
        output = format_delta_warning(info)
        assert "ENTER" in output
        assert "EXIT" in output

    def test_contains_noise_warning(self):
        info = {
            "stock_code": "603629",
            "last_conclusion": "看多",
            "new_conclusion": "看空",
            "last_direction": "bullish",
            "new_direction": "bearish",
            "last_action": "ENTER (建仓)",
            "new_action": "EXIT (清仓)",
            "last_timestamp": "2026-07-22T10:00:00",
            "new_timestamp": "2026-07-23T10:00:00",
            "hours_since_last": 24.0,
            "has_new_data": False,
            "new_data_added": [],
            "possible_noise": True,
        }
        output = format_delta_warning(info)
        assert "模型噪音" in output
        assert "人工复核" in output

    def test_contains_new_data_info(self):
        info = {
            "stock_code": "603629",
            "last_conclusion": "看多",
            "new_conclusion": "看空",
            "last_direction": "bullish",
            "new_direction": "bearish",
            "last_action": "ENTER (建仓)",
            "new_action": "EXIT (清仓)",
            "last_timestamp": "2026-07-22T10:00:00",
            "new_timestamp": "2026-07-23T10:00:00",
            "hours_since_last": 24.0,
            "has_new_data": True,
            "new_data_added": ["news", "fundamentals"],
            "possible_noise": False,
        }
        output = format_delta_warning(info)
        assert "news" in output
        assert "fundamentals" in output
        assert "新数据支撑" in output

    def test_contains_hours_since(self):
        info = {
            "stock_code": "603629",
            "last_conclusion": "看多",
            "new_conclusion": "看空",
            "last_direction": "bullish",
            "new_direction": "bearish",
            "last_action": "ENTER (建仓)",
            "new_action": "EXIT (清仓)",
            "last_timestamp": "2026-07-22T10:00:00",
            "new_timestamp": "2026-07-23T10:00:00",
            "hours_since_last": 24.0,
            "has_new_data": False,
            "new_data_added": [],
            "possible_noise": True,
        }
        output = format_delta_warning(info)
        assert "24.0" in output

    def test_c005_marker_present(self):
        """输出包含 C-005 标记"""
        info = {
            "stock_code": "603629",
            "last_conclusion": "看多",
            "new_conclusion": "看空",
            "last_direction": "bullish",
            "new_direction": "bearish",
            "last_action": "ENTER (建仓)",
            "new_action": "EXIT (清仓)",
            "last_timestamp": "2026-07-22T10:00:00",
            "new_timestamp": "2026-07-23T10:00:00",
            "hours_since_last": 24.0,
            "has_new_data": False,
            "new_data_added": [],
            "possible_noise": True,
        }
        output = format_delta_warning(info)
        assert "[C-005]" in output


# ── Integration: end-to-end flow ──


class TestIntegration:
    """集成测试：完整的 save → check → format 流程"""

    def test_full_flow_with_flip(self, tmp_path):
        """完整流程：保存结论 → 翻转检测 → 格式化输出"""
        with patch("tradingagents.agents.utils.delta_check.DELTA_LOG_DIR", str(tmp_path)):
            # 第一次分析：看多
            save_conclusion("603629", "看多买入，建议 ENTER", "high", ["market", "news"])

            # 第二次分析：看空（翻转）
            result = check_delta("603629", "看空卖出，建议 EXIT", ["market", "news"])

            assert result is not None
            assert result["last_direction"] == "bullish"
            assert result["new_direction"] == "bearish"
            assert result["possible_noise"] is True  # 无新数据源

            warning = format_delta_warning(result)
            assert "[C-005]" in warning
            assert "看多" in warning
            assert "看空" in warning
            assert "模型噪音" in warning

    def test_full_flow_no_flip(self, tmp_path):
        """完整流程：同方向不触发"""
        with patch("tradingagents.agents.utils.delta_check.DELTA_LOG_DIR", str(tmp_path)):
            save_conclusion("603629", "看多买入", "high", ["market"])
            result = check_delta("603629", "继续看多，建议买入", ["market"])
            assert result is None

    def test_full_flow_with_new_data(self, tmp_path):
        """完整流程：有新数据源时翻转合理"""
        with patch("tradingagents.agents.utils.delta_check.DELTA_LOG_DIR", str(tmp_path)):
            save_conclusion("603629", "看多买入", "high", ["market"])

            # 新增了 news 和 fundamentals 数据源
            result = check_delta("603629", "看空卖出", ["market", "news", "fundamentals"])

            assert result is not None
            assert result["has_new_data"] is True
            assert result["possible_noise"] is False
            assert "news" in result["new_data_added"]
            assert "fundamentals" in result["new_data_added"]

    def test_multiple_symbols_independent(self, tmp_path):
        """不同股票的结论互不影响"""
        with patch("tradingagents.agents.utils.delta_check.DELTA_LOG_DIR", str(tmp_path)):
            save_conclusion("603629", "看多买入", "high", ["market"])
            save_conclusion("000001", "看空卖出", "medium", ["market"])

            # 603629 翻转
            result1 = check_delta("603629", "看空卖出", ["market"])
            assert result1 is not None

            # 000001 同方向
            result2 = check_delta("000001", "继续看空", ["market"])
            assert result2 is None


# ── [C-005-R1] 止损字段不构成方向；否定/系统文字不污染 ──


class TestC005R1StopLossNotDirection:
    """止损条件属于风险控制，不得单独把研究方向判成偏空。"""

    def test_bullish_with_stop_loss_field_stays_bullish(self):
        assert _extract_direction("看多，建议买入，止损位设在150元。") == "bullish"

    def test_bullish_with_stop_loss_clause_stays_bullish(self):
        assert (
            _extract_direction("突破180元买入做多；若跌破150元触发止损，则离场。")
            == "bullish"
        )

    def test_strong_buy_with_protective_stop_stays_bullish(self):
        assert (
            _extract_direction("强烈建议买入建仓，跌破150元止损离场。") == "bullish"
        )

    def test_explicit_stop_loss_exit_still_bearish(self):
        # 明确退出指令仍是看空：真实退出不被吞掉
        assert _extract_direction("虽然基本面看多，但建议止损离场") == "bearish"
        assert _extract_direction("反弹无力，止损出局。") == "bearish"

    def test_negated_sell_not_bearish(self):
        # 否定词不污染：不建议卖出 ≠ 看空
        assert _extract_direction("不建议卖出，继续持有，回调可加仓。") != "bearish"
        # "无力"的"无"不得误判为否定（回归 J）
        assert _extract_direction("反弹无力，止损出局。") == "bearish"

    def test_negated_buy_not_bullish(self):
        assert _extract_direction("短线不宜买入，观望为宜。") != "bullish"

    def test_action_enum_lines_do_not_pollute(self):
        # 系统覆盖文字（动作枚举文档）不污染方向
        text = "可选动作：ENTER/WAIT/HOLD/REDUCE/EXIT\n结论：看多，条件买入。"
        assert _extract_direction(text) == "bullish"
        text2 = "**ENTER / WAIT / HOLD / REDUCE / EXIT**\n最终看多，回调条件买入。"
        assert _extract_direction(text2) == "bullish"

    def test_previous_delta_warning_block_does_not_pollute(self):
        # 上一次保存的结论尾部带有 [C-005] 警告区块，方向提取须剥离
        text = (
            "看多，建议买入。\n\n⚠️ [C-005] 同股票结论翻转警告\n"
            "方向变化：看多 → 看空"
        )
        assert _extract_direction(text) == "bullish"


class TestC005R1FalseFlipReplay:
    """回放：看多+止损条件不再产生虚假"bullish→bearish"翻转警告。"""

    def _make_conclusion_file(self, tmp_path, stock_code, conclusion, data_sources, hours_ago=0):
        os.makedirs(tmp_path, exist_ok=True)
        ts = datetime.now() - timedelta(hours=hours_ago)
        record = {
            "stock_code": stock_code,
            "conclusion": conclusion,
            "confidence": "medium",
            "data_sources": data_sources,
            "timestamp": ts.isoformat(),
        }
        path = tmp_path / f"{stock_code}.json"
        path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")

    def test_bullish_with_stop_condition_no_false_flip(self, tmp_path):
        self._make_conclusion_file(
            tmp_path, "600519", "看多，建议买入建仓。", ["market"]
        )
        with patch("tradingagents.agents.utils.delta_check.DELTA_LOG_DIR", str(tmp_path)):
            new_conclusion = "看多，建议买入，止损位设在150元。"
            assert _extract_direction(new_conclusion) == "bullish"
            assert check_delta("600519", new_conclusion, ["market"]) is None

    def test_real_flip_still_detected(self, tmp_path):
        self._make_conclusion_file(
            tmp_path, "600519", "看多，建议买入建仓。", ["market"]
        )
        with patch("tradingagents.agents.utils.delta_check.DELTA_LOG_DIR", str(tmp_path)):
            info = check_delta("600519", "看空，建议清仓离场。", ["market"])
            assert info is not None
            assert info["last_direction"] == "bullish"
            assert info["new_direction"] == "bearish"

    def test_replay_with_position_and_flat_outputs(self, tmp_path):
        # 回放持仓/未持仓两类输出：方向不被止损条件污染，动作随持仓分化
        from tradingagents.graph.signal_processing import _extract_decision_semantics

        bullish_with_stop = "看多，建议买入，止损位设在150元。"
        for has_position in (True, False):
            semantics = _extract_decision_semantics(
                bullish_with_stop, has_position=has_position, trigger_price=180.0
            )
            assert semantics.research_direction == "偏多", has_position
        held = _extract_decision_semantics(
            bullish_with_stop, has_position=True, trigger_price=180.0
        )
        flat = _extract_decision_semantics(
            bullish_with_stop, has_position=False, trigger_price=180.0
        )
        assert held.execution_action == "HOLD"
        assert flat.execution_action == "ENTER"
