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
        assert payload["schema_version"] == "1.0.0"
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
        assert payload["readiness_score"]["data_completeness"] == 85
        assert payload["readiness_score"]["confidence"] == "高"

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
        assert "readiness_score" not in payload  # no readiness → omitted

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
        assert CALLBACK_SCHEMA_VERSION == "1.0.0"

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
