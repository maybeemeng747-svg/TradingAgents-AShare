# [TF-P0-002] tradeflow_pool_split
"""Candidate Pool Mapping — maps pool names to candidate_type lists.

Pools:
- all: no filter (all types)
- haotian: 昊天左侧 — POLICY_AMBUSH
- policy: 政策确认 — POLICY_CONFIRM
- tech: 短线技术 — TECH_TRADE
- event: 事件观察 — EVENT_WATCH
- gap: 证据缺口 — UNCLASSIFIED_DATA_GAP
"""

from __future__ import annotations

POOL_TO_CANDIDATE_TYPES: dict[str, list[str]] = {
    "all": [],
    "haotian": ["POLICY_AMBUSH"],
    "policy": ["POLICY_CONFIRM"],
    "tech": ["TECH_TRADE"],
    "event": ["EVENT_WATCH"],
    "gap": ["UNCLASSIFIED_DATA_GAP"],
}

POOL_LABELS: dict[str, str] = {
    "all": "全部",
    "haotian": "昊天左侧",
    "policy": "政策确认",
    "tech": "短线技术",
    "event": "事件观察",
    "gap": "证据缺口",
}

ALL_POOLS = list(POOL_TO_CANDIDATE_TYPES.keys())


def pool_to_candidate_types(pool: str) -> list[str]:
    if pool in POOL_TO_CANDIDATE_TYPES:
        return POOL_TO_CANDIDATE_TYPES[pool]
    return []


def candidate_type_to_pool(candidate_type: str) -> str:
    for pool, types in POOL_TO_CANDIDATE_TYPES.items():
        if candidate_type in types:
            return pool
    if candidate_type in ("PSEUDO_POLICY", "OVERHEATED_AVOID"):
        return "all"
    return "all"
