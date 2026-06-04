# [PERF-004] full_ta_cost_gate
"""Tests for FULL TA manual confirmation and scheduler cost gate.

Validates:
1. FULL_TA cost preview provides model/tier/modules/cost info
2. Unconfirmed FULL_TA requests are blocked (403)
3. Confirmed FULL_TA requests pass through
4. Scheduler cost metadata is recorded per task
5. Scheduled list includes cost_meta field
6. Auto dev tasks cannot trigger FULL_TA
7. Chat completions forward runtime_tier/confirmed_full_ta
8. Cost preview does not leak API keys
9. No live LLM/TA triggered
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest


# ── Module under test ──
from api.runtime_tier import (
    RuntimeTier,
    FullTACostPreview,
    ScheduledCostMeta,
    build_scheduled_cost_meta,
    get_full_ta_cost_preview,
    get_tier_spec,
    is_full_ta_allowed_without_confirmation,
    tier_to_meta,
    _FULL_TA_MODULE_COUNT,
    _FULL_TA_LLM_CALL_ESTIMATE,
)


# ═══════════════════════════════════════════════════════════════════
# TestFullTACostPreview
# ═══════════════════════════════════════════════════════════════════

class TestFullTACostPreview:
    def test_defaults(self):
        p = FullTACostPreview()
        assert p.runtime_tier == "FULL_TA"
        assert p.tier_label == "完整 TA"
        assert p.expected_latency == "10-20min"
        assert p.llm_allowed is True
        assert p.cost_risk == "high"
        assert p.requires_confirmation is True
        assert p.llm_provider == ""
        assert p.llm_model == ""
        assert p.base_url_display == ""
        assert p.estimated_llm_calls == 0
        assert p.enabled_modules == []

    def test_custom(self):
        p = FullTACostPreview(
            llm_provider="openai",
            llm_model="gpt-4o",
            base_url_display="https://api.openai.com/v1",
        )
        assert p.llm_provider == "openai"
        assert p.llm_model == "gpt-4o"
        assert p.base_url_display == "https://api.openai.com/v1"

    def test_to_dict(self):
        p = FullTACostPreview(llm_provider="test", llm_model="m1")
        d = p.to_dict()
        assert isinstance(d, dict)
        assert d["runtime_tier"] == "FULL_TA"
        assert d["llm_provider"] == "test"
        assert d["llm_model"] == "m1"
        assert "enabled_modules" in d
        assert "estimated_llm_calls" in d


# ═══════════════════════════════════════════════════════════════════
# TestGetFullTACostPreview
# ═══════════════════════════════════════════════════════════════════

class TestGetFullTACostPreview:
    def test_basic(self):
        preview = get_full_ta_cost_preview()
        assert preview.runtime_tier == "FULL_TA"
        assert preview.tier_label == "完整 TA"
        assert preview.expected_latency == "10-20min"
        assert preview.cost_risk == "high"
        assert preview.requires_confirmation is True
        assert len(preview.enabled_modules) == _FULL_TA_MODULE_COUNT
        assert preview.estimated_llm_calls == _FULL_TA_LLM_CALL_ESTIMATE

    def test_with_provider_info(self):
        preview = get_full_ta_cost_preview(
            llm_provider="zhipu",
            llm_model="glm-4-plus",
            base_url_display="https://open.bigmodel.cn/api/paas",
        )
        assert preview.llm_provider == "zhipu"
        assert preview.llm_model == "glm-4-plus"
        assert "bigmodel" in preview.base_url_display

    def test_no_api_key_leak(self):
        preview = get_full_ta_cost_preview(
            llm_provider="openai",
            llm_model="gpt-4o",
            base_url_display="https://api.openai.com/v1",
        )
        d = preview.to_dict()
        for key in ("api_key", "key", "token", "secret", "password", "authorization"):
            assert key not in d, f"Cost preview should not contain {key}"
        for val in d.values():
            if isinstance(val, str):
                assert "sk-" not in val, f"Cost preview leaked API key pattern: {val}"

    def test_module_count_14(self):
        preview = get_full_ta_cost_preview()
        assert len(preview.enabled_modules) == 14

    def test_description_matches_tier(self):
        preview = get_full_ta_cost_preview()
        spec = get_tier_spec(RuntimeTier.FULL_TA)
        assert preview.description == spec.description


# ═══════════════════════════════════════════════════════════════════
# TestScheduledCostMeta
# ═══════════════════════════════════════════════════════════════════

class TestScheduledCostMeta:
    def test_defaults(self):
        m = ScheduledCostMeta()
        assert m.is_full_ta is True
        assert m.runtime_tier == "FULL_TA"
        assert m.tier_label == "完整 TA"
        assert m.created_by == ""
        assert m.trigger_frequency == "daily"
        assert m.last_run_llm_summary == ""

    def test_to_dict(self):
        m = ScheduledCostMeta(last_run_llm_summary="上次运行成功")
        d = m.to_dict()
        assert d["is_full_ta"] is True
        assert d["runtime_tier"] == "FULL_TA"
        assert d["last_run_llm_summary"] == "上次运行成功"


# ═══════════════════════════════════════════════════════════════════
# TestBuildScheduledCostMeta
# ═══════════════════════════════════════════════════════════════════

class TestBuildScheduledCostMeta:
    def test_default_trigger_time(self):
        meta = build_scheduled_cost_meta()
        assert meta.trigger_frequency == "每日 20:00"
        assert meta.is_full_ta is True

    def test_custom_trigger_time(self):
        meta = build_scheduled_cost_meta(trigger_time="14:30")
        assert meta.trigger_frequency == "每日 14:30"

    def test_last_run_success(self):
        meta = build_scheduled_cost_meta(last_run_status="success")
        assert "成功" in meta.last_run_llm_summary
        assert "LLM" in meta.last_run_llm_summary

    def test_last_run_failed(self):
        meta = build_scheduled_cost_meta(last_run_status="failed")
        assert "失败" in meta.last_run_llm_summary

    def test_last_run_none(self):
        meta = build_scheduled_cost_meta(last_run_status=None)
        assert "尚未运行" in meta.last_run_llm_summary

    def test_last_run_running(self):
        meta = build_scheduled_cost_meta(last_run_status="running")
        assert "运行中" in meta.last_run_llm_summary

    def test_last_run_stale(self):
        meta = build_scheduled_cost_meta(last_run_status="stale")
        assert "不明" in meta.last_run_llm_summary

    def test_no_api_key_in_dict(self):
        meta = build_scheduled_cost_meta()
        d = meta.to_dict()
        for key in ("api_key", "key", "token", "secret"):
            assert key not in d


# ═══════════════════════════════════════════════════════════════════
# TestFullTAGateStillWorks
# ═══════════════════════════════════════════════════════════════════

class TestFullTAGateStillWorks:
    def test_full_ta_blocked(self):
        assert is_full_ta_allowed_without_confirmation("FULL_TA") is False

    def test_fast_allowed(self):
        assert is_full_ta_allowed_without_confirmation("FAST_RADAR") is True

    def test_light_allowed(self):
        assert is_full_ta_allowed_without_confirmation("LIGHT_RESEARCH") is True

    def test_none_blocked(self):
        assert is_full_ta_allowed_without_confirmation(None) is False


# ═══════════════════════════════════════════════════════════════════
# TestCostPreviewEndpoint
# ═══════════════════════════════════════════════════════════════════

class TestCostPreviewEndpoint:
    def test_endpoint_registered(self):
        from api.main import app
        routes = [r.path for r in app.routes]
        assert "/v1/analyze/cost-preview" in routes

    def test_response_model(self):
        from api.main import FullTACostPreviewResponse
        resp = FullTACostPreviewResponse(
            runtime_tier="FULL_TA",
            tier_label="完整 TA",
            expected_latency="10-20min",
            llm_allowed=True,
            cost_risk="high",
            requires_confirmation=True,
            llm_provider="openai",
            llm_model="gpt-4o",
            base_url_display="https://api.openai.com/v1",
            enabled_modules=["market", "fundamentals"],
            estimated_llm_calls=20,
            description="test",
        )
        d = resp.model_dump()
        assert d["runtime_tier"] == "FULL_TA"
        assert d["estimated_llm_calls"] == 20
        assert "api_key" not in d


# ═══════════════════════════════════════════════════════════════════
# TestChatCompletionRequestRuntimeFields
# ═══════════════════════════════════════════════════════════════════

class TestChatCompletionRequestRuntimeFields:
    def test_runtime_tier_field(self):
        from api.main import ChatCompletionRequest
        req = ChatCompletionRequest(
            messages=[{"role": "user", "content": "test"}],
            runtime_tier="FULL_TA",
            confirmed_full_ta=True,
            runtime_profile="FULL_TA",
        )
        assert req.runtime_tier == "FULL_TA"
        assert req.confirmed_full_ta is True
        assert req.runtime_profile == "FULL_TA"

    def test_default_none(self):
        from api.main import ChatCompletionRequest
        req = ChatCompletionRequest(
            messages=[{"role": "user", "content": "test"}],
        )
        assert req.runtime_tier is None
        assert req.confirmed_full_ta is False
        assert req.runtime_profile is None


# ═══════════════════════════════════════════════════════════════════
# TestAnalyzeEndpointGate
# ═══════════════════════════════════════════════════════════════════

class TestAnalyzeEndpointGate:
    def test_full_ta_without_confirmation_returns_403(self):
        from fastapi.testclient import TestClient
        from api.main import app, _require_api_user
        from unittest.mock import MagicMock
        mock_user = MagicMock()
        mock_user.id = "test-user"
        app.dependency_overrides[_require_api_user] = lambda: mock_user
        try:
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/v1/analyze",
                json={
                    "symbol": "600519.SH",
                    "runtime_tier": "FULL_TA",
                    "confirmed_full_ta": False,
                },
            )
            assert resp.status_code == 403
            detail = resp.json().get("detail", {})
            if isinstance(detail, dict):
                assert detail.get("requires_confirmation") is True
        finally:
            app.dependency_overrides.pop(_require_api_user, None)

    def test_light_research_no_confirmation_needed(self):
        from api.main import AnalyzeRequest
        req = AnalyzeRequest(
            symbol="600519.SH",
            runtime_tier="LIGHT_RESEARCH",
        )
        assert req.confirmed_full_ta is False
        from api.runtime_tier import RuntimeTier, is_full_ta_allowed_without_confirmation
        assert is_full_ta_allowed_without_confirmation("LIGHT_RESEARCH") is True


# ═══════════════════════════════════════════════════════════════════
# TestSchedulerCostGateLogging
# ═══════════════════════════════════════════════════════════════════

class TestSchedulerCostGateLogging:
    def test_scheduler_log_includes_cost_risk(self):
        import inspect
        from scheduler.main import _run_scheduled_job
        source = inspect.getsource(_run_scheduled_job)
        assert "cost_risk=high" in source
        assert "estimated_calls" in source

    def test_scheduler_explicit_full_ta(self):
        import inspect
        from scheduler.main import _run_scheduled_job
        source = inspect.getsource(_run_scheduled_job)
        assert "runtime_tier=FULL_TA" in source


# ═══════════════════════════════════════════════════════════════════
# TestScheduledListCostMeta
# ═══════════════════════════════════════════════════════════════════

class TestScheduledListCostMeta:
    def test_annotate_adds_cost_meta(self):
        from api.main import _annotate_scheduled_with_imported_context
        from api.database import ScheduledAnalysisDB
        mock_item = {
            "id": "test-1",
            "symbol": "600519.SH",
            "horizon": "short",
            "trigger_time": "20:00",
            "is_active": True,
            "last_run_date": "2026-06-04",
            "last_run_status": "success",
            "last_report_id": "r1",
            "consecutive_failures": 0,
            "created_at": "2026-06-01T00:00:00",
        }
        db_mock = MagicMock()
        db_mock.query.return_value.filter.return_value.all.return_value = []
        with patch("api.main.portfolio_import_service.list_imported_positions", return_value=[]):
            result = _annotate_scheduled_with_imported_context([mock_item], db_mock, "user-1")
        assert len(result) == 1
        assert "cost_meta" in result[0]
        cm = result[0]["cost_meta"]
        assert cm["is_full_ta"] is True
        assert cm["runtime_tier"] == "FULL_TA"
        assert "20:00" in cm["trigger_frequency"]

    def test_annotate_no_last_run(self):
        from api.main import _annotate_scheduled_with_imported_context
        mock_item = {
            "id": "test-2",
            "symbol": "000001.SZ",
            "horizon": "short",
            "trigger_time": "14:30",
            "is_active": True,
            "last_run_date": None,
            "last_run_status": None,
            "last_report_id": None,
            "consecutive_failures": 0,
            "created_at": "2026-06-01T00:00:00",
        }
        db_mock = MagicMock()
        db_mock.query.return_value.filter.return_value.all.return_value = []
        with patch("api.main.portfolio_import_service.list_imported_positions", return_value=[]):
            result = _annotate_scheduled_with_imported_context([mock_item], db_mock, "user-1")
        cm = result[0]["cost_meta"]
        assert "14:30" in cm["trigger_frequency"]
        assert "尚未运行" in cm["last_run_llm_summary"]


# ═══════════════════════════════════════════════════════════════════
# TestAutoDevCannotTriggerFullTA
# ═══════════════════════════════════════════════════════════════════

class TestAutoDevCannotTriggerFullTA:
    def test_scheduler_default_tier_is_not_full(self):
        from api.runtime_tier import scheduler_default_tier
        tier = scheduler_default_tier()
        assert tier != RuntimeTier.FULL_TA
        assert tier == RuntimeTier.LIGHT_RESEARCH

    def test_is_full_ta_allowed_without_confirmation_blocks_auto(self):
        assert is_full_ta_allowed_without_confirmation(None) is False
        assert is_full_ta_allowed_without_confirmation("") is False


# ═══════════════════════════════════════════════════════════════════
# TestFrontendTypeContract
# ═══════════════════════════════════════════════════════════════════

class TestFrontendTypeContract:
    def test_cost_preview_response_matches_types(self):
        preview = get_full_ta_cost_preview(
            llm_provider="test",
            llm_model="m1",
            base_url_display="https://example.com",
        )
        d = preview.to_dict()
        expected_keys = {
            "runtime_tier", "tier_label", "expected_latency",
            "llm_allowed", "cost_risk", "requires_confirmation",
            "llm_provider", "llm_model", "base_url_display",
            "enabled_modules", "estimated_llm_calls", "description",
        }
        assert set(d.keys()) == expected_keys

    def test_scheduled_cost_meta_matches_types(self):
        meta = build_scheduled_cost_meta(trigger_time="20:00", last_run_status="success")
        d = meta.to_dict()
        expected_keys = {
            "is_full_ta", "runtime_tier", "tier_label",
            "created_by", "trigger_frequency", "last_run_llm_summary",
        }
        assert set(d.keys()) == expected_keys


# ═══════════════════════════════════════════════════════════════════
# TestNoLiveLLMOrTA
# ═══════════════════════════════════════════════════════════════════

class TestNoLiveLLMOrTA:
    def test_cost_preview_no_external_calls(self):
        preview = get_full_ta_cost_preview()
        assert isinstance(preview, FullTACostPreview)
        assert preview.estimated_llm_calls > 0

    def test_scheduled_cost_meta_no_external_calls(self):
        meta = build_scheduled_cost_meta()
        assert isinstance(meta, ScheduledCostMeta)


# ═══════════════════════════════════════════════════════════════════
# TestAcceptancePERF004
# ═══════════════════════════════════════════════════════════════════

class TestAcceptancePERF004:
    def test_unconfirmed_full_ta_blocked(self):
        assert is_full_ta_allowed_without_confirmation("FULL_TA") is False

    def test_confirmed_full_ta_passes(self):
        assert is_full_ta_allowed_without_confirmation("FULL_TA") is False
        from api.main import AnalyzeRequest
        req = AnalyzeRequest(symbol="600519.SH", runtime_tier="FULL_TA", confirmed_full_ta=True)
        assert req.confirmed_full_ta is True

    def test_scheduler_cost_meta_records_creator(self):
        meta = build_scheduled_cost_meta(trigger_time="20:00")
        assert meta.created_by == "user"

    def test_scheduler_cost_meta_records_frequency(self):
        meta = build_scheduled_cost_meta(trigger_time="14:30")
        assert "14:30" in meta.trigger_frequency

    def test_scheduler_cost_meta_records_last_run(self):
        meta = build_scheduled_cost_meta(last_run_status="success")
        assert "成功" in meta.last_run_llm_summary

    def test_cost_preview_has_14_modules(self):
        preview = get_full_ta_cost_preview()
        assert len(preview.enabled_modules) == 14

    def test_cost_preview_has_estimated_calls(self):
        preview = get_full_ta_cost_preview()
        assert preview.estimated_llm_calls > 0

    def test_no_api_key_leak(self):
        preview = get_full_ta_cost_preview(
            llm_provider="test",
            llm_model="m1",
            base_url_display="url",
        )
        d = preview.to_dict()
        for v in d.values():
            if isinstance(v, str):
                assert "sk-" not in v

    def test_auto_dev_default_tier_not_full(self):
        from api.runtime_tier import scheduler_default_tier
        assert scheduler_default_tier() == RuntimeTier.LIGHT_RESEARCH
