# [KB-006] local_knowledge_context_api
"""Local knowledge query API + investment-controller read-only context bucket.

Wraps KB-003 ``query_local_knowledge`` to expose a slim, controller-friendly
summary of Tree Work ``wiki/investment`` hits. The investment-controller (and
the new ``/v1/knowledge/local/search`` endpoint) can use this to cite local
knowledge as background context in pre/open/post-market briefings — without
reading files directly or fabricating context.

Design contract (see docs/TASKS.md KB-006):

    * READ-ONLY. Never writes to the knowledge base, never calls LLM, never
      hits the network.
    * Never returns full page body text — only ``title``, ``rel_path``,
      ``updated_at``, ``confidence``, ``summary_snippet`` (<=200 chars),
      ``risks`` (<=5 items) and negative flags (``is_stale`` /
      ``is_low_confidence`` / ``is_to_be_supplemented``).
    * Only ``wiki/investment`` partition is visible. Other partitions
      (``inbox/``, ``raw/``, private notes, …) are never exposed.
    * Configurable knowledge root (env ``KNOWLEDGE_ROOT`` /
      ``AUTO_DEV_KNOWLEDGE_ROOT``, default ``~/Documents/knowledge``).
    * Disable switch: env ``KNOWLEDGE_CONTEXT_DISABLED=1`` /
      ``KNOWLEDGE_LOCAL_DISABLED=1`` (or pass ``disabled=True`` /
      ``knowledge_root=""``). Disabled state returns ``data_status=skipped``
      and empty hits — never raises.
    * Background / opinion source only. Never emits a strong trade verb;
      ``local_knowledge_hits`` is a research-priority hint, not a trade
      signal, and must not change the strong-action gate.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from tradingagents.dataflows.local_knowledge_audit import (
    default_knowledge_root,
)
from tradingagents.dataflows.local_knowledge_provider import (
    STATUS_FAILED,
    STATUS_HAS_DATA,
    STATUS_LOW_CONFIDENCE,
    STATUS_NORMAL_NO_DATA,
    STATUS_STALE,
    VENDOR,
    LocalKnowledgeQueryResult,
    compute_local_knowledge_score,
    query_local_knowledge,
)

# [PERF-001] runtime_tier — local knowledge search stays inside FAST_RADAR.
from api.runtime_tier import tradeflow_meta as _tradeflow_meta  # noqa: E402

logger = logging.getLogger(__name__)

CONTEXT_SOURCE = "local_knowledge_context"

# Canonical data_status vocabulary reused from IC-TA-001 (kept in sync).
DATA_STATUS_FRESH = "fresh"
DATA_STATUS_STALE = "stale"
DATA_STATUS_MISSING = "missing"
DATA_STATUS_FAILED = "failed"
DATA_STATUS_SKIPPED = "skipped"
ALLOWED_DATA_STATUSES = frozenset({
    DATA_STATUS_FRESH,
    DATA_STATUS_STALE,
    DATA_STATUS_MISSING,
    DATA_STATUS_FAILED,
    DATA_STATUS_SKIPPED,
})

# Strong action verbs the synthesised context must NEVER emit.
_STRONG_ACTION_PATTERNS = ("立即买入", "立即卖出", "满仓", "清仓", "全仓")

# Disable env vars — any one set to a truthy value turns the bucket off.
_DISABLE_ENV_VARS = (
    "KNOWLEDGE_CONTEXT_DISABLED",
    "KNOWLEDGE_LOCAL_DISABLED",
)
# Knowledge-root override env vars (same precedence as KB-001 audit).
_ROOT_ENV_VARS = ("KNOWLEDGE_ROOT", "AUTO_DEV_KNOWLEDGE_ROOT")

# Per-query soft caps (mirror KB-003 limits so the API stays inside the
# FAST_RADAR latency budget and never dumps full page bodies).
DEFAULT_MAX_PAGES = 5
_ABSOLUTE_MAX_PAGES = 20
_SUMMARY_SNIPPET_MAX_CHARS = 200
_MAX_RISKS_PER_HIT = 5
_MAX_THEMES_PER_QUERY = 10
_MAX_HITS_PER_CONTEXT_BUCKET = 20


# ──────────────────────────────────────────────────────────────────────────────
# Config helpers
# ──────────────────────────────────────────────────────────────────────────────


def _env_truthy(value: Optional[str]) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on", "y", "t"}


def is_local_knowledge_disabled(
    *,
    disabled: Optional[bool] = None,
    env: Optional[Dict[str, str]] = None,
) -> bool:
    """Return True when the local-knowledge context must be turned off.

    Priority:
        1. Explicit ``disabled`` kwarg (caller override, e.g. test fixture).
        2. ``KNOWLEDGE_CONTEXT_DISABLED`` / ``KNOWLEDGE_LOCAL_DISABLED`` env.
    """
    if disabled is True:
        return True
    if disabled is False:
        return False
    env_map = env if env is not None else os.environ
    return any(_env_truthy(env_map.get(name)) for name in _DISABLE_ENV_VARS)


def resolve_knowledge_root(
    *,
    knowledge_root: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
) -> str:
    """Resolve the knowledge root, applying env overrides.

    Priority:
        1. Explicit ``knowledge_root`` kwarg (non-empty).
        2. ``KNOWLEDGE_ROOT`` / ``AUTO_DEV_KNOWLEDGE_ROOT`` env.
        3. ``~/Documents/knowledge`` (via KB-001 ``default_knowledge_root``).
    """
    if knowledge_root and knowledge_root.strip():
        return knowledge_root.strip()
    env_map = env if env is not None else os.environ
    for name in _ROOT_ENV_VARS:
        val = env_map.get(name)
        if val and val.strip():
            return val.strip()
    return default_knowledge_root()


# ──────────────────────────────────────────────────────────────────────────────
# Status mapping
# ──────────────────────────────────────────────────────────────────────────────


def _map_query_status_to_data_status(query_status: str) -> str:
    """Translate KB-003 query status → IC-TA-001 data_status vocabulary."""
    if query_status == STATUS_HAS_DATA:
        return DATA_STATUS_FRESH
    if query_status == STATUS_STALE:
        return DATA_STATUS_STALE
    if query_status == STATUS_LOW_CONFIDENCE:
        return DATA_STATUS_STALE  # low-confidence degrades to stale
    if query_status == STATUS_FAILED:
        return DATA_STATUS_FAILED
    # NORMAL_NO_DATA and anything unexpected.
    return DATA_STATUS_MISSING


def _data_status_for_disabled() -> str:
    return DATA_STATUS_SKIPPED


# ──────────────────────────────────────────────────────────────────────────────
# Slim result shaping — no full page body ever leaves this module
# ──────────────────────────────────────────────────────────────────────────────


def _slim_match(match_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Reduce a KB-003 match dict to the controller-safe fields.

    Drops ``sources`` body links that could leak long原文；keeps title / path /
    updated_at / confidence / summary_snippet / risks / negative flags /
    matched_by. ``summary_snippet`` is hard-capped at
    ``_SUMMARY_SNIPPET_MAX_CHARS``.
    """
    summary = str(match_dict.get("summary") or "")
    if len(summary) > _SUMMARY_SNIPPET_MAX_CHARS:
        summary = summary[: _SUMMARY_SNIPPET_MAX_CHARS].rstrip() + "…"
    return {
        "rel_path": str(match_dict.get("rel_path") or ""),
        "title": str(match_dict.get("title") or ""),
        "page_type": str(match_dict.get("page_type") or "unclassified"),
        "summary_snippet": summary,
        "themes": list(match_dict.get("themes") or [])[:5],
        "symbols": list(match_dict.get("symbols") or [])[:5],
        "risks": list(match_dict.get("risks") or [])[:_MAX_RISKS_PER_HIT],
        "updated_at": match_dict.get("updated_at"),
        "confidence": str(match_dict.get("confidence") or "low"),
        "machine_readiness": str(match_dict.get("machine_readiness") or "low"),
        "is_stale": bool(match_dict.get("is_stale") or False),
        "is_low_confidence": bool(match_dict.get("is_low_confidence") or False),
        "is_to_be_supplemented": bool(match_dict.get("is_to_be_supplemented") or False),
        "matched_by": list(match_dict.get("matched_by") or []),
    }


def _shape_query_result(
    result: LocalKnowledgeQueryResult,
    *,
    as_of: str,
    query_desc: Dict[str, Any],
    knowledge_root: str,
    notes: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Shape a :class:`LocalKnowledgeQueryResult` into the API payload.

    The payload always carries: ``source``, ``as_of``, ``data_status``,
    ``vendor``, ``knowledge_root``, ``query``, ``hit_count``,
    ``fresh_hit_count``, ``stale_hit_count``, ``low_confidence_hit_count``,
    ``has_fresh_hit``, ``themes``, ``summary_lines``, ``risks``,
    ``confidence``, ``updated_at``, ``hits``, ``errors``, plus the KB-004
    ``score`` digest (without strong action verbs).
    """
    data_status = _map_query_status_to_data_status(result.status)
    slim_hits = [_slim_match(m.to_dict()) for m in result.matched_pages]

    fresh_count = sum(
        1 for m in result.matched_pages
        if not (m.is_stale or m.is_low_confidence or m.is_to_be_supplemented)
    )
    stale_count = sum(
        1 for m in result.matched_pages
        if m.is_stale and not (m.is_low_confidence or m.is_to_be_supplemented)
    )
    low_count = sum(
        1 for m in result.matched_pages
        if m.is_low_confidence or m.is_to_be_supplemented
    )

    score_digest = compute_local_knowledge_score(result)

    if notes is not None and result.errors:
        notes.append(
            f"local_knowledge: {len(result.errors)} parse error(s) during scan"
        )

    return {
        "source": CONTEXT_SOURCE,
        "as_of": as_of,
        "data_status": data_status,
        "vendor": VENDOR,
        "endpoint": "wiki/investment",
        "knowledge_root": knowledge_root,
        "query": dict(query_desc),
        "status": result.status,
        "hit_count": len(result.matched_pages),
        "fresh_hit_count": fresh_count,
        "stale_hit_count": stale_count,
        "low_confidence_hit_count": low_count,
        "has_fresh_hit": fresh_count > 0,
        "themes": list(result.themes)[:_MAX_THEMES_PER_QUERY],
        "summary_lines": list(result.summary)[:3],
        "risks": list(result.risks)[:_MAX_RISKS_PER_HIT],
        "confidence": result.confidence,
        "updated_at": result.updated_at,
        "hits": slim_hits,
        # KB-004 score digest (research-priority hint only, no trade verbs).
        "score": {
            "local_knowledge_score": score_digest["local_knowledge_score"],
            "knowledge_hit_count": score_digest["knowledge_hit_count"],
            "fresh_hit_count": score_digest["fresh_hit_count"],
            "stale_hit_count": score_digest["stale_hit_count"],
            "low_confidence_hit_count": score_digest["low_confidence_hit_count"],
            "has_hit": score_digest["has_hit"],
            "local_knowledge_summary": score_digest["local_knowledge_summary"],
        },
        "errors": list(result.errors)[:5],
        "read_only": True,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Disabled / empty payloads
# ──────────────────────────────────────────────────────────────────────────────


def _empty_payload(
    *,
    as_of: str,
    knowledge_root: str,
    query_desc: Dict[str, Any],
    data_status: str,
    errors: Optional[List[str]] = None,
) -> Dict[str, Any]:
    if data_status not in ALLOWED_DATA_STATUSES:
        data_status = DATA_STATUS_MISSING
    return {
        "source": CONTEXT_SOURCE,
        "as_of": as_of,
        "data_status": data_status,
        "vendor": VENDOR,
        "endpoint": "wiki/investment",
        "knowledge_root": knowledge_root,
        "query": dict(query_desc),
        "status": STATUS_NORMAL_NO_DATA,
        "hit_count": 0,
        "fresh_hit_count": 0,
        "stale_hit_count": 0,
        "low_confidence_hit_count": 0,
        "has_fresh_hit": False,
        "themes": [],
        "summary_lines": [],
        "risks": [],
        "confidence": "low",
        "updated_at": None,
        "hits": [],
        "score": {
            "local_knowledge_score": 0.0,
            "knowledge_hit_count": 0,
            "fresh_hit_count": 0,
            "stale_hit_count": 0,
            "low_confidence_hit_count": 0,
            "has_hit": False,
            "local_knowledge_summary": "",
        },
        "errors": list(errors or [])[:5],
        "read_only": True,
    }


def _disabled_payload(
    *,
    as_of: str,
    query_desc: Dict[str, Any],
) -> Dict[str, Any]:
    """Stable payload returned when local knowledge is administratively off."""
    return _empty_payload(
        as_of=as_of,
        knowledge_root="",
        query_desc=query_desc,
        data_status=_data_status_for_disabled(),
        errors=["local_knowledge_context disabled by config"],
    )


# ──────────────────────────────────────────────────────────────────────────────
# Public API: search_local_knowledge (endpoint helper)
# ──────────────────────────────────────────────────────────────────────────────


def _split_multi(value: Optional[str]) -> List[str]:
    """Split a comma / whitespace separated string into a clean list."""
    if not value:
        return []
    out: List[str] = []
    for piece in str(value).replace("，", ",").split(","):
        piece = piece.strip()
        if piece:
            out.append(piece)
    return out


def _clamp_max_pages(max_pages: Optional[int]) -> int:
    if max_pages is None:
        return DEFAULT_MAX_PAGES
    try:
        value = int(max_pages)
    except (TypeError, ValueError):
        return DEFAULT_MAX_PAGES
    if value <= 0:
        return DEFAULT_MAX_PAGES
    return min(value, _ABSOLUTE_MAX_PAGES)


def search_local_knowledge(
    *,
    symbol: Optional[str] = None,
    name: Optional[str] = None,
    themes: Optional[Sequence[str]] = None,
    tags: Optional[Sequence[str]] = None,
    max_pages: Optional[int] = None,
    knowledge_root: Optional[str] = None,
    disabled: Optional[bool] = None,
    as_of: Optional[str] = None,
    notes: Optional[List[str]] = None,
    include_runtime_meta: bool = True,
) -> Dict[str, Any]:
    """Search ``wiki/investment`` and return a controller-safe summary.

    Used by the ``GET /v1/knowledge/local/search`` endpoint and by the IC
    context ``local_knowledge_hits`` bucket. Never raises; degrades to
    ``data_status=failed`` / ``skipped`` / ``missing`` as appropriate.
    """
    timestamp = as_of or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    symbol = (symbol or "").strip() or None
    name = (name or "").strip() or None
    themes_list = [str(t).strip() for t in (themes or []) if str(t).strip()]
    tags_list = [str(t).strip() for t in (tags or []) if str(t).strip()]

    query_desc: Dict[str, Any] = {}
    if symbol:
        query_desc["symbol"] = symbol
    if name:
        query_desc["name"] = name
    if themes_list:
        query_desc["themes"] = list(themes_list)
    if tags_list:
        query_desc["tags"] = list(tags_list)

    runtime_meta = (
        _tradeflow_meta("local_knowledge_search") if include_runtime_meta else None
    )

    if is_local_knowledge_disabled(disabled=disabled):
        if notes is not None:
            notes.append("local_knowledge: disabled by config")
        payload = _disabled_payload(as_of=timestamp, query_desc=query_desc)
        if runtime_meta is not None:
            payload["runtime_tier_meta"] = runtime_meta
        return payload

    if not any([symbol, name, themes_list, tags_list]):
        # No query condition → don't scan the whole library; return missing.
        payload = _empty_payload(
            as_of=timestamp,
            knowledge_root="",
            query_desc=query_desc,
            data_status=DATA_STATUS_MISSING,
            errors=["no query condition provided (symbol/name/themes/tags)"],
        )
        if runtime_meta is not None:
            payload["runtime_tier_meta"] = runtime_meta
        return payload

    root = resolve_knowledge_root(knowledge_root=knowledge_root)
    pages_limit = _clamp_max_pages(max_pages)

    try:
        result = query_local_knowledge(
            root,
            symbol=symbol,
            name=name,
            themes=themes_list or None,
            tags=tags_list or None,
            max_pages=pages_limit,
        )
    except Exception as exc:  # pragma: no cover - defensive: KB-003 already swallows
        logger.warning("[kb-006] local knowledge query failed: %s", exc)
        payload = _empty_payload(
            as_of=timestamp,
            knowledge_root=root,
            query_desc=query_desc,
            data_status=DATA_STATUS_FAILED,
            errors=[f"query_local_knowledge failed: {type(exc).__name__}: {exc}"],
        )
        if runtime_meta is not None:
            payload["runtime_tier_meta"] = runtime_meta
        return payload

    payload = _shape_query_result(
        result,
        as_of=timestamp,
        query_desc=query_desc,
        knowledge_root=root,
        notes=notes,
    )
    if runtime_meta is not None:
        payload["runtime_tier_meta"] = runtime_meta
    return payload


# ──────────────────────────────────────────────────────────────────────────────
# Public API: collect_local_knowledge_hits (IC context bucket)
# ──────────────────────────────────────────────────────────────────────────────


def _hit_digest(symbol: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Reduce a search payload to a per-symbol digest for the IC bucket."""
    hits = payload.get("hits", [])
    top_themes: List[str] = []
    seen: set[str] = set()
    for h in hits:
        for t in h.get("themes") or []:
            if t and t not in seen:
                seen.add(t)
                top_themes.append(t)
            if len(top_themes) >= 5:
                break
        if len(top_themes) >= 5:
            break
    return {
        "symbol": symbol,
        "has_fresh_hit": bool(payload.get("has_fresh_hit")),
        "hit_count": int(payload.get("hit_count") or 0),
        "fresh_hit_count": int(payload.get("fresh_hit_count") or 0),
        "stale_hit_count": int(payload.get("stale_hit_count") or 0),
        "low_confidence_hit_count": int(payload.get("low_confidence_hit_count") or 0),
        "data_status": payload.get("data_status"),
        "confidence": payload.get("confidence"),
        "updated_at": payload.get("updated_at"),
        "themes": top_themes,
        "summary_lines": list(payload.get("summary_lines") or [])[:2],
        "top_hits": [
            {
                "rel_path": h.get("rel_path"),
                "title": h.get("title"),
                "updated_at": h.get("updated_at"),
                "confidence": h.get("confidence"),
                "is_stale": h.get("is_stale"),
                "is_low_confidence": h.get("is_low_confidence"),
                "is_to_be_supplemented": h.get("is_to_be_supplemented"),
            }
            for h in hits[:3]
        ],
        "score": payload.get("score") or {},
        "source": CONTEXT_SOURCE,
    }


def collect_local_knowledge_hits(
    *,
    symbols: Optional[Sequence[str]] = None,
    themes: Optional[Sequence[str]] = None,
    knowledge_root: Optional[str] = None,
    disabled: Optional[bool] = None,
    as_of: Optional[str] = None,
    notes: Optional[List[str]] = None,
    max_symbols: int = _MAX_HITS_PER_CONTEXT_BUCKET,
) -> Dict[str, Any]:
    """Build the IC context ``local_knowledge_hits`` bucket.

    For each input symbol (and an optional theme query), runs a KB-003 lookup
    and returns a slim per-symbol digest. Themes are also queried once (as a
    single ``themes`` search) so the controller can see cross-symbol theme
    hits even when no individual symbol matched.

    The bucket is always present; on disabled / failure it degrades to
    ``data_status=skipped`` / ``failed`` with an empty ``items`` list.
    """
    timestamp = as_of or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    clean_symbols = [s for s in (symbols or []) if s and str(s).strip()]
    clean_themes = [t for t in (themes or []) if t and str(t).strip()]

    if is_local_knowledge_disabled(disabled=disabled):
        if notes is not None:
            notes.append("local_knowledge_hits: disabled by config")
        return {
            "source": CONTEXT_SOURCE,
            "as_of": timestamp,
            "data_status": _data_status_for_disabled(),
            "knowledge_root": "",
            "symbol_count": 0,
            "theme_count": 0,
            "fresh_symbol_count": 0,
            "stale_symbol_count": 0,
            "items": [],
            "theme_query": None,
            "errors": ["local_knowledge_context disabled by config"],
            "read_only": True,
        }

    root = resolve_knowledge_root(knowledge_root=knowledge_root)

    items: List[Dict[str, Any]] = []
    fresh_symbols = 0
    stale_symbols = 0
    failed_symbols = 0
    seen_symbols: set[str] = set()
    errors: List[str] = []

    for symbol in clean_symbols[:max_symbols]:
        symbol = symbol.strip()
        if not symbol or symbol in seen_symbols:
            continue
        seen_symbols.add(symbol)
        try:
            payload = search_local_knowledge(
                symbol=symbol,
                knowledge_root=root,
                disabled=False,
                as_of=timestamp,
                max_pages=DEFAULT_MAX_PAGES,
            )
        except Exception as exc:  # pragma: no cover - defensive
            errors.append(f"{symbol}: lookup failed: {type(exc).__name__}")
            failed_symbols += 1
            continue
        if not payload.get("hits") and payload.get("data_status") == DATA_STATUS_FAILED:
            errors.append(f"{symbol}: query returned FAILED")
            failed_symbols += 1
            continue
        digest = _hit_digest(symbol, payload)
        if digest["hit_count"] <= 0:
            continue
        items.append(digest)
        if digest["has_fresh_hit"]:
            fresh_symbols += 1
        elif digest["hit_count"] > 0:
            stale_symbols += 1

    theme_query_payload: Optional[Dict[str, Any]] = None
    if clean_themes:
        try:
            theme_query_payload = search_local_knowledge(
                themes=clean_themes,
                knowledge_root=root,
                disabled=False,
                as_of=timestamp,
                max_pages=DEFAULT_MAX_PAGES,
            )
        except Exception as exc:  # pragma: no cover - defensive
            errors.append(f"theme query failed: {type(exc).__name__}")

    # Derive overall data_status:
    #   - FRESH if any symbol returned fresh hits OR the theme query has fresh
    #     hits.
    #   - STALE if some hits exist but none are fresh (stale/low-confidence
    #     only).
    #   - MISSING if nothing was found at all.
    theme_has_fresh = bool(
        theme_query_payload and theme_query_payload.get("has_fresh_hit")
    )
    theme_has_any = bool(
        theme_query_payload and int(theme_query_payload.get("hit_count") or 0) > 0
    )
    theme_failed = bool(
        theme_query_payload and theme_query_payload.get("data_status") == DATA_STATUS_FAILED
    )
    if theme_failed:
        errors.append("theme query returned FAILED")
    any_hit = bool(items) or theme_has_any
    if fresh_symbols or theme_has_fresh:
        data_status = DATA_STATUS_FRESH
    elif any_hit:
        data_status = DATA_STATUS_STALE
    elif failed_symbols or theme_failed or errors:
        data_status = DATA_STATUS_FAILED
    else:
        data_status = DATA_STATUS_MISSING

    if errors and notes is not None:
        notes.append(f"local_knowledge_hits: {len(errors)} error(s) during lookup")

    return {
        "source": CONTEXT_SOURCE,
        "as_of": timestamp,
        "data_status": data_status,
        "knowledge_root": root,
        "symbol_count": len(items),
        "theme_count": len(clean_themes),
        "fresh_symbol_count": fresh_symbols,
        "stale_symbol_count": stale_symbols,
        "failed_symbol_count": failed_symbols,
        "items": items,
        "theme_query": _slim_theme_query(theme_query_payload) if theme_query_payload else None,
        "errors": errors[:5],
        "read_only": True,
    }


def _slim_theme_query(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Reduce the theme-cross search payload to fields the controller needs."""
    return {
        "data_status": payload.get("data_status"),
        "hit_count": int(payload.get("hit_count") or 0),
        "fresh_hit_count": int(payload.get("fresh_hit_count") or 0),
        "stale_hit_count": int(payload.get("stale_hit_count") or 0),
        "low_confidence_hit_count": int(payload.get("low_confidence_hit_count") or 0),
        "has_fresh_hit": bool(payload.get("has_fresh_hit")),
        "themes": list(payload.get("themes") or [])[:_MAX_THEMES_PER_QUERY],
        "summary_lines": list(payload.get("summary_lines") or [])[:2],
        "top_hits": [
            {
                "rel_path": h.get("rel_path"),
                "title": h.get("title"),
                "updated_at": h.get("updated_at"),
                "confidence": h.get("confidence"),
            }
            for h in (payload.get("hits") or [])[:3]
        ],
        "score": payload.get("score") or {},
    }


# ──────────────────────────────────────────────────────────────────────────────
# Safety helpers
# ──────────────────────────────────────────────────────────────────────────────


def assert_no_strong_action_verbs(payload: Dict[str, Any]) -> None:
    """Test helper: assert no synthesised strong action verb leaked.

    Only checks synthesised free-text fields (``summary_lines`` / ``errors`` /
    ``score.local_knowledge_summary`` / theme_query summary). Page ``title`` /
    ``risks`` come from the wiki itself and are not controller-synthesised;
    they're still scanned so we can catch regressions early.
    """
    chunks: List[str] = []
    for line in payload.get("summary_lines") or []:
        chunks.append(str(line))
    for err in payload.get("errors") or []:
        chunks.append(str(err))
    score = payload.get("score") or {}
    if isinstance(score, dict):
        chunks.append(str(score.get("local_knowledge_summary") or ""))
    theme_q = payload.get("theme_query") or {}
    if isinstance(theme_q, dict):
        for line in theme_q.get("summary_lines") or []:
            chunks.append(str(line))
    for pattern in _STRONG_ACTION_PATTERNS:
        for chunk in chunks:
            if pattern in chunk:
                raise AssertionError(
                    f"strong action verb leaked into local_knowledge payload: {pattern!r}"
                )


__all__ = [
    "ALLOWED_DATA_STATUSES",
    "CONTEXT_SOURCE",
    "DEFAULT_MAX_PAGES",
    "DATA_STATUS_FAILED",
    "DATA_STATUS_FRESH",
    "DATA_STATUS_MISSING",
    "DATA_STATUS_SKIPPED",
    "DATA_STATUS_STALE",
    "VENDOR",
    "assert_no_strong_action_verbs",
    "collect_local_knowledge_hits",
    "is_local_knowledge_disabled",
    "resolve_knowledge_root",
    "search_local_knowledge",
]
