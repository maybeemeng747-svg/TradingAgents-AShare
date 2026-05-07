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

        assert payload["title"] == "TradingAgents 601958.SH 定时分析"
        assert "交易日：2026-05-07" in payload["body"]
        assert "目标价：12.3" in payload["body"]
        assert "止损价：10.8" in payload["body"]

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
