# [C-007] event_risk_gate — Tests
"""Tests for C-007 event_risk_gate module.

Covers:
1. Text-based event detection (announcements/news keywords)
2. Structured data detection (unlock_ratio, debt_ratio, net_profit_change)
3. Raw evidence extraction (extract_event_risk_inputs)
4. Risk level computation (critical/high/medium/none)
5. Risk-first mode activation and forbidden actions
6. Block open logic
7. Format output functions
8. Edge cases and boundary conditions
"""

import pytest

from tradingagents.agents.utils.event_risk_gate import (
    check_event_risk,
    detect_events_from_text,
    extract_event_risk_inputs,
    format_event_risk_warning,
    format_event_risk_block,
    EventSeverity,
    RISK_EVENT_TYPES,
    _RISK_FIRST_FORBIDDEN_ACTIONS,
)


# ── detect_events_from_text ──────────────────────────────────────────────────


class TestTextDetectionLargeUnlock:
    """大比例解禁文本检测。"""

    def test_unlock_ratio_mention(self):
        text = "本次解禁流通股本比例为8.5%，属于大规模解禁。"
        events = detect_events_from_text(text)
        assert any(e["event_type"] == "large_unlock" for e in events)

    def test_limited_shares_unlock(self):
        text = "限售股将于下周一上市流通。"
        events = detect_events_from_text(text)
        assert any(e["event_type"] == "large_unlock" for e in events)

    def test_massive_reduction(self):
        text = "大股东拟大规模减持公司股份。"
        events = detect_events_from_text(text)
        assert any(e["event_type"] == "large_unlock" for e in events)

    def test_no_false_positive(self):
        text = "公司经营正常，无重大事项。"
        events = detect_events_from_text(text)
        assert not any(e["event_type"] == "large_unlock" for e in events)


class TestTextDetectionMajorMA:
    """重大并购/重组文本检测。"""

    def test_merger_restructuring(self):
        text = "公司正在筹划重大资产重组事项。"
        events = detect_events_from_text(text)
        assert any(e["event_type"] == "major_ma" for e in events)

    def test_share_purchase(self):
        text = "公司拟发行股份购买资产。"
        events = detect_events_from_text(text)
        assert any(e["event_type"] == "major_ma" for e in events)

    def test_tender_offer(self):
        text = "涉及要约收购相关安排。"
        events = detect_events_from_text(text)
        assert any(e["event_type"] == "major_ma" for e in events)


class TestTextDetectionSuspension:
    """停复牌文本检测。"""

    def test_suspension(self):
        text = "公司股票自2025年1月1日起停牌。"
        events = detect_events_from_text(text)
        assert any(e["event_type"] == "suspension" for e in events)

    def test_resume(self):
        text = "公司股票将于下周一复牌。"
        events = detect_events_from_text(text)
        assert any(e["event_type"] == "suspension" for e in events)


class TestTextDetectionEarningsCrash:
    """业绩暴雷文本检测。"""

    def test_profit_decline(self):
        text = "预计净利润同比下降65%。"
        events = detect_events_from_text(text)
        assert any(e["event_type"] == "earnings_crash" for e in events)

    def test_loss_warning(self):
        text = "公司预计2025年业绩预亏。"
        events = detect_events_from_text(text)
        assert any(e["event_type"] == "earnings_crash" for e in events)

    def test_profit_to_loss(self):
        text = "公司由盈转亏，预计全年亏损2亿元。"
        events = detect_events_from_text(text)
        assert any(e["event_type"] == "earnings_crash" for e in events)


class TestTextDetectionLitigation:
    """重大诉讼文本检测。"""

    def test_major_litigation(self):
        text = "公司涉及重大诉讼事项，涉案金额5亿元。"
        events = detect_events_from_text(text)
        assert any(e["event_type"] == "major_litigation" for e in events)

    def test_securities_fraud(self):
        text = "因证券虚假陈述被投资者起诉赔偿。"
        events = detect_events_from_text(text)
        assert any(e["event_type"] == "major_litigation" for e in events)


class TestTextDetectionRegulatory:
    """监管立案/调查文本检测。"""

    def test_investigation(self):
        text = "公司被证监会立案调查。"
        events = detect_events_from_text(text)
        assert any(e["event_type"] == "regulatory_investigation" for e in events)

    def test_penalty(self):
        text = "公司收到交易所监管函。"
        events = detect_events_from_text(text)
        assert any(e["event_type"] == "regulatory_investigation" for e in events)

    def test_warning_letter(self):
        text = "公司收到证监局警示函。"
        events = detect_events_from_text(text)
        assert any(e["event_type"] == "regulatory_investigation" for e in events)


class TestTextDetectionWriteOff:
    """大额资产减值文本检测。"""

    def test_goodwill_impairment(self):
        text = "公司计提商誉减值准备10亿元。"
        events = detect_events_from_text(text)
        assert any(e["event_type"] == "major_write_off" for e in events)

    def test_asset_impairment(self):
        text = "公司大额计提资产减值损失。"
        events = detect_events_from_text(text)
        assert any(e["event_type"] == "major_write_off" for e in events)


class TestTextDetectionControlChange:
    """控制权变更文本检测。"""

    def test_controller_change(self):
        text = "公司实际控制人拟发生变更。"
        events = detect_events_from_text(text)
        assert any(e["event_type"] == "control_change" for e in events)

    def test_control_transfer(self):
        text = "涉及控制权转移安排。"
        events = detect_events_from_text(text)
        assert any(e["event_type"] == "control_change" for e in events)


class TestTextDetectionRiskWarning:
    """风险警示/ST文本检测。"""

    def test_st_mark(self):
        text = "公司股票被实施ST处理。"
        events = detect_events_from_text(text)
        assert any(e["event_type"] == "risk_warning" for e in events)

    def test_star_st(self):
        text = "*ST某某公司发布公告。"
        events = detect_events_from_text(text)
        assert any(e["event_type"] == "risk_warning" for e in events)

    def test_delisting(self):
        text = "公司股票面临终止上市风险。"
        events = detect_events_from_text(text)
        assert any(e["event_type"] == "risk_warning" for e in events)


class TestTextDetectionMultiple:
    """多事件同时检测。"""

    def test_multiple_events_detected(self):
        text = "公司被证监会立案调查，同时涉及重大诉讼，净利润预计下降70%。"
        events = detect_events_from_text(text)
        types = {e["event_type"] for e in events}
        assert "regulatory_investigation" in types
        assert "major_litigation" in types
        assert "earnings_crash" in types

    def test_empty_text_returns_empty(self):
        assert detect_events_from_text("") == []

    def test_none_text_returns_empty(self):
        assert detect_events_from_text(None) == []

    def test_clean_text_returns_empty(self):
        text = "公司经营正常，各项业务稳步推进。"
        assert detect_events_from_text(text) == []


# ── check_event_risk: structured data ────────────────────────────────────────


class TestStructuredDetection:
    """结构化数据检测。"""

    def test_unlock_ratio_above_threshold(self):
        result = check_event_risk("X", unlock_ratio=0.08)
        assert result["has_risk"] is True
        assert "large_unlock" in result["risk_events"]

    def test_unlock_ratio_below_threshold(self):
        result = check_event_risk("X", unlock_ratio=0.03)
        assert result["has_risk"] is False

    def test_debt_ratio_above_threshold(self):
        result = check_event_risk("X", debt_ratio=0.85)
        assert result["has_risk"] is True
        assert "high_leverage" in result["risk_events"]

    def test_debt_ratio_below_threshold(self):
        result = check_event_risk("X", debt_ratio=0.60)
        assert result["has_risk"] is False

    def test_earnings_crash(self):
        result = check_event_risk("X", net_profit_change=-0.65)
        assert result["has_risk"] is True
        assert "earnings_crash" in result["risk_events"]

    def test_earnings_normal_decline(self):
        result = check_event_risk("X", net_profit_change=-0.30)
        assert result["has_risk"] is False

    def test_suspension(self):
        result = check_event_risk("X", is_suspended=True)
        assert result["has_risk"] is True
        assert "suspension" in result["risk_events"]

    def test_ma_event(self):
        result = check_event_risk("X", has_ma_event=True)
        assert result["has_risk"] is True
        assert "major_ma" in result["risk_events"]

    def test_multiple_structured_events(self):
        result = check_event_risk("X", unlock_ratio=0.10, debt_ratio=0.85)
        assert result["has_risk"] is True
        assert len(result["risk_events"]) == 2
        assert "large_unlock" in result["risk_events"]
        assert "high_leverage" in result["risk_events"]

    def test_all_none_returns_no_risk(self):
        result = check_event_risk("X")
        assert result["has_risk"] is False
        assert result["risk_level"] == "none"


# ── check_event_risk: risk levels ────────────────────────────────────────────


class TestRiskLevels:
    """风险等级计算。"""

    def test_critical_level_earnings_crash(self):
        result = check_event_risk("X", net_profit_change=-0.70)
        assert result["risk_level"] == "critical"

    def test_high_level_unlock(self):
        result = check_event_risk("X", unlock_ratio=0.10)
        assert result["risk_level"] == "high"

    def test_medium_level_leverage(self):
        result = check_event_risk("X", debt_ratio=0.85)
        assert result["risk_level"] == "medium"

    def test_critical_overrides_high(self):
        # earnings_crash is critical, unlock is high → overall critical
        result = check_event_risk("X", net_profit_change=-0.60, unlock_ratio=0.10)
        assert result["risk_level"] == "critical"

    def test_no_risk_level_none(self):
        result = check_event_risk("X")
        assert result["risk_level"] == "none"


class TestBlockOpen:
    """开仓阻断逻辑。"""

    def test_critical_blocks_open(self):
        result = check_event_risk("X", net_profit_change=-0.70)
        assert result["block_open"] is True

    def test_high_blocks_open(self):
        result = check_event_risk("X", unlock_ratio=0.10)
        assert result["block_open"] is True

    def test_medium_does_not_block(self):
        result = check_event_risk("X", debt_ratio=0.85)
        assert result["block_open"] is False

    def test_no_risk_no_block(self):
        result = check_event_risk("X")
        assert result["block_open"] is False


class TestRiskFirstMode:
    """风控优先模式。"""

    def test_critical_activates_risk_first(self):
        result = check_event_risk("X", net_profit_change=-0.70)
        assert result["risk_first_mode"] is True

    def test_high_activates_risk_first(self):
        result = check_event_risk("X", unlock_ratio=0.10)
        assert result["risk_first_mode"] is True

    def test_medium_no_risk_first(self):
        result = check_event_risk("X", debt_ratio=0.85)
        assert result["risk_first_mode"] is False

    def test_risk_first_forbids_buy_actions(self):
        result = check_event_risk("X", net_profit_change=-0.70)
        assert len(result["forbidden_actions"]) > 0
        assert "建议建仓" in result["forbidden_actions"]
        assert "BUY" in result["forbidden_actions"]

    def test_no_risk_no_forbidden_actions(self):
        result = check_event_risk("X")
        assert result["forbidden_actions"] == []


# ── check_event_risk: text + structured combined ─────────────────────────────


class TestCombinedDetection:
    """文本+结构化组合检测。"""

    def test_text_detection_adds_to_structured(self):
        result = check_event_risk(
            "X",
            unlock_ratio=0.10,
            announcements_text="公司被证监会立案调查。",
        )
        assert "large_unlock" in result["risk_events"]
        assert "regulatory_investigation" in result["risk_events"]

    def test_text_detection_no_duplicate(self):
        # If text detects same event type as structured, no duplicate
        result = check_event_risk(
            "X",
            unlock_ratio=0.10,
            announcements_text="本次解禁流通股本比例为8%。",
        )
        unlock_count = sum(1 for e in result["risk_events"] if e == "large_unlock")
        assert unlock_count == 1

    def test_news_text_used(self):
        result = check_event_risk(
            "X",
            news_text="该公司被ST处理，投资者注意风险。",
        )
        assert "risk_warning" in result["risk_events"]


# ── extract_event_risk_inputs ────────────────────────────────────────────────


class TestExtractInputs:
    """从 raw_evidence 提取输入。"""

    def test_empty_raw_evidence(self):
        result = extract_event_risk_inputs(raw_evidence={})
        assert result["unlock_ratio"] is None
        assert result["debt_ratio"] is None
        assert result["announcements_text"] == ""

    def test_none_raw_evidence(self):
        result = extract_event_risk_inputs(raw_evidence=None)
        assert result["unlock_ratio"] is None

    def test_announcements_from_raw_evidence_string(self):
        raw = {"announcements": {"raw": "公司发布重大公告。"}}
        result = extract_event_risk_inputs(raw_evidence=raw)
        assert "公司发布重大公告" in result["announcements_text"]

    def test_announcements_from_raw_evidence_list(self):
        raw = {"announcements": {"raw": [
            {"title": "重大事项公告", "content": "公司筹划重组"},
            {"title": "业绩预告", "content": "净利润预增"},
        ]}}
        result = extract_event_risk_inputs(raw_evidence=raw)
        assert "重大事项公告" in result["announcements_text"]
        assert "业绩预告" in result["announcements_text"]

    def test_news_from_raw_evidence(self):
        raw = {"news": {"raw": "该公司被立案调查。"}}
        result = extract_event_risk_inputs(raw_evidence=raw)
        assert "立案调查" in result["news_text"]

    def test_fallback_text_params(self):
        result = extract_event_risk_inputs(
            announcements_text="外部公告文本",
            news_text="外部新闻文本",
        )
        assert result["announcements_text"] == "外部公告文本"
        assert result["news_text"] == "外部新闻文本"

    def test_raw_evidence_overrides_fallback(self):
        raw = {"announcements": {"raw": "内部公告"}}
        result = extract_event_risk_inputs(
            raw_evidence=raw,
            announcements_text="外部公告",
        )
        assert result["announcements_text"] == "内部公告"

    def test_debt_ratio_from_facts(self):
        raw = {
            "financial_period_facts": {
                "raw": [
                    {"metric": "total_assets", "report_date": "2025-12-31",
                     "period_scope": "POINT_IN_TIME", "value": 100000.0},
                    {"metric": "total_liabilities", "report_date": "2025-12-31",
                     "period_scope": "POINT_IN_TIME", "value": 85000.0},
                ],
            },
        }
        result = extract_event_risk_inputs(raw_evidence=raw)
        assert result["debt_ratio"] is not None
        assert abs(result["debt_ratio"] - 0.85) < 0.01


# ── format_event_risk_warning ────────────────────────────────────────────────


class TestFormatWarning:
    """格式化警告输出。"""

    def test_no_risk_returns_empty(self):
        assert format_event_risk_warning({"has_risk": False}) == ""

    def test_critical_format(self):
        info = check_event_risk("X", net_profit_change=-0.70)
        warning = format_event_risk_warning(info)
        assert "严重" in warning
        assert "业绩暴雷" in warning

    def test_high_format(self):
        info = check_event_risk("X", unlock_ratio=0.10)
        warning = format_event_risk_warning(info)
        assert "高风险" in warning
        assert "大比例解禁" in warning

    def test_risk_first_mode_message(self):
        info = check_event_risk("X", net_profit_change=-0.70)
        warning = format_event_risk_warning(info)
        assert "风控优先模式" in warning

    def test_block_open_message(self):
        info = check_event_risk("X", unlock_ratio=0.10)
        warning = format_event_risk_warning(info)
        assert "开仓阻断" in warning

    def test_medium_no_block_message(self):
        info = check_event_risk("X", debt_ratio=0.85)
        warning = format_event_risk_warning(info)
        assert "中风险" in warning
        assert "开仓阻断" not in warning


class TestFormatBlock:
    """格式化系统区块。"""

    def test_no_risk_returns_empty(self):
        assert format_event_risk_block({"has_risk": False}) == ""

    def test_block_format(self):
        info = check_event_risk("X", unlock_ratio=0.10)
        block = format_event_risk_block(info)
        assert "[C-007]" in block
        assert "high" in block

    def test_block_open_status(self):
        info = check_event_risk("X", unlock_ratio=0.10)
        block = format_event_risk_block(info)
        assert "阻断" in block


# ── Edge cases ───────────────────────────────────────────────────────────────


class TestEdgeCases:
    """边界条件。"""

    def test_zero_unlock_ratio(self):
        result = check_event_risk("X", unlock_ratio=0.0)
        assert result["has_risk"] is False

    def test_exactly_at_threshold(self):
        result = check_event_risk("X", unlock_ratio=0.05)
        assert result["has_risk"] is False  # > not >=

    def test_just_above_threshold(self):
        result = check_event_risk("X", unlock_ratio=0.051)
        assert result["has_risk"] is True

    def test_negative_profit_change_at_threshold(self):
        result = check_event_risk("X", net_profit_change=-0.50)
        assert result["has_risk"] is False  # < not <=

    def test_just_below_threshold(self):
        result = check_event_risk("X", net_profit_change=-0.501)
        assert result["has_risk"] is True

    def test_debt_ratio_exactly_80(self):
        result = check_event_risk("X", debt_ratio=0.80)
        assert result["has_risk"] is False  # > not >=

    def test_debt_ratio_81(self):
        result = check_event_risk("X", debt_ratio=0.81)
        assert result["has_risk"] is True

    def test_empty_stock_code(self):
        result = check_event_risk("", net_profit_change=-0.70)
        assert result["has_risk"] is True  # detection still works

    def test_all_events_active(self):
        result = check_event_risk(
            "X",
            unlock_ratio=0.10,
            debt_ratio=0.85,
            net_profit_change=-0.70,
            is_suspended=True,
            has_ma_event=True,
        )
        assert result["has_risk"] is True
        assert len(result["risk_events"]) == 5
        assert result["risk_level"] == "critical"  # earnings_crash is critical

    def test_risk_event_types_all_have_metadata(self):
        """All defined event types should have label and default_severity."""
        for event_type, meta in RISK_EVENT_TYPES.items():
            assert "label" in meta, f"{event_type} missing label"
            assert "default_severity" in meta, f"{event_type} missing default_severity"
            assert meta["default_severity"] in (
                EventSeverity.CRITICAL,
                EventSeverity.HIGH,
                EventSeverity.MEDIUM,
            )

    def test_forbidden_actions_not_empty_in_risk_first(self):
        result = check_event_risk("X", net_profit_change=-0.70)
        assert len(result["forbidden_actions"]) > 0


# ── Determinism ──────────────────────────────────────────────────────────────


class TestDeterminism:
    """确定性测试。"""

    def test_same_input_same_output(self):
        r1 = check_event_risk("X", unlock_ratio=0.10, debt_ratio=0.85)
        r2 = check_event_risk("X", unlock_ratio=0.10, debt_ratio=0.85)
        assert r1 == r2

    def test_text_detection_deterministic(self):
        text = "公司被证监会立案调查。"
        e1 = detect_events_from_text(text)
        e2 = detect_events_from_text(text)
        assert len(e1) == len(e2)
        assert [e["event_type"] for e in e1] == [e["event_type"] for e in e2]
