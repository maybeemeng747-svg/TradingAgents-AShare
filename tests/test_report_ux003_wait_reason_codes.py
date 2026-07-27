"""REPORT-UX-003 — 报告最终结论"数据不足观察"原因分解与前端显示.

Decomposes the catch-all "数据不足观察" label into explainable
``wait_reason_codes`` so the frontend can show *why* a report ended up in
WAIT/观察 instead of leaving the user with a flat black box.

Coverage:
1. At least 3 WAIT fixtures, each producing a *different* dominant reason
   code (DATA_MISSING, GATE_BLOCKED, NO_TRIGGER, RISK_FIRST, CONFLICT,
   NORMAL_NO_DATA).
2. The "data normal but no trigger" case must NOT emit DATA_MISSING — that
   was the user's headline complaint ("三篇报告最终都变成数据不足观察").
3. Non-WAIT actions (ENTER / HOLD) produce an empty code list — the strong
   action gate is unchanged.
4. ``report_service.create_report`` stores the codes inside ``result_data``
   and the API response attach helper surfaces them at top level for the UI.

No live API, no LLM, no prompts, no prod DB — all fixtures are inline and
the DB is an in-memory SQLite instance.

# [REPORT-UX-003] wait_reason_codes
"""

import copy

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.database import Base
from api.services import report_service
from tradingagents.graph.signal_processing import (
    DecisionSemantics,
    WAIT_REASON_CONFLICT,
    WAIT_REASON_ACTION_NOT_APPLICABLE,
    WAIT_REASON_DATA_MISSING,
    WAIT_REASON_GATE_BLOCKED,
    WAIT_REASON_LABELS,
    WAIT_REASON_NO_TRIGGER,
    WAIT_REASON_NORMAL_NO_DATA,
    WAIT_REASON_RISK_FIRST,
    _extract_decision_semantics,
    compute_wait_reason_codes,
)

# Realistic OHLCV (>50 chars) so ``ohlcv_5d`` is treated as HAS_DATA and is
# excluded from the blocker list when we *don't* want DATA_MISSING.
_OHLCV_HAS_DATA = (
    "date,open,high,low,close,volume,amount\n"
    "2026-06-18,10.00,10.42,9.91,10.28,128000,1315840\n"
    "2026-06-19,10.28,10.35,10.02,10.11,153000,1546830\n"
    "2026-06-20,10.11,10.50,10.05,10.44,201000,2102240\n"
    "2026-06-21,10.44,10.60,10.30,10.52,176000,1847520\n"
    "2026-06-24,10.52,10.71,10.45,10.66,142000,1513720\n"
)


def _make_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    return db, engine


def _build_result_data(scenario):
    return {
        "market_report": scenario.get("market_report", "行情报告占位"),
        "smart_money_report": scenario.get("smart_money_report", "资金面报告占位"),
        "news_report": scenario.get("news_report", "新闻/公告报告占位"),
        "final_trade_decision": scenario["final_trade_decision"],
        "research_direction": scenario.get("research_direction"),
        "execution_action": scenario.get("execution_action"),
        "action_label": scenario.get("action_label"),
        "metadata": {"raw_evidence": copy.deepcopy(scenario.get("raw_evidence", {}))},
    }


# ─── Unit: compute_wait_reason_codes pure logic ─────────────────────────────


def test_compute_wait_reason_codes_empty_for_non_wait():
    """ENTER / HOLD / REDUCE / EXIT must produce no codes — the strong action
    gate is unchanged and the UI has no WAIT reason to explain."""
    assert compute_wait_reason_codes(
        execution_action="ENTER",
        research_direction="看多",
        trigger_price=10.5,
    ) == []
    assert compute_wait_reason_codes(
        execution_action="HOLD",
        research_direction="中性",
        has_position=True,
    ) == []
    assert compute_wait_reason_codes(execution_action=None, research_direction=None) == []


def test_compute_wait_reason_codes_data_missing_via_critical_blocker():
    blockers = [
        {"key": "ohlcv_5d", "status": "query_failed"},
        {"key": "individual_fund_flow", "status": "field_missing"},
    ]
    codes = compute_wait_reason_codes(
        execution_action="WAIT",
        research_direction="中性",
        data_blockers=blockers,
    )
    assert WAIT_REASON_DATA_MISSING in codes


def test_compute_wait_reason_codes_data_missing_via_text_indicator():
    """``data_insufficient`` text indicator alone (no severe blocker) should
    also surface DATA_MISSING — the LLM body explicitly said so."""
    codes = compute_wait_reason_codes(
        execution_action="WAIT",
        research_direction="中性",
        data_insufficient=True,
    )
    assert WAIT_REASON_DATA_MISSING in codes


def test_compute_wait_reason_codes_gate_blocked():
    codes = compute_wait_reason_codes(
        execution_action="WAIT",
        research_direction="偏多",
        gate_blocked=True,
    )
    assert WAIT_REASON_GATE_BLOCKED in codes


def test_compute_wait_reason_codes_conflict():
    codes = compute_wait_reason_codes(
        execution_action="WAIT",
        research_direction="中性",
        has_conflict=True,
    )
    assert WAIT_REASON_CONFLICT in codes


def test_compute_wait_reason_codes_risk_first_for_no_position_bearish():
    """偏空/看空 + 未持仓 → 回避 path; the reason is risk avoidance, not data."""
    codes = compute_wait_reason_codes(
        execution_action="WAIT",
        research_direction="偏空",
        has_position=False,
    )
    assert WAIT_REASON_RISK_FIRST in codes
    # Even with data issues present, RISK_FIRST must remain (a bearish
    # no-position stock is avoided regardless of data completeness).
    codes_with_blockers = compute_wait_reason_codes(
        execution_action="WAIT",
        research_direction="看空",
        has_position=False,
        data_blockers=[{"key": "ohlcv_5d", "status": "query_failed"}],
    )
    assert WAIT_REASON_RISK_FIRST in codes_with_blockers
    assert WAIT_REASON_DATA_MISSING in codes_with_blockers


def test_compute_wait_reason_codes_no_trigger_for_bullish_without_price():
    codes = compute_wait_reason_codes(
        execution_action="WAIT",
        research_direction="偏多",
        trigger_price=None,
    )
    assert WAIT_REASON_NO_TRIGGER in codes


def test_compute_wait_reason_codes_no_trigger_suppressed_when_price_present():
    """偏多 + 触发价 in hand should NOT emit NO_TRIGGER (entry condition met)."""
    codes = compute_wait_reason_codes(
        execution_action="WAIT",
        research_direction="偏多",
        trigger_price=10.5,
    )
    assert WAIT_REASON_NO_TRIGGER not in codes


def test_compute_wait_reason_codes_normal_no_data_fallback():
    """The headline acceptance case: data is healthy, no conflict, no
    directional reason — must fall back to NORMAL_NO_DATA, NOT DATA_MISSING."""
    codes = compute_wait_reason_codes(
        execution_action="WAIT",
        research_direction="中性",
        data_blockers=[],  # no severe blockers
        gate_blocked=False,
        has_conflict=False,
        data_insufficient=False,
    )
    assert codes == [WAIT_REASON_NORMAL_NO_DATA]


def test_compute_wait_reason_codes_normal_no_data_with_aux_normal_blockers():
    """Ratings / LHB normal-no-data blockers are *not* severe — they must NOT
    trigger DATA_MISSING. The fallback is NORMAL_NO_DATA."""
    blockers = [
        {"key": "lhb_status", "status": "normal_no_data"},
        {"key": "ratings", "status": "normal_no_data"},
    ]
    codes = compute_wait_reason_codes(
        execution_action="WAIT",
        research_direction="中性",
        data_blockers=blockers,
    )
    assert WAIT_REASON_DATA_MISSING not in codes
    assert codes == [WAIT_REASON_NORMAL_NO_DATA]


def test_all_reason_codes_have_labels():
    """Every exported code must carry a human-readable Chinese label for the UI."""
    expected = {
        WAIT_REASON_DATA_MISSING,
        WAIT_REASON_GATE_BLOCKED,
        WAIT_REASON_CONFLICT,
        WAIT_REASON_NO_TRIGGER,
        WAIT_REASON_RISK_FIRST,
        WAIT_REASON_NORMAL_NO_DATA,
        WAIT_REASON_ACTION_NOT_APPLICABLE,
    }
    assert set(WAIT_REASON_LABELS.keys()) >= expected
    for code in expected:
        label = WAIT_REASON_LABELS[code]
        assert isinstance(label, str) and label.strip()


# ─── Integration: _extract_decision_semantics populates codes ───────────────


def test_extract_decision_semantics_populates_wait_reason_codes_for_wait():
    text = (
        "结论：数据不足，无法确认方向。\n"
        "Strong Action Gate：未通过\n"
    )
    semantics = _extract_decision_semantics(text, has_position=False)
    assert isinstance(semantics, DecisionSemantics)
    assert semantics.execution_action == "WAIT"
    assert isinstance(semantics.wait_reason_codes, list)
    # Both DATA_MISSING (text indicator) and GATE_BLOCKED should fire.
    assert WAIT_REASON_DATA_MISSING in semantics.wait_reason_codes
    assert WAIT_REASON_GATE_BLOCKED in semantics.wait_reason_codes


def test_extract_decision_semantics_no_codes_for_enter():
    text = "<!-- VERDICT: {\"direction\": \"看多\", \"reason\": \"trend confirmed\"} -->\n建议买入。"
    semantics = _extract_decision_semantics(text, has_position=False, trigger_price=10.5)
    assert semantics.execution_action == "ENTER"
    assert semantics.wait_reason_codes == []


# ─── E2E: report_service.create_report stores codes into result_data ────────


# Scenario A: DATA_MISSING — critical field query failed, action is WAIT.
SCENARIO_DATA_MISSING = {
    "id": "data_missing",
    "symbol": "603629.SH",
    "description": "主力资金查询失败 → 数据不足观察",
    "decision": "HOLD",
    "action_label": "数据不足观察",
    "research_direction": "中性",
    "execution_action": "WAIT",
    "final_trade_decision": (
        "结论：主力资金证据缺失，叠加龙虎榜未上榜，本轮不给出强方向。\n"
        "## 交易建议\n- Buy Level: 1（证据不足，不入场）\n- Risk Level: 2（关注关键支撑 9.80）\n"
    ),
    "raw_evidence": {
        "stock_data": _OHLCV_HAS_DATA,
        "fund_flow_individual": "主力资金获取失败：AKShare timeout",
        "lhb": "龙虎榜：未上榜，非异动日无数据",
    },
    "expected_codes_must_include": [WAIT_REASON_DATA_MISSING],
    "expected_codes_must_exclude": [WAIT_REASON_RISK_FIRST, WAIT_REASON_NO_TRIGGER],
}

# Scenario B: NO_TRIGGER — 偏多 but no trigger price supplied; data is healthy.
SCENARIO_NO_TRIGGER = {
    "id": "no_trigger",
    "symbol": "300750.SZ",
    "description": "偏多·无触发价 → 等待触发（不是数据不足）",
    "decision": "HOLD",
    "action_label": "等待触发",
    "research_direction": "偏多",
    "execution_action": "WAIT",
    "final_trade_decision": (
        "结论：中线逻辑偏多，但缺少明确触发价，等待回调或突破确认后再入场。\n"
    ),
    "raw_evidence": {
        "stock_data": _OHLCV_HAS_DATA,
        "fund_flow_individual": "FUND_HAS_DATA: 主力净流入 1234万",
        "lhb": "LHB_NORMAL_NO_DATA: 未上榜",
    },
    "expected_codes_must_include": [WAIT_REASON_NO_TRIGGER],
    "expected_codes_must_exclude": [WAIT_REASON_DATA_MISSING, WAIT_REASON_RISK_FIRST],
}

# Scenario C: RISK_FIRST — 偏空 + 未持仓 → 回避.
SCENARIO_RISK_FIRST = {
    "id": "risk_first",
    "symbol": "600584.SH",
    "description": "偏空·未持仓 → 回避（风险优先）",
    "decision": "HOLD",
    "action_label": "回避",
    "research_direction": "偏空",
    "execution_action": "WAIT",
    "final_trade_decision": "结论：方向偏空，未持仓不建议入场，风险优先。\n",
    "raw_evidence": {
        "stock_data": _OHLCV_HAS_DATA,
        "fund_flow_individual": "FUND_HAS_DATA: 主力净流出 800万",
        "lhb": "LHB_NORMAL_NO_DATA: 未上榜",
    },
    "expected_codes_must_include": [WAIT_REASON_RISK_FIRST],
    "expected_codes_must_exclude": [WAIT_REASON_NO_TRIGGER],
}

# Scenario D: NORMAL_NO_DATA — neutral, no blockers, no trigger; the headline
# acceptance case that must NOT collapse into DATA_MISSING.
SCENARIO_NORMAL_NO_DATA = {
    "id": "normal_no_data",
    "symbol": "000001.SZ",
    "description": "中性·数据正常·暂无触发（不是数据不足）",
    "decision": "HOLD",
    "action_label": "观望",
    "research_direction": "中性",
    "execution_action": "WAIT",
    "final_trade_decision": (
        "结论：多空均衡，无明确方向，继续观察。\n"
    ),
    "raw_evidence": {
        "stock_data": _OHLCV_HAS_DATA,
        "fund_flow_individual": "FUND_HAS_DATA: 主力净流入 100万",
        "lhb": "LHB_NORMAL_NO_DATA: 未上榜",
        "news": "ANNOUNCEMENT_NORMAL_NO_DATA: 无重大公告",
    },
    "expected_codes_must_include": [WAIT_REASON_NORMAL_NO_DATA],
    "expected_codes_must_exclude": [WAIT_REASON_DATA_MISSING],
}

# Scenario E: CONFLICT — analyst / horizon conflict in the text.
SCENARIO_CONFLICT = {
    "id": "conflict",
    "symbol": "002594.SZ",
    "description": "短中线冲突 → 暂不入场",
    "decision": "HOLD",
    "action_label": "观望",
    "research_direction": "中性",
    "execution_action": "WAIT",
    "final_trade_decision": (
        "结论：中线偏多但短线偏弱，短中线冲突，暂不入场，等待短线修复。\n"
    ),
    "raw_evidence": {
        "stock_data": _OHLCV_HAS_DATA,
        "fund_flow_individual": "FUND_HAS_DATA: 主力净流入 200万",
        "lhb": "LHB_NORMAL_NO_DATA: 未上榜",
    },
    "expected_codes_must_include": [WAIT_REASON_CONFLICT],
    "expected_codes_must_exclude": [WAIT_REASON_DATA_MISSING, WAIT_REASON_RISK_FIRST],
}

# Scenario F: GATE_BLOCKED — explicit Strong Action Gate未通过.
SCENARIO_GATE_BLOCKED = {
    "id": "gate_blocked",
    "symbol": "601689.SH",
    "description": "门禁未通过 → 强制 WAIT",
    "decision": "HOLD",
    "action_label": "数据不足观察",
    "research_direction": "偏多",
    "execution_action": "WAIT",
    "final_trade_decision": (
        "结论：方向偏多，但 Strong Action Gate：未通过，等待人工复核。\n"
    ),
    "raw_evidence": {
        "stock_data": _OHLCV_HAS_DATA,
        "fund_flow_individual": "FUND_HAS_DATA: 主力净流入 500万",
        "lhb": "LHB_NORMAL_NO_DATA: 未上榜",
    },
    "expected_codes_must_include": [WAIT_REASON_GATE_BLOCKED],
    "expected_codes_must_exclude": [WAIT_REASON_RISK_FIRST],
}

WAIT_SCENARIOS = [
    SCENARIO_DATA_MISSING,
    SCENARIO_NO_TRIGGER,
    SCENARIO_RISK_FIRST,
    SCENARIO_NORMAL_NO_DATA,
    SCENARIO_CONFLICT,
    SCENARIO_GATE_BLOCKED,
]


@pytest.mark.parametrize("scenario", WAIT_SCENARIOS, ids=[s["id"] for s in WAIT_SCENARIOS])
def test_create_report_stores_expected_wait_reason_codes(scenario):
    """[REPORT-UX-003] wait_reason_codes — each fixture produces the right
    reason codes inside result_data after going through create_report."""
    db, engine = _make_db()
    try:
        report = report_service.create_report(
            db=db,
            symbol=scenario["symbol"],
            trade_date="2026-06-24",
            decision=scenario["decision"],
            result_data=_build_result_data(scenario),
        )

        result_data = report.result_data
        assert isinstance(result_data.get("wait_reason_codes"), list)
        assert isinstance(result_data.get("wait_reason_labels"), dict)
        codes = result_data["wait_reason_codes"]
        # Non-empty — every WAIT report must explain itself.
        assert codes, f"scenario {scenario['id']} produced empty wait_reason_codes"

        for must_include in scenario["expected_codes_must_include"]:
            assert must_include in codes, (
                f"scenario {scenario['id']}: expected code {must_include} in {codes}"
            )
        for must_exclude in scenario["expected_codes_must_exclude"]:
            assert must_exclude not in codes, (
                f"scenario {scenario['id']}: code {must_exclude} should NOT be in {codes}"
            )

        # Every code carries a human-readable label the frontend can render.
        for code in codes:
            assert code in result_data["wait_reason_labels"]
            assert result_data["wait_reason_labels"][code]
    finally:
        db.close()
        engine.dispose()


def test_normal_no_data_fixture_does_not_emit_data_missing():
    """[REPORT-UX-003] acceptance — the headline user complaint was that
    "三篇报告最终都变成数据不足观察". The NORMAL_NO_DATA fixture (data sources
    healthy, just no actionable signal) must NOT carry DATA_MISSING."""
    db, engine = _make_db()
    try:
        report = report_service.create_report(
            db=db,
            symbol=SCENARIO_NORMAL_NO_DATA["symbol"],
            trade_date="2026-06-24",
            decision=SCENARIO_NORMAL_NO_DATA["decision"],
            result_data=_build_result_data(SCENARIO_NORMAL_NO_DATA),
        )
        codes = report.result_data["wait_reason_codes"]
        assert WAIT_REASON_DATA_MISSING not in codes
        assert codes == [WAIT_REASON_NORMAL_NO_DATA]
    finally:
        db.close()
        engine.dispose()


def test_non_wait_report_has_empty_wait_reason_codes():
    """A non-WAIT report (ENTER) must carry an empty code list so the frontend
    hides the reason chips."""
    enter_scenario = {
        "symbol": "300750.SZ",
        "decision": "BUY",
        "action_label": "条件入场",
        "research_direction": "看多",
        "execution_action": "ENTER",
        "final_trade_decision": (
            "<!-- VERDICT: {\"direction\": \"看多\", \"reason\": \"trend confirmed\"} -->\n"
            "建议在 10.5 元附近条件入场。\n"
        ),
        "raw_evidence": {
            "stock_data": _OHLCV_HAS_DATA,
            "fund_flow_individual": "FUND_HAS_DATA: 主力净流入 1500万",
            "lhb": "LHB_HAS_DATA: 上榜",
        },
    }
    db, engine = _make_db()
    try:
        report = report_service.create_report(
            db=db,
            symbol=enter_scenario["symbol"],
            trade_date="2026-06-24",
            decision=enter_scenario["decision"],
            result_data=_build_result_data(enter_scenario),
        )
        assert report.execution_action == "ENTER"
        assert report.result_data["wait_reason_codes"] == []
        assert report.result_data["wait_reason_labels"] == {}
    finally:
        db.close()
        engine.dispose()


# ─── Acceptance: top-level response fields mirror result_data ───────────────


def test_response_top_level_wait_reason_codes_match_result_data():
    """[REPORT-UX-003] wait_reason_codes — the API response attachment helper
    copies the codes / labels out of result_data so the top-level response
    fields are identical to the stored JSON (mirrors DATA-021's pattern)."""
    from api.main import ReportResponse, _attach_report_data_blockers_for_response

    db, engine = _make_db()
    try:
        report = report_service.create_report(
            db=db,
            symbol=SCENARIO_DATA_MISSING["symbol"],
            trade_date="2026-06-24",
            decision=SCENARIO_DATA_MISSING["decision"],
            result_data=_build_result_data(SCENARIO_DATA_MISSING),
        )

        stored_codes = report.result_data["wait_reason_codes"]
        stored_labels = report.result_data["wait_reason_labels"]
        assert stored_codes

        # Before attachment the ORM object has no top-level wait_reason_codes.
        assert getattr(report, "wait_reason_codes", None) is None or \
            getattr(report, "wait_reason_codes", None) != stored_codes

        _attach_report_data_blockers_for_response(report)

        assert report.wait_reason_codes == stored_codes
        assert report.wait_reason_labels == stored_labels

        response = ReportResponse.model_validate(report)
        assert response.wait_reason_codes == stored_codes
        assert response.wait_reason_labels == stored_labels
    finally:
        db.close()
        engine.dispose()


def test_attach_wait_reason_codes_is_additive_only():
    """[REPORT-UX-003] wait_reason_codes — attachment must be purely additive:
    every pre-existing result_data key is preserved, and only the two new keys
    are inserted / overwritten."""
    original = _build_result_data(SCENARIO_DATA_MISSING)
    original_keys = set(original.keys())

    enriched = report_service.attach_report_wait_reason_codes(
        copy.deepcopy(original), wait_reason_codes=[WAIT_REASON_DATA_MISSING]
    )

    # Original payload not mutated.
    assert set(original.keys()) == original_keys
    assert set(enriched.keys()) == original_keys | {"wait_reason_codes", "wait_reason_labels"}
    assert enriched["wait_reason_codes"] == [WAIT_REASON_DATA_MISSING]
    assert enriched["wait_reason_labels"][WAIT_REASON_DATA_MISSING]
    for key in original_keys:
        assert enriched[key] == original[key]


# ─── Acceptance: legacy row without wait_reason_codes gets recomputed ───────


def test_legacy_result_data_without_codes_gets_recomputed_on_read():
    """Older rows predate REPORT-UX-003 and have no wait_reason_codes in
    result_data. The response attach helper must recompute on read so the UI
    still shows an explanation instead of a flat label."""
    from api.main import _attach_report_data_blockers_for_response

    db, engine = _make_db()
    try:
        # First write a row the modern way (with codes).
        report = report_service.create_report(
            db=db,
            symbol=SCENARIO_NO_TRIGGER["symbol"],
            trade_date="2026-06-24",
            decision=SCENARIO_NO_TRIGGER["decision"],
            result_data=_build_result_data(SCENARIO_NO_TRIGGER),
        )
        # Simulate a legacy row by stripping the new keys from result_data.
        legacy_rd = {k: v for k, v in report.result_data.items()
                     if k not in ("wait_reason_codes", "wait_reason_labels")}
        report.result_data = legacy_rd
        # Top-level attrs cleared (simulating a fresh ORM read).
        for attr in ("wait_reason_codes", "wait_reason_labels"):
            if hasattr(report, attr):
                try:
                    setattr(report, attr, None)
                except Exception:
                    pass

        _attach_report_data_blockers_for_response(report)

        # Codes were recomputed and surfaced at top level.
        assert isinstance(report.wait_reason_codes, list)
        assert report.wait_reason_codes  # non-empty
        assert WAIT_REASON_NO_TRIGGER in report.wait_reason_codes
        assert isinstance(report.wait_reason_labels, dict)
    finally:
        db.close()
        engine.dispose()
