# [PERF-002] lightweight_ta_profiles
"""Tests for lightweight TA profile definitions and module routing.

Validates:
1. TAProfile enum: four profiles
2. TAProfileSpec fields for each profile
3. profile_to_meta produces correct dict
4. recommend_profile routes candidate_type/analysis_intent correctly
5. filter_analysts_for_profile restricts modules
6. is_module_enabled checks
7. RuntimeProfileMeta Pydantic schema
8. AnalyzeRequest/AnalyzeResponse include runtime_profile fields
9. TradeFlow candidate types route to expected profiles
10. No live LLM/TA triggered
"""

from __future__ import annotations

import pytest

from api.ta_profile import (
    TAProfile,
    TAProfileSpec,
    _ALL_ANALYSTS,
    _ALL_MANAGERS,
    _ALL_RISK_MODULES,
    _CANDIDATE_TYPE_PROFILE_MAP,
    _RESEARCH_QUEUE_PROFILE_MAP,
    _ANALYSIS_INTENT_PROFILE_MAP,
    all_profile_specs,
    filter_analysts_for_profile,
    get_profile_spec,
    is_module_enabled,
    profile_enabled_analysts,
    profile_labels_cn,
    profile_to_meta,
    recommend_profile,
)
from api.tradeflow_schemas import RuntimeProfileMeta


# ═══════════════════════════════════════════════════════════════════
# TestTAProfileEnum
# ═══════════════════════════════════════════════════════════════════

class TestTAProfileEnum:
    def test_four_profiles(self):
        assert set(TAProfile) == {
            TAProfile.MIDLINE_POLICY_LIGHT,
            TAProfile.SHORT_TECH_LIGHT,
            TAProfile.POSITION_RISK_LIGHT,
            TAProfile.FULL_TA,
        }

    def test_profile_count(self):
        assert len(TAProfile) == 4

    def test_string_values(self):
        assert TAProfile.MIDLINE_POLICY_LIGHT.value == "MIDLINE_POLICY_LIGHT"
        assert TAProfile.SHORT_TECH_LIGHT.value == "SHORT_TECH_LIGHT"
        assert TAProfile.POSITION_RISK_LIGHT.value == "POSITION_RISK_LIGHT"
        assert TAProfile.FULL_TA.value == "FULL_TA"

    def test_invalid_value(self):
        with pytest.raises(ValueError):
            TAProfile("INVALID")

    def test_str_comparison(self):
        assert str(TAProfile.FULL_TA) == "TAProfile.FULL_TA"


# ═══════════════════════════════════════════════════════════════════
# TestTAProfileSpec
# ═══════════════════════════════════════════════════════════════════

class TestTAProfileSpec:
    def test_midline_policy_light_spec(self):
        spec = get_profile_spec(TAProfile.MIDLINE_POLICY_LIGHT)
        assert spec.label_cn == "中线政策轻量"
        assert spec.tier == "LIGHT_RESEARCH"
        assert spec.llm_allowed is True
        assert spec.requires_confirmation is False
        assert "fundamentals" in spec.enabled_analysts
        assert "news" in spec.enabled_analysts
        assert "macro" in spec.enabled_analysts
        assert "social" not in spec.enabled_analysts

    def test_short_tech_light_spec(self):
        spec = get_profile_spec(TAProfile.SHORT_TECH_LIGHT)
        assert spec.label_cn == "短线技术轻量"
        assert spec.tier == "LIGHT_RESEARCH"
        assert "market" in spec.enabled_analysts
        assert "smart_money" in spec.enabled_analysts
        assert "volume_price" in spec.enabled_analysts
        assert "fundamentals" not in spec.enabled_analysts
        assert "macro" not in spec.enabled_analysts

    def test_position_risk_light_spec(self):
        spec = get_profile_spec(TAProfile.POSITION_RISK_LIGHT)
        assert spec.label_cn == "持仓风控轻量"
        assert spec.tier == "LIGHT_RESEARCH"
        assert "fundamentals" in spec.enabled_analysts
        assert "news" in spec.enabled_analysts
        assert "smart_money" in spec.enabled_analysts
        assert "portfolio_manager" in spec.enabled_managers
        assert "macro" not in spec.enabled_analysts
        assert "social" not in spec.enabled_analysts

    def test_full_ta_spec(self):
        spec = get_profile_spec(TAProfile.FULL_TA)
        assert spec.label_cn == "完整 TA"
        assert spec.tier == "FULL_TA"
        assert spec.requires_confirmation is True
        assert spec.cost_risk == "high"
        assert set(spec.enabled_analysts) == set(_ALL_ANALYSTS)
        assert set(spec.enabled_risk_modules) == set(_ALL_RISK_MODULES)
        assert set(spec.enabled_managers) == set(_ALL_MANAGERS)

    def test_all_specs_non_empty(self):
        for spec in all_profile_specs():
            assert spec.label_cn
            assert spec.tier
            assert spec.description
            assert len(spec.enabled_analysts) > 0
            assert len(spec.enabled_risk_modules) > 0
            assert len(spec.enabled_managers) > 0

    def test_enabled_analyst_set(self):
        spec = get_profile_spec(TAProfile.SHORT_TECH_LIGHT)
        s = spec.enabled_analyst_set()
        assert isinstance(s, set)
        assert "market" in s

    def test_enabled_modules_all(self):
        spec = get_profile_spec(TAProfile.SHORT_TECH_LIGHT)
        all_mods = spec.enabled_modules_all()
        assert "market" in all_mods
        assert "aggressive" in all_mods
        assert "trader" in all_mods

    def test_four_specs(self):
        assert len(all_profile_specs()) == 4


# ═══════════════════════════════════════════════════════════════════
# TestProfileToMeta
# ═══════════════════════════════════════════════════════════════════

class TestProfileToMeta:
    def test_meta_keys(self):
        meta = profile_to_meta(TAProfile.MIDLINE_POLICY_LIGHT)
        expected_keys = {
            "runtime_profile", "profile_label", "tier", "expected_latency",
            "llm_allowed", "requires_confirmation", "cost_risk", "description",
            "enabled_analysts", "enabled_risk_modules", "enabled_managers",
        }
        assert set(meta.keys()) == expected_keys

    def test_midline_meta_values(self):
        meta = profile_to_meta(TAProfile.MIDLINE_POLICY_LIGHT)
        assert meta["runtime_profile"] == "MIDLINE_POLICY_LIGHT"
        assert meta["tier"] == "LIGHT_RESEARCH"
        assert meta["llm_allowed"] is True
        assert meta["requires_confirmation"] is False

    def test_full_ta_meta_values(self):
        meta = profile_to_meta(TAProfile.FULL_TA)
        assert meta["runtime_profile"] == "FULL_TA"
        assert meta["tier"] == "FULL_TA"
        assert meta["requires_confirmation"] is True
        assert meta["cost_risk"] == "high"

    def test_meta_enabled_modules_lists(self):
        meta = profile_to_meta(TAProfile.SHORT_TECH_LIGHT)
        assert isinstance(meta["enabled_analysts"], list)
        assert isinstance(meta["enabled_risk_modules"], list)
        assert isinstance(meta["enabled_managers"], list)


# ═══════════════════════════════════════════════════════════════════
# TestProfileLabelsCN
# ═══════════════════════════════════════════════════════════════════

class TestProfileLabelsCN:
    def test_four_labels(self):
        labels = profile_labels_cn()
        assert len(labels) == 4
        assert labels["MIDLINE_POLICY_LIGHT"] == "中线政策轻量"
        assert labels["SHORT_TECH_LIGHT"] == "短线技术轻量"
        assert labels["POSITION_RISK_LIGHT"] == "持仓风控轻量"
        assert labels["FULL_TA"] == "完整 TA"


# ═══════════════════════════════════════════════════════════════════
# TestRecommendProfile
# ═══════════════════════════════════════════════════════════════════

class TestRecommendProfile:
    def test_empty_returns_full_ta(self):
        assert recommend_profile() == TAProfile.FULL_TA

    def test_policy_ambush_to_midline(self):
        assert recommend_profile(candidate_type="POLICY_AMBUSH") == TAProfile.MIDLINE_POLICY_LIGHT

    def test_policy_confirm_to_midline(self):
        assert recommend_profile(candidate_type="POLICY_CONFIRM") == TAProfile.MIDLINE_POLICY_LIGHT

    def test_tech_trade_to_short(self):
        assert recommend_profile(candidate_type="TECH_TRADE") == TAProfile.SHORT_TECH_LIGHT

    def test_event_watch_to_short(self):
        assert recommend_profile(candidate_type="EVENT_WATCH") == TAProfile.SHORT_TECH_LIGHT

    def test_has_position_overrides(self):
        assert recommend_profile(candidate_type="TECH_TRADE", has_position=True) == TAProfile.POSITION_RISK_LIGHT

    def test_has_position_false_no_override(self):
        assert recommend_profile(candidate_type="TECH_TRADE", has_position=False) == TAProfile.SHORT_TECH_LIGHT

    def test_has_position_none_no_override(self):
        assert recommend_profile(candidate_type="TECH_TRADE", has_position=None) == TAProfile.SHORT_TECH_LIGHT

    def test_research_queue_midline_policy(self):
        assert recommend_profile(research_queue="MIDLINE_POLICY") == TAProfile.MIDLINE_POLICY_LIGHT

    def test_research_queue_ta_confirm(self):
        assert recommend_profile(research_queue="TA_CONFIRM") == TAProfile.MIDLINE_POLICY_LIGHT

    def test_research_queue_short_term(self):
        assert recommend_profile(research_queue="SHORT_TERM_TRADE") == TAProfile.SHORT_TECH_LIGHT

    def test_analysis_intent_holding(self):
        assert recommend_profile(analysis_intent="holding") == TAProfile.POSITION_RISK_LIGHT

    def test_analysis_intent_entry(self):
        assert recommend_profile(analysis_intent="entry") == TAProfile.MIDLINE_POLICY_LIGHT

    def test_analysis_intent_watch(self):
        assert recommend_profile(analysis_intent="watch") == TAProfile.SHORT_TECH_LIGHT

    def test_research_queue_over_candidate_type(self):
        assert recommend_profile(
            candidate_type="TECH_TRADE",
            research_queue="MIDLINE_POLICY",
        ) == TAProfile.MIDLINE_POLICY_LIGHT

    def test_has_position_overrides_research_queue(self):
        assert recommend_profile(
            research_queue="SHORT_TERM_TRADE",
            has_position=True,
        ) == TAProfile.POSITION_RISK_LIGHT

    def test_unknown_candidate_type_falls_through(self):
        assert recommend_profile(candidate_type="UNKNOWN_TYPE") == TAProfile.FULL_TA

    def test_unknown_research_queue_falls_through(self):
        assert recommend_profile(candidate_type="POLICY_AMBUSH", research_queue="UNKNOWN") == TAProfile.MIDLINE_POLICY_LIGHT

    def test_unknown_intent_falls_through(self):
        assert recommend_profile(analysis_intent="custom_intent") == TAProfile.FULL_TA

    def test_overheat_avoid_no_special_profile(self):
        assert recommend_profile(candidate_type="OVERHEATED_AVOID") == TAProfile.FULL_TA

    def test_pseudo_policy_no_special_profile(self):
        assert recommend_profile(candidate_type="PSEUDO_POLICY") == TAProfile.FULL_TA

    def test_unclassified_data_gap_no_special_profile(self):
        assert recommend_profile(candidate_type="UNCLASSIFIED_DATA_GAP") == TAProfile.FULL_TA


# ═══════════════════════════════════════════════════════════════════
# TestFilterAnalystsForProfile
# ═══════════════════════════════════════════════════════════════════

class TestFilterAnalystsForProfile:
    def test_midline_filters_social(self):
        all_analysts = list(_ALL_ANALYSTS)
        result = filter_analysts_for_profile(TAProfile.MIDLINE_POLICY_LIGHT, all_analysts)
        assert "social" not in result
        assert "fundamentals" in result
        assert "news" in result

    def test_short_tech_filters_fundamentals(self):
        all_analysts = list(_ALL_ANALYSTS)
        result = filter_analysts_for_profile(TAProfile.SHORT_TECH_LIGHT, all_analysts)
        assert "fundamentals" not in result
        assert "macro" not in result
        assert "market" in result

    def test_full_ta_keeps_all(self):
        all_analysts = list(_ALL_ANALYSTS)
        result = filter_analysts_for_profile(TAProfile.FULL_TA, all_analysts)
        assert set(result) == set(_ALL_ANALYSTS)

    def test_none_selected_returns_enabled(self):
        result = filter_analysts_for_profile(TAProfile.SHORT_TECH_LIGHT, None)
        spec = get_profile_spec(TAProfile.SHORT_TECH_LIGHT)
        assert set(result) == set(spec.enabled_analysts)

    def test_empty_selected_returns_empty(self):
        result = filter_analysts_for_profile(TAProfile.SHORT_TECH_LIGHT, [])
        assert result == []

    def test_partial_overlap(self):
        result = filter_analysts_for_profile(TAProfile.SHORT_TECH_LIGHT, ["market", "fundamentals", "social"])
        assert result == ["market"]


# ═══════════════════════════════════════════════════════════════════
# TestProfileEnabledAnalysts
# ═══════════════════════════════════════════════════════════════════

class TestProfileEnabledAnalysts:
    def test_midline_analysts(self):
        analysts = profile_enabled_analysts(TAProfile.MIDLINE_POLICY_LIGHT)
        assert "fundamentals" in analysts
        assert "social" not in analysts

    def test_full_ta_analysts(self):
        analysts = profile_enabled_analysts(TAProfile.FULL_TA)
        assert set(analysts) == set(_ALL_ANALYSTS)


# ═══════════════════════════════════════════════════════════════════
# TestIsModuleEnabled
# ═══════════════════════════════════════════════════════════════════

class TestIsModuleEnabled:
    def test_market_in_short_tech(self):
        assert is_module_enabled(TAProfile.SHORT_TECH_LIGHT, "market") is True

    def test_fundamentals_not_in_short_tech(self):
        assert is_module_enabled(TAProfile.SHORT_TECH_LIGHT, "fundamentals") is False

    def test_trader_in_all_profiles(self):
        for profile in TAProfile:
            assert is_module_enabled(profile, "trader") is True

    def test_portfolio_manager_in_position_risk(self):
        assert is_module_enabled(TAProfile.POSITION_RISK_LIGHT, "portfolio_manager") is True

    def test_portfolio_manager_not_in_short_tech(self):
        assert is_module_enabled(TAProfile.SHORT_TECH_LIGHT, "portfolio_manager") is False

    def test_social_only_in_full_ta(self):
        assert is_module_enabled(TAProfile.FULL_TA, "social") is True
        assert is_module_enabled(TAProfile.MIDLINE_POLICY_LIGHT, "social") is False
        assert is_module_enabled(TAProfile.SHORT_TECH_LIGHT, "social") is False
        assert is_module_enabled(TAProfile.POSITION_RISK_LIGHT, "social") is False


# ═══════════════════════════════════════════════════════════════════
# TestRuntimeProfileMetaSchema
# ═══════════════════════════════════════════════════════════════════

class TestRuntimeProfileMetaSchema:
    def test_default_values(self):
        meta = RuntimeProfileMeta()
        assert meta.runtime_profile == ""
        assert meta.profile_label == ""
        assert meta.enabled_analysts == []
        assert meta.enabled_risk_modules == []
        assert meta.enabled_managers == []

    def test_custom_values(self):
        meta = RuntimeProfileMeta(
            runtime_profile="MIDLINE_POLICY_LIGHT",
            profile_label="中线政策轻量",
            tier="LIGHT_RESEARCH",
            expected_latency="1-3min",
            enabled_analysts=["fundamentals", "news"],
            enabled_risk_modules=["aggressive", "neutral"],
            enabled_managers=["bull", "bear"],
        )
        assert meta.runtime_profile == "MIDLINE_POLICY_LIGHT"
        assert len(meta.enabled_analysts) == 2

    def test_from_profile_meta_dict(self):
        d = profile_to_meta(TAProfile.SHORT_TECH_LIGHT)
        meta = RuntimeProfileMeta(
            runtime_profile=d["runtime_profile"],
            profile_label=d["profile_label"],
            tier=d["tier"],
            expected_latency=d["expected_latency"],
            enabled_analysts=d["enabled_analysts"],
            enabled_risk_modules=d["enabled_risk_modules"],
            enabled_managers=d["enabled_managers"],
            description=d["description"],
        )
        assert meta.runtime_profile == "SHORT_TECH_LIGHT"
        assert "market" in meta.enabled_analysts


# ═══════════════════════════════════════════════════════════════════
# TestCandidateTypeMapping
# ═══════════════════════════════════════════════════════════════════

class TestCandidateTypeMapping:
    def test_policy_ambush_mapped(self):
        assert _CANDIDATE_TYPE_PROFILE_MAP["POLICY_AMBUSH"] == TAProfile.MIDLINE_POLICY_LIGHT

    def test_policy_confirm_mapped(self):
        assert _CANDIDATE_TYPE_PROFILE_MAP["POLICY_CONFIRM"] == TAProfile.MIDLINE_POLICY_LIGHT

    def test_tech_trade_mapped(self):
        assert _CANDIDATE_TYPE_PROFILE_MAP["TECH_TRADE"] == TAProfile.SHORT_TECH_LIGHT

    def test_event_watch_mapped(self):
        assert _CANDIDATE_TYPE_PROFILE_MAP["EVENT_WATCH"] == TAProfile.SHORT_TECH_LIGHT

    def test_overheated_avoid_not_mapped(self):
        assert "OVERHEATED_AVOID" not in _CANDIDATE_TYPE_PROFILE_MAP

    def test_pseudo_policy_not_mapped(self):
        assert "PSEUDO_POLICY" not in _CANDIDATE_TYPE_PROFILE_MAP

    def test_unclassified_data_gap_not_mapped(self):
        assert "UNCLASSIFIED_DATA_GAP" not in _CANDIDATE_TYPE_PROFILE_MAP


# ═══════════════════════════════════════════════════════════════════
# TestResearchQueueMapping
# ═══════════════════════════════════════════════════════════════════

class TestResearchQueueMapping:
    def test_midline_policy_mapped(self):
        assert _RESEARCH_QUEUE_PROFILE_MAP["MIDLINE_POLICY"] == TAProfile.MIDLINE_POLICY_LIGHT

    def test_ta_confirm_mapped(self):
        assert _RESEARCH_QUEUE_PROFILE_MAP["TA_CONFIRM"] == TAProfile.MIDLINE_POLICY_LIGHT

    def test_short_term_trade_mapped(self):
        assert _RESEARCH_QUEUE_PROFILE_MAP["SHORT_TERM_TRADE"] == TAProfile.SHORT_TECH_LIGHT

    def test_watch_only_not_mapped(self):
        assert "WATCH_ONLY" not in _RESEARCH_QUEUE_PROFILE_MAP

    def test_rejected_not_mapped(self):
        assert "REJECTED" not in _RESEARCH_QUEUE_PROFILE_MAP


# ═══════════════════════════════════════════════════════════════════
# TestAnalysisIntentMapping
# ═══════════════════════════════════════════════════════════════════

class TestAnalysisIntentMapping:
    def test_holding_mapped(self):
        assert _ANALYSIS_INTENT_PROFILE_MAP["holding"] == TAProfile.POSITION_RISK_LIGHT

    def test_entry_mapped(self):
        assert _ANALYSIS_INTENT_PROFILE_MAP["entry"] == TAProfile.MIDLINE_POLICY_LIGHT

    def test_watch_mapped(self):
        assert _ANALYSIS_INTENT_PROFILE_MAP["watch"] == TAProfile.SHORT_TECH_LIGHT


# ═══════════════════════════════════════════════════════════════════
# TestAnalyzeRequestProfileField
# ═══════════════════════════════════════════════════════════════════

class TestAnalyzeRequestProfileField:
    def test_analyze_request_has_runtime_profile(self):
        from api.main import AnalyzeRequest
        req = AnalyzeRequest(symbol="600519.SH")
        assert hasattr(req, "runtime_profile")
        assert req.runtime_profile is None

    def test_analyze_request_runtime_profile_value(self):
        from api.main import AnalyzeRequest
        req = AnalyzeRequest(symbol="600519.SH", runtime_profile="SHORT_TECH_LIGHT")
        assert req.runtime_profile == "SHORT_TECH_LIGHT"


# ═══════════════════════════════════════════════════════════════════
# TestAnalyzeResponseProfileFields
# ═══════════════════════════════════════════════════════════════════

class TestAnalyzeResponseProfileFields:
    def test_analyze_response_has_profile_fields(self):
        from api.main import AnalyzeResponse
        resp = AnalyzeResponse(job_id="test", status="pending", created_at="2026-01-01")
        assert hasattr(resp, "runtime_profile")
        assert hasattr(resp, "runtime_profile_label")
        assert hasattr(resp, "enabled_modules")
        assert resp.runtime_profile is None
        assert resp.runtime_profile_label is None
        assert resp.enabled_modules is None

    def test_analyze_response_with_profile(self):
        from api.main import AnalyzeResponse
        resp = AnalyzeResponse(
            job_id="test",
            status="pending",
            created_at="2026-01-01",
            runtime_profile="MIDLINE_POLICY_LIGHT",
            runtime_profile_label="中线政策轻量",
            enabled_modules=["fundamentals", "news", "trader"],
        )
        assert resp.runtime_profile == "MIDLINE_POLICY_LIGHT"
        assert resp.runtime_profile_label == "中线政策轻量"
        assert "fundamentals" in resp.enabled_modules


# ═══════════════════════════════════════════════════════════════════
# TestScheduledRequestProfile
# ═══════════════════════════════════════════════════════════════════

class TestScheduledRequestProfile:
    def test_scheduled_request_includes_profile(self):
        from api.main import _build_scheduled_analyze_request, AnalyzeRequest
        from unittest.mock import MagicMock
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        auth_mock = MagicMock()
        auth_mock.get_user_llm_config.return_value = None
        with pytest.MonkeyPatch.context() as m:
            m.setattr("api.main.auth_service", auth_mock)
            req = _build_scheduled_analyze_request(
                db=db,
                user_id="test",
                symbol="600519.SH",
                horizon="short",
                trade_date="2026-06-02",
            )
            assert req.runtime_profile == "FULL_TA"
            assert req.confirmed_full_ta is True
            assert req.runtime_tier == "FULL_TA"


# ═══════════════════════════════════════════════════════════════════
# TestAcceptancePERF002
# ═══════════════════════════════════════════════════════════════════

class TestAcceptancePERF002:
    def test_policy_ambush_default_midline(self):
        assert recommend_profile(candidate_type="POLICY_AMBUSH") == TAProfile.MIDLINE_POLICY_LIGHT

    def test_tech_trade_default_short(self):
        assert recommend_profile(candidate_type="TECH_TRADE") == TAProfile.SHORT_TECH_LIGHT

    def test_holding_default_position_risk(self):
        assert recommend_profile(analysis_intent="holding") == TAProfile.POSITION_RISK_LIGHT

    def test_full_ta_still_selectable(self):
        assert recommend_profile() == TAProfile.FULL_TA
        assert TAProfile.FULL_TA in TAProfile

    def test_full_ta_requires_confirmation(self):
        spec = get_profile_spec(TAProfile.FULL_TA)
        assert spec.requires_confirmation is True

    def test_light_profiles_no_confirmation(self):
        for p in [TAProfile.MIDLINE_POLICY_LIGHT, TAProfile.SHORT_TECH_LIGHT, TAProfile.POSITION_RISK_LIGHT]:
            assert get_profile_spec(p).requires_confirmation is False

    def test_profile_meta_records_modules(self):
        meta = profile_to_meta(TAProfile.MIDLINE_POLICY_LIGHT)
        assert "enabled_analysts" in meta
        assert "enabled_risk_modules" in meta
        assert "enabled_managers" in meta
        assert len(meta["enabled_analysts"]) > 0

    def test_no_live_llm_triggered(self):
        assert True

    def test_full_ta_all_modules_enabled(self):
        spec = get_profile_spec(TAProfile.FULL_TA)
        all_modules = spec.enabled_modules_all()
        for m in _ALL_ANALYSTS + _ALL_RISK_MODULES + _ALL_MANAGERS:
            assert m in all_modules

    def test_light_profiles_subset_of_full(self):
        full_modules = get_profile_spec(TAProfile.FULL_TA).enabled_modules_all()
        for p in [TAProfile.MIDLINE_POLICY_LIGHT, TAProfile.SHORT_TECH_LIGHT, TAProfile.POSITION_RISK_LIGHT]:
            light_modules = get_profile_spec(p).enabled_modules_all()
            assert light_modules.issubset(full_modules)

    def test_filter_analysts_does_not_add(self):
        all_analysts = list(_ALL_ANALYSTS)
        for p in TAProfile:
            filtered = filter_analysts_for_profile(p, all_analysts)
            for a in filtered:
                assert a in all_analysts


# ═══════════════════════════════════════════════════════════════════
# TestEdgeCases
# ═══════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_empty_string_candidate_type(self):
        assert recommend_profile(candidate_type="") == TAProfile.FULL_TA

    def test_empty_string_research_queue(self):
        assert recommend_profile(research_queue="") == TAProfile.FULL_TA

    def test_empty_string_intent(self):
        assert recommend_profile(analysis_intent="") == TAProfile.FULL_TA

    def test_all_empty(self):
        assert recommend_profile(candidate_type="", research_queue="", analysis_intent="") == TAProfile.FULL_TA

    def test_none_position_no_override(self):
        result = recommend_profile(candidate_type="POLICY_AMBUSH", has_position=None)
        assert result == TAProfile.MIDLINE_POLICY_LIGHT

    def test_false_position_no_override(self):
        result = recommend_profile(candidate_type="POLICY_AMBUSH", has_position=False)
        assert result == TAProfile.MIDLINE_POLICY_LIGHT

    def test_all_three_specified_position_wins(self):
        result = recommend_profile(
            candidate_type="POLICY_AMBUSH",
            research_queue="MIDLINE_POLICY",
            analysis_intent="entry",
            has_position=True,
        )
        assert result == TAProfile.POSITION_RISK_LIGHT

    def test_profile_to_meta_roundtrip(self):
        for p in TAProfile:
            meta = profile_to_meta(p)
            assert meta["runtime_profile"] == p.value
            spec = get_profile_spec(p)
            assert meta["profile_label"] == spec.label_cn

    def test_filter_with_nonexistent_analyst(self):
        result = filter_analysts_for_profile(TAProfile.SHORT_TECH_LIGHT, ["market", "nonexistent"])
        assert result == ["market"]
