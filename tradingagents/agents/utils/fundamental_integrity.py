"""Deterministic integrity checks for fundamental-analysis evidence.

The checks are deliberately conservative: they do not decide whether a company
is good, only whether the system has enough verified context to make the kinds
of causal claims a fundamental report normally makes.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping


IDENTITY_UNVERIFIED = "IDENTITY_UNVERIFIED"
PERIOD_SCOPE_INVALID = "PERIOD_SCOPE_INVALID"
DERIVATION_CONFLICT = "DERIVATION_CONFLICT"
CAUSE_UNSUPPORTED = "CAUSE_UNSUPPORTED"
ACCOUNTING_POLICY_UNKNOWN = "ACCOUNTING_POLICY_UNKNOWN"

_CAUSE_KEYWORDS = (
    "原材料", "淡季", "旺季", "产品涨价", "销量提升", "成本下降",
    "行业周期", "集中交付", "预收款驱动",
)
_ACCOUNTING_KEYWORDS = (
    "净额法", "总额法", "主要责任人", "代理人", "合同负债", "预收款", "设备经销",
)


def build_official_explanation_context(
    *,
    announcements: Any,
    half_year_facts: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return only source-backed explanation/accounting evidence.

    A missing announcement feed is normal absence of evidence, never a reason
    to invent a cause.  Half-year facts are accepted only when their source type
    is not opinion-only.
    """
    entries: list[dict[str, str]] = []
    policy_terms: list[str] = []
    for raw, source in _official_texts(announcements, half_year_facts):
        matched_policy = [term for term in _ACCOUNTING_KEYWORDS if term in raw]
        matched_cause = [term for term in _CAUSE_KEYWORDS if term in raw]
        if matched_policy or matched_cause:
            entries.append({"source": source, "text": raw[:500]})
            policy_terms.extend(matched_policy)
    return {
        "status": "officially_explained" if entries else "unexplained",
        "entries": entries,
        "accounting_policy_terms": sorted(set(policy_terms)),
    }


def evaluate_fundamental_integrity(
    *,
    identity: Mapping[str, Any] | None,
    period_facts: Iterable[Mapping[str, Any]] | None,
    explanation_context: Mapping[str, Any] | None,
    report_text: str = "",
) -> dict[str, Any]:
    """Fail closed when the report's factual foundations are unavailable."""
    blockers: list[dict[str, str]] = []
    identity = identity or {}
    facts = list(period_facts or [])
    explanation_context = explanation_context or {}

    if not identity.get("commercial_analysis_allowed"):
        blockers.append({
            "code": IDENTITY_UNVERIFIED,
            "reason": "公司名称、主营或行业未由权威公司画像完整验证",
        })
    if not facts:
        blockers.append({
            "code": PERIOD_SCOPE_INVALID,
            "reason": "未获得可结构化的财务报告期，不能判断累计值与单季度值",
        })
    elif any(str(item.get("status")) == "FIELD_MISSING" for item in facts):
        blockers.append({
            "code": DERIVATION_CONFLICT,
            "reason": "单季度派生缺少累计期输入，禁止以全年累计值替代",
        })

    text = report_text or ""
    unsupported = [keyword for keyword in _CAUSE_KEYWORDS if keyword in text]
    if unsupported and explanation_context.get("status") != "officially_explained":
        blockers.append({
            "code": CAUSE_UNSUPPORTED,
            "reason": f"报告包含未经官方来源确认的因果解释：{'、'.join(unsupported[:3])}",
        })
    policy_claim = any(keyword in text for keyword in _ACCOUNTING_KEYWORDS)
    if policy_claim and not explanation_context.get("accounting_policy_terms"):
        blockers.append({
            "code": ACCOUNTING_POLICY_UNKNOWN,
            "reason": "报告讨论会计口径但无官方净额法/总额法或合同负债证据",
        })

    return {
        "status": "NEEDS_REVIEW" if blockers else "VALID",
        "blockers": blockers,
        "is_valid": not blockers,
    }


def extract_financial_anomaly_inputs(
    facts: Iterable[Mapping[str, Any]] | None,
) -> dict[str, float | None]:
    """Build C-006 arguments from verified, same-date structured facts."""
    latest: dict[str, Mapping[str, Any]] = {}
    previous: dict[str, Mapping[str, Any]] = {}
    for item in sorted(
        (item for item in facts or [] if item.get("value") is not None),
        key=lambda item: str(item.get("report_date") or ""),
    ):
        metric = str(item.get("metric") or "")
        if metric:
            if metric in latest:
                previous[metric] = latest[metric]
            latest[metric] = item

    def value(metric: str) -> float | None:
        raw = latest.get(metric, {}).get("value")
        return float(raw) if isinstance(raw, (int, float)) else None

    def previous_value(metric: str) -> float | None:
        raw = previous.get(metric, {}).get("value")
        return float(raw) if isinstance(raw, (int, float)) else None

    revenue, cost = value("revenue"), value("operating_cost")
    prior_revenue, prior_cost = previous_value("revenue"), previous_value("operating_cost")
    gross_margin = ((revenue - cost) / revenue * 100) if revenue not in (None, 0) and cost is not None else None
    gross_margin_prev = (
        (prior_revenue - prior_cost) / prior_revenue * 100
        if prior_revenue not in (None, 0) and prior_cost is not None else None
    )
    assets, liabilities = value("total_assets"), value("total_liabilities")
    prior_assets, prior_liabilities = previous_value("total_assets"), previous_value("total_liabilities")
    debt_ratio = liabilities / assets * 100 if assets not in (None, 0) and liabilities is not None else None
    debt_ratio_prev = (
        prior_liabilities / prior_assets * 100
        if prior_assets not in (None, 0) and prior_liabilities is not None else None
    )
    return {
        "gross_margin": gross_margin,
        "gross_margin_prev": gross_margin_prev,
        "operating_cashflow": _to_yi(latest.get("operating_cashflow")),
        "net_profit": _to_yi(latest.get("net_profit")),
        "debt_ratio": debt_ratio,
        "debt_ratio_prev": debt_ratio_prev,
        "total_invest_cashflow": _to_yi(latest.get("investing_cashflow")),
        "total_finance_cashflow": _to_yi(latest.get("financing_cashflow")),
    }


def _to_yi(fact: Mapping[str, Any] | None) -> float | None:
    """Convert a structured cash/profit fact to the C-006 unit (亿元)."""
    if not fact or not isinstance(fact.get("value"), (int, float)):
        return None
    unit = str(fact.get("unit") or "元")
    value = float(fact["value"])
    if unit in {"亿", "亿元"}:
        return value
    if unit in {"万", "万元"}:
        return value / 10000.0
    return value / 100000000.0


def format_fundamental_integrity_block(integrity: Mapping[str, Any]) -> str:
    blockers = integrity.get("blockers") or []
    if not blockers:
        return ""
    lines = ["【基本面语义门禁：NEEDS_REVIEW】"]
    for blocker in blockers:
        lines.append(f"- {blocker.get('code')}: {blocker.get('reason')}")
    lines.append("基本面模块已降级为待人工复核，不得作为可执行交易结论的依据。")
    return "\n".join(lines)


def _official_texts(
    announcements: Any,
    half_year_facts: Mapping[str, Any] | None,
) -> Iterable[tuple[str, str]]:
    if isinstance(announcements, str):
        value = announcements.strip()
        if value and "No announcements found" not in value and "无公告" not in value:
            yield value, "announcement"
    half_year_facts = half_year_facts or {}
    if str(half_year_facts.get("status") or "") not in {"HAS_FACTS", "HAS_DATA"}:
        return
    for page in half_year_facts.get("pages") or []:
        if not isinstance(page, Mapping) or page.get("is_opinion_only"):
            continue
        for key in ("management_commentary", "financial_facts", "segment_facts"):
            for entry in page.get(key) or []:
                if isinstance(entry, Mapping):
                    entry = entry.get("raw") or entry.get("text") or ""
                if entry:
                    yield str(entry), "half_year_fact"
