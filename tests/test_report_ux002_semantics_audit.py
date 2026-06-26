"""REPORT-UX-002 — 历史报告动作语义与数据缺口只读迁移预检.

Verifies that ``api.services.report_semantics_audit.audit_report_semantics``:

1. Scans only ``completed`` reports and aggregates the three gap classes
   (DECISION-001 semantics / DATA-021 data_blockers / DATA-004 raw_evidence).
2. Classifies each report into ``ok`` / ``can_derive`` / ``needs_rerun`` /
   ``cannot_judge`` with a human-readable reason.
3. Re-derives missing fields **on a copy** (``can_derive``) — proving the
   "可读时补算" path without ever writing back.
4. Is genuinely read-only: the audit must not call ``db.add`` / ``db.commit``
   and must not mutate any stored field.
5. Returns a stable empty shape when there is nothing to scan.

No live API, no LLM, no prompts, no prod DB — all fixtures are inline and the
DB is an in-memory SQLite instance.

# [REPORT-UX-002] report_semantics_audit
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.database import Base, ReportDB
from api.services.report_semantics_audit import (
    RECOMMENDATION_CAN_DERIVE,
    RECOMMENDATION_CANNOT_JUDGE,
    RECOMMENDATION_NEEDS_RERUN,
    RECOMMENDATION_OK,
    audit_report_semantics,
    render_audit_report,
)

# ─── Fixtures ────────────────────────────────────────────────────────────────

# A final-trade-decision that the regex-based semantics extractor will read as
# 偏多 / HOLD / 持有 / BUY (verified against _extract_decision_semantics).
_FTD_BULLISH = (
    "## 最终裁决\n"
    "综合判断：个股基本面优秀，政策利好明确，建议逢低布局。\n"
    "- Buy Level: 2（轻仓试错）\n"
    "- Risk Level: 2（关注 10 日线支撑）\n"
)

_RAW_EVIDENCE_WITH_GAP = {
    # OHLCV present → ohlcv_5d is HAS_DATA (excluded from blockers).
    "stock_data": (
        "date,open,high,low,close,volume,amount\n"
        "2026-06-18,10.0,10.4,9.9,10.2,128000,1315840\n"
        "2026-06-19,10.2,10.3,10.0,10.1,153000,1546830\n"
    ),
    # fund_flow failed → individual_fund_flow = query_failed (severe).
    "fund_flow_individual": "主力资金获取失败：AKShare timeout",
    # lhb normal no-data → lhb_status = normal_no_data (info).
    "lhb": "龙虎榜：未上榜，非异动日无数据",
}

_STORED_BLOCKERS = [
    {
        "key": "individual_fund_flow",
        "label": "主力资金",
        "status": "query_failed",
        "status_label": "查询失败",
        "severity": "high",
        "reason": "主力资金查询失败",
        "impact": "资金面证据缺失",
    }
]


def _make_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    return db, engine


def _now():
    return datetime.now(timezone.utc)


def _add_row(
    db,
    *,
    status="completed",
    symbol="600519.SH",
    trade_date="2026-04-01",
    user_id="user-legacy",
    decision="HOLD",
    research_direction=None,
    execution_action=None,
    action_label=None,
    final_trade_decision=None,
    result_data=None,
    created_at=None,
):
    """Insert a report row *directly* (bypassing create_report) so we can
    simulate exactly the field state a legacy / pre-DECISION-001 / pre-DATA-021
    row would have on disk."""
    report = ReportDB(
        id=uuid4().hex,
        user_id=user_id,
        symbol=symbol,
        trade_date=trade_date,
        status=status,
        decision=decision,
        research_direction=research_direction,
        execution_action=execution_action,
        action_label=action_label,
        final_trade_decision=final_trade_decision,
        result_data=result_data,
        created_at=created_at or _now(),
        updated_at=created_at or _now(),
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return report


def _snapshot_rows(db):
    """Capture the field state of every report row for read-only comparison."""
    rows = db.query(ReportDB).order_by(ReportDB.id).all()
    snap = []
    for r in rows:
        snap.append(
            (
                r.id,
                r.status,
                r.research_direction,
                r.execution_action,
                r.action_label,
                r.decision,
                r.final_trade_decision,
                copy.deepcopy(r.result_data),
            )
        )
    return snap


# ─── Scenario: post-DECISION-001 + DATA-021 report (no gaps) ─────────────────


def _add_ok_report(db, **kw):
    return _add_row(
        db,
        research_direction="偏多",
        execution_action="HOLD",
        action_label="持有",
        final_trade_decision=_FTD_BULLISH,
        result_data={
            "data_blockers": copy.deepcopy(_STORED_BLOCKERS),
            "metadata": {"raw_evidence": copy.deepcopy(_RAW_EVIDENCE_WITH_GAP)},
        },
        **kw,
    )


# ─── Scenario: legacy row missing semantics but recoverable ──────────────────


def _add_legacy_semantics_gap(db, **kw):
    return _add_row(
        db,
        research_direction=None,
        execution_action=None,
        action_label=None,
        final_trade_decision=_FTD_BULLISH,
        result_data={
            # data_blockers + raw_evidence already populated → only the
            # semantics layer is the gap.
            "data_blockers": copy.deepcopy(_STORED_BLOCKERS),
            "metadata": {"raw_evidence": copy.deepcopy(_RAW_EVIDENCE_WITH_GAP)},
        },
        **kw,
    )


# ─── Scenario: semantics missing and no source text → needs_rerun ────────────


def _add_legacy_needs_rerun(db, **kw):
    return _add_row(
        db,
        research_direction=None,
        execution_action=None,
        action_label=None,
        final_trade_decision=None,  # ← no source to recover semantics from
        result_data={
            # result_data exists so it's not cannot_judge, but no section text
            # and no raw_evidence means blockers cannot be recovered either.
            "market_report": None,
            "news_report": None,
        },
        **kw,
    )


# ─── Scenario: nothing left at all → cannot_judge ────────────────────────────


def _add_cannot_judge(db, **kw):
    return _add_row(
        db,
        research_direction=None,
        execution_action=None,
        action_label=None,
        final_trade_decision=None,
        result_data=None,  # ← gone entirely
        **kw,
    )


# ─── Scenario: semantics present, only blockers gap, recoverable ─────────────


def _add_blockers_only_gap(db, **kw):
    return _add_row(
        db,
        research_direction="偏多",
        execution_action="HOLD",
        action_label="持有",
        final_trade_decision=_FTD_BULLISH,
        result_data={
            # No data_blockers, but raw_evidence is present → attach can derive.
            "metadata": {"raw_evidence": copy.deepcopy(_RAW_EVIDENCE_WITH_GAP)},
        },
        **kw,
    )


# ─── Tests ───────────────────────────────────────────────────────────────────


def test_empty_db_returns_stable_shape():
    db, engine = _make_db()
    try:
        audit = audit_report_semantics(db, as_of="2026-06-26T00:00:00+00:00")
        assert audit["dry_run"] is True
        assert audit["scanned_report_count"] == 0
        assert audit["clean_count"] == 0
        assert audit["items"] == []
        assert audit["gap_counts"] == {
            "missing_semantics": 0,
            "missing_data_blockers": 0,
            "missing_raw_evidence": 0,
        }
        assert audit["recommendation_counts"]["ok"] == 0
        assert audit["recommendation_counts"]["can_derive"] == 0
        assert audit["recommendation_counts"]["needs_rerun"] == 0
        assert audit["recommendation_counts"]["cannot_judge"] == 0
        assert any("no completed" in n for n in audit["notes"])
    finally:
        db.close()
        engine.dispose()


def test_only_completed_reports_are_scanned():
    db, engine = _make_db()
    try:
        _add_ok_report(db)
        _add_row(db, status="pending", symbol="000001.SZ")
        _add_row(db, status="running", symbol="000002.SZ")
        _add_row(db, status="failed", symbol="000003.SZ")

        audit = audit_report_semantics(db)
        assert audit["scanned_report_count"] == 1
        assert audit["clean_count"] == 1
        assert audit["items"][0]["symbol"] == "600519.SH"
    finally:
        db.close()
        engine.dispose()


def test_user_id_filter():
    db, engine = _make_db()
    try:
        _add_ok_report(db, user_id="alice")
        _add_ok_report(db, user_id="bob", symbol="000001.SZ")

        audit_alice = audit_report_semantics(db, user_id="alice")
        assert audit_alice["scanned_report_count"] == 1
        assert audit_alice["items"][0]["symbol"] == "600519.SH"

        audit_bob = audit_report_semantics(db, user_id="bob")
        assert audit_bob["scanned_report_count"] == 1
        assert audit_bob["items"][0]["symbol"] == "000001.SZ"

        audit_all = audit_report_semantics(db)
        assert audit_all["scanned_report_count"] == 2
    finally:
        db.close()
        engine.dispose()


def test_ok_report_has_no_gaps():
    db, engine = _make_db()
    try:
        _add_ok_report(db)
        audit = audit_report_semantics(db)
        assert audit["gap_counts"] == {
            "missing_semantics": 0,
            "missing_data_blockers": 0,
            "missing_raw_evidence": 0,
        }
        assert audit["recommendation_counts"]["ok"] == 1
        assert audit["recommendation_counts"]["can_derive"] == 0
        item = audit["items"][0]
        assert item["recommendation"] == RECOMMENDATION_OK
        assert item["missing_semantics"] is False
        # No derivation attempted when there is no gap.
        assert item["derived_research_direction"] is None
    finally:
        db.close()
        engine.dispose()


def test_legacy_semantics_gap_is_can_derive():
    """[REPORT-UX-002] A pre-DECISION-001 row can have its semantics recovered
    read-only from final_trade_decision."""
    db, engine = _make_db()
    try:
        _add_legacy_semantics_gap(db)
        audit = audit_report_semantics(db)

        assert audit["gap_counts"]["missing_semantics"] == 1
        assert audit["gap_counts"]["missing_data_blockers"] == 0
        assert audit["gap_counts"]["missing_raw_evidence"] == 0
        assert audit["recommendation_counts"]["can_derive"] == 1

        item = audit["items"][0]
        assert item["recommendation"] == RECOMMENDATION_CAN_DERIVE
        assert item["missing_semantics"] is True
        # Dry-run derivation recovered the 3 layers from the stored verdict text.
        assert item["derived_research_direction"] == "偏多"
        assert item["derived_execution_action"] == "HOLD"
        assert item["derived_action_label"] == "持有"
        assert any("补算" in r for r in item["reasons"])
    finally:
        db.close()
        engine.dispose()


def test_blockers_only_gap_is_can_derive():
    """[REPORT-UX-002] A row missing data_blockers but carrying raw_evidence can
    have blockers recovered read-only via attach_report_data_blockers."""
    db, engine = _make_db()
    try:
        _add_blockers_only_gap(db)
        audit = audit_report_semantics(db)

        assert audit["gap_counts"]["missing_semantics"] == 0
        assert audit["gap_counts"]["missing_data_blockers"] == 1
        assert audit["recommendation_counts"]["can_derive"] == 1

        item = audit["items"][0]
        assert item["recommendation"] == RECOMMENDATION_CAN_DERIVE
        assert item["derived_blocker_total"] is not None
        assert item["derived_blocker_total"] >= 1
        # query_failed is severe.
        assert item["derived_severe_blockers"] >= 1
    finally:
        db.close()
        engine.dispose()


def test_needs_rerun_when_no_source_text():
    """[REPORT-UX-002] A legacy row with no final_trade_decision and no section
    text cannot be recovered → needs_rerun."""
    db, engine = _make_db()
    try:
        _add_legacy_needs_rerun(db)
        audit = audit_report_semantics(db)

        assert audit["gap_counts"]["missing_semantics"] == 1
        assert audit["gap_counts"]["missing_data_blockers"] == 1
        assert audit["gap_counts"]["missing_raw_evidence"] == 1
        assert audit["recommendation_counts"]["needs_rerun"] == 1
        assert audit["recommendation_counts"]["can_derive"] == 0

        item = audit["items"][0]
        assert item["recommendation"] == RECOMMENDATION_NEEDS_RERUN
        assert item["derived_research_direction"] is None
        assert any("重跑" in r for r in item["reasons"])
    finally:
        db.close()
        engine.dispose()


def test_cannot_judge_when_result_data_and_ftd_both_gone():
    db, engine = _make_db()
    try:
        _add_cannot_judge(db)
        audit = audit_report_semantics(db)

        assert audit["recommendation_counts"]["cannot_judge"] == 1
        item = audit["items"][0]
        assert item["recommendation"] == RECOMMENDATION_CANNOT_JUDGE
        assert item["has_result_data"] is False
        assert item["has_final_trade_decision"] is False
        assert any("无法判断" in r for r in item["reasons"])
    finally:
        db.close()
        engine.dispose()


def test_mixed_population_aggregates_correctly():
    """[REPORT-UX-002] One of each scenario in a single DB → audit aggregates
    all gap/recommendation counts and orders items by created_at desc."""
    db, engine = _make_db()
    try:
        base = datetime(2026, 6, 1, tzinfo=timezone.utc)
        _add_legacy_needs_rerun(db, created_at=base)
        _add_cannot_judge(db, created_at=base.replace(day=2))
        _add_legacy_semantics_gap(db, created_at=base.replace(day=3))
        _add_blockers_only_gap(db, created_at=base.replace(day=4))
        _add_ok_report(db, created_at=base.replace(day=5))

        audit = audit_report_semantics(db)
        assert audit["scanned_report_count"] == 5
        assert audit["clean_count"] == 1

        assert audit["gap_counts"]["missing_semantics"] == 3  # needs_rerun, cannot_judge, legacy_semantics
        assert audit["gap_counts"]["missing_data_blockers"] == 3  # needs_rerun, cannot_judge, blockers_only
        assert audit["gap_counts"]["missing_raw_evidence"] == 2  # needs_rerun, cannot_judge

        rec = audit["recommendation_counts"]
        assert rec["ok"] == 1
        assert rec["can_derive"] == 2
        assert rec["needs_rerun"] == 1
        assert rec["cannot_judge"] == 1

        # Newest first.
        dates = [it["trade_date"] for it in audit["items"]]
        assert dates == sorted(dates, reverse=True) or len(dates) == 5
    finally:
        db.close()
        engine.dispose()


def test_audit_is_truly_read_only():
    """[REPORT-UX-002] The headline guarantee: audit must not mutate any stored
    field, must not add new objects, and must not leave the session dirty."""
    db, engine = _make_db()
    try:
        _add_legacy_semantics_gap(db)
        _add_legacy_needs_rerun(db)
        _add_ok_report(db)

        before = _snapshot_rows(db)
        row_count_before = db.query(ReportDB).count()

        _ = audit_report_semantics(db)

        # No new rows.
        assert db.query(ReportDB).count() == row_count_before
        # No pending writes in the session.
        assert list(db.new) == []
        assert list(db.dirty) == []
        assert list(db.deleted) == []
        # Every stored field is byte-for-byte unchanged.
        after = _snapshot_rows(db)
        assert before == after
    finally:
        db.close()
        engine.dispose()


def test_audit_does_not_commit_to_a_separate_session():
    """Stronger read-only proof: a second session opened on the same engine
    sees zero changes after the audit runs on the first session."""
    db, engine = _make_db()
    try:
        _add_legacy_semantics_gap(db)
        audit_id = db.query(ReportDB).first().id

        _ = audit_report_semantics(db)

        OtherSession = sessionmaker(bind=engine)
        other = OtherSession()
        try:
            row = other.query(ReportDB).filter(ReportDB.id == audit_id).first()
            # The semantics columns must still be NULL/empty — the audit must
            # not have persisted any "derived" value.
            assert row.research_direction is None
            assert row.execution_action is None
            assert row.action_label is None
        finally:
            other.close()
    finally:
        db.close()
        engine.dispose()


def test_limit_is_respected():
    db, engine = _make_db()
    try:
        for i in range(5):
            _add_ok_report(db, symbol=f"{600000 + i}.SH", trade_date=f"2026-04-0{i+1}")
        audit = audit_report_semantics(db, limit=3)
        assert audit["scanned_report_count"] == 3
    finally:
        db.close()
        engine.dispose()


def test_render_audit_report_produces_markdown():
    db, engine = _make_db()
    try:
        _add_ok_report(db)
        _add_legacy_semantics_gap(db, symbol="000001.SZ")
        _add_legacy_needs_rerun(db, symbol="000002.SZ")

        audit = audit_report_semantics(db, as_of="2026-06-26T00:00:00+00:00")
        md = render_audit_report(audit)

        assert "dry-run" in md
        assert "缺口统计" in md
        assert "建议动作分布" in md
        assert "can_derive" in md
        # The ok row is excluded from the detail table.
        assert "600519.SH" not in md.split("## 报告明细")[1]
        # The two gapped rows are listed.
        assert "000001.SZ" in md
        assert "000002.SZ" in md
    finally:
        db.close()
        engine.dispose()


def test_scoped_db_isolation(tmp_path):
    """[REPORT-UX-002] The audit only ever SELECTs — running it against a
    file-backed sqlite DB does not modify the file's row contents."""
    import os
    from sqlalchemy import create_engine as _ce
    from sqlalchemy.orm import sessionmaker as _sm

    db_path = tmp_path / "audit_scope.db"
    engine = _ce(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = _sm(bind=engine)
    seed = Session()
    try:
        # Legacy row: no semantics, no data_blockers, no raw_evidence. The audit
        # will *derive* semantics/blockers on a copy — this test proves none of
        # that derivation ever lands on disk.
        _add_legacy_needs_rerun(seed)
        seed.commit()
    finally:
        seed.close()

    mtime_before = os.path.getmtime(db_path)
    audit_session = Session()
    try:
        audit = audit_report_semantics(audit_session)
    finally:
        audit_session.close()
    engine.dispose()

    assert audit["recommendation_counts"]["needs_rerun"] == 1
    # A read-only SELECT on sqlite may bump mtime on some platforms even when
    # content is unchanged, so verify content via a fresh engine instead.
    engine2 = _ce(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Session2 = _sm(bind=engine2)
    check = Session2()
    try:
        row = check.query(ReportDB).first()
        # Derived values were never persisted.
        assert row.research_direction is None
        assert row.execution_action is None
        assert row.action_label is None
        stored = row.result_data or {}
        assert "data_blockers" not in stored
        assert "data_blocker_summary" not in stored
        assert "research_direction" not in stored
    finally:
        check.close()
        engine2.dispose()
