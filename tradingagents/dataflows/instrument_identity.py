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
    """
    if not isinstance(raw, str) or "### Company Profile" not in raw:
        return {}
    section = raw.split("### Company Profile", 1)[1]
    section = section.split("### ", 1)[0]
    values: dict[str, str] = {}
    aliases = {
        "security_name": ("股票简称", "公司名称", "名称"),
        "symbol": ("股票代码", "证券代码", "代码"),
        "main_business": ("主营业务", "经营范围", "主营"),
        "industry": ("所属行业", "行业"),
    }
    for line in section.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 2 or all(set(cell) <= {"-", ":", " "} for cell in cells):
            continue
        key, value = cells[0], cells[1]
        for target, labels in aliases.items():
            if key in labels and value and value.lower() not in {"nan", "none", "-"}:
                values.setdefault(target, value)
    return values


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
