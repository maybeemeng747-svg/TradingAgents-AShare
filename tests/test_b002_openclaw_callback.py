"""Tests for api.services.openclaw_callback_service (B-002).

Covers:
- Payload building with full / partial / empty result_data
- Env var handling (OPENCLAW_CALLBACK_URL, OPENCLAW_CALLBACK_ENABLED, OPENCLAW_CALLBACK_TOKEN)
- HTTP callback send (success, failure, retry, timeout)
- Disabled state graceful no-op
- Fire-and-forget notification entry point
- Schema version and source constants
"""
from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_FULL_RESULT_DATA = {
    "action_label": "条件入场",
    "decision": "BUY",
    "research_direction": "看多",
    "direction": "偏多",
    "execution_action": "ENTER",
    "confidence": 78,
    "final_trade_decision": "综合技术面突破与基本面支撑，建议逢低分批建仓。",
    "trader_investment_plan": None,
    "investment_plan": None,
    "risk_items": [
        {"name": "估值偏高", "level": "medium", "description": "PE 处于历史 75 分位"},
        {"name": "外资流出", "level": "low", "description": "近 5 日北向资金小幅净流出"},
    ],
    "key_metrics": [
        {"name": "PE", "value": "32.5x", "status": "neutral"},
        {"name": "ROE", "value": "31.2%", "status": "good"},
    ],
    "readiness_score": {
        "data_completeness": 85,
        "confidence": "高",
    },
}

_MINIMAL_RESULT_DATA = {
    "direction": "中性",
    "confidence": 50,
}


# ---------------------------------------------------------------------------
# Test: build_callback_payload
# ---------------------------------------------------------------------------


class TestBuildCallbackPayload:
    """Payload structure and field extraction tests."""

    def test_full_result_data_extracts_all_fields(self):
        from api.services.openclaw_callback_service import build_callback_payload

        payload = build_callback_payload(
            report_id="rpt-001",
            symbol="600519.SH",
            trade_date="2026-07-23",
            user_id="user-1",
            horizon="short",
            source="scheduled",
            result_data=_FULL_RESULT_DATA,
        )

        # Top-level structure
        assert payload["schema_version"] == "1.1.0"
        assert payload["source"] == "tradingagents-scheduler"
        assert payload["event"] == "scheduled_analysis_completed"
        assert "timestamp" in payload
        assert payload["user_id"] == "user-1"

        # Report block
        assert payload["report"]["id"] == "rpt-001"
        assert payload["report"]["symbol"] == "600519.SH"
        assert payload["report"]["trade_date"] == "2026-07-23"
        assert payload["report"]["horizon"] == "short"
        assert payload["report"]["source"] == "scheduled"

        # Decision block — action_label preferred over decision
        assert payload["decision"]["action"] == "条件入场"
        assert payload["decision"]["direction"] == "看多"
        assert payload["decision"]["execution_action"] == "ENTER"
        assert payload["decision"]["confidence"] == 78

        # Summary
        assert "逢低分批建仓" in payload["summary"]

        # Risk items (capped at 5)
        assert len(payload["risk_items"]) == 2
        assert payload["risk_items"][0]["name"] == "估值偏高"
        assert payload["risk_items"][0]["level"] == "medium"

        # Key metrics (capped at 8)
        assert len(payload["key_metrics"]) == 2
        assert payload["key_metrics"][0]["name"] == "PE"
        assert payload["key_metrics"][0]["value"] == "32.5x"

        # Readiness score
        assert payload["readiness_score"]["status"] == "available"
        assert payload["readiness_score"]["data_completeness"] == 85
        assert payload["readiness_score"]["confidence"] == "高"
        assert payload["readiness_score"]["source"] == "readiness_score"

    def test_minimal_result_data(self):
        from api.services.openclaw_callback_service import build_callback_payload

        payload = build_callback_payload(
            report_id="rpt-002",
            symbol="000001.SZ",
            trade_date="2026-07-23",
            result_data=_MINIMAL_RESULT_DATA,
        )

        assert payload["decision"]["action"] == ""  # no action_label or decision
        assert payload["decision"]["direction"] == "中性"
        assert payload["decision"]["confidence"] == 50
        assert payload["summary"] == ""  # no trade decision text
        assert payload["risk_items"] == []
        assert payload["key_metrics"] == []
        # [B-002-R2] no source → explicit status, never omitted/empty-faked
        assert payload["readiness_score"] == {"status": "not_available"}

    def test_none_result_data(self):
        from api.services.openclaw_callback_service import build_callback_payload

        payload = build_callback_payload(
            report_id="rpt-003",
            symbol="300750.SZ",
            trade_date="2026-07-23",
            result_data=None,
        )

        assert payload["decision"]["action"] == ""
        assert payload["decision"]["direction"] == ""
        assert payload["decision"]["confidence"] is None
        assert payload["summary"] == ""
        assert payload["risk_items"] == []
        assert payload["key_metrics"] == []

    def test_empty_result_data(self):
        from api.services.openclaw_callback_service import build_callback_payload

        payload = build_callback_payload(
            report_id="rpt-004",
            symbol="601398.SH",
            trade_date="2026-07-23",
            result_data={},
        )

        assert payload["report"]["symbol"] == "601398.SH"
        assert payload["decision"]["action"] == ""
        assert payload["summary"] == ""

    def test_user_id_included_when_provided(self):
        from api.services.openclaw_callback_service import build_callback_payload

        payload = build_callback_payload(
            report_id="rpt-005",
            symbol="600519.SH",
            trade_date="2026-07-23",
            user_id="u123",
        )
        assert payload["user_id"] == "u123"

    def test_user_id_omitted_when_empty(self):
        from api.services.openclaw_callback_service import build_callback_payload

        payload = build_callback_payload(
            report_id="rpt-006",
            symbol="600519.SH",
            trade_date="2026-07-23",
            user_id="",
        )
        assert "user_id" not in payload

    def test_horizon_and_source_defaults(self):
        from api.services.openclaw_callback_service import build_callback_payload

        payload = build_callback_payload(
            report_id="rpt-007",
            symbol="600519.SH",
            trade_date="2026-07-23",
        )
        assert payload["report"]["horizon"] == "short"
        assert payload["report"]["source"] == "scheduled"

    def test_risk_items_capped_at_5(self):
        from api.services.openclaw_callback_service import build_callback_payload

        many_risks = [{"name": f"r{i}", "level": "low"} for i in range(10)]
        payload = build_callback_payload(
            report_id="rpt-008",
            symbol="600519.SH",
            trade_date="2026-07-23",
            result_data={"risk_items": many_risks},
        )
        assert len(payload["risk_items"]) == 5

    def test_key_metrics_capped_at_8(self):
        from api.services.openclaw_callback_service import build_callback_payload

        many_metrics = [{"name": f"m{i}", "value": str(i), "status": "neutral"} for i in range(12)]
        payload = build_callback_payload(
            report_id="rpt-009",
            symbol="600519.SH",
            trade_date="2026-07-23",
            result_data={"key_metrics": many_metrics},
        )
        assert len(payload["key_metrics"]) == 8

    def test_fallback_to_decision_when_no_action_label(self):
        from api.services.openclaw_callback_service import build_callback_payload

        payload = build_callback_payload(
            report_id="rpt-010",
            symbol="600519.SH",
            trade_date="2026-07-23",
            result_data={"decision": "HOLD", "direction": "看空"},
        )
        assert payload["decision"]["action"] == "HOLD"
        assert payload["decision"]["direction"] == "看空"

    def test_fallback_to_direction_when_no_research_direction(self):
        from api.services.openclaw_callback_service import build_callback_payload

        payload = build_callback_payload(
            report_id="rpt-011",
            symbol="600519.SH",
            trade_date="2026-07-23",
            result_data={"direction": "偏多"},
        )
        assert payload["decision"]["direction"] == "偏多"

    def test_summary_from_trader_investment_plan(self):
        from api.services.openclaw_callback_service import build_callback_payload

        payload = build_callback_payload(
            report_id="rpt-012",
            symbol="600519.SH",
            trade_date="2026-07-23",
            result_data={"trader_investment_plan": "建议观望，等待回调。"},
        )
        assert "等待回调" in payload["summary"]

    def test_summary_from_investment_plan(self):
        from api.services.openclaw_callback_service import build_callback_payload

        payload = build_callback_payload(
            report_id="rpt-013",
            symbol="600519.SH",
            trade_date="2026-07-23",
            result_data={"investment_plan": "长期持有计划。"},
        )
        assert "长期持有" in payload["summary"]

    def test_summary_clipping(self):
        from api.services.openclaw_callback_service import build_callback_payload

        long_text = "x" * 1000
        payload = build_callback_payload(
            report_id="rpt-014",
            symbol="600519.SH",
            trade_date="2026-07-23",
            result_data={"final_trade_decision": long_text},
        )
        assert len(payload["summary"]) <= 500

    def test_json_serializable(self):
        from api.services.openclaw_callback_service import build_callback_payload

        payload = build_callback_payload(
            report_id="rpt-015",
            symbol="600519.SH",
            trade_date="2026-07-23",
            result_data=_FULL_RESULT_DATA,
        )
        # Must be JSON-serializable
        serialized = json.dumps(payload, ensure_ascii=False)
        assert "600519" in serialized

    def test_malformed_risk_items_ignored(self):
        from api.services.openclaw_callback_service import build_callback_payload

        payload = build_callback_payload(
            report_id="rpt-016",
            symbol="600519.SH",
            trade_date="2026-07-23",
            result_data={"risk_items": ["not_a_dict", 42, None, {"name": "ok", "level": "high"}]},
        )
        # Only dict items are extracted; non-dicts are skipped
        assert len(payload["risk_items"]) == 1
        assert payload["risk_items"][0]["name"] == "ok"

    def test_malformed_key_metrics_ignored(self):
        from api.services.openclaw_callback_service import build_callback_payload

        payload = build_callback_payload(
            report_id="rpt-017",
            symbol="600519.SH",
            trade_date="2026-07-23",
            result_data={"key_metrics": ["bad", {"name": "ok", "value": "1", "status": "good"}]},
        )
        # Only dict items are extracted; non-dicts are skipped
        assert len(payload["key_metrics"]) == 1
        assert payload["key_metrics"][0]["name"] == "ok"

    def test_report_obj_prefers_report_fields(self):
        from api.services.openclaw_callback_service import build_callback_payload

        payload = build_callback_payload(
            report_id="rpt-018",
            symbol="002409.SZ",
            trade_date="2026-07-23",
            report_obj={
                "action_label": "条件确认",
                "research_direction": "偏空",
                "execution_action": "REDUCE",
                "confidence": 66,
                "final_trade_decision": "优先等待止损。",
                "risk_items": [{"name": "来自 report_obj", "level": "high"}],
                "key_metrics": [{"name": "PE", "value": "99", "status": "warning"}],
                "readiness_score": {
                    "data_completeness": 91,
                    "confidence": "中",
                },
            },
            result_data={
                "action_label": "条件入场",
                "research_direction": "偏多",
                "execution_action": "ENTER",
                "confidence": 88,
                "final_trade_decision": "来自 result_data",
                "risk_items": [{"name": "来自 result_data", "level": "low"}],
                "key_metrics": [{"name": "ROE", "value": "12", "status": "good"}],
                "readiness_score": {"data_completeness": 10, "confidence": "低"},
            },
        )

        assert payload["decision"]["action"] == "条件确认"
        assert payload["decision"]["direction"] == "偏空"
        assert payload["decision"]["execution_action"] == "REDUCE"
        assert payload["decision"]["confidence"] == 66
        assert "优先等待止损" in payload["summary"]
        assert payload["risk_items"][0]["name"] == "来自 report_obj"
        assert payload["key_metrics"][0]["name"] == "PE"
        assert payload["readiness_score"]["status"] == "available"
        assert payload["readiness_score"]["data_completeness"] == 91
        assert payload["readiness_score"]["confidence"] == "中"

    def test_report_obj_empty_falls_back_to_result_data(self):
        from api.services.openclaw_callback_service import build_callback_payload

        payload = build_callback_payload(
            report_id="rpt-019",
            symbol="002409.SZ",
            trade_date="2026-07-23",
            report_obj={},
            result_data={
                "action_label": "条件入场",
                "research_direction": "偏多",
                "execution_action": "ENTER",
                "confidence": 77,
                "final_trade_decision": "来自 result_data",
            },
        )

        assert payload["decision"]["action"] == "条件入场"
        assert payload["decision"]["direction"] == "偏多"
        assert payload["decision"]["execution_action"] == "ENTER"
        assert payload["decision"]["confidence"] == 77
        assert "来自 result_data" in payload["summary"]


# ---------------------------------------------------------------------------
# Test: is_openclaw_callback_enabled
# ---------------------------------------------------------------------------


class TestIsOpenclawCallbackEnabled:
    """Env var combination tests."""

    def test_disabled_by_default(self, monkeypatch):
        from api.services.openclaw_callback_service import is_openclaw_callback_enabled

        monkeypatch.delenv("OPENCLAW_CALLBACK_ENABLED", raising=False)
        monkeypatch.delenv("OPENCLAW_CALLBACK_URL", raising=False)
        assert is_openclaw_callback_enabled() is False

    def test_enabled_but_no_url(self, monkeypatch):
        from api.services.openclaw_callback_service import is_openclaw_callback_enabled

        monkeypatch.setenv("OPENCLAW_CALLBACK_ENABLED", "true")
        monkeypatch.delenv("OPENCLAW_CALLBACK_URL", raising=False)
        assert is_openclaw_callback_enabled() is False

    def test_url_set_but_not_enabled(self, monkeypatch):
        from api.services.openclaw_callback_service import is_openclaw_callback_enabled

        monkeypatch.setenv("OPENCLAW_CALLBACK_ENABLED", "false")
        monkeypatch.setenv("OPENCLAW_CALLBACK_URL", "https://openclaw.example.com/webhook")
        assert is_openclaw_callback_enabled() is False

    def test_both_enabled(self, monkeypatch):
        from api.services.openclaw_callback_service import is_openclaw_callback_enabled

        monkeypatch.setenv("OPENCLAW_CALLBACK_ENABLED", "true")
        monkeypatch.setenv("OPENCLAW_CALLBACK_URL", "https://openclaw.example.com/webhook")
        assert is_openclaw_callback_enabled() is True

    def test_enabled_with_truthy_values(self, monkeypatch):
        from api.services.openclaw_callback_service import is_openclaw_callback_enabled

        monkeypatch.setenv("OPENCLAW_CALLBACK_URL", "https://openclaw.example.com/webhook")
        for val in ("1", "true", "TRUE", "yes", "YES", "on", "ON"):
            monkeypatch.setenv("OPENCLAW_CALLBACK_ENABLED", val)
            assert is_openclaw_callback_enabled() is True, f"Expected True for {val!r}"

    def test_enabled_with_falsy_values(self, monkeypatch):
        from api.services.openclaw_callback_service import is_openclaw_callback_enabled

        monkeypatch.setenv("OPENCLAW_CALLBACK_URL", "https://openclaw.example.com/webhook")
        for val in ("0", "false", "FALSE", "no", "NO", "off", "OFF", ""):
            monkeypatch.setenv("OPENCLAW_CALLBACK_ENABLED", val)
            assert is_openclaw_callback_enabled() is False, f"Expected False for {val!r}"

    def test_empty_url_treated_as_disabled(self, monkeypatch):
        from api.services.openclaw_callback_service import is_openclaw_callback_enabled

        monkeypatch.setenv("OPENCLAW_CALLBACK_ENABLED", "true")
        monkeypatch.setenv("OPENCLAW_CALLBACK_URL", "   ")
        assert is_openclaw_callback_enabled() is False


# ---------------------------------------------------------------------------
# Test: send_callback
# ---------------------------------------------------------------------------


class TestSendCallback:
    """HTTP callback send tests with mocked requests.post."""

    def _make_payload(self):
        from api.services.openclaw_callback_service import build_callback_payload
        return build_callback_payload(
            report_id="rpt-100",
            symbol="600519.SH",
            trade_date="2026-07-23",
            result_data=_FULL_RESULT_DATA,
        )

    def test_send_success(self, monkeypatch):
        from api.services.openclaw_callback_service import send_callback

        monkeypatch.setenv("OPENCLAW_CALLBACK_URL", "https://openclaw.example.com/webhook")

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("api.services.openclaw_callback_service.requests.post", return_value=mock_response) as mock_post:
            result = send_callback(self._make_payload())

        assert result is True
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "openclaw.example.com" in call_args[0][0] or "openclaw.example.com" in call_args[1].get("data", b"").decode()

    def test_send_success_201(self, monkeypatch):
        from api.services.openclaw_callback_service import send_callback

        monkeypatch.setenv("OPENCLAW_CALLBACK_URL", "https://openclaw.example.com/webhook")

        mock_response = MagicMock()
        mock_response.status_code = 201

        with patch("api.services.openclaw_callback_service.requests.post", return_value=mock_response):
            result = send_callback(self._make_payload())

        assert result is True

    def test_send_no_url_returns_false(self, monkeypatch):
        from api.services.openclaw_callback_service import send_callback

        monkeypatch.delenv("OPENCLAW_CALLBACK_URL", raising=False)

        result = send_callback(self._make_payload())
        assert result is False

    def test_send_http_error_returns_false(self, monkeypatch):
        from api.services.openclaw_callback_service import send_callback

        monkeypatch.setenv("OPENCLAW_CALLBACK_URL", "https://openclaw.example.com/webhook")

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        with patch("api.services.openclaw_callback_service.requests.post", return_value=mock_response), \
             patch("api.services.openclaw_callback_service.time.sleep"):
            result = send_callback(self._make_payload())

        assert result is False

    def test_send_request_exception_returns_false(self, monkeypatch):
        import requests as real_requests
        from api.services.openclaw_callback_service import send_callback

        monkeypatch.setenv("OPENCLAW_CALLBACK_URL", "https://openclaw.example.com/webhook")

        with patch("api.services.openclaw_callback_service.requests.post", side_effect=real_requests.ConnectionError("refused")), \
             patch("api.services.openclaw_callback_service.time.sleep"):
            result = send_callback(self._make_payload())

        assert result is False

    def test_send_includes_auth_header(self, monkeypatch):
        from api.services.openclaw_callback_service import send_callback

        monkeypatch.setenv("OPENCLAW_CALLBACK_URL", "https://openclaw.example.com/webhook")
        monkeypatch.setenv("OPENCLAW_CALLBACK_TOKEN", "test-token-123")

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("api.services.openclaw_callback_service.requests.post", return_value=mock_response) as mock_post:
            send_callback(self._make_payload())

        headers = mock_post.call_args[1].get("headers", {})
        assert headers.get("Authorization") == "Bearer test-token-123"

    def test_send_no_auth_header_when_token_empty(self, monkeypatch):
        from api.services.openclaw_callback_service import send_callback

        monkeypatch.setenv("OPENCLAW_CALLBACK_URL", "https://openclaw.example.com/webhook")
        monkeypatch.delenv("OPENCLAW_CALLBACK_TOKEN", raising=False)

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("api.services.openclaw_callback_service.requests.post", return_value=mock_response) as mock_post:
            send_callback(self._make_payload())

        headers = mock_post.call_args[1].get("headers", {})
        assert "Authorization" not in headers

    def test_send_json_body_contains_symbol(self, monkeypatch):
        from api.services.openclaw_callback_service import send_callback

        monkeypatch.setenv("OPENCLAW_CALLBACK_URL", "https://openclaw.example.com/webhook")

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("api.services.openclaw_callback_service.requests.post", return_value=mock_response) as mock_post:
            send_callback(self._make_payload())

        body = mock_post.call_args[1].get("data", b"")
        if isinstance(body, bytes):
            body = body.decode("utf-8")
        parsed = json.loads(body)
        assert parsed["report"]["symbol"] == "600519.SH"

    def test_send_retry_on_failure(self, monkeypatch):
        from api.services.openclaw_callback_service import send_callback

        monkeypatch.setenv("OPENCLAW_CALLBACK_URL", "https://openclaw.example.com/webhook")

        mock_fail = MagicMock()
        mock_fail.status_code = 503
        mock_fail.text = "Service Unavailable"

        mock_ok = MagicMock()
        mock_ok.status_code = 200

        with patch("api.services.openclaw_callback_service.requests.post", side_effect=[mock_fail, mock_ok]), \
             patch("api.services.openclaw_callback_service.time.sleep"):
            result = send_callback(self._make_payload())

        assert result is True

    def test_send_exhausts_retries(self, monkeypatch):
        from api.services.openclaw_callback_service import send_callback

        monkeypatch.setenv("OPENCLAW_CALLBACK_URL", "https://openclaw.example.com/webhook")

        mock_fail = MagicMock()
        mock_fail.status_code = 500
        mock_fail.text = "error"

        with patch("api.services.openclaw_callback_service.requests.post", return_value=mock_fail), \
             patch("api.services.openclaw_callback_service.time.sleep"):
            result = send_callback(self._make_payload())

        assert result is False


# ---------------------------------------------------------------------------
# Test: send_callback_async
# ---------------------------------------------------------------------------


class TestSendCallbackAsync:
    """Async wrapper tests."""

    def test_async_delegates_to_sync(self, monkeypatch):
        from api.services.openclaw_callback_service import send_callback_async

        monkeypatch.setenv("OPENCLAW_CALLBACK_URL", "https://openclaw.example.com/webhook")

        with patch("api.services.openclaw_callback_service.send_callback", return_value=True) as mock_send:
            result = asyncio.run(send_callback_async({"test": True}))

        assert result is True
        mock_send.assert_called_once_with({"test": True})


# ---------------------------------------------------------------------------
# Test: notify_openclaw_on_report_completion
# ---------------------------------------------------------------------------


class TestNotifyOpenclawOnReportCompletion:
    """Fire-and-forget entry point tests."""

    def test_does_nothing_when_disabled(self, monkeypatch):
        from api.services.openclaw_callback_service import notify_openclaw_on_report_completion

        monkeypatch.setenv("OPENCLAW_CALLBACK_ENABLED", "false")
        monkeypatch.delenv("OPENCLAW_CALLBACK_URL", raising=False)

        # Should not raise
        notify_openclaw_on_report_completion(
            report_id="rpt-200",
            symbol="600519.SH",
            trade_date="2026-07-23",
            result_data=_FULL_RESULT_DATA,
        )

    def test_sends_when_enabled(self, monkeypatch):
        from api.services.openclaw_callback_service import notify_openclaw_on_report_completion

        monkeypatch.setenv("OPENCLAW_CALLBACK_ENABLED", "true")
        monkeypatch.setenv("OPENCLAW_CALLBACK_URL", "https://openclaw.example.com/webhook")

        with patch("api.services.openclaw_callback_service.send_callback", return_value=True) as mock_send:
            # In sync context (no running loop), it spawns a daemon thread
            notify_openclaw_on_report_completion(
                report_id="rpt-201",
                symbol="000001.SZ",
                trade_date="2026-07-23",
                user_id="u1",
                horizon="medium",
                source="scheduled_manual",
                result_data=_MINIMAL_RESULT_DATA,
            )

        # Fire-and-forget: the thread may or may not have run yet
        # We just verify no exception was raised


# ---------------------------------------------------------------------------
# Test: constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_schema_version(self):
        from api.services.openclaw_callback_service import CALLBACK_SCHEMA_VERSION
        assert CALLBACK_SCHEMA_VERSION == "1.1.0"

    def test_source(self):
        from api.services.openclaw_callback_service import CALLBACK_SOURCE
        assert CALLBACK_SOURCE == "tradingagents-scheduler"


# ---------------------------------------------------------------------------
# Test: __all__ exports
# ---------------------------------------------------------------------------


class TestExports:
    def test_all_exports_present(self):
        import api.services.openclaw_callback_service as mod

        expected = {
            "build_callback_payload",
            "send_callback",
            "send_callback_async",
            "is_openclaw_callback_enabled",
            "notify_openclaw_on_report_completion",
            "CALLBACK_SCHEMA_VERSION",
            "CALLBACK_SOURCE",
        }
        assert set(mod.__all__) == expected


# ---------------------------------------------------------------------------
# [B-002-R2] Test: readiness_score real persisted sources
# ---------------------------------------------------------------------------

_C008_TEXT = (
    "综合技术面突破与基本面支撑，建议逢低分批建仓。\n\n"
    "📋 [C-008] 执行就绪度评分\n"
    "- 数据完整度：87%\n"
    "- 置信度：高\n"
    "- 报告等级：高质量报告 · 已持仓\n"
    "- 允许动作：持有, 加仓\n"
    "- 禁止动作：追涨\n"
    "- 总结：数据完整，置信度高。"
)


class TestReadinessScoreSources:
    """[B-002-R2] readiness_score must come from a real persisted source."""

    def test_parsed_from_c008_block_in_report_obj_text(self):
        from api.services.openclaw_callback_service import build_callback_payload

        payload = build_callback_payload(
            report_id="rpt-r2-1",
            symbol="600519.SH",
            trade_date="2026-08-17",
            report_obj={"final_trade_decision": _C008_TEXT},
            result_data={},
        )

        readiness = payload["readiness_score"]
        assert readiness["status"] == "available"
        assert readiness["data_completeness"] == 87
        assert readiness["confidence"] == "高"
        assert readiness["source"] == "final_trade_decision"

    def test_parsed_from_c008_block_in_result_data_text(self):
        from api.services.openclaw_callback_service import build_callback_payload

        payload = build_callback_payload(
            report_id="rpt-r2-2",
            symbol="600519.SH",
            trade_date="2026-08-17",
            result_data={"final_trade_decision": _C008_TEXT},
        )

        readiness = payload["readiness_score"]
        assert readiness["status"] == "available"
        assert readiness["data_completeness"] == 87
        assert readiness["confidence"] == "高"

    def test_structured_field_preferred_over_text(self):
        from api.services.openclaw_callback_service import build_callback_payload

        payload = build_callback_payload(
            report_id="rpt-r2-3",
            symbol="600519.SH",
            trade_date="2026-08-17",
            report_obj={
                "final_trade_decision": _C008_TEXT,
                "readiness_score": {"data_completeness": 40, "confidence": "低"},
            },
        )

        readiness = payload["readiness_score"]
        assert readiness["status"] == "available"
        assert readiness["data_completeness"] == 40
        assert readiness["confidence"] == "低"
        assert readiness["source"] == "readiness_score"

    def test_text_without_c008_marker_is_not_trusted(self):
        from api.services.openclaw_callback_service import build_callback_payload

        # Numbers appear outside the official block — must not be used.
        text = "数据完整度：99%，置信度：高，但这是正文描述而非评分块。"
        payload = build_callback_payload(
            report_id="rpt-r2-4",
            symbol="600519.SH",
            trade_date="2026-08-17",
            result_data={"final_trade_decision": text},
        )

        assert payload["readiness_score"] == {"status": "not_available"}

    def test_fallback_rejects_inline_prose_c008_mention(self):
        from api.services.openclaw_callback_service import build_callback_payload

        # [B-002-R2-round3] codex review example: a bare inline [C-008]
        # mention with valid-looking inline values (no persisted offset, no
        # deterministic heading/bullet structure) must not be published.
        text = "模型结论：参考 [C-008] 数据完整度：99% 置信度：高，建议执行。"
        payload = build_callback_payload(
            report_id="rpt-r2-4b",
            symbol="600519.SH",
            trade_date="2026-08-17",
            result_data={"final_trade_decision": text},
        )

        assert payload["readiness_score"] == {"status": "not_available"}

    def test_fallback_rejects_heading_with_inline_values(self):
        from api.services.openclaw_callback_service import build_callback_payload

        # Heading present but values inline on the same line (not bullet
        # field rows) → not the deterministic system-block structure.
        text = "📋 [C-008] 执行就绪度评分 数据完整度：99% 置信度：高"
        payload = build_callback_payload(
            report_id="rpt-r2-4c",
            symbol="600519.SH",
            trade_date="2026-08-17",
            result_data={"final_trade_decision": text},
        )

        assert payload["readiness_score"] == {"status": "not_available"}

    def test_offset_out_of_range_completeness_not_available(self):
        from api.services.openclaw_callback_service import build_callback_payload

        # v1.1 contract: available requires a valid (0-100 int) completeness
        # AND a valid confidence; out-of-range values fail closed.
        text = (
            "📋 [C-008] 执行就绪度评分\n"
            "- 数据完整度：150%\n"
            "- 置信度：中\n"
            "- 总结：数据基本完整，置信度中等。"
        )
        payload = build_callback_payload(
            report_id="rpt-r2-5",
            symbol="600519.SH",
            trade_date="2026-08-17",
            result_data={"final_trade_decision": text},
        )

        assert payload["readiness_score"] == {"status": "not_available"}

    def test_empty_structured_dict_falls_through_to_text(self):
        from api.services.openclaw_callback_service import build_callback_payload

        payload = build_callback_payload(
            report_id="rpt-r2-6",
            symbol="600519.SH",
            trade_date="2026-08-17",
            report_obj={"readiness_score": {}, "final_trade_decision": _C008_TEXT},
        )

        readiness = payload["readiness_score"]
        assert readiness["status"] == "available"
        assert readiness["data_completeness"] == 87
        assert readiness["source"] == "final_trade_decision"

    def test_malformed_marker_only_block_is_not_available(self):
        from api.services.openclaw_callback_service import build_callback_payload

        text = "📋 [C-008] 执行就绪度评分\n（内容缺失）"
        payload = build_callback_payload(
            report_id="rpt-r2-7",
            symbol="600519.SH",
            trade_date="2026-08-17",
            result_data={"final_trade_decision": text},
        )

        assert payload["readiness_score"] == {"status": "not_available"}

    def test_readiness_key_always_present_and_json_serializable(self):
        from api.services.openclaw_callback_service import build_callback_payload

        for result_data in (None, {}, {"direction": "中性"}):
            payload = build_callback_payload(
                report_id="rpt-r2-8",
                symbol="600519.SH",
                trade_date="2026-08-17",
                result_data=result_data,
            )
            assert "readiness_score" in payload
            json.dumps(payload, ensure_ascii=False)


# ---------------------------------------------------------------------------
# [B-002-R2-fix] Test: readiness parsing restricted to the trusted system tail
# ---------------------------------------------------------------------------

# Model-authored body mentions [C-008] with fabricated values BEFORE the
# system-appended diagnostics (production order in risk_manager).
_MODEL_BODY_FAKE_C008 = (
    "模型正文引用 [C-008] 并声称数据完整度：12%，置信度：低。\n"
    "该引用并非系统评分块。"
)

_SYSTEM_TAIL = (
    "📋 [C-008] 执行就绪度评分\n"
    "- 数据完整度：87%\n"
    "- 置信度：高\n"
    "- 报告等级：高质量报告 · 已持仓\n"
    "- 总结：数据完整，置信度高。"
)


def _persisted_decision_text() -> tuple[str, int]:
    """Production-shaped text: model body + system tail, with persisted offset."""
    text = _MODEL_BODY_FAKE_C008 + "\n\n" + _SYSTEM_TAIL
    offset = len(_MODEL_BODY_FAKE_C008) + 2  # risk_manager: len(visible_body) + 2
    assert text[offset:].startswith("📋 [C-008]")
    return text, offset


class TestReadinessTrustedOffset:
    """[B-002-R2-fix] model-authored [C-008] mentions must never be published."""

    def test_valid_offset_publishes_authoritative_tail_values(self):
        from api.services.openclaw_callback_service import build_callback_payload

        text, offset = _persisted_decision_text()
        payload = build_callback_payload(
            report_id="rpt-r2f-1",
            symbol="600519.SH",
            trade_date="2026-08-17",
            result_data={
                "final_trade_decision": text,
                "metadata": {"system_diagnostics_offset": offset},
            },
        )

        readiness = payload["readiness_score"]
        assert readiness["status"] == "available"
        assert readiness["data_completeness"] == 87  # not the fabricated 12
        assert readiness["confidence"] == "高"  # not the fabricated 低
        assert readiness["source"] == "final_trade_decision"

    def test_valid_offset_via_report_obj_result_data(self):
        from api.services.openclaw_callback_service import build_callback_payload

        text, offset = _persisted_decision_text()
        payload = build_callback_payload(
            report_id="rpt-r2f-2",
            symbol="600519.SH",
            trade_date="2026-08-17",
            report_obj={
                "final_trade_decision": text,
                "result_data": {
                    "final_trade_decision": text,
                    "metadata": {"system_diagnostics_offset": offset},
                },
            },
            result_data={},
        )

        readiness = payload["readiness_score"]
        assert readiness["status"] == "available"
        assert readiness["data_completeness"] == 87
        assert readiness["confidence"] == "高"

    def test_valid_offset_but_tail_has_no_marker_is_not_available(self):
        from api.services.openclaw_callback_service import build_callback_payload

        # Offset points at a system tail without a C-008 block (e.g. legacy
        # diagnostics); the model-authored mention must not be published.
        text = _MODEL_BODY_FAKE_C008 + "\n\n📊 数据源可用性：\n- 全部正常"
        offset = len(_MODEL_BODY_FAKE_C008) + 2
        payload = build_callback_payload(
            report_id="rpt-r2f-3",
            symbol="600519.SH",
            trade_date="2026-08-17",
            result_data={
                "final_trade_decision": text,
                "metadata": {"system_diagnostics_offset": offset},
            },
        )

        assert payload["readiness_score"] == {"status": "not_available"}

    def test_offset_out_of_range_is_not_available(self):
        from api.services.openclaw_callback_service import build_callback_payload

        text, _ = _persisted_decision_text()
        payload = build_callback_payload(
            report_id="rpt-r2f-4",
            symbol="600519.SH",
            trade_date="2026-08-17",
            result_data={
                "final_trade_decision": text,
                "metadata": {"system_diagnostics_offset": len(text) + 50},
            },
        )

        assert payload["readiness_score"] == {"status": "not_available"}

    def test_offset_non_int_is_not_available(self):
        from api.services.openclaw_callback_service import build_callback_payload

        text, _ = _persisted_decision_text()
        for bad in ("42", 1.5, True):
            payload = build_callback_payload(
                report_id="rpt-r2f-5",
                symbol="600519.SH",
                trade_date="2026-08-17",
                result_data={
                    "final_trade_decision": text,
                    "metadata": {"system_diagnostics_offset": bad},
                },
            )
            assert payload["readiness_score"] == {"status": "not_available"}, repr(bad)

    def test_offset_explicit_null_fails_closed(self):
        from api.services.openclaw_callback_service import build_callback_payload

        # [B-002-R2-round2] explicit system_diagnostics_offset: null means the
        # persisted metadata is inconsistent with the text: present-but-invalid
        # must NOT fall back to the legacy last-[C-008] heuristic — otherwise a
        # malformed report whose model body ends with a fake [C-008] block
        # could publish fabricated readiness.
        text, _ = _persisted_decision_text()
        payload = build_callback_payload(
            report_id="rpt-r2f-5b",
            symbol="600519.SH",
            trade_date="2026-08-17",
            result_data={
                "final_trade_decision": text,
                "metadata": {"system_diagnostics_offset": None},
            },
        )

        assert payload["readiness_score"] == {"status": "not_available"}

    def test_offset_explicit_null_via_report_obj_result_data_fails_closed(self):
        from api.services.openclaw_callback_service import build_callback_payload

        text, _ = _persisted_decision_text()
        payload = build_callback_payload(
            report_id="rpt-r2f-5c",
            symbol="600519.SH",
            trade_date="2026-08-17",
            report_obj={
                "final_trade_decision": text,
                "result_data": {
                    "final_trade_decision": text,
                    "metadata": {"system_diagnostics_offset": None},
                },
            },
            result_data={},
        )

        assert payload["readiness_score"] == {"status": "not_available"}

    def test_offset_absent_metadata_dict_still_legacy_fallback(self):
        from api.services.openclaw_callback_service import build_callback_payload

        # metadata dict persisted WITHOUT the offset key = true absence
        # (legacy) → last-[C-008] fallback still applies.
        text, _ = _persisted_decision_text()
        payload = build_callback_payload(
            report_id="rpt-r2f-5d",
            symbol="600519.SH",
            trade_date="2026-08-17",
            result_data={
                "final_trade_decision": text,
                "metadata": {"other_key": 1},
            },
        )

        readiness = payload["readiness_score"]
        assert readiness["status"] == "available"
        assert readiness["data_completeness"] == 87
        assert readiness["confidence"] == "高"

    def test_offset_zero_parses_whole_text_as_system_tail(self):
        from api.services.openclaw_callback_service import _parse_readiness_from_text

        text = _SYSTEM_TAIL
        parsed = _parse_readiness_from_text(text, trusted_offset=0)
        assert parsed == {"data_completeness": 87, "confidence": "高"}

    def test_legacy_no_metadata_falls_back_to_last_c008_heading(self):
        from api.services.openclaw_callback_service import build_callback_payload

        # Legacy report without persisted offset: the system block is appended
        # last, so the last [C-008] heading is the authoritative one.
        text, _ = _persisted_decision_text()
        payload = build_callback_payload(
            report_id="rpt-r2f-6",
            symbol="600519.SH",
            trade_date="2026-08-17",
            result_data={"final_trade_decision": text},
        )

        readiness = payload["readiness_score"]
        assert readiness["status"] == "available"
        assert readiness["data_completeness"] == 87
        assert readiness["confidence"] == "高"

    def test_legacy_no_metadata_malformed_last_block_not_available(self):
        from api.services.openclaw_callback_service import build_callback_payload

        # The system-appended block is malformed; the earlier model-authored
        # mention with values must NOT be selected by the fallback.
        text = _MODEL_BODY_FAKE_C008 + "\n\n📋 [C-008] 执行就绪度评分\n（内容缺失）"
        payload = build_callback_payload(
            report_id="rpt-r2f-7",
            symbol="600519.SH",
            trade_date="2026-08-17",
            result_data={"final_trade_decision": text},
        )

        assert payload["readiness_score"] == {"status": "not_available"}

    def test_parser_direct_trusted_tail_semantics(self):
        from api.services.openclaw_callback_service import _parse_readiness_from_text

        text, offset = _persisted_decision_text()
        assert _parse_readiness_from_text(text, trusted_offset=offset) == {
            "data_completeness": 87,
            "confidence": "高",
        }
        # Trusted window without any [C-008] marker → None
        no_marker_text = "正文没有任何标记。\n\n📊 数据源可用性"
        assert (
            _parse_readiness_from_text(
                no_marker_text, trusted_offset=len("正文没有任何标记。\n\n")
            )
            is None
        )
        # No offset info → last-heading fallback
        assert _parse_readiness_from_text(text) == {
            "data_completeness": 87,
            "confidence": "高",
        }


# ---------------------------------------------------------------------------
# [B-002-R2-round2] Test: available requires a fully valid readiness score
# ---------------------------------------------------------------------------


class TestReadinessAvailableValidation:
    """[B-002-R2-round2] truncated/invalid candidates must not be `available`."""

    def test_truncated_block_completeness_only_not_available(self):
        from api.services.openclaw_callback_service import build_callback_payload

        text = "📋 [C-008] 执行就绪度评分\n- 数据完整度：87%\n（后续内容被截断）"
        payload = build_callback_payload(
            report_id="rpt-r2v-1",
            symbol="600519.SH",
            trade_date="2026-08-17",
            result_data={"final_trade_decision": text},
        )

        assert payload["readiness_score"] == {"status": "not_available"}

    def test_truncated_block_confidence_only_not_available(self):
        from api.services.openclaw_callback_service import build_callback_payload

        text = "📋 [C-008] 执行就绪度评分\n- 置信度：中\n（数据完整度缺失）"
        payload = build_callback_payload(
            report_id="rpt-r2v-2",
            symbol="600519.SH",
            trade_date="2026-08-17",
            result_data={"final_trade_decision": text},
        )

        assert payload["readiness_score"] == {"status": "not_available"}

    def test_structured_wrong_types_not_available(self):
        from api.services.openclaw_callback_service import build_callback_payload

        for dc, conf in (
            ("87", "高"),  # string completeness
            (87.5, "高"),  # float completeness
            (True, "高"),  # bool masquerading as int
            (87, None),  # null confidence
            (87, "中等"),  # invalid confidence value
            (None, "高"),  # null completeness
            (-1, "高"),  # below range
            (101, "高"),  # above range
        ):
            payload = build_callback_payload(
                report_id="rpt-r2v-3",
                symbol="600519.SH",
                trade_date="2026-08-17",
                result_data={"readiness_score": {"data_completeness": dc, "confidence": conf}},
            )
            assert payload["readiness_score"] == {"status": "not_available"}, (dc, conf)

    def test_structured_partial_does_not_block_valid_text_source(self):
        from api.services.openclaw_callback_service import build_callback_payload

        # A partial/invalid structured snapshot (forward-compat field with no
        # production writer) must neither be published nor mask the
        # authoritative persisted text block.
        payload = build_callback_payload(
            report_id="rpt-r2v-4",
            symbol="600519.SH",
            trade_date="2026-08-17",
            report_obj={
                "readiness_score": {"confidence": "中"},
                "final_trade_decision": _C008_TEXT,
            },
        )

        readiness = payload["readiness_score"]
        assert readiness["status"] == "available"
        assert readiness["data_completeness"] == 87
        assert readiness["confidence"] == "高"
        assert readiness["source"] == "final_trade_decision"

    def test_structured_boundary_completeness_values_available(self):
        from api.services.openclaw_callback_service import build_callback_payload

        for dc in (0, 100):
            payload = build_callback_payload(
                report_id="rpt-r2v-5",
                symbol="600519.SH",
                trade_date="2026-08-17",
                result_data={
                    "readiness_score": {"data_completeness": dc, "confidence": "低"}
                },
            )
            readiness = payload["readiness_score"]
            assert readiness["status"] == "available", dc
            assert readiness["data_completeness"] == dc
            assert readiness["confidence"] == "低"

    def test_available_payload_never_carries_null_fields(self):
        from api.services.openclaw_callback_service import build_callback_payload

        # Fuzz-ish sweep: whatever the inputs, an available score always has
        # int completeness + valid confidence; otherwise it is not_available.
        cases = (
            {"readiness_score": {"data_completeness": 85, "confidence": "高"}},
            {"readiness_score": {"data_completeness": 85}},
            {"readiness_score": {"confidence": "高"}},
            {"readiness_score": {}},
            {"final_trade_decision": _C008_TEXT},
            {"final_trade_decision": "📋 [C-008] 执行就绪度评分\n- 数据完整度：87%"},
            {"final_trade_decision": None},
            {},
        )
        for result_data in cases:
            payload = build_callback_payload(
                report_id="rpt-r2v-6",
                symbol="600519.SH",
                trade_date="2026-08-17",
                result_data=result_data,
            )
            readiness = payload["readiness_score"]
            assert "readiness_score" in payload
            if readiness["status"] == "available":
                assert isinstance(readiness["data_completeness"], int)
                assert not isinstance(readiness["data_completeness"], bool)
                assert 0 <= readiness["data_completeness"] <= 100
                assert readiness["confidence"] in ("高", "中", "低")
                assert readiness["source"] in ("readiness_score", "final_trade_decision")
            else:
                assert readiness == {"status": "not_available"}


# ---------------------------------------------------------------------------
# [B-002-R2] Test: scheduler callback path aligned with real ReportDB schema
# ---------------------------------------------------------------------------


class TestSchedulerCallbackOrmAlignment:
    """Enabled-callback path must not read non-existent ORM columns."""

    @pytest.fixture
    def db(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        from api.database import Base

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        session = Session()
        yield session
        session.close()

    @staticmethod
    def _make_report(**overrides):
        from api.database import ReportDB

        fields = {
            "id": "rpt-b002r2-orm",
            "user_id": "user-1",
            "symbol": "600519.SH",
            "trade_date": "2026-08-14",
            "status": "completed",
            "decision": "BUY",
            "direction": "偏多",
            "research_direction": "看多",
            "execution_action": "ENTER",
            "action_label": "条件入场",
            "confidence": 72,
            "result_data": {"decision": "BUY", "confidence": 72},
            "risk_items": [{"name": "估值偏高", "level": "medium", "description": "PE 高"}],
            "key_metrics": [{"name": "PE", "value": "32.5x", "status": "neutral"}],
            "final_trade_decision": _C008_TEXT,
            "trader_investment_plan": "第一笔 1/4 仓试探。",
            "investment_plan": None,
        }
        fields.update(overrides)
        return ReportDB(**fields)

    @staticmethod
    def _fake_db_ctx(db):
        class FakeDbCtx:
            def __enter__(self):
                return db

            def __exit__(self, exc_type, exc_val, exc_tb):
                if exc_type is not None:
                    db.rollback()

        return FakeDbCtx()

    def test_report_payload_keys_match_reportdb_schema(self, db, monkeypatch):
        import sqlalchemy
        from api.database import ReportDB
        import scheduler.main as scheduler_main

        db.add(self._make_report())
        db.commit()

        captured = {}

        def fake_notify(**kwargs):
            captured.update(kwargs)

        monkeypatch.setenv("OPENCLAW_CALLBACK_ENABLED", "true")
        monkeypatch.setenv("OPENCLAW_CALLBACK_URL", "https://openclaw.example.com/webhook")

        from unittest.mock import patch

        with patch(
            "api.services.openclaw_callback_service.notify_openclaw_on_report_completion",
            side_effect=fake_notify,
        ), patch(
            "scheduler.main.get_db_ctx", return_value=self._fake_db_ctx(db)
        ):
            asyncio.run(
                scheduler_main._send_openclaw_callback(
                    "user-1", "rpt-b002r2-orm", "600519.SH", "2026-08-14", "short", "scheduled"
                )
            )

        report_obj = captured["report_obj"]
        assert isinstance(report_obj, dict)
        real_columns = set(sqlalchemy.inspect(ReportDB).columns.keys())
        assert set(report_obj.keys()) <= real_columns
        for removed in ("horizon", "analysis_summary", "opinion", "readiness_score"):
            assert removed not in report_obj
        for expected in (
            "id",
            "symbol",
            "trade_date",
            "result_data",
            "decision",
            "direction",
            "research_direction",
            "execution_action",
            "action_label",
            "confidence",
            "risk_items",
            "key_metrics",
            "final_trade_decision",
            "trader_investment_plan",
            "investment_plan",
        ):
            assert expected in report_obj
        assert captured["horizon"] == "short"
        assert captured["result_data"] == {"decision": "BUY", "confidence": 72}

    def test_enabled_callback_sends_full_payload(self, db, monkeypatch):
        import scheduler.main as scheduler_main

        db.add(self._make_report())
        db.commit()

        payloads = []

        async def fake_send_async(payload):
            payloads.append(payload)
            return True

        monkeypatch.setenv("OPENCLAW_CALLBACK_ENABLED", "true")
        monkeypatch.setenv("OPENCLAW_CALLBACK_URL", "https://openclaw.example.com/webhook")

        from unittest.mock import patch

        async def _scenario():
            await scheduler_main._send_openclaw_callback(
                "user-1", "rpt-b002r2-orm", "600519.SH", "2026-08-14", "short", "scheduled"
            )
            for _ in range(4):
                await asyncio.sleep(0)

        with patch(
            "api.services.openclaw_callback_service.send_callback_async",
            side_effect=fake_send_async,
        ), patch(
            "scheduler.main.get_db_ctx", return_value=self._fake_db_ctx(db)
        ):
            asyncio.run(_scenario())

        assert len(payloads) == 1
        payload = payloads[0]

        assert payload["schema_version"] == "1.1.0"
        assert payload["source"] == "tradingagents-scheduler"
        assert payload["event"] == "scheduled_analysis_completed"
        assert payload["user_id"] == "user-1"

        report = payload["report"]
        assert report["id"] == "rpt-b002r2-orm"
        assert report["symbol"] == "600519.SH"
        assert report["trade_date"] == "2026-08-14"
        assert report["horizon"] == "short"
        assert report["source"] == "scheduled"

        decision = payload["decision"]
        assert decision["action"] == "条件入场"
        assert decision["direction"] == "看多"
        assert decision["execution_action"] == "ENTER"
        assert decision["confidence"] == 72

        assert "逢低分批建仓" in payload["summary"]
        assert payload["risk_items"][0]["name"] == "估值偏高"
        assert payload["risk_items"][0]["level"] == "medium"
        assert payload["key_metrics"][0]["name"] == "PE"
        assert payload["key_metrics"][0]["value"] == "32.5x"

        readiness = payload["readiness_score"]
        assert readiness["status"] == "available"
        assert readiness["data_completeness"] == 87
        assert readiness["confidence"] == "高"
        assert readiness["source"] == "final_trade_decision"

        json.dumps(payload, ensure_ascii=False)

    def test_missing_report_row_still_sends_explicit_not_available(self, db, monkeypatch):
        import scheduler.main as scheduler_main

        payloads = []

        async def fake_send_async(payload):
            payloads.append(payload)
            return True

        monkeypatch.setenv("OPENCLAW_CALLBACK_ENABLED", "true")
        monkeypatch.setenv("OPENCLAW_CALLBACK_URL", "https://openclaw.example.com/webhook")

        from unittest.mock import patch

        async def _scenario():
            await scheduler_main._send_openclaw_callback(
                "user-1", "rpt-missing", "600519.SH", "2026-08-14", "short", "scheduled"
            )
            for _ in range(4):
                await asyncio.sleep(0)

        with patch(
            "api.services.openclaw_callback_service.send_callback_async",
            side_effect=fake_send_async,
        ), patch(
            "scheduler.main.get_db_ctx", return_value=self._fake_db_ctx(db)
        ):
            asyncio.run(_scenario())

        assert len(payloads) == 1
        payload = payloads[0]
        assert payload["report"]["symbol"] == "600519.SH"
        assert payload["decision"]["action"] == ""
        assert payload["readiness_score"] == {"status": "not_available"}
