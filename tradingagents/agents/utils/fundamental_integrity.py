"""Deterministic integrity checks for fundamental-analysis evidence.

FUND-003A-B: Per-term evidence binding — direction, negation, and causal
relation are extracted per keyword (not per entry), preventing multi-indicator
misbinding when an entry contains multiple terms.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable, Mapping


IDENTITY_UNVERIFIED = "IDENTITY_UNVERIFIED"
PERIOD_SCOPE_INVALID = "PERIOD_SCOPE_INVALID"
DERIVATION_CONFLICT = "DERIVATION_CONFLICT"
CAUSE_UNSUPPORTED = "CAUSE_UNSUPPORTED"
ACCOUNTING_POLICY_UNKNOWN = "ACCOUNTING_POLICY_UNKNOWN"

_CAUSE_KEYWORDS = (
    "原材料", "淡季", "旺季", "产品涨价", "销量提升", "成本下降",
    "行业周期", "集中交付",
    "原材料下降", "原材料成本下降", "采购成本下降",
    "原材料上涨", "原材料成本上涨", "采购成本上涨",
)
_ACCOUNTING_KEYWORDS = (
    "净额法", "总额法", "主要责任人", "代理人", "合同负债",
    "预收款", "预收货款", "预收客户货款", "设备经销", "预收款驱动",
)

_ACCOUNTING_CANONICAL: dict[str, str] = {
    "预收货款": "预收款",
    "预收客户货款": "预收款",
    "预收客户款": "预收款",
    "预收款驱动": "预收款",
    "合同负债驱动": "预收款",
}

_CAUSE_CANONICAL: dict[str, str] = {
    "原材料": "原材料下降",
    "原材料成本下降": "原材料下降",
    "采购成本下降": "原材料下降",
    "原材料成本上涨": "原材料上涨",
    "采购成本上涨": "原材料上涨",
}

_SYNONYM_GROUPS: list[tuple[str, ...]] = [
    ("预收款", "预收货款", "预收客户货款", "预收客户款", "合同负债驱动", "预收款驱动"),
    ("原材料下降", "原材料成本下降", "采购成本下降"),
    ("原材料上涨", "原材料成本上涨", "采购成本上涨"),
]

_SYNONYM_MAP: dict[str, str] = {}
for _group in _SYNONYM_GROUPS:
    _canonical = _group[0]
    for _term in _group:
        _SYNONYM_MAP[_term] = _canonical

_UP_WORDS = {"上涨", "上升", "增加", "增长", "提升", "走高", "攀升", "升高", "扩大", "增多"}
_DOWN_WORDS = {"下降", "降低", "减少", "回落", "走低", "收缩", "缩小", "下滑", "下跌", "缩减"}
_NEGATION_WORDS = {"未", "不", "没有", "并非", "未见", "尚未", "未曾"}

_NEGATION_PATTERN = re.compile(
    r"(未|不|没有|并非|未见|尚未|未曾)\s*(?:采用|使用|执行|确认|实行)?\s*(净额法|总额法|主要责任人|代理人)",
)

_RELATION_KEYWORDS = {"由于", "因为", "因此", "所以", "导致", "推动", "驱动", "主要系", "所致"}


def _claim_id_for(prefix: str, content: str, occurrence: int = 0) -> str:
    normalized = content.strip()
    suffix = f"@{occurrence}" if occurrence > 0 else ""
    digest = hashlib.sha256(f"{normalized}{suffix}".encode("utf-8")).hexdigest()[:8]
    return f"{prefix}-{digest}"


def _canonicalize_term(term: str) -> str:
    canonical = _ACCOUNTING_CANONICAL.get(term, term)
    canonical = _CAUSE_CANONICAL.get(canonical, canonical)
    canonical = _SYNONYM_MAP.get(canonical, canonical)
    return canonical


def _normalize_terms(terms: Iterable[str]) -> list[str]:
    result: set[str] = set()
    for t in terms:
        result.add(_canonicalize_term(t))
    return sorted(result)


def _extract_direction_at(text: str, keyword: str, pos: int) -> str | None:
    keyword_end = pos + len(keyword)
    best_dir = None
    best_dist = len(text)
    for w in _UP_WORDS:
        w_idx = text.find(w)
        while w_idx >= 0:
            dist = min(abs(w_idx - pos), abs(w_idx - keyword_end))
            if dist < best_dist:
                best_dist = dist
                best_dir = "up"
            w_idx = text.find(w, w_idx + 1)
    for w in _DOWN_WORDS:
        w_idx = text.find(w)
        while w_idx >= 0:
            dist = min(abs(w_idx - pos), abs(w_idx - keyword_end))
            if dist < best_dist:
                best_dist = dist
                best_dir = "down"
            w_idx = text.find(w, w_idx + 1)
    if best_dist <= 15:
        return best_dir
    return None


def _is_negated_at(text: str, keyword: str, pos: int) -> bool:
    for m in _NEGATION_PATTERN.finditer(text):
        if keyword in m.group(0) and abs(m.start() - pos) <= len(keyword) + 3:
            return True
    window_start = max(0, pos - 6)
    window = text[window_start:pos]
    for neg in _NEGATION_WORDS:
        if neg in window:
            return True
    return False


def _is_negated(text: str, keyword: str) -> bool:
    for m in _NEGATION_PATTERN.finditer(text):
        if keyword in m.group(0):
            return True
    idx = text.find(keyword)
    if idx < 0:
        return False
    window_start = max(0, idx - 6)
    window = text[window_start:idx]
    for neg in _NEGATION_WORDS:
        if neg in window:
            return True
    return False


def _find_all_occurrences(text: str, keyword: str) -> list[int]:
    positions: list[int] = []
    start = 0
    while True:
        idx = text.find(keyword, start)
        if idx < 0:
            break
        positions.append(idx)
        start = idx + 1
    return positions


def _directions_conflict(ev_dir: str | None, claim_dir: str | None) -> bool:
    if ev_dir is None or claim_dir is None:
        return False
    return ev_dir != claim_dir


def _extract_context_around(text: str, keyword: str, margin: int = 200) -> str:
    idx = text.find(keyword)
    if idx < 0:
        return text[:500]
    start = max(0, idx - margin)
    end = min(len(text), idx + len(keyword) + margin)
    return text[start:end]


def _evidence_text_for(raw: str, keywords: list[str]) -> str:
    for kw in keywords:
        if kw in raw:
            return _extract_context_around(raw, kw)
    return raw[:500]


def _scan_term_occurrences(
    raw: str,
    terms: list[str],
    *,
    is_cause: bool,
) -> dict[str, dict[str, Any]]:
    """Scan every occurrence of each term and return per-canonical-term
    direction, negation, and relation.  Conflicting occurrences → fail closed."""
    raw_occurrences: dict[str, list[tuple[str | None, bool, bool]]] = {}
    for term in terms:
        canonical = _canonicalize_term(term)
        if canonical not in raw_occurrences:
            raw_occurrences[canonical] = []
        start = 0
        while True:
            idx = raw.find(term, start)
            if idx < 0:
                break
            end = idx + len(term)
            direction = _extract_direction_at(raw, term, idx)
            negated = _is_negated_at(raw, term, idx)
            rel_win = raw[max(0, idx - 30):min(len(raw), end + 30)]
            has_rel = any(rk in rel_win for rk in _RELATION_KEYWORDS) if is_cause else False
            raw_occurrences[canonical].append((direction, negated, has_rel))
            start = end

    result: dict[str, dict[str, Any]] = {}
    for canonical, occs in raw_occurrences.items():
        if not occs:
            continue
        dirs = {o[0] for o in occs}
        negs = {o[1] for o in occs}
        final_dir = None if len(dirs) > 1 else occs[0][0]
        final_neg = False if len(negs) > 1 else occs[0][1]
        has_rel = any(o[2] for o in occs)
        relation = "cause" if (is_cause and has_rel) else ("accounting" if not is_cause else None)
        result[canonical] = {
            "direction": final_dir,
            "negation": final_neg,
            "relation": relation,
            "evidence_id": None,
        }
    return result


def build_official_explanation_context(
    *,
    announcements: Any,
    half_year_facts: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Each entry carries ``term_details`` — dict keyed by canonical term with
    per-term direction, negation, relation, and evidence_id."""
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
            all_matched = list(matched_cause) + [
                t for t in _ACCOUNTING_KEYWORDS if t in raw
            ]
            cause_details = _scan_term_occurrences(raw, matched_cause, is_cause=True)
            accounting_details = _scan_term_occurrences(
                raw, [t for t in _ACCOUNTING_KEYWORDS if t in raw], is_cause=False
            )
            for details in (cause_details, accounting_details):
                for term_key in details:
                    details[term_key]["evidence_id"] = f"E{idx:03d}"
            term_details: dict[str, dict[str, Any]] = {}
            term_details.update(cause_details)
            term_details.update(accounting_details)
            entries.append({
                "evidence_id": f"E{idx:03d}",
                "source": source,
                "text": _evidence_text_for(raw, all_matched),
                "cause_terms": sorted(set(matched_cause)),
                "accounting_terms": sorted(set(matched_accounting)),
                "term_details": term_details,
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
    """Each claim has an audit_fragment showing matched evidence term and
    its semantic attributes."""
    ctx = explanation_context or {}
    entries = ctx.get("entries") or []
    as_of = ctx.get("as_of")

    evidence_term_info: dict[str, dict[str, dict[str, Any]]] = {}
    evidence_source: dict[str, str] = {}
    for entry in entries:
        eid = entry.get("evidence_id", "")
        evidence_source[eid] = entry.get("source", "unknown")
        term_details = entry.get("term_details") or {}
        if not term_details:
            continue
        evidence_term_info[eid] = {}
        for term_key, details in term_details.items():
            canonical = _canonicalize_term(term_key)
            evidence_term_info[eid][canonical] = details

    text = report_text or ""
    claims: list[dict[str, Any]] = []

    # Deduplicate: track occupied positions so shorter keywords don't
    # duplicate a position already claimed by a longer keyword.
    occupied_positions: set[int] = set()
    for keyword in sorted(_CAUSE_KEYWORDS, key=len, reverse=True):
        canonical = _canonicalize_term(keyword)
        positions = _find_all_occurrences(text, keyword)
        for pos in positions:
            if pos in occupied_positions:
                continue
            occupied_positions.add(pos)
            claim_dir = _extract_direction_at(text, keyword, pos)
            supporting: list[str] = []
            audit_fragment = None
            for eid, term_map in evidence_term_info.items():
                td = term_map.get(canonical)
                if td is None:
                    continue
                ev_dir = td.get("direction")
                if _directions_conflict(ev_dir, claim_dir):
                    continue
                ev_neg = td.get("negation", False)
                if ev_neg:
                    continue
                supporting.append(eid)
                if audit_fragment is None:
                    audit_fragment = {
                        "matched_term": keyword,
                        "direction": td.get("direction"),
                        "negation": td.get("negation", False),
                        "relation": td.get("relation"),
                        "evidence_id": td.get("evidence_id"),
                    }
            if supporting:
                status = "officially_explained"
            elif entries:
                status = "evidence_conflict"
            else:
                status = "unexplained"
            src = evidence_source.get(supporting[0]) if supporting else None
            occurrence = text[:pos].count(keyword)
            claims.append({
                "claim_id": _claim_id_for("CAUSE", keyword, occurrence),
                "metric": keyword,
                "cause_terms": [keyword],
                "policy_terms": [],
                "evidence_ids": supporting,
                "source_type": src,
                "as_of": as_of,
                "status": status,
                "audit_fragment": audit_fragment,
            })

    occupied_positions_acct: set[int] = set()
    for keyword in _ACCOUNTING_KEYWORDS:
        canonical = _canonicalize_term(keyword)
        positions = _find_all_occurrences(text, keyword)
        for pos in positions:
            if pos in occupied_positions_acct:
                continue
            occupied_positions_acct.add(pos)
            claim_negated = _is_negated_at(text, keyword, pos)
            claim_dir = _extract_direction_at(text, keyword, pos)
            supporting = []
            audit_fragment = None
            for eid, term_map in evidence_term_info.items():
                td = term_map.get(canonical)
                if td is None:
                    continue
                ev_dir = td.get("direction")
                if _directions_conflict(ev_dir, claim_dir):
                    continue
                ev_negated = td.get("negation", False)
                if ev_negated and not claim_negated:
                    continue
                if claim_negated and not ev_negated:
                    continue
                supporting.append(eid)
                if audit_fragment is None:
                    audit_fragment = {
                        "matched_term": keyword,
                        "direction": td.get("direction"),
                        "negation": td.get("negation", False),
                        "relation": td.get("relation"),
                        "evidence_id": td.get("evidence_id"),
                    }
            if supporting:
                status = "officially_explained"
            elif entries:
                status = "evidence_conflict"
            else:
                status = "unexplained"
            src = evidence_source.get(supporting[0]) if supporting else None
            occurrence = text[:pos].count(keyword)
            claims.append({
                "claim_id": _claim_id_for("ACCT", canonical, occurrence),
                "metric": canonical,
                "cause_terms": [],
                "policy_terms": [canonical],
                "evidence_ids": supporting,
                "source_type": src,
                "as_of": as_of,
                "status": status,
                "audit_fragment": audit_fragment,
            })

    return claims


def evaluate_fundamental_integrity(
    *,
    identity: Mapping[str, Any] | None,
    period_facts: Iterable[Mapping[str, Any]] | None,
    explanation_context: Mapping[str, Any] | None,
    report_text: str = "",
) -> dict[str, Any]:
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
