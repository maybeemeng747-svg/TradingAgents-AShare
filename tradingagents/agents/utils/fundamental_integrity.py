"""Deterministic integrity checks for fundamental-analysis evidence.

FUND-003A-B: Per-term-occurrence evidence binding — direction, negation,
and causal relation are extracted per keyword occurrence, not per entry.
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
    "原材料采购成本下降", "原材料采购成本上涨",
    "原材料价格下降", "原材料价格上涨",
    "原材料成本下降", "采购成本下降", "原材料成本上涨", "采购成本上涨",
    "原材料下降", "原材料上涨",
    "产品涨价", "销量提升", "成本下降", "行业周期", "集中交付",
    "原材料", "淡季", "旺季",
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

_SYNONYM_GROUPS: list[tuple[str, ...]] = [
    ("预收款", "预收货款", "预收客户货款", "预收客户款", "合同负债驱动", "预收款驱动"),
    (
        "原材料下降", "原材料采购成本下降", "原材料价格下降",
        "原材料成本下降", "采购成本下降",
    ),
    (
        "原材料上涨", "原材料采购成本上涨", "原材料价格上涨",
        "原材料成本上涨", "采购成本上涨",
    ),
]

_CAUSE_SYNONYM_GROUPS: list[tuple[str, ...]] = [
    (
        "原材料下降", "原材料采购成本下降", "原材料价格下降",
        "原材料成本下降", "采购成本下降",
    ),
    (
        "原材料上涨", "原材料采购成本上涨", "原材料价格上涨",
        "原材料成本上涨", "采购成本上涨",
    ),
]

_SYNONYM_MAP: dict[str, str] = {}
for _group in _SYNONYM_GROUPS:
    _canonical = _group[0]
    for _term in _group:
        _SYNONYM_MAP[_term] = _canonical

_CAUSE_SYNONYM_MAP: dict[str, str] = {}
for _group in _CAUSE_SYNONYM_GROUPS:
    _canonical = _group[0]
    for _term in _group:
        _CAUSE_SYNONYM_MAP[_term] = _canonical

_UP_WORDS = {"上涨", "涨价", "上升", "增加", "增长", "提升", "走高", "攀升", "升高", "扩大", "增多"}
_DOWN_WORDS = {"下降", "降低", "减少", "回落", "走低", "收缩", "缩小", "下滑", "下跌", "缩减"}
_NEGATION_WORDS = {"未", "不", "没有", "并非", "未见", "尚未", "未曾"}

_NEGATION_PATTERN = re.compile(
    r"(未|不|没有|并非|未见|尚未|未曾)\s*(?:采用|使用|执行|确认|实行)?\s*(净额法|总额法|主要责任人|代理人)",
)
_DIRECTION_NEGATION_SUFFIX_PATTERN = re.compile(
    r"(?:未|不|没有|并非|未见|尚未|未曾)"
    r"(?:(?:\s|出现|发生|呈现|形成|明显|显著|大幅|持续|进一步|再度|重新|"
    r"实质性|趋势性|快速|继续|能|有|见|再|过|地))*$"
)
_RELATION_KEYWORDS = (
    "主要系", "由于", "因为", "因此", "所以", "导致", "推动", "驱动", "所致", "造成", "带来",
    "受益于", "得益于", "来自",
)
_CAUSE_BEFORE_RESULT_CUES = {
    "因此", "所以", "导致", "推动", "驱动", "造成", "带来", "所致",
}
_RESULT_BEFORE_CAUSE_CUES = {
    "主要系", "由于", "因为", "受益于", "得益于", "来自",
}

# A cause is not enough by itself: "原材料下降是采购优化的结果" does
# not prove the separate claim that it drove gross margin.  Keep the target
# vocabulary deliberately small and financial-report oriented so an unknown
# endpoint fails closed rather than being guessed from free text.
_RELATION_TARGET_GROUPS: list[tuple[str, ...]] = [
    ("经营现金流", "现金流"),
    ("营业收入", "营收", "收入"),
    ("归母净利润", "扣非净利润", "净利润", "利润"),
    ("毛利率",),
    ("净利率",),
    ("合同负债",),
    ("存货",),
    ("应收账款",),
    ("业绩",),
]
_RELATION_TARGET_MAP = {
    term: group[0]
    for group in _RELATION_TARGET_GROUPS
    for term in group
}


def _claim_id_for(prefix: str, content: str) -> str:
    normalized = content.strip()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:8]
    return f"{prefix}-{digest}"


def _normalize_terms(terms: Iterable[str]) -> list[str]:
    result: set[str] = set()
    for t in terms:
        canonical = _ACCOUNTING_CANONICAL.get(t, t)
        synonym = _SYNONYM_MAP.get(canonical, canonical)
        result.add(synonym)
    return sorted(result)


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


def _clause_bounds(text: str, pos: int, keyword_end: int) -> tuple[int, int]:
    """Keep semantic cues in the same sentence as their keyword occurrence."""
    delimiters = "。！？；\n"
    start = max((text.rfind(mark, 0, pos) for mark in delimiters), default=-1) + 1
    end_candidates = [text.find(mark, keyword_end) for mark in delimiters]
    end = min((candidate for candidate in end_candidates if candidate >= 0), default=len(text))
    return start, end


def _extract_direction_at(text: str, keyword: str, pos: int) -> str | None:
    keyword_end = pos + len(keyword)
    intrinsic_up = any(word in keyword for word in _UP_WORDS)
    intrinsic_down = any(word in keyword for word in _DOWN_WORDS)
    if intrinsic_up != intrinsic_down:
        return "up" if intrinsic_up else "down"

    clause_start, clause_end = _clause_bounds(text, pos, keyword_end)

    def crosses_phrase_boundary(word_pos: int, word_len: int) -> bool:
        word_end = word_pos + word_len
        if word_pos >= keyword_end:
            between = text[keyword_end:word_pos]
        elif word_end <= pos:
            between = text[word_end:pos]
        else:
            between = ""
        return "，" in between or "," in between

    best_dir = None
    best_dist = len(text)
    for w in _UP_WORDS:
        w_idx = text.find(w, clause_start, clause_end)
        while w_idx >= 0 and w_idx < clause_end:
            if crosses_phrase_boundary(w_idx, len(w)):
                w_idx = text.find(w, w_idx + 1, clause_end)
                continue
            dist = min(abs(w_idx - pos), abs(w_idx - keyword_end))
            if dist < best_dist:
                best_dist = dist
                best_dir = "up"
            w_idx = text.find(w, w_idx + 1, clause_end)
    for w in _DOWN_WORDS:
        w_idx = text.find(w, clause_start, clause_end)
        while w_idx >= 0 and w_idx < clause_end:
            if crosses_phrase_boundary(w_idx, len(w)):
                w_idx = text.find(w, w_idx + 1, clause_end)
                continue
            dist = min(abs(w_idx - pos), abs(w_idx - keyword_end))
            if dist < best_dist:
                best_dist = dist
                best_dir = "down"
            w_idx = text.find(w, w_idx + 1, clause_end)
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


def _is_direction_negated_at(text: str, keyword: str, pos: int) -> bool:
    """Detect negation attached to a nearby direction word, e.g. 未下降."""
    keyword_end = pos + len(keyword)
    _, clause_end = _clause_bounds(text, pos, keyword_end)
    phrase_breaks = [
        boundary
        for separator in ("，", ",")
        if (boundary := text.find(separator, keyword_end, clause_end)) >= 0
    ]
    phrase_end = min(phrase_breaks, default=clause_end)
    for direction_word in _UP_WORDS | _DOWN_WORDS:
        direction_pos = text.find(direction_word, keyword_end, phrase_end)
        while direction_pos >= 0:
            between = text[keyword_end:direction_pos]
            if _DIRECTION_NEGATION_SUFFIX_PATTERN.search(between):
                return True
            direction_pos = text.find(direction_word, direction_pos + 1, phrase_end)
    return False


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


def _extract_context_around_position(
    text: str, position: int, keyword_len: int, margin: int = 200
) -> str:
    start = max(0, position - margin)
    end = min(len(text), position + keyword_len + margin)
    return text[start:end]


def _cue_assigns_cause_role(
    cue: str,
    cue_pos: int,
    cause_pos: int,
    cause_end: int,
) -> bool:
    if cue == "原因":
        return True
    if cue in _CAUSE_BEFORE_RESULT_CUES:
        return cause_end <= cue_pos
    if cue in _RESULT_BEFORE_CAUSE_CUES:
        return cause_pos >= cue_pos + len(cue)
    return False


def _is_enumerated_cause_prefix(fragment: str) -> bool:
    remaining = fragment
    for cause_term in sorted(_CAUSE_KEYWORDS, key=len, reverse=True):
        remaining = remaining.replace(cause_term, "")
    remaining = re.sub(r"(?:\s|[、，,]|以及|并且|及|和|与)+", "", remaining)
    return not remaining


def _extract_relation_at(text: str, keyword: str, pos: int) -> tuple[str | None, str | None]:
    """Return the nearest causal cue tied to this exact keyword occurrence."""
    keyword_end = pos + len(keyword)
    clause_start, clause_end = _clause_bounds(text, pos, keyword_end)
    following = text[keyword_end:clause_end]
    if re.match(r"^(?:是|为).{0,15}?(?:主要)?原因", following):
        return "causal", "原因"
    preceding = text[max(clause_start, pos - 24):pos]
    if re.search(r"(?:主要)?原因(?:是|为|包括)\s*$", preceding):
        return "causal", "原因"
    # [FUND-007A] Handle "主要系/主要原因 <keyword> ... 所致" pattern.
    # The cue is before the keyword and "所致" is after; the verb "是/为"
    # between keyword and "所致" marks the result boundary.
    _zws_pos = preceding.rfind("主要系")
    _zycy_pos = preceding.rfind("主要原因")
    _zy_pos = max(_zws_pos, _zycy_pos)
    if _zy_pos >= 0:
        _zy_end = max(_zy_pos + len("主要系"), _zy_pos + len("主要原因"))
        _result_seg = text[_zy_end:pos]
        if re.search(r"(?:是|为)\s*$", _result_seg):
            return "causal", "主要系"
        _所致_match = re.search(r"所致", text[keyword_end:clause_end])
        if _所致_match and _所致_match.start() > 0:
            return "causal", "主要系"
    for cue in _CAUSE_BEFORE_RESULT_CUES:
        cue_pos = preceding.rfind(cue)
        if cue_pos < 0:
            continue
        cleft_result = preceding[cue_pos + len(cue):]
        if (
            re.search(r".+(?:的)?(?:是|为)\s*$", cleft_result)
            and any(target in cleft_result for target in _RELATION_TARGET_MAP)
        ):
            return "causal", cue
    start = max(clause_start, pos - 6)
    end = min(clause_end, keyword_end + 6)
    window = text[start:end]
    nearby_candidates: list[tuple[int, str, int]] = []
    for cue in _RELATION_KEYWORDS:
        cue_pos = window.find(cue)
        while cue_pos >= 0:
            absolute_pos = start + cue_pos
            distance = min(abs(absolute_pos - pos), abs(absolute_pos - keyword_end))
            nearby_candidates.append((distance, cue, absolute_pos))
            cue_pos = window.find(cue, cue_pos + 1)
    valid_candidates = [
        candidate
        for candidate in nearby_candidates
        if candidate[0] <= 6
        and _cue_assigns_cause_role(candidate[1], candidate[2], pos, keyword_end)
    ]
    if valid_candidates:
        # Nested wording can place one cue on each side of a cause. Prefer the
        # cue that preserves a recognized financial result instead of blindly
        # choosing the nearest token (for example, 由于...下降导致成本改善).
        targeted_candidates = [
            candidate
            for candidate in valid_candidates
            if _extract_relation_targets(text, keyword, pos, candidate[1])
        ]
        chosen = min(targeted_candidates or valid_candidates, key=lambda item: item[0])
        return "causal", chosen[1]

    if not valid_candidates:
        # A trailing verb can also be shared by a listed set of causes, e.g.
        # "行业周期及集中交付推动利润改善".  Do not cross a clause break.
        suffix = text[keyword_end:min(clause_end, keyword_end + 30)]
        for cue in _RELATION_KEYWORDS:
            cue_pos = suffix.find(cue)
            if cue_pos < 0:
                continue
            absolute_pos = keyword_end + cue_pos
            between = suffix[:cue_pos]
            only_connector_comma = not between.strip("，, ")
            if (
                not any(mark in between for mark in "；。！？\n")
                and ("，" not in between and "," not in between or only_connector_comma)
                and _cue_assigns_cause_role(cue, absolute_pos, pos, keyword_end)
            ):
                return "causal", cue
        # A leading connector can legitimately be farther away when a report
        # names the result before its cause.  Only accept it across an
        # uninterrupted phrase, so a later unrelated clause cannot leak back.
        prefix = text[max(clause_start, pos - 30):pos]
        for cue in _RELATION_KEYWORDS:
            cue_pos = prefix.rfind(cue)
            if cue_pos < 0:
                continue
            between = prefix[cue_pos + len(cue):]
            absolute_pos = max(clause_start, pos - 30) + cue_pos
            comma_is_enumeration = (
                "，" not in between and "," not in between
            ) or _is_enumerated_cause_prefix(between)
            if (
                not any(mark in between for mark in "；。！？\n")
                and comma_is_enumeration
                and _cue_assigns_cause_role(cue, absolute_pos, pos, keyword_end)
            ):
                return "causal", cue
        return None, None
    return None, None


def _extract_relation_targets(
    text: str,
    keyword: str,
    pos: int,
    relation_cue: str | None,
) -> list[str]:
    """Return every financial result directly connected by ``relation_cue``."""
    if not relation_cue:
        return []
    keyword_end = pos + len(keyword)
    clause_start, clause_end = _clause_bounds(text, pos, keyword_end)

    cue_positions = _find_all_occurrences(text[clause_start:clause_end], relation_cue)
    if not cue_positions:
        # ``原因`` is emitted by the structured ``是...主要原因`` matcher.
        search_start, search_end = keyword_end, clause_end
    else:
        absolute_positions = [clause_start + item for item in cue_positions]
        cue_pos = min(
            absolute_positions,
            key=lambda item: min(abs(item - pos), abs(item - keyword_end)),
        )
        cue_end = cue_pos + len(relation_cue)
        comma_before_cue = max(
            text.rfind(separator, clause_start, cue_pos)
            for separator in ("，", ",")
        )
        comma_after_candidates = [
            position
            for separator in ("，", ",")
            if (position := text.find(separator, cue_end, clause_end)) >= 0
        ]
        comma_after_cue = min(comma_after_candidates, default=-1)
        cue_segment_start = comma_before_cue + 1 if comma_before_cue >= 0 else clause_start
        cue_segment_end = comma_after_cue if comma_after_cue >= 0 else clause_end

        if (
            cue_pos < pos
            and relation_cue in _CAUSE_BEFORE_RESULT_CUES
            and re.search(r"(?:的)?(?:是|为)\s*$", text[cue_end:pos])
        ):
            # 倒装句："导致毛利率提升的是原材料下降"。
            search_start, search_end = cue_end, pos
        elif cue_pos < pos:
            # Some disclosures insert a comma immediately before the cue
            # ("毛利率提升，主要由于...").  If the cue's own comma segment
            # has no target, include exactly the preceding segment; otherwise
            # keep the scan in the current segment to avoid borrowing an
            # unrelated earlier metric.
            immediate_prefix = text[cue_segment_start:cue_pos]
            has_target_in_immediate_prefix = any(
                target in immediate_prefix for target in _RELATION_TARGET_MAP
            )
            if comma_before_cue >= 0 and not has_target_in_immediate_prefix:
                previous_comma = max(
                    text.rfind(separator, clause_start, comma_before_cue)
                    for separator in ("，", ",")
                )
                search_start = previous_comma + 1 if previous_comma >= 0 else clause_start
            else:
                search_start = cue_segment_start
            search_end = cue_pos
            has_backward_target = any(
                text.find(target, search_start, search_end) >= 0
                for target in _RELATION_TARGET_MAP
            )
            if (
                not has_backward_target
                and relation_cue in _RESULT_BEFORE_CAUSE_CUES
                and comma_after_cue >= keyword_end
            ):
                result_start = comma_after_cue + 1
                next_comma_candidates = [
                    position
                    for separator in ("，", ",")
                    if (position := text.find(separator, result_start, clause_end)) >= 0
                ]
                next_comma = min(next_comma_candidates, default=-1)
                search_start = result_start
                search_end = next_comma if next_comma >= 0 else clause_end
        elif relation_cue in {"因此", "所以", "导致", "推动", "驱动", "造成", "带来"}:
            # Usually the result follows the cue ("原因推动结果").  In the
            # common "原因对结果有推动作用" / "原因给结果带来..." shape,
            # however, the financial target sits between the cause and verb.
            interposed = text[keyword_end:cue_pos]
            has_interposed_target = any(
                target in interposed for target in _RELATION_TARGET_MAP
            )
            if (
                has_interposed_target
                and re.match(r"^\s*(?:对|给|为|使|令|让)", interposed)
            ):
                search_start, search_end = keyword_end, cue_pos
            else:
                search_start, search_end = cue_end, cue_segment_end
        elif relation_cue == "所致":
            # 结果 + 原因 + 所致。
            search_start, search_end = cue_segment_start, pos
        elif relation_cue == "原因":
            # 原因项 + 是/为 + 财务结果 + 的主要原因。
            search_start, search_end = keyword_end, cue_pos
        else:
            # "原因，主要系另一原因" explains the cause term itself.  Do not
            # borrow a financial word from a later comma-delimited fragment.
            search_start, search_end = cue_segment_start, pos

    matches: list[tuple[int, str]] = []
    for target in sorted(_RELATION_TARGET_MAP, key=len, reverse=True):
        target_pos = text.find(target, search_start, search_end)
        while target_pos >= 0:
            target_end = target_pos + len(target)
            overlaps_cause = target_pos < keyword_end and target_end > pos
            if not overlaps_cause:
                matches.append((target_pos, _RELATION_TARGET_MAP[target]))
            target_pos = text.find(target, target_pos + 1, search_end)
    targets: list[str] = []
    for _, canonical in sorted(matches):
        if canonical not in targets:
            targets.append(canonical)
    return targets


def _evidence_text_for(raw: str, keywords: list[str]) -> str:
    for kw in keywords:
        if kw in raw:
            return _extract_context_around(raw, kw)
    return raw[:500]


def _extract_term_occurrences(
    text: str, keywords: tuple[str, ...]
) -> list[dict[str, Any]]:
    occurrences: list[dict[str, Any]] = []
    # Prefer the longest keyword at one position.  A single "原材料成本下降"
    # occurrence must not also become independent "原材料"/"成本下降" evidence.
    occupied_spans: list[tuple[int, int]] = []
    for kw in sorted(keywords, key=len, reverse=True):
        for pos in _find_all_occurrences(text, kw):
            end_pos = pos + len(kw)
            canonical = _ACCOUNTING_CANONICAL.get(kw, kw)
            canonical = _SYNONYM_MAP.get(canonical, canonical)
            cause_canonical = _CAUSE_SYNONYM_MAP.get(kw)
            if cause_canonical:
                canonical = cause_canonical
            if any(pos < occupied_end and end_pos > occupied_start for occupied_start, occupied_end in occupied_spans):
                continue
            occupied_spans.append((pos, end_pos))
            direction = _extract_direction_at(text, kw, pos)
            if canonical == "原材料" and direction == "down":
                canonical = "原材料下降"
            elif canonical == "原材料" and direction == "up":
                canonical = "原材料上涨"
            negated = _is_negated_at(text, kw, pos) or _is_direction_negated_at(text, kw, pos)
            relation, relation_cue = _extract_relation_at(text, kw, pos)
            relation_targets = (
                _extract_relation_targets(text, kw, pos, relation_cue)
                if relation == "causal"
                else []
            )
            occurrences.append({
                "surface_term": kw,
                "canonical": canonical,
                "direction": direction,
                "negation": negated,
                "relation": relation,
                "relation_cue": relation_cue,
                "relation_target": relation_targets[0] if relation_targets else None,
                "relation_targets": relation_targets,
                "position": pos,
                "context": _extract_context_around_position(text, pos, len(kw), margin=60),
            })
    return occurrences


def _cause_family(canonical: str) -> str:
    if canonical in {"原材料下降", "原材料上涨"}:
        return "原材料"
    return canonical


def _audit_fragment(evidence_id: str, occurrence: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "matched_term": occurrence.get("surface_term") or occurrence.get("canonical"),
        "canonical": occurrence.get("canonical"),
        "direction": occurrence.get("direction"),
        "negation": bool(occurrence.get("negation")),
        "relation": occurrence.get("relation"),
        "relation_cue": occurrence.get("relation_cue"),
        "relation_target": occurrence.get("relation_target"),
        "relation_targets": occurrence.get("relation_targets") or [],
        "position": occurrence.get("position"),
    }


def build_official_explanation_context(
    *,
    announcements: Any,
    half_year_facts: Mapping[str, Any] | None,
) -> dict[str, Any]:
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
            occurrences = _extract_term_occurrences(raw, tuple(all_matched))
            entries.append({
                "evidence_id": f"E{idx:03d}",
                "source": source,
                "text": _evidence_text_for(raw, all_matched),
                "cause_terms": sorted(set(matched_cause)),
                "accounting_terms": sorted(set(matched_accounting)),
                "occurrences": occurrences,
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
    ctx = explanation_context or {}
    entries = ctx.get("entries") or []
    as_of = ctx.get("as_of")

    evidence_occurrences: dict[str, list[dict[str, Any]]] = {}
    evidence_source: dict[str, str] = {}
    for entry in entries:
        eid = entry.get("evidence_id", "")
        evidence_source[eid] = entry.get("source", "unknown")
        evidence_occurrences[eid] = entry.get("occurrences") or []

    text = report_text or ""
    claims: list[dict[str, Any]] = []

    # Neutral mentions such as "关注原材料" are not claims.  A directed fact can
    # be supported by the same official fact, while a causal claim additionally
    # requires an official causal occurrence for that exact term.
    for claim_occ in _extract_term_occurrences(text, _CAUSE_KEYWORDS):
        is_causal_claim = claim_occ["relation"] == "causal"
        if not is_causal_claim and claim_occ["direction"] is None:
            continue
        claim_canonical = str(claim_occ["canonical"])
        claim_dir = claim_occ.get("direction")
        claim_negated = bool(claim_occ["negation"])
        claim_targets = set(
            claim_occ.get("relation_targets")
            or ([claim_occ.get("relation_target")] if claim_occ.get("relation_target") else [])
        )
        supporting: list[str] = []
        fragments: list[dict[str, Any]] = []
        contradictory_evidence = False
        for eid, ev_occs in evidence_occurrences.items():
            for ev_occ in ev_occs:
                ev_canonical = str(ev_occ.get("canonical") or "")
                if _cause_family(ev_canonical) != _cause_family(claim_canonical):
                    continue
                ev_negated = bool(ev_occ.get("negation"))
                if ev_canonical == claim_canonical and ev_negated != claim_negated:
                    contradictory_evidence = True
                    continue
                if ev_negated != claim_negated:
                    # "未上涨" does not contradict "下降"; it merely cannot
                    # prove it.  Negating the exact same directed fact above is
                    # a contradiction and must fail closed.
                    continue
                ev_dir = ev_occ.get("direction")
                if _directions_conflict(ev_dir, claim_dir):
                    if not ev_negated and not claim_negated:
                        contradictory_evidence = True
                    continue
                ev_targets = set(
                    ev_occ.get("relation_targets")
                    or ([ev_occ.get("relation_target")] if ev_occ.get("relation_target") else [])
                )
                if (
                    ev_canonical != claim_canonical
                    or (
                        is_causal_claim
                        and (
                            ev_occ.get("relation") != "causal"
                            or not claim_targets
                            or not claim_targets.issubset(ev_targets)
                        )
                    )
                ):
                    continue
                if eid not in supporting:
                    supporting.append(eid)
                    fragments.append(_audit_fragment(eid, ev_occ))
        if supporting and not contradictory_evidence:
            status = "officially_explained"
        elif entries:
            status = "evidence_conflict"
        else:
            status = "unexplained"
        ctx_text = _extract_context_around_position(
            text, int(claim_occ["position"]), len(str(claim_occ["surface_term"])), margin=60
        )
        claims.append({
            "claim_id": _claim_id_for(
                "CAUSE", f"{claim_occ['surface_term']}@{claim_occ['position']}:{ctx_text}"
            ),
            "metric": claim_occ["canonical"],
            "cause_terms": [claim_occ["surface_term"]],
            "policy_terms": [],
            "evidence_ids": supporting,
            "source_type": evidence_source.get(supporting[0]) if supporting else None,
            "as_of": as_of,
            "status": status,
            "audit_fragment": fragments[0] if fragments else None,
            "audit_fragments": fragments,
        })

    seen_accounting: set[tuple[str, int]] = set()
    for keyword in _ACCOUNTING_KEYWORDS:
        positions = _find_all_occurrences(text, keyword)
        for pos in positions:
            canonical = _ACCOUNTING_CANONICAL.get(keyword, keyword)
            canonical = _SYNONYM_MAP.get(canonical, canonical)
            if (canonical, pos) in seen_accounting:
                continue
            seen_accounting.add((canonical, pos))
            claim_negated = _is_negated_at(text, keyword, pos) or _is_direction_negated_at(
                text, keyword, pos
            )
            claim_dir = _extract_direction_at(text, keyword, pos)
            supporting: list[str] = []
            fragments: list[dict[str, Any]] = []
            contradictory_evidence = False
            for eid, ev_occs in evidence_occurrences.items():
                for ev_occ in ev_occs:
                    ev_canonical = ev_occ.get("canonical", "")
                    if ev_canonical != canonical:
                        continue
                    ev_dir = ev_occ.get("direction")
                    if _directions_conflict(ev_dir, claim_dir):
                        if not ev_occ.get("negation", False) and not claim_negated:
                            contradictory_evidence = True
                        continue
                    ev_negated = ev_occ.get("negation", False)
                    if bool(ev_negated) != claim_negated:
                        contradictory_evidence = True
                        continue
                    if eid not in supporting:
                        supporting.append(eid)
                        fragments.append(_audit_fragment(eid, ev_occ))
            if supporting and not contradictory_evidence:
                status = "officially_explained"
            elif entries:
                status = "evidence_conflict"
            else:
                status = "unexplained"
            src = evidence_source.get(supporting[0]) if supporting else None
            ctx_text = _extract_context_around_position(text, pos, len(keyword), margin=60)
            claims.append({
                "claim_id": _claim_id_for("ACCT", f"{canonical}@{pos}:{ctx_text}"),
                "metric": canonical,
                "cause_terms": [],
                "policy_terms": [canonical],
                "evidence_ids": supporting,
                "source_type": src,
                "as_of": as_of,
                "status": status,
                "audit_fragment": fragments[0] if fragments else None,
                "audit_fragments": fragments,
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
        # Verified facts remain visible in the gated report, but any unresolved
        # integrity blocker removes the module from directional voting.
        "weight_allowed": not blockers,
        "claims": claims,
    }


def extract_financial_anomaly_inputs(
    facts: Iterable[Mapping[str, Any]] | None,
) -> dict[str, float | None]:
    # [FUND-004B] Group facts by (report_date, period_scope, unit) so that
    # gross_margin, debt_ratio and cashflow comparisons only use values from
    # the same report date, same cumulative/single-quarter scope and same unit.
    # Mixing across groups produced fabricated 88%/-500% gross margins.

    _INCOME_METRICS = {"revenue", "operating_cost", "net_profit"}
    _CASHFLOW_METRICS = {"operating_cashflow", "investing_cashflow", "financing_cashflow"}
    _BALANCE_METRICS = {"total_assets", "total_liabilities"}

    def _group_key(item: Mapping[str, Any]) -> tuple[str, str, str]:
        return (
            str(item.get("report_date") or ""),
            str(item.get("period_scope") or ""),
            str(item.get("unit") or "元"),
        )

    def _value(item: Mapping[str, Any] | None) -> float | None:
        if not item:
            return None
        raw = item.get("value")
        return float(raw) if isinstance(raw, (int, float)) else None

    # Build groups keyed by (report_date, period_scope, unit).
    # For each group, track the latest value per metric (last occurrence wins
    # when the same metric appears multiple times with the same key).
    groups: dict[tuple[str, str, str], dict[str, Mapping[str, Any]]] = {}
    for item in facts or []:
        if item.get("value") is None:
            continue
        metric = str(item.get("metric") or "")
        if not metric:
            continue
        key = _group_key(item)
        groups.setdefault(key, {})[metric] = item

    # Sort groups by report_date descending (latest first).
    sorted_keys = sorted(groups.keys(), key=lambda k: k[0], reverse=True)

    def _find_best_group(
        required_metrics: set[str],
    ) -> dict[str, Mapping[str, Any]] | None:
        """Return the latest group that contains ALL required metrics."""
        for key in sorted_keys:
            group = groups[key]
            if all(m in group for m in required_metrics):
                return group
        return None

    def _find_previous_group(
        required_metrics: set[str],
        current_date: str,
    ) -> dict[str, Mapping[str, Any]] | None:
        """Return the latest group before current_date with all required metrics."""
        for key in sorted_keys:
            if key[0] >= current_date:
                continue
            group = groups[key]
            if all(m in group for m in required_metrics):
                return group
        return None

    def _find_year_over_year_group(
        required_metrics: set[str],
        current_date: str,
        current_scope: str,
        current_unit: str,
    ) -> dict[str, Mapping[str, Any]] | None:
        """Return the exact prior-year comparable group or fail closed.

        Interim income statements are cumulative.  Comparing 2026 Q1 with
        2025 FY (or H1 with Q1) creates a plausible-looking but meaningless
        growth rate, so growth metrics never fall back to the nearest period
        or cross units.
        """
        try:
            current_year = int(current_date[:4])
            comparable_date = f"{current_year - 1}{current_date[4:]}"
        except (TypeError, ValueError):
            return None
        for key in sorted_keys:
            if (
                key[0] != comparable_date
                or key[1] != current_scope
                or key[2] != current_unit
            ):
                continue
            group = groups[key]
            if all(metric in group for metric in required_metrics):
                return group
        return None

    # --- Income group: gross_margin from same-date/same-scope revenue + cost ---
    income_group = _find_best_group({"revenue", "operating_cost"})
    if income_group:
        revenue = _value(income_group["revenue"])
        cost = _value(income_group["operating_cost"])
        income_date = str(income_group["revenue"].get("report_date") or "")
        income_scope = str(income_group["revenue"].get("period_scope") or "")
    else:
        revenue, cost, income_date, income_scope = None, None, "", ""

    gross_margin = (
        (revenue - cost) / revenue * 100
        if revenue not in (None, 0) and cost is not None
        else None
    )

    prev_income = _find_previous_group({"revenue", "operating_cost"}, income_date)
    if prev_income:
        prior_revenue = _value(prev_income["revenue"])
        prior_cost = _value(prev_income["operating_cost"])
    else:
        prior_revenue, prior_cost = None, None

    gross_margin_prev = (
        (prior_revenue - prior_cost) / prior_revenue * 100
        if prior_revenue not in (None, 0) and prior_cost is not None
        else None
    )

    # --- Balance group: debt_ratio from same-date POINT_IN_TIME assets + liabilities ---
    balance_group = _find_best_group({"total_assets", "total_liabilities"})
    if balance_group:
        assets = _value(balance_group["total_assets"])
        liabilities = _value(balance_group["total_liabilities"])
        balance_date = str(balance_group["total_assets"].get("report_date") or "")
    else:
        assets, liabilities, balance_date = None, None, ""

    debt_ratio = (
        liabilities / assets * 100
        if assets not in (None, 0) and liabilities is not None
        else None
    )

    prev_balance = _find_previous_group({"total_assets", "total_liabilities"}, balance_date)
    if prev_balance:
        prior_assets = _value(prev_balance["total_assets"])
        prior_liabilities = _value(prev_balance["total_liabilities"])
    else:
        prior_assets, prior_liabilities = None, None

    debt_ratio_prev = (
        prior_liabilities / prior_assets * 100
        if prior_assets not in (None, 0) and prior_liabilities is not None
        else None
    )

    # --- Cashflow/profit: must come from same (report_date, period_scope, unit) group ---
    # [FUND-004B-R1] profit and operating_cashflow must share group key.
    profit_cashflow_group = _find_best_group({"net_profit", "operating_cashflow"})
    profit_group = profit_cashflow_group
    cashflow_group = profit_cashflow_group

    # invest/finance cashflows: independent groups (no cross-metric ratio)
    invest_group = _find_best_group({"investing_cashflow"})
    finance_group = _find_best_group({"financing_cashflow"})

    # --- Phase 2: ROE from net_profit (income) + total_equity (balance) ---
    # These live in different period_scope groups, so we match by report_date.
    equity_items: list[Mapping[str, Any]] = []
    for item in facts or []:
        if str(item.get("metric") or "") == "total_equity" and item.get("value") is not None:
            equity_items.append(item)
    equity_items.sort(key=lambda x: str(x.get("report_date") or ""), reverse=True)

    equity_by_date: dict[str, float] = {}
    for item in equity_items:
        d = str(item.get("report_date") or "")
        if d and d not in equity_by_date:
            equity_by_date[d] = _value(item) or 0.0

    profit_items: list[Mapping[str, Any]] = []
    for item in facts or []:
        if str(item.get("metric") or "") == "net_profit" and item.get("value") is not None:
            profit_items.append(item)
    profit_items.sort(key=lambda x: str(x.get("report_date") or ""), reverse=True)

    def _find_roe_pair() -> tuple[float | None, str]:
        """Find latest (net_profit, total_equity) sharing the same report_date."""
        for pi in profit_items:
            pd = str(pi.get("report_date") or "")
            if pd and pd in equity_by_date:
                np_val = _value(pi)
                eq_val = equity_by_date[pd]
                if np_val is not None and eq_val:
                    return np_val / eq_val * 100, pd
        return None, ""

    roe, roe_date = _find_roe_pair()

    def _find_prev_roe_pair(before_date: str) -> float | None:
        """Find latest ROE pair strictly before before_date."""
        for pi in profit_items:
            pd = str(pi.get("report_date") or "")
            if pd and pd < before_date and pd in equity_by_date:
                np_val = _value(pi)
                eq_val = equity_by_date[pd]
                if np_val is not None and eq_val:
                    return np_val / eq_val * 100
        return None

    roe_prev = _find_prev_roe_pair(roe_date) if roe_date else None

    # --- Phase 2: per-metric growth from exact prior-year comparable groups ---
    def _growth_rate(current: float | None, prior: float | None) -> float | None:
        if current is not None and prior is not None and prior != 0:
            return (current - prior) / abs(prior) * 100
        return None

    revenue_group = _find_best_group({"revenue"})
    if revenue_group:
        current_revenue = _value(revenue_group["revenue"])
        revenue_date = str(revenue_group["revenue"].get("report_date") or "")
        revenue_scope = str(revenue_group["revenue"].get("period_scope") or "")
        revenue_unit = str(revenue_group["revenue"].get("unit") or "元")
    else:
        current_revenue, revenue_date, revenue_scope, revenue_unit = None, "", "", ""

    yoy_revenue_group = _find_year_over_year_group(
        {"revenue"}, revenue_date, revenue_scope, revenue_unit
    )
    yoy_revenue = (
        _value(yoy_revenue_group["revenue"]) if yoy_revenue_group else None
    )
    # Preserve the existing sequential-period anomaly signals, while exposing
    # explicit YoY fields for report cards.  The UI must never label a
    # sequential cumulative comparison as "同比".
    revenue_growth = _growth_rate(revenue, prior_revenue)
    operating_cost_growth = _growth_rate(cost, prior_cost)
    revenue_growth_yoy = _growth_rate(current_revenue, yoy_revenue)

    net_profit_yoy_group = _find_best_group({"net_profit"})
    if net_profit_yoy_group:
        current_net_profit = _value(net_profit_yoy_group["net_profit"])
        net_profit_date = str(
            net_profit_yoy_group["net_profit"].get("report_date") or ""
        )
        net_profit_scope = str(
            net_profit_yoy_group["net_profit"].get("period_scope") or ""
        )
        net_profit_unit = str(
            net_profit_yoy_group["net_profit"].get("unit") or "元"
        )
    else:
        current_net_profit, net_profit_date, net_profit_scope, net_profit_unit = (
            None,
            "",
            "",
            "",
        )
    yoy_profit_group = _find_year_over_year_group(
        {"net_profit"}, net_profit_date, net_profit_scope, net_profit_unit
    )
    prior_net_profit = (
        _value(yoy_profit_group["net_profit"]) if yoy_profit_group else None
    )
    net_profit_growth_yoy = _growth_rate(current_net_profit, prior_net_profit)

    # --- Phase 2: AR / inventory growth from current vs prior group ---
    ar_group = _find_best_group({"accounts_receivable"})
    if ar_group:
        ar_current = _value(ar_group["accounts_receivable"])
        ar_date = str(ar_group["accounts_receivable"].get("report_date") or "")
    else:
        ar_current, ar_date = None, ""

    prev_ar_group = _find_previous_group({"accounts_receivable"}, ar_date)
    ar_prev = _value(prev_ar_group["accounts_receivable"]) if prev_ar_group else None
    accounts_receivable_growth = _growth_rate(ar_current, ar_prev)

    inv_group = _find_best_group({"inventory"})
    if inv_group:
        inv_current = _value(inv_group["inventory"])
        inv_date = str(inv_group["inventory"].get("report_date") or "")
    else:
        inv_current, inv_date = None, ""

    prev_inv_group = _find_previous_group({"inventory"}, inv_date)
    inv_prev = _value(prev_inv_group["inventory"]) if prev_inv_group else None
    inventory_growth = _growth_rate(inv_current, inv_prev)

    return {
        "gross_margin": gross_margin,
        "gross_margin_prev": gross_margin_prev,
        "operating_cashflow": _to_yi(cashflow_group.get("operating_cashflow") if cashflow_group else None),
        "net_profit": _to_yi(profit_group.get("net_profit") if profit_group else None),
        "debt_ratio": debt_ratio,
        "debt_ratio_prev": debt_ratio_prev,
        "total_invest_cashflow": _to_yi(invest_group.get("investing_cashflow") if invest_group else None),
        "total_finance_cashflow": _to_yi(finance_group.get("financing_cashflow") if finance_group else None),
        # Phase 2
        "roe": roe,
        "roe_prev": roe_prev,
        "revenue_growth": revenue_growth,
        "revenue_growth_yoy": revenue_growth_yoy,
        "net_profit_growth_yoy": net_profit_growth_yoy,
        "operating_cost_growth": operating_cost_growth,
        "accounts_receivable_growth": accounts_receivable_growth,
        "inventory_growth": inventory_growth,
        "total_assets": _to_yi(
            balance_group.get("total_assets") if balance_group else None
        ),
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


# [FUND-004A] fundamental_semantic_gate_forward

_GATED_HEADER = (
    "【基本面语义门禁已触发 — 该模块不参与方向权重】\n\n"
    "基本面报告存在以下完整性问题，已被门禁拦截：\n"
)


def _extract_verified_facts(pool: Mapping[str, Any] | None) -> str:
    """Extract verified financial facts from data pool for the gated report."""
    if not pool:
        return ""
    parts: list[str] = []
    identity = pool.get("instrument_identity")
    if isinstance(identity, Mapping) and identity.get("security_name"):
        parts.append(f"公司名称：{identity.get('security_name')}")
    if isinstance(identity, Mapping) and identity.get("industry"):
        parts.append(f"行业：{identity.get('industry')}")
    period_facts = pool.get("financial_period_facts") or []
    if period_facts:
        latest_by_metric: dict[str, Mapping[str, Any]] = {}
        for fact in period_facts:
            if not isinstance(fact, Mapping) or fact.get("value") is None:
                continue
            metric = str(fact.get("metric") or "")
            if not metric:
                continue
            existing = latest_by_metric.get(metric)
            if existing is None or str(fact.get("report_date", "")) > str(existing.get("report_date", "")):
                latest_by_metric[metric] = fact
        metric_labels = {
            "revenue": "营业收入", "operating_cost": "营业成本",
            "net_profit": "净利润", "operating_cashflow": "经营现金流",
            "total_assets": "总资产", "total_liabilities": "总负债",
            "gross_margin": "毛利率", "debt_ratio": "资产负债率",
        }
        for metric, label in metric_labels.items():
            fact = latest_by_metric.get(metric)
            if fact is not None:
                value = fact.get("value")
                unit = fact.get("unit", "")
                report_date = fact.get("report_date", "")
                parts.append(f"{label}：{value} {unit}（{report_date}）")
    return "\n".join(parts) if parts else "无可验证财务事实"


def build_gated_fundamentals_report(
    *,
    original_report: str,
    integrity: Mapping[str, Any],
    pool: Mapping[str, Any] | None,
) -> str:
    """Build a safe fundamentals report when integrity gate fails.

    Contains only blocker reasons and verified raw facts — no narrative
    claims that could be unsupported causal or accounting explanations.
    """
    blockers = integrity.get("blockers") or []
    blocker_codes = {str(item.get("code") or "") for item in blockers}
    if blocker_codes and blocker_codes <= {DERIVATION_CONFLICT}:
        return _build_partially_gated_fundamentals_report(
            blockers=blockers,
            pool=pool,
        )

    blocker_lines = "\n".join(
        f"- {b.get('code')}: {b.get('reason')}" for b in blockers
    )
    verified_facts = _extract_verified_facts(pool)
    return (
        f"{_GATED_HEADER}{blocker_lines}\n\n"
        f"【保留的可验证财务事实】\n{verified_facts}\n\n"
        f"注意：以上事实仅供参考，不得从中推导因果解释或会计口径结论。\n"
        f"基本面模块不参与方向权重。Bull/Bear 研究不得引用被拒绝的基本面叙事。"
    )


def _build_partially_gated_fundamentals_report(
    *,
    blockers: Iterable[Mapping[str, Any]],
    pool: Mapping[str, Any] | None,
) -> str:
    blocker_lines = "\n".join(
        f"- {item.get('code')}: {item.get('reason')}" for item in blockers
    )
    verified_facts = _extract_verified_facts(pool)
    return (
        "【基本面语义门禁已触发 — 期间派生项局部降级】\n\n"
        f"{blocker_lines}\n\n"
        "原模型基本面叙事已隔离，不参与方向判断；"
        "仅保留以下结构化、可追溯的原始事实：\n\n"
        f"【保留的可验证财务事实】\n{verified_facts}\n\n"
        "基本面门禁仍未完全通过，不得据此新增或加仓；"
        "方向统一降为中性，等待期间口径补齐。\n"
        '<!-- VERDICT: {"direction": "中性", "reason": "单季度派生冲突待复核"} -->'
    )


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
