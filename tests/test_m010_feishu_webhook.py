"""Tests for api.services.feishu_webhook_service (M-010).

Covers:
- URL normalization (full URL, token-only, http upgrade, invalid)
- URL masking (safe logging)
- Card message building (report card, draft card, text, test)
- Env var handling (FEISHU_WEBHOOK_URL, FEISHU_WEBHOOK_ENABLED)
- HTTP send (success, failure, retry, timeout)
- Disabled state graceful no-op
"""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# URL normalization tests
# ---------------------------------------------------------------------------


class TestNormalizeWebhookUrl:
    def test_full_url_passes_through(self):
        from api.services.feishu_webhook_service import normalize_webhook_url

        url = "https://open.feishu.cn/open-apis/bot/v2/hook/abc-def-123"
        assert normalize_webhook_url(url) == url

    def test_token_only_expanded(self):
        from api.services.feishu_webhook_service import normalize_webhook_url

        result = normalize_webhook_url("abc-def-123")
        assert result == "https://open.feishu.cn/open-apis/bot/v2/hook/abc-def-123"

    def test_http_upgraded_to_https(self):
        from api.services.feishu_webhook_service import normalize_webhook_url

        url = "http://open.feishu.cn/open-apis/bot/v2/hook/abc-def"
        result = normalize_webhook_url(url)
        assert result.startswith("https://")

    def test_empty_raises(self):
        from api.services.feishu_webhook_service import normalize_webhook_url

        with pytest.raises(ValueError, match="不能为空"):
            normalize_webhook_url("")

    def test_none_raises(self):
        from api.services.feishu_webhook_service import normalize_webhook_url

        with pytest.raises(ValueError, match="不能为空"):
            normalize_webhook_url(None)

    def test_wrong_host_raises(self):
        from api.services.feishu_webhook_service import normalize_webhook_url

        with pytest.raises(ValueError, match="仅支持飞书官方"):
            normalize_webhook_url("https://evil.com/open-apis/bot/v2/hook/abc")

    def test_wrong_path_raises(self):
        from api.services.feishu_webhook_service import normalize_webhook_url

        with pytest.raises(ValueError, match="路径格式不正确"):
            normalize_webhook_url("https://open.feishu.cn/wrong/path/abc")

    def test_invalid_token_chars_raises(self):
        from api.services.feishu_webhook_service import normalize_webhook_url

        with pytest.raises(ValueError, match="token 格式不正确"):
            normalize_webhook_url("abc def!")

    def test_whitespace_trimmed(self):
        from api.services.feishu_webhook_service import normalize_webhook_url

        result = normalize_webhook_url("  abc-def-123  ")
        assert result == "https://open.feishu.cn/open-apis/bot/v2/hook/abc-def-123"


# ---------------------------------------------------------------------------
# URL masking tests
# ---------------------------------------------------------------------------


class TestMaskWebhookUrl:
    def test_full_url_masks_token(self):
        from api.services.feishu_webhook_service import mask_webhook_url

        url = "https://open.feishu.cn/open-apis/bot/v2/hook/abcdefgh1234"
        masked = mask_webhook_url(url)
        assert "open.feishu.cn" in masked
        assert "abcdefgh1234" not in masked

    def test_token_only_masks(self):
        from api.services.feishu_webhook_service import mask_webhook_url

        masked = mask_webhook_url("abcdefgh1234")
        assert masked is not None
        assert "abcdefgh1234" not in masked

    def test_short_token_fully_masked(self):
        from api.services.feishu_webhook_service import mask_webhook_url

        masked = mask_webhook_url("abc")
        assert masked == "******"

    def test_none_returns_none(self):
        from api.services.feishu_webhook_service import mask_webhook_url

        assert mask_webhook_url(None) is None

    def test_empty_returns_none(self):
        from api.services.feishu_webhook_service import mask_webhook_url

        assert mask_webhook_url("") is None


# ---------------------------------------------------------------------------
# Message building tests
# ---------------------------------------------------------------------------


class TestBuildCardMessage:
    def test_basic_structure(self):
        from api.services.feishu_webhook_service import build_card_message

        card = build_card_message("Test Title", ["line 1", "line 2"])
        assert card["msg_type"] == "interactive"
        assert card["card"]["header"]["title"]["content"] == "Test Title"
        assert card["card"]["header"]["template"] == "blue"
        elements = card["card"]["elements"]
        assert len(elements) == 1
        assert "line 1" in elements[0]["text"]["content"]

    def test_color_parameter(self):
        from api.services.feishu_webhook_service import build_card_message

        card = build_card_message("Title", ["content"], color="red")
        assert card["card"]["header"]["template"] == "red"

    def test_title_truncated(self):
        from api.services.feishu_webhook_service import build_card_message

        long_title = "x" * 200
        card = build_card_message(long_title, ["content"])
        assert len(card["card"]["header"]["title"]["content"]) == 128


class TestBuildTextMessage:
    def test_basic_structure(self):
        from api.services.feishu_webhook_service import build_text_message

        msg = build_text_message("hello")
        assert msg["msg_type"] == "text"
        assert msg["content"]["text"] == "hello"

    def test_content_truncated(self):
        from api.services.feishu_webhook_service import build_text_message

        long_text = "x" * 5000
        msg = build_text_message(long_text)
        assert len(msg["content"]["text"]) == 4000


class TestBuildReportCard:
    def test_basic_fields(self):
        from api.services.feishu_webhook_service import build_report_card

        card = build_report_card(
            symbol="600519.SH",
            trade_date="2026-07-23",
            action_label="条件入场",
            direction="看多",
            confidence=78,
        )
        assert "600519.SH" in card["card"]["header"]["title"]["content"]
        content = card["card"]["elements"][0]["text"]["content"]
        assert "条件入场" in content
        assert "看多" in content
        assert "78%" in content

    def test_price_fields(self):
        from api.services.feishu_webhook_service import build_report_card

        card = build_report_card(
            symbol="600519.SH",
            trade_date="2026-07-23",
            target_price=1880.0,
            stop_loss_price=1750.0,
        )
        content = card["card"]["elements"][0]["text"]["content"]
        assert "1880" in content
        assert "1750" in content

    def test_risk_items(self):
        from api.services.feishu_webhook_service import build_report_card

        card = build_report_card(
            symbol="600519.SH",
            trade_date="2026-07-23",
            risk_items=[
                {"name": "估值偏高", "level": "medium", "description": "PE 处于历史高位"},
                {"name": "业绩风险", "level": "high", "description": "营收增速放缓"},
            ],
        )
        content = card["card"]["elements"][0]["text"]["content"]
        assert "估值偏高" in content
        assert "业绩风险" in content

    def test_summary_clipped(self):
        from api.services.feishu_webhook_service import build_report_card

        long_summary = "x" * 1000
        card = build_report_card(
            symbol="600519.SH",
            trade_date="2026-07-23",
            summary=long_summary,
        )
        content = card["card"]["elements"][0]["text"]["content"]
        assert len(content) < 1200  # summary clipped to 500

    def test_color_bearish(self):
        from api.services.feishu_webhook_service import build_report_card

        card = build_report_card(
            symbol="600519.SH", trade_date="2026-07-23", direction="看空",
        )
        assert card["card"]["header"]["template"] == "red"

    def test_color_bullish(self):
        from api.services.feishu_webhook_service import build_report_card

        card = build_report_card(
            symbol="600519.SH", trade_date="2026-07-23", direction="看多",
        )
        assert card["card"]["header"]["template"] == "green"


class TestBuildDraftCard:
    def test_single_draft(self):
        from api.services.feishu_webhook_service import build_draft_card

        drafts = [{
            "symbol": "600519.SH",
            "name": "贵州茅台",
            "event_type": "holdings_risk",
            "reason": "跌破关键支撑位",
            "priority": "P0",
            "as_of": "2026-07-23 10:00:00",
        }]
        card = build_draft_card("TradingAgents", drafts, channel_label="盘中提醒")
        assert card["msg_type"] == "interactive"
        content = card["card"]["elements"][0]["text"]["content"]
        assert "600519.SH" in content
        assert "贵州茅台" in content
        assert "P0" in content

    def test_multiple_drafts(self):
        from api.services.feishu_webhook_service import build_draft_card

        drafts = [
            {"symbol": "600519.SH", "name": "贵州茅台", "priority": "P0", "event_type": "e1", "reason": "r1", "as_of": ""},
            {"symbol": "000001.SZ", "name": "平安银行", "priority": "P1", "event_type": "e2", "reason": "r2", "as_of": ""},
        ]
        card = build_draft_card("TradingAgents", drafts, channel_label="日报摘要")
        content = card["card"]["elements"][0]["text"]["content"]
        assert "600519.SH" in content
        assert "000001.SZ" in content

    def test_more_than_10_truncated(self):
        from api.services.feishu_webhook_service import build_draft_card

        drafts = [
            {"symbol": f"00000{i}.SZ", "name": f"stock{i}", "priority": "P2",
             "event_type": f"e{i}", "reason": f"r{i}", "as_of": ""}
            for i in range(15)
        ]
        card = build_draft_card("TradingAgents", drafts)
        content = card["card"]["elements"][0]["text"]["content"]
        assert "共 15 条" in content


class TestBuildTestMessage:
    def test_structure(self):
        from api.services.feishu_webhook_service import build_test_message

        msg = build_test_message()
        assert msg["msg_type"] == "text"
        assert "Warmup" in msg["content"]["text"]


# ---------------------------------------------------------------------------
# Env var tests
# ---------------------------------------------------------------------------


class TestEnvVars:
    def test_enabled_when_configured(self):
        with patch.dict(os.environ, {
            "FEISHU_WEBHOOK_URL": "https://open.feishu.cn/open-apis/bot/v2/hook/abc",
            "FEISHU_WEBHOOK_ENABLED": "true",
        }):
            from api.services.feishu_webhook_service import is_feishu_webhook_enabled
            assert is_feishu_webhook_enabled() is True

    def test_disabled_when_no_url(self):
        with patch.dict(os.environ, {"FEISHU_WEBHOOK_URL": "", "FEISHU_WEBHOOK_ENABLED": "true"}, clear=False):
            from api.services.feishu_webhook_service import is_feishu_webhook_enabled
            assert is_feishu_webhook_enabled() is False

    def test_disabled_when_explicitly_off(self):
        with patch.dict(os.environ, {
            "FEISHU_WEBHOOK_URL": "https://open.feishu.cn/open-apis/bot/v2/hook/abc",
            "FEISHU_WEBHOOK_ENABLED": "false",
        }):
            from api.services.feishu_webhook_service import is_feishu_webhook_enabled
            assert is_feishu_webhook_enabled() is False

    def test_get_url_returns_none_when_empty(self):
        with patch.dict(os.environ, {"FEISHU_WEBHOOK_URL": ""}):
            from api.services.feishu_webhook_service import get_feishu_webhook_url
            assert get_feishu_webhook_url() is None

    def test_get_url_returns_configured(self):
        url = "https://open.feishu.cn/open-apis/bot/v2/hook/abc"
        with patch.dict(os.environ, {"FEISHU_WEBHOOK_URL": url}):
            from api.services.feishu_webhook_service import get_feishu_webhook_url
            assert get_feishu_webhook_url() == url


# ---------------------------------------------------------------------------
# HTTP send tests
# ---------------------------------------------------------------------------


class TestSendMessage:
    def test_success(self):
        from api.services.feishu_webhook_service import send_message

        mock_response = MagicMock()
        mock_response.json.return_value = {"code": 0, "msg": "success"}
        mock_response.raise_for_status.return_value = None

        with patch("api.services.feishu_webhook_service.requests.post", return_value=mock_response):
            result = send_message({"msg_type": "text", "content": {"text": "hi"}}, "https://open.feishu.cn/open-apis/bot/v2/hook/abc")
            assert result is True

    def test_api_error(self):
        from api.services.feishu_webhook_service import send_message

        mock_response = MagicMock()
        mock_response.json.return_value = {"code": 19001, "msg": "invalid token"}
        mock_response.raise_for_status.return_value = None

        with patch("api.services.feishu_webhook_service.requests.post", return_value=mock_response):
            result = send_message({"msg_type": "text", "content": {"text": "hi"}}, "https://open.feishu.cn/open-apis/bot/v2/hook/abc")
            assert result is False

    def test_non_json_response(self):
        from api.services.feishu_webhook_service import send_message

        mock_response = MagicMock()
        mock_response.json.side_effect = ValueError("not json")
        mock_response.text = "Internal Server Error"
        mock_response.raise_for_status.return_value = None

        with patch("api.services.feishu_webhook_service.requests.post", return_value=mock_response):
            result = send_message({"msg_type": "text", "content": {"text": "hi"}}, "https://open.feishu.cn/open-apis/bot/v2/hook/abc")
            assert result is False

    def test_network_error_raises(self):
        from api.services.feishu_webhook_service import send_message

        with patch("api.services.feishu_webhook_service.requests.post", side_effect=ConnectionError("timeout")):
            with pytest.raises(ConnectionError):
                send_message({"msg_type": "text", "content": {"text": "hi"}}, "https://open.feishu.cn/open-apis/bot/v2/hook/abc")


# ---------------------------------------------------------------------------
# Async send_with_retry tests
# ---------------------------------------------------------------------------


class TestSendWithRetry:
    def test_success_first_try(self):
        import asyncio
        from api.services.feishu_webhook_service import send_with_retry

        with patch("api.services.feishu_webhook_service.send_message", return_value=True):
            result = asyncio.get_event_loop().run_until_complete(
                send_with_retry({"msg_type": "text"}, "https://open.feishu.cn/open-apis/bot/v2/hook/abc")
            )
            assert result is True

    def test_retry_on_failure_then_success(self):
        import asyncio
        from api.services.feishu_webhook_service import send_with_retry

        call_count = 0
        def mock_send(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return call_count >= 2

        with patch("api.services.feishu_webhook_service.send_message", side_effect=mock_send):
            with patch("api.services.feishu_webhook_service.asyncio.sleep"):
                result = asyncio.get_event_loop().run_until_complete(
                    send_with_retry(
                        {"msg_type": "text"},
                        "https://open.feishu.cn/open-apis/bot/v2/hook/abc",
                        max_retries=2,
                    )
                )
                assert result is True
                assert call_count == 2


# ---------------------------------------------------------------------------
# Constants and exports
# ---------------------------------------------------------------------------


class TestConstants:
    def test_exports(self):
        from api.services.feishu_webhook_service import __all__
        assert "send_message" in __all__
        assert "is_feishu_webhook_enabled" in __all__
        assert "normalize_webhook_url" in __all__
        assert "build_report_card" in __all__
        assert "build_draft_card" in __all__
        assert "send_with_retry" in __all__
