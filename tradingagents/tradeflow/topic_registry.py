# [H-012] mandate_topic_registry
"""Topic Registry — maintainable registry of 昊天 policy topics with
lifecycle, evidence, industry chain roles and a per-topic watchlist.

Upgrades scattered policy keywords into a structured, queryable registry:
  1. Pre-defined topic definitions with policy level, keywords, chain segments.
  2. Candidate → topic matching via mandate_topic / policy_tags / keywords.
  3. Topic watchlist generation: core symbols, beneficiary path, evidence gaps.
  4. Short note suggestion that feeds into H-008 watchlist_note.

Constraints:
  - No LLM calls.
  - Topic state changes only affect candidate explanation and ranking,
    never the trade action.
  - Does not overwrite user watchlist notes — only generates suggestions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .topic_lifecycle import (
    TopicLifecycleState,
    _LIFECYCLE_STATE_LABELS,
    _LEFT_SIDE_STATES,
    _OBSERVE_ONLY_STATES,
)


# ── Policy levels ─────────────────────────────────────────────────────

POLICY_LEVEL_CENTRAL = "CENTRAL"
POLICY_LEVEL_MINISTRY = "MINISTRY"
POLICY_LEVEL_LOCAL = "LOCAL"
POLICY_LEVEL_INDUSTRY = "INDUSTRY"
POLICY_LEVEL_MEDIA = "MEDIA"
POLICY_LEVEL_UNKNOWN = "UNKNOWN"

_POLICY_LEVEL_WEIGHTS: dict[str, int] = {
    POLICY_LEVEL_CENTRAL: 5,
    POLICY_LEVEL_MINISTRY: 4,
    POLICY_LEVEL_LOCAL: 3,
    POLICY_LEVEL_INDUSTRY: 2,
    POLICY_LEVEL_MEDIA: 1,
    POLICY_LEVEL_UNKNOWN: 0,
}


# ── Topic status (distinct from lifecycle state) ─────────────────────
# Maps to task description: 酝酿/发酵/确认/兑现/退潮

TOPIC_STATUS_BREWING = "BREWING"        # 酝酿 — early stage, left-side
TOPIC_STATUS_FERMENTING = "FERMENTING"  # 发酵 — accelerating
TOPIC_STATUS_CONFIRMING = "CONFIRMING"  # 确认 — multi-source confirmed
TOPIC_STATUS_DELIVERING = "DELIVERING"  # 兑现 — actual orders/revenue
TOPIC_STATUS_RECEDING = "RECEDING"      # 退潮 — fading/crowded
TOPIC_STATUS_UNKNOWN = "UNKNOWN"

_TOPIC_STATUS_LABELS: dict[str, str] = {
    TOPIC_STATUS_BREWING: "酝酿",
    TOPIC_STATUS_FERMENTING: "发酵",
    TOPIC_STATUS_CONFIRMING: "确认",
    TOPIC_STATUS_DELIVERING: "兑现",
    TOPIC_STATUS_RECEDING: "退潮",
    TOPIC_STATUS_UNKNOWN: "未知",
}

_LEFT_SIDE_STATUSES = {TOPIC_STATUS_BREWING, TOPIC_STATUS_FERMENTING}
_CONFIRM_STATUSES = {TOPIC_STATUS_CONFIRMING, TOPIC_STATUS_DELIVERING}
_OBSERVE_ONLY_STATUSES = {TOPIC_STATUS_RECEDING}


def _lifecycle_to_status(lifecycle_state: str) -> str:
    """Map H-010 lifecycle state to H-012 topic status."""
    mapping = {
        TopicLifecycleState.EMERGING.value: TOPIC_STATUS_BREWING,
        TopicLifecycleState.ACCELERATING.value: TOPIC_STATUS_FERMENTING,
        TopicLifecycleState.CONFIRMING.value: TOPIC_STATUS_CONFIRMING,
        TopicLifecycleState.CROWDED.value: TOPIC_STATUS_RECEDING,
        TopicLifecycleState.FADING.value: TOPIC_STATUS_RECEDING,
        TopicLifecycleState.UNKNOWN.value: TOPIC_STATUS_UNKNOWN,
    }
    return mapping.get(lifecycle_state, TOPIC_STATUS_UNKNOWN)


# ── Industry chain segment ────────────────────────────────────────────

@dataclass
class ChainSegment:
    """One segment of an industry chain within a topic."""
    name: str = ""
    role: str = ""
    example_symbols: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "role": self.role,
            "example_symbols": self.example_symbols,
        }


# ── Topic definition (static, pre-defined) ───────────────────────────

@dataclass
class TopicDefinition:
    """Pre-defined topic definition with keywords, policy level, chain."""
    topic: str = ""
    aliases: list[str] = field(default_factory=list)
    policy_level: str = POLICY_LEVEL_UNKNOWN
    keywords: list[str] = field(default_factory=list)
    chain_segments: list[ChainSegment] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "topic": self.topic,
            "aliases": self.aliases,
            "policy_level": self.policy_level,
            "keywords": self.keywords,
            "chain_segments": [s.to_dict() for s in self.chain_segments],
            "description": self.description,
        }


# ── Topic registry entry (dynamic, updated at runtime) ───────────────

@dataclass
class TopicRegistryEntry:
    """One topic entry in the registry — tracks evidence and state."""
    topic: str = ""
    topic_status: str = TOPIC_STATUS_UNKNOWN
    topic_status_label: str = "未知"
    lifecycle_state: str = TopicLifecycleState.UNKNOWN.value
    policy_level: str = POLICY_LEVEL_UNKNOWN
    last_signal_date: str = ""
    signal_count: int = 0
    evidence_links: list[dict] = field(default_factory=list)
    evidence_summary: str = ""
    chain_segments: list[ChainSegment] = field(default_factory=list)
    is_left_side: bool = False
    is_observe_only: bool = False
    is_confirmed: bool = False
    definition: Optional[TopicDefinition] = None
    matched_candidates: list[str] = field(default_factory=list)
    update_count: int = 0

    def to_dict(self) -> dict:
        return {
            "topic": self.topic,
            "topic_status": self.topic_status,
            "topic_status_label": self.topic_status_label,
            "lifecycle_state": self.lifecycle_state,
            "policy_level": self.policy_level,
            "policy_level_weight": _POLICY_LEVEL_WEIGHTS.get(self.policy_level, 0),
            "last_signal_date": self.last_signal_date,
            "signal_count": self.signal_count,
            "evidence_links": self.evidence_links,
            "evidence_summary": self.evidence_summary,
            "chain_segments": [s.to_dict() for s in self.chain_segments],
            "is_left_side": self.is_left_side,
            "is_observe_only": self.is_observe_only,
            "is_confirmed": self.is_confirmed,
            "matched_candidates": self.matched_candidates,
            "update_count": self.update_count,
        }


# ── Watchlist item ────────────────────────────────────────────────────

@dataclass
class TopicWatchlistSymbol:
    """One symbol within a topic watchlist."""
    symbol: str = ""
    name: str = ""
    company_role: str = ""
    beneficiary_path: list[str] = field(default_factory=list)
    mandate_score: float = 0.0
    tier: str = ""
    evidence_gaps: list[str] = field(default_factory=list)
    note_suggestion: str = ""

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "company_role": self.company_role,
            "beneficiary_path": self.beneficiary_path,
            "mandate_score": round(self.mandate_score, 1),
            "tier": self.tier,
            "evidence_gaps": self.evidence_gaps,
            "note_suggestion": self.note_suggestion,
        }


@dataclass
class TopicWatchlistEntry:
    """One topic's watchlist — core symbols + beneficiary paths + gaps."""
    topic: str = ""
    topic_status: str = TOPIC_STATUS_UNKNOWN
    topic_status_label: str = "未知"
    policy_level: str = POLICY_LEVEL_UNKNOWN
    is_left_side: bool = False
    is_observe_only: bool = False
    symbols: list[TopicWatchlistSymbol] = field(default_factory=list)
    chain_segments: list[ChainSegment] = field(default_factory=list)
    counter_evidence_gaps: list[str] = field(default_factory=list)
    note_suggestion: str = ""

    def to_dict(self) -> dict:
        return {
            "topic": self.topic,
            "topic_status": self.topic_status,
            "topic_status_label": self.topic_status_label,
            "policy_level": self.policy_level,
            "is_left_side": self.is_left_side,
            "is_observe_only": self.is_observe_only,
            "symbols": [s.to_dict() for s in self.symbols],
            "chain_segments": [s.to_dict() for s in self.chain_segments],
            "counter_evidence_gaps": self.counter_evidence_gaps,
            "note_suggestion": self.note_suggestion,
        }


@dataclass
class TopicWatchlistResult:
    """Full watchlist result across all topics."""
    topics: list[TopicWatchlistEntry] = field(default_factory=list)
    total_topics: int = 0
    total_symbols: int = 0

    def to_dict(self) -> dict:
        return {
            "topics": [t.to_dict() for t in self.topics],
            "total_topics": self.total_topics,
            "total_symbols": self.total_symbols,
        }


# ── Pre-defined topic definitions ────────────────────────────────────

_DEFAULT_TOPIC_DEFINITIONS: list[TopicDefinition] = [
    TopicDefinition(
        topic="低空经济",
        aliases=["eVTOL", "无人机", "低空"],
        policy_level=POLICY_LEVEL_MINISTRY,
        keywords=["低空经济", "eVTOL", "无人机", "飞行汽车", "空中出租车", "低空空域"],
        chain_segments=[
            ChainSegment(name="整机制造", role="核心整机", example_symbols=[]),
            ChainSegment(name="动力系统", role="电机/电池", example_symbols=[]),
            ChainSegment(name="航电系统", role="飞控/导航", example_symbols=[]),
            ChainSegment(name="空管基础设施", role="通信/导航/监视", example_symbols=[]),
        ],
        description="低空经济产业：eVTOL、无人机、低空空域管理与基础设施。",
    ),
    TopicDefinition(
        topic="算力",
        aliases=["AI算力", "智算中心", "GPU"],
        policy_level=POLICY_LEVEL_MINISTRY,
        keywords=["算力", "智算中心", "GPU", "AI芯片", "数据中心", "液冷", "光模块", "东数西算"],
        chain_segments=[
            ChainSegment(name="AI芯片", role="GPU/ASIC", example_symbols=[]),
            ChainSegment(name="光模块", role="光互联", example_symbols=[]),
            ChainSegment(name="服务器", role="AI服务器", example_symbols=[]),
            ChainSegment(name="液冷散热", role="热管理", example_symbols=[]),
            ChainSegment(name="IDC运营", role="数据中心", example_symbols=[]),
        ],
        description="算力基础设施：AI芯片、光模块、服务器、数据中心、液冷。",
    ),
    TopicDefinition(
        topic="半导体设备",
        aliases=["半导体", "国产替代", "芯片设备"],
        policy_level=POLICY_LEVEL_MINISTRY,
        keywords=["半导体设备", "光刻", "刻蚀", "薄膜沉积", "离子注入", "量测", "国产替代", "芯片制造"],
        chain_segments=[
            ChainSegment(name="刻蚀设备", role="核心工艺设备", example_symbols=[]),
            ChainSegment(name="薄膜沉积", role="CVD/PVD", example_symbols=[]),
            ChainSegment(name="光刻设备", role="核心前道光刻", example_symbols=[]),
            ChainSegment(name="量测设备", role="检测/量测", example_symbols=[]),
            ChainSegment(name="清洗设备", role="清洗/湿法", example_symbols=[]),
        ],
        description="半导体设备国产替代：刻蚀、薄膜、光刻、量测、清洗。",
    ),
    TopicDefinition(
        topic="机器人",
        aliases=["人形机器人", "工业机器人", "具身智能"],
        policy_level=POLICY_LEVEL_MINISTRY,
        keywords=["机器人", "人形机器人", "具身智能", "减速器", "伺服", "机器视觉", "谐波减速器"],
        chain_segments=[
            ChainSegment(name="减速器", role="核心零部件", example_symbols=[]),
            ChainSegment(name="伺服系统", role="动力控制", example_symbols=[]),
            ChainSegment(name="机器视觉", role="感知", example_symbols=[]),
            ChainSegment(name="整机制造", role="本体", example_symbols=[]),
        ],
        description="机器人产业：人形机器人、核心零部件（减速器、伺服）、机器视觉。",
    ),
    TopicDefinition(
        topic="新能源",
        aliases=["光伏", "风电", "储能", "锂电"],
        policy_level=POLICY_LEVEL_MINISTRY,
        keywords=["光伏", "风电", "储能", "锂电池", "新能源", "HJT", "TOPCon", "钠离子电池", "固态电池"],
        chain_segments=[
            ChainSegment(name="光伏", role="硅片/电池/组件", example_symbols=[]),
            ChainSegment(name="锂电池", role="正负极/电解液/隔膜", example_symbols=[]),
            ChainSegment(name="储能", role="储能系统集成", example_symbols=[]),
            ChainSegment(name="风电", role="风机/塔筒/海风", example_symbols=[]),
        ],
        description="新能源：光伏、锂电、储能、风电产业链。",
    ),
    TopicDefinition(
        topic="国产替代",
        aliases=["自主可控", "信创"],
        policy_level=POLICY_LEVEL_CENTRAL,
        keywords=["国产替代", "自主可控", "信创", "国产软件", "操作系统", "数据库", "EDA"],
        chain_segments=[
            ChainSegment(name="基础软件", role="OS/数据库", example_symbols=[]),
            ChainSegment(name="EDA工具", role="芯片设计工具", example_symbols=[]),
            ChainSegment(name="应用软件", role="行业应用", example_symbols=[]),
        ],
        description="国产替代/信创：操作系统、数据库、EDA、行业软件。",
    ),
    TopicDefinition(
        topic="并购重组",
        aliases=["重组", "借壳"],
        policy_level=POLICY_LEVEL_CENTRAL,
        keywords=["并购重组", "重大资产重组", "借壳", "吸收合并", "分拆上市"],
        chain_segments=[
            ChainSegment(name="央国企重组", role="集团整合", example_symbols=[]),
            ChainSegment(name="产业并购", role="同业整合", example_symbols=[]),
        ],
        description="并购重组：央企整合、产业并购、分拆上市。",
    ),
    TopicDefinition(
        topic="出海",
        aliases=["海外扩张", "国际化"],
        policy_level=POLICY_LEVEL_INDUSTRY,
        keywords=["出海", "海外", "国际化", "跨境电商", "海外建厂", "一带一路"],
        chain_segments=[
            ChainSegment(name="汽车出海", role="整车出口", example_symbols=[]),
            ChainSegment(name="跨境电商", role="出海零售", example_symbols=[]),
            ChainSegment(name="工程出海", role="海外工程", example_symbols=[]),
        ],
        description="企业出海：汽车出口、跨境电商、海外工程。",
    ),
]


def get_default_topic_definitions() -> list[TopicDefinition]:
    """Return the default pre-defined topic definitions."""
    return list(_DEFAULT_TOPIC_DEFINITIONS)


def match_topic_from_text(
    text: str,
    definitions: Optional[list[TopicDefinition]] = None,
) -> str:
    """Match a topic from text using keyword/alias matching.

    Returns the topic name or empty string if no match.
    """
    if not text:
        return ""
    defs = definitions if definitions is not None else _DEFAULT_TOPIC_DEFINITIONS
    text_lower = text.lower()
    for d in defs:
        for kw in d.keywords:
            if kw and kw in text:
                return d.topic
        for alias in d.aliases:
            if alias and alias.lower() in text_lower:
                return d.topic
    return ""


def match_topic(
    mandate_topic: str = "",
    policy_tags: Optional[list[str]] = None,
    name: str = "",
    definitions: Optional[list[TopicDefinition]] = None,
) -> str:
    """Match topic from mandate_topic, policy_tags, or name.

    Priority: explicit mandate_topic > policy_tags keyword > name keyword.
    """
    if mandate_topic:
        defs = definitions if definitions is not None else _DEFAULT_TOPIC_DEFINITIONS
        all_topics = {d.topic for d in defs}
        if mandate_topic in all_topics:
            return mandate_topic

    for tag in (policy_tags or []):
        matched = match_topic_from_text(tag, definitions)
        if matched:
            return matched

    if mandate_topic:
        matched = match_topic_from_text(mandate_topic, definitions)
        if matched:
            return matched

    if name:
        matched = match_topic_from_text(name, definitions)
        if matched:
            return matched

    return mandate_topic or ""


# ── Topic Registry ────────────────────────────────────────────────────

class TopicRegistry:
    """In-memory registry of topic entries."""

    def __init__(self, definitions: Optional[list[TopicDefinition]] = None) -> None:
        self._definitions: dict[str, TopicDefinition] = {}
        self._entries: dict[str, TopicRegistryEntry] = {}
        for d in (definitions or _DEFAULT_TOPIC_DEFINITIONS):
            self._definitions[d.topic] = d

    def get_definition(self, topic: str) -> Optional[TopicDefinition]:
        return self._definitions.get(topic)

    def get_definition_by_alias(self, text: str) -> Optional[TopicDefinition]:
        """Find a definition by matching text against keywords/aliases."""
        matched = match_topic_from_text(text, list(self._definitions.values()))
        if matched:
            return self._definitions.get(matched)
        return None

    def all_definitions(self) -> dict[str, TopicDefinition]:
        return dict(self._definitions)

    def register_candidate_topic(
        self,
        topic: str = "",
        mandate_topic: str = "",
        policy_tags: Optional[list[str]] = None,
        name: str = "",
        lifecycle_state: str = "",
        last_signal_date: str = "",
        signal_count: int = 0,
        evidence_refs: Optional[list[dict]] = None,
        policy_level: str = "",
        candidate_symbol: str = "",
    ) -> TopicRegistryEntry:
        """Register or update a topic entry from a candidate.

        Returns the updated TopicRegistryEntry.
        """
        resolved = match_topic(
            mandate_topic=mandate_topic or topic,
            policy_tags=policy_tags,
            name=name,
            definitions=list(self._definitions.values()),
        )
        if not resolved:
            resolved = mandate_topic or topic

        if not resolved:
            return TopicRegistryEntry()

        entry = self._entries.get(resolved)
        if entry is None:
            definition = self._definitions.get(resolved)
            entry = TopicRegistryEntry(
                topic=resolved,
                policy_level=policy_level or (definition.policy_level if definition else POLICY_LEVEL_UNKNOWN),
                chain_segments=list(definition.chain_segments) if definition else [],
                definition=definition,
            )
            self._entries[resolved] = entry

        if candidate_symbol and candidate_symbol not in entry.matched_candidates:
            entry.matched_candidates.append(candidate_symbol)

        if signal_count > entry.signal_count:
            entry.signal_count = signal_count

        if last_signal_date and last_signal_date > entry.last_signal_date:
            entry.last_signal_date = last_signal_date

        ev_refs = evidence_refs or []
        for ref in ev_refs:
            title = ref.get("title", "")
            source = ref.get("source", "")
            date = ref.get("date", "")
            url = ref.get("url", "")
            link_entry = {"title": title, "source": source, "date": date, "url": url}
            if link_entry not in entry.evidence_links:
                entry.evidence_links.append(link_entry)
        entry.evidence_links = entry.evidence_links[:20]

        if ev_refs:
            titles = [r.get("title", "") for r in ev_refs if r.get("title")]
            if titles:
                existing = entry.evidence_summary
                new_titles = "; ".join(titles[:3])
                if new_titles not in existing:
                    entry.evidence_summary = f"{existing}; {new_titles}".strip("; ").strip() if existing else new_titles

        if policy_level:
            current_weight = _POLICY_LEVEL_WEIGHTS.get(entry.policy_level, 0)
            new_weight = _POLICY_LEVEL_WEIGHTS.get(policy_level, 0)
            if new_weight > current_weight:
                entry.policy_level = policy_level

        if lifecycle_state:
            entry.lifecycle_state = lifecycle_state

        entry.topic_status = _lifecycle_to_status(entry.lifecycle_state)
        entry.topic_status_label = _TOPIC_STATUS_LABELS.get(entry.topic_status, "未知")
        entry.is_left_side = entry.topic_status in _LEFT_SIDE_STATUSES
        entry.is_observe_only = entry.topic_status in _OBSERVE_ONLY_STATUSES
        entry.is_confirmed = entry.topic_status in _CONFIRM_STATUSES

        entry.update_count += 1
        return entry

    def get(self, topic: str) -> Optional[TopicRegistryEntry]:
        return self._entries.get(topic)

    def get_or_create(self, topic: str) -> TopicRegistryEntry:
        if topic not in self._entries:
            definition = self._definitions.get(topic)
            self._entries[topic] = TopicRegistryEntry(
                topic=topic,
                policy_level=definition.policy_level if definition else POLICY_LEVEL_UNKNOWN,
                chain_segments=list(definition.chain_segments) if definition else [],
                definition=definition,
            )
        return self._entries[topic]

    def all_entries(self) -> dict[str, TopicRegistryEntry]:
        return dict(self._entries)

    def to_dict(self) -> dict:
        return {topic: entry.to_dict() for topic, entry in self._entries.items()}

    def clear(self) -> None:
        self._entries.clear()


_DEFAULT_TOPIC_REGISTRY = TopicRegistry()


def get_default_topic_registry() -> TopicRegistry:
    return _DEFAULT_TOPIC_REGISTRY


# ── Topic status helpers ──────────────────────────────────────────────

def get_topic_status_label(status: str) -> str:
    return _TOPIC_STATUS_LABELS.get(status, "未知")


def is_topic_left_side(status: str) -> bool:
    return status in _LEFT_SIDE_STATUSES


def is_topic_observe_only(status: str) -> bool:
    return status in _OBSERVE_ONLY_STATUSES


# ── Watchlist note suggestion ─────────────────────────────────────────

_FORBIDDEN_WORDS = {"买入", "卖出", "清仓", "满仓", "梭哈", "加仓", "减仓"}


def _sanitize_note(text: str) -> str:
    for w in _FORBIDDEN_WORDS:
        text = text.replace(w, "***")
    return text


def suggest_topic_watchlist_note(
    topic: str = "",
    topic_status: str = TOPIC_STATUS_UNKNOWN,
    policy_level: str = POLICY_LEVEL_UNKNOWN,
    symbol_count: int = 0,
    top_symbol: str = "",
    top_role: str = "",
    evidence_gaps: Optional[list[str]] = None,
) -> str:
    """Generate a short note suggestion for a topic watchlist entry.

    Format: `主题｜状态｜级别｜核心X只｜缺口:a/b`

    Does NOT contain buy/sell suggestions.
    Does NOT overwrite user notes.
    """
    if not topic:
        return ""

    parts: list[str] = [topic]

    status_label = _TOPIC_STATUS_LABELS.get(topic_status, "未知")
    parts.append(status_label)

    level_map = {
        POLICY_LEVEL_CENTRAL: "中央",
        POLICY_LEVEL_MINISTRY: "部委",
        POLICY_LEVEL_LOCAL: "地方",
        POLICY_LEVEL_INDUSTRY: "行业",
        POLICY_LEVEL_MEDIA: "媒体",
        POLICY_LEVEL_UNKNOWN: "未知级别",
    }
    level_label = level_map.get(policy_level, "未知级别")
    parts.append(level_label)

    if symbol_count > 0:
        parts.append(f"核心{symbol_count}只")
    else:
        parts.append("暂无候选")

    if top_symbol and top_role:
        parts.append(f"标杆:{top_symbol}({top_role})")

    gaps = evidence_gaps or []
    if gaps:
        parts.append(f"缺口:{'/'.join(gaps[:3])}")

    return _sanitize_note("｜".join(parts))


# ── Watchlist generation ──────────────────────────────────────────────

def build_topic_watchlist(
    candidates: list[dict],
    registry: Optional[TopicRegistry] = None,
    max_symbols_per_topic: int = 5,
) -> TopicWatchlistResult:
    """Build a per-topic watchlist from candidate dicts.

    Each candidate dict should have fields like:
      symbol, name, mandate_topic, policy_tags, company_role,
      beneficiary_path, mandate_score_component, tier,
      blocking_evidence_gaps, watchlist_evidence_gap,
      topic_lifecycle_state, topic_signal_count, topic_last_signal_date,
      policy_evidence_refs.

    Returns TopicWatchlistResult with per-topic entries.
    """
    reg = registry if registry is not None else get_default_topic_registry()

    topic_candidates: dict[str, list[dict]] = {}

    for cand in candidates:
        topic = match_topic(
            mandate_topic=cand.get("mandate_topic", ""),
            policy_tags=cand.get("policy_tags"),
            name=cand.get("name", ""),
            definitions=list(reg.all_definitions().values()),
        )
        if not topic:
            topic = cand.get("mandate_topic", "")
        if not topic:
            continue

        if topic not in topic_candidates:
            topic_candidates[topic] = []
        topic_candidates[topic].append(cand)

        evidence_refs = cand.get("policy_evidence_refs") or cand.get("mandate_evidence_refs") or []
        policy_level = ""
        for ref in evidence_refs:
            sl = ref.get("source_level", "")
            if sl in ("CENTRAL", "STATE_COUNCIL"):
                policy_level = POLICY_LEVEL_CENTRAL
                break
            elif sl in ("MINISTRY",) and not policy_level:
                policy_level = POLICY_LEVEL_MINISTRY
            elif sl in ("LOCAL",) and not policy_level:
                policy_level = POLICY_LEVEL_LOCAL

        reg.register_candidate_topic(
            topic=topic,
            mandate_topic=cand.get("mandate_topic", ""),
            policy_tags=cand.get("policy_tags"),
            name=cand.get("name", ""),
            lifecycle_state=cand.get("topic_lifecycle_state", ""),
            last_signal_date=cand.get("topic_last_signal_date", ""),
            signal_count=cand.get("topic_signal_count", 0),
            evidence_refs=evidence_refs,
            policy_level=policy_level,
            candidate_symbol=cand.get("symbol", ""),
        )

    watchlist_topics: list[TopicWatchlistEntry] = []
    total_symbols = 0

    for topic, topic_cands in topic_candidates.items():
        entry = reg.get(topic)
        definition = reg.get_definition(topic)

        sorted_cands = sorted(
            topic_cands,
            key=lambda c: c.get("mandate_score_component", c.get("composite_score", 0.0)),
            reverse=True,
        )
        top_cands = sorted_cands[:max_symbols_per_topic]

        watch_symbols: list[TopicWatchlistSymbol] = []
        for cand in top_cands:
            gaps = cand.get("blocking_evidence_gaps") or cand.get("watchlist_evidence_gap") or []
            note = suggest_topic_watchlist_note(
                topic=topic,
                topic_status=entry.topic_status if entry else TOPIC_STATUS_UNKNOWN,
                policy_level=entry.policy_level if entry else POLICY_LEVEL_UNKNOWN,
                symbol_count=len(top_cands),
                top_symbol=cand.get("symbol", ""),
                top_role=cand.get("company_role", ""),
                evidence_gaps=gaps,
            )
            watch_symbols.append(TopicWatchlistSymbol(
                symbol=cand.get("symbol", ""),
                name=cand.get("name", ""),
                company_role=cand.get("company_role", ""),
                beneficiary_path=cand.get("beneficiary_path", []),
                mandate_score=cand.get("mandate_score_component", cand.get("composite_score", 0.0)),
                tier=cand.get("tier", ""),
                evidence_gaps=gaps,
                note_suggestion=note,
            ))
            total_symbols += 1

        counter_evidence_gaps: list[str] = []
        seen_gaps: set[str] = set()
        for cand in topic_cands:
            for g in (cand.get("blocking_evidence_gaps") or []):
                if g and g not in seen_gaps:
                    counter_evidence_gaps.append(g)
                    seen_gaps.add(g)
                    if len(counter_evidence_gaps) >= 5:
                        break
            if len(counter_evidence_gaps) >= 5:
                break

        topic_note = suggest_topic_watchlist_note(
            topic=topic,
            topic_status=entry.topic_status if entry else TOPIC_STATUS_UNKNOWN,
            policy_level=entry.policy_level if entry else POLICY_LEVEL_UNKNOWN,
            symbol_count=len(top_cands),
            top_symbol=top_cands[0].get("symbol", "") if top_cands else "",
            top_role=top_cands[0].get("company_role", "") if top_cands else "",
            evidence_gaps=counter_evidence_gaps,
        )

        chain_segments = []
        if entry and entry.chain_segments:
            chain_segments = list(entry.chain_segments)
        elif definition:
            chain_segments = list(definition.chain_segments)

        watchlist_topics.append(TopicWatchlistEntry(
            topic=topic,
            topic_status=entry.topic_status if entry else TOPIC_STATUS_UNKNOWN,
            topic_status_label=entry.topic_status_label if entry else "未知",
            policy_level=entry.policy_level if entry else POLICY_LEVEL_UNKNOWN,
            is_left_side=entry.is_left_side if entry else False,
            is_observe_only=entry.is_observe_only if entry else False,
            symbols=watch_symbols,
            chain_segments=chain_segments,
            counter_evidence_gaps=counter_evidence_gaps,
            note_suggestion=topic_note,
        ))

    status_order = {
        TOPIC_STATUS_BREWING: 0,
        TOPIC_STATUS_FERMENTING: 1,
        TOPIC_STATUS_CONFIRMING: 2,
        TOPIC_STATUS_DELIVERING: 3,
        TOPIC_STATUS_RECEDING: 4,
        TOPIC_STATUS_UNKNOWN: 5,
    }
    watchlist_topics.sort(key=lambda t: (status_order.get(t.topic_status, 9), -len(t.symbols)))

    return TopicWatchlistResult(
        topics=watchlist_topics,
        total_topics=len(watchlist_topics),
        total_symbols=total_symbols,
    )
