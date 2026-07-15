"""Deterministic instrument identity contract for fundamental analysis.

This module intentionally does not fetch data.  It turns a provider's company
profile payload into a small, auditable contract so an LLM never has to infer a
company's business from financial ratios alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping


IDENTITY_HAS_DATA = "HAS_DATA"
IDENTITY_PARTIAL = "PARTIAL"
IDENTITY_MISSING = "MISSING"
IDENTITY_CONFLICT = "CONFLICT"


def infer_exchange(symbol: str) -> str:
    normalized = (symbol or "").strip().upper()
    if normalized.endswith(".SH"):
        return "SSE"
    if normalized.endswith(".SZ"):
        return "SZSE"
    if normalized.endswith(".BJ"):
        return "BSE"
    if normalized.endswith(".HK"):
        return "HKEX"
    return "UNKNOWN"


@dataclass(frozen=True)
class InstrumentIdentity:
    """A provider-backed identity record, never a model-generated profile."""

    symbol: str
    security_name: str | None
    exchange: str
    main_business: str | None
    industry: str | None
    source: str
    status: str
    as_of: str | None = None
    source_symbol: str | None = None
    missing_fields: tuple[str, ...] = field(default_factory=tuple)
    conflict_reason: str | None = None

    @property
    def commercial_analysis_allowed(self) -> bool:
        return (
            self.status == IDENTITY_HAS_DATA
            and bool(self.security_name)
            and bool(self.main_business)
            and bool(self.industry)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "security_name": self.security_name,
            "exchange": self.exchange,
            "main_business": self.main_business,
            "industry": self.industry,
            "source": self.source,
            "status": self.status,
            "as_of": self.as_of,
            "source_symbol": self.source_symbol,
            "missing_fields": list(self.missing_fields),
            "conflict_reason": self.conflict_reason,
            "commercial_analysis_allowed": self.commercial_analysis_allowed,
        }


def build_instrument_identity(
    symbol: str,
    profile: Mapping[str, Any] | None = None,
    *,
    source: str = "akshare_company_profile",
    as_of: str | None = None,
) -> InstrumentIdentity:
    """Build an identity contract from structured profile fields.

    ``profile`` is deliberately small and provider-neutral.  Unknown fields are
    ignored; a missing profile is represented as missing, not inferred.
    """
    normalized = (symbol or "").strip().upper()
    profile = profile or {}

    # Cross-source conflict: different provider sections returned different codes
    if profile.get("_cross_source_conflict"):
        return InstrumentIdentity(
            symbol=normalized,
            security_name=_first(profile, "security_name", "name", "company_name", "股票简称", "公司名称"),
            exchange=infer_exchange(normalized),
            main_business=_first(profile, "main_business", "business", "主营业务", "经营范围"),
            industry=_first(profile, "industry", "所属行业", "行业"),
            source=source,
            status=IDENTITY_CONFLICT,
            as_of=as_of,
            conflict_reason="cross_source_code_mismatch",
        )

    source_symbol = _clean(profile.get("symbol") or profile.get("security_code"))
    name = _first(profile, "security_name", "name", "company_name", "股票简称", "公司名称")
    main_business = _first(profile, "main_business", "business", "主营业务", "经营范围")
    industry = _first(profile, "industry", "所属行业", "行业")
    exchange = _clean(profile.get("exchange")) or infer_exchange(normalized)

    if source_symbol and _normalize_code(source_symbol) != _normalize_code(normalized):
        return InstrumentIdentity(
            symbol=normalized,
            security_name=name,
            exchange=exchange,
            main_business=main_business,
            industry=industry,
            source=source,
            status=IDENTITY_CONFLICT,
            as_of=as_of,
            source_symbol=source_symbol,
            conflict_reason="profile_symbol_mismatch",
        )

    missing = tuple(
        field_name
        for field_name, value in (
            ("security_name", name),
            ("main_business", main_business),
            ("industry", industry),
        )
        if not value
    )
    status = IDENTITY_HAS_DATA if not missing else (IDENTITY_PARTIAL if profile else IDENTITY_MISSING)
    return InstrumentIdentity(
        symbol=normalized,
        security_name=name,
        exchange=exchange,
        main_business=main_business,
        industry=industry,
        source=source,
        status=status,
        as_of=as_of,
        source_symbol=source_symbol,
        missing_fields=missing,
    )


def extract_profile_from_fundamentals(raw: Any) -> dict[str, str]:
    """Extract only labelled profile fields from existing provider markdown.

    Financial Abstract is intentionally ignored.  If the provider did not
    return a ``Company Profile`` section, this returns an empty mapping.

    Supports two formats:
    - cn_akshare: markdown table with ``| item | value |`` rows
    - cn_astock:  bullet list with ``- **字段**: 值`` rows

    Also handles ``### Company Profile (巨潮资讯)`` fallback sections
    appended by cninfo provider fallback.
    """
    if not isinstance(raw, str) or "### Company Profile" not in raw:
        return {}
    # Collect ALL sections that start with "### Company Profile"
    # (handles both "### Company Profile" and "### Company Profile (巨潮资讯)")
    sections: list[str] = []
    idx = 0
    while True:
        pos = raw.find("### Company Profile", idx)
        if pos == -1:
            break
        # Skip past the header line
        line_end = raw.find("\n", pos)
        section_start = line_end + 1 if line_end != -1 else pos + len("### Company Profile")
        # Find the next ### header (any level)
        next_header = raw.find("\n### ", section_start)
        if next_header == -1:
            sections.append(raw[section_start:])
        else:
            sections.append(raw[section_start:next_header])
        idx = section_start + 1
    aliases = {
        "security_name": ("股票简称", "公司名称", "名称"),
        "symbol": ("股票代码", "证券代码", "代码"),
        "main_business": ("主营业务", "经营范围", "主营"),
        "industry": ("所属行业", "行业"),
    }
    list_pattern = re.compile(r"^-\s+\*\*(.+?)\*\*[:：]\s*(.+)$")

    def store_field(fields: dict[str, str], target: str, key: str, value: str) -> None:
        cleaned = _clean(value)
        if not cleaned:
            return
        if target == "main_business":
            # 经营范围 is deliberately a fallback.  A later, more precise
            # 主营业务 field must replace it regardless of provider row order.
            if key in {"主营业务", "主营"} or target not in fields:
                fields[target] = cleaned
                if key in {"主营业务", "主营"}:
                    fields["_main_business_precise"] = "True"
            return
        if target == "security_name":
            if key == "股票简称" or target not in fields:
                fields[target] = cleaned
                if key == "股票简称":
                    fields["_security_name_short"] = "True"
            return
        fields.setdefault(target, cleaned)

    # Parse each section independently
    parsed_sections: list[dict[str, str]] = []
    for section in sections:
        section_fields: dict[str, str] = {}
        for line in section.splitlines():
            stripped = line.strip()
            m = list_pattern.match(stripped)
            if m:
                key, value = m.group(1).strip(), m.group(2).strip()
                for target, labels in aliases.items():
                    if key in labels:
                        store_field(section_fields, target, key, value)
                continue
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if len(cells) < 2 or all(set(cell) <= {"-", ":", " "} for cell in cells):
                continue
            key, value = cells[0], cells[1]
            for target, labels in aliases.items():
                if key in labels:
                    store_field(section_fields, target, key, value)
        if section_fields:
            parsed_sections.append(section_fields)

    if not parsed_sections:
        return {}
    if len(parsed_sections) == 1:
        return {
            key: value
            for key, value in parsed_sections[0].items()
            if not key.startswith("_")
        }

    # Cross-source symbol code conflict detection.  Compare every non-empty
    # code so a code-less first source cannot hide disagreement between later
    # provider sections.
    source_codes: set[str] = set()
    for section_fields in parsed_sections:
        match = re.search(r"(?<!\d)(\d{6})(?!\d)", section_fields.get("symbol", ""))
        if match:
            source_codes.add(match.group(1))
    if len(source_codes) > 1:
        # Conflict: return first section's fields only.  In particular, do not
        # import business/industry fields from a mismatched provider.
        result = {
            key: value
            for key, value in parsed_sections[0].items()
            if not key.startswith("_")
        }
        result["_cross_source_conflict"] = "True"
        return result

    # No conflict: merge sections while preserving semantic field quality.
    # A precise 主营业务 beats 经营范围, and a 股票简称 beats 公司名称,
    # regardless of source order.
    merged: dict[str, str] = {}
    main_business_precise = False
    security_name_short = False
    for section_fields in parsed_sections:
        for key, value in section_fields.items():
            if key.startswith("_"):
                continue
            if key == "main_business":
                section_precise = section_fields.get("_main_business_precise") == "True"
                if key not in merged or (section_precise and not main_business_precise):
                    merged[key] = value
                    main_business_precise = section_precise
                continue
            if key == "security_name":
                section_short = section_fields.get("_security_name_short") == "True"
                if key not in merged or (section_short and not security_name_short):
                    merged[key] = value
                    security_name_short = section_short
                continue
            merged.setdefault(key, value)
    return merged


def render_identity_context(identity: Mapping[str, Any] | None) -> str:
    data = identity or {}
    allowed = bool(data.get("commercial_analysis_allowed"))
    lines = [
        "【公司身份契约】",
        f"状态：{data.get('status', IDENTITY_MISSING)}",
        f"代码：{data.get('symbol', '—')}",
        f"名称：{data.get('security_name') or '未验证'}",
        f"交易所：{data.get('exchange', 'UNKNOWN')}",
        f"主营：{data.get('main_business') or '未验证'}",
        f"行业：{data.get('industry') or '未验证'}",
        f"来源：{data.get('source', '—')}",
    ]
    if not allowed:
        lines.append(
            "硬约束：身份/主营/行业未完整验证。仅陈述有来源的财务事实；"
            "禁止推断商业模式、行业周期、竞争格局或财务异动原因。"
        )
    return "\n".join(lines)


def _first(profile: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = _clean(profile.get(key))
        if value:
            return value
    return None


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null", "-", "—"}:
        return None
    return text


def _normalize_code(value: str) -> str:
    match = re.search(r"(\d{6})", value or "")
    return match.group(1) if match else (value or "").strip().upper()
