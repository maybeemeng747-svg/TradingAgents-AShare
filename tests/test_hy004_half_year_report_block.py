# [HY-004] half_year_report_block
"""HY-004 — TA 报告接入"半年报事实对照"区块（P1）.

Replays historical-report fixtures through ``report_service.create_report()``
+ ``_attach_report_data_blockers_for_response`` + ``ReportResponse.model_validate``
to verify that the new "半年报事实对照" block is **purely additive** and never
overrides the strong action gate, the wait_reason_codes or the data_blockers.

Headline acceptance (HY-004 任务验收方式):

  1. **有半年报事实时只增加区块，不改变原 decision/action_label.**
  2. **无半年报事实时显示缺口（NO_DATA），不误判 FAILED.**
  3. **数据不足报告仍保留原缺口原因（wait_reason_codes / data_blockers）.**
  4. **半年报事实与 data_blockers / wait_reason_codes 解耦——知识命中不得
     掩盖数据缺口.**
  5. **前端/接口字段向后兼容——ReportResponse schema round-trip 不破.**

Coverage matrix (7 fixtures):

| # | Scenario                  | HY hit            | Action      | wait_codes      |
|---|---------------------------|-------------------|-------------|-----------------|
| S1| 有事实 + WAIT 数据不足    | HAS_FACTS 000977  | WAIT 观察   | DATA_MISSING    |
| S2| 无半年报事实              | NO_DATA 000001    | WAIT 观察   | DATA_MISSING    |
| S3| 仅过期半年报              | STALE 600519      | WAIT 观察   | DATA_MISSING    |
| S4| 低置信半年报（缺报告期）  | LOW_CONF 603296   | WAIT 观察   | DATA_MISSING    |
| S5| 方向性 + 有事实           | HAS_FACTS 000977  | ENTER 看多  | (empty)         |
| S6| 有事实 + HOLD 持有        | HAS_FACTS 000977  | HOLD 持有   | (empty)         |
| S7| 仅事实冲突                | CONFLICT          | WAIT 观察   | DATA_MISSING    |

No live API, no LLM, no prompts, no prod DB — all fixtures are inline and the
DB is an in-memory SQLite instance. The knowledge tree is a tmp_path fixture
reusing KB-013 half_year_fixtures.
"""

from __future__ import annotations

import copy
from datetime import date
from pathlib import Path
from typing import Any, Dict

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.database import Base
from api.services import report_service
from tests.half_year_fixtures import build_half_year_fixture_kb
from tradingagents.dataflows.half_year_facts_provider import (
    DATA_CONFLICT,
    DATA_FRESH,
    DATA_MISSING_PERIOD,
    DATA_STALE,
    HalfYearFactsQueryResult,
    build_half_year_facts_raw_evidence_entry,
    query_half_year_facts,
)
from tradingagents.dataflows.local_knowledge_audit import INVESTMENT_SUBDIR
from tradingagents.graph.signal_processing import WAIT_REASON_DATA_MISSING


# ── Inline helpers ────────────────────────────────────────────────────────


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture()
def fixture_kb(tmp_path: Path) -> Path:
    """Mini Tree Work 知识库 with KB-013 half-year fixtures.

    命中映射（使用 KB-013 fixture 样本）:
      - 000977 / 浪潮信息 → HAS_FACTS（qualified 半年报页）
      - 603296 / 华勤技术 → LOW_CONFIDENCE（缺报告期半年报页）
      - 600519 / 贵州茅台 → STALE（已过期半年报页）
      - 000001 / 平安银行 → NO_DATA（非财报页，控制组）
    """
    return build_half_year_fixture_kb(tmp_path)


@pytest.fixture()
def conflict_kb(tmp_path: Path) -> Path:
    """Two half-year pages with conflicting revenue for the same period.

    Used for S7 (CONFLICT status).
    """
    page_a = """---
title: 公司A-2025H1-来源1
created: 2026-08-29
updated: 2026-08-29
symbols: ["888888.SZ 冲突公司"]
report_type: 半年报
evidence_level: A
valid_until: 2099-12-31
stale_risk: 低
financial_period: 2025H1
disclosure_date: 2026-08-29
source_type: [exchange_filing]
financial_facts:
  - 营收 100亿 (+10%)
risk_factors: [风险A]
---

# A

## 一句话总结

A。

## 风险提示

- A。
"""
    page_b = """---
title: 公司A-2025H1-来源2
created: 2026-08-29
updated: 2026-08-29
symbols: ["888888.SZ 冲突公司"]
report_type: 半年报
evidence_level: A
valid_until: 2099-12-31
stale_risk: 低
financial_period: 2025H1
disclosure_date: 2026-08-30
source_type: [fact_table]
financial_facts:
  - 营收 150亿 (+15%)
risk_factors: [风险B]
---

# B

## 一句话总结

B。

## 风险提示

- B。
"""
    inv = tmp_path / INVESTMENT_SUBDIR
    _write(inv / "a.md", page_a)
    _write(inv / "b.md", page_b)
    return tmp_path


def _make_db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    SessionLocal = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    return db, engine


# Realistic OHLCV (>50 chars) so ohlcv_5d is HAS_DATA.
_OHLCV_HAS_DATA = (
    "date,open,high,low,close,volume,amount\n"
    "2026-06-18,10.00,10.42,9.91,10.28,128000,1315840\n"
    "2026-06-19,10.28,10.35,10.02,10.11,153000,1546830\n"
    "2026-06-20,10.11,10.50,10.05,10.44,201000,2102240\n"
    "2026-06-21,10.44,10.60,10.30,10.52,176000,1847520\n"
    "2026-06-24,10.52,10.71,10.45,10.66,142000,1513720\n"
)

# Realistic failing raw_evidence for "数据不足观察" scenarios (S1-S4, S7):
# ohlcv has data, but fund flow query genuinely failed.
_DATA_MISSING_RAW_EVIDENCE = {
    "stock_data": _OHLCV_HAS_DATA,
    "fund_flow_individual": "主力资金获取失败：AKShare timeout，Eastmoney push2 返回 502",
    "lhb": "龙虎榜：未上榜，非异动日无数据",
}

_BUY_RISK_LEVEL_BLOCK = (
    "## 交易建议\n"
    "- Buy Level: 1（证据不足，不入场）\n"
    "- Risk Level: 2（关注关键支撑 9.80）\n"
)

_DATA_MISSING_FINAL_DECISION = (
    "结论：主力资金证据缺失，叠加龙虎榜未上榜，本轮不给出强方向。\n"
    + _BUY_RISK_LEVEL_BLOCK
)

_ENTER_FINAL_DECISION = (
    "结论：基本面与资金面共振，等待突破 10.80 后入场。\n"
    "## 交易建议\n"
    "- Buy Level: 3（条件入场）\n"
    "- Risk Level: 2（止损 9.80）\n"
)

_HOLD_FINAL_DECISION = (
    "结论：已持仓，趋势未破，继续持有。\n"
    "## 交易建议\n"
    "- Buy Level: 2（持有不加仓）\n"
    "- Risk Level: 2（止损 9.50）\n"
)


def _build_result_data(scenario: Dict[str, Any]) -> Dict[str, Any]:
    """Build a result_data payload matching a scenario dict.

    Optionally injects a half_year_facts raw_evidence entry when the scenario
    sets ``inject_hy_raw_evidence=True`` (simulates data_collector pipeline).
    """
    raw_evidence = copy.deepcopy(scenario.get("raw_evidence", {}))
    result = {
        "market_report": "行情报告占位",
        "smart_money_report": "资金面报告占位",
        "news_report": "新闻/公告报告占位",
        "final_trade_decision": scenario["final_trade_decision"],
        "research_direction": scenario.get("research_direction"),
        "execution_action": scenario.get("execution_action"),
        "action_label": scenario.get("action_label"),
        "metadata": {"raw_evidence": raw_evidence},
    }
    return result


def _inject_hy_raw_evidence(
    result_data: Dict[str, Any],
    kb_root: str,
    symbol: str,
    trade_date: str = "2026-07-11",
) -> Dict[str, Any]:
    """Inject a half_year_facts raw_evidence entry (mirrors data_collector)."""
    metadata = result_data.setdefault("metadata", {})
    raw_ev = metadata.setdefault("raw_evidence", {})
    hy_result = query_half_year_facts(kb_root, symbol=symbol)
    raw_ev["half_year_facts"] = build_half_year_facts_raw_evidence_entry(
        hy_result, trade_date=trade_date, fetched_at=trade_date + "T09:30:00Z"
    )
    return result_data


# ── Replay scenarios (7 fixtures) ─────────────────────────────────────────

# S1: 数据不足观察 + 有半年报事实 (HAS_FACTS) — headline regression case.
SCENARIO_DATA_MISSING_WITH_FACTS = {
    "id": "s1_data_missing__has_facts",
    "symbol": "000977.SZ",
    "name": "浪潮信息",
    "description": "数据不足观察 + 半年报事实命中（HAS_FACTS）",
    "decision": "HOLD",
    "action_label": "数据不足观察",
    "research_direction": "中性",
    "execution_action": "WAIT",
    "final_trade_decision": _DATA_MISSING_FINAL_DECISION,
    "raw_evidence": _DATA_MISSING_RAW_EVIDENCE,
    "expected_hy_status": "HAS_FACTS",
    "expected_hy_block_nonempty": True,
    "expected_wait_codes_contains": [WAIT_REASON_DATA_MISSING],
    "expected_blocker_status": {
        "individual_fund_flow": "query_failed",
        "lhb_status": "normal_no_data",
    },
}

# S2: 数据不足观察 + 无半年报事实 — proves HY absence doesn't break the
# wait_reason / data_blocker chain, and that NO_DATA is NOT FAILED.
SCENARIO_NO_HALF_YEAR_DATA = {
    "id": "s2_data_missing__no_hy",
    "symbol": "000001.SZ",
    "name": "平安银行",
    "description": "数据不足观察 + 无半年报事实命中（NO_DATA）",
    "decision": "HOLD",
    "action_label": "数据不足观察",
    "research_direction": "中性",
    "execution_action": "WAIT",
    "final_trade_decision": _DATA_MISSING_FINAL_DECISION,
    "raw_evidence": _DATA_MISSING_RAW_EVIDENCE,
    "expected_hy_status": "NO_DATA",
    "expected_hy_block_nonempty": False,
    "expected_wait_codes_contains": [WAIT_REASON_DATA_MISSING],
    "expected_blocker_status": {
        "individual_fund_flow": "query_failed",
        "lhb_status": "normal_no_data",
    },
}

# S3: 数据不足观察 + 仅过期半年报 — HY section renders a stale warning, but
# data_blockers/wait_reason_codes are preserved verbatim.
SCENARIO_STALE_HALF_YEAR = {
    "id": "s3_data_missing__stale_hy",
    "symbol": "600519.SH",
    "name": "贵州茅台",
    "description": "数据不足观察 + 仅过期半年报命中（STALE）",
    "decision": "HOLD",
    "action_label": "数据不足观察",
    "research_direction": "中性",
    "execution_action": "WAIT",
    "final_trade_decision": _DATA_MISSING_FINAL_DECISION,
    "raw_evidence": _DATA_MISSING_RAW_EVIDENCE,
    "expected_hy_status": "STALE",
    "expected_hy_block_nonempty": True,
    "expected_wait_codes_contains": [WAIT_REASON_DATA_MISSING],
    "expected_blocker_status": {
        "individual_fund_flow": "query_failed",
        "lhb_status": "normal_no_data",
    },
}

# S4: 数据不足观察 + 低置信半年报（缺报告期）— HY section renders a
# low-confidence warning; half-year knowledge must not push the candidate up.
SCENARIO_LOW_CONFIDENCE_HALF_YEAR = {
    "id": "s4_data_missing__low_confidence_hy",
    "symbol": "603296.SH",
    "name": "华勤技术",
    "description": "数据不足观察 + 低置信半年报命中（LOW_CONFIDENCE，缺报告期）",
    "decision": "HOLD",
    "action_label": "数据不足观察",
    "research_direction": "中性",
    "execution_action": "WAIT",
    "final_trade_decision": _DATA_MISSING_FINAL_DECISION,
    "raw_evidence": _DATA_MISSING_RAW_EVIDENCE,
    "expected_hy_status": "LOW_CONFIDENCE",
    "expected_hy_block_nonempty": True,
    "expected_wait_codes_contains": [WAIT_REASON_DATA_MISSING],
    "expected_blocker_status": {
        "individual_fund_flow": "query_failed",
        "lhb_status": "normal_no_data",
    },
}

# S5: 方向性结论 (ENTER/看多) + 有半年报事实 — proves a fresh HY hit does
# NOT push a directional report into WAIT or alter the action gate.
SCENARIO_DIRECTIONAL_WITH_FACTS = {
    "id": "s5_directional__has_facts",
    "symbol": "000977.SZ",
    "name": "浪潮信息",
    "description": "方向性结论（看多 ENTER） + 半年报事实命中（HAS_FACTS）",
    "decision": "BUY",
    "action_label": "条件入场",
    "research_direction": "看多",
    "execution_action": "ENTER",
    "final_trade_decision": _ENTER_FINAL_DECISION,
    "raw_evidence": {
        "stock_data": _OHLCV_HAS_DATA,
        "fund_flow_individual": "主力资金净流入 1.2 亿",
        "lhb": "龙虎榜：机构净买入 5000 万",
    },
    "expected_hy_status": "HAS_FACTS",
    "expected_hy_block_nonempty": True,
    "expected_wait_codes_contains": [],
    "expected_blocker_status": {},
}

# S6: 持有结论 (HOLD/持有) + 有半年报事实 — proves HOLD action is preserved.
SCENARIO_HOLD_WITH_FACTS = {
    "id": "s6_hold__has_facts",
    "symbol": "000977.SZ",
    "name": "浪潮信息",
    "description": "持有结论（HOLD 不加仓） + 半年报事实命中（HAS_FACTS）",
    "decision": "HOLD",
    "action_label": "持有",
    "research_direction": "偏多",
    "execution_action": "HOLD",
    "final_trade_decision": _HOLD_FINAL_DECISION,
    "raw_evidence": {
        "stock_data": _OHLCV_HAS_DATA,
        "fund_flow_individual": "主力资金净流入 0.3 亿",
        "lhb": "龙虎榜：未上榜",
    },
    "expected_hy_status": "HAS_FACTS",
    "expected_hy_block_nonempty": True,
    "expected_wait_codes_contains": [],
    "expected_blocker_status": {},
}

# S7: 仅事实冲突 — half_year_facts CONFLICT status must surface without
# crashing the report pipeline.
SCENARIO_CONFLICT_HALF_YEAR = {
    "id": "s7_conflict_hy",
    "symbol": "888888.SZ",
    "name": "冲突公司",
    "description": "数据不足观察 + 半年报事实冲突（CONFLICT）",
    "decision": "HOLD",
    "action_label": "数据不足观察",
    "research_direction": "中性",
    "execution_action": "WAIT",
    "final_trade_decision": _DATA_MISSING_FINAL_DECISION,
    "raw_evidence": _DATA_MISSING_RAW_EVIDENCE,
    "expected_hy_status": "CONFLICT",
    "expected_hy_block_nonempty": True,
    "expected_wait_codes_contains": [WAIT_REASON_DATA_MISSING],
    "expected_blocker_status": {
        "individual_fund_flow": "query_failed",
        "lhb_status": "normal_no_data",
    },
    "use_conflict_kb": True,
}


ALL_SCENARIOS = [
    SCENARIO_DATA_MISSING_WITH_FACTS,
    SCENARIO_NO_HALF_YEAR_DATA,
    SCENARIO_STALE_HALF_YEAR,
    SCENARIO_LOW_CONFIDENCE_HALF_YEAR,
    SCENARIO_DIRECTIONAL_WITH_FACTS,
    SCENARIO_HOLD_WITH_FACTS,
]


# ── 1. Core replay: create_report() preserves the action gate ─────────────


@pytest.mark.parametrize("scenario", ALL_SCENARIOS, ids=[s["id"] for s in ALL_SCENARIOS])
def test_replay_preserves_action_gate(scenario, fixture_kb: Path, monkeypatch):
    """[HY-004] attach_report_half_year_facts must be read-only with respect
    to the strong action gate — decision / action_label / research_direction /
    execution_action survive the HY attach."""
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
    db, engine = _make_db()
    try:
        report = report_service.create_report(
            db=db,
            symbol=scenario["symbol"],
            trade_date="2026-07-11",
            decision=scenario["decision"],
            result_data=_build_result_data(scenario),
        )

        # Strong action gate preserved.
        assert report.decision == scenario["decision"]
        assert report.action_label == scenario["action_label"]
        assert report.research_direction == scenario["research_direction"]
        assert report.execution_action == scenario["execution_action"]

        # result_data copies also unchanged.
        assert report.result_data["action_label"] == scenario["action_label"]
        assert report.result_data["research_direction"] == scenario["research_direction"]
        assert report.result_data["execution_action"] == scenario["execution_action"]
    finally:
        db.close()
        engine.dispose()


@pytest.mark.parametrize("scenario", ALL_SCENARIOS, ids=[s["id"] for s in ALL_SCENARIOS])
def test_replay_wait_reason_codes_preserved(
    scenario, fixture_kb: Path, monkeypatch
):
    """[HY-004] wait_reason_codes are owned by REPORT-UX-003/DATA-021 and
    must NOT be wiped or rewritten by the HY attach step."""
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
    db, engine = _make_db()
    try:
        report = report_service.create_report(
            db=db,
            symbol=scenario["symbol"],
            trade_date="2026-07-11",
            decision=scenario["decision"],
            result_data=_build_result_data(scenario),
        )

        codes = report.result_data.get("wait_reason_codes") or []
        for expected_code in scenario["expected_wait_codes_contains"]:
            assert expected_code in codes, (
                f"expected wait_reason_codes to contain {expected_code}, "
                f"got {codes}"
            )

        # Non-WAIT action must produce no codes — HY hit must not invent a
        # WAIT reason.
        if scenario["execution_action"] != "WAIT":
            assert codes == [], (
                f"non-WAIT action {scenario['execution_action']} must have "
                f"empty wait_reason_codes, got {codes}"
            )
    finally:
        db.close()
        engine.dispose()


@pytest.mark.parametrize("scenario", ALL_SCENARIOS, ids=[s["id"] for s in ALL_SCENARIOS])
def test_replay_data_blockers_preserved(
    scenario, fixture_kb: Path, monkeypatch
):
    """[HY-004] data_blockers are owned by DATA-021 and must survive the HY
    attach step verbatim. Half-year facts are NOT a substitute for missing
    market data — this is the core isolation contract."""
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
    db, engine = _make_db()
    try:
        report = report_service.create_report(
            db=db,
            symbol=scenario["symbol"],
            trade_date="2026-07-11",
            decision=scenario["decision"],
            result_data=_build_result_data(scenario),
        )

        blockers = report.result_data.get("data_blockers") or []
        by_key = {b["key"]: b for b in blockers}

        for field_key, expected_status in scenario["expected_blocker_status"].items():
            assert field_key in by_key, (
                f"{field_key} missing from data_blockers; HY hit must not "
                f"mask real data gaps"
            )
            assert by_key[field_key]["status"] == expected_status, (
                f"{field_key}: expected {expected_status}, "
                f"got {by_key[field_key]['status']}"
            )

        # Buy Level / Risk Level text must survive intact.
        assert "Buy Level:" in report.final_trade_decision
        assert "Risk Level:" in report.final_trade_decision
    finally:
        db.close()
        engine.dispose()


@pytest.mark.parametrize("scenario", ALL_SCENARIOS, ids=[s["id"] for s in ALL_SCENARIOS])
def test_replay_half_year_facts_block_matches_state(
    scenario, fixture_kb: Path, monkeypatch
):
    """[HY-004] half_year_facts_block / summary / status must reflect the
    HY state for the symbol, but they must NOT bleed into action_label /
    wait_reason_codes / data_blockers."""
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
    db, engine = _make_db()
    try:
        report = report_service.create_report(
            db=db,
            symbol=scenario["symbol"],
            trade_date="2026-07-11",
            decision=scenario["decision"],
            result_data=_build_result_data(scenario),
        )

        result_data = report.result_data
        block = result_data.get("half_year_facts_block") or ""
        summary = result_data.get("half_year_facts_summary")
        status = result_data.get("half_year_facts_status")

        # Status always present (short filter token).
        assert isinstance(status, str)
        assert status == scenario["expected_hy_status"]

        # Summary always a dict (so UI can show "无半年报事实命中" instead of
        # leaving a hole).
        assert isinstance(summary, dict)

        if scenario["expected_hy_block_nonempty"]:
            assert block, (
                f"expected non-empty half_year_facts_block for "
                f"{scenario['symbol']} ({scenario['expected_hy_status']})"
            )
            assert summary.get("status") not in (None, "")
        else:
            # NO_DATA → block is empty (per render contract) — UI hides the
            # section. Summary is still attached with status.
            assert block == ""
            # Critical: NO_DATA must NOT be confused with FAILED.
            assert status != "FAILED"
            assert summary.get("status") in (
                "NORMAL_NO_DATA",
                None,
            )

        # Critical isolation: the HY block presence must NOT change the
        # action_label.
        assert report.action_label == scenario["action_label"]
    finally:
        db.close()
        engine.dispose()


# ── 2. Headline acceptance: HY facts must NOT mask DATA_MISSING ────────────


def test_headline_facts_do_not_mask_data_missing(
    fixture_kb: Path, monkeypatch
):
    """[HY-004] the headline acceptance criterion:

    A report on 000977 (浪潮信息, fresh half-year facts) whose raw_evidence
    shows ``individual_fund_flow=query_failed`` must STILL expose:
      - ``wait_reason_codes`` containing ``DATA_MISSING``
      - ``data_blockers`` containing ``individual_fund_flow: query_failed``
      - ``action_label == '数据不足观察'``
      - ``half_year_facts_status == 'HAS_FACTS'`` (block populated alongside)

    The 半年报事实对照 block is allowed to render alongside, but it must be
    framed as supplementary background — never as a substitute for the
    missing market data.
    """
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
    db, engine = _make_db()
    try:
        report = report_service.create_report(
            db=db,
            symbol=SCENARIO_DATA_MISSING_WITH_FACTS["symbol"],
            trade_date="2026-07-11",
            decision=SCENARIO_DATA_MISSING_WITH_FACTS["decision"],
            result_data=_build_result_data(SCENARIO_DATA_MISSING_WITH_FACTS),
        )

        # 1. The HY section DID render — so the test is meaningful.
        block = report.result_data.get("half_year_facts_block") or ""
        assert block, "expected 浪潮信息 HY hit to populate half_year_facts_block"
        assert "浪潮信息" in block or "000977" in block
        assert report.result_data.get("half_year_facts_status") == "HAS_FACTS"

        # 2. Despite the HY hit, the data-gap reason survived.
        codes = report.result_data.get("wait_reason_codes") or []
        assert WAIT_REASON_DATA_MISSING in codes, (
            "DATA_MISSING was masked by half_year_facts hit — this is the "
            "exact regression HY-004 exists to catch"
        )

        # 3. data_blockers still report the query failure.
        blockers = {
            b["key"]: b for b in (report.result_data.get("data_blockers") or [])
        }
        assert blockers["individual_fund_flow"]["status"] == "query_failed"

        # 4. action_label is STILL 数据不足观察 — the HY hit did not turn it
        # into 条件入场 / 持有 etc.
        assert report.action_label == "数据不足观察"
    finally:
        db.close()
        engine.dispose()


# ── 3. NO_DATA must NOT be confused with FAILED ──────────────────────────


def test_no_half_year_data_is_gap_not_failure(
    fixture_kb: Path, monkeypatch
):
    """[HY-004] 无半年报事实时显示缺口（NO_DATA），不误判 FAILED.

    A symbol with no half-year pages (000001 平安银行 — non-financial control
    page) must surface ``half_year_facts_status='NO_DATA'`` (gap), NOT
    ``FAILED`` (provider exception). The block is empty so the UI hides the
    section, but the summary still carries status so the UI can render a
    "无半年报事实命中" hint instead of an error.
    """
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
    db, engine = _make_db()
    try:
        report = report_service.create_report(
            db=db,
            symbol=SCENARIO_NO_HALF_YEAR_DATA["symbol"],
            trade_date="2026-07-11",
            decision=SCENARIO_NO_HALF_YEAR_DATA["decision"],
            result_data=_build_result_data(SCENARIO_NO_HALF_YEAR_DATA),
        )

        assert report.result_data.get("half_year_facts_status") == "NO_DATA"
        assert report.result_data.get("half_year_facts_block") == ""
        summary = report.result_data.get("half_year_facts_summary")
        assert isinstance(summary, dict)
        # Gap, not failure.
        assert summary.get("status") == "NORMAL_NO_DATA"
        assert summary.get("matched_count") == 0
    finally:
        db.close()
        engine.dispose()


# ── 4. Conflict status surfaces without crashing ─────────────────────────


def test_conflict_status_surfaces_without_crashing(
    conflict_kb: Path, monkeypatch
):
    """[HY-004] half_year_facts CONFLICT status must surface without
    crashing the report pipeline. The conflict_detail enters the
    "待验证事项" sub-section so the UI can flag it for Tree Work review."""
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(conflict_kb))
    db, engine = _make_db()
    try:
        report = report_service.create_report(
            db=db,
            symbol=SCENARIO_CONFLICT_HALF_YEAR["symbol"],
            trade_date="2026-07-11",
            decision=SCENARIO_CONFLICT_HALF_YEAR["decision"],
            result_data=_build_result_data(SCENARIO_CONFLICT_HALF_YEAR),
        )

        # CONFLICT status surfaces.
        assert report.result_data.get("half_year_facts_status") == "CONFLICT"
        block = report.result_data.get("half_year_facts_block") or ""
        assert block, "expected non-empty block for CONFLICT scenario"
        summary = report.result_data.get("half_year_facts_summary")
        assert isinstance(summary, dict)
        assert summary.get("has_conflict") is True
        # 待验证事项 sub-section includes the conflict detail.
        needs_verify = summary.get("sections", {}).get("needs_verification", {})
        assert needs_verify.get("available") is True
        assert any(
            "冲突" in (item or "") or "revenue" in (item or "")
            for item in needs_verify.get("preview", [])
        ), f"conflict detail missing from needs_verification preview: {needs_verify}"

        # Action gate preserved.
        assert report.action_label == "数据不足观察"
        assert WAIT_REASON_DATA_MISSING in (
            report.result_data.get("wait_reason_codes") or []
        )
    finally:
        db.close()
        engine.dispose()


# ── 5. ReportResponse schema round-trip ──────────────────────────────────


@pytest.mark.parametrize("scenario", ALL_SCENARIOS, ids=[s["id"] for s in ALL_SCENARIOS])
def test_replay_response_schema_serializes_all_layers(
    scenario, fixture_kb: Path, monkeypatch
):
    """[HY-004] historical report response schema does not break —
    ReportResponse.model_validate + model_dump round-trip preserves all
    layers (KB / HY / wait_reason)."""
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
    db, engine = _make_db()
    try:
        report = report_service.create_report(
            db=db,
            symbol=scenario["symbol"],
            trade_date="2026-07-11",
            decision=scenario["decision"],
            result_data=_build_result_data(scenario),
        )

        # Attach top-level fields (mirrors api/main.py response path).
        from api.main import (
            ReportResponse,
            _attach_report_data_blockers_for_response,
        )

        _attach_report_data_blockers_for_response(report)

        # Schema round-trip succeeds.
        resp = ReportResponse.model_validate(report)
        dumped = resp.model_dump()

        # Strong action gate.
        assert dumped["action_label"] == scenario["action_label"]
        assert dumped["execution_action"] == scenario["execution_action"]

        # HY fields are declared on ReportResponse — at minimum the keys
        # exist, even when None.
        for key in (
            "half_year_facts_block",
            "half_year_facts_summary",
            "half_year_facts_status",
        ):
            assert key in dumped, f"ReportResponse dropped HY field: {key}"

        # Status always mirrored.
        assert dumped["half_year_facts_status"] == scenario["expected_hy_status"]

        # For HAS_FACTS scenarios the block must round-trip as non-empty
        # markdown.
        if scenario["expected_hy_block_nonempty"]:
            assert dumped["half_year_facts_block"]
            assert isinstance(dumped["half_year_facts_summary"], dict)
    finally:
        db.close()
        engine.dispose()


# ── 6. attach_report_half_year_facts is purely additive ──────────────────


def test_attach_report_half_year_facts_is_additive_only(
    fixture_kb: Path, monkeypatch
):
    """[HY-004] attach_report_half_year_facts must be purely additive:
    every pre-existing result_data key (including data_blockers /
    wait_reason_codes / action_label / local_knowledge_block) is preserved
    byte-for-byte, and only HY keys are inserted."""
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))

    # Build a result_data that already contains data_blockers + wait_reason_codes
    # + local_knowledge_block (mirrors production create_report order).
    base = _build_result_data(SCENARIO_DATA_MISSING_WITH_FACTS)
    base = report_service.attach_report_data_blockers(base)
    resolved = report_service.resolve_report_fields(result_data=base)
    base = report_service.attach_report_wait_reason_codes(
        base, wait_reason_codes=resolved.get("wait_reason_codes")
    )
    base = report_service.attach_report_local_knowledge(
        copy.deepcopy(base), symbol="000977"
    )
    pre_attach_keys = set(base.keys())
    pre_attach_snapshot = {k: copy.deepcopy(v) for k, v in base.items()}

    enriched = report_service.attach_report_half_year_facts(
        copy.deepcopy(base), symbol="000977"
    )

    # Original keys all preserved.
    for key in pre_attach_keys:
        assert key in enriched, f"attach dropped pre-existing key: {key}"

    # Original values are byte-for-byte identical (additive only).
    for key, value in pre_attach_snapshot.items():
        assert enriched[key] == value, (
            f"attach mutated pre-existing value for key: {key}"
        )

    # HY keys inserted (HAS_FACTS scenario → block non-empty).
    assert "half_year_facts_block" in enriched
    assert "half_year_facts_summary" in enriched
    assert "half_year_facts_status" in enriched
    assert enriched["half_year_facts_block"]
    assert enriched["half_year_facts_status"] == "HAS_FACTS"

    # The strong action gate survived.
    assert enriched["action_label"] == "数据不足观察"
    assert enriched["execution_action"] == "WAIT"
    assert WAIT_REASON_DATA_MISSING in (enriched.get("wait_reason_codes") or [])


# ── 7. Cached raw_evidence entry is reused (no re-query) ─────────────────


def test_cached_raw_evidence_entry_is_reused(
    fixture_kb: Path, monkeypatch
):
    """[HY-004] when ``metadata.raw_evidence.half_year_facts`` is already
    present (data_collector populated it), attach_report_half_year_facts
    MUST reuse it instead of re-querying the knowledge base.

    We verify reuse by pointing AUTO_DEV_KNOWLEDGE_ROOT at a directory that
    has NO half-year pages for the symbol; if the attach function re-queried,
    it would return NO_DATA. Because the cached payload says HAS_DATA, the
    only way the block stays populated is by reusing the cached entry."""
    # Build a cached payload from the real fixture KB.
    real_result = query_half_year_facts(str(fixture_kb), symbol="000977")
    assert real_result.status == "HAS_DATA"
    cached_entry = build_half_year_facts_raw_evidence_entry(
        real_result, trade_date="2026-07-11", fetched_at="2026-07-11T09:30:00Z"
    )

    # Point KB root at an empty dir — re-query would yield NO_DATA/FAILED.
    empty_root = fixture_kb.parent / "empty_kb_hy004"
    empty_root.mkdir(exist_ok=True)
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(empty_root))

    base = _build_result_data(SCENARIO_DATA_MISSING_WITH_FACTS)
    base.setdefault("metadata", {}).setdefault("raw_evidence", {})[
        "half_year_facts"
    ] = cached_entry

    enriched = report_service.attach_report_half_year_facts(
        copy.deepcopy(base), symbol="000977"
    )

    # The cached entry was reused → status stays HAS_FACTS (not NO_DATA).
    assert enriched.get("half_year_facts_status") == "HAS_FACTS", (
        "expected cached entry reuse (HAS_FACTS), got "
        f"{enriched.get('half_year_facts_status')} — attach function likely "
        "re-queried the (empty) KB instead of reusing the cached payload"
    )
    assert enriched.get("half_year_facts_block")
    summary = enriched.get("half_year_facts_summary")
    assert isinstance(summary, dict)
    assert summary.get("matched_count") == 1
    assert summary.get("latest_period") == "2025H1"


def test_legacy_row_without_cached_entry_requeries(
    fixture_kb: Path, monkeypatch
):
    """[HY-004] legacy rows that predate HY-004 (no cached entry) must
    fall back to a re-query by symbol. This ensures old reports still
    display the HY section when read back."""
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))

    base = _build_result_data(SCENARIO_DATA_MISSING_WITH_FACTS)
    # No half_year_facts entry in raw_evidence — simulates a legacy row.

    enriched = report_service.attach_report_half_year_facts(
        copy.deepcopy(base), symbol="000977"
    )

    # Fallback re-query populated the HY section.
    assert enriched.get("half_year_facts_status") == "HAS_FACTS"
    assert enriched.get("half_year_facts_block")


def test_attach_skips_silently_without_symbol_or_cache():
    """[HY-004] when neither a cached entry nor a symbol is available, the
    attach step must be a silent no-op (return result_data unchanged)."""
    base = {"market_report": "占位", "final_trade_decision": "x"}
    enriched = report_service.attach_report_half_year_facts(
        copy.deepcopy(base), symbol=None
    )
    # No HY keys added; original keys preserved.
    assert enriched == base
    assert "half_year_facts_block" not in enriched


def test_attach_handles_invalid_result_data():
    """[HY-004] non-dict result_data is returned unchanged (no crash)."""
    assert report_service.attach_report_half_year_facts(None, symbol="000977") is None
    assert report_service.attach_report_half_year_facts("string", symbol="000977") == "string"


# ── 8. Block structure: 事实 / 管理层表述 / 待验证事项 / 风险 ────────────


def test_block_has_four_required_sections(fixture_kb: Path, monkeypatch):
    """[HY-004] 区块明确分为：事实、管理层表述、待验证事项、风险.

    The ``half_year_facts_summary.sections`` dict must expose all four
    sub-sections so the frontend can render them as distinct cards without
    re-parsing the markdown block.
    """
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
    db, engine = _make_db()
    try:
        report = report_service.create_report(
            db=db,
            symbol="000977.SZ",
            trade_date="2026-07-11",
            decision="HOLD",
            result_data=_build_result_data(SCENARIO_DATA_MISSING_WITH_FACTS),
        )

        summary = report.result_data.get("half_year_facts_summary") or {}
        sections = summary.get("sections") or {}

        # All four sub-sections present.
        assert set(sections.keys()) == {
            "facts",
            "management_commentary",
            "needs_verification",
            "risks",
        }

        # Each sub-section has the required shape.
        for name, sec in sections.items():
            assert isinstance(sec, dict), f"section {name} not a dict"
            assert "available" in sec
            assert "count" in sec
            assert "preview" in sec
            assert isinstance(sec["preview"], list)

        # qualified fixture (浪潮信息) has facts + management_commentary +
        # forward_guidance (needs_verification) + risks.
        assert sections["facts"]["available"] is True
        assert sections["facts"]["count"] >= 1
        assert sections["management_commentary"]["available"] is True
        assert sections["needs_verification"]["available"] is True
        assert sections["risks"]["available"] is True
    finally:
        db.close()
        engine.dispose()


# ── 9. Stale / low-confidence must NOT inflate to HAS_FACTS ──────────────


def test_stale_and_low_confidence_do_not_inflate_to_has_facts(
    fixture_kb: Path, monkeypatch
):
    """[HY-004] STALE / LOW_CONFIDENCE half-year facts must NOT be
    misreported as HAS_FACTS. The status token must reflect the real state
    so the frontend renders the correct warning chip."""
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
    db, engine = _make_db()
    try:
        # Stale-only symbol (贵州茅台 600519).
        stale_report = report_service.create_report(
            db=db,
            symbol="600519.SH",
            trade_date="2026-07-11",
            decision="HOLD",
            result_data=_build_result_data(SCENARIO_STALE_HALF_YEAR),
        )
        assert stale_report.result_data.get("half_year_facts_status") == "STALE"
        stale_summary = stale_report.result_data.get("half_year_facts_summary") or {}
        assert stale_summary.get("has_stale") is True

        # Low-confidence symbol (华勤技术 603296 — missing_period).
        low_report = report_service.create_report(
            db=db,
            symbol="603296.SH",
            trade_date="2026-07-11",
            decision="HOLD",
            result_data=_build_result_data(SCENARIO_LOW_CONFIDENCE_HALF_YEAR),
        )
        assert low_report.result_data.get("half_year_facts_status") == "LOW_CONFIDENCE"
        low_summary = low_report.result_data.get("half_year_facts_summary") or {}
        assert low_summary.get("has_missing_period") is True
    finally:
        db.close()
        engine.dispose()


# ── 10. from_dict round-trip (HalfYearFactsQueryResult) ──────────────────


def test_half_year_facts_query_result_from_dict_roundtrip():
    """[HY-004] HalfYearFactsQueryResult.from_dict round-trips to_dict
    output so the report attach chain can reuse cached payloads."""
    original = HalfYearFactsQueryResult(
        status="HAS_DATA",
        symbol="000977",
        name="浪潮信息",
        latest_period="2025H1",
        latest_disclosure_date="2026-08-29",
        data_status=DATA_FRESH,
    )
    d = original.to_dict()
    restored = HalfYearFactsQueryResult.from_dict(d)
    assert restored.status == original.status
    assert restored.symbol == original.symbol
    assert restored.latest_period == original.latest_period
    assert restored.data_status == original.data_status


def test_half_year_facts_query_result_from_dict_tolerant():
    """[HY-004] from_dict tolerates partial / malformed payloads without
    crashing (legacy rows may have missing keys)."""
    # Empty dict → defaults.
    r = HalfYearFactsQueryResult.from_dict({})
    assert r.status == "NORMAL_NO_DATA"
    assert r.pages == []

    # Non-dict input → empty result.
    r2 = HalfYearFactsQueryResult.from_dict("not a dict")  # type: ignore[arg-type]
    assert r2.pages == []

    # Partial payload with malformed pages.
    r3 = HalfYearFactsQueryResult.from_dict({
        "status": "HAS_DATA",
        "pages": [
            {"rel_path": "a.md", "title": "A", "financial_period": "2025H1"},
            "not a dict",
            None,
        ],
    })
    assert r3.status == "HAS_DATA"
    assert len(r3.pages) == 1  # malformed entries skipped
    assert r3.pages[0].financial_period == "2025H1"


# ── 11. data_collector integration: half_year_facts in raw_evidence ─────


def test_data_collector_populates_half_year_facts_raw_evidence(
    fixture_kb: Path, monkeypatch
):
    """[HY-004] DataCollector.build_raw_evidence must populate the
    ``half_year_facts`` raw_evidence entry so the attach chain can reuse
    it (no re-query on report read)."""
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
    from tradingagents.graph.data_collector import DataCollector

    dc = DataCollector()
    # Pre-populate cache so build_raw_evidence does not short-circuit to {}.
    dc._cache["000977_2026-07-11"] = {"stock_data": "x" * 100}
    raw_ev = dc.build_raw_evidence("000977", "2026-07-11")

    assert "half_year_facts" in raw_ev, (
        "data_collector.build_raw_evidence must populate half_year_facts entry"
    )
    hy_entry = raw_ev["half_year_facts"]
    assert isinstance(hy_entry, dict)
    assert hy_entry["field"] == "half_year_facts"
    assert hy_entry["vendor"] == "tree_work_wiki"
    # 浪潮信息 has a qualified half-year page → HAS_DATA.
    assert hy_entry["status"] == "HAS_DATA"
    assert isinstance(hy_entry["raw"], dict)
    assert hy_entry["raw"]["status"] == "HAS_DATA"
    assert hy_entry["raw"]["latest_period"] == "2025H1"
    # Also confirm local_knowledge still present (HY injection didn't clobber).
    assert "local_knowledge" in raw_ev


def test_data_collector_half_year_facts_failure_safe(
    fixture_kb: Path, monkeypatch
):
    """[HY-004] half_year_facts entry is failure-safe: even when the KB
    has no half-year data, build_raw_evidence must NOT crash; it produces
    a NORMAL_NO_DATA entry instead."""
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
    from tradingagents.graph.data_collector import DataCollector

    dc = DataCollector()
    dc._cache["000001_2026-07-11"] = {"stock_data": "x" * 100}
    # 000001 (平安银行) has only a non-financial page → NO_DATA, not crash.
    raw_ev = dc.build_raw_evidence("000001", "2026-07-11")
    assert "half_year_facts" in raw_ev
    assert raw_ev["half_year_facts"]["status"] == "NORMAL_NO_DATA"
