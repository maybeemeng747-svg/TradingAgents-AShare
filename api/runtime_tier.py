# [PERF-001] runtime_tier_contract
# [PERF-004] full_ta_cost_gate
"""Runtime tier contract for TradeFlow, light TA, and full TA.

Defines three tiers with latency budgets, LLM access, and confirmation gates.
All API endpoints and UI actions must declare their tier.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


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
    "tradeflow_company_overview",  # [TF-UI-011] candidate_research_entry
    "tradeflow_observe",
    "tradeflow_ta_queue",
    "tradeflow_review",
    "tradeflow_data_health",
    "tradeflow_filtered",
    "tradeflow_discovery",
    "tradeflow_observe_run",
    "tradeflow_observe_scheduler",
    "tradeflow_paper_ledger",  # [TF-PAPER-001] paper_trading_ledger
    "tradeflow_paper_review",  # [TF-PAPER-001] paper_trading_ledger
    "tradeflow_source_freshness",  # [DATA-018] source_freshness_report
    "tradeflow_live_sampling",  # [DATA-020] live_sampling_health_ui
    "tradeflow_topic_registry",  # [H-012] mandate_topic_registry
    "tradeflow_topic_watchlist",  # [H-012] mandate_topic_registry
    "tradeflow_topic_heatmap",  # [H-013] mandate_topic_heatmap
    "tradeflow_observation_items",  # [TRACK-001] observation_warehouse
    "investment_controller_context",  # [IC-TA-001] investment_controller_context
    "notification_draft_dry_run",  # [TRACK-NOTIFY-001] notification_payload_dry_run
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


# [PERF-004] full_ta_cost_gate

@dataclass
class FullTACostPreview:
    runtime_tier: str = "FULL_TA"
    tier_label: str = "完整 TA"
    expected_latency: str = "10-20min"
    llm_allowed: bool = True
    cost_risk: str = "high"
    requires_confirmation: bool = True
    llm_provider: str = ""
    llm_model: str = ""
    base_url_display: str = ""
    enabled_modules: List[str] = field(default_factory=list)
    estimated_llm_calls: int = 0
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "runtime_tier": self.runtime_tier,
            "tier_label": self.tier_label,
            "expected_latency": self.expected_latency,
            "llm_allowed": self.llm_allowed,
            "cost_risk": self.cost_risk,
            "requires_confirmation": self.requires_confirmation,
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "base_url_display": self.base_url_display,
            "enabled_modules": self.enabled_modules,
            "estimated_llm_calls": self.estimated_llm_calls,
            "description": self.description,
        }


_FULL_TA_MODULE_COUNT = 14
_FULL_TA_LLM_CALL_ESTIMATE = 20


def get_full_ta_cost_preview(
    llm_provider: str = "",
    llm_model: str = "",
    base_url_display: str = "",
) -> FullTACostPreview:
    spec = get_tier_spec(RuntimeTier.FULL_TA)
    return FullTACostPreview(
        runtime_tier=spec.tier.value,
        tier_label=spec.label_cn,
        expected_latency=spec.expected_latency,
        llm_allowed=spec.llm_allowed,
        cost_risk=spec.cost_risk,
        requires_confirmation=spec.requires_confirmation,
        llm_provider=llm_provider,
        llm_model=llm_model,
        base_url_display=base_url_display,
        enabled_modules=[
            "市场分析", "舆情分析", "新闻分析", "基本面分析", "宏观分析",
            "主力资金分析", "量价分析", "多头研究", "空头研究", "研究总监",
            "交易员", "激进风控", "中性风控", "稳健风控",
        ],
        estimated_llm_calls=_FULL_TA_LLM_CALL_ESTIMATE,
        description=spec.description,
    )


@dataclass
class ScheduledCostMeta:
    is_full_ta: bool = True
    runtime_tier: str = "FULL_TA"
    tier_label: str = "完整 TA"
    created_by: str = ""
    trigger_frequency: str = "daily"
    last_run_llm_summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_full_ta": self.is_full_ta,
            "runtime_tier": self.runtime_tier,
            "tier_label": self.tier_label,
            "created_by": self.created_by,
            "trigger_frequency": self.trigger_frequency,
            "last_run_llm_summary": self.last_run_llm_summary,
        }


def build_scheduled_cost_meta(
    trigger_time: str = "20:00",
    last_run_status: Optional[str] = None,
) -> ScheduledCostMeta:
    return ScheduledCostMeta(
        is_full_ta=True,
        runtime_tier=RuntimeTier.FULL_TA.value,
        tier_label=_TIER_SPECS[RuntimeTier.FULL_TA].label_cn,
        created_by="user",
        trigger_frequency=f"每日 {trigger_time}",
        last_run_llm_summary=_summarize_last_run(last_run_status),
    )


def _summarize_last_run(last_run_status: Optional[str]) -> str:
    if not last_run_status:
        return "尚未运行"
    status_map = {
        "success": "上次运行成功（已调用 LLM）",
        "failed": "上次运行失败",
        "running": "正在运行中",
        "stale": "上次运行状态不明",
    }
    return status_map.get(last_run_status, f"上次状态: {last_run_status}")
