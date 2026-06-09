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

        assert payload["title"] == "601958.SH HOLD/中性 62%"
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

        assert payload["title"] == "601958.SH 等待触发/偏多 62%"
        assert "结论：等待触发，方向：偏多，动作码：WAIT" in payload["body"]
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

        assert payload["title"] == "000938.SZ SELL/偏空 85%"
        assert "价位：目标 31 / 止损 33.8" in payload["body"]
        assert "风控：Hold" in payload["body"]
        assert "未持仓：严格禁止开立任何多头仓位" in payload["body"]
        assert "已持仓：应执行减仓或清仓操作" in payload["body"]
        assert "### 风控委员会审核报告" not in payload["body"]
        assert len(payload["body"]) <= 900

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
