"""REPORT-UX-001 — TA 报告"数据不足观察"端到端回放验收.

Replays three field-level data-blocker fixtures through
``report_service.create_report()`` and the API response attachment helper to
verify that DATA-021 made "数据不足" auditable without weakening the strong
action gate:

1. ``result_data.data_blockers`` carries the right field-level statuses, keeping
   ``query_failed`` / ``normal_no_data`` / ``skipped`` apart instead of a flat
   black box.
2. The top-level response fields (``data_blockers`` / ``data_blocker_summary``)
   mirror what is stored inside ``result_data``.
3. DATA-021 metadata never rewrites ``decision`` / ``action_label`` /
   ``research_direction`` / ``execution_action`` or the Buy Level / Risk Level
   text embedded in ``final_trade_decision``.

No live API, no LLM, no prompts, no prod DB — all fixtures are inline and the
DB is an in-memory SQLite instance.

# [REPORT-UX-001] data_blocker_replay
"""

import copy

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.database import Base
from api.services import report_service
from tradingagents.agents.utils.readiness_score import build_data_blockers

# A realistic OHLCV CSV (>50 chars) so the ``ohlcv_5d`` evidence is treated as
# HAS_DATA and excluded from the blocker list in the "data available" scenarios.
_OHLCV_HAS_DATA = (
    "date,open,high,low,close,volume,amount\n"
    "2026-06-18,10.00,10.42,9.91,10.28,128000,1315840\n"
    "2026-06-19,10.28,10.35,10.02,10.11,153000,1546830\n"
    "2026-06-20,10.11,10.50,10.05,10.44,201000,2102240\n"
    "2026-06-21,10.44,10.60,10.30,10.52,176000,1847520\n"
    "2026-06-24,10.52,10.71,10.45,10.66,142000,1513720\n"
)

# Markdown block embedded in final_trade_decision to prove Buy Level / Risk
# Level text survives the data_blocker attachment untouched.
_BUY_RISK_LEVEL_BLOCK = (
    "## 交易建议\n"
    "- Buy Level: 1（证据不足，不入场）\n"
    "- Risk Level: 2（关注关键支撑 9.80）\n"
)


def _blocker_by_key(blockers):
    return {item["key"]: item for item in blockers}


def _make_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    return db, engine


# ─── Fixture scenarios (≥3 as required by REPORT-UX-001) ─────────────────────

# Scenario 1: 主力资金失败 + 龙虎榜正常无数据 — the canonical distinction case.
SCENARIO_FUND_FLOW_FAILED_LHB_NORMAL = {
    "id": "fund_flow_failed__lhb_normal",
    "symbol": "603629.SH",
    "description": "主力资金查询失败 vs 龙虎榜正常无数据",
    "decision": "HOLD",
    "action_label": "数据不足观察",
    "research_direction": "中性",
    "execution_action": "WAIT",
    "final_trade_decision": (
        "结论：主力资金证据缺失，叠加龙虎榜未上榜，本轮不给出强方向。\n"
        + _BUY_RISK_LEVEL_BLOCK
    ),
    "raw_evidence": {
        "stock_data": _OHLCV_HAS_DATA,
        "fund_flow_individual": "主力资金获取失败：AKShare timeout，Eastmoney push2 返回 502",
        "lhb": "龙虎榜：未上榜，非异动日无数据",
    },
    "expected": {
        "individual_fund_flow": "query_failed",
        "lhb_status": "normal_no_data",
    },
    "excluded": ["ohlcv_5d"],
}

# Scenario 2: 公告失败 + 评级正常无数据 — structured G-006 contract path.
SCENARIO_NEWS_FAILED_RATINGS_NORMAL = {
    "id": "news_failed__ratings_normal",
    "symbol": "300750.SZ",
    "description": "公告查询失败 vs 分析师评级正常无数据",
    "decision": "HOLD",
    "action_label": "数据不足观察",
    "research_direction": "偏多",
    "execution_action": "WAIT",
    "final_trade_decision": (
        "结论：公告/事件证据查询失败，评级正常无数据，等待事件补证。\n"
        + _BUY_RISK_LEVEL_BLOCK
    ),
    "raw_evidence": {
        "stock_data": _OHLCV_HAS_DATA,
        "news": {"status": "FAILED", "raw": "ANNOUNCEMENT_FAILED: cninfo 接口不可用"},
        "ratings": {"status": "NORMAL_NO_DATA", "raw": "RATINGS_NORMAL_NO_DATA: 无分析师评级"},
    },
    "expected": {
        "announcements": "query_failed",
        "ratings": "normal_no_data",
    },
    "excluded": ["ohlcv_5d"],
}

# Scenario 3: 行情缺失 + skipped 辅助源 — market data failed, aux sources skipped.
SCENARIO_MARKET_MISSING_SKIPPED_AUX = {
    "id": "market_missing__skipped_aux",
    "symbol": "600584.SH",
    "description": "行情/K线缺失 + 辅助源 skipped",
    "decision": "HOLD",
    "action_label": "数据不足观察",
    "research_direction": "中性",
    "execution_action": "WAIT",
    "final_trade_decision": (
        "结论：基础行情证据缺失，辅助数据源本轮已跳过，无法给出强结论。\n"
        + _BUY_RISK_LEVEL_BLOCK
    ),
    "raw_evidence": {
        "stock_data": {"status": "FAILED", "raw": "STOCK_DATA_FAILED: 行情接口超时"},
        "fund_flow_individual": {"status": "SKIPPED", "raw": ""},
        "lhb": {"status": "SKIPPED", "raw": ""},
        "ratings": {"status": "SKIPPED", "raw": ""},
        "news": {"status": "SKIPPED", "raw": ""},
    },
    "expected": {
        "ohlcv_5d": "query_failed",
        "individual_fund_flow": "skipped",
        "lhb_status": "skipped",
        "ratings": "skipped",
        "announcements": "skipped",
    },
    "excluded": [],
}

SCENARIOS = [
    SCENARIO_FUND_FLOW_FAILED_LHB_NORMAL,
    SCENARIO_NEWS_FAILED_RATINGS_NORMAL,
    SCENARIO_MARKET_MISSING_SKIPPED_AUX,
]


def _build_result_data(scenario):
    """Build a result_data payload for a scenario.

    report sections are neutral placeholders; the field-level statuses are
    driven entirely by ``metadata.raw_evidence`` (which is what the production
    pipeline relies on once DATA-004 raw_evidence is populated).
    """
    return {
        "market_report": "行情报告占位",
        "smart_money_report": "资金面报告占位",
        "news_report": "新闻/公告报告占位",
        "final_trade_decision": scenario["final_trade_decision"],
        "research_direction": scenario["research_direction"],
        "execution_action": scenario["execution_action"],
        "action_label": scenario["action_label"],
        "metadata": {"raw_evidence": copy.deepcopy(scenario["raw_evidence"])},
    }


# ─── Core replay: every scenario through create_report() ─────────────────────


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
def test_replay_scenario_attaches_expected_data_blockers(scenario):
    """[REPORT-UX-001] data_blocker_replay — each fixture produces the right
    field-level statuses inside result_data after going through create_report."""
    db, engine = _make_db()
    try:
        original_final = scenario["final_trade_decision"]
        report = report_service.create_report(
            db=db,
            symbol=scenario["symbol"],
            trade_date="2026-06-24",
            decision=scenario["decision"],
            result_data=_build_result_data(scenario),
        )

        result_data = report.result_data
        assert isinstance(result_data.get("data_blockers"), list)
        assert isinstance(result_data.get("data_blocker_summary"), dict)
        by_key = _blocker_by_key(result_data["data_blockers"])

        # Expected field-level statuses for this scenario.
        for field_key, expected_status in scenario["expected"].items():
            assert field_key in by_key, f"{field_key} missing from blockers"
            assert by_key[field_key]["status"] == expected_status, (
                f"{field_key}: expected {expected_status}, "
                f"got {by_key[field_key]['status']}"
            )

        # Fields with data must not appear as blockers.
        for excluded_key in scenario["excluded"]:
            assert excluded_key not in by_key, (
                f"{excluded_key} should be HAS_DATA and excluded from blockers"
            )

        # data_blocker_summary counts must be consistent with the blocker list.
        counts = result_data["data_blocker_summary"]["counts"]
        for status_wire in ("query_failed", "normal_no_data", "skipped"):
            listed = sum(1 for b in result_data["data_blockers"] if b["status"] == status_wire)
            assert counts.get(status_wire, 0) == listed

        # final_trade_decision (incl. Buy Level / Risk Level) preserved verbatim.
        assert report.final_trade_decision == original_final
        assert result_data["final_trade_decision"] == original_final
        assert "Buy Level: 1" in report.final_trade_decision
        assert "Risk Level: 2" in report.final_trade_decision
    finally:
        db.close()
        engine.dispose()


# ─── Acceptance: 主力资金失败 vs 龙虎榜正常无数据 explicit distinction ─────────


def test_fund_flow_failed_vs_lhb_normal_no_data_distinction():
    """[REPORT-UX-001] data_blocker_replay — the headline acceptance: a query
    failure and a normal no-data result for the same raw_evidence pool must not
    collapse into the same status."""
    blockers = build_data_blockers(
        reports={},
        raw_evidence=copy.deepcopy(SCENARIO_FUND_FLOW_FAILED_LHB_NORMAL["raw_evidence"]),
    )
    by_key = _blocker_by_key(blockers)

    assert by_key["individual_fund_flow"]["status"] == "query_failed"
    assert by_key["individual_fund_flow"]["severity"] == "high"
    assert "查询失败" in by_key["individual_fund_flow"]["status_label"]
    assert by_key["lhb_status"]["status"] == "normal_no_data"
    assert by_key["lhb_status"]["severity"] == "info"
    assert "正常无数据" in by_key["lhb_status"]["reason"]
    # The two statuses are genuinely different wire values.
    assert by_key["individual_fund_flow"]["status"] != by_key["lhb_status"]["status"]


# ─── Acceptance: result_data.data_blockers == response top-level fields ───────


def test_response_top_level_data_blockers_match_result_data():
    """[REPORT-UX-001] data_blocker_replay — the API response attachment helper
    copies data_blockers / data_blocker_summary out of result_data so the
    top-level response fields are identical to the stored JSON."""
    from api.main import ReportResponse, _attach_report_data_blockers_for_response

    db, engine = _make_db()
    try:
        report = report_service.create_report(
            db=db,
            symbol=SCENARIO_NEWS_FAILED_RATINGS_NORMAL["symbol"],
            trade_date="2026-06-24",
            decision=SCENARIO_NEWS_FAILED_RATINGS_NORMAL["decision"],
            result_data=_build_result_data(SCENARIO_NEWS_FAILED_RATINGS_NORMAL),
        )

        stored_blockers = report.result_data["data_blockers"]
        stored_summary = report.result_data["data_blocker_summary"]

        # Before attachment the ORM object has no top-level data_blockers attr.
        assert getattr(report, "data_blockers", None) is None

        _attach_report_data_blockers_for_response(report)

        # Top-level attrs now mirror result_data exactly.
        assert report.data_blockers == stored_blockers
        assert report.data_blocker_summary == stored_summary

        # And the pydantic response model serializes them consistently.
        response = ReportResponse.model_validate(report)
        assert response.data_blockers == stored_blockers
        assert response.data_blocker_summary == stored_summary
        # Each blocker carries the wire statuses the frontend knows how to render.
        for blocker in response.data_blockers:
            assert blocker["status"] in {
                "query_failed",
                "normal_no_data",
                "not_queried",
                "field_missing",
                "skipped",
            }
            for required in ("key", "label", "status_label", "severity", "reason", "impact"):
                assert required in blocker
    finally:
        db.close()
        engine.dispose()


# ─── Acceptance: DATA-021 never rewrites the strong action gate ──────────────


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
def test_data_blockers_do_not_rewrite_action_gate(scenario):
    """[REPORT-UX-001] data_blocker_replay — decision / action_label /
    research_direction / execution_action are owned by the decision pipeline,
    not by DATA-021. The blocker attachment must leave them untouched."""
    db, engine = _make_db()
    try:
        report = report_service.create_report(
            db=db,
            symbol=scenario["symbol"],
            trade_date="2026-06-24",
            decision=scenario["decision"],
            result_data=_build_result_data(scenario),
        )

        assert report.decision == scenario["decision"]
        # Semantics were supplied explicitly in result_data, so they must survive.
        assert report.action_label == scenario["action_label"]
        assert report.research_direction == scenario["research_direction"]
        assert report.execution_action == scenario["execution_action"]
        # result_data copies of the semantics are also unchanged.
        assert report.result_data["action_label"] == scenario["action_label"]
        assert report.result_data["research_direction"] == scenario["research_direction"]

        # Sanity: data_blockers really were attached (so the no-rewrite claim is
        # meaningful and not vacuous).
        assert isinstance(report.result_data.get("data_blockers"), list)
        assert report.result_data["data_blockers"]
    finally:
        db.close()
        engine.dispose()


def test_attach_report_data_blockers_is_additive_only():
    """[REPORT-UX-001] data_blocker_replay — attach_report_data_blockers must be
    purely additive: every pre-existing result_data key is preserved, and only
    data_blockers / data_blocker_summary are inserted."""
    original = _build_result_data(SCENARIO_FUND_FLOW_FAILED_LHB_NORMAL)
    original_keys = set(original.keys())

    enriched = report_service.attach_report_data_blockers(copy.deepcopy(original))

    # Original payload is not mutated in place.
    assert set(original.keys()) == original_keys
    # Enriched payload adds exactly the two DATA-021 keys.
    assert "data_blockers" in enriched
    assert "data_blocker_summary" in enriched
    assert set(enriched.keys()) == original_keys | {"data_blockers", "data_blocker_summary"}
    # Every original value is byte-for-byte identical after enrichment.
    for key in original_keys:
        assert enriched[key] == original[key]
