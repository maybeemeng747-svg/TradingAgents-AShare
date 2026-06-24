# [H-014] mandate_concentration_gate
"""Mandate Concentration Gate — compress the 昊天 (Haotian) left-side pool so
the radar stops spraying as many names as short-term T-trading.

The left-side ambush list should focus on a small number of *strong* policy
themes. Within each strong theme only the Top 1-3 names deserve a main slot;
weak/receding themes should not occupy any main slot but must keep an
explanation so the user understands "why only these few".

Responsibilities:
1. Resolve a canonical topic for every POLICY_AMBUSH / POLICY_CONFIRM
   candidate via :func:`topic_registry.match_topic`.
2. Aggregate per-topic concentration metrics from the candidate fields that
   are already present on the list view (``topic_lifecycle_state`` from H-010,
   ``topic_signal_count``, ``mandate_score_component``, ``blocking_evidence_gaps``
   from H-011, ``overheat_flags`` from H-009).
3. Classify each topic into ``strong`` / ``moderate`` / ``weak`` with an
   admission cap (strong→``concentration_per_topic_max``, moderate→
   ``concentration_moderate_topic_max``, weak→0).
4. Produce a ``concentration_summary`` that answers:
   - 现在昊天重心在哪个主题 (top theme)
   - 该主题先看哪几只 (top admitted symbols)
   - 证据缺口是什么 (aggregated blocking gaps)

Design constraints (from task H-014):
- No LLM calls. No strong buy/sell words.
- Never deletes original candidates — only changes main/observation layering.
- Candidates without a resolvable topic fall through to the existing pool
  caps; H-014 never blocks them.
- Deterministic: same inputs → same output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .strategy_config import StrategyConfig, DEFAULT_STRATEGY_CONFIG
from .topic_lifecycle import (
    TopicLifecycleState,
    _LEFT_SIDE_STATES,
    _OBSERVE_ONLY_STATES,
)
from .topic_registry import match_topic


_HAOTIAN_TYPES = {"POLICY_AMBUSH", "POLICY_CONFIRM"}

STRENGTH_STRONG = "strong"
STRENGTH_MODERATE = "moderate"
STRENGTH_WEAK = "weak"
STRENGTH_UNKNOWN = "unknown"


def _safe_float(val, default: float = 0.0) -> float:
    try:
        if val is None:
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


@dataclass
class TopicConcentrationMetrics:
    """Concentration metrics for a single resolved topic."""

    topic: str = ""
    candidate_count: int = 0
    lifecycle_state: str = TopicLifecycleState.UNKNOWN.value
    topic_status_label: str = "未知"
    max_signal_count: int = 0
    has_mandate_score: bool = False
    has_left_side_lifecycle: bool = False
    is_observe_only: bool = False
    evidence_gap_burden: int = 0
    overheat_flag_count: int = 0
    strength: str = STRENGTH_UNKNOWN
    strength_reason: str = ""
    admission_cap: int = 0
    symbols: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "topic": self.topic,
            "candidate_count": self.candidate_count,
            "lifecycle_state": self.lifecycle_state,
            "topic_status_label": self.topic_status_label,
            "max_signal_count": self.max_signal_count,
            "has_mandate_score": self.has_mandate_score,
            "has_left_side_lifecycle": self.has_left_side_lifecycle,
            "is_observe_only": self.is_observe_only,
            "evidence_gap_burden": self.evidence_gap_burden,
            "overheat_flag_count": self.overheat_flag_count,
            "strength": self.strength,
            "strength_reason": self.strength_reason,
            "admission_cap": self.admission_cap,
            "symbols": list(self.symbols),
        }


def resolve_topic_for_entry(entry: dict) -> str:
    """Resolve a canonical topic for a candidate dict.

    Uses :func:`topic_registry.match_topic` over ``mandate_topic``,
    ``policy_tags`` and ``name``. Returns ``""`` when no topic can be
    resolved — callers must skip H-014 for such entries.
    """
    return match_topic(
        mandate_topic=entry.get("mandate_topic", ""),
        policy_tags=entry.get("policy_tags"),
        name=entry.get("name", ""),
    )


def _topic_status_label(lifecycle_state: str) -> str:
    """Map a lifecycle state to a short Chinese label for display."""
    mapping = {
        TopicLifecycleState.EMERGING.value: "萌芽",
        TopicLifecycleState.ACCELERATING.value: "加速",
        TopicLifecycleState.CONFIRMING.value: "确认",
        TopicLifecycleState.CROWDED.value: "拥挤",
        TopicLifecycleState.FADING.value: "退潮",
        TopicLifecycleState.UNKNOWN.value: "未知",
    }
    return mapping.get(lifecycle_state, "未知")


def assess_topic_strength(
    topic: str,
    topic_entries: list[dict],
    cfg: Optional[StrategyConfig] = None,
) -> TopicConcentrationMetrics:
    """Classify the strength of one topic from its candidate group.

    Strength rules (evaluated in order):
    1. ``weak`` — observe-only lifecycle (CROWDED/FADING/RECEDING), admission
       cap 0. Such themes must not occupy main slots.
    2. ``strong`` — left-side / confirming lifecycle AND signal count ≥
       ``concentration_min_signals_for_strong`` AND at least one candidate
       carries a mandate score. Admission cap =
       ``concentration_per_topic_max``.
    3. ``moderate`` — everything else (e.g. left-side but no signal evidence,
       or unknown lifecycle). Admission cap =
       ``concentration_moderate_topic_max``.
    """
    if cfg is None:
        cfg = DEFAULT_STRATEGY_CONFIG

    metrics = TopicConcentrationMetrics(topic=topic)
    if not topic or not topic_entries:
        metrics.strength_reason = "无主题或无候选"
        return metrics

    metrics.candidate_count = len(topic_entries)
    metrics.symbols = [e.get("symbol", "") for e in topic_entries if e.get("symbol")]

    # Aggregate lifecycle / signal / mandate / gap signals across the group.
    signal_counts = [int(e.get("topic_signal_count", 0) or 0) for e in topic_entries]
    metrics.max_signal_count = max(signal_counts) if signal_counts else 0

    lifecycle_states = [e.get("topic_lifecycle_state", "") for e in topic_entries]
    # Pick the lifecycle state of the best-informed candidate (highest signal
    # count) so a stale snapshot on a weak sibling does not drag the topic
    # down. Ties break toward the first (highest mandate_score_component).
    ordered = sorted(
        topic_entries,
        key=lambda e: (
            int(e.get("topic_signal_count", 0) or 0),
            _safe_float(e.get("mandate_score_component")),
        ),
        reverse=True,
    )
    metrics.lifecycle_state = ordered[0].get("topic_lifecycle_state", "") or TopicLifecycleState.UNKNOWN.value
    metrics.topic_status_label = _topic_status_label(metrics.lifecycle_state)

    metrics.has_mandate_score = any(
        _safe_float(e.get("mandate_score_component")) > 0 for e in topic_entries
    )
    metrics.has_left_side_lifecycle = metrics.lifecycle_state in {
        s.value for s in _LEFT_SIDE_STATES
    }
    metrics.is_observe_only = metrics.lifecycle_state in {
        s.value for s in _OBSERVE_ONLY_STATES
    } or metrics.lifecycle_state in {"RECEDING"}

    metrics.evidence_gap_burden = sum(
        len(e.get("blocking_evidence_gaps") or []) for e in topic_entries
    )
    overheat_total = sum(
        len(e.get("overheat_flags") or []) for e in topic_entries
    )
    metrics.overheat_flag_count = overheat_total

    left_or_confirmed = metrics.has_left_side_lifecycle or metrics.lifecycle_state == TopicLifecycleState.CONFIRMING.value

    # Rule 1: observe-only lifecycle → weak, cap 0.
    if metrics.is_observe_only:
        metrics.strength = STRENGTH_WEAK
        metrics.admission_cap = 0
        metrics.strength_reason = (
            f"主题{metrics.topic_status_label}({metrics.lifecycle_state})，"
            f"不宜占据主候选"
        )
        return metrics

    # Rule 2: strong — left-side/confirming + enough signals + mandate score.
    if (
        left_or_confirmed
        and metrics.max_signal_count >= cfg.concentration_min_signals_for_strong
        and metrics.has_mandate_score
    ):
        metrics.strength = STRENGTH_STRONG
        metrics.admission_cap = cfg.concentration_per_topic_max
        metrics.strength_reason = (
            f"{metrics.topic_status_label}主题，{metrics.max_signal_count}条政策信号，"
            f"主候选上限{cfg.concentration_per_topic_max}只"
        )
        return metrics

    # Rule 3: moderate — keep a single representative only.
    metrics.strength = STRENGTH_MODERATE
    metrics.admission_cap = cfg.concentration_moderate_topic_max
    if not left_or_confirmed:
        metrics.strength_reason = (
            f"主题生命周期未确认({metrics.lifecycle_state})，仅保留Top"
            f"{cfg.concentration_moderate_topic_max}观察"
        )
    elif metrics.max_signal_count < cfg.concentration_min_signals_for_strong:
        metrics.strength_reason = (
            f"政策信号偏少({metrics.max_signal_count}<{cfg.concentration_min_signals_for_strong})，"
            f"仅保留Top{cfg.concentration_moderate_topic_max}观察"
        )
    else:
        metrics.strength_reason = (
            f"缺少昊天分支撑，仅保留Top{cfg.concentration_moderate_topic_max}观察"
        )
    return metrics


def compute_topic_metrics_map(
    entries: list[dict],
    cfg: Optional[StrategyConfig] = None,
) -> dict[str, TopicConcentrationMetrics]:
    """Build a ``{topic: TopicConcentrationMetrics}`` map for haotian entries.

    Only POLICY_AMBUSH / POLICY_CONFIRM entries are considered; entries
    without a resolvable topic are skipped (they fall through to the existing
    pool caps).
    """
    if cfg is None:
        cfg = DEFAULT_STRATEGY_CONFIG

    grouped: dict[str, list[dict]] = {}
    for entry in entries:
        if entry.get("candidate_type", "") not in _HAOTIAN_TYPES:
            continue
        topic = resolve_topic_for_entry(entry)
        if not topic:
            continue
        grouped.setdefault(topic, []).append(entry)

    metrics_map: dict[str, TopicConcentrationMetrics] = {}
    for topic, topic_entries in grouped.items():
        metrics_map[topic] = assess_topic_strength(topic, topic_entries, cfg)
    return metrics_map


def build_concentration_summary(
    topic_metrics: dict[str, TopicConcentrationMetrics],
    admitted_by_topic: dict[str, list[str]],
    cfg: Optional[StrategyConfig] = None,
) -> dict:
    """Build the ``concentration_summary`` payload for API / frontend.

    Answers the three H-014 questions:
    - top theme + its status
    - top admitted symbols per theme
    - aggregated blocking evidence gaps
    """
    if cfg is None:
        cfg = DEFAULT_STRATEGY_CONFIG

    if not topic_metrics:
        return {
            "enabled": bool(cfg.concentration_enabled),
            "top_theme": "",
            "top_theme_status": "",
            "top_theme_main_symbols": [],
            "per_topic": {},
            "weak_topics_downgraded": [],
            "per_topic_cap_downgraded": [],
            "headline": "",
        }

    # Strong topics first (by signal count then candidate count), then moderate.
    strength_rank = {STRENGTH_STRONG: 0, STRENGTH_MODERATE: 1, STRENGTH_WEAK: 2, STRENGTH_UNKNOWN: 3}
    ordered_topics = sorted(
        topic_metrics.values(),
        key=lambda m: (
            strength_rank.get(m.strength, 9),
            -m.max_signal_count,
            -m.candidate_count,
            m.topic,
        ),
    )

    per_topic_payload: dict[str, dict] = {}
    weak_topics: list[str] = []
    top_theme = ""
    top_theme_status = ""
    top_theme_symbols: list[str] = []

    for m in ordered_topics:
        admitted = admitted_by_topic.get(m.topic, [])
        per_topic_payload[m.topic] = {
            "strength": m.strength,
            "strength_reason": m.strength_reason,
            "admission_cap": m.admission_cap,
            "candidate_count": m.candidate_count,
            "admitted_count": len(admitted),
            "admitted_symbols": admitted,
            "lifecycle_state": m.lifecycle_state,
            "topic_status_label": m.topic_status_label,
            "max_signal_count": m.max_signal_count,
            "evidence_gap_burden": m.evidence_gap_burden,
            "symbols": m.symbols,
        }
        if m.strength == STRENGTH_WEAK:
            weak_topics.append(m.topic)
        if not top_theme and m.strength in {STRENGTH_STRONG, STRENGTH_MODERATE} and admitted:
            top_theme = m.topic
            top_theme_status = m.topic_status_label
            top_theme_symbols = admitted

    # Collect aggregated blocking gaps across admitted candidates' topics so
    # the UI can show "证据缺口是什么". We reuse the metrics' gap burden summary.
    headline_parts: list[str] = []
    if top_theme:
        headline_parts.append(f"昊天重心：{top_theme}（{top_theme_status}）")
        if top_theme_symbols:
            headline_parts.append("先看：" + "、".join(top_theme_symbols[:3]))
    if weak_topics:
        headline_parts.append(f"弱主题降级：{('、'.join(weak_topics))}")
    headline = "｜".join(headline_parts)

    return {
        "enabled": bool(cfg.concentration_enabled),
        "top_theme": top_theme,
        "top_theme_status": top_theme_status,
        "top_theme_main_symbols": top_theme_symbols,
        "per_topic": per_topic_payload,
        "weak_topics_downgraded": weak_topics,
        "per_topic_cap_downgraded": [],  # populated by run_pool_gate caller
        "headline": headline,
        "per_topic_max": cfg.concentration_per_topic_max,
        "moderate_topic_max": cfg.concentration_moderate_topic_max,
        "min_signals_for_strong": cfg.concentration_min_signals_for_strong,
    }


def downgrade_reason_for_weak(metrics: TopicConcentrationMetrics) -> str:
    """Human-readable reason when a candidate is downgraded by a weak topic."""
    return f"[H-014主题集中度]{metrics.strength_reason}"


def downgrade_reason_for_cap(
    metrics: TopicConcentrationMetrics,
    rank_in_topic: int,
) -> str:
    """Human-readable reason when a candidate exceeds the per-topic cap."""
    return (
        f"[H-014主题集中度]{metrics.topic}已达Top{metrics.admission_cap}"
        f"（{metrics.strength_reason}），本只序号{rank_in_topic}降为观察"
    )
