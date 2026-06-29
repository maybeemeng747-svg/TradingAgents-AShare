# [H-017] mandate_evidence_packet
"""Mandate Evidence Packet — policy / industry / company three-layer link.

A 昊天 (Haotian) left-side candidate must not be reduced to a single score.
This module assembles a structured, auditable *evidence packet* that surfaces
three layers so the user can judge whether a name is worth long-term
observation:

1. **Policy layer** (``policy_theme``) — which policy topic, what level
   (central / ministry / local), what lifecycle status, and which policy
   documents / meetings back it.
2. **Industry layer** (``industry_chain_role``) — where the company sits in
   the topic's industry chain (upstream chip / midstream module / downstream
   application …) and the beneficiary path segments already matched.
3. **Company layer** (``company_role``) — the company's classified role
   (leader / core supplier / infra provider / application scene / peripheral /
   concept-only / unknown) and whether company-level evidence exists.

The packet also records ``evidence_titles`` (deduplicated, layer-tagged) and
``missing_evidence`` (human-readable gap descriptions). When the policy or
company layer is empty the packet is flagged ``needs_manual_research=True``
so downstream UI / reports never inflate a thin candidate.

Design constraints (from task H-017):
- No LLM calls.
- Does not fabricate policy conclusions — only reorganizes evidence that is
  already present on the candidate, the topic registry and (optionally) the
  raw_evidence dict.
- No buy / sell suggestions.
- Deterministic: same inputs → same output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .industry_mandate_map import CompanyRole, get_industry_chain
from .topic_registry import (
    POLICY_LEVEL_UNKNOWN,
    _POLICY_LEVEL_WEIGHTS,
    _TOPIC_STATUS_LABELS,
    TOPIC_STATUS_UNKNOWN,
    match_topic,
)


# ── Layers / confidence labels ───────────────────────────────────────

LAYER_POLICY = "policy"
LAYER_INDUSTRY = "industry"
LAYER_COMPANY = "company"

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"

# Cap the number of evidence titles retained per packet so the payload stays
# UI-friendly and deterministic.
_MAX_EVIDENCE_TITLES = 12

# raw_evidence keys that carry company-layer evidence when available.
_RAW_EVIDENCE_COMPANY_KEYS = ("announcements", "research_report", "news")


# ── Data model ───────────────────────────────────────────────────────

@dataclass
class EvidenceTitle:
    """One evidence item tagged with its link layer."""

    title: str = ""
    source: str = ""
    date: str = ""
    layer: str = ""
    source_level: str = ""

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "source": self.source,
            "date": self.date,
            "layer": self.layer,
            "source_level": self.source_level,
        }


@dataclass
class MandateEvidencePacket:
    """Policy / industry / company three-layer evidence for one candidate.

    All fields are read-only facts aggregated from existing evidence — never
    a trade recommendation. ``needs_manual_research`` is the only derived
    flag: it is ``True`` when the policy or company layer is too thin to
    support a left-side observation thesis.
    """

    symbol: str = ""
    name: str = ""
    topic: str = ""

    # ── Policy layer ──
    policy_theme: str = ""
    policy_level: str = POLICY_LEVEL_UNKNOWN
    policy_level_weight: int = 0
    topic_status: str = TOPIC_STATUS_UNKNOWN
    topic_status_label: str = "未知"
    policy_evidence_count: int = 0

    # ── Industry layer ──
    industry_chain_role: str = ""
    industry_chain_segments: List[str] = field(default_factory=list)
    beneficiary_path: List[str] = field(default_factory=list)

    # ── Company layer ──
    company_role: str = CompanyRole.UNKNOWN.value
    company_role_label: str = "未知"
    raw_company_role: str = ""
    has_company_evidence: bool = False
    company_evidence_available: List[str] = field(default_factory=list)

    # ── Aggregated ──
    evidence_titles: List[EvidenceTitle] = field(default_factory=list)
    missing_evidence: List[str] = field(default_factory=list)
    needs_manual_research: bool = False
    confidence: str = CONFIDENCE_LOW
    confidence_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "topic": self.topic,
            "policy_theme": self.policy_theme,
            "policy_level": self.policy_level,
            "policy_level_weight": self.policy_level_weight,
            "topic_status": self.topic_status,
            "topic_status_label": self.topic_status_label,
            "policy_evidence_count": self.policy_evidence_count,
            "industry_chain_role": self.industry_chain_role,
            "industry_chain_segments": list(self.industry_chain_segments),
            "beneficiary_path": list(self.beneficiary_path),
            "company_role": self.company_role,
            "company_role_label": self.company_role_label,
            "raw_company_role": self.raw_company_role,
            "has_company_evidence": self.has_company_evidence,
            "company_evidence_available": list(self.company_evidence_available),
            "evidence_titles": [t.to_dict() for t in self.evidence_titles],
            "missing_evidence": list(self.missing_evidence),
            "needs_manual_research": self.needs_manual_research,
            "confidence": self.confidence,
            "confidence_reason": self.confidence_reason,
        }


# ── Helpers ──────────────────────────────────────────────────────────

_COMPANY_ROLE_LABELS: Dict[str, str] = {
    CompanyRole.LEADER.value: "龙头/核心标的",
    CompanyRole.CORE_SUPPLIER.value: "核心供应商",
    CompanyRole.INFRA_PROVIDER.value: "基础设施",
    CompanyRole.APPLICATION_SCENE.value: "应用场景",
    CompanyRole.PERIPHERAL.value: "外围配套",
    CompanyRole.CONCEPT_ONLY.value: "仅概念关联",
    CompanyRole.UNKNOWN.value: "未知",
}


def _company_role_label(role: str) -> str:
    return _COMPANY_ROLE_LABELS.get(role, "未知")


def _resolve_topic(candidate: dict) -> str:
    """Resolve the canonical policy topic for a candidate dict."""
    return match_topic(
        mandate_topic=candidate.get("mandate_topic", ""),
        policy_tags=candidate.get("policy_tags"),
        name=candidate.get("name", ""),
    )


def _collect_policy_titles(
    candidate: dict,
    topic_entry: Optional[Any],
) -> List[EvidenceTitle]:
    """Collect evidence titles for the policy layer.

    Sources (in priority order):
    1. ``policy_evidence_refs`` on the candidate (S-001, structured).
    2. ``mandate_evidence_refs`` on the candidate (H-003, structured).
    3. ``evidence_links`` on the topic registry entry (H-012).
    """
    titles: List[EvidenceTitle] = []
    seen: set[str] = set()

    def _add(title: str, source: str, date: str, level: str) -> None:
        key = f"{title}|{source}|{date}"
        if not title or key in seen:
            return
        seen.add(key)
        titles.append(EvidenceTitle(
            title=title,
            source=source,
            date=date,
            layer=LAYER_POLICY,
            source_level=level,
        ))

    for ref in (candidate.get("policy_evidence_refs") or []):
        if isinstance(ref, dict):
            _add(
                ref.get("title", ""),
                ref.get("source", ""),
                ref.get("date", ""),
                ref.get("source_level", ""),
            )

    for ref in (candidate.get("mandate_evidence_refs") or []):
        if isinstance(ref, dict):
            _add(
                ref.get("title", ""),
                ref.get("source", ""),
                ref.get("date", ""),
                ref.get("source_level", ""),
            )

    if topic_entry is not None:
        links = getattr(topic_entry, "evidence_links", None) or []
        for link in links:
            if isinstance(link, dict):
                _add(
                    link.get("title", ""),
                    link.get("source", ""),
                    link.get("date", ""),
                    link.get("source_level", ""),
                )

    return titles


def _detect_company_evidence_sources(raw_evidence: Optional[dict]) -> List[str]:
    """Return the raw_evidence keys that carry usable company-layer evidence.

    Only keys whose status is ``OK`` / ``ok`` and whose ``record_count`` is
    positive are reported. Free-text payloads are *not* parsed here — we only
    note availability so the packet can say "公告/研报已采集" without
    fabricating titles from unstructured strings.
    """
    if not raw_evidence or not isinstance(raw_evidence, dict):
        return []
    available: List[str] = []
    for key in _RAW_EVIDENCE_COMPANY_KEYS:
        entry = raw_evidence.get(key)
        if not isinstance(entry, dict):
            continue
        status = str(entry.get("status", "")).upper()
        record_count = entry.get("record_count", 0) or 0
        raw = entry.get("raw")
        has_payload = bool(raw) and raw != "No announcements found for" and str(raw).strip() != ""
        if status in ("OK", "SUCCESS", "SUCCESS_WITH_FALLBACK") and (record_count > 0 or has_payload):
            available.append(key)
    return available


def _build_missing_evidence(
    *,
    topic: str,
    policy_titles: List[EvidenceTitle],
    chain_segments: List[str],
    beneficiary_path: List[str],
    company_role: str,
    raw_company_role: str,
    has_company_evidence: bool,
    company_evidence_available: List[str],
    blocking_gaps: List[str],
) -> List[str]:
    """Assemble human-readable missing-evidence descriptions.

    Only describes what is *missing* — never what action to take.
    """
    missing: List[str] = []

    if not topic:
        missing.append("未匹配到政策主题，无法定位政策链路")
    elif not policy_titles:
        missing.append("缺政策层证据（无政策文件/会议/政策事件引用）")

    if not chain_segments and not beneficiary_path:
        missing.append("缺产业层定位（产业链环节未确认）")
    elif beneficiary_path and not chain_segments:
        missing.append("产业链环节匹配偏弱，受益路径待补充")

    if company_role in ("", CompanyRole.UNKNOWN.value):
        if raw_company_role:
            missing.append("公司角色未分类（需人工确认产业链位置）")
        else:
            missing.append("缺公司层角色（公司定位未知）")
    elif company_role == CompanyRole.CONCEPT_ONLY.value:
        missing.append("公司仅概念关联，缺实质性受益证据")
    elif not has_company_evidence:
        missing.append("缺公司层证据（无公告/研报/公司事件佐证）")

    if not company_evidence_available:
        missing.append("公告/研报原始数据未采集")

    # Surface blocking evidence gaps from H-011 without modification.
    for gap in blocking_gaps[:4]:
        if gap and gap not in missing:
            missing.append(gap)

    return missing


def _assess_confidence(
    *,
    policy_titles_count: int,
    policy_level_weight: int,
    chain_segments_count: int,
    company_role: str,
    has_company_evidence: bool,
) -> tuple[str, str]:
    """Return (confidence, reason) — descriptive, never a buy signal.

    high   — policy + industry + company all have substance.
    medium — two of three layers have substance.
    low    — one or zero layers have substance.
    """
    layers_substantive = 0
    if policy_titles_count >= 2 and policy_level_weight >= 3:
        layers_substantive += 1
    elif policy_titles_count >= 1:
        # single policy doc still counts partially but not "strong"
        pass
    if chain_segments_count >= 1:
        layers_substantive += 1
    if company_role not in ("", CompanyRole.UNKNOWN.value, CompanyRole.CONCEPT_ONLY.value) and has_company_evidence:
        layers_substantive += 1

    if layers_substantive >= 3:
        return CONFIDENCE_HIGH, "政策/产业/公司三层证据齐备"
    if layers_substantive == 2:
        return CONFIDENCE_MEDIUM, "三层中有两层具备证据，尚需补证"
    return CONFIDENCE_LOW, "证据薄弱，建议人工研究补全"


# ── Public API ───────────────────────────────────────────────────────

def build_evidence_packet(
    candidate: dict,
    *,
    topic_entry: Optional[Any] = None,
    raw_evidence: Optional[dict] = None,
) -> MandateEvidencePacket:
    """Build a :class:`MandateEvidencePacket` from a candidate dict.

    Parameters
    ----------
    candidate
        Candidate dict carrying the H-series fields (``mandate_topic``,
        ``policy_tags``, ``policy_evidence_refs``, ``mandate_evidence_refs``,
        ``company_role``, ``beneficiary_path``, ``topic_lifecycle_state``,
        ``blocking_evidence_gaps`` …). Missing fields degrade gracefully.
    topic_entry
        Optional :class:`topic_registry.TopicRegistryEntry` whose
        ``evidence_links`` / ``chain_segments`` / ``policy_level`` enrich
        the packet. When ``None`` the static topic definition is used.
    raw_evidence
        Optional raw_evidence dict (``state.metadata.raw_evidence``). Only
        used to detect whether announcements / research_report / news were
        collected — free text is never parsed.
    """
    topic = _resolve_topic(candidate)
    symbol = candidate.get("symbol", "")
    name = candidate.get("name", "")

    # ── Policy layer ──
    policy_level = POLICY_LEVEL_UNKNOWN
    topic_status = candidate.get("topic_lifecycle_state", "") or ""
    if topic_status:
        # map lifecycle → H-012 topic status lazily without importing circular
        from .topic_registry import _lifecycle_to_status
        topic_status = _lifecycle_to_status(topic_status)
    else:
        topic_status = candidate.get("topic_status", "") or TOPIC_STATUS_UNKNOWN

    if topic_entry is not None:
        policy_level = getattr(topic_entry, "policy_level", "") or POLICY_LEVEL_UNKNOWN
        if not topic_status or topic_status == TOPIC_STATUS_UNKNOWN:
            topic_status = getattr(topic_entry, "topic_status", "") or TOPIC_STATUS_UNKNOWN

    policy_titles = _collect_policy_titles(candidate, topic_entry)
    policy_evidence_count = len(policy_titles)

    # If no topic_entry policy level, infer from evidence refs source_level.
    if policy_level == POLICY_LEVEL_UNKNOWN:
        for ref in (candidate.get("policy_evidence_refs") or []):
            if isinstance(ref, dict):
                sl = ref.get("source_level", "")
                if sl in ("CENTRAL", "STATE_COUNCIL"):
                    policy_level = "CENTRAL"
                    break
                if sl == "MINISTRY" and policy_level == POLICY_LEVEL_UNKNOWN:
                    policy_level = "MINISTRY"
                elif sl == "LOCAL" and policy_level == POLICY_LEVEL_UNKNOWN:
                    policy_level = "LOCAL"

    policy_level_weight = _POLICY_LEVEL_WEIGHTS.get(policy_level, 0)
    topic_status_label = _TOPIC_STATUS_LABELS.get(topic_status, "未知")

    # ── Industry layer ──
    beneficiary_path = list(candidate.get("beneficiary_path") or [])
    chain_definition = get_industry_chain(topic)
    chain_segment_names = [link.segment for link in chain_definition]
    # Matched segments = intersection of beneficiary_path and defined chain.
    matched_segments = [s for s in beneficiary_path if s in chain_segment_names]
    if not matched_segments and topic_entry is not None:
        # fall back to registry-defined segments
        reg_segments = getattr(topic_entry, "chain_segments", None) or []
        matched_segments = [getattr(s, "name", str(s)) for s in reg_segments]

    industry_chain_role = "、".join(matched_segments) if matched_segments else (
        "、".join(beneficiary_path) if beneficiary_path else ""
    )

    # ── Company layer ──
    raw_company_role = candidate.get("company_role", "") or ""
    company_role = _normalize_company_role(raw_company_role)
    company_evidence_available = _detect_company_evidence_sources(raw_evidence)
    has_company_evidence = bool(company_evidence_available) or company_role not in (
        "", CompanyRole.UNKNOWN.value, CompanyRole.CONCEPT_ONLY.value,
    )
    # Display label: prefer the enum label; fall back to the raw descriptive
    # text (e.g. "动力系统") when it carries useful context the enum lacks.
    company_role_label = _company_role_label(company_role)
    if company_role == CompanyRole.UNKNOWN.value and raw_company_role:
        company_role_label = raw_company_role

    # ── Aggregated evidence titles ──
    evidence_titles = list(policy_titles)
    # Company-layer titles: prefer structured mandate_evidence_refs already
    # captured above; add a marker for raw company evidence availability.
    seen_titles = {t.title for t in evidence_titles}
    for key in company_evidence_available:
        label_map = {
            "announcements": "公告已采集",
            "research_report": "研报已采集",
            "news": "新闻已采集",
        }
        marker = label_map.get(key, key)
        if marker not in seen_titles:
            evidence_titles.append(EvidenceTitle(
                title=marker,
                source=key,
                date="",
                layer=LAYER_COMPANY,
                source_level="",
            ))
            seen_titles.add(marker)

    evidence_titles = evidence_titles[:_MAX_EVIDENCE_TITLES]

    # ── Missing evidence ──
    blocking_gaps = list(candidate.get("blocking_evidence_gaps") or [])
    missing = _build_missing_evidence(
        topic=topic,
        policy_titles=policy_titles,
        chain_segments=matched_segments,
        beneficiary_path=beneficiary_path,
        company_role=company_role,
        raw_company_role=raw_company_role,
        has_company_evidence=has_company_evidence,
        company_evidence_available=company_evidence_available,
        blocking_gaps=blocking_gaps,
    )

    # ── needs_manual_research ──
    policy_thin = (not topic) or (policy_evidence_count == 0)
    company_thin = company_role in ("", CompanyRole.UNKNOWN.value, CompanyRole.CONCEPT_ONLY.value) or not has_company_evidence
    needs_manual_research = policy_thin or company_thin

    confidence, confidence_reason = _assess_confidence(
        policy_titles_count=policy_evidence_count,
        policy_level_weight=policy_level_weight,
        chain_segments_count=len(matched_segments),
        company_role=company_role,
        has_company_evidence=has_company_evidence,
    )
    if needs_manual_research and confidence == CONFIDENCE_HIGH:
        confidence = CONFIDENCE_MEDIUM
        confidence_reason = "虽有部分证据，但存在关键缺口，建议人工研究"

    return MandateEvidencePacket(
        symbol=symbol,
        name=name,
        topic=topic,
        policy_theme=topic,
        policy_level=policy_level,
        policy_level_weight=policy_level_weight,
        topic_status=topic_status,
        topic_status_label=topic_status_label,
        policy_evidence_count=policy_evidence_count,
        industry_chain_role=industry_chain_role,
        industry_chain_segments=matched_segments,
        beneficiary_path=beneficiary_path,
        company_role=company_role,
        company_role_label=company_role_label,
        raw_company_role=raw_company_role,
        has_company_evidence=has_company_evidence,
        company_evidence_available=company_evidence_available,
        evidence_titles=evidence_titles,
        missing_evidence=missing,
        needs_manual_research=needs_manual_research,
        confidence=confidence,
        confidence_reason=confidence_reason,
    )


def _normalize_company_role(raw_role: str) -> str:
    """Normalize a free-text company role to a CompanyRole enum value.

    Candidate dicts sometimes carry Chinese role labels (e.g. ``核心供应商``)
    rather than the enum value. We map the common labels back to the enum so
    the packet stays consistent with H-003.
    """
    if not raw_role:
        return CompanyRole.UNKNOWN.value
    # Exact enum match.
    try:
        return CompanyRole(raw_role).value
    except ValueError:
        pass
    # Chinese / descriptive label mapping.
    role_map = {
        "龙头": CompanyRole.LEADER.value,
        "核心标的": CompanyRole.LEADER.value,
        "领军": CompanyRole.LEADER.value,
        "领导者": CompanyRole.LEADER.value,
        "核心供应商": CompanyRole.CORE_SUPPLIER.value,
        "供应商": CompanyRole.CORE_SUPPLIER.value,
        "主要供应商": CompanyRole.CORE_SUPPLIER.value,
        "基础设施": CompanyRole.INFRA_PROVIDER.value,
        "基础设施提供商": CompanyRole.INFRA_PROVIDER.value,
        "应用场景": CompanyRole.APPLICATION_SCENE.value,
        "系统集成": CompanyRole.APPLICATION_SCENE.value,
        "解决方案": CompanyRole.APPLICATION_SCENE.value,
        "外围": CompanyRole.PERIPHERAL.value,
        "配套": CompanyRole.PERIPHERAL.value,
        "外围配套": CompanyRole.PERIPHERAL.value,
        "概念": CompanyRole.CONCEPT_ONLY.value,
        "概念股": CompanyRole.CONCEPT_ONLY.value,
        "题材": CompanyRole.CONCEPT_ONLY.value,
    }
    if raw_role in role_map:
        return role_map[raw_role]
    # Substring fallback.
    for key, role in role_map.items():
        if key in raw_role:
            return role
    return CompanyRole.UNKNOWN.value


def build_evidence_packets_for_candidates(
    candidates: List[dict],
    *,
    topic_registry: Optional[Any] = None,
    raw_evidence_by_symbol: Optional[Dict[str, dict]] = None,
) -> Dict[str, MandateEvidencePacket]:
    """Build packets for a list of candidate dicts, keyed by symbol.

    ``topic_registry`` may be a :class:`TopicRegistry` instance; when provided
    the matching :class:`TopicRegistryEntry` is passed to
    :func:`build_evidence_packet` for richer policy / chain evidence.
    ``raw_evidence_by_symbol`` maps ``symbol → raw_evidence dict``.
    """
    packets: Dict[str, MandateEvidencePacket] = {}
    for cand in candidates:
        symbol = cand.get("symbol", "")
        if not symbol:
            continue
        topic_entry = None
        if topic_registry is not None:
            topic = _resolve_topic(cand)
            if topic:
                topic_entry = topic_registry.get(topic)
        raw_ev = None
        if raw_evidence_by_symbol:
            raw_ev = raw_evidence_by_symbol.get(symbol)
        packets[symbol] = build_evidence_packet(
            cand, topic_entry=topic_entry, raw_evidence=raw_ev,
        )
    return packets


def render_evidence_packet_summary(packet: MandateEvidencePacket) -> str:
    """Render a short, human-readable three-layer summary line.

    Never emits trade verbs. Suitable for the daily report / drawer tooltip.
    """
    parts: List[str] = []
    if packet.policy_theme:
        parts.append(
            f"政策[{packet.policy_theme}/{packet.topic_status_label}"
            f"/{packet.policy_evidence_count}条]"
        )
    else:
        parts.append("政策[未匹配]")
    if packet.industry_chain_role:
        parts.append(f"产业[{packet.industry_chain_role}]")
    else:
        parts.append("产业[未定位]")
    parts.append(f"公司[{packet.company_role_label}]")
    if packet.needs_manual_research:
        parts.append("需人工补证")
    return "｜".join(parts)
