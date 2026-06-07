# [UI-009] candidate_ta_plan_draft
"""Tests for UI-009: Research plan draft generator.

Covers:
- Research plan draft generation (core module).
- Plan rendering.
- Blocking conditions.
- Service-layer integration with mock DB.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from unittest.mock import patch

import pytest

from tradingagents.tradeflow.research_plan_draft import (
    ResearchPlanDraft,
    generate_research_plan,
    render_plan_markdown,
    _determine_horizon,
    _determine_analysis_intent,
    _determine_position_context,
    _determine_runtime_profile,
    _determine_modules,
    _determine_required_evidence,
    _check_can_generate,
    _MIDLINE_MODULES,
    _SHORT_TERM_MODULES,
    _FULL_TA_MODULES,
    _MIN_EVIDENCE_COVERAGE,
)


class TestResearchPlanDraft:
    def test_defaults(self):
        d = ResearchPlanDraft()
        assert d.symbol == ""
        assert d.can_generate is True
        assert d.enabled_modules == []
        assert d.required_evidence == []

    def test_to_dict(self):
        d = ResearchPlanDraft(
            symbol="600519.SH",
            trade_date="2026-06-07",
            candidate_type="POLICY_AMBUSH",
            research_queue="MIDLINE_POLICY",
            research_intent="policy_validation",
            horizon="medium",
            can_generate=True,
        )
        result = d.to_dict()
        assert result["symbol"] == "600519.SH"
        assert result["candidate_type"] == "POLICY_AMBUSH"
        assert result["can_generate"] is True
        assert "plan_markdown" in result


class TestDetermineHorizon:
    def test_policy_ambush(self):
        assert _determine_horizon("POLICY_AMBUSH") == "medium"

    def test_policy_confirm(self):
        assert _determine_horizon("POLICY_CONFIRM") == "medium"

    def test_tech_trade(self):
        assert _determine_horizon("TECH_TRADE") == "short"

    def test_event_watch(self):
        assert _determine_horizon("EVENT_WATCH") == "short"

    def test_unknown(self):
        assert _determine_horizon("UNKNOWN") == "short"


class TestDetermineAnalysisIntent:
    def test_policy_ambush(self):
        assert _determine_analysis_intent("POLICY_AMBUSH") == "entry"

    def test_policy_confirm(self):
        assert _determine_analysis_intent("POLICY_CONFIRM") == "entry"

    def test_tech_trade(self):
        assert _determine_analysis_intent("TECH_TRADE") == "watch"

    def test_event_watch(self):
        assert _determine_analysis_intent("EVENT_WATCH") == "watch"


class TestDeterminePositionContext:
    def test_default(self):
        assert _determine_position_context("POLICY_AMBUSH") == "false"

    def test_tech_trade(self):
        assert _determine_position_context("TECH_TRADE") == "false"


class TestDetermineRuntimeProfile:
    def test_policy_ambush(self):
        assert _determine_runtime_profile("POLICY_AMBUSH") == "MIDLINE_POLICY_LIGHT"

    def test_policy_confirm(self):
        assert _determine_runtime_profile("POLICY_CONFIRM") == "MIDLINE_POLICY_LIGHT"

    def test_tech_trade(self):
        assert _determine_runtime_profile("TECH_TRADE") == "SHORT_TECH_LIGHT"

    def test_unknown(self):
        assert _determine_runtime_profile("EVENT_WATCH") == "FULL_TA"


class TestDetermineModules:
    def test_policy_ambush(self):
        mods = _determine_modules("POLICY_AMBUSH")
        assert mods == list(_MIDLINE_MODULES)

    def test_tech_trade(self):
        mods = _determine_modules("TECH_TRADE")
        assert mods == list(_SHORT_TERM_MODULES)

    def test_unknown(self):
        mods = _determine_modules("EVENT_WATCH")
        assert mods == list(_FULL_TA_MODULES)


class TestDetermineRequiredEvidence:
    def test_policy_ambush(self):
        ev = _determine_required_evidence("POLICY_AMBUSH")
        assert "policy_evidence" in ev
        assert "beneficiary_path" in ev
        assert "stock_data" in ev

    def test_tech_trade(self):
        ev = _determine_required_evidence("TECH_TRADE")
        assert "realtime_quote" in ev
        assert "stock_data" in ev

    def test_event_watch(self):
        ev = _determine_required_evidence("EVENT_WATCH")
        assert "event_source" in ev


class TestCheckCanGenerate:
    def test_rejected(self):
        ok, reason = _check_can_generate("POLICY_AMBUSH", "REJECTED", 0.5)
        assert not ok
        assert "拒绝" in reason

    def test_overheated_avoid(self):
        ok, reason = _check_can_generate("OVERHEATED_AVOID", "REJECTED", 0.5)
        assert not ok
        assert "拒绝" in reason or "OVERHEATED_AVOID" in reason

    def test_pseudo_policy(self):
        ok, reason = _check_can_generate("PSEUDO_POLICY", "WATCH_ONLY", 0.5)
        assert not ok
        assert "PSEUDO_POLICY" in reason

    def test_low_coverage(self):
        ok, reason = _check_can_generate("POLICY_AMBUSH", "MIDLINE_POLICY", 0.05)
        assert not ok
        assert "过低" in reason

    def test_critical_missing(self):
        ok, reason = _check_can_generate(
            "POLICY_AMBUSH", "MIDLINE_POLICY", 0.2,
            missing_evidence=["stock_data", "fund_flow", "other"],
        )
        assert not ok
        assert "关键证据缺失" in reason

    def test_normal(self):
        ok, reason = _check_can_generate("POLICY_AMBUSH", "MIDLINE_POLICY", 0.5)
        assert ok
        assert reason == ""

    def test_normal_with_some_missing(self):
        ok, reason = _check_can_generate(
            "POLICY_AMBUSH", "MIDLINE_POLICY", 0.5,
            missing_evidence=["some_field"],
        )
        assert ok


class TestGenerateResearchPlan:
    def test_policy_ambush_midline(self):
        plan = generate_research_plan(
            symbol="600519.SH",
            trade_date="2026-06-07",
            candidate_type="POLICY_AMBUSH",
            research_queue="MIDLINE_POLICY",
            research_intent="policy_validation",
            route_reason="政策左侧→中线",
            evidence_coverage=0.6,
        )
        assert plan.symbol == "600519.SH"
        assert plan.horizon == "medium"
        assert plan.analysis_intent == "entry"
        assert plan.runtime_profile == "MIDLINE_POLICY_LIGHT"
        assert plan.research_queue == "MIDLINE_POLICY"
        assert plan.can_generate is True
        assert plan.plan_markdown != ""

    def test_tech_trade_short(self):
        plan = generate_research_plan(
            symbol="000001.SZ",
            trade_date="2026-06-07",
            candidate_type="TECH_TRADE",
            research_queue="SHORT_TERM_TRADE",
            research_intent="trend_confirmation",
            route_reason="纯技术→短线",
            evidence_coverage=0.5,
        )
        assert plan.horizon == "short"
        assert plan.runtime_profile == "SHORT_TECH_LIGHT"
        assert "technical_analyst" in plan.enabled_modules

    def test_blocked_overheated(self):
        plan = generate_research_plan(
            symbol="000001.SZ",
            trade_date="2026-06-07",
            candidate_type="OVERHEATED_AVOID",
            research_queue="REJECTED",
            evidence_coverage=0.5,
        )
        assert plan.can_generate is False
        assert plan.block_reason != ""

    def test_blocked_low_coverage(self):
        plan = generate_research_plan(
            symbol="000001.SZ",
            trade_date="2026-06-07",
            candidate_type="POLICY_AMBUSH",
            research_queue="MIDLINE_POLICY",
            evidence_coverage=0.05,
        )
        assert plan.can_generate is False

    def test_empty_candidate_type(self):
        plan = generate_research_plan(
            symbol="000001.SZ",
            trade_date="2026-06-07",
            candidate_type="",
            research_queue="",
            evidence_coverage=0.5,
        )
        assert plan.horizon == "short"
        assert plan.runtime_profile == "FULL_TA"


class TestRenderPlanMarkdown:
    def test_basic(self):
        draft = ResearchPlanDraft(
            symbol="600519.SH",
            trade_date="2026-06-07",
            candidate_type="POLICY_AMBUSH",
            research_queue="MIDLINE_POLICY",
            research_intent="policy_validation",
            horizon="medium",
            runtime_profile="MIDLINE_POLICY_LIGHT",
            route_reason="政策左侧→中线",
            enabled_modules=["fundamentals_analyst", "news_analyst"],
            required_evidence=["stock_data", "policy_evidence"],
        )
        md = render_plan_markdown(draft)
        assert "600519.SH" in md
        assert "POLICY_AMBUSH" in md
        assert "MIDLINE_POLICY" in md
        assert "中线" in md
        assert "fundamentals_analyst" in md
        assert "policy_evidence" in md

    def test_blocked(self):
        draft = ResearchPlanDraft(
            symbol="000001.SZ",
            trade_date="2026-06-07",
            candidate_type="OVERHEATED_AVOID",
            can_generate=False,
            block_reason="候选已过热",
        )
        md = render_plan_markdown(draft)
        assert "无法生成" in md
        assert "候选已过热" in md


class TestServiceIntegration:
    def _make_test_db(self) -> str:
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        conn = sqlite3.connect(path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tradeflow_candidates (
                symbol TEXT,
                trade_date TEXT,
                name TEXT,
                tier TEXT DEFAULT '',
                composite_score REAL DEFAULT 0,
                score REAL DEFAULT 0,
                strategy_tags_json TEXT DEFAULT '[]',
                primary_strategy TEXT DEFAULT '',
                trigger_price REAL,
                support_price REAL,
                invalid_price REAL,
                need_deep_ta INTEGER DEFAULT 0,
                observe_state TEXT DEFAULT 'WAITING',
                observe_trigger_count INTEGER DEFAULT 0,
                observe_first_trigger_time TEXT DEFAULT '',
                tradeflow_data_completeness REAL DEFAULT 0,
                missing_data_fields_json TEXT DEFAULT '[]',
                data_completeness REAL DEFAULT 0,
                missing_evidence_json TEXT DEFAULT '[]',
                game_balance TEXT DEFAULT '',
                bull_case TEXT DEFAULT '',
                bear_case TEXT DEFAULT '',
                policy_case TEXT DEFAULT '',
                fund_flow_case TEXT DEFAULT '',
                why_deep_ta TEXT DEFAULT '',
                why_not_deep_ta TEXT DEFAULT '',
                risk_flags_json TEXT DEFAULT '[]',
                policy_tags_json TEXT DEFAULT '[]',
                version_score REAL DEFAULT 0,
                narrative_score REAL DEFAULT 0,
                fund_flow_anomaly_score REAL DEFAULT 0,
                fund_flow_anomaly_tags_json TEXT DEFAULT '[]',
                fund_flow_unit_verified INTEGER DEFAULT 0,
                evidence_gate_applied INTEGER DEFAULT 0,
                deep_ta_status TEXT DEFAULT '',
                deep_ta_dispatch_reason TEXT DEFAULT '',
                deep_ta_model TEXT DEFAULT '',
                source TEXT DEFAULT 'manual',
                universe_sources_json TEXT DEFAULT '[]',
                resonance_count INTEGER DEFAULT 0,
                ta_budget_priority INTEGER DEFAULT 0,
                tier_reason TEXT DEFAULT '',
                missing_evidence_for_upgrade_json TEXT DEFAULT '[]',
                candidate_type TEXT DEFAULT '',
                mandate_score_component REAL DEFAULT 0,
                ambush_score REAL DEFAULT 0,
                mandate_topic TEXT DEFAULT '',
                company_role TEXT DEFAULT '',
                beneficiary_path_json TEXT DEFAULT '[]',
                candidate_type_reason TEXT DEFAULT '',
                deep_ta_route TEXT DEFAULT '',
                research_queue TEXT DEFAULT '',
                research_intent TEXT DEFAULT '',
                research_route_reason TEXT DEFAULT '',
                action_tier TEXT DEFAULT 'scan',
                trade_priority_score REAL DEFAULT 0,
                action_tier_reason TEXT DEFAULT '',
                evidence_json TEXT DEFAULT '{}',
                created_at TEXT DEFAULT '',
                updated_at TEXT DEFAULT '',
                PRIMARY KEY (trade_date, symbol)
            )
        """)
        evidence = json.dumps({"stock_data": True, "fund_flow": True, "announcements": False})
        conn.execute(
            "INSERT INTO tradeflow_candidates (symbol, trade_date, name, candidate_type, research_queue, research_intent, research_route_reason, evidence_json, tradeflow_data_completeness) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("600519.SH", "2026-06-07", "贵州茅台", "POLICY_AMBUSH", "MIDLINE_POLICY", "policy_validation", "政策左侧→中线", evidence, 0.67),
        )
        conn.execute(
            "INSERT INTO tradeflow_candidates (symbol, trade_date, name, candidate_type, research_queue, research_intent, research_route_reason, evidence_json, tradeflow_data_completeness) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("000001.SZ", "2026-06-07", "平安银行", "TECH_TRADE", "SHORT_TERM_TRADE", "trend_confirmation", "纯技术→短线", "{}", 0.0),
        )
        conn.commit()
        conn.close()
        return path

    def test_generate_plan_service(self):
        from api.services.tradeflow_service import generate_research_plan as svc_gen
        db_path = self._make_test_db()
        try:
            result = svc_gen("600519.SH", "2026-06-07", tf_db_path=db_path)
            assert result["status"] == "ok"
            assert result["symbol"] == "600519.SH"
            assert result["candidate_type"] == "POLICY_AMBUSH"
            assert result["research_queue"] == "MIDLINE_POLICY"
            assert result["horizon"] == "medium"
            assert result["runtime_profile"] == "MIDLINE_POLICY_LIGHT"
            assert result["can_generate"] is True
            assert result["plan_markdown"] != ""
        finally:
            os.unlink(db_path)

    def test_generate_plan_no_data(self):
        from api.services.tradeflow_service import generate_research_plan as svc_gen
        db_path = self._make_test_db()
        try:
            result = svc_gen("999999.SH", "2026-06-07", tf_db_path=db_path)
            assert result["status"] == "no_data"
            assert result["can_generate"] is False
        finally:
            os.unlink(db_path)

    def test_generate_plan_tech_trade(self):
        from api.services.tradeflow_service import generate_research_plan as svc_gen
        db_path = self._make_test_db()
        try:
            result = svc_gen("000001.SZ", "2026-06-07", tf_db_path=db_path)
            assert result["status"] == "ok"
            assert result["horizon"] == "short"
            assert result["runtime_profile"] == "SHORT_TECH_LIGHT"
        finally:
            os.unlink(db_path)

    def test_generate_plan_no_db(self):
        from api.services.tradeflow_service import generate_research_plan as svc_gen
        result = svc_gen("600519.SH", "2026-06-07", tf_db_path="/nonexistent/path.db")
        assert result["status"] == "no_data"
        assert result["can_generate"] is False


class TestAcceptanceUI009:
    def test_policy_ambush_default_midline(self):
        plan = generate_research_plan(
            symbol="600519.SH",
            trade_date="2026-06-07",
            candidate_type="POLICY_AMBUSH",
            research_queue="MIDLINE_POLICY",
            research_intent="policy_validation",
            evidence_coverage=0.6,
        )
        assert plan.horizon == "medium"
        assert plan.runtime_profile == "MIDLINE_POLICY_LIGHT"
        assert plan.analysis_intent == "entry"
        assert "policy_validation" in plan.research_intent

    def test_tech_trade_default_short(self):
        plan = generate_research_plan(
            symbol="000001.SZ",
            trade_date="2026-06-07",
            candidate_type="TECH_TRADE",
            research_queue="SHORT_TERM_TRADE",
            research_intent="trend_confirmation",
            evidence_coverage=0.5,
        )
        assert plan.horizon == "short"
        assert plan.runtime_profile == "SHORT_TECH_LIGHT"
        assert "trend_confirmation" in plan.research_intent

    def test_missing_evidence_blocks(self):
        plan = generate_research_plan(
            symbol="000001.SZ",
            trade_date="2026-06-07",
            candidate_type="POLICY_AMBUSH",
            research_queue="MIDLINE_POLICY",
            evidence_coverage=0.05,
        )
        assert plan.can_generate is False
        assert plan.block_reason != ""

    def test_plan_markdown_rendered(self):
        plan = generate_research_plan(
            symbol="600519.SH",
            trade_date="2026-06-07",
            candidate_type="POLICY_AMBUSH",
            research_queue="MIDLINE_POLICY",
            research_intent="policy_validation",
            evidence_coverage=0.6,
        )
        assert plan.plan_markdown != ""
        assert "600519.SH" in plan.plan_markdown

    def test_service_roundtrip(self):
        from api.services.tradeflow_service import generate_research_plan as svc_gen
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        conn = sqlite3.connect(db_path)
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
            ("600519.SH", "2026-06-07", "贵州茅台", "POLICY_AMBUSH", "MIDLINE_POLICY", "policy_validation", "test", '{"a": true, "b": true}', 0.8),
        )
        conn.commit()
        conn.close()
        try:
            result = svc_gen("600519.SH", "2026-06-07", tf_db_path=db_path)
            assert result["status"] == "ok"
            assert result["can_generate"] is True
            assert result["plan_markdown"] != ""
        finally:
            os.unlink(db_path)

    def test_no_strong_buy_sell_words(self):
        plan = generate_research_plan(
            symbol="600519.SH",
            trade_date="2026-06-07",
            candidate_type="POLICY_AMBUSH",
            research_queue="MIDLINE_POLICY",
            evidence_coverage=0.6,
        )
        md = plan.plan_markdown.lower()
        for w in ["买入", "卖出", "清仓", "重仓", "加仓", "减仓"]:
            assert w not in md, f"Found strong word '{w}' in plan markdown"

    def test_rejected_candidate_cannot_generate(self):
        plan = generate_research_plan(
            symbol="000001.SZ",
            trade_date="2026-06-07",
            candidate_type="OVERHEATED_AVOID",
            research_queue="REJECTED",
            evidence_coverage=0.5,
        )
        assert plan.can_generate is False
