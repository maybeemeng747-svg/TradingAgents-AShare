"""Tests for api.services.bark_notification_service."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def _make_report(**overrides):
    defaults = dict(
        id="rpt-1",
        symbol="601958.SH",
        trade_date="2026-05-07",
        decision="HOLD",
        direction="中性",
        confidence=62,
        target_price=12.3,
        stop_loss_price=10.8,
        final_trade_decision="结论：盘中未出现明确放量突破，按计划等待确认。",
        trader_investment_plan=None,
        investment_plan=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


async def _noop_sleep(*args):
    pass


class TestBarkPayload:
    def test_report_payload_contains_trade_levels(self):
        from api.services.bark_notification_service import build_report_payload

        payload = build_report_payload(_make_report())

        assert payload["title"] == "601958.SH 中性 HOLD 62%"
        assert "TradingAgents 定时分析 | 2026-05-07" in payload["body"]
        assert "价位：目标 12.3 / 止损 10.8" in payload["body"]

    def test_report_payload_prefers_action_label(self):
        from api.services.bark_notification_service import build_report_payload

        payload = build_report_payload(_make_report(
            decision="HOLD",
            direction="中性",
            result_data={
                "action_label": "等待触发",
                "research_direction": "偏多",
                "execution_action": "WAIT",
            },
        ))

        assert payload["title"] == "601958.SH 偏多 等待触发 62%"
        assert "结论：等待触发，方向：偏多，动作：WAIT" in payload["body"]
        assert "结论：HOLD" not in payload["body"]

    def test_report_payload_uses_push_summary_instead_of_full_markdown_report(self):
        from api.services.bark_notification_service import build_report_payload

        payload = build_report_payload(_make_report(
            symbol="000938.SZ",
            trade_date="2026-05-08",
            decision="SELL",
            direction="偏空",
            confidence=85,
            target_price=31.0,
            stop_loss_price=33.8,
            final_trade_decision=(
                "### 风控委员会审核报告 **审核结论：** **Hold (持有/观望)** "
                "核心判断：交易员方案的方向判断（卖出/观望）与上游研究团队的偏空结论一致。"
                "未持仓者：严格禁止开立任何多头仓位。初始目标仓位为0%。"
                "已持仓者（若存在）：应执行减仓或清仓操作。建议在股价反弹至33.50-33.80元区间时，将仓位降至20%以下或清仓。"
                "事件风险约束：需高度警惕并监控事件风险。"
            ),
        ))

        assert payload["title"] == "000938.SZ 偏空 SELL 85%"
        assert "价位：目标 31 / 止损 33.8" in payload["body"]
        assert "风控：Hold" in payload["body"]
        assert "持仓：应执行减仓或清仓操作" in payload["body"]
        assert "### 风控委员会审核报告" not in payload["body"]
        assert len(payload["body"]) <= 900

    def test_report_payload_preserves_short_complete_guidance(self):
        from api.services.bark_notification_service import build_report_payload

        payload = build_report_payload(_make_report(
            final_trade_decision="已持仓者：清仓。未持仓者：不开仓。",
        ))

        assert "持仓：清仓" in payload["body"]
        assert "未持仓：不开仓" in payload["body"]

    def test_report_payload_preserves_short_complete_trigger(self):
        from api.services.bark_notification_service import build_report_payload

        payload = build_report_payload(_make_report(
            final_trade_decision="未持仓者：等待。站稳年线。",
        ))

        assert "触发：站稳年线" in payload["body"]

    def test_report_payload_omits_incomplete_trigger_fragment(self):
        from api.services.bark_notification_service import build_report_payload

        payload = build_report_payload(_make_report(
            final_trade_decision="触发基本面失效条件的核心。",
        ))

        assert "触发：" not in payload["body"]

    def test_report_payload_uses_later_valid_trigger_after_fragment(self):
        from api.services.bark_notification_service import build_report_payload

        payload = build_report_payload(_make_report(
            final_trade_decision="触发基本面失效条件的核心。站稳年线。",
        ))

        assert "触发：站稳年线" in payload["body"]

    def test_report_payload_scans_later_trigger_with_same_keyword(self):
        from api.services.bark_notification_service import build_report_payload

        payload = build_report_payload(_make_report(
            final_trade_decision="触发基本面失效条件的核心。触发：突破12.3元。",
        ))

        assert "触发：突破12.3元" in payload["body"]

    def test_report_payload_does_not_treat_generic_count_as_trigger(self):
        from api.services.bark_notification_service import build_report_payload

        payload = build_report_payload(_make_report(
            final_trade_decision="触发基本面失效条件的核心有3项。触发：突破12.3元。",
        ))

        assert "触发：突破12.3元" in payload["body"]
        assert "触发基本面失效条件的核心有3项" not in payload["body"]

    def test_report_payload_extracts_trigger_after_comma_in_same_sentence(self):
        from api.services.bark_notification_service import build_report_payload

        payload = build_report_payload(_make_report(
            final_trade_decision="触发基本面失效条件的核心有3项，触发：突破12.3元。",
        ))

        assert "触发：突破12.3元" in payload["body"]
        assert "触发基本面失效条件的核心有3项" not in payload["body"]

    def test_report_payload_prefers_explicit_entry_trigger_over_stop_loss(self):
        from api.services.bark_notification_service import build_report_payload

        payload = build_report_payload(_make_report(
            final_trade_decision="已持仓者：跌破10元止损。未持仓者：触发：突破12元再买入。",
        ))

        assert "触发：突破12元再买入" in payload["body"]
        assert "触发：跌破10元止损" not in payload["body"]

    def test_report_payload_accepts_trigger_price_with_wei_separator(self):
        from api.services.bark_notification_service import build_report_payload

        payload = build_report_payload(_make_report(
            final_trade_decision="为剩余仓位设置条件单，触发价为14.40元，动作为全部卖出。",
        ))

        assert "触发：14.40元" in payload["body"]

    def test_test_payload_uses_default_copy(self):
        from api.services.bark_notification_service import build_test_payload

        payload = build_test_payload()

        assert payload["title"] == "TradingAgents Bark 测试"
        assert "测试消息" in payload["body"]


class TestBarkSendMessage:
    @patch("api.services.bark_notification_service.requests.post")
    def test_accepts_plain_device_key(self, mock_post):
        from api.services.bark_notification_service import send_message

        response = MagicMock()
        response.json.return_value = {"code": 200}
        mock_post.return_value = response

        result = send_message({"title": "hello", "body": "world"}, "abcd1234efgh5678")

        assert result is True
        assert mock_post.call_args.args[0] == "https://api.day.app/abcd1234efgh5678"

    @patch("api.services.bark_notification_service.requests.post")
    def test_rejects_http_url(self, mock_post):
        from api.services.bark_notification_service import send_message

        with pytest.raises(ValueError):
            send_message({"title": "hello", "body": "world"}, "http://api.day.app/key")

        mock_post.assert_not_called()

    @patch("api.services.bark_notification_service.requests.post")
    def test_returns_false_for_non_200_code(self, mock_post):
        from api.services.bark_notification_service import send_message

        response = MagicMock()
        response.json.return_value = {"code": 400}
        mock_post.return_value = response

        assert send_message({"title": "hello", "body": "world"}, "abcd1234efgh5678") is False


class TestBarkRetry:
    def test_success_on_retry(self):
        from api.services.bark_notification_service import send_report_message_with_retry

        with patch("api.services.bark_notification_service.send_message", side_effect=[False, True]) as mock_send, \
             patch("api.services.bark_notification_service.asyncio.sleep", side_effect=_noop_sleep):
            result = asyncio.run(send_report_message_with_retry(_make_report(), "https://api.day.app/key"))
            assert result is True
            assert mock_send.call_count == 2
