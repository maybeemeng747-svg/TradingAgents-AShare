# [DATA-003] mandate_event_normalization
"""Unified event-to-MandateSignal normalizer for 昊天雷达.

Bridges multiple raw event sources (event_source.EventItem, CNInfo announcements,
research reports, policy news) into a single deduplicated MandateSignal stream.

Key behaviours:
1. Multi-source ingestion: EventItem objects, raw dicts (cninfo/cn_astock), and
   plain text events all flow through one normalisation path.
2. Source-level classification: broker research → MEDIA (never CENTRAL);
   company announcements → COMPANY_NOTICE (cannot auto-promote to policy).
3. Title deduplication: same title across multiple sources keeps the highest-
   authority source and merges raw_refs from all duplicates.
4. Topic tagging: reuses H-002 topic keyword vocabulary for policy_tags.
5. Direction inference: combines event_type and source_level heuristics.
6. No LLM calls, no TA triggers, no buy/sell output.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional, Union

from tradingagents.tradeflow.mandate_signal import (
    MandateEventType,
    MandateSignal,
    SourceLevel,
    _SOURCE_LEVEL_WEIGHT,
    classify_event_type,
    classify_source_level,
    compute_confidence,
)

logger = logging.getLogger(__name__)

_RESEARCH_ORG_KEYWORDS = re.compile(
    r"证券|券商|研究所|研报|分析师|中金|中信|华泰|国泰君安|招商证券|"
    r"海通证券|广发证券|申万宏源|东方证券|兴业证券|平安证券|"
    r"长江证券|国信证券|天风证券|东吴证券|华西证券|"
    r"安信证券|方正证券|光大证券|民生证券|华创证券"
)

_MEDIA_SOURCE_KEYWORDS = re.compile(
    r"财联社|cls\.cn|证券时报|上海证券报|证券日报|第一财经|"
    r"新浪财经|同花顺|东方财富|界面新闻|澎湃新闻|网易财经|"
    r"腾讯财经|搜狐财经|和讯|金融界"
)

_COMPANY_NOTICE_KEYWORDS = re.compile(
    r"巨潮资讯|cninfo|公告|公司公告|董事会决议|股东大会|"
    r"监事会|招股说明书|年报|半年报|季报|减持|增持|回购"
)

_POLICY_SOURCE_KEYWORDS = re.compile(
    r"国务院|中共中央|党中央|国办|国务院办公厅|"
    r"发改委|工信部|证监会|商务部|科技部|财政部|"
    r"人民银行|央行|交通运输部|住建部|"
    r"省政府|市政府|办公厅|"
    r"沪深交易所|上交所|深交所|北交所|国资委"
)

_BEARISH_EVENT_KEYWORDS = re.compile(
    r"风险提示|减持|问询|监管函|处罚|立案|退市|"
    r"亏损|下滑|下降|下调|卖出评级|减持评级"
)

_BULLISH_EVENT_KEYWORDS = re.compile(
    r"回购|增持|买入评级|推荐|中标|获批|批复|"
    r"重大合同|战略合作|业绩预增|扭亏|盈利"
)


@dataclass
class NormalizedEventBatch:
    signals: list[MandateSignal] = field(default_factory=list)
    total_raw: int = 0
    duplicates_removed: int = 0
    by_symbol: dict[str, list[MandateSignal]] = field(default_factory=dict)
    by_source_level: dict[str, int] = field(default_factory=dict)
    by_event_type: dict[str, int] = field(default_factory=dict)
    dedup_groups: int = 0

    def to_dict(self) -> dict:
        return {
            "total_raw": self.total_raw,
            "total_normalized": len(self.signals),
            "duplicates_removed": self.duplicates_removed,
            "dedup_groups": self.dedup_groups,
            "symbols_count": len(self.by_symbol),
            "by_source_level": dict(self.by_source_level),
            "by_event_type": dict(self.by_event_type),
        }


def _title_dedup_key(title: str) -> str:
    normalized = re.sub(r"\s+", "", title.strip())
    if len(normalized) > 60:
        normalized = normalized[:60]
    return normalized.lower()


def _classify_source_level_enhanced(
    title: str,
    source: str = "",
    event_type_hint: str = "",
) -> SourceLevel:
    if _RESEARCH_ORG_KEYWORDS.search(f"{title} {source}"):
        return SourceLevel.MEDIA
    if event_type_hint == "rating":
        return SourceLevel.MEDIA
    if event_type_hint == "buyback":
        return SourceLevel.COMPANY_NOTICE
    if event_type_hint == "notice":
        if _POLICY_SOURCE_KEYWORDS.search(f"{title} {source}"):
            return classify_source_level(title, source)
        return SourceLevel.COMPANY_NOTICE
    return classify_source_level(title, source)


def _infer_direction(title: str, source: str = "", event_type_hint: str = "") -> str:
    if event_type_hint == "buyback":
        return "bullish"
    if event_type_hint == "rating":
        bullish = {"买入", "增持", "推荐", "强烈推荐", "优于大势"}
        bearish = {"卖出", "减持", "回避"}
        for kw in bullish:
            if kw in f"{title} {source}":
                return "bullish"
        for kw in bearish:
            if kw in f"{title} {source}":
                return "bearish"
        return "neutral"
    if event_type_hint == "notice":
        if _BEARISH_EVENT_KEYWORDS.search(title):
            return "bearish"
        if _BULLISH_EVENT_KEYWORDS.search(title):
            return "bullish"
        return "neutral"
    if _BEARISH_EVENT_KEYWORDS.search(title):
        return "bearish"
    if _BULLISH_EVENT_KEYWORDS.search(title):
        return "bullish"
    return "neutral"


def _topic_tags_from_title(title: str) -> list[str]:
    from tradingagents.tradeflow.mandate_score import match_topics
    return match_topics(title)


def _raw_event_to_mandate_signal(
    raw: Union[dict, Any],
    default_symbol: str = "",
) -> Optional[MandateSignal]:
    if isinstance(raw, dict):
        title = str(raw.get("title", "") or "").strip()
        if not title or title == "nan":
            return None
        symbol = str(raw.get("symbol", "") or default_symbol).strip()
        source = str(raw.get("source", "") or "").strip()
        date = str(raw.get("date", "") or "").strip()
        url = str(raw.get("url", "") or raw.get("evidence_url", "") or "").strip()
        event_type_hint = str(raw.get("event_type", "") or "").strip()
        evidence_text = str(raw.get("evidence_text", "") or title).strip()
        detail = raw.get("detail", {}) or {}
        if not url and isinstance(detail, dict):
            url = str(detail.get("url", "") or detail.get("evidence_url", "") or "").strip()

        source_level = _classify_source_level_enhanced(title, source, event_type_hint)
        event_type = classify_event_type(title)
        if event_type_hint == "rating" and source_level == SourceLevel.MEDIA:
            event_type = MandateEventType.BUYBACK_RATING
        if event_type_hint == "buyback":
            event_type = MandateEventType.BUYBACK_RATING
        direction = raw.get("direction", "") or _infer_direction(title, source, event_type_hint)
        policy_tags = _topic_tags_from_title(title)

        confidence = compute_confidence(
            source_level=source_level,
            has_title=bool(title),
            has_date=bool(date),
            has_source=bool(source),
            has_evidence_text=bool(evidence_text),
        )

        raw_ref = {
            "original_title": title,
            "original_source": source,
            "original_date": date,
            "original_event_type": event_type_hint,
            "url": url,
        }
        if isinstance(detail, dict) and detail:
            raw_ref["detail_keys"] = sorted(detail.keys())

        return MandateSignal(
            symbol=symbol,
            topic=policy_tags[0] if policy_tags else "",
            title=title,
            source=source,
            source_level=source_level.value,
            date=date,
            evidence_text=evidence_text,
            evidence_url=url,
            event_type=event_type.value,
            direction=direction,
            confidence=confidence,
            policy_tags=policy_tags,
            industry_tags=[],
            company_role="",
            raw_refs=[raw_ref],
        )
    title = getattr(raw, "title", "") or ""
    if not title or title == "nan":
        return None
    symbol = getattr(raw, "symbol", "") or default_symbol
    source = getattr(raw, "source", "") or ""
    date = getattr(raw, "date", "") or ""
    event_type_hint = getattr(raw, "event_type", "") or ""
    detail = getattr(raw, "detail", {}) or {}
    url = ""
    if isinstance(detail, dict):
        url = detail.get("url", "") or detail.get("evidence_url", "") or ""
    evidence_text = getattr(raw, "evidence_text", "") or title

    source_level = _classify_source_level_enhanced(title, source, event_type_hint)
    event_type = classify_event_type(title)
    if event_type_hint == "rating" and source_level == SourceLevel.MEDIA:
        event_type = MandateEventType.BUYBACK_RATING
    if event_type_hint == "buyback":
        event_type = MandateEventType.BUYBACK_RATING
    item_direction = getattr(raw, "direction", "") or ""
    direction = item_direction or _infer_direction(title, source, event_type_hint)
    policy_tags = _topic_tags_from_title(title)

    confidence = compute_confidence(
        source_level=source_level,
        has_title=bool(title),
        has_date=bool(date),
        has_source=bool(source),
        has_evidence_text=bool(evidence_text),
    )

    raw_ref = {
        "original_title": title,
        "original_source": source,
        "original_date": date,
        "original_event_type": event_type_hint,
        "url": url,
    }
    if isinstance(detail, dict) and detail:
        raw_ref["detail_keys"] = sorted(detail.keys())

    return MandateSignal(
        symbol=symbol,
        topic=policy_tags[0] if policy_tags else "",
        title=title,
        source=source,
        source_level=source_level.value,
        date=date,
        evidence_text=evidence_text,
        evidence_url=url,
        event_type=event_type.value,
        direction=direction,
        confidence=confidence,
        policy_tags=policy_tags,
        industry_tags=[],
        company_role="",
        raw_refs=[raw_ref],
    )


def _source_level_sort_key(sl_value: str) -> float:
    try:
        return _SOURCE_LEVEL_WEIGHT[SourceLevel(sl_value)]
    except (ValueError, KeyError):
        return 0.0


def _merge_duplicate_signals(
    signals: list[MandateSignal],
) -> list[MandateSignal]:
    groups: dict[str, list[MandateSignal]] = {}
    for sig in signals:
        key = (sig.symbol, _title_dedup_key(sig.title))
        groups.setdefault(key, []).append(sig)

    merged: list[MandateSignal] = []
    for key, group in groups.items():
        if len(group) == 1:
            merged.append(group[0])
            continue

        group.sort(key=lambda s: _source_level_sort_key(s.source_level), reverse=True)
        best = group[0]

        all_refs: list[dict] = list(best.raw_refs)
        seen_ref_keys: set[str] = set()
        for ref in all_refs:
            rk = (ref.get("original_source", ""), ref.get("original_date", ""))
            seen_ref_keys.add(rk)

        for dup in group[1:]:
            for ref in dup.raw_refs:
                rk = (ref.get("original_source", ""), ref.get("original_date", ""))
                if rk not in seen_ref_keys:
                    all_refs.append(ref)
                    seen_ref_keys.add(rk)

        all_tags: list[str] = list(best.policy_tags)
        tag_set = set(all_tags)
        for dup in group[1:]:
            for tag in dup.policy_tags:
                if tag not in tag_set:
                    all_tags.append(tag)
                    tag_set.add(tag)

        if best.confidence < 1.0:
            max_conf = max(s.confidence for s in group)
            best.confidence = max(best.confidence, max_conf * 0.85)

        merged_signal = MandateSignal(
            symbol=best.symbol,
            topic=best.topic,
            title=best.title,
            source=best.source,
            source_level=best.source_level,
            date=best.date,
            evidence_text=best.evidence_text,
            evidence_url=best.evidence_url,
            event_type=best.event_type,
            direction=best.direction,
            confidence=best.confidence,
            policy_tags=all_tags,
            industry_tags=best.industry_tags,
            company_role=best.company_role,
            raw_refs=all_refs,
        )
        merged.append(merged_signal)

    return merged


def normalize_events(
    raw_events: list[Union[dict, Any]],
    default_symbol: str = "",
) -> NormalizedEventBatch:
    total_raw = len(raw_events)
    signals: list[MandateSignal] = []
    for raw in raw_events:
        sig = _raw_event_to_mandate_signal(raw, default_symbol)
        if sig is not None:
            signals.append(sig)

    pre_dedup = len(signals)
    signals = _merge_duplicate_signals(signals)
    duplicates_removed = pre_dedup - len(signals)

    dedup_groups = pre_dedup - len(signals)

    by_symbol: dict[str, list[MandateSignal]] = {}
    for sig in signals:
        by_symbol.setdefault(sig.symbol, []).append(sig)

    by_source_level: dict[str, int] = {}
    for sig in signals:
        by_source_level[sig.source_level] = by_source_level.get(sig.source_level, 0) + 1

    by_event_type: dict[str, int] = {}
    for sig in signals:
        by_event_type[sig.event_type] = by_event_type.get(sig.event_type, 0) + 1

    return NormalizedEventBatch(
        signals=signals,
        total_raw=total_raw,
        duplicates_removed=duplicates_removed,
        by_symbol=by_symbol,
        by_source_level=by_source_level,
        by_event_type=by_event_type,
        dedup_groups=dedup_groups,
    )


def normalize_event_source_result(
    event_source_result: Any,
) -> NormalizedEventBatch:
    items_by_symbol = getattr(event_source_result, "items_by_symbol", {})
    if not items_by_symbol:
        return NormalizedEventBatch()

    all_raw: list[Any] = []
    for symbol, items in items_by_symbol.items():
        for item in items:
            all_raw.append(item)

    batch = normalize_events(all_raw)
    return batch


def normalize_event_items_by_symbol(
    items_by_symbol: dict[str, list],
) -> NormalizedEventBatch:
    if not items_by_symbol:
        return NormalizedEventBatch()

    all_raw: list[Any] = []
    for symbol, items in items_by_symbol.items():
        for item in items:
            if not getattr(item, "symbol", ""):
                if hasattr(item, "__dict__"):
                    pass
                else:
                    continue
            all_raw.append(item)

    return normalize_events(all_raw)


def normalize_raw_dicts(
    events: list[dict],
    default_symbol: str = "",
) -> NormalizedEventBatch:
    return normalize_events(events, default_symbol)


def is_research_report(title: str, source: str = "") -> bool:
    return bool(_RESEARCH_ORG_KEYWORDS.search(f"{title} {source}"))


def is_company_announcement(title: str, source: str = "", event_type: str = "") -> bool:
    if event_type == "notice":
        return not bool(_POLICY_SOURCE_KEYWORDS.search(f"{title} {source}"))
    return bool(_COMPANY_NOTICE_KEYWORDS.search(f"{title} {source}")) and not bool(
        _POLICY_SOURCE_KEYWORDS.search(f"{title} {source}")
    )


def is_policy_document(title: str, source: str = "") -> bool:
    return bool(_POLICY_SOURCE_KEYWORDS.search(f"{title} {source}"))
