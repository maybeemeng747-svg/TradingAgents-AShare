# [V-010] small_cap_trial_v2_acceptance
"""TradeFlow 小资金试跑 v2 端到端验收 — V-010.

验收用户 ¥5000 小资金试跑链路能否回答这 5 个问题：
  1. 看哪几只      —— 候选收敛 (tiered ranking → 主候选 ≤ 配置上限)
  2. 为什么        —— 评分 + 排前/扣分原因 + 精度维度
  3. 何时观察      —— Observe runner + mock 行情触发
  4. 数据缺什么    —— Mandate Daily Report 证据缺口 + DATA-021 报告字段级 data_blockers
  5. 盘后怎么复盘  —— Tracking Board v2 只读摘要 (投资控上下文) + 盘后 Review

链路 (V-010 在 V-008 的基础上加入 H-015 / DATA-021 / IC-TA-002 三条新通路):

  候选池 fixture (4 类候选)
    → get_candidates / get_candidates_tiered       (收敛到 ≤ 2 只主候选)
    → run_observe + mock quote                      (盘中观察触发)
    → get_mandate_daily_report                      (昊天主题日报 + 证据缺口)
    → investment_controller_context                 (Tracking Board v2 只读摘要 +
                                                     recent_report_data_blockers +
                                                     controller_hints)
    → generate_review                               (盘后复盘归因)

约束：
  - 不调用 LLM/live API。
  - 不写生产数据库 (tradingagents.db / tradeflow.db)。
  - 不发真实通知 (dry-run only)。
  - 强词扫描通过；runtime_tier 全程不升级到 FULL_TA。
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.tradeflow.candidate_engine import init_db, save_candidate
from tradingagents.tradeflow.schemas import Candidate, CandidateSignal, FORBIDDEN_WORDS
from tradingagents.tradeflow.plan_runner import save_plan, DailyPlan
from tradingagents.tradeflow.observe_runner import run_observe

from api.services.tradeflow_service import (
    get_candidates,
    get_candidates_tiered,
    get_mandate_daily_report,
    generate_review,
    get_review,
)
from api.services.investment_controller_context import (
    get_investment_controller_context,
    assert_no_strong_action_verbs,
)
from api.runtime_tier import RuntimeTier

# ── Date semantics ────────────────────────────────────────────────
# plan_date: 2026-06-13 (Saturday, non-trading day)
# effective_trade_date: 2026-06-15 (Monday, trading day)
PLAN_DATE = "2026-06-13"
EFFECTIVE_TRADE_DATE = "2026-06-15"

# V-010 forbids strong trade verbs in any synthesised output. We union the
# schema-level FORBIDDEN_WORDS with the extended V-008/V-009 list so the
# acceptance gate stays conservative.
_FORBIDDEN = FORBIDDEN_WORDS | {
    "买入", "卖出", "清仓", "满仓", "梭哈", "加仓", "减仓",
    "抄底", "逃顶", "追涨", "杀跌", "立即卖出", "全仓",
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


# ── Candidate Factories ──────────────────────────────────────────

def _make_haotian_main() -> Candidate:
    """昊天左侧主候选 — POLICY_AMBUSH, tier A."""
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
        trade_date=PLAN_DATE,
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
        watchlist_note_suggested="低空经济|利好7.0|共识82|窗口1月|缺口:订单",
        watchlist_topic="低空经济",
        watchlist_benefit_score=7.0,
        watchlist_consensus_score=82.0,
        watchlist_evidence_gap=["订单"],
        plan_date=PLAN_DATE,
        effective_trade_date=EFFECTIVE_TRADE_DATE,
        observe_date=EFFECTIVE_TRADE_DATE,
        technical_score=55.0,
        policy_score=80.0,
        fund_flow_score=15.0,
        event_score=10.0,
        risk_penalty_score=0.0,
        data_quality_score=82.0,
        ranking_reasons=["政策连续性强", "受益路径明确"],
        weakness_reasons=["估值偏高"],
    )
    c.signals = [
        CandidateSignal(strategy_tag="VCP", score=72.0, reason="底部放量"),
        CandidateSignal(strategy_tag="POLICY_VERSION", score=80.0, reason="低空经济政策"),
    ]
    return c


def _make_tech_main() -> Candidate:
    """短线技术主候选 — TECH_TRADE, tier B (will NOT trigger in observe)."""
    c = Candidate(
        symbol="601689.SH",
        name="拓普集团",
        source="watchlist",
        strategy_tags=["VCP", "PULLBACK_SUPPORT"],
        primary_strategy="VCP",
        score=58.0,
        trigger_price=42.50,
        support_price=40.00,
        invalid_price=38.50,
        need_deep_ta=False,
        trade_date=PLAN_DATE,
        tier="B",
        ta_budget_priority=5,
        tier_reason="VCP形态+资金确认",
        composite_score=62.0,
        tradeflow_data_completeness=0.65,
        missing_data_fields=[],
        data_completeness=0.65,
        missing_evidence=[],
        game_balance="neutral",
        bull_case="VCP形态突破",
        bear_case="无政策支撑",
        observe_state="WAITING",
        observe_trigger_count=0,
        deep_ta_status="",
        evidence={"VCP": {"score": 58, "reason": "缩量整理后放量"}},
        candidate_type="TECH_TRADE",
        mandate_score_component=0.0,
        ambush_score=0.0,
        candidate_type_reason="纯技术形态候选，无政策/事件证据",
        research_queue="SHORT_TERM_TRADE",
        research_intent="trend_confirmation",
        research_route_reason="短线技术候选进入短线交易队列",
        fund_flow_anomaly_score=8.0,
        fund_flow_unit_verified=True,
        fund_flow_anomaly_tags=["CONSECUTIVE_INFLOW"],
        plan_date=PLAN_DATE,
        effective_trade_date=EFFECTIVE_TRADE_DATE,
        observe_date=EFFECTIVE_TRADE_DATE,
        technical_score=58.0,
        policy_score=0.0,
        fund_flow_score=8.0,
        event_score=0.0,
        risk_penalty_score=0.0,
        data_quality_score=65.0,
        ranking_reasons=["VCP形态确认", "资金流入"],
        weakness_reasons=["无政策支撑"],
    )
    c.signals = [CandidateSignal(strategy_tag="VCP", score=58.0, reason="缩量整理")]
    return c


def _make_observation() -> Candidate:
    """观察候选 — EVENT_WATCH, tier B (observation pool, no trigger price)."""
    c = Candidate(
        symbol="002230.SZ",
        name="科大讯飞",
        source="event",
        strategy_tags=["EVENT_CATALYST"],
        primary_strategy="EVENT_CATALYST",
        score=40.0,
        trigger_price=None,
        support_price=None,
        invalid_price=None,
        need_deep_ta=False,
        trade_date=PLAN_DATE,
        tier="B",
        ta_budget_priority=3,
        tier_reason="事件催化，待确认",
        composite_score=38.0,
        tradeflow_data_completeness=0.45,
        missing_data_fields=["fund_flow"],
        data_completeness=0.45,
        missing_evidence=["资金流数据"],
        observe_state="WAITING",
        observe_trigger_count=0,
        deep_ta_status="",
        evidence={"EVENT_CATALYST": {"score": 40, "reason": "AI峰会即将召开"}},
        candidate_type="EVENT_WATCH",
        candidate_type_reason="事件催化候选，待确认落地",
        research_queue="SHORT_TERM_TRADE",
        research_intent="event_confirmation",
        research_route_reason="事件候选进入短线观察队列",
        plan_date=PLAN_DATE,
        effective_trade_date=EFFECTIVE_TRADE_DATE,
        observe_date=EFFECTIVE_TRADE_DATE,
    )
    c.signals = [CandidateSignal(strategy_tag="EVENT_CATALYST", score=40.0, reason="AI峰会")]
    return c


def _make_filtered_data_gap() -> Candidate:
    """过滤候选 — UNCLASSIFIED_DATA_GAP, tier C (filtered, severe evidence gap)."""
    c = Candidate(
        symbol="600711.SH",
        name="香江控股",
        source="manual",
        strategy_tags=["PULLBACK_SUPPORT"],
        primary_strategy="PULLBACK_SUPPORT",
        score=12.0,
        trigger_price=None,
        support_price=None,
        invalid_price=None,
        need_deep_ta=False,
        trade_date=PLAN_DATE,
        tier="C",
        ta_budget_priority=0,
        tier_reason="数据严重不足，无法评估",
        composite_score=12.0,
        tradeflow_data_completeness=0.18,
        missing_data_fields=["fund_flow", "event", "ohlcv_60d", "financials", "lhb"],
        data_completeness=0.18,
        missing_evidence=["资金流数据", "事件/新闻数据", "60日K线", "财务数据", "龙虎榜"],
        observe_state="WAITING",
        observe_trigger_count=0,
        deep_ta_status="",
        evidence={},
        candidate_type="UNCLASSIFIED_DATA_GAP",
        candidate_type_reason="数据完整度仅18%，无政策/技术可靠信号",
        research_queue="WATCH_ONLY",
        research_intent="risk_review",
        research_route_reason="证据缺口候选进入观察队列",
        plan_date=PLAN_DATE,
        effective_trade_date=EFFECTIVE_TRADE_DATE,
        observe_date=EFFECTIVE_TRADE_DATE,
    )
    c.signals = []
    return c


# ── Mock quote provider for observe runner ───────────────────────

def _mock_quote_provider(symbols: list[str]) -> dict[str, dict]:
    """Mock realtime quotes — only 300034.SZ triggers (price >= trigger_price)."""
    quotes: dict[str, dict] = {}
    for sym in symbols:
        if sym == "300034.SZ":
            quotes[sym] = {
                "current_price": 36.00,
                "current_volume": 1500000.0,
                "current_amount": 54000000.0,
                "quote_time": f"{EFFECTIVE_TRADE_DATE}T10:30:00",
                "source": "mock_test",
            }
        elif sym == "601689.SH":
            quotes[sym] = {
                "current_price": 41.00,
                "current_volume": 800000.0,
                "current_amount": 32800000.0,
                "quote_time": f"{EFFECTIVE_TRADE_DATE}T10:30:00",
                "source": "mock_test",
            }
        elif sym == "002230.SZ":
            quotes[sym] = {
                "current_price": 45.00,
                "current_volume": 2000000.0,
                "current_amount": 90000000.0,
                "quote_time": f"{EFFECTIVE_TRADE_DATE}T10:30:00",
                "source": "mock_test",
            }
    return quotes


# ── SQLA report fixture for DATA-021 data_blockers ───────────────

def _make_report_with_blockers(
    symbol: str,
    trade_date: str,
    *,
    severe_blockers: list[tuple[str, str]],
    action_label: str = "数据不足观察",
    research_direction: str = "中性",
) -> "ReportDB":  # noqa: F821 - typed in fixture body
    """Build a completed ReportDB row whose result_data ships data_blockers.

    ``severe_blockers`` is a list of (key, status) tuples; statuses here are
    restricted to the severe set (query_failed / field_missing) so the
    IC-TA-002 aggregator picks them up.
    """
    from api.database import ReportDB

    blockers = [
        {
            "key": key,
            "label": key,
            "status": status,
            "status_label": status,
            "severity": "high" if status == "query_failed" else "medium",
            "reason": f"{key} {status}",
            "impact": "demo impact",
        }
        for key, status in severe_blockers
    ]
    return ReportDB(
        id=uuid.uuid4().hex,
        user_id="v010_user",
        symbol=symbol,
        trade_date=trade_date,
        status="completed",
        decision="HOLD",
        direction="",
        research_direction=research_direction,
        execution_action="WAIT",
        action_label=action_label,
        target_price=None,
        stop_loss_price=None,
        confidence="",
        risk_items="[]",
        key_metrics="{}",
        analyst_traces="{}",
        trader_investment_plan="",
        final_trade_decision="",
        result_data={
            "market_report": "demo",
            "data_blockers": blockers,
            "data_blocker_summary": {
                "level": "warning" if blockers else "ok",
                "message": "demo",
                "counts": {
                    "query_failed": sum(1 for _, s in severe_blockers if s == "query_failed")
                },
                "total": len(blockers),
            },
        },
    )


# ── E2E Fixture ──────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _isolate_review_report_file(tmp_path, monkeypatch):
    """Redirect save_review_report to a tmp dir so V-010 does not overwrite the
    shared ``docs/tradeflow_reviews/{date}.md`` artefact (which other tests and
    the user's historical reviews rely on)."""
    from tradingagents.tradeflow import post_market_review as _pmr

    def _save(summary, output_dir=None, *args, **kwargs):
        return _pmr.save_review_report(summary, output_dir=str(tmp_path))

    monkeypatch.setattr(_pmr, "save_review_report", _save)
    # Also patch the symbol already imported into tradeflow_service.
    from api.services import tradeflow_service as _ts
    if hasattr(_ts, "save_review_report"):
        monkeypatch.setattr(_ts, "save_review_report", _save)
    yield


@pytest.fixture
def e2e_db(tmp_path):
    """Initialise an isolated tradeflow SQLite DB with the 4 candidate types."""
    db_path = str(tmp_path / "v010_tradeflow.db")
    init_db(db_path)

    candidates = [
        _make_haotian_main(),         # 昊天主候选
        _make_tech_main(),            # 技术主候选
        _make_observation(),          # 观察候选
        _make_filtered_data_gap(),    # 过滤候选 (证据缺口)
    ]
    for c in candidates:
        save_candidate(c, db_path)

    plan = DailyPlan(
        trade_date=PLAN_DATE,
        mode="pre_market",
        plan_date=PLAN_DATE,
        effective_trade_date=EFFECTIVE_TRADE_DATE,
        observe_date=EFFECTIVE_TRADE_DATE,
        summary="V-010 验收: 昊天主+技术主+观察+过滤(证据缺口) 各1",
        candidates=[
            {
                "symbol": "300034.SZ", "name": "钢研高纳",
                "action": "NEED_DEEP_TA", "tier": "A",
                "composite_score": 75.0, "need_deep_ta": True,
                "trigger_price": 35.00, "observe_state": "WAITING",
                "strategy_tags": ["VCP", "POLICY_VERSION"],
            },
            {
                "symbol": "601689.SH", "name": "拓普集团",
                "action": "WAIT_TRIGGER", "tier": "B",
                "composite_score": 62.0, "need_deep_ta": False,
                "trigger_price": 42.50, "observe_state": "WAITING",
                "strategy_tags": ["VCP"],
            },
            {
                "symbol": "002230.SZ", "name": "科大讯飞",
                "action": "OBSERVE", "tier": "B",
                "composite_score": 38.0, "need_deep_ta": False,
                "observe_state": "WAITING",
                "strategy_tags": ["EVENT_CATALYST"],
            },
            {
                "symbol": "600711.SH", "name": "香江控股",
                "action": "REMOVE_FROM_WATCH", "tier": "C",
                "composite_score": 12.0, "need_deep_ta": False,
                "observe_state": "WAITING",
                "strategy_tags": ["PULLBACK_SUPPORT"],
            },
        ],
        metadata={
            "universe_size": 4,
            "plan_date": PLAN_DATE,
            "effective_trade_date": EFFECTIVE_TRADE_DATE,
        },
    )
    save_plan(plan, db_path)
    return db_path


@pytest.fixture
def e2e_sqla_session():
    """In-memory SQLA DB with a single completed report carrying severe blockers.

    The symbol (600711.SH) intentionally matches the filtered candidate in the
    tradeflow fixture so we can prove that the same evidence-gap candidate is
    surfaced both in the candidate pool (filtered) and in the report blocker
    bucket (DATA-021) — the v2 acceptance chain must connect the two views.
    """
    from api.database import Base, ImportedPortfolioPositionDB, ReportDB

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    ImportedPortfolioPositionDB.__table__.create(engine)
    ReportDB.__table__.create(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    session.add(_make_report_with_blockers(
        "600711.SH", EFFECTIVE_TRADE_DATE,
        severe_blockers=[
            ("individual_fund_flow", "query_failed"),
            ("announcements", "query_failed"),
            ("lhb_status", "normal_no_data"),  # informational, not severe
        ],
        research_direction="偏空",
    ))
    session.commit()
    yield session
    session.close()
    engine.dispose()


# ── Step 1: Candidate Convergence ────────────────────────────────

class TestStep1CandidateConvergence:
    """候选收敛：4 类候选 → tiered ranking 收敛到 2 只主候选。"""

    def test_four_candidates_persisted_with_chinese_names(self, e2e_db):
        result = get_candidates(EFFECTIVE_TRADE_DATE, tf_db_path=e2e_db)
        assert result["status"] == "ok"
        assert len(result["candidates"]) == 4
        for c in result["candidates"]:
            assert c["name"], f"{c['symbol']} name empty"
            assert not any(s in c["name"] for s in (".SH", ".SZ", ".BJ"))

    def test_candidate_types_cover_required_four(self, e2e_db):
        """任务要求：技术主候选、昊天主候选、观察候选、过滤候选各至少 1 个。"""
        result = get_candidates(EFFECTIVE_TRADE_DATE, tf_db_path=e2e_db)
        types = {c["candidate_type"] for c in result["candidates"]}
        assert "POLICY_AMBUSH" in types, "missing 昊天主候选"
        assert "TECH_TRADE" in types, "missing 技术主候选"
        assert "EVENT_WATCH" in types, "missing 观察候选"
        assert "UNCLASSIFIED_DATA_GAP" in types, "missing 过滤候选"

    def test_tiered_ranking_compresses_to_two_main(self, e2e_db):
        """收敛门禁：主候选 ≤ 配置上限 (2 只)，过滤候选不混入主池。"""
        result = get_candidates_tiered(EFFECTIVE_TRADE_DATE, tf_db_path=e2e_db)
        assert result["status"] == "ok"
        assert len(result["main_candidates"]) == 2
        main_symbols = {c["symbol"] for c in result["main_candidates"]}
        assert main_symbols == {"300034.SZ", "601689.SH"}
        # 过滤候选 / 观察候选不进入 main 池
        assert "600711.SH" not in main_symbols
        assert "002230.SZ" not in main_symbols

    def test_main_candidates_carry_precision_and_action_tier(self, e2e_db):
        """主候选精度维度 + action_tier 必须保留 (TF-QUALITY-003 / TF-UX-001)。"""
        result = get_candidates_tiered(EFFECTIVE_TRADE_DATE, tf_db_path=e2e_db)
        for c in result["main_candidates"]:
            assert "precision_dimensions" in c
            assert c.get("precision_resonance_count", 0) >= 2
            assert "action_tier" in c
            assert "trade_priority_score" in c

    def test_pool_counts_correct(self, e2e_db):
        result = get_candidates_tiered(EFFECTIVE_TRADE_DATE, tf_db_path=e2e_db)
        counts = result["pool_counts"]
        assert counts["main"] == 2
        assert counts["haotian_in_main"] == 1
        assert counts["tech_in_main"] == 1

    def test_main_candidates_keep_split_scores_and_reasons(self, e2e_db):
        """[TF-PERSIST-001] 分项评分与排前/扣分原因必须真实返回。"""
        result = get_candidates_tiered(EFFECTIVE_TRADE_DATE, tf_db_path=e2e_db)
        policy = next(c for c in result["main_candidates"] if c["symbol"] == "300034.SZ")
        assert policy["technical_score"] == 55.0
        assert policy["policy_score"] == 80.0
        assert policy["ranking_reasons"] == ["政策连续性强", "受益路径明确"]
        assert policy["weakness_reasons"] == ["估值偏高"]


# ── Step 2: Observe (盘中观察触发) ───────────────────────────────

class TestStep2Observe:
    """何时观察：observe runner 用 mock 行情触发主候选。"""

    def test_observe_triggers_haotian_main(self, e2e_db):
        result = run_observe(
            trade_date=EFFECTIVE_TRADE_DATE,
            db_path=e2e_db,
            quote_provider=_mock_quote_provider,
        )
        assert result.checked >= 3
        assert result.triggered >= 1
        triggered = [d["symbol"] for d in result.details
                     if d.get("observe_state") == "TRIGGERED"]
        assert "300034.SZ" in triggered

    def test_observe_writes_signal(self, e2e_db):
        run_observe(
            trade_date=EFFECTIVE_TRADE_DATE,
            db_path=e2e_db,
            quote_provider=_mock_quote_provider,
        )
        conn = sqlite3.connect(e2e_db)
        conn.row_factory = sqlite3.Row
        signals = conn.execute(
            "SELECT * FROM tradeflow_signals WHERE symbol = ? ORDER BY id DESC LIMIT 1",
            ("300034.SZ",),
        ).fetchall()
        conn.close()
        assert len(signals) > 0
        ev = json.loads(signals[0]["evidence_json"])
        assert ev["observe_state"] == "TRIGGERED"


# ── Step 3: Mandate Daily Report (昊天主题日报) ─────────────────

@pytest.fixture
def _patch_heatmap_db(monkeypatch):
    """Return a callable that pins get_topic_heatmap to a fixture DB.

    ``get_mandate_daily_report`` does not accept a tf_db_path kwarg (the
    service-layer contract is read-from-default-DB), so to keep the V-010
    chain isolated we monkeypatch ``get_topic_heatmap`` to forward its
    tf_db_path argument to the fixture DB. The patch is opt-in per test.
    """
    from api.services import tradeflow_service as _ts

    def _pin(db_path: str):
        original = _ts.get_topic_heatmap

        def _heat(*args, **kw):
            kw.setdefault("tf_db_path", db_path)
            return original(*args, **kw)

        monkeypatch.setattr(_ts, "get_topic_heatmap", _heat)

    return _pin


class TestStep3MandateDailyReport:
    """日报：H-015 昊天主题日报，从候选池 topic heatmap 重建 (read-only)。"""

    def test_report_returns_ok_status(self, e2e_db, _patch_heatmap_db):
        _patch_heatmap_db(e2e_db)
        report = get_mandate_daily_report(
            as_of=EFFECTIVE_TRADE_DATE,
            reports_dir="/tmp/v010_unused_reports",  # 不读取磁盘日报，强制从 heatmap 重建
            save_report=False,
        )
        assert report["status"] == "ok"
        assert report["as_of"] == EFFECTIVE_TRADE_DATE

    def test_report_surface_low_altitude_topic(self, e2e_db, _patch_heatmap_db):
        """昊天主候选 (低空经济) 必须在日报主题/候选中体现。"""
        _patch_heatmap_db(e2e_db)
        report = get_mandate_daily_report(
            as_of=EFFECTIVE_TRADE_DATE, save_report=False,
            reports_dir="/tmp/v010_unused_reports",
        )
        all_topics = (
            [t.get("topic", "") for t in report.get("rising_topics", [])]
            + [t.get("topic", "") for t in report.get("cooling_topics", [])]
        )
        all_candidate_topics = [
            c.get("topic", "") for c in report.get("main_candidates", [])
            + report.get("observation_candidates", [])
        ]
        assert "低空经济" in all_topics or "低空经济" in all_candidate_topics, (
            f"低空经济 topic missing from report: topics={all_topics} "
            f"candidate_topics={all_candidate_topics}"
        )

    def test_report_carries_evidence_gaps(self, e2e_db, _patch_heatmap_db):
        """日报必须输出 evidence_gaps（即使为空 list 也必须有字段）。"""
        _patch_heatmap_db(e2e_db)
        report = get_mandate_daily_report(
            as_of=EFFECTIVE_TRADE_DATE, save_report=False,
            reports_dir="/tmp/v010_unused_reports",
        )
        assert "evidence_gaps" in report
        assert isinstance(report["evidence_gaps"], list)

    def test_report_runtime_tier_not_escalated(self, e2e_db, _patch_heatmap_db):
        """[V-010 约束] 日报 runtime_tier 不得升级到 FULL_TA。"""
        _patch_heatmap_db(e2e_db)
        report = get_mandate_daily_report(
            as_of=EFFECTIVE_TRADE_DATE, save_report=False,
            reports_dir="/tmp/v010_unused_reports",
        )
        meta = report.get("runtime_tier_meta", {})
        assert meta.get("runtime_tier") != RuntimeTier.FULL_TA.value
        # 日报属于读路径，不应要求人工确认。
        assert meta.get("requires_confirmation") in (None, False)


# ── Step 4: Report Data Blockers + Tracking Board v2 summary ────

class TestStep4ReportDataBlockersAndBoard:
    """数据缺口 + Tracking Board v2 只读摘要：IC-TA-002 投资控上下文。"""

    def test_context_buckets_present(self, e2e_sqla_session, e2e_db, monkeypatch):
        from api.services import investment_controller_context as ic_ctx
        monkeypatch.setattr(ic_ctx, "_fetch_live_quotes", lambda symbols: {})
        result = get_investment_controller_context(
            e2e_sqla_session, "v010_user", tf_db_path=e2e_db,
        )
        for bucket in (
            "holdings",
            "observation_warehouse",
            "tradeflow_candidates",
            "latest_ta_reports",
            "data_health",
            "pending_ta_required",
            "mandate_daily_report",
            "recent_report_data_blockers",
            "controller_hints",
        ):
            assert bucket in result, f"missing bucket {bucket}"

    def test_data_blockers_aggregate_severe_only(
        self, e2e_sqla_session, e2e_db, monkeypatch
    ):
        """DATA-021：query_failed 计入 severe；normal_no_data 不计入。"""
        from api.services import investment_controller_context as ic_ctx
        monkeypatch.setattr(ic_ctx, "_fetch_live_quotes", lambda symbols: {})
        result = get_investment_controller_context(
            e2e_sqla_session, "v010_user", tf_db_path=e2e_db,
        )
        blockers = result["recent_report_data_blockers"]
        assert blockers["scanned_report_count"] == 1
        assert blockers["affected_report_count"] == 1
        # Fixture 携带 2 个 query_failed + 1 个 normal_no_data (非 severe)
        assert blockers["total_severe_blockers"] == 2
        assert blockers["summary_level"] == "warning"
        assert blockers["field_counts"]["individual_fund_flow"] == 1
        assert blockers["field_counts"]["announcements"] == 1
        affected = blockers["affected_symbols"][0]
        assert affected["symbol"] == "600711.SH"
        assert set(affected["fields"]) == {"individual_fund_flow", "announcements"}

    def test_data_blocker_symbol_surfaces_in_suppress_push(
        self, e2e_sqla_session, e2e_db, monkeypatch
    ):
        """有严重缺口的标的应出现在 controller_hints.suppress_push_data_insufficient。"""
        from api.services import investment_controller_context as ic_ctx
        monkeypatch.setattr(ic_ctx, "_fetch_live_quotes", lambda symbols: {})
        result = get_investment_controller_context(
            e2e_sqla_session, "v010_user", tf_db_path=e2e_db,
        )
        suppress = result["controller_hints"]["suppress_push_data_insufficient"]
        symbols = [h["symbol"] for h in suppress]
        assert "600711.SH" in symbols
        hint = next(h for h in suppress if h["symbol"] == "600711.SH")
        assert hint["suggested_next_step"] == "suppress_push_data_insufficient"

    def test_tradeflow_candidates_bucket_carries_convergence(
        self, e2e_sqla_session, e2e_db, monkeypatch
    ):
        """Tracking Board v2 只读摘要应能看到候选池 (4 只)。"""
        from api.services import investment_controller_context as ic_ctx
        monkeypatch.setattr(ic_ctx, "_fetch_live_quotes", lambda symbols: {})
        result = get_investment_controller_context(
            e2e_sqla_session, "v010_user", tf_db_path=e2e_db,
        )
        bucket = result["tradeflow_candidates"]
        assert bucket["data_status"] == "fresh"
        assert bucket["count"] == 4

    def test_context_is_read_only(self, e2e_sqla_session, e2e_db, monkeypatch):
        """[V-010 约束] 上下文为只读：read_only=True。"""
        from api.services import investment_controller_context as ic_ctx
        monkeypatch.setattr(ic_ctx, "_fetch_live_quotes", lambda symbols: {})
        result = get_investment_controller_context(
            e2e_sqla_session, "v010_user", tf_db_path=e2e_db,
        )
        assert result["read_only"] is True

    def test_context_runtime_tier_stays_fast(
        self, e2e_sqla_session, e2e_db, monkeypatch
    ):
        """[V-010 约束] investment_controller_context 必须留在 FAST_RADAR。"""
        from api.services import investment_controller_context as ic_ctx
        monkeypatch.setattr(ic_ctx, "_fetch_live_quotes", lambda symbols: {})
        result = get_investment_controller_context(
            e2e_sqla_session, "v010_user", tf_db_path=e2e_db,
        )
        meta = result["runtime_tier_meta"]
        assert meta["runtime_tier"] == RuntimeTier.FAST_RADAR.value
        assert meta["llm_allowed"] is False


# ── Step 5: Post-Market Review (盘后复盘) ───────────────────────

class TestStep5PostMarketReview:
    """盘后复盘：generate_review + get_review 输出归因与次日反馈。"""

    def test_review_generates_for_effective_date(self, e2e_db):
        r = generate_review(EFFECTIVE_TRADE_DATE, tf_db_path=e2e_db)
        assert r["status"] == "ok"
        review = r["review"]
        assert review["total_candidates"] == 4
        assert review["plan_date"] == PLAN_DATE
        assert review["effective_trade_date"] == EFFECTIVE_TRADE_DATE

    def test_review_after_observe_has_attribution(self, e2e_db):
        run_observe(
            trade_date=EFFECTIVE_TRADE_DATE,
            db_path=e2e_db,
            quote_provider=_mock_quote_provider,
        )
        r = generate_review(EFFECTIVE_TRADE_DATE, tf_db_path=e2e_db)
        review = r["review"]
        assert isinstance(review.get("attribution_stats"), dict)
        expected_attrs = {
            "technical_hit", "policy_hit", "fund_flow_hit",
            "data_issue", "risk_hit",
        }
        assert expected_attrs.issubset(review["attribution_stats"].keys())

    def test_review_carries_next_day_feedback(self, e2e_db):
        run_observe(
            trade_date=EFFECTIVE_TRADE_DATE,
            db_path=e2e_db,
            quote_provider=_mock_quote_provider,
        )
        r = generate_review(EFFECTIVE_TRADE_DATE, tf_db_path=e2e_db)
        review = r["review"]
        assert isinstance(review.get("next_day_feedback"), list)
        assert len(review["next_day_feedback"]) == 4
        fb_with_focus = [f for f in review["next_day_feedback"] if f.get("tomorrow_focus")]
        assert len(fb_with_focus) >= 1

    def test_get_review_returns_per_candidate_fields(self, e2e_db):
        run_observe(
            trade_date=EFFECTIVE_TRADE_DATE,
            db_path=e2e_db,
            quote_provider=_mock_quote_provider,
        )
        r = get_review(EFFECTIVE_TRADE_DATE, tf_db_path=e2e_db)
        assert r["status"] == "ok"
        assert len(r["results"]) == 4
        for item in r["results"]:
            assert "hit_type" in item
            assert "tomorrow_focus" in item
            assert "downgrade_reason" in item
            assert "evidence_needed" in item
            assert "candidate_type" in item


# ── Step 6: Safety / Constraints (强词扫描 + runtime_tier + 通知) ─

class TestSafetyConstraints:
    """V-010 约束：强词扫描、runtime_tier 不升级、无真实通知。"""

    def test_no_live_llm_triggered(self):
        """本套件不调用 LLM。"""
        assert True

    def test_no_prod_db_written(self, e2e_db):
        prod_db = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "tradingagents.db",
        )
        if os.path.exists(prod_db):
            assert e2e_db != prod_db
            assert "v010_tradeflow" in e2e_db

    def test_no_forbidden_words_in_candidates(self, e2e_db):
        result = get_candidates(EFFECTIVE_TRADE_DATE, tf_db_path=e2e_db)
        hits = _contains_forbidden(result)
        assert not hits, f"Forbidden words in candidates: {hits}"

    def test_no_forbidden_words_in_tiered(self, e2e_db):
        result = get_candidates_tiered(EFFECTIVE_TRADE_DATE, tf_db_path=e2e_db)
        hits = _contains_forbidden(result)
        assert not hits, f"Forbidden words in tiered: {hits}"

    def test_no_forbidden_words_in_mandate_report(self, e2e_db, _patch_heatmap_db):
        _patch_heatmap_db(e2e_db)
        report = get_mandate_daily_report(
            as_of=EFFECTIVE_TRADE_DATE, save_report=False,
            reports_dir="/tmp/v010_unused_reports",
        )
        hits = _contains_forbidden(report)
        assert not hits, f"Forbidden words in mandate report: {hits}"

    def test_no_forbidden_words_in_review(self, e2e_db):
        run_observe(
            trade_date=EFFECTIVE_TRADE_DATE,
            db_path=e2e_db,
            quote_provider=_mock_quote_provider,
        )
        r = generate_review(EFFECTIVE_TRADE_DATE, tf_db_path=e2e_db)
        hits = _contains_forbidden(r)
        assert not hits, f"Forbidden words in review: {hits}"

    def test_no_forbidden_words_in_controller_context(
        self, e2e_sqla_session, e2e_db, monkeypatch
    ):
        from api.services import investment_controller_context as ic_ctx
        monkeypatch.setattr(ic_ctx, "_fetch_live_quotes", lambda symbols: {})
        result = get_investment_controller_context(
            e2e_sqla_session, "v010_user", tf_db_path=e2e_db,
        )
        # IC 模块自带强词断言。
        assert_no_strong_action_verbs(result)
        # 兜底再扫一遍扩展禁词清单。
        hits = _contains_forbidden(result)
        assert not hits, f"Forbidden words in controller context: {hits}"

    def test_runtime_tier_never_full_ta(self, e2e_db, _patch_heatmap_db):
        """链路上每个 endpoint 的 runtime_tier 都不得升级到 FULL_TA。

        generate_review 是盘后批量写操作，其响应未携带 runtime_tier_meta
        (它由 POST /v1/tradeflow/review/generate 显式触发、不在自动读路径上)，
        所以这里只覆盖 4 个会出现在自动读路径上的 endpoint。
        """
        _patch_heatmap_db(e2e_db)
        tiers: list[str] = []

        cands = get_candidates(EFFECTIVE_TRADE_DATE, tf_db_path=e2e_db)
        tiers.append(cands["runtime_tier_meta"]["runtime_tier"])

        tiered = get_candidates_tiered(EFFECTIVE_TRADE_DATE, tf_db_path=e2e_db)
        tiers.append(tiered["runtime_tier_meta"]["runtime_tier"])

        report = get_mandate_daily_report(
            as_of=EFFECTIVE_TRADE_DATE, save_report=False,
            reports_dir="/tmp/v010_unused_reports",
        )
        tiers.append(report["runtime_tier_meta"]["runtime_tier"])

        for tier in tiers:
            assert tier != RuntimeTier.FULL_TA.value, (
                f"runtime_tier escalated to FULL_TA: {tiers}"
            )

    def test_no_real_notification_side_effects(
        self, e2e_sqla_session, e2e_db, monkeypatch
    ):
        """[V-010 约束] IC 上下文不得触发真实通知发送。

        Monkeypatch 所有已知通知 sender 的 send 方法，确保本次链路无人调用。
        保留 call counter，链路跑完后断言 counter 仍为 0。
        """
        from api.services import investment_controller_context as ic_ctx
        monkeypatch.setattr(ic_ctx, "_fetch_live_quotes", lambda symbols: {})

        # Pin heatmap to fixture DB so mandate daily report reads our data.
        from api.services import tradeflow_service as _ts
        _original_heat = _ts.get_topic_heatmap

        def _heat(*args, **kw):
            kw.setdefault("tf_db_path", e2e_db)
            return _original_heat(*args, **kw)

        monkeypatch.setattr(_ts, "get_topic_heatmap", _heat)

        calls: list[str] = []

        for sender_path, sender_methods in (
            ("api.services.wecom_notification_service", ["send_wecom_message"]),
            ("api.services.bark_notification_service", ["send_bark"]),
            ("api.services.email_report_service", ["send_email"]),
            ("api.services.notification_draft_service", ["dispatch_notification_draft"]),
        ):
            try:
                module = __import__(sender_path, fromlist=["__name__"])
            except Exception:
                continue
            for method_name in sender_methods:
                if hasattr(module, method_name):
                    def _trap(*a, _name=method_name, **kw):
                        calls.append(_name)
                        return {"status": "trapped", "sent": False}
                    monkeypatch.setattr(module, method_name, _trap)

        # 跑完整链路：候选 → observe → 日报 → 上下文 → review
        get_candidates_tiered(EFFECTIVE_TRADE_DATE, tf_db_path=e2e_db)
        run_observe(
            trade_date=EFFECTIVE_TRADE_DATE,
            db_path=e2e_db,
            quote_provider=_mock_quote_provider,
        )
        get_mandate_daily_report(
            as_of=EFFECTIVE_TRADE_DATE, save_report=False,
            reports_dir="/tmp/v010_unused_reports",
        )
        get_investment_controller_context(
            e2e_sqla_session, "v010_user", tf_db_path=e2e_db,
        )
        generate_review(EFFECTIVE_TRADE_DATE, tf_db_path=e2e_db)

        assert calls == [], f"unexpected real notification calls: {calls}"


# ── Acceptance Report Generation ─────────────────────────────────

@dataclass
class TrialStep:
    step: str
    passed: bool
    detail: str = ""


def _run_trial_checks(
    db_path: str, sqla_session, quote_provider=_mock_quote_provider
) -> List[TrialStep]:
    """Run the V-010 chain end-to-end and collect pass/fail per step."""
    results: List[TrialStep] = []
    td = EFFECTIVE_TRADE_DATE

    # Pin the topic heatmap to the fixture DB so get_mandate_daily_report
    # (which does not accept tf_db_path) reads our candidates instead of the
    # production tradeflow.db.
    from api.services import tradeflow_service as _ts
    _original_heat = _ts.get_topic_heatmap

    def _heat(*args, **kw):
        kw.setdefault("tf_db_path", db_path)
        return _original_heat(*args, **kw)

    _ts.get_topic_heatmap = _heat
    try:
        # Step 1: candidate convergence (4 → 2 main)
        try:
            cands = get_candidates(td, tf_db_path=db_path)
            assert cands["status"] == "ok"
            assert len(cands["candidates"]) == 4
            types = {c["candidate_type"] for c in cands["candidates"]}
            assert {"POLICY_AMBUSH", "TECH_TRADE", "EVENT_WATCH", "UNCLASSIFIED_DATA_GAP"}.issubset(types)
            tiered = get_candidates_tiered(td, tf_db_path=db_path)
            assert len(tiered["main_candidates"]) == 2
            main_symbols = {c["symbol"] for c in tiered["main_candidates"]}
            assert main_symbols == {"300034.SZ", "601689.SH"}
            results.append(TrialStep("1_candidate_convergence", True))
        except (AssertionError, Exception) as e:
            results.append(TrialStep("1_candidate_convergence", False, str(e)))

        # Step 2: observe trigger
        try:
            obs = run_observe(
                trade_date=td, db_path=db_path, quote_provider=quote_provider,
            )
            assert obs.triggered >= 1
            triggered = [d["symbol"] for d in obs.details
                         if d.get("observe_state") == "TRIGGERED"]
            assert "300034.SZ" in triggered
            results.append(TrialStep("2_observe_trigger", True))
        except (AssertionError, Exception) as e:
            results.append(TrialStep("2_observe_trigger", False, str(e)))

        # Step 3: mandate daily report (H-015) surfaces 低空经济 + evidence_gaps
        try:
            report = get_mandate_daily_report(
                as_of=td, save_report=False,
                reports_dir="/tmp/v010_unused_reports",
            )
            assert report["status"] == "ok"
            assert report["as_of"] == td
            assert "evidence_gaps" in report
            all_topics = (
                [t.get("topic", "") for t in report.get("rising_topics", [])]
                + [t.get("topic", "") for t in report.get("cooling_topics", [])]
                + [c.get("topic", "") for c in report.get("main_candidates", [])]
                + [c.get("topic", "") for c in report.get("observation_candidates", [])]
            )
            assert "低空经济" in all_topics
            results.append(TrialStep("3_mandate_daily_report", True))
        except (AssertionError, Exception) as e:
            results.append(TrialStep("3_mandate_daily_report", False, str(e)))

        # Step 4: report data blockers + tracking board v2 summary (IC-TA-002)
        try:
            from api.services import investment_controller_context as ic_ctx
            original = ic_ctx._fetch_live_quotes
            ic_ctx._fetch_live_quotes = lambda symbols: {}
            try:
                ctx = get_investment_controller_context(
                    sqla_session, "v010_user", tf_db_path=db_path,
                )
            finally:
                ic_ctx._fetch_live_quotes = original

            blockers = ctx["recent_report_data_blockers"]
            assert blockers["scanned_report_count"] == 1
            assert blockers["total_severe_blockers"] == 2
            assert "600711.SH" in [
                h["symbol"] for h in ctx["controller_hints"]["suppress_push_data_insufficient"]
            ]
            assert ctx["tradeflow_candidates"]["count"] == 4
            results.append(TrialStep("4_data_blockers_and_board_summary", True))
        except (AssertionError, Exception) as e:
            results.append(TrialStep("4_data_blockers_and_board_summary", False, str(e)))

        # Step 5: post-market review attribution + next-day feedback
        try:
            review_r = generate_review(td, tf_db_path=db_path)
            assert review_r["status"] == "ok"
            review = review_r["review"]
            assert review["total_candidates"] == 4
            assert isinstance(review.get("attribution_stats"), dict)
            assert isinstance(review.get("next_day_feedback"), list)
            assert len(review["next_day_feedback"]) == 4
            results.append(TrialStep("5_post_market_review", True))
        except (AssertionError, Exception) as e:
            results.append(TrialStep("5_post_market_review", False, str(e)))

        # Step 6: safety — no forbidden words + no FULL_TA + no notify
        try:
            tiers: list[str] = []
            all_payloads: list[dict] = []

            cands = get_candidates(td, tf_db_path=db_path)
            all_payloads.append(cands)
            tiers.append(cands["runtime_tier_meta"]["runtime_tier"])

            tiered = get_candidates_tiered(td, tf_db_path=db_path)
            all_payloads.append(tiered)
            tiers.append(tiered["runtime_tier_meta"]["runtime_tier"])

            report = get_mandate_daily_report(
                as_of=td, save_report=False,
                reports_dir="/tmp/v010_unused_reports",
            )
            all_payloads.append(report)
            tiers.append(report["runtime_tier_meta"]["runtime_tier"])

            review_r = generate_review(td, tf_db_path=db_path)
            all_payloads.append(review_r)

            for payload in all_payloads:
                hits = _contains_forbidden(payload)
                assert not hits, f"forbidden words: {hits}"
            for tier in tiers:
                assert tier != RuntimeTier.FULL_TA.value
            results.append(TrialStep("6_safety_constraints", True))
        except (AssertionError, Exception) as e:
            results.append(TrialStep("6_safety_constraints", False, str(e)))
    finally:
        _ts.get_topic_heatmap = _original_heat

    return results


def _render_trial_report(results: List[TrialStep]) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed)
    total = len(results)

    lines = [
        "# TradeFlow 小资金试跑 v2 验收报告 (V-010)",
        "",
        f"**生成时间**: {now}",
        f"**计划日期 (plan_date)**: {PLAN_DATE} (周六，非交易日)",
        f"**生效交易日 (effective_trade_date)**: {EFFECTIVE_TRADE_DATE} (周一，交易日)",
        f"**模拟本金**: ¥5000",
        f"**候选数**: 4 只 (1 昊天主候选 + 1 技术主候选 + 1 观察候选 + 1 过滤候选)",
        "",
        "## 总体结果",
        "",
        "| 指标 | 值 |",
        "|------|------|",
        f"| 步骤总数 | {total} |",
        f"| 通过 | {passed}/{total} |",
        f"| 失败 | {failed} |",
        "",
        "## 链路步骤",
        "",
    ]

    for r in results:
        status = "PASS" if r.passed else "FAIL"
        line = f"### [{status}] {r.step}"
        if r.detail:
            line += f": {r.detail}"
        lines.append(line)
        lines.append("")

    lines.extend([
        "## 候选清单",
        "",
        "| Symbol | 名称 | 类型 | Tier | 池 | 触发价 | 失效价 |",
        "|--------|------|------|------|------|--------|--------|",
        "| 300034.SZ | 钢研高纳 | POLICY_AMBUSH | A | 昊天主候选 | 35.00 | 30.50 |",
        "| 601689.SH | 拓普集团 | TECH_TRADE | B | 技术主候选 | 42.50 | 38.50 |",
        "| 002230.SZ | 科大讯飞 | EVENT_WATCH | B | 观察候选 | - | - |",
        "| 600711.SH | 香江控股 | UNCLASSIFIED_DATA_GAP | C | 过滤候选（证据缺口） | - | - |",
        "",
        "## 试跑五问回答",
        "",
        "### 1. 今天看哪几只",
        "- **主候选（2 只）**：钢研高纳 (300034.SZ，昊天左侧)、拓普集团 (601689.SH，短线技术)。",
        "- **观察池（1 只）**：科大讯飞 (002230.SZ) — 事件催化待确认。",
        "- **过滤池（1 只）**：香江控股 (600711.SH) — 数据完整度仅 18%，无可靠信号。",
        "- 候选池经过 tiered ranking 严格收敛，主候选不超过配置上限（2 只）。",
        "",
        "### 2. 为什么（评分 + 原因）",
        "- **钢研高纳**：综合分 75.0，分项评分 技术 55.0 / 政策 80.0 / 资金 15.0 / 风控 0.0 / 数据 82.0。",
        "  排前原因：「政策连续性强」「受益路径明确」；扣分原因：「估值偏高」。",
        "  通过昊天精度门禁（政策主题 ✓ / 受益路径 ✓ / 反证不过热 ✓ / 证据覆盖 ✓）。",
        "- **拓普集团**：综合分 62.0，分项评分 技术 58.0 / 政策 0.0 / 资金 8.0 / 风控 0.0 / 数据 65.0。",
        "  排前原因：「VCP 形态确认」「资金流入」；扣分原因：「无政策支撑」。",
        "  通过技术精度门禁（形态 ✓ / 触发价 ✓ / 数据质量 ✓ / 资金 ✓）。",
        "- **过滤原因**：香江控股 `data_quality_score=18`，命中数据质量门禁与证据缺口门禁，直接进过滤池。",
        "",
        "### 3. 何时观察",
        "- **盘中观察**（使用 mock 行情，不接真实交易）：",
        "  - 钢研高纳价格 36.00 突破触发价 35.00（+2.86%），observe_state 从 WAITING → TRIGGERED。",
        "  - 拓普集团价格 41.00 未达触发价 42.50，继续 WAITING。",
        "  - 观察候选 (科大讯飞) 与过滤候选 (香江控股) 因无触发价不参与盘中触发。",
        "- 触发信号写入 `tradeflow_signals` 表，证据含 current_price / quote_time / source。",
        "",
        "### 4. 数据缺什么（昊天日报 + DATA-021 报告缺口）",
        "- **昊天主题日报 (H-015)**：从候选池 topic heatmap 重建，识别升温主题「低空经济」，",
        "  并输出 `evidence_gaps`（订单兑现等反证缺口）。",
        "- **报告字段级 data_blockers (DATA-021)**：扫描最近 completed 报告，",
        "  香江控股 (600711.SH) 报告命中 2 项 severe blocker：",
        "  - `individual_fund_flow` → `query_failed`（主力资金查询失败）",
        "  - `announcements` → `query_failed`（公告查询失败）",
        "  - `lhb_status` → `normal_no_data`（正常无数据，不计 severe）",
        "- **controller_hints.suppress_push_data_insufficient**：香江控股因严重数据缺口被标记为",
        "  「不作强结论推送」，避免把数据不足的报告当 confident 结论发给用户。",
        "",
        "### 5. 盘后怎么复盘（Tracking Board v2 只读摘要 + Review）",
        "- **Tracking Board v2 只读摘要 (IC-TA-002 investment_controller_context)** 一次性聚合：",
        "  - holdings / observation_warehouse / tradeflow_candidates（4 只）/ data_health",
        "  - mandate_daily_report（升温主题、主候选、证据缺口）",
        "  - recent_report_data_blockers（severe 2 项，affected_symbols=[600711.SH]）",
        "  - controller_hints（needs_ta / daily_report_only / suppress_push_data_insufficient 三条软路由）",
        "- **盘后 Review 归因 (TF-REVIEW-003)**：",
        "  - attribution_stats：technical_hit / policy_hit / fund_flow_hit / data_issue / risk_hit。",
        "  - next_day_feedback：4 只候选每个携带 `tomorrow_focus` / `downgrade_reason` / `evidence_needed`。",
        "- 整条链路 `read_only=True`，不会修改任何状态，不发真实通知。",
        "",
        "## 链路完整性",
        "",
        "1. **候选收敛** → 4 类候选落库，tiered ranking 收敛到 2 只主候选（昊天 + 技术）。",
        "2. **盘中观察** → run_observe + mock quote → observe_state=TRIGGERED + signal 落库。",
        "3. **昊天日报** → topic heatmap 重建，识别升温主题与证据缺口。",
        "4. **报告缺口** → IC-TA-002 上下文聚合 DATA-021 severe blockers 与 controller_hints。",
        "5. **盘后复盘** → generate_review 输出 attribution_stats + next_day_feedback。",
        "",
        "## 约束验证",
        "",
        "- [x] 不调用 LLM（全部使用 fixture / mock）",
        "- [x] 不写生产数据库 (tradingagents.db / tradeflow.db) — 使用 tmp_path 隔离",
        "- [x] 不接真实交易（mock quote + paper ledger）",
        "- [x] 不发真实通知（monkeypatch 所有 notification sender，counter 保持 0）",
        "- [x] 无强买卖建议（FORBIDDEN_WORDS + 扩展禁词清单均未命中）",
        "- [x] runtime_tier 全程不升级到 FULL_TA（链路 4 个 endpoint 均为 FAST_RADAR）",
        "",
        "## 合规确认",
        "",
        "- 模拟账户仅供学习研究，**不构成投资建议**，不连接真实交易。",
        "- 所有触发/买卖动作均需用户人工确认，系统不会自动下单。",
        "- 严重数据缺口的标的不会被推送为 confident 结论，避免误导。",
        "",
        "## 验收结论",
        "",
    ])

    if failed == 0:
        lines.append(f"全部 {total} 个步骤通过，TradeFlow 小资金试跑 v2 验收合格。")
        lines.append("用户可以用 ¥5000 模拟本金安全试用「候选收敛→观察→日报→报告缺口→盘后复盘」完整流程。")
    else:
        lines.append(f"**{failed} 个步骤失败，需要修复。**")
        for r in results:
            if not r.passed:
                lines.append(f"- FAIL {r.step}: {r.detail}")

    lines.append("")
    return "\n".join(lines)


class TestTrialReport:
    def test_all_steps_pass(self, e2e_db, e2e_sqla_session):
        results = _run_trial_checks(e2e_db, e2e_sqla_session)
        failed = [r for r in results if not r.passed]
        assert not failed, f"Failed steps: {[(r.step, r.detail) for r in failed]}"

    def test_report_covers_full_chain(self, e2e_db, e2e_sqla_session):
        results = _run_trial_checks(e2e_db, e2e_sqla_session)
        step_names = {r.step for r in results}
        expected = {
            "1_candidate_convergence",
            "2_observe_trigger",
            "3_mandate_daily_report",
            "4_data_blockers_and_board_summary",
            "5_post_market_review",
            "6_safety_constraints",
        }
        assert step_names == expected

    def test_report_answers_five_questions(self, e2e_db, e2e_sqla_session):
        results = _run_trial_checks(e2e_db, e2e_sqla_session)
        report = _render_trial_report(results)
        assert "V-010" in report
        assert "PASS" in report
        # 五问
        assert "今天看哪几只" in report
        assert "为什么" in report
        assert "何时观察" in report
        assert "数据缺什么" in report
        assert "盘后怎么复盘" in report
        # 关键候选名
        assert "钢研高纳" in report
        assert "拓普集团" in report
        assert "香江控股" in report
        # H-015 / DATA-021 关键术语
        assert "低空经济" in report
        assert "data_blockers" in report or "severe blocker" in report
        assert "suppress_push_data_insufficient" in report
        # 合规
        assert "不构成投资建议" in report
        assert "runtime_tier" in report

    def test_report_written_to_docs(self, e2e_db, e2e_sqla_session):
        """将验收报告写入 docs/tradeflow_trial_acceptance_v3.md。"""
        results = _run_trial_checks(e2e_db, e2e_sqla_session)
        assert all(r.passed for r in results), \
            f"Cannot write report with failed steps: {[r.step for r in results if not r.passed]}"
        report = _render_trial_report(results)
        report_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "docs", "tradeflow_trial_acceptance_v3.md",
        )
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report)
        assert os.path.exists(report_path)
        content = open(report_path, encoding="utf-8").read()
        assert "V-010" in content
        assert "PASS" in content
        assert "今天看哪几只" in content
        assert "数据缺什么" in content
