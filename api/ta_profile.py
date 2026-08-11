# [PERF-002] lightweight_ta_profiles
"""Lightweight TA Profile definitions and module routing.

Defines four profiles that control which analyst modules run during analysis:
- MIDLINE_POLICY_LIGHT: for POLICY_AMBUSH/POLICY_CONFIRM candidates
- SHORT_TECH_LIGHT: for TECH_TRADE candidates
- POSITION_RISK_LIGHT: for holding review
- FULL_TA: complete 14-agent analysis (requires confirmation)

Each profile declares which analyst/manager/risk modules are enabled.
TradeFlow candidates are automatically routed to the appropriate profile
based on candidate_type, research_queue, and analysis_intent.

Constraints:
- Does not delete FULL_TA.
- Does not change prompts.
- Does not auto-call live LLM; routing config only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set


class TAProfile(str, Enum):
    MIDLINE_POLICY_LIGHT = "MIDLINE_POLICY_LIGHT"
    SHORT_TECH_LIGHT = "SHORT_TECH_LIGHT"
    POSITION_RISK_LIGHT = "POSITION_RISK_LIGHT"
    FULL_TA = "FULL_TA"


@dataclass
class TAProfileSpec:
    profile: TAProfile
    label_cn: str
    tier: str
    expected_latency: str
    llm_allowed: bool
    requires_confirmation: bool
    cost_risk: str
    description: str
    enabled_analysts: List[str]
    enabled_risk_modules: List[str]
    enabled_managers: List[str]

    def enabled_analyst_set(self) -> Set[str]:
        return set(self.enabled_analysts)

    def enabled_modules_all(self) -> Set[str]:
        modules = set(self.enabled_analysts)
        modules.update(self.enabled_risk_modules)
        modules.update(self.enabled_managers)
        return modules


_ALL_ANALYSTS = [
    "fundamentals", "news", "macro", "market",
    "social", "smart_money", "volume_price",
]

_ALL_RISK_MODULES = [
    "aggressive", "neutral", "conservative",
]

_ALL_MANAGERS = [
    "bull", "bear", "research_manager",
    "trader", "portfolio_manager",
]

_PROFILE_SPECS: Dict[TAProfile, TAProfileSpec] = {
    TAProfile.MIDLINE_POLICY_LIGHT: TAProfileSpec(
        profile=TAProfile.MIDLINE_POLICY_LIGHT,
        label_cn="中线政策轻量",
        tier="LIGHT_RESEARCH",
        expected_latency="1-3min",
        llm_allowed=True,
        requires_confirmation=False,
        cost_risk="low",
        description="中线政策验证：政策/公告/基本面/风险/量价，跳过情绪和技术细节。",
        enabled_analysts=[
            "fundamentals", "news", "macro", "smart_money", "volume_price",
        ],
        enabled_risk_modules=["aggressive", "neutral", "conservative"],
        enabled_managers=["bull", "bear", "research_manager", "trader"],
    ),
    TAProfile.SHORT_TECH_LIGHT: TAProfileSpec(
        profile=TAProfile.SHORT_TECH_LIGHT,
        label_cn="短线技术轻量",
        tier="LIGHT_RESEARCH",
        expected_latency="1-2min",
        llm_allowed=True,
        requires_confirmation=False,
        cost_risk="low",
        description="短线技术确认：技术/量价/资金/风险，跳过基本面和宏观。",
        enabled_analysts=[
            "market", "smart_money", "volume_price", "news",
        ],
        enabled_risk_modules=["aggressive", "neutral"],
        enabled_managers=["bull", "bear", "research_manager", "trader"],
    ),
    TAProfile.POSITION_RISK_LIGHT: TAProfileSpec(
        profile=TAProfile.POSITION_RISK_LIGHT,
        label_cn="持仓风控轻量",
        tier="LIGHT_RESEARCH",
        expected_latency="1-2min",
        llm_allowed=True,
        requires_confirmation=False,
        cost_risk="low",
        description="持仓复盘：风控/资金/量价/公告，跳过情绪和宏观。",
        enabled_analysts=[
            "fundamentals", "news", "smart_money", "volume_price",
        ],
        enabled_risk_modules=["aggressive", "neutral", "conservative"],
        enabled_managers=["bull", "bear", "research_manager", "trader", "portfolio_manager"],
    ),
    TAProfile.FULL_TA: TAProfileSpec(
        profile=TAProfile.FULL_TA,
        label_cn="完整 TA",
        tier="FULL_TA",
        expected_latency="10-20min",
        llm_allowed=True,
        requires_confirmation=True,
        cost_risk="high",
        description="完整 14 Agent TA，多空辩论和风控全链路，必须人工确认。",
        enabled_analysts=list(_ALL_ANALYSTS),
        enabled_risk_modules=list(_ALL_RISK_MODULES),
        enabled_managers=list(_ALL_MANAGERS),
    ),
}

_CANDIDATE_TYPE_PROFILE_MAP: Dict[str, TAProfile] = {
    "POLICY_AMBUSH": TAProfile.MIDLINE_POLICY_LIGHT,
    "POLICY_CONFIRM": TAProfile.MIDLINE_POLICY_LIGHT,
    "TECH_TRADE": TAProfile.SHORT_TECH_LIGHT,
    "EVENT_WATCH": TAProfile.SHORT_TECH_LIGHT,
}

_RESEARCH_QUEUE_PROFILE_MAP: Dict[str, TAProfile] = {
    "MIDLINE_POLICY": TAProfile.MIDLINE_POLICY_LIGHT,
    "TA_CONFIRM": TAProfile.MIDLINE_POLICY_LIGHT,
    "SHORT_TERM_TRADE": TAProfile.SHORT_TECH_LIGHT,
}

_ANALYSIS_INTENT_PROFILE_MAP: Dict[str, TAProfile] = {
    "holding": TAProfile.POSITION_RISK_LIGHT,
    "add": TAProfile.POSITION_RISK_LIGHT,
    "reduce": TAProfile.POSITION_RISK_LIGHT,
    "stop_loss": TAProfile.POSITION_RISK_LIGHT,
    "entry": TAProfile.MIDLINE_POLICY_LIGHT,
    "watch": TAProfile.SHORT_TECH_LIGHT,
}


def get_profile_spec(profile: TAProfile) -> TAProfileSpec:
    return _PROFILE_SPECS[profile]


def all_profile_specs() -> List[TAProfileSpec]:
    return list(_PROFILE_SPECS.values())


def profile_labels_cn() -> Dict[str, str]:
    return {p.value: _PROFILE_SPECS[p].label_cn for p in TAProfile}


def profile_to_meta(profile: TAProfile) -> dict:
    spec = get_profile_spec(profile)
    return {
        "runtime_profile": spec.profile.value,
        "profile_label": spec.label_cn,
        "tier": spec.tier,
        "expected_latency": spec.expected_latency,
        "llm_allowed": spec.llm_allowed,
        "requires_confirmation": spec.requires_confirmation,
        "cost_risk": spec.cost_risk,
        "description": spec.description,
        "enabled_analysts": list(spec.enabled_analysts),
        "enabled_risk_modules": list(spec.enabled_risk_modules),
        "enabled_managers": list(spec.enabled_managers),
    }


def recommend_profile(
    candidate_type: str = "",
    research_queue: str = "",
    analysis_intent: str = "",
    has_position: Optional[bool] = None,
) -> TAProfile:
    if has_position is True:
        return TAProfile.POSITION_RISK_LIGHT

    if research_queue and research_queue in _RESEARCH_QUEUE_PROFILE_MAP:
        return _RESEARCH_QUEUE_PROFILE_MAP[research_queue]

    if candidate_type and candidate_type in _CANDIDATE_TYPE_PROFILE_MAP:
        return _CANDIDATE_TYPE_PROFILE_MAP[candidate_type]

    if analysis_intent and analysis_intent in _ANALYSIS_INTENT_PROFILE_MAP:
        return _ANALYSIS_INTENT_PROFILE_MAP[analysis_intent]

    return TAProfile.FULL_TA


def profile_enabled_analysts(profile: TAProfile) -> List[str]:
    return list(get_profile_spec(profile).enabled_analysts)


def is_module_enabled(profile: TAProfile, module_name: str) -> bool:
    return module_name in get_profile_spec(profile).enabled_modules_all()


def filter_analysts_for_profile(
    profile: TAProfile,
    selected_analysts: Optional[List[str]] = None,
) -> List[str]:
    spec = get_profile_spec(profile)
    enabled = spec.enabled_analyst_set()
    if selected_analysts is None:
        return list(enabled)
    return [a for a in selected_analysts if a in enabled]
