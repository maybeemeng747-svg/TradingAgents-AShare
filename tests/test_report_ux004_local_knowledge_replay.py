# [REPORT-UX-004] local_knowledge_replay
"""REPORT-UX-004 — 本地知识补充区块历史报告回放验收.

Replays five historical-report fixtures through ``report_service.create_report()``
+ ``_attach_report_data_blockers_for_response`` + ``ReportResponse.model_validate``
to verify that the three explanatory layers do NOT pollute each other:

  1. **本地知识补充** (KB-003/KB-008) — ``local_knowledge_block`` /
     ``local_knowledge_summary`` / ``research_attention_*`` — only additive
     background information.
  2. **研报关注度** (KB-008/KB-009) — ``research_attention_score`` /
     ``research_attention_effective_score`` / decay fields — research priority
     hint, never a strong-action trigger.
  3. **数据不足观察原因** (REPORT-UX-003/DATA-021) — ``wait_reason_codes`` /
     ``data_blockers`` — the *real* reason a report ended in WAIT.

Headline acceptance: even when the local knowledge block has a strong fresh
hit (华勤技术 603296), a report whose raw_evidence shows
``individual_fund_flow=query_failed`` must STILL surface
``wait_reason_codes=[DATA_MISSING]`` and ``action_label=数据不足观察``. The
local knowledge hit must NOT be misread as market-truth or used to override
the data gap.

Coverage matrix (5 fixtures, ≥ required 3-5):

| # | Scenario            | KB hit          | Action      | wait_reason_codes     |
|---|---------------------|-----------------|-------------|-----------------------|
| S1| 数据不足 + KB 命中  | HAS_DATA 603296 | WAIT 观察   | DATA_MISSING          |
| S2| 数据不足 + 无命中   | NORMAL_NO_DATA  | WAIT 观察   | DATA_MISSING          |
| S3| 数据不足 + 仅 stale | STALE 600000    | WAIT 观察   | DATA_MISSING          |
| S4| 数据不足 + 低置信   | LOW_CONF DELL   | WAIT 观察   | DATA_MISSING          |
| S5| 方向性 + KB 命中    | HAS_DATA 603296 | ENTER 看多  | (empty — non-WAIT)    |

No live API, no LLM, no prompts, no prod DB — all fixtures are inline and the
DB is an in-memory SQLite instance. The knowledge tree is a tmp_path fixture
mirroring KB-007/KB-008 (so KB-008 research attention scores are non-zero for
603296).
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, List

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.database import Base
from api.services import report_service
from tests.test_kb007_research_attention import (
    _COMPANY_PAGE_A,
    _COMPANY_PAGE_B,
    _EXPIRED_PAGE,
    _FUND_PAGE,
    _HK_PAGE,
    _NO_SYMBOLS_PAGE,
    _SCORE_TABLE_PAGE,
    _UNLISTED_PAGE,
    _US_TODO_PAGE,
)
from tradingagents.dataflows.local_knowledge_audit import INVESTMENT_SUBDIR
from tradingagents.graph.signal_processing import (
    WAIT_REASON_DATA_MISSING,
)


# ── Inline helpers ────────────────────────────────────────────────────────


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture()
def fixture_kb(tmp_path: Path) -> Path:
    """Mini Tree Work 知识库（与 KB-007/KB-008/KB-011 同款 fixture）。

    命中映射：
      - 603296 / 华勤技术      → HAS_DATA（3 页命中，KB-008 关注度 > 0）
      - 600000 / 浦发银行      → STALE（仅在过期页命中）
      - DELL.US               → LOW_CONFIDENCE（仅在待补充页命中）
      - 999999                → NORMAL_NO_DATA
    """
    inv = tmp_path / INVESTMENT_SUBDIR
    _write(inv / "华勤技术603296-超节点.md", _COMPANY_PAGE_A)
    _write(inv / "AI算力基础设施-公司评分表.md", _SCORE_TABLE_PAGE)
    _write(inv / "华勤技术-深度研究.md", _COMPANY_PAGE_B)
    _write(inv / "腾讯控股-游戏复苏.md", _HK_PAGE)
    _write(inv / "Dell-FQ127-待补充.md", _US_TODO_PAGE)
    _write(inv / "沪深300ETF-指数跟踪.md", _FUND_PAGE)
    _write(inv / "某私募主体-调研纪要.md", _UNLISTED_PAGE)
    _write(inv / "某周期股-已过期.md", _EXPIRED_PAGE)
    _write(inv / "行业综述-无标的.md", _NO_SYMBOLS_PAGE)
    return tmp_path


def _make_db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    SessionLocal = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    return db, engine


# A realistic OHLCV CSV (>50 chars) so ``ohlcv_5d`` is HAS_DATA — used in S5
# where we want a directional ENTER rather than a data-missing WAIT.
_OHLCV_HAS_DATA = (
    "date,open,high,low,close,volume,amount\n"
    "2026-06-18,10.00,10.42,9.91,10.28,128000,1315840\n"
    "2026-06-19,10.28,10.35,10.02,10.11,153000,1546830\n"
    "2026-06-20,10.11,10.50,10.05,10.44,201000,2102240\n"
    "2026-06-21,10.44,10.60,10.30,10.52,176000,1847520\n"
    "2026-06-24,10.52,10.71,10.45,10.66,142000,1513720\n"
)

# Realistic failing raw_evidence for "数据不足观察" scenarios (S1-S4):
# ohlcv has data, but fund flow query genuinely failed. This is the canonical
# case where DATA_MISSING must surface regardless of any KB hit.
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


def _build_result_data(scenario: Dict[str, Any]) -> Dict[str, Any]:
    """Build a result_data payload matching a scenario dict."""
    raw_evidence = copy.deepcopy(scenario.get("raw_evidence", {}))
    return {
        "market_report": "行情报告占位",
        "smart_money_report": "资金面报告占位",
        "news_report": "新闻/公告报告占位",
        "final_trade_decision": scenario["final_trade_decision"],
        "research_direction": scenario.get("research_direction"),
        "execution_action": scenario.get("execution_action"),
        "action_label": scenario.get("action_label"),
        "metadata": {"raw_evidence": raw_evidence},
    }


# ── Replay scenarios (5 fixtures) ─────────────────────────────────────────

# S1: 数据不足观察 + 本地知识 HAS_DATA — the headline regression case.
# The KB section will populate (华勤技术 hit), but DATA_MISSING must survive.
SCENARIO_DATA_MISSING_WITH_KB_HIT = {
    "id": "s1_data_missing__kb_hit",
    "symbol": "603296.SH",
    "name": "华勤技术",
    "description": "数据不足观察 + 本地知识命中（HAS_DATA）",
    "decision": "HOLD",
    "action_label": "数据不足观察",
    "research_direction": "中性",
    "execution_action": "WAIT",
    "final_trade_decision": _DATA_MISSING_FINAL_DECISION,
    "raw_evidence": _DATA_MISSING_RAW_EVIDENCE,
    "expected_kb_status": "HAS_DATA",
    "expected_kb_block_nonempty": True,
    "expected_wait_codes_contains": [WAIT_REASON_DATA_MISSING],
    "expected_blocker_status": {
        "individual_fund_flow": "query_failed",
        "lhb_status": "normal_no_data",
    },
}

# S2: 数据不足观察 + 无本地知识命中 — proves KB absence doesn't break the
# wait_reason / data_blocker chain.
SCENARIO_DATA_MISSING_NO_HIT = {
    "id": "s2_data_missing__no_hit",
    "symbol": "999999.SH",
    "name": "无命中标的",
    "description": "数据不足观察 + 本地知识无命中（NORMAL_NO_DATA）",
    "decision": "HOLD",
    "action_label": "数据不足观察",
    "research_direction": "中性",
    "execution_action": "WAIT",
    "final_trade_decision": _DATA_MISSING_FINAL_DECISION,
    "raw_evidence": _DATA_MISSING_RAW_EVIDENCE,
    "expected_kb_status": "NORMAL_NO_DATA",
    "expected_kb_block_nonempty": False,
    "expected_wait_codes_contains": [WAIT_REASON_DATA_MISSING],
    "expected_blocker_status": {
        "individual_fund_flow": "query_failed",
        "lhb_status": "normal_no_data",
    },
}

# S3: 数据不足观察 + 仅 stale 命中 — KB section renders a stale warning, but
# data_blockers/wait_reason_codes are preserved verbatim.
SCENARIO_DATA_MISSING_STALE_ONLY = {
    "id": "s3_data_missing__stale_only",
    "symbol": "600000.SH",
    "name": "浦发银行",
    "description": "数据不足观察 + 仅过期本地知识命中（STALE）",
    "decision": "HOLD",
    "action_label": "数据不足观察",
    "research_direction": "中性",
    "execution_action": "WAIT",
    "final_trade_decision": _DATA_MISSING_FINAL_DECISION,
    "raw_evidence": _DATA_MISSING_RAW_EVIDENCE,
    "expected_kb_status": "STALE",
    "expected_kb_block_nonempty": True,
    "expected_wait_codes_contains": [WAIT_REASON_DATA_MISSING],
    "expected_blocker_status": {
        "individual_fund_flow": "query_failed",
        "lhb_status": "normal_no_data",
    },
}

# S4: 数据不足观察 + 低置信/待补充 命中 — KB section renders a low-confidence
# warning; research_attention must not push the candidate up.
SCENARIO_DATA_MISSING_LOW_CONFIDENCE = {
    "id": "s4_data_missing__low_confidence",
    "symbol": "DELL.US",
    "name": "Dell",
    "description": "数据不足观察 + 低置信/待补充本地知识命中（LOW_CONFIDENCE）",
    "decision": "HOLD",
    "action_label": "数据不足观察",
    "research_direction": "中性",
    "execution_action": "WAIT",
    "final_trade_decision": _DATA_MISSING_FINAL_DECISION,
    "raw_evidence": _DATA_MISSING_RAW_EVIDENCE,
    "expected_kb_status": "LOW_CONFIDENCE",
    "expected_kb_block_nonempty": True,
    "expected_wait_codes_contains": [WAIT_REASON_DATA_MISSING],
    "expected_blocker_status": {
        "individual_fund_flow": "query_failed",
        "lhb_status": "normal_no_data",
    },
}

# S5: 方向性结论 (ENTER/看多) + 本地知识命中 — proves a fresh KB hit does NOT
# push a directional report into WAIT or alter the action gate. wait_reason_codes
# must be empty because the action is non-WAIT.
SCENARIO_DIRECTIONAL_WITH_KB_HIT = {
    "id": "s5_directional__kb_hit",
    "symbol": "603296.SH",
    "name": "华勤技术",
    "description": "方向性结论（看多 ENTER） + 本地知识命中（HAS_DATA）",
    "decision": "BUY",
    "action_label": "条件入场",
    "research_direction": "看多",
    "execution_action": "ENTER",
    "final_trade_decision": (
        "结论：基本面与资金面共振，等待突破 10.80 后入场。\n"
        "- Buy Level: 3（条件入场）\n"
        "- Risk Level: 2（止损 9.80）\n"
    ),
    "raw_evidence": {
        "stock_data": _OHLCV_HAS_DATA,
        "fund_flow_individual": "主力资金净流入 1.2 亿",
        "lhb": "龙虎榜：机构净买入 5000 万",
    },
    "expected_kb_status": "HAS_DATA",
    "expected_kb_block_nonempty": True,
    "expected_wait_codes_contains": [],
    "expected_blocker_status": {},  # No blockers when data is available.
}

SCENARIOS = [
    SCENARIO_DATA_MISSING_WITH_KB_HIT,
    SCENARIO_DATA_MISSING_NO_HIT,
    SCENARIO_DATA_MISSING_STALE_ONLY,
    SCENARIO_DATA_MISSING_LOW_CONFIDENCE,
    SCENARIO_DIRECTIONAL_WITH_KB_HIT,
]


# ── 1. Core replay: create_report() → response attachment → ReportResponse ─


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
def test_replay_scenario_preserves_action_gate(
    scenario, fixture_kb: Path, monkeypatch
):
    """[REPORT-UX-004] attach_report_local_knowledge must be read-only with
    respect to the strong action gate — decision / action_label /
    research_direction / execution_action survive the KB-003/KB-008 attach."""
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
    db, engine = _make_db()
    try:
        report = report_service.create_report(
            db=db,
            symbol=scenario["symbol"],
            trade_date="2026-07-05",
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


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
def test_replay_scenario_wait_reason_codes_preserved(
    scenario, fixture_kb: Path, monkeypatch
):
    """[REPORT-UX-004] wait_reason_codes are computed from data_blockers /
    action_label and must NOT be wiped or rewritten by the KB attach step."""
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
    db, engine = _make_db()
    try:
        report = report_service.create_report(
            db=db,
            symbol=scenario["symbol"],
            trade_date="2026-07-05",
            decision=scenario["decision"],
            result_data=_build_result_data(scenario),
        )

        codes = report.result_data.get("wait_reason_codes") or []
        for expected_code in scenario["expected_wait_codes_contains"]:
            assert expected_code in codes, (
                f"expected wait_reason_codes to contain {expected_code}, "
                f"got {codes}"
            )

        # Non-WAIT action must produce no codes — KB hit must not invent a
        # WAIT reason.
        if scenario["execution_action"] != "WAIT":
            assert codes == [], (
                f"non-WAIT action {scenario['execution_action']} must have "
                f"empty wait_reason_codes, got {codes}"
            )
    finally:
        db.close()
        engine.dispose()


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
def test_replay_scenario_data_blockers_preserved(
    scenario, fixture_kb: Path, monkeypatch
):
    """[REPORT-UX-004] data_blockers are owned by DATA-021 and must survive
    the KB attach step verbatim. The local knowledge hit is NOT a substitute
    for missing market data."""
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
    db, engine = _make_db()
    try:
        report = report_service.create_report(
            db=db,
            symbol=scenario["symbol"],
            trade_date="2026-07-05",
            decision=scenario["decision"],
            result_data=_build_result_data(scenario),
        )

        blockers = report.result_data.get("data_blockers") or []
        by_key = {b["key"]: b for b in blockers}

        for field_key, expected_status in scenario["expected_blocker_status"].items():
            assert field_key in by_key, (
                f"{field_key} missing from data_blockers; KB hit must not "
                f"mask real data gaps"
            )
            assert by_key[field_key]["status"] == expected_status, (
                f"{field_key}: expected {expected_status}, "
                f"got {by_key[field_key]['status']}"
            )

        # Buy Level / Risk Level text must survive intact (proves the strong
        # decision text is not silently rewritten by an explanatory layer).
        assert "Buy Level:" in report.final_trade_decision
        assert "Risk Level:" in report.final_trade_decision
    finally:
        db.close()
        engine.dispose()


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
def test_replay_scenario_local_knowledge_block_matches_state(
    scenario, fixture_kb: Path, monkeypatch
):
    """[REPORT-UX-004] the local_knowledge_block / summary must reflect the
    KB state for the symbol, but they must NOT bleed into action_label /
    wait_reason_codes."""
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
    db, engine = _make_db()
    try:
        report = report_service.create_report(
            db=db,
            symbol=scenario["symbol"],
            trade_date="2026-07-05",
            decision=scenario["decision"],
            result_data=_build_result_data(scenario),
        )

        result_data = report.result_data
        block = result_data.get("local_knowledge_block") or ""
        summary = result_data.get("local_knowledge_summary")

        if scenario["expected_kb_block_nonempty"]:
            assert block, (
                f"expected non-empty local_knowledge_block for "
                f"{scenario['symbol']} ({scenario['expected_kb_status']})"
            )
            assert isinstance(summary, dict)
            # Status round-trips into the summary so the UI can render the
            # correct chip (HAS_DATA vs STALE vs LOW_CONFIDENCE).
            assert summary.get("status") == scenario["expected_kb_status"]
        else:
            # NORMAL_NO_DATA → block is empty (per render_local_knowledge_block
            # contract) — the UI hides the section.
            assert block == ""
            # The summary is still attached (with status) so the UI can show
            # "无本地知识命中" instead of leaving a hole.
            assert isinstance(summary, dict)
            assert summary.get("status") in (
                "NORMAL_NO_DATA",
                "FAILED",
                None,
            )

        # Critical isolation: the KB block presence must NOT change the
        # action_label away from what the scenario expects. (Catches a
        # regression where a fresh KB hit overrides 数据不足观察.)
        assert report.action_label == scenario["action_label"]
    finally:
        db.close()
        engine.dispose()


# ── 2. Headline acceptance: KB hit must NOT mask DATA_MISSING ─────────────


def test_headline_kb_hit_does_not_mask_data_missing(
    fixture_kb: Path, monkeypatch
):
    """[REPORT-UX-004] the headline acceptance criterion:

    A report on 603296 (华勤技术, fresh KB hit) whose raw_evidence shows
    ``individual_fund_flow=query_failed`` must STILL expose:
      - ``wait_reason_codes`` containing ``DATA_MISSING``
      - ``data_blockers`` containing ``individual_fund_flow: query_failed``
      - ``action_label == '数据不足观察'``

    The 本地知识补充 block is allowed to render alongside, but it must be
    framed as supplementary background — never as a substitute for the
    missing market data.
    """
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
    db, engine = _make_db()
    try:
        report = report_service.create_report(
            db=db,
            symbol=SCENARIO_DATA_MISSING_WITH_KB_HIT["symbol"],
            trade_date="2026-07-05",
            decision=SCENARIO_DATA_MISSING_WITH_KB_HIT["decision"],
            result_data=_build_result_data(SCENARIO_DATA_MISSING_WITH_KB_HIT),
        )

        # 1. The KB section DID render — so the test is meaningful (not a
        # vacuous "no KB hit so nothing to mask").
        block = report.result_data.get("local_knowledge_block") or ""
        assert block, "expected 华勤技术 KB hit to populate local_knowledge_block"
        assert "华勤技术" in block or "603296" in block

        # 2. Despite the KB hit, the data-gap reason survived.
        codes = report.result_data.get("wait_reason_codes") or []
        assert WAIT_REASON_DATA_MISSING in codes, (
            "DATA_MISSING was masked by local knowledge hit — this is the "
            "exact regression REPORT-UX-004 exists to catch"
        )

        # 3. data_blockers still report the query failure.
        blockers = {b["key"]: b for b in (report.result_data.get("data_blockers") or [])}
        assert blockers["individual_fund_flow"]["status"] == "query_failed"

        # 4. action_label is STILL 数据不足观察 — the KB hit did not turn it
        # into 条件入场 / 持有 etc.
        assert report.action_label == "数据不足观察"
    finally:
        db.close()
        engine.dispose()


# ── 3. ReportResponse schema does not break ──────────────────────────────


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
def test_replay_response_schema_serializes_all_layers(
    scenario, fixture_kb: Path, monkeypatch
):
    """[REPORT-UX-004] historical report response schema does not break —
    ReportResponse.model_validate + model_dump round-trip preserves all
    three explanatory layers (KB / research_attention / wait_reason)."""
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
    db, engine = _make_db()
    try:
        report = report_service.create_report(
            db=db,
            symbol=scenario["symbol"],
            trade_date="2026-07-05",
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

        # data_blockers + wait_reason_codes mirrored to top level.
        if scenario["expected_blocker_status"]:
            assert dumped["data_blockers"] is not None
            keys = {b["key"] for b in dumped["data_blockers"]}
            for expected_key in scenario["expected_blocker_status"]:
                assert expected_key in keys

        codes = dumped.get("wait_reason_codes") or []
        for expected_code in scenario["expected_wait_codes_contains"]:
            assert expected_code in codes

        # KB / research_attention fields are declared on ReportResponse (KB-011
        # regression) — at minimum the keys exist, even when None.
        for key in (
            "local_knowledge_block",
            "local_knowledge_summary",
            "research_attention_score",
            "knowledge_theme_count",
            "research_attention_summary",
            "research_attention_block",
        ):
            assert key in dumped, f"ReportResponse dropped KB field: {key}"

        # For HAS_DATA scenarios the block must round-trip as non-empty
        # markdown; the frontend can render it without falling back into
        # result_data.
        if scenario["expected_kb_block_nonempty"]:
            assert dumped["local_knowledge_block"]
            assert isinstance(dumped["local_knowledge_summary"], dict)
    finally:
        db.close()
        engine.dispose()


# ── 4. Stale / low-confidence must NOT inflate research priority ──────────


def test_stale_and_low_confidence_hits_do_not_inflate_research_score(
    fixture_kb: Path, monkeypatch
):
    """[REPORT-UX-004] STALE / LOW_CONFIDENCE hits must surface in the KB
    block (so the user knows they exist) but the effective research attention
    score must NOT be inflated to look like a fresh signal.

    This guards against a regression where stale pages silently bump a
    candidate's research priority despite the explicit KB-009 rule that
    stale/low-confidence pages contribute 0.
    """
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
    db, engine = _make_db()
    try:
        # Stale-only symbol (浦发银行 600000).
        stale_report = report_service.create_report(
            db=db,
            symbol="600000.SH",
            trade_date="2026-07-05",
            decision="HOLD",
            result_data=_build_result_data(SCENARIO_DATA_MISSING_STALE_ONLY),
        )
        stale_summary = stale_report.result_data.get("local_knowledge_summary") or {}
        assert stale_summary.get("status") == "STALE"

        # Low-confidence symbol (DELL.US).
        low_report = report_service.create_report(
            db=db,
            symbol="DELL.US",
            trade_date="2026-07-05",
            decision="HOLD",
            result_data=_build_result_data(SCENARIO_DATA_MISSING_LOW_CONFIDENCE),
        )
        low_summary = low_report.result_data.get("local_knowledge_summary") or {}
        assert low_summary.get("status") == "LOW_CONFIDENCE"

        # Neither stale nor low-confidence should produce a fresh-magnitude
        # research_attention_score. The base score may be small but the
        # *effective* score (post KB-009 decay) must be 0 or near-0.
        for summary_dict in (stale_summary, low_summary):
            effective = summary_dict.get("research_attention_effective_score")
            if effective is not None:
                assert effective <= 0.5, (
                    f"stale/low-confidence hit produced effective_score="
                    f"{effective}, expected near 0 (KB-009 decay)"
                )
    finally:
        db.close()
        engine.dispose()


# ── 5. attach_report_local_knowledge is purely additive ──────────────────


def test_attach_report_local_knowledge_is_additive_only(
    fixture_kb: Path, monkeypatch
):
    """[REPORT-UX-004] attach_report_local_knowledge must be purely additive:
    every pre-existing result_data key (including data_blockers /
    wait_reason_codes / action_label) is preserved byte-for-byte, and only
    KB / research_attention keys are inserted."""
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))

    # Build a result_data that already contains data_blockers + wait_reason_codes
    # (mirrors the production create_report order: data_blockers → resolve →
    # wait_reason_codes → local_knowledge).
    base = _build_result_data(SCENARIO_DATA_MISSING_WITH_KB_HIT)
    base = report_service.attach_report_data_blockers(base)
    resolved = report_service.resolve_report_fields(result_data=base)
    base = report_service.attach_report_wait_reason_codes(
        base, wait_reason_codes=resolved.get("wait_reason_codes")
    )
    pre_attach_keys = set(base.keys())
    pre_attach_snapshot = {k: copy.deepcopy(v) for k, v in base.items()}

    enriched = report_service.attach_report_local_knowledge(
        copy.deepcopy(base), symbol="603296"
    )

    # Original keys all preserved.
    for key in pre_attach_keys:
        assert key in enriched, f"attach dropped pre-existing key: {key}"

    # Original values are byte-for-byte identical (additive only).
    for key, value in pre_attach_snapshot.items():
        assert enriched[key] == value, (
            f"attach mutated pre-existing value for key: {key}"
        )

    # KB keys inserted (HAS_DATA scenario → block non-empty).
    assert "local_knowledge_block" in enriched
    assert "local_knowledge_summary" in enriched
    assert enriched["local_knowledge_block"]

    # The strong action gate survived.
    assert enriched["action_label"] == "数据不足观察"
    assert enriched["execution_action"] == "WAIT"
    assert WAIT_REASON_DATA_MISSING in (enriched.get("wait_reason_codes") or [])
