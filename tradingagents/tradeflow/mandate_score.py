# [H-002] mandate_policy_continuity
"""Mandate Score — policy continuity and source authority scoring for 昊天雷达.

Implements the first layer of Mandate Score:
- Identifies whether a policy/industry topic is continuously heating up.
- Scores signals based on source authority hierarchy.
- Deduplicates same-source repetitions to prevent infinite stacking.
- Outputs explainable reasons and evidence refs.

Components:
- MandateScoreResult: output of mandate scoring.
- compute_mandate_score(): main scoring function.
- Topic vocabulary v0: keyword-to-topic mapping for known policy themes.

Constraints:
- Rule-based scoring, no LLM calls.
- Only consumes MandateSignal instances (no live web scraping).
- Missing evidence never yields high scores.
- Same-source duplicate titles do not infinitely stack scores.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from tradingagents.tradeflow.mandate_signal import (
    MandateSignal,
    SourceLevel,
    _SOURCE_LEVEL_WEIGHT,
)


_TOPIC_KEYWORDS_V0: dict[str, list[str]] = {
    "新质生产力": ["新质生产力"],
    "低空经济": ["低空经济", "低空飞行", "eVTOL", "空中出租"],
    "机器人": ["机器人", "人形机器人", "工业机器人", "服务机器人", "减速器", "伺服"],
    "算力": ["算力", "智算", "超算", "AI芯片", "GPU", "液冷", "IDC", "光模块"],
    "军工": ["军工", "国防", "武器", "导弹", "舰船", "航空发动机", "军用"],
    "半导体": ["半导体", "芯片", "集成电路", "晶圆", "光刻", "EDA", "封测"],
    "国产替代": ["国产替代", "自主可控", "国产化", "信创"],
    "并购重组": ["并购", "重组", "吸收合并", "重大资产重组", "收购"],
    "国企改革": ["国企改革", "混改", "国资", "央企改革", "中特估"],
    "出海": ["出海", "国际化", "海外布局", "一带一路"],
    "中特估": ["中特估", "中国特色估值", "央企估值"],
    "AI应用": ["AI应用", "大模型", "人工智能", "AIGC", "生成式AI", "ChatGPT"],
    "数据要素": ["数据要素", "数据资产", "数据交易", "数据确权"],
}

_TOPIC_KEYWORDS_FLAT: list[tuple[str, str]] = []
for _topic, _keywords in _TOPIC_KEYWORDS_V0.items():
    for _kw in _keywords:
        _TOPIC_KEYWORDS_FLAT.append((_topic, _kw))
_TOPIC_KEYWORDS_FLAT.sort(key=lambda x: -len(x[1]))


def match_topics(text: str) -> list[str]:
    if not text:
        return []
    topics: list[str] = []
    seen: set[str] = set()
    for topic, kw in _TOPIC_KEYWORDS_FLAT:
        if topic not in seen and kw in text:
            topics.append(topic)
            seen.add(topic)
    return topics


_SOURCE_AUTHORITY_WEIGHTS: dict[SourceLevel, float] = {
    SourceLevel.CENTRAL: 1.0,
    SourceLevel.STATE_COUNCIL: 0.95,
    SourceLevel.MINISTRY: 0.8,
    SourceLevel.EXCHANGE: 0.7,
    SourceLevel.SOE_GROUP: 0.65,
    SourceLevel.LOCAL_GOV: 0.5,
    SourceLevel.COMPANY_NOTICE: 0.3,
    SourceLevel.MEDIA: 0.15,
}

_MAX_AUTHORITY_SCORE = 100.0
_MAX_CONTINUITY_SCORE = 100.0
_MAX_MANDATE_SCORE = 100.0
_MAX_SAME_SOURCE_COUNT = 3
_SAME_SOURCE_DIMINISHING_FACTOR = 0.5
_SINGLE_DAY_PENALTY = 0.5
_MIN_EVIDENCE_FOR_HIGH_SCORE = 0.4


@dataclass
class MandateScoreResult:
    mandate_score: float = 0.0
    policy_continuity_score: float = 0.0
    source_authority_score: float = 0.0
    topic_heat_delta: float = 0.0
    mandate_reasons: list[str] = field(default_factory=list)
    mandate_evidence_refs: list[dict] = field(default_factory=list)
    topic: str = ""
    signal_count: int = 0
    unique_dates: int = 0
    unique_sources: int = 0
    unique_source_levels: int = 0
    has_policy_document: bool = False
    has_high_authority: bool = False
    is_noise: bool = False

    def to_dict(self) -> dict:
        return {
            "mandate_score": round(self.mandate_score, 2),
            "policy_continuity_score": round(self.policy_continuity_score, 2),
            "source_authority_score": round(self.source_authority_score, 2),
            "topic_heat_delta": round(self.topic_heat_delta, 2),
            "mandate_reasons": self.mandate_reasons,
            "mandate_evidence_refs": self.mandate_evidence_refs,
            "topic": self.topic,
            "signal_count": self.signal_count,
            "unique_dates": self.unique_dates,
            "unique_sources": self.unique_sources,
            "unique_source_levels": self.unique_source_levels,
            "has_policy_document": self.has_policy_document,
            "has_high_authority": self.has_high_authority,
            "is_noise": self.is_noise,
        }


def _collect_evidence_refs(signals: list[MandateSignal]) -> list[dict]:
    refs: list[dict] = []
    seen_titles: set[str] = set()
    for sig in signals:
        key = sig.title.strip()
        if key and key not in seen_titles:
            seen_titles.add(key)
            ref = {
                "title": sig.title,
                "source": sig.source,
                "source_level": sig.source_level,
                "date": sig.date,
                "confidence": round(sig.confidence, 3),
            }
            if sig.evidence_url:
                ref["evidence_url"] = sig.evidence_url
            if sig.raw_refs:
                ref["raw_refs"] = sig.raw_refs
            refs.append(ref)
    return refs


def _deduplicate_signals(signals: list[MandateSignal]) -> list[MandateSignal]:
    seen: set[tuple[str, str, str]] = set()
    unique: list[MandateSignal] = []
    for sig in signals:
        key = (sig.title.strip(), sig.source.strip(), sig.date.strip())
        if key not in seen:
            seen.add(key)
            unique.append(sig)
    return unique


def _compute_source_authority_score(signals: list[MandateSignal]) -> tuple[float, list[str]]:
    if not signals:
        return 0.0, []

    reasons: list[str] = []
    source_scores: dict[SourceLevel, int] = {}
    for sig in signals:
        sl = sig.source_level_enum
        if sl is not None:
            source_scores[sl] = source_scores.get(sl, 0) + 1

    if not source_scores:
        return 0.0, ["no_classified_source_level"]

    sorted_levels = sorted(source_scores.keys(), key=lambda x: -_SOURCE_AUTHORITY_WEIGHTS.get(x, 0.0))
    top_level = sorted_levels[0]
    top_weight = _SOURCE_AUTHORITY_WEIGHTS.get(top_level, 0.0)

    base_score = top_weight * 60

    diversity_bonus = 0.0
    for sl in sorted_levels[1:]:
        w = _SOURCE_AUTHORITY_WEIGHTS.get(sl, 0.0)
        diversity_bonus += w * 12
    diversity_bonus = min(diversity_bonus, 25)

    count_bonus = 0.0
    for sl, count in source_scores.items():
        if count > 1:
            w = _SOURCE_AUTHORITY_WEIGHTS.get(sl, 0.0)
            effective = 0.0
            for i in range(1, min(count, _MAX_SAME_SOURCE_COUNT)):
                effective += _SAME_SOURCE_DIMINISHING_FACTOR ** i
            count_bonus += w * effective * 8
    count_bonus = min(count_bonus, 15)

    raw_score = base_score + diversity_bonus + count_bonus

    has_high = any(sl for sl in source_scores if _SOURCE_AUTHORITY_WEIGHTS.get(sl, 0.0) >= 0.7)
    if has_high:
        reasons.append(f"high_authority_source:{top_level.value}")
    else:
        reasons.append(f"highest_source:{top_level.value}")

    levels_str = ", ".join(sl.value for sl in sorted_levels)
    reasons.append(f"source_levels:[{levels_str}]")

    return min(raw_score, _MAX_AUTHORITY_SCORE), reasons


def _compute_policy_continuity_score(
    signals: list[MandateSignal],
    topic: str,
) -> tuple[float, list[str]]:
    if not signals:
        return 0.0, []

    reasons: list[str] = []
    dates: set[str] = set()
    sources: set[str] = set()
    levels: set[str] = set()
    event_types: set[str] = set()

    for sig in signals:
        if sig.date:
            dates.add(sig.date)
        if sig.source:
            sources.add(sig.source)
        if sig.source_level:
            levels.add(sig.source_level)
        if sig.event_type:
            event_types.add(sig.event_type)

    n_dates = len(dates)
    n_sources = len(sources)
    n_levels = len(levels)

    date_score = min(n_dates * 25, 50)
    source_diversity_score = min(n_sources * 10, 25)
    level_diversity_score = min(n_levels * 8, 25)

    raw_continuity = date_score + source_diversity_score + level_diversity_score

    if n_dates == 1:
        raw_continuity *= _SINGLE_DAY_PENALTY
        reasons.append("single_day_isolated_signal")
    else:
        reasons.append(f"multi_day_continuity:{n_dates}_days")

    if n_sources > 1:
        reasons.append(f"multi_source:{n_sources}_sources")
    else:
        reasons.append("single_source")

    if n_levels > 1:
        reasons.append(f"multi_level:{n_levels}_levels")

    if "POLICY_DOCUMENT" in event_types or "MEETING_SIGNAL" in event_types:
        raw_continuity += 10
        reasons.append("has_policy_or_meeting_signal")

    return min(raw_continuity, _MAX_CONTINUITY_SCORE), reasons


def _compute_topic_heat_delta(
    current_signals: list[MandateSignal],
    historical_signals: Optional[list[MandateSignal]] = None,
) -> float:
    if not current_signals:
        return 0.0

    current_weight = sum(
        _SOURCE_AUTHORITY_WEIGHTS.get(sig.source_level_enum, 0.0) * sig.confidence
        for sig in current_signals
        if sig.source_level_enum is not None
    )

    if historical_signals is None or not historical_signals:
        return min(current_weight * 20, 100.0)

    historical_weight = sum(
        _SOURCE_AUTHORITY_WEIGHTS.get(sig.source_level_enum, 0.0) * sig.confidence
        for sig in historical_signals
        if sig.source_level_enum is not None
    )

    delta = current_weight - historical_weight
    return max(0.0, min(delta * 20, 100.0))


def compute_mandate_score(
    signals: list[MandateSignal],
    topic: str = "",
    historical_signals: Optional[list[MandateSignal]] = None,
) -> MandateScoreResult:
    if not signals:
        return MandateScoreResult(
            topic=topic,
            mandate_reasons=["no_signals"],
            is_noise=True,
        )

    deduped = _deduplicate_signals(signals)

    detected_topic = topic
    if not detected_topic:
        for sig in deduped:
            if sig.topic:
                detected_topic = sig.topic
                break
    if not detected_topic:
        all_text = " ".join(sig.title for sig in deduped if sig.title)
        topics = match_topics(all_text)
        if topics:
            detected_topic = topics[0]

    authority_score, auth_reasons = _compute_source_authority_score(deduped)
    continuity_score, cont_reasons = _compute_policy_continuity_score(deduped, detected_topic)
    heat_delta = _compute_topic_heat_delta(deduped, historical_signals)

    all_reasons = list(auth_reasons) + list(cont_reasons)

    dates: set[str] = set()
    sources: set[str] = set()
    levels: set[str] = set()
    has_policy_doc = False
    has_high_auth = False

    for sig in deduped:
        if sig.date:
            dates.add(sig.date)
        if sig.source:
            sources.add(sig.source)
        if sig.source_level:
            levels.add(sig.source_level)
        if sig.event_type in ("POLICY_DOCUMENT", "MEETING_SIGNAL", "INDUSTRY_PLAN"):
            has_policy_doc = True
        if sig.is_high_authority():
            has_high_auth = True

    avg_confidence = sum(sig.confidence for sig in deduped) / len(deduped)
    evidence_factor = min(avg_confidence * 1.5, 1.0)

    raw_mandate = (authority_score * 0.5 + continuity_score * 0.3 + heat_delta * 0.2) * evidence_factor

    is_noise = False
    if len(dates) <= 1 and len(sources) <= 1 and not has_high_auth:
        is_noise = True
        all_reasons.append("noise:single_day_single_source_no_high_authority")
    elif avg_confidence < _MIN_EVIDENCE_FOR_HIGH_SCORE:
        if raw_mandate > 50:
            raw_mandate = min(raw_mandate, 50.0)
            all_reasons.append("capped:low_avg_confidence")
    if not has_policy_doc and not has_high_auth and len(dates) <= 1:
        all_reasons.append("why_just_noise:no_policy_doc_no_high_auth_single_day")
    else:
        if has_policy_doc:
            all_reasons.append("has_policy_document_evidence")
        if has_high_auth:
            all_reasons.append("has_high_authority_source")
        if len(dates) > 1:
            all_reasons.append(f"continuous_across_{len(dates)}_days")

    evidence_refs = _collect_evidence_refs(deduped)

    return MandateScoreResult(
        mandate_score=round(min(raw_mandate, _MAX_MANDATE_SCORE), 2),
        policy_continuity_score=round(min(continuity_score, _MAX_CONTINUITY_SCORE), 2),
        source_authority_score=round(min(authority_score, _MAX_AUTHORITY_SCORE), 2),
        topic_heat_delta=round(heat_delta, 2),
        mandate_reasons=all_reasons,
        mandate_evidence_refs=evidence_refs,
        topic=detected_topic,
        signal_count=len(deduped),
        unique_dates=len(dates),
        unique_sources=len(sources),
        unique_source_levels=len(levels),
        has_policy_document=has_policy_doc,
        has_high_authority=has_high_auth,
        is_noise=is_noise,
    )


def compute_mandate_scores_by_topic(
    signals: list[MandateSignal],
    historical_signals: Optional[list[MandateSignal]] = None,
) -> dict[str, MandateScoreResult]:
    topic_signals: dict[str, list[MandateSignal]] = {}
    unassigned: list[MandateSignal] = []

    for sig in signals:
        assigned = False
        sig_topic = sig.topic
        if sig_topic:
            topic_signals.setdefault(sig_topic, []).append(sig)
            assigned = True
        else:
            text = f"{sig.title} {' '.join(sig.policy_tags)}"
            topics = match_topics(text)
            if topics:
                for t in topics:
                    topic_signals.setdefault(t, []).append(sig)
                assigned = True
        if not assigned:
            unassigned.append(sig)

    results: dict[str, MandateScoreResult] = {}
    for topic, sigs in topic_signals.items():
        results[topic] = compute_mandate_score(sigs, topic=topic, historical_signals=historical_signals)

    if unassigned:
        results["_unassigned"] = compute_mandate_score(
            unassigned, topic="", historical_signals=historical_signals
        )

    return results
