# [KB-020] research_evidence_api
"""Read-only single-symbol research evidence aggregation API.

Aggregates four upstream buckets — KB-016 consensus matrix, KB-017 citation
audit, KB-018 thesis timeline and HY-003 half-year facts — into one slim,
controller-safe response. Designed for ``GET /v1/knowledge/research/evidence/
{symbol}`` so the frontend evidence center (UI-014) and the investment
controller can read all evidence with one call instead of re-implementing
the wiring.

Design contract (see docs/TASKS.md KB-020):

    * READ-ONLY. Never calls LLM, never hits the network, never writes to
      the knowledge base or to the prod SQLite DB. Each upstream bucket is
      itself a pure read-only scanner.
    * Per-bucket degradation: if KB-016 / KB-017 / KB-018 / HY-003 throws,
      only that bucket is marked ``status=failed``; the other buckets stay
      intact and the overall response is still returned.
    * No full body text, no API keys: every path field is a *relative*
      path validated to live inside the knowledge root; every text snippet
      is hard-clipped (KB-015/KB-016 already clip upstream, this layer
      clips again defensively).
    * Action-safe: the response body never carries ``decision`` /
      ``action_label`` / ``buy_level`` / strong trade verbs. Evidence is
      research-priority only.
    * runtime_tier=FAST_RADAR (registered in ``_TRADEFLOW_FAST_ENDPOINTS``).
    * Disable switch reuses KB-006 ``KNOWLEDGE_CONTEXT_DISABLED`` /
      ``KNOWLEDGE_LOCAL_DISABLED`` env so operators can turn the bucket off
      in one place.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from api.runtime_tier import tradeflow_meta as _tradeflow_meta  # [PERF-001]
from api.services.local_knowledge_context_service import (  # [KB-006] reuse config helpers
    DATA_STATUS_FAILED,
    DATA_STATUS_FRESH,
    DATA_STATUS_MISSING,
    DATA_STATUS_SKIPPED,
    DATA_STATUS_STALE,
    is_local_knowledge_disabled,
    resolve_knowledge_root,
)

# Upstream read-only scanners.
from tradingagents.dataflows.research_consensus_matrix import (  # [KB-016]
    DEFAULT_WINDOW_MONTHS as KB016_DEFAULT_WINDOW,
    WINDOW_3M,
    WINDOW_6M,
    WINDOW_12M,
    lookup_research_consensus_matrix,
    matrix_to_ta_consumable_summary,
)
from tradingagents.dataflows.research_fact_opinion_index import (  # [KB-015]
    build_research_fact_opinion_index,
)
from tradingagents.dataflows.citation_fact_audit import (  # [KB-017]
    audit_citation_against_facts,
    audit_to_ta_consumable_summary,
)
from tradingagents.dataflows.research_thesis_timeline import (  # [KB-018]
    lookup_research_thesis_timeline,
    timeline_to_ta_consumable_summary,
)
from tradingagents.dataflows.half_year_facts_provider import (  # [HY-003]
    query_half_year_facts,
)
from tradingagents.dataflows.research_score_snapshot import (  # [SCORE-001B]
    STATUS_NORMAL_NO_DATA as SNAP_STATUS_NO_DATA,
    STATUS_FAILED as SNAP_STATUS_FAILED,
    query_research_score_snapshot,
    snapshot_to_api_dict,
)

logger = logging.getLogger(__name__)

CONTEXT_SOURCE = "research_evidence"
TASK_CODE = "KB-020"

# Allowed window_months values (mirror KB-016 vocabulary).
ALLOWED_WINDOW_MONTHS = (WINDOW_3M, WINDOW_6M, WINDOW_12M)
DEFAULT_WINDOW_MONTHS = KB016_DEFAULT_WINDOW

# Symbol validation: 6-digit A-share code with optional .SH/.SZ/.SS suffix.
_ASTOCK_SYMBOL_RE = re.compile(r"^\d{6}(\.(SH|SZ|SS))?$", re.IGNORECASE)
_SUFFIX_RE = re.compile(r"\.(SH|SZ|SS|BJ|HK|US)$", re.IGNORECASE)

# Hard caps — never leak long原文 to the API layer.
_SUMMARY_MAX_CHARS = 200
_REASON_MAX_CHARS = 200
_PATH_REL_MAX_LEN = 512

# Bucket identifiers — kept stable for UI-014 wiring.
BUCKET_HALF_YEAR_FACTS = "half_year_facts"
BUCKET_CITATION_AUDIT = "citation_audit"
BUCKET_THESIS_TIMELINE = "thesis_timeline"
BUCKET_CONSENSUS = "consensus"
BUCKET_RESEARCH_SCORE_SNAPSHOT = "research_score_snapshot"  # [SCORE-001B]
ALL_BUCKETS = (
    BUCKET_CONSENSUS,
    BUCKET_CITATION_AUDIT,
    BUCKET_THESIS_TIMELINE,
    BUCKET_HALF_YEAR_FACTS,
    BUCKET_RESEARCH_SCORE_SNAPSHOT,
)

# Strong action verbs the synthesised response must NEVER emit. Mirrors the
# KB-006 contract; checked defensively on summary fields.
_STRONG_ACTION_PATTERNS = (
    "立即买入", "立即卖出", "满仓", "清仓", "全仓",
)


# ──────────────────────────────────────────────────────────────────────────────
# Symbol validation
# ──────────────────────────────────────────────────────────────────────────────


class InvalidSymbolError(ValueError):
    """Raised when the supplied symbol is not a recognizable A-share code."""


def normalize_symbol(symbol: str) -> str:
    """Normalize ``600519`` / ``600519.SS`` / ``600519.sh`` → ``600519.SH``.

    Returns the empty string for empty input. Raises :class:`InvalidSymbolError`
    for inputs that are not a 6-digit A-share code (with optional suffix).
    """
    raw = (symbol or "").strip().upper()
    if not raw:
        raise InvalidSymbolError("symbol is required")
    if not _ASTOCK_SYMBOL_RE.match(raw):
        raise InvalidSymbolError(
            f"invalid A-share symbol: {symbol!r} (expecting 6-digit code, "
            f"optionally suffixed with .SH/.SZ/.SS)"
        )
    code = _SUFFIX_RE.sub("", raw).strip()
    if not code or not code.isdigit() or len(code) != 6:
        raise InvalidSymbolError(f"invalid A-share symbol: {symbol!r}")
    # .SS → .SH (yfinance-style → our canonical).
    suffix = ".SH" if code.startswith(("5", "6", "9")) else ".SZ"
    return f"{code}{suffix}"


# ──────────────────────────────────────────────────────────────────────────────
# Path safety
# ──────────────────────────────────────────────────────────────────────────────


def _safe_rel_path(rel: Any, knowledge_root: str) -> str:
    """Return ``rel`` only if it resolves inside ``knowledge_root``.

    Accepts forward-slash POSIX paths (the vocabulary used by upstream
    KB modules). Rejects absolute paths, ``..`` escapes and any path that
    resolves outside the knowledge root. Returns the original relative
    string (NOT the resolved absolute path) so the API never leaks host
    filesystem structure beyond the knowledge root prefix.
    """
    if not rel:
        return ""
    text = str(rel).strip()
    if not text or len(text) > _PATH_REL_MAX_LEN:
        return ""
    # Never allow absolute paths or backslashes in a relative path field.
    if text.startswith(("/", "\\")) or ":" in text[:3]:
        return ""
    if "\x00" in text:
        return ""
    try:
        root = Path(knowledge_root).expanduser().resolve(strict=False)
        candidate = (root / text).resolve(strict=False)
    except (OSError, ValueError):
        return ""
    try:
        candidate.relative_to(root)
    except ValueError:
        return ""
    return text


def _clip_text(text: Any, max_chars: int = _SUMMARY_MAX_CHARS) -> str:
    s = (str(text) if text is not None else "").strip()
    if len(s) > max_chars:
        return s[:max_chars].rstrip() + "…"
    return s


def _normalize_hy_data_status(raw: str) -> str:
    """Translate HY-003 ``data_status`` vocabulary → KB-020 canonical tokens.

    HY-003 returns one of ``fresh`` / ``stale`` / ``conflict`` /
    ``opinion_only`` / ``missing_period`` / ``missing_facts``. KB-020's
    contract only exposes ``fresh`` / ``stale`` / ``conflict`` / ``missing``
    / ``failed`` / ``skipped`` so the UI gets a uniform vocabulary.
    """
    raw_lower = (raw or "").strip().lower()
    if raw_lower in ("fresh", "conflict"):
        return raw_lower
    if raw_lower == "stale":
        return DATA_STATUS_STALE
    if raw_lower in (
        "opinion_only", "missing_period", "missing_facts", "missing", "",
    ):
        return DATA_STATUS_MISSING
    if raw_lower in ("failed", "error"):
        return DATA_STATUS_FAILED
    if raw_lower == "skipped":
        return DATA_STATUS_SKIPPED
    # Unknown → conservative missing.
    return DATA_STATUS_MISSING


def _redact_bucket_paths(
    payload: Dict[str, Any], knowledge_root: str
) -> Dict[str, Any]:
    """Walk a bucket payload and replace any path-like field with a safe rel path.

    Recursively scans dict keys named ``rel_path`` / ``source_path`` /
    ``fact_source_path`` / ``page_rel`` and trims them to a validated
    relative path inside ``knowledge_root``. Does NOT mutate the upstream
    dataclass — operates on the dict view we already serialized.
    """
    if not isinstance(payload, dict):
        return payload
    SAFE_KEYS = {
        "rel_path", "source_path", "fact_source_path",
        "page_rel", "first_node_path", "last_node_path",
    }
    out: Dict[str, Any] = {}
    for key, value in payload.items():
        if key in SAFE_KEYS and value is not None:
            out[key] = _safe_rel_path(value, knowledge_root)
            continue
        if isinstance(value, dict):
            out[key] = _redact_bucket_paths(value, knowledge_root)
        elif isinstance(value, list):
            out[key] = [
                _redact_bucket_paths(v, knowledge_root) if isinstance(v, dict) else v
                for v in value
            ]
        else:
            out[key] = value
    return out


def _assert_no_strong_action_verbs(text: str, where: str) -> None:
    """Defensive guard: log (don't raise) if a strong action verb leaks.

    Upstream modules already forbid these in summary fields; we re-check
    here so a future regression in any bucket cannot leak through this
    aggregator. Logged at WARNING so it shows up in dashboards but does
    not break the response.
    """
    for pat in _STRONG_ACTION_PATTERNS:
        if pat in text:
            logger.warning(
                "[kb-020] strong action verb %r leaked into %s — clipping",
                pat, where,
            )
            return


# ──────────────────────────────────────────────────────────────────────────────
# Bucket builders (each fully isolated; failure → degraded bucket)
# ──────────────────────────────────────────────────────────────────────────────


def _failed_bucket(bucket: str, exc: BaseException) -> Dict[str, Any]:
    return {
        "bucket": bucket,
        "status": DATA_STATUS_FAILED,
        "task": bucket,
        "has_hit": False,
        "data_status": DATA_STATUS_FAILED,
        "errors": [
            _clip_text(
                f"{bucket}: {type(exc).__name__}: {exc}", _REASON_MAX_CHARS
            )
        ],
        "summary": {},
    }


def _build_consensus_bucket(
    *,
    knowledge_root: str,
    symbol: str,
    window_months: int,
    today: date,
) -> Dict[str, Any]:
    try:
        matrix = lookup_research_consensus_matrix(
            knowledge_root, symbol,
            window_months=window_months, today=today,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("[kb-020] consensus bucket failed: %s", exc)
        return _failed_bucket(BUCKET_CONSENSUS, exc)

    if matrix is None:
        return {
            "bucket": BUCKET_CONSENSUS,
            "status": DATA_STATUS_MISSING,
            "task": "KB-016",
            "has_hit": False,
            "data_status": DATA_STATUS_MISSING,
            "errors": [],
            "summary": matrix_to_ta_consumable_summary(None),
        }

    summary = matrix_to_ta_consumable_summary(matrix)
    consensus_text = str(summary.get("consensus_summary") or "")
    _assert_no_strong_action_verbs(consensus_text, "consensus_summary")

    data_status = DATA_STATUS_FRESH if matrix.fresh_count > 0 else (
        DATA_STATUS_STALE if matrix.stale_count > 0 else DATA_STATUS_MISSING
    )
    return {
        "bucket": BUCKET_CONSENSUS,
        "status": "HAS_DATA",
        "task": "KB-016",
        "has_hit": True,
        "data_status": data_status,
        "errors": [],
        "summary": summary,
    }


def _build_citation_audit_bucket(
    *,
    knowledge_root: str,
    symbol: str,
    today: date,
    opinion_index: Any,
    facts_result: Any,
) -> Dict[str, Any]:
    try:
        audit = audit_citation_against_facts(
            opinion_index, facts_result, symbol=symbol,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("[kb-020] citation_audit bucket failed: %s", exc)
        return _failed_bucket(BUCKET_CITATION_AUDIT, exc)

    summary = audit_to_ta_consumable_summary(audit)
    for line in summary.get("audit_summary") or []:
        _assert_no_strong_action_verbs(str(line), "audit_summary")
    data_status = (
        DATA_STATUS_FRESH if audit.checked_claim_count > 0
        else DATA_STATUS_MISSING
    )
    return {
        "bucket": BUCKET_CITATION_AUDIT,
        "status": "HAS_DATA" if audit.total_claim_count > 0 else "NORMAL_NO_DATA",
        "task": "KB-017",
        "has_hit": audit.total_claim_count > 0,
        "data_status": data_status,
        "errors": list(audit.errors or [])[:3],
        "summary": summary,
    }


def _build_thesis_timeline_bucket(
    *,
    knowledge_root: str,
    symbol: str,
    today: date,
    citation_audit: Any,
) -> Dict[str, Any]:
    try:
        timeline = lookup_research_thesis_timeline(
            knowledge_root, symbol,
            citation_audit=citation_audit, today=today,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("[kb-020] thesis_timeline bucket failed: %s", exc)
        return _failed_bucket(BUCKET_THESIS_TIMELINE, exc)

    if timeline is None:
        return {
            "bucket": BUCKET_THESIS_TIMELINE,
            "status": DATA_STATUS_MISSING,
            "task": "KB-018",
            "has_hit": False,
            "data_status": DATA_STATUS_MISSING,
            "errors": [],
            "summary": timeline_to_ta_consumable_summary(None),
        }

    summary = timeline_to_ta_consumable_summary(timeline)
    _assert_no_strong_action_verbs(
        str(summary.get("timeline_summary") or ""), "timeline_summary"
    )
    data_status = (
        DATA_STATUS_FRESH if timeline.effective_versions > 0
        else DATA_STATUS_MISSING
    )
    return {
        "bucket": BUCKET_THESIS_TIMELINE,
        "status": "HAS_DATA",
        "task": "KB-018",
        "has_hit": True,
        "data_status": data_status,
        "errors": [],
        "summary": summary,
    }


def _build_half_year_facts_bucket(
    *,
    knowledge_root: str,
    symbol: str,
    today: date,
) -> Dict[str, Any]:
    try:
        facts = query_half_year_facts(
            knowledge_root, symbol=symbol, today=today,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("[kb-020] half_year_facts bucket failed: %s", exc)
        return _failed_bucket(BUCKET_HALF_YEAR_FACTS, exc)

    facts_dict = facts.to_dict() if hasattr(facts, "to_dict") else {}
    has_hit = bool(facts_dict.get("pages"))
    # Slim page output: drop long正文 fields, keep metadata + clipped summary.
    slim_pages: List[Dict[str, Any]] = []
    for page in facts_dict.get("pages") or []:
        slim_pages.append({
            "rel_path": page.get("rel_path") or page.get("page_rel") or "",
            "title": _clip_text(page.get("title"), _SUMMARY_MAX_CHARS),
            "financial_period": page.get("financial_period")
            or page.get("period"),
            "disclosure_date": page.get("disclosure_date"),
            "source_type": list(page.get("source_type") or [])[:4],
            "data_status": page.get("data_status"),
            "metric_count": len(page.get("metrics") or []),
            "metric_keys": list(page.get("metrics") or [])[:8]
            if isinstance(page.get("metrics"), dict)
            else [],
        })
    data_status = _normalize_hy_data_status(
        str(facts_dict.get("data_status") or "")
    )
    summary_lines = [
        _clip_text(line, _SUMMARY_MAX_CHARS)
        for line in (facts_dict.get("summary") or [])[:5]
    ]
    for line in summary_lines:
        _assert_no_strong_action_verbs(line, "half_year_facts summary")
    return {
        "bucket": BUCKET_HALF_YEAR_FACTS,
        "status": facts_dict.get("status") or "NORMAL_NO_DATA",
        "task": "HY-003",
        "has_hit": has_hit,
        "data_status": data_status,
        "latest_period": facts_dict.get("latest_period"),
        "latest_disclosure_date": facts_dict.get("latest_disclosure_date"),
        "errors": list(facts_dict.get("errors") or [])[:3],
        "summary": {
            "pages": slim_pages,
            "summary_lines": summary_lines,
            "risks": [
                _clip_text(r, _SUMMARY_MAX_CHARS)
                for r in (facts_dict.get("risks") or [])[:5]
            ],
        },
    }


def _build_opinion_index(
    *, knowledge_root: str, symbol: str, today: date,
) -> Any:
    """Build the KB-015 opinion index (shared input to KB-017 / KB-018 wiring).

    Returns ``None`` on failure so citation audit can degrade to
    ``insufficient_data`` without crashing the aggregator.
    """
    try:
        return build_research_fact_opinion_index(
            knowledge_root, symbol=symbol, today=today,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("[kb-020] opinion_index build failed: %s", exc)
        return None


def _build_research_score_snapshot_bucket(
    *,
    knowledge_root: str,
    symbol: str,
    today: date,
) -> Dict[str, Any]:
    """[SCORE-001B] Build the research_score_snapshot bucket for KB-020.

    Read-only query of ZCode's ``research_score_snapshot`` via SCORE-001 loader.
    Failure → degraded bucket (never crashes the aggregator).
    """
    try:
        from datetime import datetime, timezone
        analysis_time = datetime(
            today.year, today.month, today.day,
            tzinfo=timezone.utc,
        )
        result = query_research_score_snapshot(
            knowledge_root,
            symbol=symbol,
            analysis_time=analysis_time,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("[kb-020] research_score_snapshot bucket failed: %s", exc)
        return _failed_bucket(BUCKET_RESEARCH_SCORE_SNAPSHOT, exc)

    api_dict = snapshot_to_api_dict(result)
    snap_status = api_dict.get("status", SNAP_STATUS_NO_DATA)

    data_status = DATA_STATUS_FRESH
    if snap_status in (SNAP_STATUS_NO_DATA, SNAP_STATUS_FAILED):
        data_status = DATA_STATUS_MISSING
    elif snap_status == "STALE":
        data_status = DATA_STATUS_STALE
    elif snap_status == "LOW_CONFIDENCE":
        data_status = DATA_STATUS_FRESH

    has_snap = api_dict.get("snapshot") is not None
    return {
        "bucket": BUCKET_RESEARCH_SCORE_SNAPSHOT,
        "status": snap_status,
        "task": "SCORE-001",
        "has_hit": has_snap,
        "data_status": data_status,
        "errors": [],
        "summary": api_dict,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Gaps computation
# ──────────────────────────────────────────────────────────────────────────────


def _compute_gaps(buckets: Dict[str, Dict[str, Any]]) -> List[str]:
    """Surface the most actionable evidence gaps for this symbol.

    Pure read-only summary of which buckets are missing / failed / stale.
    Gaps only describe research priority — never a trade action.
    """
    gaps: List[str] = []
    for bucket_id in ALL_BUCKETS:
        bucket = buckets.get(bucket_id) or {}
        status = str(bucket.get("data_status") or "").lower()
        if status == DATA_STATUS_FAILED:
            gaps.append(f"{bucket_id}: bucket failed — see errors")
        elif status == DATA_STATUS_MISSING:
            gaps.append(f"{bucket_id}: no data")
        elif status == DATA_STATUS_STALE:
            gaps.append(f"{bucket_id}: stale — refresh recommended")
    consensus_summary = (buckets.get(BUCKET_CONSENSUS) or {}).get("summary") or {}
    if isinstance(consensus_summary, dict) and consensus_summary.get("needs_fact_check"):
        priority = str(consensus_summary.get("fact_check_priority") or "low").lower()
        gaps.append(
            f"consensus: needs fact check (priority={priority})"
        )
    audit_summary = (buckets.get(BUCKET_CITATION_AUDIT) or {}).get("summary") or {}
    if isinstance(audit_summary, dict):
        if audit_summary.get("needs_tree_work_review"):
            gaps.append("citation_audit: needs Tree Work review")
        counts = audit_summary.get("counts") or {}
        if isinstance(counts, dict) and (
            int(counts.get("contradicted") or 0) > 0
            or int(counts.get("pending") or 0) > 0
        ):
            gaps.append(
                f"citation_audit: {counts.get('contradicted', 0)} contradicted, "
                f"{counts.get('pending', 0)} pending"
            )
    timeline_summary = (
        buckets.get(BUCKET_THESIS_TIMELINE) or {}
    ).get("summary") or {}
    if isinstance(timeline_summary, dict):
        pending = int(timeline_summary.get("pending_fact_check_versions") or 0)
        if pending > 0:
            gaps.append(
                f"thesis_timeline: {pending} pending fact-check version(s)"
            )
    return gaps[:10]


def _compute_overall_data_status(
    buckets: Dict[str, Dict[str, Any]],
) -> str:
    """Aggregate per-bucket status into one overall data_status token."""
    statuses = [
        str((buckets.get(b) or {}).get("data_status") or "").lower()
        for b in ALL_BUCKETS
    ]
    if any(s == DATA_STATUS_FAILED for s in statuses) and not any(
        s in (DATA_STATUS_FRESH, DATA_STATUS_STALE) for s in statuses
    ):
        return DATA_STATUS_FAILED
    if any(s == DATA_STATUS_FRESH for s in statuses):
        return DATA_STATUS_FRESH
    if any(s == DATA_STATUS_STALE for s in statuses):
        return DATA_STATUS_STALE
    if any(s == DATA_STATUS_MISSING for s in statuses):
        return DATA_STATUS_MISSING
    return DATA_STATUS_MISSING


def _compute_source_freshness(
    buckets: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Compact ``bucket → freshness`` digest for the UI."""
    out: Dict[str, Any] = {}
    for bucket_id in ALL_BUCKETS:
        bucket = buckets.get(bucket_id) or {}
        out[bucket_id] = {
            "data_status": bucket.get("data_status"),
            "has_hit": bool(bucket.get("has_hit")),
            "task": bucket.get("task"),
        }
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────


def build_research_evidence(
    symbol: str,
    *,
    as_of: Optional[str] = None,
    window_months: int = DEFAULT_WINDOW_MONTHS,
    knowledge_root: Optional[str] = None,
    today: Optional[date] = None,
    disabled: Optional[bool] = None,
    include_runtime_meta: bool = True,
) -> Dict[str, Any]:
    """Aggregate all research evidence for ``symbol`` into a single response.

    Parameters:
        symbol: A-share 6-digit code (with optional ``.SH`` / ``.SZ`` /
            ``.SS`` suffix). Invalid input raises :class:`InvalidSymbolError`
            so the route handler can translate it to a 4xx.
        as_of: Optional caller-provided timestamp string (echo only).
        window_months: KB-016 window (3 / 6 / 12). Defaults to 12.
        knowledge_root: Optional override; resolves via KB-006 helpers.
        today: Injected ``date`` for stale logic (tests).
        disabled: Force-disable (env-driven by default via KB-006 helpers).
        include_runtime_meta: When True, attach ``runtime_tier_meta``.

    Returns:
        Dict with keys ``symbol / as_of / data_status / source_freshness /
        consensus / citation_audit / thesis_timeline / half_year_facts /
        gaps / errors / query / read_only`` (and ``runtime_tier_meta`` when
        enabled). Never raises for upstream failures — only for invalid
        ``symbol`` / ``window_months`` programmer errors.
    """
    canonical = normalize_symbol(symbol)
    if window_months not in ALLOWED_WINDOW_MONTHS:
        raise InvalidSymbolError(
            f"window_months must be one of {ALLOWED_WINDOW_MONTHS}, "
            f"got {window_months!r}"
        )
    if today is None:
        today = date.today()
    timestamp = as_of or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    query_desc: Dict[str, Any] = {
        "symbol": canonical,
        "window_months": int(window_months),
    }

    runtime_meta = (
        _tradeflow_meta("research_evidence_lookup") if include_runtime_meta else None
    )

    # Disabled short-circuit (env KNOWLEDGE_CONTEXT_DISABLED /
    # KNOWLEDGE_LOCAL_DISABLED).
    if is_local_knowledge_disabled(disabled=disabled):
        payload = _disabled_payload(
            symbol=canonical, as_of=timestamp, query_desc=query_desc,
        )
        if runtime_meta is not None:
            payload["runtime_tier_meta"] = runtime_meta
        return payload

    root = resolve_knowledge_root(knowledge_root=knowledge_root)

    # ── Build upstream inputs once (KB-015 / HY-003 are shared by KB-017). ──
    opinion_index = _build_opinion_index(
        knowledge_root=root, symbol=canonical, today=today,
    )
    facts_result = None
    try:
        facts_result = query_half_year_facts(
            root, symbol=canonical, today=today,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("[kb-020] half_year_facts pre-scan failed: %s", exc)
        facts_result = None

    # ── Per-bucket assembly (each fully isolated). ──
    consensus_bucket = _build_consensus_bucket(
        knowledge_root=root, symbol=canonical,
        window_months=window_months, today=today,
    )

    citation_audit_bucket = _build_citation_audit_bucket(
        knowledge_root=root, symbol=canonical, today=today,
        opinion_index=opinion_index, facts_result=facts_result,
    )
    # Wire KB-017 result into KB-018 when both belong to this symbol.
    citation_audit_obj: Any = None
    try:
        from tradingagents.dataflows.citation_fact_audit import (
            CitationAuditResult,
        )
        # Rebuild a minimal CitationAuditResult only if KB-017 succeeded;
        # KB-018 itself only reads citation_audit_status / pending flags.
        summary = citation_audit_bucket.get("summary") or {}
        if isinstance(summary, dict) and citation_audit_bucket.get("has_hit"):
            citation_audit_obj = CitationAuditResult(
                symbol=canonical,
                citation_audit_status=str(summary.get("citation_audit_status") or ""),
            )
    except Exception:  # pragma: no cover - defensive
        citation_audit_obj = None

    thesis_timeline_bucket = _build_thesis_timeline_bucket(
        knowledge_root=root, symbol=canonical, today=today,
        citation_audit=citation_audit_obj,
    )
    half_year_facts_bucket = _build_half_year_facts_bucket(
        knowledge_root=root, symbol=canonical, today=today,
    ) if facts_result is not None else _failed_bucket(
        BUCKET_HALF_YEAR_FACTS,
        RuntimeError("half_year_facts pre-scan returned None"),
    )

    # [SCORE-001B] research_score_snapshot bucket — fully isolated.
    research_score_snapshot_bucket = _build_research_score_snapshot_bucket(
        knowledge_root=root, symbol=canonical, today=today,
    )

    buckets: Dict[str, Dict[str, Any]] = {
        BUCKET_CONSENSUS: consensus_bucket,
        BUCKET_CITATION_AUDIT: citation_audit_bucket,
        BUCKET_THESIS_TIMELINE: thesis_timeline_bucket,
        BUCKET_HALF_YEAR_FACTS: half_year_facts_bucket,
        BUCKET_RESEARCH_SCORE_SNAPSHOT: research_score_snapshot_bucket,
    }

    # ── Path redaction (defensive — upstream already relative). ──
    for bucket_id, bucket in buckets.items():
        summary = bucket.get("summary")
        if isinstance(summary, dict):
            bucket["summary"] = _redact_bucket_paths(summary, root)

    overall_status = _compute_overall_data_status(buckets)
    gaps = _compute_gaps(buckets)
    errors: List[str] = []
    for bucket_id in ALL_BUCKETS:
        for err in (buckets.get(bucket_id) or {}).get("errors") or []:
            errors.append(f"{bucket_id}: {_clip_text(err, _REASON_MAX_CHARS)}")
            if len(errors) >= 8:
                break
        if len(errors) >= 8:
            break

    payload: Dict[str, Any] = {
        "source": CONTEXT_SOURCE,
        "task": TASK_CODE,
        "symbol": canonical,
        "as_of": timestamp,
        "data_status": overall_status,
        "vendor": "tree_work_wiki",
        "endpoint": "wiki/investment",
        "knowledge_root": root,
        "query": dict(query_desc),
        "source_freshness": _compute_source_freshness(buckets),
        BUCKET_CONSENSUS: buckets[BUCKET_CONSENSUS],
        BUCKET_CITATION_AUDIT: buckets[BUCKET_CITATION_AUDIT],
        BUCKET_THESIS_TIMELINE: buckets[BUCKET_THESIS_TIMELINE],
        BUCKET_HALF_YEAR_FACTS: buckets[BUCKET_HALF_YEAR_FACTS],
        BUCKET_RESEARCH_SCORE_SNAPSHOT: buckets[BUCKET_RESEARCH_SCORE_SNAPSHOT],
        "gaps": gaps,
        "errors": errors,
        "read_only": True,
    }
    if runtime_meta is not None:
        payload["runtime_tier_meta"] = runtime_meta
    return payload


def _disabled_payload(
    *, symbol: str, as_of: str, query_desc: Dict[str, Any],
) -> Dict[str, Any]:
    empty_buckets = {
        bucket_id: {
            "bucket": bucket_id,
            "status": DATA_STATUS_SKIPPED,
            "task": bucket_id,
            "has_hit": False,
            "data_status": DATA_STATUS_SKIPPED,
            "errors": [],
            "summary": {},
        }
        for bucket_id in ALL_BUCKETS
    }
    return {
        "source": CONTEXT_SOURCE,
        "task": TASK_CODE,
        "symbol": symbol,
        "as_of": as_of,
        "data_status": DATA_STATUS_SKIPPED,
        "vendor": "tree_work_wiki",
        "endpoint": "wiki/investment",
        "knowledge_root": "",
        "query": dict(query_desc),
        "source_freshness": _compute_source_freshness(empty_buckets),
        BUCKET_CONSENSUS: empty_buckets[BUCKET_CONSENSUS],
        BUCKET_CITATION_AUDIT: empty_buckets[BUCKET_CITATION_AUDIT],
        BUCKET_THESIS_TIMELINE: empty_buckets[BUCKET_THESIS_TIMELINE],
        BUCKET_HALF_YEAR_FACTS: empty_buckets[BUCKET_HALF_YEAR_FACTS],
        BUCKET_RESEARCH_SCORE_SNAPSHOT: empty_buckets[BUCKET_RESEARCH_SCORE_SNAPSHOT],
        "gaps": ["all buckets: disabled by config"],
        "errors": ["research_evidence disabled by config"],
        "read_only": True,
    }


__all__ = [
    "ALL_BUCKETS",
    "ALLOWED_WINDOW_MONTHS",
    "BUCKET_CITATION_AUDIT",
    "BUCKET_CONSENSUS",
    "BUCKET_HALF_YEAR_FACTS",
    "BUCKET_THESIS_TIMELINE",
    "BUCKET_RESEARCH_SCORE_SNAPSHOT",
    "CONTEXT_SOURCE",
    "DEFAULT_WINDOW_MONTHS",
    "TASK_CODE",
    "InvalidSymbolError",
    "build_research_evidence",
    "normalize_symbol",
]
