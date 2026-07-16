"""Deterministic integrity checks for fundamental-analysis evidence.

The checks are deliberately conservative: they do not decide whether a company
is good, only whether the system has enough verified context to make the kinds
of causal claims a fundamental report normally makes.

FUND-003A: Claim-level evidence binding — each causal or accounting-policy
claim in the report is independently matched against official evidence.
A "合同负债" announcement cannot validate a "原材料下降" claim.
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
    "净额法", "总额法", "主要责任人", "代理人", "合同负债",
    "预收款", "预收货款", "预收客户货款", "设备经销",
)

# Canonical form mapping: variant surface forms → canonical keyword.
# Used so that "预收货款" in an announcement matches "预收款" in a report.
_ACCOUNTING_CANONICAL: dict[str, str] = {
    "预收货款": "预收款",
    "预收客户货款": "预收款",
}

_SEQ = 0


def _next_claim_id(prefix: str = "CL") -> str:
    global _SEQ
    _SEQ += 1
    return f"{prefix}-{_SEQ:04d}"


def _normalize_terms(terms: Iterable[str]) -> list[str]:
    """Map variant surface forms to canonical keywords.

    e.g. "预收货款" → "预收款" so that report text using "预收款" matches
    evidence containing "预收货款".
    """
    return sorted({_ACCOUNTING_CANONICAL.get(t, t) for t in terms})


def build_official_explanation_context(
    *,
    announcements: Any,
    half_year_facts: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return only source-backed explanation/accounting evidence.

    A missing announcement feed is normal absence of evidence, never a reason
    to invent a cause.  Half-year facts are accepted only when their source type
    is not opinion-only.

    Each entry now carries ``cause_terms`` and ``accounting_terms`` so that
    downstream claim-level binding can verify that a specific claim is backed
    by semantically matching evidence — not merely that *some* keyword appeared
    in *some* official text.
    """
    entries: list[dict[str, Any]] = []
    all_cause_terms: list[str] = []
    all_accounting_terms: list[str] = []
    idx = 0
    for raw, source in _official_texts(announcements, half_year_facts):
        matched_accounting = _normalize_terms(
            term for term in _ACCOUNTING_KEYWORDS if term in raw
        )
        matched_cause = [term for term in _CAUSE_KEYWORDS if term in raw]
        if matched_accounting or matched_cause:
            idx += 1
            entries.append({
                "evidence_id": f"E{idx:03d}",
                "source": source,
                "text": raw[:500],
                "cause_terms": sorted(set(matched_cause)),
                "accounting_terms": sorted(set(matched_accounting)),
            })
            all_cause_terms.extend(matched_cause)
            all_accounting_terms.extend(matched_accounting)
    return {
        "status": "officially_explained" if entries else "unexplained",
        "data_status": "HAS_DATA" if entries else "NORMAL_NO_DATA",
        "entries": entries,
        "cause_terms": sorted(set(all_cause_terms)),
        "accounting_policy_terms": sorted(set(all_accounting_terms)),
    }


def bind_claims(
    report_text: str,
    explanation_context: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Evaluate each causal / accounting claim in *report_text* individually.

    Returns a list of ``ClaimBinding`` dicts.  Each dict has:
      - claim_id:      stable id (for logging / audit)
      - metric:        the keyword that triggered the claim
      - cause_terms:   cause keywords found in the report (for cause claims)
      - policy_terms:  accounting keywords found in the report (for accounting claims)
      - evidence_ids:  list of evidence entry ids that match this claim
      - source_type:   "announcement" / "half_year_fact" / None
      - as_of:         trade date from explanation context
      - status:        "officially_explained" / "evidence_conflict" / "unexplained"

    A claim is "officially_explained" only when an evidence entry contains
    the *same* keyword (same metric, same semantic field).  If evidence exists
    but for different keywords, the status is "evidence_conflict".
    """
    ctx = explanation_context or {}
    entries = ctx.get("entries") or []
    as_of = ctx.get("as_of")

    evidence_cause: dict[str, list[str]] = {}
    evidence_accounting: dict[str, list[str]] = {}
    evidence_source: dict[str, str] = {}
    for entry in entries:
        eid = entry.get("evidence_id", "")
        evidence_cause[eid] = entry.get("cause_terms") or []
        evidence_accounting[eid] = entry.get("accounting_terms") or []
        evidence_source[eid] = entry.get("source", "unknown")

    text = report_text or ""
    claims: list[dict[str, Any]] = []

    for keyword in _CAUSE_KEYWORDS:
        if keyword not in text:
            continue
        supporting = [
            eid for eid, terms in evidence_cause.items() if keyword in terms
        ]
        if supporting:
            status = "officially_explained"
        elif entries:
            status = "evidence_conflict"
        else:
            status = "unexplained"
        src = evidence_source.get(supporting[0]) if supporting else None
        claims.append({
            "claim_id": _next_claim_id("CAUSE"),
            "metric": keyword,
            "cause_terms": [keyword],
            "policy_terms": [],
            "evidence_ids": supporting,
            "source_type": src,
            "as_of": as_of,
            "status": status,
        })

    seen_accounting: set[str] = set()
    for keyword in _ACCOUNTING_KEYWORDS:
        if keyword not in text:
            continue
        canonical = _ACCOUNTING_CANONICAL.get(keyword, keyword)
        if canonical in seen_accounting:
            continue
        seen_accounting.add(canonical)
        supporting = [
            eid for eid, terms in evidence_accounting.items() if canonical in terms
        ]
        if supporting:
            status = "officially_explained"
        elif entries:
            status = "evidence_conflict"
        else:
            status = "unexplained"
        src = evidence_source.get(supporting[0]) if supporting else None
        claims.append({
            "claim_id": _next_claim_id("ACCT"),
            "metric": canonical,
            "cause_terms": [],
            "policy_terms": [canonical],
            "evidence_ids": supporting,
            "source_type": src,
            "as_of": as_of,
            "status": status,
        })

    return claims


def evaluate_fundamental_integrity(
    *,
    identity: Mapping[str, Any] | None,
    period_facts: Iterable[Mapping[str, Any]] | None,
    explanation_context: Mapping[str, Any] | None,
    report_text: str = "",
) -> dict[str, Any]:
    """Fail closed when the report's factual foundations are unavailable.

    FUND-003A: Each causal/accounting claim is individually bound to evidence.
    A "合同负债" announcement cannot validate a "原材料下降" claim.
    """
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
    claims = bind_claims(text, explanation_context)

    unsupported_causes = [
        c for c in claims
        if c["claim_id"].startswith("CAUSE") and c["status"] != "officially_explained"
    ]
    if unsupported_causes:
        terms = [c["metric"] for c in unsupported_causes[:3]]
        blockers.append({
            "code": CAUSE_UNSUPPORTED,
            "reason": f"报告包含未经官方来源确认的因果解释：{'、'.join(terms)}",
        })

    unsupported_accounting = [
        c for c in claims
        if c["claim_id"].startswith("ACCT") and c["status"] != "officially_explained"
    ]
    if unsupported_accounting:
        terms = [c["metric"] for c in unsupported_accounting[:3]]
        blockers.append({
            "code": ACCOUNTING_POLICY_UNKNOWN,
            "reason": f"报告讨论会计口径但无官方证据：{'、'.join(terms)}",
        })

    return {
        "status": "NEEDS_REVIEW" if blockers else "VALID",
        "blockers": blockers,
        "is_valid": not blockers,
        "claims": claims,
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
