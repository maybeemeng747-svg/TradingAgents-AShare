# [H-010] mandate_topic_lifecycle
"""Topic Lifecycle Registry — tracks policy topic lifecycle states for 昊天雷达.

Distinguishes between emerging, accelerating, confirming, crowded, fading
policy topics to help the Haotian Radar decide whether a topic is suitable
for left-side ambush, right-side confirmation, or observation only.

Components:
- TopicLifecycleState: enum for topic lifecycle stages.
- TopicLifecycleEntry: one topic entry in the registry.
- TopicLifecycleResult: per-candidate lifecycle evaluation output.
- TopicLifecycleRegistry: in-memory registry of topic states.
- evaluate_topic_lifecycle(): main evaluation function.

Constraints:
- Rule-based, no LLM calls.
- Single isolated event → UNKNOWN/EMERGING, never high confidence.
- Multi-day multi-source policy signals → ACCELERATING/CONFIRMING.
- Overheated + signals not continuing → CROWDED/FADING.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class TopicLifecycleState(str, Enum):
    EMERGING = "EMERGING"
    ACCELERATING = "ACCELERATING"
    CONFIRMING = "CONFIRMING"
    CROWDED = "CROWDED"
    FADING = "FADING"
    UNKNOWN = "UNKNOWN"


_LIFECYCLE_STATE_LABELS: dict[TopicLifecycleState, str] = {
    TopicLifecycleState.EMERGING: "新主题萌芽",
    TopicLifecycleState.ACCELERATING: "升温加速",
    TopicLifecycleState.CONFIRMING: "兑现确认",
    TopicLifecycleState.CROWDED: "拥挤过热",
    TopicLifecycleState.FADING: "退潮衰减",
    TopicLifecycleState.UNKNOWN: "未知",
}

_LEFT_SIDE_STATES = {TopicLifecycleState.EMERGING, TopicLifecycleState.ACCELERATING}
_OBSERVE_ONLY_STATES = {TopicLifecycleState.CROWDED, TopicLifecycleState.FADING}


@dataclass
class TopicLifecycleEntry:
    topic: str
    state: TopicLifecycleState = TopicLifecycleState.UNKNOWN
    signal_count: int = 0
    unique_dates: int = 0
    unique_sources: int = 0
    last_signal_date: str = ""
    has_policy_document: bool = False
    has_high_authority: bool = False
    is_noise: bool = False
    mandate_score: float = 0.0
    heat_delta: float = 0.0
    overheat_flags: list[str] = field(default_factory=list)
    update_count: int = 0

    def to_dict(self) -> dict:
        return {
            "topic": self.topic,
            "state": self.state.value,
            "signal_count": self.signal_count,
            "unique_dates": self.unique_dates,
            "unique_sources": self.unique_sources,
            "last_signal_date": self.last_signal_date,
            "has_policy_document": self.has_policy_document,
            "has_high_authority": self.has_high_authority,
            "is_noise": self.is_noise,
            "mandate_score": round(self.mandate_score, 2),
            "heat_delta": round(self.heat_delta, 2),
            "overheat_flags": self.overheat_flags,
            "update_count": self.update_count,
        }


@dataclass
class TopicLifecycleResult:
    topic_lifecycle_state: str = "UNKNOWN"
    topic_lifecycle_reason: str = ""
    topic_last_signal_date: str = ""
    topic_signal_count: int = 0
    is_left_side_suitable: bool = False
    is_observe_only: bool = False
    lifecycle_label: str = "未知"
    lifecycle_entry: Optional[TopicLifecycleEntry] = None

    def to_dict(self) -> dict:
        d = {
            "topic_lifecycle_state": self.topic_lifecycle_state,
            "topic_lifecycle_reason": self.topic_lifecycle_reason,
            "topic_last_signal_date": self.topic_last_signal_date,
            "topic_signal_count": self.topic_signal_count,
            "is_left_side_suitable": self.is_left_side_suitable,
            "is_observe_only": self.is_observe_only,
            "lifecycle_label": self.lifecycle_label,
        }
        if self.lifecycle_entry:
            d["lifecycle_entry"] = self.lifecycle_entry.to_dict()
        return d


class TopicLifecycleRegistry:
    def __init__(self) -> None:
        self._entries: dict[str, TopicLifecycleEntry] = {}

    def get(self, topic: str) -> Optional[TopicLifecycleEntry]:
        return self._entries.get(topic)

    def get_or_create(self, topic: str) -> TopicLifecycleEntry:
        if topic not in self._entries:
            self._entries[topic] = TopicLifecycleEntry(topic=topic)
        return self._entries[topic]

    def update_entry(
        self,
        topic: str,
        *,
        signal_count: int = 0,
        unique_dates: int = 0,
        unique_sources: int = 0,
        last_signal_date: str = "",
        has_policy_document: bool = False,
        has_high_authority: bool = False,
        is_noise: bool = False,
        mandate_score: float = 0.0,
        heat_delta: float = 0.0,
        overheat_flags: Optional[list[str]] = None,
    ) -> TopicLifecycleEntry:
        entry = self.get_or_create(topic)
        entry.signal_count = signal_count
        entry.unique_dates = unique_dates
        entry.unique_sources = unique_sources
        entry.last_signal_date = last_signal_date
        entry.has_policy_document = has_policy_document
        entry.has_high_authority = has_high_authority
        entry.is_noise = is_noise
        entry.mandate_score = mandate_score
        entry.heat_delta = heat_delta
        if overheat_flags is not None:
            entry.overheat_flags = overheat_flags
        entry.update_count += 1
        entry.state = _classify_lifecycle_state(entry)
        return entry

    def all_entries(self) -> dict[str, TopicLifecycleEntry]:
        return dict(self._entries)

    def to_dict(self) -> dict:
        return {topic: entry.to_dict() for topic, entry in self._entries.items()}

    def clear(self) -> None:
        self._entries.clear()


_DEFAULT_REGISTRY = TopicLifecycleRegistry()


def get_default_registry() -> TopicLifecycleRegistry:
    return _DEFAULT_REGISTRY


def _classify_lifecycle_state(entry: TopicLifecycleEntry) -> TopicLifecycleState:
    if not entry.topic or entry.signal_count == 0:
        return TopicLifecycleState.UNKNOWN

    sc = entry.signal_count
    ud = entry.unique_dates
    us = entry.unique_sources
    has_policy = entry.has_policy_document
    has_high = entry.has_high_authority
    noise = entry.is_noise
    ms = entry.mandate_score
    hd = entry.heat_delta
    of = entry.overheat_flags

    if noise and sc <= 1 and ud <= 1:
        return TopicLifecycleState.UNKNOWN

    crowded_flags = {"overheated_price_position", "crowded_consensus_risk"}
    has_crowded = bool(set(of) & crowded_flags) if of else False

    if has_crowded and hd <= 0 and not has_policy and not has_high:
        return TopicLifecycleState.FADING

    if has_crowded:
        return TopicLifecycleState.CROWDED

    if hd < 0 and not has_policy and ud <= 2:
        return TopicLifecycleState.FADING

    if sc <= 2 and ud <= 1 and not has_policy and not has_high:
        return TopicLifecycleState.EMERGING

    if ud >= 3 and us >= 2 and (has_policy or has_high) and ms >= 50:
        return TopicLifecycleState.CONFIRMING

    if ud >= 2 and (has_policy or has_high or ms >= 30):
        return TopicLifecycleState.ACCELERATING

    if sc >= 3 and us >= 2:
        return TopicLifecycleState.ACCELERATING

    if sc >= 2 and ud >= 2:
        return TopicLifecycleState.EMERGING

    return TopicLifecycleState.EMERGING


def evaluate_topic_lifecycle(
    *,
    topic: str = "",
    signal_count: int = 0,
    unique_dates: int = 0,
    unique_sources: int = 0,
    last_signal_date: str = "",
    has_policy_document: bool = False,
    has_high_authority: bool = False,
    is_noise: bool = False,
    mandate_score: float = 0.0,
    heat_delta: float = 0.0,
    overheat_flags: Optional[list[str]] = None,
    registry: Optional[TopicLifecycleRegistry] = None,
) -> TopicLifecycleResult:
    if not topic:
        return TopicLifecycleResult(
            topic_lifecycle_state=TopicLifecycleState.UNKNOWN.value,
            topic_lifecycle_reason="无政策主题",
            lifecycle_label=_LIFECYCLE_STATE_LABELS[TopicLifecycleState.UNKNOWN],
        )

    reg = registry if registry is not None else get_default_registry()

    entry = reg.update_entry(
        topic,
        signal_count=signal_count,
        unique_dates=unique_dates,
        unique_sources=unique_sources,
        last_signal_date=last_signal_date,
        has_policy_document=has_policy_document,
        has_high_authority=has_high_authority,
        is_noise=is_noise,
        mandate_score=mandate_score,
        heat_delta=heat_delta,
        overheat_flags=overheat_flags,
    )

    state = entry.state
    reason = _build_lifecycle_reason(entry, state)

    is_left = state in _LEFT_SIDE_STATES
    is_observe = state in _OBSERVE_ONLY_STATES

    return TopicLifecycleResult(
        topic_lifecycle_state=state.value,
        topic_lifecycle_reason=reason,
        topic_last_signal_date=entry.last_signal_date,
        topic_signal_count=entry.signal_count,
        is_left_side_suitable=is_left,
        is_observe_only=is_observe,
        lifecycle_label=_LIFECYCLE_STATE_LABELS.get(state, "未知"),
        lifecycle_entry=entry,
    )


def _build_lifecycle_reason(entry: TopicLifecycleEntry, state: TopicLifecycleState) -> str:
    parts: list[str] = []

    if state == TopicLifecycleState.UNKNOWN:
        if not entry.topic:
            return "无政策主题"
        if entry.is_noise and entry.signal_count <= 1:
            return "单一孤立事件，噪声判定"
        return "信号不足，无法判定生命周期"

    if state == TopicLifecycleState.EMERGING:
        parts.append("新主题萌芽")
        if entry.unique_dates <= 1:
            parts.append(f"仅{entry.unique_dates}天信号")
        if not entry.has_policy_document:
            parts.append("无政策原文")
        if not entry.has_high_authority:
            parts.append("无高权威来源")

    elif state == TopicLifecycleState.ACCELERATING:
        parts.append("升温加速")
        parts.append(f"{entry.unique_dates}天连续信号")
        if entry.has_policy_document:
            parts.append("有政策文件")
        if entry.has_high_authority:
            parts.append("有高权威来源")

    elif state == TopicLifecycleState.CONFIRMING:
        parts.append("政策兑现确认")
        parts.append(f"{entry.unique_dates}天多源信号")
        if entry.has_policy_document:
            parts.append("政策文件持续")
        if entry.mandate_score >= 50:
            parts.append(f"昊天分{entry.mandate_score:.0f}")

    elif state == TopicLifecycleState.CROWDED:
        parts.append("拥挤过热")
        crowded_flags = [f for f in (entry.overheat_flags or [])
                         if f in ("overheated_price_position", "crowded_consensus_risk")]
        if crowded_flags:
            parts.append(f"过热标记:{','.join(crowded_flags)}")

    elif state == TopicLifecycleState.FADING:
        parts.append("退潮衰减")
        if entry.heat_delta < 0:
            parts.append("热度下降")
        if not entry.has_policy_document:
            parts.append("无新政策文件")
        if entry.heat_delta <= 0 and not entry.has_high_authority:
            parts.append("信号不延续")

    return "; ".join(parts)


def apply_lifecycle_to_candidate(
    lifecycle_result: TopicLifecycleResult,
    candidate_tier: str,
) -> tuple[str, str]:
    tier = candidate_tier
    reason_suffix = ""

    if lifecycle_result.is_observe_only:
        if tier == "A":
            tier = "B"
            reason_suffix = "[H-010主题退潮/拥挤]降级到B层"
        elif tier == "B":
            reason_suffix = "[H-010主题退潮/拥挤]维持观察"

    return tier, reason_suffix
