"""REPORT-UX-002 — 历史报告动作语义与数据缺口只读迁移预检.

Read-only dry-run audit that scans historical TA reports and reports which ones
can have their DECISION-001 3-layer action semantics
(``research_direction`` / ``execution_action`` / ``action_label``) and DATA-021
field-level ``data_blockers`` + DATA-004 ``raw_evidence`` recovered in-place,
which ones need to be re-run, and which ones can no longer be judged.

What this module does
----------------------
- Scans ``ReportDB`` rows (only ``completed`` reports) using a ``load_only``
  projection, mirroring the read-only pattern in
  ``investment_controller_context._collect_recent_report_data_blockers``.
- Detects three gap classes per report:
    * ``missing_semantics``    — any of the 3 action-semantics fields is empty.
    * ``missing_data_blockers``— ``result_data.data_blockers`` is absent.
    * ``missing_raw_evidence`` — neither ``result_data.raw_evidence`` nor
      ``result_data.metadata.raw_evidence`` is present.
- Re-derives the missing fields **on a copy** (without ever mutating the ORM row
  or calling ``db.commit()``), exactly the way the live read path
  (``report_service.resolve_report_fields`` /
  ``investment_controller_context._collect_recent_report_data_blockers``) does
  for legacy rows, and records what the dry-run would produce.
- Classifies each report into one of four recommendations:
    * ``can_derive``    — the missing fields can be recomputed from the stored
                          ``final_trade_decision`` / ``raw_evidence``; a future
                          backfill (separate task) could populate them.
    * ``needs_rerun``   — at least one gap has no source text to recover from;
                          the report should be regenerated.
    * ``cannot_judge``  — ``result_data`` and ``final_trade_decision`` are both
                          gone, so even a rerun path is unknown.
    * ``ok``            — no gaps detected.

Hard guarantees
---------------
- Never calls ``db.add`` / ``db.commit`` / ``db.delete``.
- Never calls an LLM (``_extract_decision_semantics`` is pure regex/parser).
- Never mutates ``result_data`` of any row (always operates on ``dict(result_data)``).
- Failures per-row are swallowed and counted as ``audit_error`` so one bad row
  cannot abort the whole scan.

# [REPORT-UX-002] report_semantics_audit
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session, load_only

logger = logging.getLogger(__name__)

# ─── Recommendations ─────────────────────────────────────────────────────────

RECOMMENDATION_OK = "ok"
RECOMMENDATION_CAN_DERIVE = "can_derive"  # 可读时补算
RECOMMENDATION_NEEDS_RERUN = "needs_rerun"  # 需要重跑
RECOMMENDATION_CANNOT_JUDGE = "cannot_judge"  # 无法判断
RECOMMENDATION_AUDIT_ERROR = "audit_error"  # 扫描该行时抛错

_ALL_RECOMMENDATIONS = (
    RECOMMENDATION_OK,
    RECOMMENDATION_CAN_DERIVE,
    RECOMMENDATION_NEEDS_RERUN,
    RECOMMENDATION_CANNOT_JUDGE,
    RECOMMENDATION_AUDIT_ERROR,
)

# Cap the scan so the helper stays in the FAST_RADAR budget (cf. PERF-001).
DEFAULT_AUDIT_LIMIT = 500


@dataclass
class ReportAuditItem:
    """One row of the audit. Everything needed to explain *why* an old report
    still shows old (or empty) fields, and what a backfill would recover."""

    report_id: str
    symbol: str
    trade_date: str
    created_at: Optional[str]
    status: str

    # Gap flags (true == field is missing/empty).
    missing_semantics: bool
    missing_data_blockers: bool
    missing_raw_evidence: bool

    # What is currently stored (for explaining "why old UI shows old fields").
    stored_research_direction: str
    stored_execution_action: str
    stored_action_label: str
    stored_decision: str
    has_final_trade_decision: bool
    has_result_data: bool

    # Dry-run recovered values (never written back).
    derived_research_direction: Optional[str] = None
    derived_execution_action: Optional[str] = None
    derived_action_label: Optional[str] = None
    derived_severe_blockers: Optional[int] = None
    derived_blocker_total: Optional[int] = None

    # Verdict + human-readable explanation.
    recommendation: str = RECOMMENDATION_OK
    reasons: List[str] = field(default_factory=list)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_audit(as_of: str, note: str) -> Dict[str, Any]:
    return {
        "source": "report_semantics_audit",
        "as_of": as_of,
        "dry_run": True,
        "scanned_report_count": 0,
        "clean_count": 0,
        "gap_counts": {
            "missing_semantics": 0,
            "missing_data_blockers": 0,
            "missing_raw_evidence": 0,
        },
        "recommendation_counts": {rec: 0 for rec in _ALL_RECOMMENDATIONS},
        "items": [],
        "notes": [note],
    }


def _row_has_semantics(row: Any) -> bool:
    """A report counts as "has semantics" only when all 3 layers are present."""
    return bool(
        getattr(row, "research_direction", None)
        and getattr(row, "execution_action", None)
        and getattr(row, "action_label", None)
    )


def _audit_one_row(row: Any) -> ReportAuditItem:
    """Build an audit item for a single report row, deriving missing fields
    read-only. Never raises — wraps everything in try/except so a malformed row
    is reported as ``audit_error`` rather than aborting the scan."""

    has_result_data = isinstance(getattr(row, "result_data", None), dict)
    result_data: Dict[str, Any] = dict(getattr(row, "result_data", None) or {})
    final_trade_decision = getattr(row, "final_trade_decision", None) or result_data.get(
        "final_trade_decision"
    )
    has_ftd = bool(final_trade_decision)

    missing_semantics = not _row_has_semantics(row)
    missing_data_blockers = not isinstance(result_data.get("data_blockers"), list)

    # raw_evidence presence (DATA-004) — both storage shapes supported.
    raw_evidence = result_data.get("raw_evidence")
    if not isinstance(raw_evidence, dict):
        metadata = result_data.get("metadata")
        if isinstance(metadata, dict):
            raw_evidence = metadata.get("raw_evidence")
    missing_raw_evidence = not isinstance(raw_evidence, dict)

    item = ReportAuditItem(
        report_id=str(getattr(row, "id", "") or ""),
        symbol=str(getattr(row, "symbol", "") or ""),
        trade_date=str(getattr(row, "trade_date", "") or ""),
        created_at=(
            getattr(row, "created_at").isoformat()
            if getattr(row, "created_at", None) is not None
            else None
        ),
        status=str(getattr(row, "status", "") or ""),
        missing_semantics=missing_semantics,
        missing_data_blockers=missing_data_blockers,
        missing_raw_evidence=missing_raw_evidence,
        stored_research_direction=str(getattr(row, "research_direction", "") or ""),
        stored_execution_action=str(getattr(row, "execution_action", "") or ""),
        stored_action_label=str(getattr(row, "action_label", "") or ""),
        stored_decision=str(getattr(row, "decision", "") or ""),
        has_final_trade_decision=has_ftd,
        has_result_data=has_result_data,
    )

    # Dry-run recovery of missing semantics, mirroring
    # ``report_service.resolve_report_fields`` which already does this on the
    # live read path for legacy rows.
    semantics_recoverable = True
    if missing_semantics:
        if has_ftd:
            try:
                from tradingagents.graph.signal_processing import (
                    _extract_decision_semantics,
                )

                semantics = _extract_decision_semantics(final_trade_decision)
                item.derived_research_direction = semantics.research_direction
                item.derived_execution_action = semantics.execution_action
                item.derived_action_label = semantics.action_label
                if not (
                    semantics.research_direction
                    and semantics.execution_action
                    and semantics.action_label
                ):
                    semantics_recoverable = False
                    item.reasons.append(
                        "final_trade_decision 无法解析出完整 3 层语义（可能为空文/中性兜底）"
                    )
            except Exception as exc:
                semantics_recoverable = False
                item.reasons.append(f"语义回放抛错：{exc}")
        else:
            semantics_recoverable = False
            item.reasons.append("缺少 final_trade_decision，无法补算动作语义")

    # Dry-run recovery of missing data_blockers, mirroring
    # ``investment_controller_context._collect_recent_report_data_blockers``
    # which re-runs ``attach_report_data_blockers`` on a copy for legacy rows.
    blocker_recoverable = True
    if missing_data_blockers:
        if has_result_data:
            try:
                from api.services.report_service import attach_report_data_blockers

                enriched = attach_report_data_blockers(dict(result_data))
                blockers = (
                    enriched.get("data_blockers") if isinstance(enriched, dict) else None
                )
                if isinstance(blockers, list):
                    item.derived_blocker_total = len(blockers)
                    item.derived_severe_blockers = sum(
                        1
                        for b in blockers
                        if isinstance(b, dict)
                        and b.get("status") in ("query_failed", "field_missing")
                    )
                else:
                    blocker_recoverable = False
                    item.reasons.append("data_blockers 回放结果为空（result_data 无可用证据）")
            except Exception as exc:
                blocker_recoverable = False
                item.reasons.append(f"data_blockers 回放抛错：{exc}")
        else:
            blocker_recoverable = False
            item.reasons.append("缺少 result_data，无法补算 data_blockers")

    if missing_raw_evidence:
        # raw_evidence is the canonical input to data_blockers; without it any
        # backfilled blocker is necessarily shallow (section-text-only).
        item.reasons.append(
            "缺少 raw_evidence（DATA-004），data_blockers 只能从报告正文兜底"
        )

    # ─── Recommendation: worst case wins ────────────────────────────────────
    has_any_gap = missing_semantics or missing_data_blockers or missing_raw_evidence
    if not has_any_gap:
        item.recommendation = RECOMMENDATION_OK
    elif not has_result_data and not has_ftd:
        # Nothing left to recover from — even a rerun path is unknown.
        item.recommendation = RECOMMENDATION_CANNOT_JUDGE
        item.reasons.insert(0, "result_data 与 final_trade_decision 均缺失，无法判断")
    elif not semantics_recoverable or (
        # Blocker gap that we can only close shallowly is still recoverable, but
        # if raw_evidence is gone *and* there is no section text either, the
        # blocker layer is genuinely unrecoverable → needs_rerun.
        missing_data_blockers
        and not blocker_recoverable
        and missing_raw_evidence
        and not _has_any_section_text(result_data)
    ):
        item.recommendation = RECOMMENDATION_NEEDS_RERUN
        item.reasons.insert(0, "至少一项缺口无法从现有文本补回，建议重跑该报告（needs_rerun）")
    else:
        item.recommendation = RECOMMENDATION_CAN_DERIVE
        item.reasons.insert(0, "缺失字段可在只读副本上补算，后续可走独立 backfill 写回")

    return item


def _has_any_section_text(result_data: Dict[str, Any]) -> bool:
    for key in (
        "market_report",
        "volume_price_report",
        "smart_money_report",
        "news_report",
        "fundamentals_report",
    ):
        if result_data.get(key):
            return True
    return False


def audit_report_semantics(
    db: Session,
    *,
    user_id: Optional[str] = None,
    limit: int = DEFAULT_AUDIT_LIMIT,
    as_of: Optional[str] = None,
) -> Dict[str, Any]:
    """Scan historical reports and return a read-only migration pre-check audit.

    Parameters
    ----------
    db:
        SQLAlchemy session. Only ``SELECT`` is performed; this function never
        calls ``db.add`` / ``db.commit`` / ``db.delete``.
    user_id:
        Optional user filter. ``None`` scans across all users (migration scope).
    limit:
        Max number of most recent completed reports to scan.
    as_of:
        Override the ``as_of`` timestamp (mainly for deterministic tests).

    Returns
    -------
    dict with ``source`` / ``as_of`` / ``dry_run=True`` / ``scanned_report_count``
    / ``clean_count`` / ``gap_counts`` / ``recommendation_counts`` / ``items`` /
    ``notes``. The shape is stable even when no reports exist.
    """
    as_of = as_of or _utc_now_iso()

    try:
        from api.database import ReportDB

        query = (
            db.query(ReportDB)
            .options(
                load_only(
                    ReportDB.id,
                    ReportDB.user_id,
                    ReportDB.symbol,
                    ReportDB.trade_date,
                    ReportDB.status,
                    ReportDB.decision,
                    ReportDB.research_direction,
                    ReportDB.execution_action,
                    ReportDB.action_label,
                    ReportDB.final_trade_decision,
                    ReportDB.result_data,
                    ReportDB.created_at,
                )
            )
            .filter(ReportDB.status == "completed")
        )
        if user_id is not None:
            query = query.filter(ReportDB.user_id == user_id)
        rows = query.order_by(ReportDB.created_at.desc()).limit(limit).all()
    except Exception as exc:
        logger.warning("[REPORT-UX-002] report scan failed: %s", exc)
        return _empty_audit(as_of, f"report scan failed: {exc}")

    if not rows:
        return _empty_audit(as_of, "no completed reports to scan")

    items: List[ReportAuditItem] = []
    gap_counts = {
        "missing_semantics": 0,
        "missing_data_blockers": 0,
        "missing_raw_evidence": 0,
    }
    recommendation_counts = {rec: 0 for rec in _ALL_RECOMMENDATIONS}
    clean_count = 0

    for row in rows:
        try:
            item = _audit_one_row(row)
        except Exception as exc:
            # Defensive: never let one bad row abort the whole audit.
            logger.warning(
                "[REPORT-UX-002] audit failed for report %s: %s",
                getattr(row, "id", "?"),
                exc,
            )
            recommendation_counts[RECOMMENDATION_AUDIT_ERROR] += 1
            continue

        items.append(item)
        if item.missing_semantics:
            gap_counts["missing_semantics"] += 1
        if item.missing_data_blockers:
            gap_counts["missing_data_blockers"] += 1
        if item.missing_raw_evidence:
            gap_counts["missing_raw_evidence"] += 1
        recommendation_counts[item.recommendation] = (
            recommendation_counts.get(item.recommendation, 0) + 1
        )
        if item.recommendation == RECOMMENDATION_OK:
            clean_count += 1

    notes: List[str] = []
    affected = len(items) - clean_count
    if affected == 0:
        notes.append(
            f"scanned {len(items)} completed report(s); all carry 3-layer semantics + data_blockers"
        )
    else:
        notes.append(
            f"scanned {len(items)} completed report(s); {affected} have at least one gap"
        )
    if recommendation_counts.get(RECOMMENDATION_NEEDS_RERUN):
        notes.append(
            f"{recommendation_counts[RECOMMENDATION_NEEDS_RERUN]} report(s) need a rerun"
        )
    if recommendation_counts.get(RECOMMENDATION_CANNOT_JUDGE):
        notes.append(
            f"{recommendation_counts[RECOMMENDATION_CANNOT_JUDGE]} report(s) cannot be judged"
        )

    return {
        "source": "report_semantics_audit",
        "as_of": as_of,
        "dry_run": True,
        "scanned_report_count": len(items),
        "clean_count": clean_count,
        "gap_counts": gap_counts,
        "recommendation_counts": recommendation_counts,
        "items": [asdict(it) for it in items],
        "notes": notes,
    }


# ─── Rendering (for the doc/CLI example) ─────────────────────────────────────


def render_audit_report(audit: Dict[str, Any]) -> str:
    """Render an audit result as a human-readable markdown document.

    Used by ``scripts/audit_report_semantics.py`` and by the sample doc; purely
    a presentation layer, does not touch the DB.
    """
    lines: List[str] = []
    lines.append("# 历史报告动作语义与数据缺口迁移预检（dry-run）")
    lines.append("")
    lines.append(f"- 生成时间 (as_of): `{audit.get('as_of')}`")
    lines.append(f"- 扫描报告数: **{audit.get('scanned_report_count', 0)}**")
    lines.append(f"- 无缺口报告数: **{audit.get('clean_count', 0)}**")
    lines.append("- 模式: **只读预检（dry-run）**，未写入任何数据库")
    lines.append("")

    gap = audit.get("gap_counts", {}) or {}
    lines.append("## 缺口统计")
    lines.append("")
    lines.append("| 缺口类型 | 数量 | 说明 |")
    lines.append("| --- | ---: | --- |")
    lines.append(
        f"| 动作语义缺失 (research_direction / execution_action / action_label) "
        f"| {gap.get('missing_semantics', 0)} | DECISION-001 3 层语义 |"
    )
    lines.append(
        f"| data_blockers 缺失 | {gap.get('missing_data_blockers', 0)} | DATA-021 字段级降级 |"
    )
    lines.append(
        f"| raw_evidence 缺失 | {gap.get('missing_raw_evidence', 0)} | DATA-004 来源契约 |"
    )
    lines.append("")

    rec = audit.get("recommendation_counts", {}) or {}
    lines.append("## 建议动作分布")
    lines.append("")
    lines.append("| 建议 | 数量 | 含义 |")
    lines.append("| --- | ---: | --- |")
    lines.append(
        f"| `can_derive` (可读时补算) | {rec.get(RECOMMENDATION_CAN_DERIVE, 0)} "
        "| 缺失字段可在只读副本上补算，后续可走独立 backfill 写回 |"
    )
    lines.append(
        f"| `needs_rerun` (需要重跑) | {rec.get(RECOMMENDATION_NEEDS_RERUN, 0)} "
        "| 至少一项缺口无法从现有文本补回，建议重跑该报告 |"
    )
    lines.append(
        f"| `cannot_judge` (无法判断) | {rec.get(RECOMMENDATION_CANNOT_JUDGE, 0)} "
        "| result_data 与 final_trade_decision 均缺失 |"
    )
    lines.append(
        f"| `ok` (无缺口) | {rec.get(RECOMMENDATION_OK, 0)} | 3 层语义 + data_blockers 齐全 |"
    )
    lines.append(
        f"| `audit_error` (扫描异常) | {rec.get(RECOMMENDATION_AUDIT_ERROR, 0)} "
        "| 单行扫描抛错，已跳过 |"
    )
    lines.append("")

    notes = audit.get("notes") or []
    if notes:
        lines.append("## 备注")
        lines.append("")
        for n in notes:
            lines.append(f"- {n}")
        lines.append("")

    items = audit.get("items") or []
    if items:
        lines.append("## 报告明细（仅列出有缺口的报告）")
        lines.append("")
        lines.append(
            "| report_id | symbol | trade_date | 缺口 | 建议 | 当前语义 | 补算后语义 |"
        )
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for it in items:
            if it.get("recommendation") == RECOMMENDATION_OK:
                continue
            gap_flags = []
            if it.get("missing_semantics"):
                gap_flags.append("语义")
            if it.get("missing_data_blockers"):
                gap_flags.append("blockers")
            if it.get("missing_raw_evidence"):
                gap_flags.append("raw_ev")
            gap_str = ", ".join(gap_flags) or "—"
            stored = (
                f"{it.get('stored_research_direction') or '—'}/"
                f"{it.get('stored_execution_action') or '—'}/"
                f"{it.get('stored_action_label') or '—'}"
            )
            derived = (
                f"{it.get('derived_research_direction') or '—'}/"
                f"{it.get('derived_execution_action') or '—'}/"
                f"{it.get('derived_action_label') or '—'}"
            )
            if not it.get("missing_semantics"):
                derived = "（不适用）"
            lines.append(
                f"| `{it.get('report_id')}` | {it.get('symbol')} "
                f"| {it.get('trade_date')} | {gap_str} | `{it.get('recommendation')}` "
                f"| {stored} | {derived} |"
            )
        lines.append("")
        lines.append("> 说明：`补算后语义` 列展示的是**只读 dry-run** 的推导结果，"
                     "并未写回数据库。`can_derive` 报告可通过后续独立 backfill 任务落地。")

    return "\n".join(lines)
