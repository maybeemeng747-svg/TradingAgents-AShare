"""Cross-source financial fact bundle for knowledge research.

The bundle is deliberately provider-neutral and contains no LLM output.  It
reuses TA's existing company-identity and financial-period contracts, then
compares two or more providers before a fact can be marked verified.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, Mapping, Protocol

from tradingagents.dataflows.financial_periods import (
    FinancialFact,
    derive_single_quarters,
    normalize_financial_markdown,
)
from tradingagents.dataflows.instrument_identity import (
    IDENTITY_CONFLICT,
    IDENTITY_HAS_DATA,
    IDENTITY_PARTIAL,
    build_instrument_identity,
    extract_profile_from_fundamentals,
)


BUNDLE_VERSION = "1.1.0"
HAS_DATA = "HAS_DATA"
NORMAL_NO_DATA = "NORMAL_NO_DATA"
QUERY_FAILED = "QUERY_FAILED"

VERIFIED_CROSS_SOURCE = "VERIFIED_CROSS_SOURCE"
SINGLE_SOURCE = "SINGLE_SOURCE"
CONFLICT = "CONFLICT"

DEFAULT_PROVIDER_NAMES = ("cn_astock", "cn_eastmoney_financial")
_DEFAULT_FINANCIAL_SOURCE_IDS = {
    # Both adapters currently read Sina's CompanyFinanceService for the three
    # statements. They are separate code paths, not independent data sources.
    "cn_akshare": "sina_finance",
    "cn_astock": "sina_finance",
    "cn_eastmoney_financial": "eastmoney_datacenter",
    "yfinance": "yahoo_finance",
    "alpha_vantage": "alpha_vantage",
}
_DEFAULT_IDENTITY_SOURCE_IDS = {
    "cn_akshare": "eastmoney",
    "cn_astock": "eastmoney",
    "cn_eastmoney_financial": "eastmoney",
    "yfinance": "yahoo_finance",
    "alpha_vantage": "alpha_vantage",
}
_SOURCE_VALUE_PRIORITY = {
    # Eastmoney exposes statement values to cents, while Sina commonly rounds
    # large values in its markdown table. Cross-source verification must not
    # invent a third value by averaging the two.
    "eastmoney_datacenter": 100,
    "sina_finance": 90,
}
_STATEMENT_METHODS = {
    "income_statement": "get_income_statement",
    "cashflow": "get_cashflow",
    "balance_sheet": "get_balance_sheet",
}
_FAILURE_MARKERS = (
    "unavailable",
    "获取失败",
    "暂不可用",
    "query failed",
    "request failed",
    "error:",
)
_NO_DATA_MARKERS = (
    "no income statement",
    "no income data",
    "no cashflow",
    "no balance sheet",
    "no balance data",
    "no fundamentals found",
)
_CREDENTIAL_NAME = (
    r"(?:[a-z0-9]+[_-])*"
    r"(?:authorization|api[_-]?key|apikey|access[_-]?token|refresh[_-]?token|"
    r"token|client[_-]?secret|secret|password|cookie|session(?:[_-]?id)?)"
)
_QUOTED_SECRET_RE = re.compile(
    rf"""(?ix)
    (["']?{_CREDENTIAL_NAME}["']?\s*[:=]\s*)
    (["'])(.*?)\2
    """
)
_SECRET_PARAM_RE = re.compile(
    rf"(?i)((?:[?&]|(?<![a-z0-9])){_CREDENTIAL_NAME}=)[^&\s,;]+"
)
_AUTH_HEADER_RE = re.compile(
    r"(?i)((?:proxy[_-]?)?authorization\s*:\s*(?:bearer|basic)\s+)\S+"
)
_UNQUOTED_SECRET_RE = re.compile(
    rf"""(?ix)
    ((?<![a-z0-9]){_CREDENTIAL_NAME}\s*[:=]\s*)
    (?:(?:bearer|basic)\s+)?[^,\s;}}]+
    """
)


class FinancialProvider(Protocol):
    name: str

    def get_fundamentals(self, ticker: str, curr_date: str | None = None) -> str: ...

    def get_income_statement(
        self, ticker: str, freq: str = "quarterly", curr_date: str | None = None
    ) -> str: ...

    def get_cashflow(
        self, ticker: str, freq: str = "quarterly", curr_date: str | None = None
    ) -> str: ...

    def get_balance_sheet(
        self, ticker: str, freq: str = "quarterly", curr_date: str | None = None
    ) -> str: ...


@dataclass(frozen=True)
class ProviderCapture:
    provider: str
    source_id: str
    identity_source_ids: tuple[str, ...]
    status: str
    identity: dict[str, Any]
    statements: dict[str, dict[str, Any]]
    facts: tuple[FinancialFact, ...]
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "source_id": self.source_id,
            "identity_source_ids": list(self.identity_source_ids),
            "status": self.status,
            "identity": self.identity,
            "statements": self.statements,
            "facts": [fact.to_dict() for fact in self.facts],
            "error": self.error,
        }


def _statement_payload_status(raw: Any) -> str | None:
    if raw is None:
        return QUERY_FAILED
    if not isinstance(raw, str):
        return QUERY_FAILED
    if not raw.strip():
        return NORMAL_NO_DATA
    lowered = raw.lower()
    if any(marker in lowered for marker in _FAILURE_MARKERS):
        return QUERY_FAILED
    if any(marker in lowered for marker in _NO_DATA_MARKERS):
        return NORMAL_NO_DATA
    return None


def _sanitize_error_text(value: Any) -> str:
    text = str(value or "")
    text = _QUOTED_SECRET_RE.sub(
        lambda match: (
            f"{match.group(1)}{match.group(2)}[REDACTED]{match.group(2)}"
        ),
        text,
    )
    text = _SECRET_PARAM_RE.sub(r"\1[REDACTED]", text)
    text = _AUTH_HEADER_RE.sub(r"\1[REDACTED]", text)
    text = _UNQUOTED_SECRET_RE.sub(r"\1[REDACTED]", text)
    return text[:500]


def _exception_message(exc: Exception) -> str:
    return _sanitize_error_text(f"{type(exc).__name__}: {exc}")


def _financial_source_id(provider: FinancialProvider) -> str:
    explicit = getattr(provider, "financial_source_id", None)
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    return _DEFAULT_FINANCIAL_SOURCE_IDS.get(provider.name, provider.name)


def _identity_source_ids(
    provider: FinancialProvider,
    fundamentals: str | None,
) -> tuple[str, ...]:
    explicit = getattr(provider, "identity_source_id", None)
    if isinstance(explicit, str) and explicit.strip():
        return (explicit.strip(),)
    if isinstance(explicit, (list, tuple, set)):
        values = tuple(sorted({str(value).strip() for value in explicit if str(value).strip()}))
        if values:
            return values
    if provider.name == "cn_akshare" and isinstance(fundamentals, str):
        if "Company Profile (巨潮资讯)" in fundamentals and "Company Profile\n" not in fundamentals:
            return ("cninfo",)
    return (_DEFAULT_IDENTITY_SOURCE_IDS.get(provider.name, provider.name),)


def _statement_capture(
    provider: FinancialProvider,
    method_name: str,
    statement_type: str,
    symbol: str,
    as_of: str,
    observed_at: str,
) -> tuple[dict[str, Any], list[FinancialFact]]:
    method = getattr(provider, method_name)
    try:
        raw = method(symbol, freq="quarterly", curr_date=as_of)
    except Exception as exc:  # provider boundary: preserve the real failure class
        return {
            "status": QUERY_FAILED,
            "error": _exception_message(exc),
        }, []

    payload_status = _statement_payload_status(raw)
    if payload_status is not None:
        return {
            "status": payload_status,
            "error": (
                _sanitize_error_text(raw.strip() if isinstance(raw, str) else raw)
                if payload_status == QUERY_FAILED
                else None
            ),
        }, []

    facts = normalize_financial_markdown(
        raw,
        statement_type=statement_type,
        source=provider.name,
        observed_at=observed_at,
    )
    if not facts:
        return {
            "status": QUERY_FAILED,
            "error": (
                f"nonempty provider payload could not be parsed as {statement_type}"
            ),
        }, []
    try:
        cutoff = date.fromisoformat(as_of)
    except ValueError:
        return {
            "status": QUERY_FAILED,
            "error": f"invalid as_of date: {as_of}",
        }, []
    visible_facts: list[FinancialFact] = []
    discarded_after_as_of = 0
    discarded_invalid_date = 0
    for fact in facts:
        try:
            report_date = date.fromisoformat(fact.report_date)
            disclosure_date = date.fromisoformat(fact.disclosure_date or "")
        except (TypeError, ValueError):
            discarded_invalid_date += 1
            continue
        if report_date > cutoff or disclosure_date > cutoff:
            discarded_after_as_of += 1
            continue
        visible_facts.append(fact)
    facts = visible_facts
    if not facts:
        return {
            "status": NORMAL_NO_DATA,
            "error": "provider payload had no eligible financial rows at as_of",
            "discarded_after_as_of": discarded_after_as_of,
            "discarded_invalid_date": discarded_invalid_date,
        }, []
    return {
        "status": HAS_DATA,
        "error": None,
        "discarded_after_as_of": discarded_after_as_of,
        "discarded_invalid_date": discarded_invalid_date,
    }, facts


def capture_provider(
    provider: FinancialProvider,
    *,
    symbol: str,
    as_of: str,
    observed_at: str,
) -> ProviderCapture:
    identity_error: str | None = None
    fundamentals: str | None = None
    historical_live_capture = date.fromisoformat(as_of) < date.fromisoformat(
        observed_at
    )
    if historical_live_capture:
        identity_error = (
            "live provider payloads were not captured at the requested historical "
            "as_of; use an archived immutable fact bundle"
        )
        identity_payload = {
            "status": QUERY_FAILED,
            "symbol": symbol.strip().upper(),
            "error": identity_error,
        }
    else:
        try:
            fundamentals = provider.get_fundamentals(symbol, curr_date=as_of)
            profile = extract_profile_from_fundamentals(fundamentals)
            identity_payload = build_instrument_identity(
                symbol,
                profile,
                source=provider.name,
                as_of=as_of,
            ).to_dict()
        except Exception as exc:
            identity_error = _exception_message(exc)
            identity_payload = {
                "status": QUERY_FAILED,
                "symbol": symbol.strip().upper(),
                "error": identity_error,
            }

    statements: dict[str, dict[str, Any]] = {}
    facts: list[FinancialFact] = []
    if historical_live_capture:
        statements = {
            statement_type: {
                "status": QUERY_FAILED,
                "error": identity_error,
            }
            for statement_type in _STATEMENT_METHODS
        }
    else:
        for statement_type, method_name in _STATEMENT_METHODS.items():
            statement, statement_facts = _statement_capture(
                provider,
                method_name,
                statement_type,
                symbol,
                as_of,
                observed_at,
            )
            statements[statement_type] = statement
            facts.extend(statement_facts)

    facts.extend(derive_single_quarters(facts))
    has_statement = any(item["status"] == HAS_DATA for item in statements.values())
    has_statement_failure = any(
        item["status"] == QUERY_FAILED for item in statements.values()
    )
    if identity_payload.get("status") == IDENTITY_CONFLICT:
        status = CONFLICT
    elif has_statement:
        status = HAS_DATA
    elif has_statement_failure:
        status = QUERY_FAILED
    else:
        status = NORMAL_NO_DATA
    return ProviderCapture(
        provider=provider.name,
        source_id=_financial_source_id(provider),
        identity_source_ids=_identity_source_ids(provider, fundamentals),
        status=status,
        identity=identity_payload,
        statements=statements,
        facts=tuple(facts),
        error=identity_error,
    )


def _fact_key(fact: FinancialFact) -> tuple[str, str, str, bool]:
    return (
        fact.metric,
        fact.report_date,
        fact.period_scope,
        fact.is_derived,
    )


def _values_agree(values: Iterable[float], *, relative_tolerance: float) -> bool:
    materialized = list(values)
    if len(materialized) < 2:
        return False
    high = max(materialized)
    low = min(materialized)
    scale = max(abs(high), abs(low), 1.0)
    return abs(high - low) / scale <= relative_tolerance


def reconcile_facts(
    captures: Iterable[ProviderCapture],
    *,
    relative_tolerance: float = 0.005,
) -> list[dict[str, Any]]:
    grouped: dict[
        tuple[str, str, str, bool],
        list[tuple[str, str, FinancialFact]],
    ] = {}
    for capture in captures:
        if capture.status not in {HAS_DATA, NORMAL_NO_DATA}:
            continue
        for fact in capture.facts:
            if fact.status != HAS_DATA or fact.value is None:
                continue
            grouped.setdefault(_fact_key(fact), []).append(
                (capture.provider, capture.source_id, fact)
            )

    reconciled: list[dict[str, Any]] = []
    for key in sorted(grouped):
        rows = grouped[key]
        providers = sorted({provider for provider, _source, _fact in rows})
        source_ids = sorted({source for _provider, source, _fact in rows})
        values_by_source: dict[str, list[float]] = {}
        for _provider, source, fact in rows:
            values_by_source.setdefault(source, []).append(float(fact.value))
        same_source_conflict = any(
            len(values) >= 2
            and not _values_agree(values, relative_tolerance=relative_tolerance)
            for values in values_by_source.values()
        )
        all_source_values = [
            value for values in values_by_source.values() for value in values
        ]
        representative = rows[0][2]
        disclosure_dates = [
            fact.disclosure_date
            for _provider, _source, fact in rows
            if fact.disclosure_date is not None
        ]
        if not disclosure_dates:
            continue
        disclosure_date = max(
            disclosure_dates
        )
        disclosure_date_inferred = any(
            fact.disclosure_date == disclosure_date
            and fact.disclosure_date_inferred
            for _provider, _source, fact in rows
        )
        if same_source_conflict:
            verification_status = CONFLICT
            value = None
            value_source_id = None
        elif len(source_ids) >= 2 and _values_agree(
            all_source_values, relative_tolerance=relative_tolerance
        ):
            verification_status = VERIFIED_CROSS_SOURCE
            selected_row = max(
                rows,
                key=lambda row: (
                    _SOURCE_VALUE_PRIORITY.get(row[1], 0),
                    row[1],
                    row[0],
                ),
            )
            value = float(selected_row[2].value)
            value_source_id = selected_row[1]
        elif len(source_ids) >= 2:
            verification_status = CONFLICT
            value = None
            value_source_id = None
        else:
            verification_status = SINGLE_SOURCE
            selected_row = max(rows, key=lambda row: (row[1], row[0]))
            value = float(selected_row[2].value)
            value_source_id = selected_row[1]

        reconciled.append(
            {
                "metric": representative.metric,
                "report_date": representative.report_date,
                # A reconciled fact becomes visible only when every source used
                # for that reconciliation was available.
                "disclosure_date": disclosure_date,
                "disclosure_date_inferred": disclosure_date_inferred,
                "period_scope": representative.period_scope,
                "value": round(value, 10) if value is not None else None,
                "unit": representative.unit,
                "is_derived": representative.is_derived,
                "formula": representative.formula,
                "verification_status": verification_status,
                "providers": providers,
                "source_ids": source_ids,
                "value_source_id": value_source_id,
                "provider_values": {
                    provider: fact.value for provider, _source, fact in rows
                },
            }
        )
    return reconciled


def _identity_consensus(captures: Iterable[ProviderCapture]) -> dict[str, Any]:
    materialized = list(captures)
    conflicted = [
        capture
        for capture in materialized
        if capture.identity.get("status") == IDENTITY_CONFLICT
    ]
    if conflicted:
        return {
            "status": CONFLICT,
            "providers": sorted(capture.provider for capture in conflicted),
            "reasons": sorted(
                {
                    str(capture.identity.get("conflict_reason") or "identity_conflict")
                    for capture in conflicted
                }
            ),
        }
    identities = [
        (capture.identity_source_ids, capture.identity)
        for capture in materialized
        if capture.identity.get("status") in {IDENTITY_HAS_DATA, IDENTITY_PARTIAL}
        and capture.identity.get("symbol")
        and capture.identity.get("security_name")
    ]
    if not identities:
        if any(
            capture.identity.get("status") == QUERY_FAILED
            for capture in materialized
        ):
            return {"status": QUERY_FAILED}
        return {"status": NORMAL_NO_DATA}

    symbols = {
        str(item.get("symbol") or "").strip().upper()
        for _sources, item in identities
        if item.get("symbol")
    }
    displayed_names = {
        str(item.get("security_name") or "").strip()
        for _sources, item in identities
        if item.get("security_name")
    }
    industries = {item.get("industry") for _sources, item in identities}
    source_ids = sorted(
        {
            source
            for sources, _item in identities
            for source in sources
        }
    )
    if len(symbols) != 1 or not _company_names_compatible(displayed_names):
        return {
            "status": CONFLICT,
            "symbols": sorted(str(value) for value in symbols),
            "security_names": sorted(displayed_names),
        }
    return {
        "status": (
            VERIFIED_CROSS_SOURCE if len(source_ids) >= 2 else SINGLE_SOURCE
        ),
        "symbol": next(iter(symbols)),
        "security_name": sorted(displayed_names, key=len)[0],
        "industry": next(iter(industries)) if len(industries) == 1 else None,
        "providers": sorted(item["source"] for _sources, item in identities),
        "source_ids": source_ids,
    }


def _normalized_company_name(value: str) -> str:
    normalized = re.sub(r"[\s·・,，.。()（）\-—_]+", "", value or "")
    for suffix in (
        "集团股份有限公司",
        "股份有限公司",
        "集团有限公司",
        "有限责任公司",
        "有限公司",
    ):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            break
    return normalized


_LEGAL_LOCATION_PREFIXES = tuple(
    sorted(
        {
            "北京市",
            "天津市",
            "上海市",
            "重庆市",
            "河北省",
            "山西省",
            "辽宁省",
            "吉林省",
            "黑龙江省",
            "江苏省",
            "浙江省",
            "安徽省",
            "福建省",
            "江西省",
            "山东省",
            "河南省",
            "湖北省",
            "湖南省",
            "广东省",
            "海南省",
            "四川省",
            "贵州省",
            "云南省",
            "陕西省",
            "甘肃省",
            "青海省",
            "内蒙古自治区",
            "广西壮族自治区",
            "西藏自治区",
            "宁夏回族自治区",
            "新疆维吾尔自治区",
            "北京",
            "天津",
            "上海",
            "重庆",
            "河北",
            "山西",
            "辽宁",
            "吉林",
            "黑龙江",
            "江苏",
            "浙江",
            "安徽",
            "福建",
            "江西",
            "山东",
            "河南",
            "湖北",
            "湖南",
            "广东",
            "海南",
            "四川",
            "贵州",
            "云南",
            "陕西",
            "甘肃",
            "青海",
            "内蒙古",
            "广西",
            "西藏",
            "宁夏",
            "新疆",
        },
        key=len,
        reverse=True,
    )
)


def _company_name_key(value: str) -> str:
    normalized = _normalized_company_name(value)
    for prefix in _LEGAL_LOCATION_PREFIXES:
        if normalized.startswith(prefix) and len(normalized) - len(prefix) >= 3:
            return normalized[len(prefix) :]
    return normalized


def _company_names_compatible(names: Iterable[str]) -> bool:
    normalized = {
        _company_name_key(name)
        for name in names
        if _company_name_key(name)
    }
    return len(normalized) <= 1


def _canonical_a_share_symbol(symbol: str) -> str:
    normalized = str(symbol or "").strip().upper()
    parts = normalized.split(".", 1)
    bare = parts[0]
    if len(bare) != 6 or not bare.isdigit():
        raise ValueError(f"invalid A-share symbol: {symbol}")
    suffix = (
        "SH"
        if bare.startswith("6")
        else "SZ"
        if bare.startswith(("0", "3"))
        else "BJ"
        if bare.startswith(("4", "8", "92"))
        else None
    )
    if suffix is None:
        raise ValueError(f"unsupported A-share symbol: {symbol}")
    if len(parts) == 2 and parts[1] != suffix:
        raise ValueError(
            f"A-share symbol exchange suffix mismatch: {symbol}; expected .{suffix}"
        )
    return f"{bare}.{suffix}"


def build_financial_fact_bundle(
    *,
    symbol: str,
    as_of: str,
    providers: Iterable[FinancialProvider],
    generated_at: str | None = None,
    relative_tolerance: float = 0.005,
    observed_at: str | None = None,
) -> dict[str, Any]:
    canonical_symbol = _canonical_a_share_symbol(symbol)
    as_of_date = date.fromisoformat(as_of)
    now = datetime.now().astimezone()
    collection_time = generated_at or now.isoformat()
    # generated_at is output metadata, not proof that a provider payload was
    # visible then. Only an explicit observed_at may override today's date.
    collection_date = observed_at or now.date().isoformat()
    observed_date = date.fromisoformat(collection_date)
    if as_of_date > observed_date:
        raise ValueError(
            f"future as_of is not valid for a live capture: {as_of} > "
            f"{collection_date}"
        )
    captures = [
        capture_provider(
            provider,
            symbol=canonical_symbol,
            as_of=as_of,
            observed_at=collection_date,
        )
        for provider in providers
    ]
    facts = reconcile_facts(captures, relative_tolerance=relative_tolerance)
    identity = _identity_consensus(captures)
    verified_count = sum(
        fact["verification_status"] == VERIFIED_CROSS_SOURCE for fact in facts
    )
    conflict_count = sum(fact["verification_status"] == CONFLICT for fact in facts)
    if (
        any(capture.status == CONFLICT for capture in captures)
        or identity.get("status") == CONFLICT
        or conflict_count
    ):
        status = CONFLICT
    elif verified_count:
        status = HAS_DATA
    elif facts:
        status = SINGLE_SOURCE
    elif any(capture.status == QUERY_FAILED for capture in captures):
        status = QUERY_FAILED
    else:
        status = NORMAL_NO_DATA

    return {
        "bundle_version": BUNDLE_VERSION,
        "symbol": canonical_symbol,
        "as_of": as_of,
        "generated_at": collection_time,
        "observed_at": collection_date,
        "status": status,
        "identity": identity,
        "providers": [capture.to_dict() for capture in captures],
        "facts": facts,
        "summary": {
            "verified_cross_source": verified_count,
            "single_source": sum(
                fact["verification_status"] == SINGLE_SOURCE for fact in facts
            ),
            "conflicts": conflict_count,
        },
        "limitations": [
            "Structured provider data verifies reported numeric fields, not management explanations.",
            "Original filing text is still required for change drivers, accounting policy, and segment interpretation.",
        ],
    }
