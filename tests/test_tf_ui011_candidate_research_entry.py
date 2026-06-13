# [TF-UI-011] candidate_research_entry
"""Tests for TF-UI-011: Candidate detail one-click lightweight TA, K-line & company overview.

Covers:
- Company overview service (mock data source, graceful fallback).
- Research plan response includes profile metadata (expected_latency, llm_allowed, etc.).
- Full TA guard: light profiles don't require confirmation; FULL_TA does.
- Profile routing for candidate types.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from unittest.mock import patch, MagicMock

import pytest

from api.ta_profile import (
    TAProfile,
    recommend_profile,
    profile_to_meta,
    get_profile_spec,
)


# ─── Company Overview Service ─────────────────────────────────────────────────

class TestCompanyOverviewService:
    """Test get_company_overview service function."""

    def _make_test_db(self) -> str:
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        conn = sqlite3.connect(path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tradeflow_candidates (
                symbol TEXT,
                trade_date TEXT,
                name TEXT DEFAULT '',
                candidate_type TEXT DEFAULT '',
                PRIMARY KEY (trade_date, symbol)
            )
        """)
        conn.execute(
            "INSERT INTO tradeflow_candidates (symbol, trade_date, name, candidate_type) VALUES (?, ?, ?, ?)",
            ("600519.SH", "2026-06-14", "贵州茅台", "POLICY_AMBUSH"),
        )
        conn.commit()
        conn.close()
        return path

    def test_overview_with_fundamentals(self):
        """Company overview returns profile when data source is available."""
        from api.services.tradeflow_service import get_company_overview

        mock_text = "## Fundamentals for 600519.SH\n\n### Company Profile (东财)\n- **行业**: 白酒\n- **总市值(元)**: 2000000000000"
        with patch("api.services.tradeflow_service._connect", return_value=None):
            with patch("tradingagents.dataflows.interface.route_to_vendor", return_value=mock_text):
                with patch("tradingagents.dataflows.interface.get_last_hit_vendor", return_value="cn_astock"):
                    result = get_company_overview("600519.SH")

        assert result["status"] == "ok"
        assert result["symbol"] == "600519.SH"
        assert result["profile_available"] is True
        assert "白酒" in result["company_profile"]
        assert result["data_source"] == "cn_astock"
        assert result["industry"] == "白酒"

    def test_overview_fundamentals_unavailable(self):
        """Company overview falls back gracefully when data source fails."""
        from api.services.tradeflow_service import get_company_overview

        with patch("api.services.tradeflow_service._connect", return_value=None):
            with patch("tradingagents.dataflows.interface.route_to_vendor", side_effect=RuntimeError("No vendor available")):
                result = get_company_overview("600519.SH")

        assert result["status"] == "unavailable"
        assert result["profile_available"] is False
        assert result["error"] != ""
        assert result["company_profile"] == ""

    def test_overview_resolves_name_from_db(self):
        """Company overview resolves name from existing candidate DB."""
        from api.services.tradeflow_service import get_company_overview

        db_path = self._make_test_db()
        try:
            with patch("tradingagents.dataflows.interface.route_to_vendor", side_effect=RuntimeError("No vendor")):
                result = get_company_overview("600519.SH", tf_db_path=db_path)
            assert result["name"] == "贵州茅台"
        finally:
            os.unlink(db_path)

    def test_overview_runtime_tier_meta(self):
        """Company overview returns runtime tier metadata."""
        from api.services.tradeflow_service import get_company_overview

        with patch("api.services.tradeflow_service._connect", return_value=None):
            with patch("tradingagents.dataflows.interface.route_to_vendor", side_effect=RuntimeError("No vendor")):
                result = get_company_overview("600519.SH")

        assert "runtime_tier_meta" in result
        meta = result["runtime_tier_meta"]
        assert meta["runtime_tier"] == "FAST_RADAR"


# ─── Research Plan Profile Metadata ────────────────────────────────────────────

class TestResearchPlanProfileMetadata:
    """Test that research plan response includes profile metadata."""

    def _make_test_db(self) -> str:
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        conn = sqlite3.connect(path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tradeflow_candidates (
                symbol TEXT, trade_date TEXT, name TEXT DEFAULT '',
                candidate_type TEXT DEFAULT '', research_queue TEXT DEFAULT '',
                research_intent TEXT DEFAULT '', research_route_reason TEXT DEFAULT '',
                evidence_json TEXT DEFAULT '{}', tradeflow_data_completeness REAL DEFAULT 0,
                PRIMARY KEY (trade_date, symbol)
            )
        """)
        conn.execute(
            "INSERT INTO tradeflow_candidates VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("600519.SH", "2026-06-14", "贵州茅台", "POLICY_AMBUSH", "MIDLINE_POLICY", "policy_validation", "test", '{"a": true, "b": true}', 0.8),
        )
        conn.execute(
            "INSERT INTO tradeflow_candidates VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("000001.SZ", "2026-06-14", "平安银行", "TECH_TRADE", "SHORT_TERM_TRADE", "trend_confirmation", "test", '{"a": true, "b": true}', 0.5),
        )
        conn.commit()
        conn.close()
        return path

    def test_policy_ambush_has_profile_metadata(self):
        """Research plan for POLICY_AMBUSH includes MIDLINE_POLICY_LIGHT metadata."""
        from api.services.tradeflow_service import generate_research_plan as svc_gen

        db_path = self._make_test_db()
        try:
            result = svc_gen("600519.SH", "2026-06-14", tf_db_path=db_path)
            assert result["status"] == "ok"
            assert result["runtime_profile"] == "MIDLINE_POLICY_LIGHT"
            assert result.get("profile_label") == "中线政策轻量"
            assert result.get("expected_latency") == "1-3min"
            assert result.get("llm_allowed") is True
            assert result.get("requires_confirmation") is False
            assert result.get("cost_risk") == "low"
        finally:
            os.unlink(db_path)

    def test_tech_trade_has_profile_metadata(self):
        """Research plan for TECH_TRADE includes SHORT_TECH_LIGHT metadata."""
        from api.services.tradeflow_service import generate_research_plan as svc_gen

        db_path = self._make_test_db()
        try:
            result = svc_gen("000001.SZ", "2026-06-14", tf_db_path=db_path)
            assert result["status"] == "ok"
            assert result["runtime_profile"] == "SHORT_TECH_LIGHT"
            assert result.get("profile_label") == "短线技术轻量"
            assert result.get("expected_latency") == "1-2min"
            assert result.get("llm_allowed") is True
            assert result.get("requires_confirmation") is False
        finally:
            os.unlink(db_path)

    def test_light_profiles_no_confirmation(self):
        """Light profiles do not require user confirmation."""
        from api.services.tradeflow_service import generate_research_plan as svc_gen

        db_path = self._make_test_db()
        try:
            for symbol, expected_profile in [("600519.SH", "MIDLINE_POLICY_LIGHT"), ("000001.SZ", "SHORT_TECH_LIGHT")]:
                result = svc_gen(symbol, "2026-06-14", tf_db_path=db_path)
                assert result["requires_confirmation"] is False, f"{expected_profile} should not require confirmation"
        finally:
            os.unlink(db_path)


# ─── Profile Routing ───────────────────────────────────────────────────────────

class TestProfileRouting:
    """Test that candidate types are correctly routed to profiles."""

    def test_policy_ambush_routes_to_midline(self):
        profile = recommend_profile(candidate_type="POLICY_AMBUSH")
        assert profile == TAProfile.MIDLINE_POLICY_LIGHT

    def test_policy_confirm_routes_to_midline(self):
        profile = recommend_profile(candidate_type="POLICY_CONFIRM")
        assert profile == TAProfile.MIDLINE_POLICY_LIGHT

    def test_tech_trade_routes_to_short(self):
        profile = recommend_profile(candidate_type="TECH_TRADE")
        assert profile == TAProfile.SHORT_TECH_LIGHT

    def test_event_watch_routes_to_short(self):
        profile = recommend_profile(candidate_type="EVENT_WATCH")
        assert profile == TAProfile.SHORT_TECH_LIGHT

    def test_has_position_routes_to_position_risk(self):
        profile = recommend_profile(candidate_type="POLICY_AMBUSH", has_position=True)
        assert profile == TAProfile.POSITION_RISK_LIGHT

    def test_unknown_type_routes_to_full_ta(self):
        profile = recommend_profile(candidate_type="UNKNOWN_TYPE")
        assert profile == TAProfile.FULL_TA


# ─── Full TA Guard ────────────────────────────────────────────────────────────

class TestFullTAGuard:
    """Test that FULL_TA requires confirmation and has high cost risk."""

    def test_full_ta_requires_confirmation(self):
        spec = get_profile_spec(TAProfile.FULL_TA)
        assert spec.requires_confirmation is True
        assert spec.cost_risk == "high"
        assert spec.tier == "FULL_TA"
        assert "10-20min" in spec.expected_latency

    def test_light_profiles_no_confirmation(self):
        for profile in [TAProfile.MIDLINE_POLICY_LIGHT, TAProfile.SHORT_TECH_LIGHT, TAProfile.POSITION_RISK_LIGHT]:
            spec = get_profile_spec(profile)
            assert spec.requires_confirmation is False, f"{profile} should not require confirmation"
            assert spec.cost_risk == "low"

    def test_full_ta_includes_all_modules(self):
        spec = get_profile_spec(TAProfile.FULL_TA)
        # Should have more analysts than any light profile
        midline_spec = get_profile_spec(TAProfile.MIDLINE_POLICY_LIGHT)
        assert len(spec.enabled_analysts) > len(midline_spec.enabled_analysts)


# ─── Profile Meta ──────────────────────────────────────────────────────────────

class TestProfileMeta:
    """Test profile_to_meta returns expected fields."""

    def test_meta_has_all_required_fields(self):
        for profile in TAProfile:
            meta = profile_to_meta(profile)
            assert "runtime_profile" in meta
            assert "profile_label" in meta
            assert "tier" in meta
            assert "expected_latency" in meta
            assert "llm_allowed" in meta
            assert "requires_confirmation" in meta
            assert "cost_risk" in meta
            assert "enabled_analysts" in meta

    def test_meta_values_correct(self):
        meta = profile_to_meta(TAProfile.MIDLINE_POLICY_LIGHT)
        assert meta["runtime_profile"] == "MIDLINE_POLICY_LIGHT"
        assert meta["profile_label"] == "中线政策轻量"
        assert meta["tier"] == "LIGHT_RESEARCH"
        assert meta["expected_latency"] == "1-3min"


# ─── Schema Validation ────────────────────────────────────────────────────────

class TestSchemaValidation:
    """Test Pydantic schema includes new fields."""

    def test_company_overview_response_schema(self):
        from api.tradeflow_schemas import CompanyOverviewResponse

        resp = CompanyOverviewResponse(
            status="ok",
            symbol="600519.SH",
            name="贵州茅台",
            industry="白酒",
            company_profile="test profile",
            profile_available=True,
            data_source="cn_astock",
        )
        assert resp.symbol == "600519.SH"
        assert resp.profile_available is True
        assert resp.industry == "白酒"

    def test_company_overview_response_defaults(self):
        from api.tradeflow_schemas import CompanyOverviewResponse

        resp = CompanyOverviewResponse(symbol="000001.SZ")
        assert resp.status == "ok"
        assert resp.profile_available is False
        assert resp.company_profile == ""
        assert resp.error == ""
        assert resp.runtime_tier_meta.runtime_tier == "FAST_RADAR"

    def test_research_plan_response_has_profile_fields(self):
        from api.tradeflow_schemas import TradeFlowResearchPlanResponse

        resp = TradeFlowResearchPlanResponse(
            status="ok",
            symbol="600519.SH",
            runtime_profile="MIDLINE_POLICY_LIGHT",
        )
        assert hasattr(resp, "profile_label")
        assert hasattr(resp, "expected_latency")
        assert hasattr(resp, "llm_allowed")
        assert hasattr(resp, "requires_confirmation")
        assert hasattr(resp, "cost_risk")
