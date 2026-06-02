# [PERF-001] runtime_tier_contract
"""Runtime tier contract for TradeFlow, light TA, and full TA.

Defines three tiers with latency budgets, LLM access, and confirmation gates.
All API endpoints and UI actions must declare their tier.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class RuntimeTier(str, Enum):
    FAST_RADAR = "FAST_RADAR"
    LIGHT_RESEARCH = "LIGHT_RESEARCH"
    FULL_TA = "FULL_TA"


@dataclass
class RuntimeTierSpec:
    tier: RuntimeTier
    label_cn: str
    expected_latency: str
    llm_allowed: bool
    requires_confirmation: bool
    cost_risk: str
    description: str


_TIER_SPECS: Dict[RuntimeTier, RuntimeTierSpec] = {
    RuntimeTier.FAST_RADAR: RuntimeTierSpec(
        tier=RuntimeTier.FAST_RADAR,
        label_cn="快速筛选",
        expected_latency="5-30s",
        llm_allowed=False,
        requires_confirmation=False,
        cost_risk="none",
        description="TradeFlow 筛选、数据健康、证据缺口、候选分类。默认不调用 LLM。",
    ),
    RuntimeTier.LIGHT_RESEARCH: RuntimeTierSpec(
        tier=RuntimeTier.LIGHT_RESEARCH,
        label_cn="轻量研究",
        expected_latency="1-3min",
        llm_allowed=True,
        requires_confirmation=False,
        cost_risk="low",
        description="轻量 TA，只跑必要模块，用于中线政策验证、短线技术确认、持仓风险复核。",
    ),
    RuntimeTier.FULL_TA: RuntimeTierSpec(
        tier=RuntimeTier.FULL_TA,
        label_cn="完整 TA",
        expected_latency="10-20min",
        llm_allowed=True,
        requires_confirmation=True,
        cost_risk="high",
        description="完整 14 Agent TA，多空辩论和风控全链路，必须人工确认。",
    ),
}

_TRADEFLOW_FAST_ENDPOINTS: set[str] = {
    "tradeflow_daily_plan",
    "tradeflow_candidates",
    "tradeflow_candidate_detail",
    "tradeflow_observe",
    "tradeflow_ta_queue",
    "tradeflow_review",
    "tradeflow_data_health",
    "tradeflow_filtered",
    "tradeflow_discovery",
    "tradeflow_observe_run",
}


def get_tier_spec(tier: RuntimeTier) -> RuntimeTierSpec:
    return _TIER_SPECS[tier]


def tier_to_meta(tier: RuntimeTier) -> dict:
    spec = get_tier_spec(tier)
    return {
        "runtime_tier": spec.tier.value,
        "expected_latency": spec.expected_latency,
        "llm_allowed": spec.llm_allowed,
        "requires_confirmation": spec.requires_confirmation,
        "cost_risk": spec.cost_risk,
        "tier_label": spec.label_cn,
        "tier_description": spec.description,
    }


def tradeflow_endpoint_tier(endpoint_name: str) -> RuntimeTier:
    if endpoint_name in _TRADEFLOW_FAST_ENDPOINTS:
        return RuntimeTier.FAST_RADAR
    return RuntimeTier.LIGHT_RESEARCH


def tradeflow_meta(endpoint_name: str) -> dict:
    return tier_to_meta(tradeflow_endpoint_tier(endpoint_name))


def is_full_ta_allowed_without_confirmation(runtime_tier: Optional[str]) -> bool:
    if runtime_tier is None:
        return False
    try:
        tier = RuntimeTier(runtime_tier)
    except ValueError:
        return False
    if tier == RuntimeTier.FULL_TA:
        return False
    return True


def scheduler_default_tier() -> RuntimeTier:
    return RuntimeTier.LIGHT_RESEARCH


def all_tier_specs() -> List[RuntimeTierSpec]:
    return list(_TIER_SPECS.values())


def tier_labels_cn() -> Dict[str, str]:
    return {t.value: _TIER_SPECS[t].label_cn for t in RuntimeTier}
