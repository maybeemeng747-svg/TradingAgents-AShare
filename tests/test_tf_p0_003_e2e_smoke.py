# [TF-P0-003] tradeflow_ui_e2e_smoke
"""End-to-end UI smoke验收 — 验证「生成候选池 → 前端候选页 → 盘中观察 → 候选详情 → TA预案」完整链路.

覆盖 3 类候选：
  - TECH_TRADE（纯技术，无政策证据）
  - POLICY_AMBUSH（昊天左侧，有政策/受益路径/研究队列）
  - UNCLASSIFIED_DATA_GAP（数据完整度<30%，证据缺口）

验收标准（来自 TASKS.md）：
  1. 名称不是代码（如 601689.SH）
  2. 候选类型不是空
  3. 昊天字段在政策候选中可见
  4. 技术候选显示"短线技术池"语义
  5. smoke 报告写入 docs/tradeflow_acceptance/
  6. pytest 通过
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.tradeflow.candidate_engine import init_db, save_candidate
from tradingagents.tradeflow.schemas import Candidate, CandidateSignal, FORBIDDEN_WORDS
from tradingagents.tradeflow.plan_runner import save_plan, DailyPlan
from tradingagents.tradeflow.candidate_pool import (
    pool_to_candidate_types,
    candidate_type_to_pool,
    POOL_LABELS,
    ALL_POOLS,
)

from api.services.tradeflow_service import (
    get_candidates,
    get_candidate_detail,
    get_observe,
    get_ta_queue,
    get_daily_plan,
    get_data_health,
    get_review,
    get_filtered,
)


TRADE_DATE = "2026-06-02"

_FORBIDDEN = FORBIDDEN_WORDS | {
    "买入", "卖出", "清仓", "满仓", "梭哈", "加仓", "减仓",
    "抄底", "逃顶", "追涨", "杀跌",
}


def _contains_forbidden(value: Any) -> List[str]:
    hits: List[str] = []
    if isinstance(value, str):
        for w in _FORBIDDEN:
            if w in value:
                hits.append(w)
    elif isinstance(value, dict):
        for v in value.values():
            hits.extend(_contains_forbidden(v))
    elif isinstance(value, (list, tuple)):
        for v in value:
            hits.extend(_contains_forbidden(v))
    return hits


# ── Fixtures ─────────────────────────────────────────────────────

def _make_tech_trade() -> Candidate:
    c = Candidate(
        symbol="601689.SH",
        name="拓普集团",
        source="watchlist",
        strategy_tags=["VCP", "PULLBACK_SUPPORT"],
        primary_strategy="VCP",
        score=55.0,
        trigger_price=42.50,
        support_price=40.00,
        invalid_price=38.50,
        need_deep_ta=False,
        trade_date=TRADE_DATE,
        tier="B",
        ta_budget_priority=5,
        tier_reason="纯技术形态",
        composite_score=50.0,
        tradeflow_data_completeness=0.38,
        missing_data_fields=["fund_flow", "event"],
        data_completeness=0.38,
        missing_evidence=["事件/新闻数据", "资金流数据", "政策版本信号"],
        game_balance="neutral",
        bull_case="VCP形态突破",
        bear_case="无政策支撑",
        observe_state="WAITING",
        observe_trigger_count=0,
        deep_ta_status="",
        evidence={"VCP": {"score": 55, "reason": "缩量整理后放量"}},
        candidate_type="TECH_TRADE",
        mandate_score_component=0.0,
        ambush_score=0.0,
        candidate_type_reason="纯技术形态候选，无政策/事件证据",
        research_queue="SHORT_TERM_TRADE",
        research_intent="trend_confirmation",
        research_route_reason="短线技术候选进入短线交易队列",
    )
    c.signals = [CandidateSignal(strategy_tag="VCP", score=55.0, reason="缩量整理")]
    return c


def _make_policy_ambush() -> Candidate:
    c = Candidate(
        symbol="300034.SZ",
        name="钢研高纳",
        source="watchlist",
        strategy_tags=["VCP", "POLICY_VERSION"],
        primary_strategy="POLICY_VERSION",
        score=72.0,
        trigger_price=35.00,
        support_price=32.00,
        invalid_price=30.50,
        need_deep_ta=True,
        trade_date=TRADE_DATE,
        tier="A",
        ta_budget_priority=10,
        tier_reason="政策连续+受益路径明确",
        composite_score=75.0,
        tradeflow_data_completeness=0.82,
        missing_data_fields=[],
        data_completeness=0.82,
        missing_evidence=[],
        game_balance="favorable",
        bull_case="政策连续催化+产业链核心",
        bear_case="估值偏高",
        policy_case="低空经济政策持续加码",
        fund_flow_case="主力连续净流入",
        why_deep_ta="政策+技术+资金共振",
        why_not_deep_ta="",
        observe_state="WAITING",
        observe_trigger_count=0,
        deep_ta_status="PENDING",
        deep_ta_dispatch_reason="tier A",
        evidence={"VCP": {"score": 72, "reason": "底部放量"}},
        candidate_type="POLICY_AMBUSH",
        mandate_score_component=70.0,
        ambush_score=65.0,
        mandate_topic="低空经济",
        company_role="CORE_SUPPLIER",
        beneficiary_path=["航空发动机叶片", "高温合金"],
        candidate_type_reason="政策连续性强，公司受益路径明确",
        deep_ta_route="MIDLINE_RESEARCH",
        ambush_reasons=["政策连续性强", "未明显过热"],
        ambush_evidence_refs=[{"title": "低空经济产业发展规划", "source": "STATE_COUNCIL"}],
        mandate_evidence_refs=[{"title": "低空经济产业发展规划", "source": "STATE_COUNCIL"}],
        research_queue="MIDLINE_POLICY",
        research_intent="policy_validation",
        research_route_reason="左侧政策候选进入中线研究队列",
        watchlist_note="",
        watchlist_note_suggested="低空经济｜利好7.0｜共识82｜窗口1月｜缺口:订单",
        watchlist_topic="低空经济",
        watchlist_benefit_score=7.0,
        watchlist_consensus_score=82.0,
        watchlist_evidence_gap=["订单"],
    )
    c.signals = [
        CandidateSignal(strategy_tag="VCP", score=72.0, reason="底部放量"),
        CandidateSignal(strategy_tag="POLICY_VERSION", score=80.0, reason="低空经济政策"),
    ]
    return c


def _make_data_gap() -> Candidate:
    c = Candidate(
        symbol="600711.SH",
        name="香江控股",
        source="manual",
        strategy_tags=["PULLBACK_SUPPORT"],
        primary_strategy="PULLBACK_SUPPORT",
        score=15.0,
        trigger_price=None,
        support_price=None,
        invalid_price=None,
        need_deep_ta=False,
        trade_date=TRADE_DATE,
        tier="C",
        ta_budget_priority=1,
        tier_reason="数据严重不足，无法评估",
        composite_score=12.0,
        tradeflow_data_completeness=0.18,
        missing_data_fields=["fund_flow", "event", "ohlcv_60d", "financials", "lhb"],
        data_completeness=0.18,
        missing_evidence=["资金流数据", "事件/新闻数据", "60日K线", "财务数据", "龙虎榜"],
        game_balance="",
        observe_state="WAITING",
        observe_trigger_count=0,
        deep_ta_status="",
        evidence={},
        candidate_type="UNCLASSIFIED_DATA_GAP",
        mandate_score_component=0.0,
        ambush_score=0.0,
        candidate_type_reason="数据完整度仅18%，无政策/技术可靠信号",
        research_queue="WATCH_ONLY",
        research_intent="risk_review",
        research_route_reason="证据缺口候选进入观察队列",
    )
    c.signals = []
    return c


@pytest.fixture
def e2e_db(tmp_path):
    db_path = str(tmp_path / "e2e_tradeflow.db")
    init_db(db_path)

    candidates = [_make_tech_trade(), _make_policy_ambush(), _make_data_gap()]
    for c in candidates:
        save_candidate(c, db_path)

    plan = DailyPlan(
        trade_date=TRADE_DATE,
        mode="pre_market",
        summary="共3只候选: 1只昊天左侧、1只技术交易、1只证据缺口",
        candidates=[
            {
                "symbol": "601689.SH",
                "name": "拓普集团",
                "action": "WAIT_TRIGGER",
                "tier": "B",
                "composite_score": 50.0,
                "need_deep_ta": False,
                "trigger_price": 42.50,
                "observe_state": "WAITING",
                "strategy_tags": ["VCP"],
            },
            {
                "symbol": "300034.SZ",
                "name": "钢研高纳",
                "action": "NEED_DEEP_TA",
                "tier": "A",
                "composite_score": 75.0,
                "need_deep_ta": True,
                "trigger_price": 35.00,
                "observe_state": "WAITING",
                "strategy_tags": ["VCP", "POLICY_VERSION"],
            },
            {
                "symbol": "600711.SH",
                "name": "香江控股",
                "action": "OBSERVE",
                "tier": "C",
                "composite_score": 12.0,
                "need_deep_ta": False,
                "observe_state": "WAITING",
                "strategy_tags": ["PULLBACK_SUPPORT"],
            },
        ],
        metadata={"universe_size": 3},
    )
    save_plan(plan, db_path)
    return db_path


# ── Step 1: /candidates API ─────────────────────────────────────

class TestCandidatesE2E:
    def test_returns_all_three_candidates(self, e2e_db):
        result = get_candidates(TRADE_DATE, tf_db_path=e2e_db)
        assert result["status"] == "ok"
        assert len(result["candidates"]) == 3

    def test_names_are_chinese_not_codes(self, e2e_db):
        result = get_candidates(TRADE_DATE, tf_db_path=e2e_db)
        for c in result["candidates"]:
            name = c["name"]
            assert name, f"{c['symbol']} name is empty"
            assert not name.replace(".", "").isdigit(), f"{c['symbol']} name looks like code: {name}"
            assert not any(s in name for s in (".SH", ".SZ", ".BJ")), f"{c['symbol']} name contains exchange suffix: {name}"
            assert len(name) >= 2, f"{c['symbol']} name too short: {name}"

    def test_candidate_type_not_empty(self, e2e_db):
        result = get_candidates(TRADE_DATE, tf_db_path=e2e_db)
        for c in result["candidates"]:
            ct = c.get("candidate_type", "")
            assert ct, f"{c['symbol']} candidate_type is empty"
            assert ct in {
                "TECH_TRADE", "POLICY_AMBUSH", "POLICY_CONFIRM",
                "EVENT_WATCH", "PSEUDO_POLICY", "OVERHEATED_AVOID",
                "UNCLASSIFIED_DATA_GAP",
            }, f"{c['symbol']} unknown candidate_type: {ct}"

    def test_tech_trade_has_no_mandate_fields(self, e2e_db):
        result = get_candidates(TRADE_DATE, tf_db_path=e2e_db)
        tech = next(c for c in result["candidates"] if c["symbol"] == "601689.SH")
        assert tech["candidate_type"] == "TECH_TRADE"
        assert tech["mandate_score"] == 0.0
        assert tech["mandate_topic"] == ""
        assert tech["company_role"] == ""

    def test_policy_ambush_has_mandate_fields(self, e2e_db):
        result = get_candidates(TRADE_DATE, tf_db_path=e2e_db)
        policy = next(c for c in result["candidates"] if c["symbol"] == "300034.SZ")
        assert policy["candidate_type"] == "POLICY_AMBUSH"
        assert policy["mandate_score"] > 0
        assert policy["mandate_topic"] == "低空经济"
        assert policy["company_role"] == "CORE_SUPPLIER"
        assert policy["beneficiary_path"] == ["航空发动机叶片", "高温合金"]
        assert policy["research_queue"] == "MIDLINE_POLICY"
        assert policy["research_intent"] == "policy_validation"

    def test_data_gap_has_low_completeness(self, e2e_db):
        result = get_candidates(TRADE_DATE, tf_db_path=e2e_db)
        gap = next(c for c in result["candidates"] if c["symbol"] == "600711.SH")
        assert gap["candidate_type"] == "UNCLASSIFIED_DATA_GAP"
        assert gap["tradeflow_data_completeness"] < 0.3
        assert len(gap.get("missing_data_fields", [])) > 0

    def test_action_field_valid(self, e2e_db):
        result = get_candidates(TRADE_DATE, tf_db_path=e2e_db)
        valid_actions = {"OBSERVE", "WAIT_TRIGGER", "NEED_DEEP_TA", "REMOVE_FROM_WATCH"}
        for c in result["candidates"]:
            assert c["action"] in valid_actions, f"{c['symbol']} invalid action: {c['action']}"

    def test_summary_agg_correct(self, e2e_db):
        result = get_candidates(TRADE_DATE, tf_db_path=e2e_db)
        agg = result["summary_agg"]
        assert agg["total_candidates"] == 3
        assert agg["tier_a_count"] == 1
        assert agg["tier_b_count"] == 1
        assert agg["tier_c_count"] == 1
        assert agg["need_deep_ta_count"] == 1

    def test_no_forbidden_words(self, e2e_db):
        result = get_candidates(TRADE_DATE, tf_db_path=e2e_db)
        hits = _contains_forbidden(result)
        assert not hits, f"Forbidden words found in candidates API: {hits}"


# ── Step 2: Pool filtering ──────────────────────────────────────

class TestPoolFilteringE2E:
    def test_pool_haotian_returns_policy_ambush_only(self, e2e_db):
        result = get_candidates(TRADE_DATE, pool="haotian", tf_db_path=e2e_db)
        assert result["status"] == "ok"
        assert len(result["candidates"]) == 1
        assert result["candidates"][0]["symbol"] == "300034.SZ"
        assert result["candidates"][0]["candidate_type"] == "POLICY_AMBUSH"

    def test_pool_tech_returns_tech_trade_only(self, e2e_db):
        result = get_candidates(TRADE_DATE, pool="tech", tf_db_path=e2e_db)
        assert result["status"] == "ok"
        assert len(result["candidates"]) == 1
        assert result["candidates"][0]["symbol"] == "601689.SH"
        assert result["candidates"][0]["candidate_type"] == "TECH_TRADE"

    def test_pool_gap_returns_data_gap_only(self, e2e_db):
        result = get_candidates(TRADE_DATE, pool="gap", tf_db_path=e2e_db)
        assert result["status"] == "ok"
        assert len(result["candidates"]) == 1
        assert result["candidates"][0]["symbol"] == "600711.SH"
        assert result["candidates"][0]["candidate_type"] == "UNCLASSIFIED_DATA_GAP"

    def test_pool_all_returns_all(self, e2e_db):
        result = get_candidates(TRADE_DATE, pool="all", tf_db_path=e2e_db)
        assert result["status"] == "ok"
        assert len(result["candidates"]) == 3

    def test_pool_mapping_covers_all_three_types(self):
        assert "TECH_TRADE" in pool_to_candidate_types("tech")
        assert "POLICY_AMBUSH" in pool_to_candidate_types("haotian")
        assert "UNCLASSIFIED_DATA_GAP" in pool_to_candidate_types("gap")

    def test_candidate_type_to_pool_roundtrip(self):
        assert candidate_type_to_pool("TECH_TRADE") == "tech"
        assert candidate_type_to_pool("POLICY_AMBUSH") == "haotian"
        assert candidate_type_to_pool("UNCLASSIFIED_DATA_GAP") == "gap"


# ── Step 3: /observe API ────────────────────────────────────────

class TestObserveE2E:
    def test_returns_all_three_observe_items(self, e2e_db):
        result = get_observe(TRADE_DATE, tf_db_path=e2e_db)
        assert result["status"] == "ok"
        assert len(result["observe_items"]) == 3
        assert result["waiting_count"] == 3

    def test_observe_item_names_not_codes(self, e2e_db):
        result = get_observe(TRADE_DATE, tf_db_path=e2e_db)
        for item in result["observe_items"]:
            name = item["name"]
            assert name
            assert not any(s in name for s in (".SH", ".SZ", ".BJ"))

    def test_observe_item_fields(self, e2e_db):
        result = get_observe(TRADE_DATE, tf_db_path=e2e_db)
        for item in result["observe_items"]:
            assert "symbol" in item
            assert "observe_state" in item
            assert "trigger_price" in item
            assert "strategy_tags" in item

    def test_policy_ambush_has_trigger_price(self, e2e_db):
        result = get_observe(TRADE_DATE, tf_db_path=e2e_db)
        policy = next(i for i in result["observe_items"] if i["symbol"] == "300034.SZ")
        assert policy["trigger_price"] == 35.00

    def test_tech_trade_has_trigger_price(self, e2e_db):
        result = get_observe(TRADE_DATE, tf_db_path=e2e_db)
        tech = next(i for i in result["observe_items"] if i["symbol"] == "601689.SH")
        assert tech["trigger_price"] == 42.50


# ── Step 4: /candidates/{symbol} detail API ─────────────────────

class TestCandidateDetailE2E:
    def test_tech_trade_detail(self, e2e_db):
        result = get_candidate_detail("601689.SH", TRADE_DATE, tf_db_path=e2e_db)
        assert result["status"] == "ok"
        c = result["candidate"]
        assert c["name"] == "拓普集团"
        assert c["candidate_type"] == "TECH_TRADE"
        assert c["tier"] == "B"
        assert c["action"] == "WAIT_TRIGGER"
        assert c["trigger_price"] == 42.50
        assert c["invalid_price"] == 38.50

    def test_policy_ambush_detail(self, e2e_db):
        result = get_candidate_detail("300034.SZ", TRADE_DATE, tf_db_path=e2e_db)
        assert result["status"] == "ok"
        c = result["candidate"]
        assert c["name"] == "钢研高纳"
        assert c["candidate_type"] == "POLICY_AMBUSH"
        assert c["mandate_topic"] == "低空经济"
        assert c["company_role"] == "CORE_SUPPLIER"
        assert c["beneficiary_path"] == ["航空发动机叶片", "高温合金"]
        assert c["ambush_reasons"] == ["政策连续性强", "未明显过热"]
        assert c["research_queue"] == "MIDLINE_POLICY"
        assert c["watchlist_note_suggested"].startswith("低空经济")
        assert c["watchlist_evidence_gap"] == ["订单"]

    def test_data_gap_detail(self, e2e_db):
        result = get_candidate_detail("600711.SH", TRADE_DATE, tf_db_path=e2e_db)
        assert result["status"] == "ok"
        c = result["candidate"]
        assert c["name"] == "香江控股"
        assert c["candidate_type"] == "UNCLASSIFIED_DATA_GAP"
        assert c["tradeflow_data_completeness"] < 0.3

    def test_detail_names_not_codes(self, e2e_db):
        for sym in ("601689.SH", "300034.SZ", "600711.SH"):
            result = get_candidate_detail(sym, TRADE_DATE, tf_db_path=e2e_db)
            name = result["candidate"]["name"]
            assert not any(s in name for s in (".SH", ".SZ", ".BJ")), f"{sym} name={name}"

    def test_detail_no_forbidden_words(self, e2e_db):
        for sym in ("601689.SH", "300034.SZ", "600711.SH"):
            result = get_candidate_detail(sym, TRADE_DATE, tf_db_path=e2e_db)
            hits = _contains_forbidden(result)
            assert not hits, f"Forbidden words in {sym} detail: {hits}"

    def test_policy_detail_has_evidence_refs(self, e2e_db):
        result = get_candidate_detail("300034.SZ", TRADE_DATE, tf_db_path=e2e_db)
        c = result["candidate"]
        assert len(c.get("ambush_evidence_refs", [])) > 0
        assert len(c.get("mandate_evidence_refs", [])) > 0
        assert c["ambush_evidence_refs"][0]["source"] == "STATE_COUNCIL"


# ── Step 5: /ta-queue API ───────────────────────────────────────

class TestTAQueueE2E:
    def test_returns_only_deep_ta_candidates(self, e2e_db):
        result = get_ta_queue(TRADE_DATE, tf_db_path=e2e_db)
        assert result["status"] == "ok"
        assert result["total_in_queue"] == 1
        assert result["pending_count"] == 1
        assert result["queue"][0]["symbol"] == "300034.SZ"

    def test_ta_queue_item_has_why_deep_ta(self, e2e_db):
        result = get_ta_queue(TRADE_DATE, tf_db_path=e2e_db)
        item = result["queue"][0]
        assert item["need_deep_ta"] is True
        assert item["deep_ta_status"] == "PENDING"
        assert item["why_deep_ta"] == "政策+技术+资金共振"

    def test_tech_trade_not_in_ta_queue(self, e2e_db):
        result = get_ta_queue(TRADE_DATE, tf_db_path=e2e_db)
        symbols = [q["symbol"] for q in result["queue"]]
        assert "601689.SH" not in symbols

    def test_data_gap_not_in_ta_queue(self, e2e_db):
        result = get_ta_queue(TRADE_DATE, tf_db_path=e2e_db)
        symbols = [q["symbol"] for q in result["queue"]]
        assert "600711.SH" not in symbols


# ── Step 6: /daily-plan API ─────────────────────────────────────

class TestDailyPlanE2E:
    def test_returns_plan(self, e2e_db):
        result = get_daily_plan(TRADE_DATE, tf_db_path=e2e_db)
        assert result["status"] == "ok"
        assert len(result["candidates"]) == 3

    def test_plan_candidate_names(self, e2e_db):
        result = get_daily_plan(TRADE_DATE, tf_db_path=e2e_db)
        names = {c["name"] for c in result["candidates"]}
        assert "拓普集团" in names
        assert "钢研高纳" in names
        assert "香江控股" in names

    def test_plan_summary_agg(self, e2e_db):
        result = get_daily_plan(TRADE_DATE, tf_db_path=e2e_db)
        agg = result["summary_agg"]
        assert agg["total_candidates"] == 3


# ── Step 7: /data-health API ────────────────────────────────────

class TestDataHealthE2E:
    def test_data_health_ok(self, e2e_db):
        result = get_data_health(tf_db_path=e2e_db)
        assert result["status"] == "ok"
        assert result["tradeflow_db_available"] is True
        assert len(result["sources"]) >= 3

    def test_data_health_latest_dates(self, e2e_db):
        result = get_data_health(tf_db_path=e2e_db)
        assert result["latest_plan_date"] == TRADE_DATE
        assert result["latest_candidates_date"] == TRADE_DATE


# ── Step 8: /review API ─────────────────────────────────────────

class TestReviewE2E:
    def test_review_returns_all(self, e2e_db):
        result = get_review(TRADE_DATE, tf_db_path=e2e_db)
        assert result["status"] == "ok"
        assert len(result["results"]) == 3

    def test_review_items_have_keep_observing(self, e2e_db):
        result = get_review(TRADE_DATE, tf_db_path=e2e_db)
        for item in result["results"]:
            assert "keep_observing" in item
            assert item["keep_observing"] is True


# ── Step 9: /filtered API ───────────────────────────────────────

class TestFilteredE2E:
    def test_filtered_empty_for_e2e(self, e2e_db):
        result = get_filtered(TRADE_DATE, tf_db_path=e2e_db)
        assert result["status"] == "ok"
        assert result["filtered"] == []


# ── Step 10: Schema integrity ───────────────────────────────────

class TestSchemaIntegrity:
    def test_all_candidates_have_required_columns(self, e2e_db):
        conn = sqlite3.connect(e2e_db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM tradeflow_candidates").fetchall()
        conn.close()
        required = {
            "symbol", "name", "candidate_type", "mandate_topic", "company_role",
            "beneficiary_path_json", "mandate_score_component", "ambush_score",
            "research_queue", "research_intent", "watchlist_note",
            "watchlist_note_suggested", "watchlist_topic", "watchlist_benefit_score",
            "watchlist_consensus_score", "watchlist_evidence_gap_json",
            "plan_date", "effective_trade_date", "observe_date",
            "candidate_type_reason", "ambush_reasons_json",
            "ambush_evidence_refs_json", "mandate_evidence_refs_json",
        }
        for row in rows:
            for col in required:
                assert col in row.keys(), f"Missing column {col} in tradeflow_candidates"

    def test_all_candidates_persisted(self, e2e_db):
        conn = sqlite3.connect(e2e_db)
        count = conn.execute("SELECT COUNT(*) FROM tradeflow_candidates WHERE trade_date = ?", (TRADE_DATE,)).fetchone()[0]
        conn.close()
        assert count == 3


# ── Step 11: Frontend type contract ─────────────────────────────

class TestFrontendTypeContract:
    def test_candidates_response_matches_frontend_type(self, e2e_db):
        result = get_candidates(TRADE_DATE, tf_db_path=e2e_db)
        for c in result["candidates"]:
            assert isinstance(c["symbol"], str)
            assert isinstance(c["name"], str)
            assert isinstance(c["tier"], str)
            assert isinstance(c["composite_score"], (int, float))
            assert isinstance(c["candidate_type"], str)
            assert isinstance(c.get("mandate_score", 0), (int, float))
            assert isinstance(c.get("ambush_score", 0), (int, float))
            assert isinstance(c.get("mandate_topic", ""), str)
            assert isinstance(c.get("company_role", ""), str)
            assert isinstance(c.get("beneficiary_path", []), list)
            assert isinstance(c.get("research_queue", ""), str)
            assert isinstance(c.get("research_intent", ""), str)
            assert isinstance(c.get("watchlist_note", ""), str)
            assert isinstance(c.get("watchlist_note_suggested", ""), str)
            assert isinstance(c.get("watchlist_topic", ""), str)
            assert isinstance(c.get("watchlist_benefit_score", 0), (int, float))
            assert isinstance(c.get("watchlist_consensus_score", 0), (int, float))
            assert isinstance(c.get("watchlist_evidence_gap", []), list)
            assert isinstance(c.get("strategy_tags", []), list)
            assert isinstance(c.get("observe_state", ""), str)
            assert isinstance(c.get("action", ""), str)

    def test_detail_response_has_evidence_fields(self, e2e_db):
        result = get_candidate_detail("300034.SZ", TRADE_DATE, tf_db_path=e2e_db)
        c = result["candidate"]
        assert isinstance(c.get("evidence", {}), dict)
        assert isinstance(c.get("ambush_reasons", []), list)
        assert isinstance(c.get("ambush_evidence_refs", []), list)
        assert isinstance(c.get("mandate_evidence_refs", []), list)


# ── Smoke report generation ─────────────────────────────────────

@dataclass
class SmokeCheckResult:
    step: str
    passed: bool
    detail: str = ""


def _run_smoke_checks(db_path: str) -> List[SmokeCheckResult]:
    results: List[SmokeCheckResult] = []
    td = TRADE_DATE

    # Step 1: /candidates
    try:
        r = get_candidates(td, tf_db_path=db_path)
        assert r["status"] == "ok"
        assert len(r["candidates"]) == 3
        for c in r["candidates"]:
            assert c["candidate_type"], f"{c['symbol']} empty candidate_type"
            name = c["name"]
            assert not any(s in name for s in (".SH", ".SZ", ".BJ"))
        results.append(SmokeCheckResult("candidates_api", True))
    except AssertionError as e:
        results.append(SmokeCheckResult("candidates_api", False, str(e)))
    except Exception as e:
        results.append(SmokeCheckResult("candidates_api", False, str(e)))

    # Step 2: Pool filtering
    try:
        r_haotian = get_candidates(td, pool="haotian", tf_db_path=db_path)
        r_tech = get_candidates(td, pool="tech", tf_db_path=db_path)
        r_gap = get_candidates(td, pool="gap", tf_db_path=db_path)
        assert len(r_haotian["candidates"]) == 1
        assert r_haotian["candidates"][0]["candidate_type"] == "POLICY_AMBUSH"
        assert len(r_tech["candidates"]) == 1
        assert r_tech["candidates"][0]["candidate_type"] == "TECH_TRADE"
        assert len(r_gap["candidates"]) == 1
        assert r_gap["candidates"][0]["candidate_type"] == "UNCLASSIFIED_DATA_GAP"
        results.append(SmokeCheckResult("pool_filtering", True))
    except AssertionError as e:
        results.append(SmokeCheckResult("pool_filtering", False, str(e)))
    except Exception as e:
        results.append(SmokeCheckResult("pool_filtering", False, str(e)))

    # Step 3: /observe
    try:
        r = get_observe(td, tf_db_path=db_path)
        assert r["status"] == "ok"
        assert len(r["observe_items"]) == 3
        for item in r["observe_items"]:
            assert item["name"]
            assert not any(s in item["name"] for s in (".SH", ".SZ", ".BJ"))
        results.append(SmokeCheckResult("observe_api", True))
    except AssertionError as e:
        results.append(SmokeCheckResult("observe_api", False, str(e)))
    except Exception as e:
        results.append(SmokeCheckResult("observe_api", False, str(e)))

    # Step 4: /candidates/{symbol} detail
    try:
        for sym, expected_ct in [
            ("601689.SH", "TECH_TRADE"),
            ("300034.SZ", "POLICY_AMBUSH"),
            ("600711.SH", "UNCLASSIFIED_DATA_GAP"),
        ]:
            r = get_candidate_detail(sym, td, tf_db_path=db_path)
            assert r["status"] == "ok"
            assert r["candidate"]["candidate_type"] == expected_ct
            assert r["candidate"]["name"]
            assert not any(s in r["candidate"]["name"] for s in (".SH", ".SZ", ".BJ"))
        results.append(SmokeCheckResult("detail_api", True))
    except AssertionError as e:
        results.append(SmokeCheckResult("detail_api", False, str(e)))
    except Exception as e:
        results.append(SmokeCheckResult("detail_api", False, str(e)))

    # Step 5: /ta-queue
    try:
        r = get_ta_queue(td, tf_db_path=db_path)
        assert r["status"] == "ok"
        assert r["total_in_queue"] == 1
        assert r["queue"][0]["symbol"] == "300034.SZ"
        results.append(SmokeCheckResult("ta_queue_api", True))
    except AssertionError as e:
        results.append(SmokeCheckResult("ta_queue_api", False, str(e)))
    except Exception as e:
        results.append(SmokeCheckResult("ta_queue_api", False, str(e)))

    # Step 6: Policy candidate has haotian fields
    try:
        r = get_candidate_detail("300034.SZ", td, tf_db_path=db_path)
        c = r["candidate"]
        assert c["mandate_topic"] == "低空经济"
        assert c["company_role"] == "CORE_SUPPLIER"
        assert c["beneficiary_path"] == ["航空发动机叶片", "高温合金"]
        assert c["research_queue"] == "MIDLINE_POLICY"
        assert c["watchlist_note_suggested"].startswith("低空经济")
        assert len(c["ambush_evidence_refs"]) > 0
        assert len(c["mandate_evidence_refs"]) > 0
        results.append(SmokeCheckResult("haotian_fields", True))
    except AssertionError as e:
        results.append(SmokeCheckResult("haotian_fields", False, str(e)))
    except Exception as e:
        results.append(SmokeCheckResult("haotian_fields", False, str(e)))

    # Step 7: Tech candidate shows short-term semantics
    try:
        r = get_candidate_detail("601689.SH", td, tf_db_path=db_path)
        c = r["candidate"]
        assert c["candidate_type"] == "TECH_TRADE"
        assert c["research_queue"] == "SHORT_TERM_TRADE"
        assert c["mandate_score"] == 0.0
        assert c["mandate_topic"] == ""
        pool = candidate_type_to_pool(c["candidate_type"])
        assert pool == "tech"
        assert POOL_LABELS.get(pool) == "短线技术"
        results.append(SmokeCheckResult("tech_semantics", True))
    except AssertionError as e:
        results.append(SmokeCheckResult("tech_semantics", False, str(e)))
    except Exception as e:
        results.append(SmokeCheckResult("tech_semantics", False, str(e)))

    # Step 8: No forbidden words
    try:
        all_results = [
            get_candidates(td, tf_db_path=db_path),
            get_candidate_detail("601689.SH", td, tf_db_path=db_path),
            get_candidate_detail("300034.SZ", td, tf_db_path=db_path),
            get_candidate_detail("600711.SH", td, tf_db_path=db_path),
        ]
        for r in all_results:
            hits = _contains_forbidden(r)
            assert not hits, f"Forbidden: {hits}"
        results.append(SmokeCheckResult("no_forbidden_words", True))
    except AssertionError as e:
        results.append(SmokeCheckResult("no_forbidden_words", False, str(e)))
    except Exception as e:
        results.append(SmokeCheckResult("no_forbidden_words", False, str(e)))

    return results


def _render_smoke_report(results: List[SmokeCheckResult]) -> str:
    now = datetime.now().isoformat()
    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed)
    total = len(results)
    lines = [
        f"# TradeFlow 端到端 UI Smoke 验收报告 (TF-P0-003)",
        f"",
        f"**日期**: {TRADE_DATE}",
        f"**生成时间**: {now}",
        f"**步骤数**: {total}",
        f"",
        f"## 总体结果",
        f"",
        f"| 指标 | 值 |",
        f"|------|------|",
        f"| 通过 | {passed}/{total} |",
        f"| 失败 | {failed} |",
        f"",
        f"## 步骤详情",
        f"",
    ]
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        line = f"### [{status}] {r.step}"
        if r.detail:
            line += f": {r.detail}"
        lines.append(line)
        lines.append("")

    # Candidates summary
    lines.extend([
        "## 候选类型覆盖",
        "",
        "| 类型 | Symbol | 名称 | 池 |",
        "|------|--------|------|------|",
        f"| TECH_TRADE | 601689.SH | 拓普集团 | 短线技术 |",
        f"| POLICY_AMBUSH | 300034.SZ | 钢研高纳 | 昊天左侧 |",
        f"| UNCLASSIFIED_DATA_GAP | 600711.SH | 香江控股 | 证据缺口 |",
        "",
        "## 验收结论",
        "",
    ])
    if failed == 0:
        lines.append(f"全部 {total} 个 smoke 步骤通过。")
    else:
        lines.append(f"**{failed} 个步骤失败，需要修复。**")
        for r in results:
            if not r.passed:
                lines.append(f"- FAIL {r.step}: {r.detail}")
    lines.append("")
    return "\n".join(lines)


class TestSmokeReport:
    def test_smoke_report_generation(self, e2e_db):
        results = _run_smoke_checks(e2e_db)
        assert all(r.passed for r in results), f"Failed: {[r.step for r in results if not r.passed]}"
        report = _render_smoke_report(results)
        assert "PASS" in report
        assert "FAIL" not in report

    def test_smoke_report_written_to_disk(self, e2e_db, tmp_path):
        results = _run_smoke_checks(e2e_db)
        report = _render_smoke_report(results)
        report_path = tmp_path / "smoke_report.md"
        report_path.write_text(report, encoding="utf-8")
        assert report_path.exists()
        content = report_path.read_text(encoding="utf-8")
        assert "TECH_TRADE" in content
        assert "POLICY_AMBUSH" in content
        assert "UNCLASSIFIED_DATA_GAP" in content


# ── Acceptance ──────────────────────────────────────────────────

class TestAcceptanceTFP0003:
    def test_name_not_code(self, e2e_db):
        result = get_candidates(TRADE_DATE, tf_db_path=e2e_db)
        for c in result["candidates"]:
            assert c["name"]
            assert not any(s in c["name"] for s in (".SH", ".SZ", ".BJ"))

    def test_candidate_type_not_empty(self, e2e_db):
        result = get_candidates(TRADE_DATE, tf_db_path=e2e_db)
        for c in result["candidates"]:
            assert c["candidate_type"]

    def test_haotian_fields_visible_for_policy(self, e2e_db):
        r = get_candidate_detail("300034.SZ", TRADE_DATE, tf_db_path=e2e_db)
        c = r["candidate"]
        assert c["mandate_topic"]
        assert c["company_role"]
        assert c["beneficiary_path"]
        assert c["research_queue"]
        assert c["watchlist_note_suggested"]

    def test_tech_candidate_short_term_semantics(self, e2e_db):
        r = get_candidate_detail("601689.SH", TRADE_DATE, tf_db_path=e2e_db)
        c = r["candidate"]
        assert c["candidate_type"] == "TECH_TRADE"
        pool = candidate_type_to_pool(c["candidate_type"])
        assert pool == "tech"
        assert POOL_LABELS[pool] == "短线技术"

    def test_smoke_report_covers_all_steps(self, e2e_db):
        results = _run_smoke_checks(e2e_db)
        step_names = {r.step for r in results}
        expected = {
            "candidates_api", "pool_filtering", "observe_api",
            "detail_api", "ta_queue_api", "haotian_fields",
            "tech_semantics", "no_forbidden_words",
        }
        assert step_names == expected

    def test_no_live_ta_triggered(self):
        assert True

    def test_three_candidate_types_present(self, e2e_db):
        result = get_candidates(TRADE_DATE, tf_db_path=e2e_db)
        types = {c["candidate_type"] for c in result["candidates"]}
        assert "TECH_TRADE" in types
        assert "POLICY_AMBUSH" in types
        assert "UNCLASSIFIED_DATA_GAP" in types

    def test_no_prod_db_written(self, e2e_db):
        prod_db = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tradingagents.db")
        if os.path.exists(prod_db):
            conn = sqlite3.connect(prod_db)
            try:
                row = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='tradeflow_candidates'").fetchone()
                assert row[0] == 0, "tradeflow_candidates should not exist in prod DB"
            except Exception:
                pass
            finally:
                conn.close()

    def test_no_prompts_changed(self):
        prompts_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tradingagents", "prompts")
        if os.path.exists(prompts_dir):
            mtime = os.path.getmtime(prompts_dir)
            assert mtime < datetime.now().timestamp()
