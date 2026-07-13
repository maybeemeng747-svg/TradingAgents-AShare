# [REPORT-UX-006] knowledge_evidence_card
"""REPORT-UX-006 — TA 报告知识证据来源卡与缺口解释（backend side）.

The card itself is a frontend presentation layer derived purely from the
existing ``local_knowledge_summary`` + ``half_year_facts_summary`` dicts
(see ``frontend/src/utils/knowledgeContract.ts::deriveKnowledgeEvidenceCard``).
The only backend change is honoring the KB-disabled env flag inside
``attach_report_local_knowledge`` so that reports surface a "知识库已禁用"
gap instead of issuing a slow/failed query against a KB the operator turned
off (mirrors KB-006 ``local_knowledge_context_service``).

This test file covers the three backend contracts:

  1. **Disabled plumbing**: when ``KNOWLEDGE_LOCAL_DISABLED`` (or
     ``KNOWLEDGE_CONTEXT_DISABLED``) is truthy, the attach function returns a
     minimal summary with ``kb_disabled=True`` / ``matched_count=0`` and does
     NOT issue a wiki query.
  2. **Action-semantic invariant**: the disabled summary is additive only —
     ``decision`` / ``execution_action`` / ``action_label`` /
     ``research_direction`` are untouched (REPORT-UX-005 hard contract).
  3. **Schema round-trip**: the ``kb_disabled`` flag survives
     ``ReportResponse.model_validate`` serialization so the frontend card can
     read it.

执行约束：
  - 不调用 live LLM。
  - 不写生产 DB（in-memory SQLite）。
  - 不改 prompts。
  - fixture 知识库在 tmp_path 下构建。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.database import Base
from api.services import report_service
from tradingagents.dataflows.local_knowledge_audit import INVESTMENT_SUBDIR


# ── Inline helpers ────────────────────────────────────────────────────────


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# Minimal local-knowledge page that would normally produce a HAS_DATA hit for
# 603296 — used to prove the disabled path SKIPS the query even when a hit
# is available in the KB.
_HIT_PAGE = """\
---
title: 华勤技术603296-超节点
created: 2026-05-01
updated: 2026-05-01
sources:
- kind: broker_research
  url: https://example.com/huaqin
symbols:
- "603296.SH"
name: 华勤技术
tags:
- AI算力
---

## 核心逻辑
超节点架构放量，AI算力受益。

## 风险
- 客户集中度高
"""

# A minimal OHLCV CSV (>50 chars) so raw_evidence.ohlcv_5d is HAS_DATA and the
# report does not collapse to DATA_MISSING — keeps action semantics directional.
_OHLCV_HAS_DATA = (
    "date,open,high,low,close,volume,amount\n"
    "2026-07-07,38.20,38.80,37.90,38.55,1230000,47400000.00\n"
    "2026-07-08,38.55,39.10,38.40,39.00,980000,38200000.00\n"
    "2026-07-09,39.00,39.45,38.85,39.30,1100000,43200000.00\n"
    "2026-07-10,39.30,39.60,39.10,39.50,870000,34300000.00\n"
    "2026-07-11,39.50,39.90,39.30,39.80,1340000,53300000.00\n"
)


def _build_result_data(symbol: str, kb_root: str) -> Dict[str, Any]:
    """Build a minimal ENTER result_data with a fresh OHLCV raw_evidence entry
    so the report is not DATA_MISSING and KB attach has something to read."""
    return {
        "symbol": symbol,
        "name": "华勤技术",
        "trade_date": "2026-07-11",
        "decision": "BUY",
        "action_label": "条件入场",
        "research_direction": "看多",
        "execution_action": "ENTER",
        "final_trade_decision": (
            "## 最终交易决策\n\n看多入场。\n\nBuy Level: 试错仓\nRisk Level: 单笔2%\n"
        ),
        "market_report": "市场平稳。",
        "investment_plan": "看多入场。",
        "final_trade_decision_text": "看多入场。",
        "metadata": {
            "raw_evidence": {
                "ohlcv_5d": {
                    "field": "ohlcv_5d",
                    "value": _OHLCV_HAS_DATA,
                    "status": "HAS_DATA",
                    "vendor": "baostock",
                    "as_of": "2026-07-11",
                    "fetched_at": "2026-07-11T15:30:00",
                },
            },
        },
    }


@pytest.fixture()
def fixture_kb(tmp_path: Path) -> Path:
    inv = tmp_path / INVESTMENT_SUBDIR
    _write(inv / "华勤技术603296-超节点.md", _HIT_PAGE)
    return tmp_path


def _make_db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    SessionLocal = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    return db, engine


_DISABLE_VARS = ("KNOWLEDGE_LOCAL_DISABLED", "KNOWLEDGE_CONTEXT_DISABLED")


# ── 1. Disabled plumbing: attach surfaces kb_disabled without querying ────


@pytest.mark.parametrize("env_var", _DISABLE_VARS)
def test_attach_surfaces_kb_disabled_when_env_set(
    env_var: str, fixture_kb: Path, monkeypatch
):
    """[REPORT-UX-006] 当任一禁用 env 为真值时，attach_report_local_knowledge
    必须返回带 ``kb_disabled=True`` 的最小 summary，且 matched_count=0，
    不发起 wiki 查询（不会因慢查询阻塞报告主体）。"""
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
    monkeypatch.setenv(env_var, "1")

    result_data = _build_result_data("603296.SH", str(fixture_kb))

    enriched = report_service.attach_report_local_knowledge(
        result_data=result_data, symbol="603296.SH"
    )

    assert isinstance(enriched, dict)
    summary = enriched.get("local_knowledge_summary")
    assert isinstance(summary, dict)
    assert summary.get("kb_disabled") is True
    assert summary.get("matched_count") == 0
    # The markdown block must be absent/empty — disabled means no KB body.
    assert not enriched.get("local_knowledge_block")


def test_attach_does_not_query_when_disabled_even_with_hit_available(
    fixture_kb: Path, monkeypatch
):
    """[REPORT-UX-006] 即使知识库中存在命中页（603296 华勤技术），禁用后
    attach 也不得返回命中。证明禁用确实短路了查询。"""
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
    monkeypatch.setenv("KNOWLEDGE_LOCAL_DISABLED", "true")

    result_data = _build_result_data("603296.SH", str(fixture_kb))
    enriched = report_service.attach_report_local_knowledge(
        result_data=result_data, symbol="603296.SH"
    )
    summary = enriched.get("local_knowledge_summary") or {}
    assert summary.get("kb_disabled") is True
    assert summary.get("matched_count") == 0
    assert summary.get("status") == "NORMAL_NO_DATA"


def test_attach_queries_normally_when_not_disabled(fixture_kb: Path, monkeypatch):
    """[REPORT-UX-006] 未禁用时，attach 仍按 KB-003 路径正常命中。回归保护：
    禁用短路不应误伤正常路径。"""
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
    # Ensure no disable env leaks from the host.
    for var in _DISABLE_VARS:
        monkeypatch.delenv(var, raising=False)

    result_data = _build_result_data("603296.SH", str(fixture_kb))
    enriched = report_service.attach_report_local_knowledge(
        result_data=result_data, symbol="603296.SH"
    )
    summary = enriched.get("local_knowledge_summary") or {}
    assert summary.get("kb_disabled") is not True
    # Query happened and returned hits — exact status (HAS_DATA vs
    # LOW_CONFIDENCE) depends on provider heuristics; the point is the
    # disabled short-circuit did NOT fire.
    assert summary.get("matched_count", 0) > 0
    assert summary.get("status") in {"HAS_DATA", "LOW_CONFIDENCE", "STALE"}
    assert enriched.get("local_knowledge_block")


# ── 2. Action-semantic invariant: disabled does not alter actions ─────────


def test_disabled_kb_does_not_overwrite_directional_action(
    fixture_kb: Path, monkeypatch
):
    """[REPORT-UX-005/006 hard contract] 知识库禁用后产生的 kb_disabled gap
    不得把 ENTER 覆写成 WAIT/观察，也不得改变 action_label。"""
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
    monkeypatch.setenv("KNOWLEDGE_LOCAL_DISABLED", "1")

    db, engine = _make_db()
    try:
        report = report_service.create_report(
            db=db,
            symbol="603296.SH",
            trade_date="2026-07-11",
            decision="BUY",
            result_data=_build_result_data("603296.SH", str(fixture_kb)),
        )

        # Strong action gate intact.
        assert report.decision == "BUY"
        assert report.execution_action == "ENTER"
        assert report.action_label == "条件入场"
        assert report.research_direction == "看多"

        # Disabled gap surfaced in result_data but no WAIT reason invented.
        summary = report.result_data.get("local_knowledge_summary") or {}
        assert summary.get("kb_disabled") is True
        codes = report.result_data.get("wait_reason_codes") or []
        assert codes == [], (
            f"disabled KB must not invent wait_reason_codes for ENTER, got {codes}"
        )
    finally:
        db.close()
        engine.dispose()


# ── 3. Schema round-trip: kb_disabled flag survives serialization ─────────


def test_kb_disabled_flag_survives_report_response_serialization(
    fixture_kb: Path, monkeypatch
):
    """[REPORT-UX-006] kb_disabled 嵌在 local_knowledge_summary dict 内，
    必须能通过 ReportResponse.model_validate 往返序列化，前端 card 才能读到。"""
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
    monkeypatch.setenv("KNOWLEDGE_LOCAL_DISABLED", "yes")

    db, engine = _make_db()
    try:
        report = report_service.create_report(
            db=db,
            symbol="603296.SH",
            trade_date="2026-07-11",
            decision="BUY",
            result_data=_build_result_data("603296.SH", str(fixture_kb)),
        )

        from api.main import (
            ReportResponse,
            _attach_report_data_blockers_for_response,
        )

        _attach_report_data_blockers_for_response(report)
        resp = ReportResponse.model_validate(report)
        dumped = resp.model_dump()

        # Action gate intact through serialization.
        assert dumped["execution_action"] == "ENTER"
        assert dumped["action_label"] == "条件入场"

        # kb_disabled flag survives the round-trip.
        summary = dumped.get("local_knowledge_summary")
        assert isinstance(summary, dict)
        assert summary.get("kb_disabled") is True
        assert summary.get("matched_count") == 0

        # The half_year_facts fields declared on ReportResponse also survive
        # (REPORT-UX-006 frontend reads them via the new Report interface).
        for key in (
            "half_year_facts_block",
            "half_year_facts_summary",
            "half_year_facts_status",
        ):
            assert key in dumped, (
                f"ReportResponse must declare {key} so the frontend card can read it"
            )
    finally:
        db.close()
        engine.dispose()
