# [H-001] mandate_signal_model
"""MandateSignal — policy intent recognition data model for 昊天雷达 v0.

Defines the base data structure for classifying policy / event / industry /
company evidence into scorable signals that feed into the 昊天意志评分
(mandate score) pipeline.

Components:
- SourceLevel enum: authority hierarchy for signal provenance.
- MandateEventType enum: what kind of mandate / event this signal represents.
- MandateSignal dataclass: one normalised signal ready for scoring.
- Converter: event_source.EventItem → MandateSignal (no data loss).
- Validation: missing source / date / title → low confidence, never high.

Constraints:
- No LLM calls.
- No TA triggers.
- No buy/sell suggestions.
- No changes to tradingagents/prompts/.
- Symbol isolation: signals are per-symbol, no cross-contamination.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class SourceLevel(Enum):
    CENTRAL = "CENTRAL"
    STATE_COUNCIL = "STATE_COUNCIL"
    MINISTRY = "MINISTRY"
    LOCAL_GOV = "LOCAL_GOV"
    EXCHANGE = "EXCHANGE"
    SOE_GROUP = "SOE_GROUP"
    COMPANY_NOTICE = "COMPANY_NOTICE"
    MEDIA = "MEDIA"


class MandateEventType(Enum):
    POLICY_DOCUMENT = "POLICY_DOCUMENT"
    MEETING_SIGNAL = "MEETING_SIGNAL"
    INDUSTRY_PLAN = "INDUSTRY_PLAN"
    SUBSIDY_SUPPORT = "SUBSIDY_SUPPORT"
    PROCUREMENT_ORDER = "PROCUREMENT_ORDER"
    LICENSE_APPROVAL = "LICENSE_APPROVAL"
    M_AND_A_RESTRUCTURING = "M_AND_A_RESTRUCTURING"
    SOE_REFORM = "SOE_REFORM"
    BUYBACK_RATING = "BUYBACK_RATING"


_SOURCE_LEVEL_WEIGHT: dict[SourceLevel, float] = {
    SourceLevel.CENTRAL: 1.0,
    SourceLevel.STATE_COUNCIL: 0.95,
    SourceLevel.MINISTRY: 0.8,
    SourceLevel.EXCHANGE: 0.7,
    SourceLevel.SOE_GROUP: 0.65,
    SourceLevel.LOCAL_GOV: 0.5,
    SourceLevel.COMPANY_NOTICE: 0.3,
    SourceLevel.MEDIA: 0.15,
}

_MAX_CONFIDENCE_LOW_EVIDENCE = 0.3

_SOURCE_KEYWORD_PATTERNS: list[tuple[re.Pattern, SourceLevel]] = [
    (re.compile(r"中共中央|党中央|总书记"), SourceLevel.CENTRAL),
    (re.compile(r"国务院|国办|国务院办公厅"), SourceLevel.STATE_COUNCIL),
    (re.compile(
        r"发改委|工信部|证监会|商务部|科技部|财政部|农业农村部|"
        r"交通运输部|住建部|教育部|卫健委|自然资源部|生态环境部|"
        r"人民银行|央行|银保监|金管局|国家能源局|国家数据局"
    ), SourceLevel.MINISTRY),
    (re.compile(r"沪深交易所|上交所|深交所|北交所|港交所"), SourceLevel.EXCHANGE),
    (re.compile(r"国资委|央企|集团"), SourceLevel.SOE_GROUP),
    (re.compile(
        r"省|市|区|县|地方|开发区|高新区|管委会|"
        r"省政府|市政府|区政府|县政府"
    ), SourceLevel.LOCAL_GOV),
    (re.compile(r"巨潮资讯|cninfo|公告|公司公告|董事会|股东大会|监事会"), SourceLevel.COMPANY_NOTICE),
    (re.compile(r"东财|同花顺|新浪财经|财联社|证券时报|证券日报|上海证券报|第一财经|界面新闻|澎湃"), SourceLevel.MEDIA),
]

_EVENT_TYPE_KEYWORDS: list[tuple[re.Pattern, MandateEventType]] = [
    (re.compile(r"规划|行动计划|实施方案|指导意见|纲要|意见$|通知$|若干政策"), MandateEventType.POLICY_DOCUMENT),
    (re.compile(r"会议|座谈会|常务会议|工作会议|专题会|推进会"), MandateEventType.MEETING_SIGNAL),
    (re.compile(r"产业政策|产业规划|产业集群|产业基地|产业示范"), MandateEventType.INDUSTRY_PLAN),
    (re.compile(r"补贴|税收优惠|专项资金|财政支持|资助|奖励|扶持资金|贴息"), MandateEventType.SUBSIDY_SUPPORT),
    (re.compile(r"采购|招标|中标|订单|项目中标|采购订单|集中采购"), MandateEventType.PROCUREMENT_ORDER),
    (re.compile(r"许可证|牌照|资质|获批|批文|通行证|准入|审批通过"), MandateEventType.LICENSE_APPROVAL),
    (re.compile(r"并购|重组|吸收合并|战略重组|重大资产重组|收购|兼并"), MandateEventType.M_AND_A_RESTRUCTURING),
    (re.compile(r"国企改革|混改|国资|股权激励.*国企|央企.*行动|央企改革"), MandateEventType.SOE_REFORM),
    (re.compile(r"回购|评级|增持|减持|买入评级|卖出评级"), MandateEventType.BUYBACK_RATING),
]


@dataclass
class MandateSignal:
    symbol: str = ""
    topic: str = ""
    title: str = ""
    source: str = ""
    source_level: str = ""
    date: str = ""
    evidence_text: str = ""
    evidence_url: str = ""
    event_type: str = ""
    direction: str = "neutral"
    confidence: float = 0.0
    policy_tags: list[str] = field(default_factory=list)
    industry_tags: list[str] = field(default_factory=list)
    company_role: str = ""
    raw_refs: list[dict] = field(default_factory=list)

    def __post_init__(self):
        if isinstance(self.source_level, SourceLevel):
            self.source_level = self.source_level.value
        if isinstance(self.event_type, MandateEventType):
            self.event_type = self.event_type.value
        self._clamp_confidence()

    def _clamp_confidence(self):
        self.confidence = max(0.0, min(1.0, self.confidence))
        if not self._has_minimum_evidence():
            self.confidence = min(self.confidence, _MAX_CONFIDENCE_LOW_EVIDENCE)

    def _has_minimum_evidence(self) -> bool:
        return bool(self.title and self.source and self.date)

    @property
    def source_level_enum(self) -> Optional[SourceLevel]:
        try:
            return SourceLevel(self.source_level)
        except ValueError:
            return None

    @property
    def event_type_enum(self) -> Optional[MandateEventType]:
        try:
            return MandateEventType(self.event_type)
        except ValueError:
            return None

    @property
    def source_level_weight(self) -> float:
        sl = self.source_level_enum
        if sl is None:
            return 0.0
        return _SOURCE_LEVEL_WEIGHT.get(sl, 0.0)

    def is_high_authority(self) -> bool:
        sl = self.source_level_enum
        if sl is None:
            return False
        return _SOURCE_LEVEL_WEIGHT.get(sl, 0.0) >= 0.7

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "topic": self.topic,
            "title": self.title,
            "source": self.source,
            "source_level": self.source_level,
            "date": self.date,
            "evidence_text": self.evidence_text,
            "evidence_url": self.evidence_url,
            "event_type": self.event_type,
            "direction": self.direction,
            "confidence": round(self.confidence, 3),
            "policy_tags": self.policy_tags,
            "industry_tags": self.industry_tags,
            "company_role": self.company_role,
            "raw_refs": self.raw_refs,
        }


def classify_source_level(title: str, source: str = "") -> SourceLevel:
    if not title and not source:
        return SourceLevel.MEDIA
    text = f"{title} {source}"
    for pattern, level in _SOURCE_KEYWORD_PATTERNS:
        if pattern.search(text):
            return level
    return SourceLevel.MEDIA


def classify_event_type(title: str) -> MandateEventType:
    if not title:
        return MandateEventType.POLICY_DOCUMENT
    for pattern, et in _EVENT_TYPE_KEYWORDS:
        if pattern.search(title):
            return et
    return MandateEventType.POLICY_DOCUMENT


def compute_confidence(
    source_level: SourceLevel,
    has_title: bool,
    has_date: bool,
    has_source: bool,
    has_evidence_text: bool = False,
) -> float:
    if not (has_title or has_date or has_source or has_evidence_text):
        return 0.0
    weight = _SOURCE_LEVEL_WEIGHT.get(source_level, 0.0)
    evidence_factor = 0.0
    if has_title:
        evidence_factor += 0.35
    if has_date:
        evidence_factor += 0.25
    if has_source:
        evidence_factor += 0.2
    if has_evidence_text:
        evidence_factor += 0.2
    conf = weight * 0.6 + evidence_factor * 0.4
    if not (has_title and has_source and has_date):
        conf = min(conf, _MAX_CONFIDENCE_LOW_EVIDENCE)
    return round(min(1.0, max(0.0, conf)), 3)


def event_item_to_mandate_signal(item, direction: str = "") -> MandateSignal:
    title = getattr(item, "title", "") or ""
    source = getattr(item, "source", "") or ""
    date = getattr(item, "date", "") or ""
    symbol = getattr(item, "symbol", "") or ""
    evidence_text = title

    source_level = classify_source_level(title, source)
    event_type = classify_event_type(title)

    detail = getattr(item, "detail", {}) or {}
    evidence_url = detail.get("url", "") or detail.get("evidence_url", "")
    item_direction = direction or getattr(item, "direction", "neutral") or "neutral"

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
        "original_event_type": getattr(item, "event_type", ""),
        "original_direction": getattr(item, "direction", ""),
    }
    if detail:
        raw_ref["detail_keys"] = sorted(detail.keys())

    return MandateSignal(
        symbol=symbol,
        topic="",
        title=title,
        source=source,
        source_level=source_level.value,
        date=date,
        evidence_text=evidence_text,
        evidence_url=evidence_url,
        event_type=event_type.value,
        direction=item_direction,
        confidence=confidence,
        policy_tags=[],
        industry_tags=[],
        company_role="",
        raw_refs=[raw_ref],
    )


def convert_event_items(
    items_by_symbol: dict[str, list],
) -> dict[str, list[MandateSignal]]:
    result: dict[str, list[MandateSignal]] = {}
    for symbol, items in items_by_symbol.items():
        if not symbol:
            continue
        signals: list[MandateSignal] = []
        for item in items:
            sig = event_item_to_mandate_signal(item)
            if sig.title:
                signals.append(sig)
        if signals:
            result[symbol] = signals
    return result


def validate_mandate_signal(signal: MandateSignal) -> list[str]:
    issues: list[str] = []
    if not signal.symbol:
        issues.append("missing symbol")
    if not signal.title:
        issues.append("missing title")
    if not signal.source:
        issues.append("missing source")
    if not signal.date:
        issues.append("missing date")
    if not signal.source_level:
        issues.append("missing source_level")
    if not signal.event_type:
        issues.append("missing event_type")
    if signal.confidence > _MAX_CONFIDENCE_LOW_EVIDENCE and not signal._has_minimum_evidence():
        issues.append(
            f"confidence {signal.confidence} exceeds max {_MAX_CONFIDENCE_LOW_EVIDENCE} "
            f"for low-evidence signal"
        )
    return issues
