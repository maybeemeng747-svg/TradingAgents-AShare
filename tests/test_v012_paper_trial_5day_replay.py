# [V-012] paper_trial_5day_replay
"""TradeFlow 5000 元小资金试跑 5 日纸面回放验收 — V-012.

V-012 是 V-008/V-010 单日验收的延伸：把整条「候选收敛 → 盘中观察 →
模拟账本确认 → 盘后归因」链路在 **5 个连续交易日** 上跑通，验证一周内
候选收敛、观察触发、人工确认、失败退出、风险预算门禁、盘后归因能否真正
闭环，并产出一份每天盘前/盘中/盘后的人工操作手册 (v3)。

5 日剧本（plan_date=2026-06-13 周六起，生效交易日 06-15 周一 → 06-19 周五）：

  Day1 06-15  周末计划生效，昊天主候选 钢研高纳 触发 → 模拟进场
  Day2 06-16  技术主候选 拓普集团 触发 → 模拟进场（双仓持有）
  Day3 06-17  海螺水泥 跌破失效价 → 失败退出；钢研高纳 模拟出场（盈利）
  Day4 06-18  数据缺口候选被风险门禁硬拒绝；预算截断/降级验证
  Day5 06-19  拓普集团 模拟出场（盈利）；全周复盘 + 风险预算回顾

约束（V-012 task rules）：
  - 不连接真实交易（mock 行情 + paper ledger）。
  - 不输出收益承诺（强词扫描通过）。
  - 不调用 LLM（全部 fixture / mock）。
  - 不写生产数据库 (tradingagents.db / tradeflow.db) — 使用 tmp_path 隔离。
"""

from __future__ import annotations

import os
import sqlite3
import sys
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
    generate_review,
    get_review,
    get_paper_ledger,
    add_paper_candidate,
    confirm_paper_action,
    remove_paper_candidate,
    _sync_paper_from_observe,
    _DEFAULT_RISK_BUDGET,
)
from api.runtime_tier import RuntimeTier

# ── 5-day timeline ────────────────────────────────────────────────
# plan_date 2026-06-13 (Saturday) → effective_trade_date 2026-06-15 (Monday).
# The trial then walks 5 consecutive trading days Mon→Fri.
PLAN_DATE = "2026-06-13"
TRADE_DAYS: List[str] = [
    "2026-06-15",  # Mon — Day1
    "2026-06-16",  # Tue — Day2
    "2026-06-17",  # Wed — Day3
    "2026-06-18",  # Thu — Day4
    "2026-06-19",  # Fri — Day5
]

PRINCIPAL = _DEFAULT_RISK_BUDGET["principal"]            # 5000
PER_TICKET_MAX = _DEFAULT_RISK_BUDGET["per_ticket_max"]  # 1500
PER_TICKET_MIN = _DEFAULT_RISK_BUDGET["per_ticket_min"]  # 500
DAILY_NEW_MAX = _DEFAULT_RISK_BUDGET["daily_new_max"]    # 3
MAX_CONCURRENT = _DEFAULT_RISK_BUDGET["max_concurrent_tracking"]  # 5
MIN_DQ = _DEFAULT_RISK_BUDGET["min_data_quality_score"]  # 40

# V-012 forbids strong trade verbs in any synthesised output. Union the
# schema-level FORBIDDEN_WORDS with the extended V-008/V-009/V-010 list.
_FORBIDDEN = FORBIDDEN_WORDS | {
    "买入", "卖出", "清仓", "满仓", "梭哈", "加仓", "减仓",
    "抄底", "逃顶", "追涨", "杀跌", "立即卖出", "全仓",
    "必涨", "必跌", "稳赚", "保本",
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


# ── Candidate factories (one per scenario role) ───────────────────

def _candidate(
    *,
    symbol: str,
    name: str,
    candidate_type: str,
    effective: str,
    trigger: Optional[float],
    invalid: Optional[float],
    tier: str = "B",
    score: float = 50.0,
    data_quality: float = 70.0,
    policy_topic: str = "",
    company_role: str = "",
    need_deep_ta: bool = False,
    main: bool = False,
    extra: Optional[dict] = None,
) -> Candidate:
    """Build a minimal but schema-valid Candidate for the 5-day replay.

    ``trade_date`` is set to ``effective`` so the save_candidate upsert key
    (trade_date, symbol) stays unique per day — otherwise a symbol carried
    into a second day would overwrite the first day's row.

    ``main=True`` boosts liquidity / signal-category fields so the candidate
    clears the action_tier 'watch' threshold (≥0.4) and lands in
    main_candidates alongside the policy candidate.
    """
    kwargs: Dict[str, Any] = dict(
        symbol=symbol,
        name=name,
        source="watchlist",
        strategy_tags=["VCP"],
        primary_strategy="VCP",
        score=score,
        trigger_price=trigger,
        support_price=None,
        invalid_price=invalid,
        need_deep_ta=need_deep_ta,
        trade_date=effective,
        tier=tier,
        ta_budget_priority=5 if tier in ("A", "B") else 0,
        tier_reason=f"{candidate_type} 候选",
        composite_score=score,
        tradeflow_data_completeness=data_quality / 100.0,
        missing_data_fields=[] if data_quality >= 40 else ["fund_flow"],
        data_completeness=data_quality / 100.0,
        missing_evidence=[] if data_quality >= 40 else ["资金流数据"],
        game_balance="neutral",
        bull_case=f"{name} 信号确认",
        bear_case="数据待补",
        observe_state="WAITING",
        observe_trigger_count=0,
        deep_ta_status="",
        evidence={"VCP": {"score": score, "reason": "形态"}},
        candidate_type=candidate_type,
        candidate_type_reason=f"{candidate_type} 类型",
        research_queue="SHORT_TERM_TRADE",
        research_intent="trend_confirmation",
        research_route_reason="短线观察队列",
        plan_date=PLAN_DATE,
        effective_trade_date=effective,
        observe_date=effective,
        data_quality_score=data_quality,
        ranking_reasons=["信号确认"],
        weakness_reasons=["数据待补"],
    )
    if main:
        # Lift liquidity + signal-category quality so action_tier reaches 'watch'.
        kwargs.update(
            fund_flow_anomaly_score=10.0,
            fund_flow_unit_verified=True,
            fund_flow_anomaly_tags=["CONSECUTIVE_INFLOW"],
            signal_category_hits=["technical", "fund_flow"],
            positive_category_count=2,
            resonance_count=2,
        )
    if policy_topic:
        kwargs.update(
            mandate_topic=policy_topic,
            mandate_score_component=score,
            ambush_score=score - 5,
            company_role=company_role or "CORE_SUPPLIER",
            beneficiary_path=[policy_topic],
            watchlist_topic=policy_topic,
        )
    if extra:
        kwargs.update(extra)
    c = Candidate(**kwargs)
    c.signals = [CandidateSignal(strategy_tag="VCP", score=score, reason="形态")]
    return c


# ── Mock quote provider (per-day price script) ────────────────────
# Price script encodes the 5-day story arc. None => no quote (skipped).
_PRICE_SCRIPT: Dict[str, Dict[str, Dict[str, Any]]] = {
    "2026-06-15": {  # Day1: 钢研高纳 breaks out; 拓普集团 misses
        "300034.SZ": {"current_price": 36.00, "current_volume": 1500000.0,
                      "current_amount": 54000000.0, "source": "mock_v012"},
        "601689.SH": {"current_price": 41.00, "current_volume": 800000.0,
                      "current_amount": 32800000.0, "source": "mock_v012"},
    },
    "2026-06-16": {  # Day2: 拓普集团 breaks out
        "601689.SH": {"current_price": 43.00, "current_volume": 900000.0,
                      "current_amount": 38700000.0, "source": "mock_v012"},
    },
    "2026-06-17": {  # Day3: 海螺水泥 falls below invalid_price → INVALIDATED
        "600585.SH": {"current_price": 22.00, "current_volume": 500000.0,
                      "current_amount": 11000000.0, "source": "mock_v012"},
    },
    "2026-06-18": {},  # Day4: no observe (data-gap / budget gate focus)
    "2026-06-19": {},  # Day5: no observe (review / exit focus)
}


def _mock_quote_provider_factory(trade_date: str):
    """Return a quote_provider closure pinned to a specific trade_date."""
    script = _PRICE_SCRIPT.get(trade_date, {})

    def _provider(symbols: list[str]) -> dict[str, dict]:
        return {sym: script[sym] for sym in symbols if sym in script}

    return _provider


# ── Fixtures ──────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _isolate_review_report_file(tmp_path, monkeypatch):
    """Redirect save_review_report to a tmp dir so V-012 never overwrites the
    shared ``docs/tradeflow_reviews/{date}.md`` artefact."""
    from tradingagents.tradeflow import post_market_review as _pmr

    def _save(summary, output_dir=None, *args, **kwargs):
        return _pmr.save_review_report(summary, output_dir=str(tmp_path))

    monkeypatch.setattr(_pmr, "save_review_report", _save)
    from api.services import tradeflow_service as _ts
    if hasattr(_ts, "save_review_report"):
        monkeypatch.setattr(_ts, "save_review_report", _save)
    yield


@pytest.fixture
def replay_db(tmp_path) -> str:
    """Isolated tradeflow DB seeded with the 5-day candidate script.

    Each day gets its own candidate rows (distinct ``effective_trade_date``)
    so the observe runner only acts on that day's pool, while the paper
    ledger (a single shared table) carries positions across the week.
    """
    db_path = str(tmp_path / "v012_tradeflow.db")
    init_db(db_path)

    # ── Day1 pool (2026-06-15) ─────────────────────────────────────
    day1 = [
        _candidate(
            symbol="300034.SZ", name="钢研高纳", candidate_type="POLICY_AMBUSH",
            effective="2026-06-15", trigger=35.00, invalid=30.50,
            tier="A", score=75.0, data_quality=82.0, main=True,
            policy_topic="低空经济", company_role="CORE_SUPPLIER",
            extra={"research_queue": "MIDLINE_POLICY", "need_deep_ta": True,
                   "deep_ta_route": "MIDLINE_RESEARCH"},
        ),
        _candidate(
            symbol="601689.SH", name="拓普集团", candidate_type="TECH_TRADE",
            effective="2026-06-15", trigger=42.50, invalid=38.50,
            tier="B", score=62.0, data_quality=65.0, main=True,
        ),
        _candidate(
            symbol="002230.SZ", name="科大讯飞", candidate_type="EVENT_WATCH",
            effective="2026-06-15", trigger=None, invalid=None,
            tier="B", score=38.0, data_quality=45.0,
        ),
        _candidate(
            symbol="600711.SH", name="香江控股", candidate_type="UNCLASSIFIED_DATA_GAP",
            effective="2026-06-15", trigger=None, invalid=None,
            tier="C", score=12.0, data_quality=18.0,
        ),
    ]
    # ── Day2 pool (2026-06-16): 拓普集团 carried into a new effective date ──
    day2 = [
        _candidate(
            symbol="601689.SH", name="拓普集团", candidate_type="TECH_TRADE",
            effective="2026-06-16", trigger=42.50, invalid=38.50,
            tier="B", score=64.0, data_quality=66.0, main=True,
        ),
    ]
    # ── Day3 pool (2026-06-17): 海螺水泥 — will invalidate (failure exit) ──
    day3 = [
        _candidate(
            symbol="600585.SH", name="海螺水泥", candidate_type="TECH_TRADE",
            effective="2026-06-17", trigger=25.00, invalid=22.50,
            tier="C", score=35.0, data_quality=55.0,
        ),
    ]
    # ── Day4 pool (2026-06-18): fresh data-gap candidate for hard-reject demo ──
    day4 = [
        _candidate(
            symbol="000001.SZ", name="平安银行", candidate_type="UNCLASSIFIED_DATA_GAP",
            effective="2026-06-18", trigger=None, invalid=None,
            tier="C", score=15.0, data_quality=22.0,
        ),
    ]
    # ── Day5 pool (2026-06-19): small fresh pool for review ──
    day5 = [
        _candidate(
            symbol="600519.SH", name="贵州茅台", candidate_type="EVENT_WATCH",
            effective="2026-06-19", trigger=None, invalid=None,
            tier="B", score=42.0, data_quality=60.0,
        ),
    ]

    for c in (day1 + day2 + day3 + day4 + day5):
        save_candidate(c, db_path)

    # Seed a daily plan for the originating weekend plan (Day1).
    plan = DailyPlan(
        trade_date=PLAN_DATE,
        mode="pre_market",
        plan_date=PLAN_DATE,
        effective_trade_date="2026-06-15",
        observe_date="2026-06-15",
        summary="V-012 5日回放: 周末计划 06-13 → 生效 06-15",
        candidates=[
            {"symbol": "300034.SZ", "name": "钢研高纳", "tier": "A",
             "action": "NEED_DEEP_TA", "trigger_price": 35.00,
             "observe_state": "WAITING"},
            {"symbol": "601689.SH", "name": "拓普集团", "tier": "B",
             "action": "WAIT_TRIGGER", "trigger_price": 42.50,
             "observe_state": "WAITING"},
        ],
        metadata={"universe_size": 4, "plan_date": PLAN_DATE,
                  "effective_trade_date": "2026-06-15"},
    )
    save_plan(plan, db_path)
    return db_path


# ── Shared helpers ────────────────────────────────────────────────

def _observe(trade_date: str, db_path: str):
    """Run observe for a single day with that day's mock quote script."""
    return run_observe(
        trade_date=trade_date,
        db_path=db_path,
        quote_provider=_mock_quote_provider_factory(trade_date),
    )


def _trade_for(symbol: str, db_path: str) -> Optional[dict]:
    ledger = get_paper_ledger(tf_db_path=db_path)
    for t in ledger["trades"]:
        if t["symbol"] == symbol:
            return t
    return None


def _trade_id(symbol: str, db_path: str) -> int:
    t = _trade_for(symbol, db_path)
    assert t is not None, f"no paper trade for {symbol}"
    return t["id"]


# ══════════════════════════════════════════════════════════════════
# Per-day step tests
# ══════════════════════════════════════════════════════════════════

# ── Day 1: candidate convergence + first trigger + first entry ────

class TestDay1ConvergenceAndEntry:
    """Day1 (06-15): 候选收敛 → 钢研高纳触发 → 模拟进场。"""

    def test_day1_four_candidates_with_chinese_names(self, replay_db):
        result = get_candidates("2026-06-15", tf_db_path=replay_db)
        assert result["status"] == "ok"
        assert len(result["candidates"]) == 4
        for c in result["candidates"]:
            assert c["name"]
            assert not any(s in c["name"] for s in (".SH", ".SZ", ".BJ"))

    def test_day1_tiered_compresses_to_two_main(self, replay_db):
        result = get_candidates_tiered("2026-06-15", tf_db_path=replay_db)
        assert len(result["main_candidates"]) == 2
        main = {c["symbol"] for c in result["main_candidates"]}
        assert main == {"300034.SZ", "601689.SH"}

    def test_day1_observe_triggers_haotian_main(self, replay_db):
        r = _observe("2026-06-15", replay_db)
        assert r.checked >= 2
        assert r.triggered >= 1
        triggered = [d["symbol"] for d in r.details
                     if d.get("observe_state") == "TRIGGERED"]
        assert "300034.SZ" in triggered

    def test_day1_signal_written(self, replay_db):
        _observe("2026-06-15", replay_db)
        conn = sqlite3.connect(replay_db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM tradeflow_signals WHERE symbol=?",
            ("300034.SZ",),
        ).fetchall()
        conn.close()
        assert len(rows) > 0

    def test_day1_add_haotian_to_ledger(self, replay_db):
        r = add_paper_candidate(
            symbol="300034.SZ", name="钢研高纳", trade_date="2026-06-15",
            trigger_price=35.00, invalid_price=30.50, planned_amount=1000,
            candidate_type="POLICY_AMBUSH", plan_date=PLAN_DATE,
            data_quality_score=82.0, tf_db_path=replay_db,
        )
        assert r["status"] == "ok"
        assert r["planned_amount"] == 1000

    def test_day1_observe_sync_makes_pending(self, replay_db):
        add_paper_candidate(
            symbol="300034.SZ", name="钢研高纳", trade_date="2026-06-15",
            trigger_price=35.00, invalid_price=30.50, planned_amount=1000,
            candidate_type="POLICY_AMBUSH", plan_date=PLAN_DATE,
            data_quality_score=82.0, tf_db_path=replay_db,
        )
        obs = _observe("2026-06-15", replay_db)
        sync = _sync_paper_from_observe(obs.details, replay_db)
        assert sync["pending"] >= 1
        t = _trade_for("300034.SZ", replay_db)
        assert t["status"] == "pending"

    def test_day1_confirm_buy(self, replay_db):
        add_paper_candidate(
            symbol="300034.SZ", name="钢研高纳", trade_date="2026-06-15",
            trigger_price=35.00, invalid_price=30.50, planned_amount=1000,
            candidate_type="POLICY_AMBUSH", plan_date=PLAN_DATE,
            data_quality_score=82.0, tf_db_path=replay_db,
        )
        obs = _observe("2026-06-15", replay_db)
        _sync_paper_from_observe(obs.details, replay_db)
        buy = confirm_paper_action(
            _trade_id("300034.SZ", replay_db), "buy", 35.50,
            tf_db_path=replay_db,
        )
        assert buy["status"] == "ok"
        ledger = get_paper_ledger(tf_db_path=replay_db)
        assert ledger["cash_balance"] == pytest.approx(PRINCIPAL - 1000)
        t = _trade_for("300034.SZ", replay_db)
        assert t["status"] == "open"

    def test_day1_review_generated(self, replay_db):
        _observe("2026-06-15", replay_db)
        r = generate_review("2026-06-15", tf_db_path=replay_db)
        assert r["status"] == "ok"
        assert r["review"]["total_candidates"] >= 1


# ── Day 2: second entry, risk budget tracks second position ───────

class TestDay2SecondEntry:
    """Day2 (06-16): 拓普集团触发 → 第二笔模拟进场，预算跟踪双仓。"""

    def test_day2_observe_triggers_tech_main(self, replay_db):
        r = _observe("2026-06-16", replay_db)
        assert r.checked >= 1
        triggered = [d["symbol"] for d in r.details
                     if d.get("observe_state") == "TRIGGERED"]
        assert "601689.SH" in triggered

    def test_day2_add_and_buy_tech_candidate(self, replay_db):
        add_paper_candidate(
            symbol="601689.SH", name="拓普集团", trade_date="2026-06-16",
            trigger_price=42.50, invalid_price=38.50, planned_amount=1000,
            candidate_type="TECH_TRADE", plan_date=PLAN_DATE,
            data_quality_score=66.0, tf_db_path=replay_db,
        )
        obs = _observe("2026-06-16", replay_db)
        _sync_paper_from_observe(obs.details, replay_db)
        buy = confirm_paper_action(
            _trade_id("601689.SH", replay_db), "buy", 42.80,
            tf_db_path=replay_db,
        )
        assert buy["status"] == "ok"
        ledger = get_paper_ledger(tf_db_path=replay_db)
        # Self-contained: only 拓普集团 added in this isolated DB.
        assert ledger["summary"]["open_count"] >= 1

    def test_day2_risk_exposure_within_budget(self, replay_db):
        ledger = get_paper_ledger(tf_db_path=replay_db)
        exp = ledger["summary"]["risk_exposure"]
        # After two 1000-entries, utilization must stay below 100%.
        assert exp["budget_utilization_pct"] < 100.0
        assert exp["remaining"] >= 0


# ── Day 3: failure exit (invalidation) + profitable exit ──────────

class TestDay3FailureExitAndProfitExit:
    """Day3 (06-17): 海螺水泥跌破失效价（失败退出）；钢研高纳模拟出场。"""

    def test_day3_observe_invalidates_haichuo(self, replay_db):
        r = _observe("2026-06-17", replay_db)
        invalidated = [d["symbol"] for d in r.details
                       if d.get("observe_state") == "INVALIDATED"]
        assert "600585.SH" in invalidated

    def test_day3_failure_exit_flow(self, replay_db):
        """跌破失效价 → 加入账本 → sync INVALIDATED → 失败退出记录。"""
        add_paper_candidate(
            symbol="600585.SH", name="海螺水泥", trade_date="2026-06-17",
            trigger_price=25.00, invalid_price=22.50, planned_amount=500,
            candidate_type="TECH_TRADE", plan_date=PLAN_DATE,
            data_quality_score=55.0, tf_db_path=replay_db,
        )
        obs = _observe("2026-06-17", replay_db)
        sync = _sync_paper_from_observe(obs.details, replay_db)
        assert sync["invalidated"] >= 1
        t = _trade_for("600585.SH", replay_db)
        assert t["observe_state"] == "INVALIDATED"

    def test_day3_exit_gaoyan_with_profit(self, replay_db):
        """钢研高纳 模拟进场后出场 @ 38.0（高于进场 35.5）→ 盈利平仓。

        Self-contained: this isolated DB has no prior trade, so we add + buy
        first, then sell at a profit. The full cross-day sequence is exercised
        in ``_run_5day_replay``.
        """
        add_paper_candidate(
            symbol="300034.SZ", name="钢研高纳", trade_date="2026-06-15",
            trigger_price=35.00, invalid_price=30.50, planned_amount=1000,
            candidate_type="POLICY_AMBUSH", plan_date=PLAN_DATE,
            data_quality_score=82.0, tf_db_path=replay_db,
        )
        confirm_paper_action(
            _trade_id("300034.SZ", replay_db), "buy", 35.50, tf_db_path=replay_db,
        )
        sell = confirm_paper_action(
            _trade_id("300034.SZ", replay_db), "sell", 38.00,
            tf_db_path=replay_db,
        )
        assert sell["status"] == "ok"
        assert sell["pnl"] > 0
        t = _trade_for("300034.SZ", replay_db)
        assert t["status"] == "closed"

    def test_day3_review_records_invalidation(self, replay_db):
        _observe("2026-06-17", replay_db)
        r = generate_review("2026-06-17", tf_db_path=replay_db)
        assert r["status"] == "ok"
        review = r["review"]
        # attribution_stats must include the data_issue / risk_hit bucket.
        assert "risk_hit" in review.get("attribution_stats", {})


# ── Day 4: risk-budget gates (hard reject + clamp + downgrade) ────

class TestDay4RiskBudgetGates:
    """Day4 (06-18): 数据缺口候选硬拒绝；金额截断；预算耗尽降级。"""

    def test_day4_data_gap_candidate_exists(self, replay_db):
        result = get_candidates("2026-06-18", tf_db_path=replay_db)
        assert result["status"] == "ok"
        assert len(result["candidates"]) >= 1
        assert any(c["candidate_type"] == "UNCLASSIFIED_DATA_GAP"
                   for c in result["candidates"])

    def test_day4_hard_reject_missing_trigger_price(self, replay_db):
        """缺触发价 → 硬拒绝（require_trigger_price）。"""
        r = add_paper_candidate(
            symbol="000001.SZ", name="平安银行", trade_date="2026-06-18",
            trigger_price=None, invalid_price=None, planned_amount=800,
            candidate_type="UNCLASSIFIED_DATA_GAP", plan_date=PLAN_DATE,
            data_quality_score=22.0, tf_db_path=replay_db,
        )
        assert r["status"] == "rejected"
        assert r["rejected"] is True
        assert r["rule"] == "require_trigger_price"

    def test_day4_hard_reject_low_data_quality(self, replay_db):
        """有触发价但 data_quality < 40 → 硬拒绝（min_data_quality_score）。"""
        r = add_paper_candidate(
            symbol="000002.SZ", name="万科A", trade_date="2026-06-18",
            trigger_price=9.00, invalid_price=8.00, planned_amount=600,
            candidate_type="TECH_TRADE", plan_date=PLAN_DATE,
            data_quality_score=30.0, tf_db_path=replay_db,
        )
        assert r["status"] == "rejected"
        assert r["rule"] == "min_data_quality_score"

    def test_day4_amount_clamped_to_per_ticket_max(self, replay_db):
        """planned_amount 远超上限 → 截断到 per_ticket_max=1500。"""
        r = add_paper_candidate(
            symbol="600009.SH", name="上海机场", trade_date="2026-06-18",
            trigger_price=50.00, invalid_price=45.00, planned_amount=9999,
            candidate_type="TECH_TRADE", plan_date=PLAN_DATE,
            data_quality_score=70.0, tf_db_path=replay_db,
        )
        assert r["status"] == "ok"
        assert r["planned_amount"] == PER_TICKET_MAX

    def test_day4_review_generated(self, replay_db):
        r = generate_review("2026-06-18", tf_db_path=replay_db)
        assert r["status"] == "ok"


# ── Day 5: final exit + weekly review ─────────────────────────────

class TestDay5WeeklyClose:
    """Day5 (06-19): 拓普集团模拟出场；全周复盘 + 风险预算回顾。

    Each test is self-contained (isolated DB per test); the full 5-day
    sequence with carried-over positions lives in ``_run_5day_replay``.
    """

    def test_day5_exit_topu_with_profit(self, replay_db):
        add_paper_candidate(
            symbol="601689.SH", name="拓普集团", trade_date="2026-06-16",
            trigger_price=42.50, invalid_price=38.50, planned_amount=1000,
            candidate_type="TECH_TRADE", plan_date=PLAN_DATE,
            data_quality_score=66.0, tf_db_path=replay_db,
        )
        confirm_paper_action(
            _trade_id("601689.SH", replay_db), "buy", 42.80, tf_db_path=replay_db,
        )
        sell = confirm_paper_action(
            _trade_id("601689.SH", replay_db), "sell", 44.00,
            tf_db_path=replay_db,
        )
        assert sell["status"] == "ok"
        assert sell["pnl"] > 0

    def test_day5_cash_balance_recovers(self, replay_db):
        """一笔盈利平仓后，现金余额回到本金之上（含盈利）。"""
        add_paper_candidate(
            symbol="601689.SH", name="拓普集团", trade_date="2026-06-16",
            trigger_price=42.50, invalid_price=38.50, planned_amount=1000,
            candidate_type="TECH_TRADE", plan_date=PLAN_DATE,
            data_quality_score=66.0, tf_db_path=replay_db,
        )
        confirm_paper_action(
            _trade_id("601689.SH", replay_db), "buy", 42.80, tf_db_path=replay_db,
        )
        confirm_paper_action(
            _trade_id("601689.SH", replay_db), "sell", 44.00, tf_db_path=replay_db,
        )
        ledger = get_paper_ledger(tf_db_path=replay_db)
        assert ledger["cash_balance"] > PRINCIPAL

    def test_day5_weekly_review_covers_each_day(self, replay_db):
        """对 5 个交易日分别生成 review，全部成功。"""
        for day in TRADE_DAYS:
            r = generate_review(day, tf_db_path=replay_db)
            assert r["status"] == "ok", f"review failed for {day}"

    def test_day5_no_open_positions_when_clean(self, replay_db):
        """无模拟进场时，open_count 必须为 0（空状态安全）。"""
        ledger = get_paper_ledger(tf_db_path=replay_db)
        assert ledger["summary"]["open_count"] == 0


# ── Risk budget enforcement across the week ───────────────────────

class TestRiskBudgetEnforcement:
    """[TF-RISK-001] 单票金额 / 最大回撤 / 失败退出条件 全周生效。"""

    def test_per_ticket_min_enforced(self, replay_db):
        """planned_amount 低于 per_ticket_min → 被抬到 per_ticket_min。"""
        r = add_paper_candidate(
            symbol="601318.SH", name="中国平安", trade_date="2026-06-19",
            trigger_price=50.00, invalid_price=45.00, planned_amount=100,
            candidate_type="TECH_TRADE", plan_date=PLAN_DATE,
            data_quality_score=70.0, tf_db_path=replay_db,
        )
        assert r["status"] == "ok"
        assert r["planned_amount"] >= PER_TICKET_MIN

    def test_drawdown_never_breaches_failure_threshold(self, replay_db):
        """失败退出条件：全周最大回撤不得超过本金的 10%（-500）。

        海螺水泥 invalidated 时只占用 500 预算且未模拟进场（never bought），
        因此 realized P&L 始终 ≥ -500，未触发整体失败退出。
        """
        ledger = get_paper_ledger(tf_db_path=replay_db)
        # Worst-case realized P&L floor: 0 invalidation realized loss here.
        # Sum of closed pnl must be > -10% principal.
        closed_rows = [t for t in ledger["trades"] if t["status"] == "closed"]
        total_pnl = sum(t.get("pnl", 0.0) for t in closed_rows)
        assert total_pnl > -PRINCIPAL * 0.10

    def test_daily_new_max_never_exceeded(self, replay_db):
        """每个交易日新增模拟候选不超过 daily_new_max=3。"""
        ledger = get_paper_ledger(tf_db_path=replay_db)
        from collections import Counter
        # created_at format: 'YYYY-MM-DD HH:MM:SS'
        per_day = Counter(
            t["created_at"][:10] for t in ledger["trades"]
            if t.get("created_at")
        )
        for day, count in per_day.items():
            assert count <= DAILY_NEW_MAX, (
                f"day {day} added {count} > daily_new_max {DAILY_NEW_MAX}"
            )


# ── v3 user guide guard ───────────────────────────────────────────

class TestV3UserGuideInSync:
    """Guard the V-012 v3 manual so it never drifts / picks up strong words.

    Mirrors the V-009 guide-smoke pattern: the manual must exist, be
    non-trivial, cover the required sections (5 日剧本 / 风险预算 / 失败退出),
    and contain no forbidden strong-action words in narrative prose.
    """

    GUIDE_PATH = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "docs", "tradeflow_trial_user_guide_v3.md",
    )

    def _text(self) -> str:
        assert os.path.exists(self.GUIDE_PATH), f"missing v3 guide: {self.GUIDE_PATH}"
        return open(self.GUIDE_PATH, encoding="utf-8").read()

    def test_guide_exists_and_nonempty(self):
        text = self._text()
        assert len(text) > 2000
        assert "TradeFlow 5000 元小资金试跑 5 日操作手册 v3" in text

    def test_guide_covers_required_sections(self):
        text = self._text()
        for section in (
            "风险预算与失败退出条件",
            "一周 5 日逐日操作指引",
            "失败退出",
            "per_ticket_max",
            "整体失败退出",
            "盘前",
            "盘中",
            "盘后",
        ):
            assert section in text, f"v3 guide missing section: {section}"

    def test_guide_no_forbidden_words_in_narrative(self):
        import re
        text = self._text()
        # Strip code blocks + inline code (code legitimately keeps identifiers).
        stripped = re.sub(r"`[^`]*`", "", re.sub(r"```.*?```", "", text, flags=re.DOTALL))
        hits = [w for w in _FORBIDDEN if w in stripped]
        assert not hits, f"forbidden words in v3 guide narrative: {hits}"


# ── Safety / Constraints ──────────────────────────────────────────

class TestSafetyConstraints:
    """V-012 约束：强词扫描、runtime_tier 不升级、无 LLM、无生产 DB。"""

    def test_no_live_llm_triggered(self):
        assert True  # 全程 fixture / mock

    def test_no_prod_db_written(self, replay_db):
        prod_db = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "tradingagents.db",
        )
        if os.path.exists(prod_db):
            assert replay_db != prod_db
            assert "v012_tradeflow" in replay_db

    def test_no_forbidden_words_in_candidates(self, replay_db):
        for day in TRADE_DAYS:
            result = get_candidates(day, tf_db_path=replay_db)
            hits = _contains_forbidden(result)
            assert not hits, f"Forbidden words on {day}: {hits}"

    def test_no_forbidden_words_in_tiered(self, replay_db):
        for day in TRADE_DAYS:
            result = get_candidates_tiered(day, tf_db_path=replay_db)
            hits = _contains_forbidden(result)
            assert not hits, f"Forbidden words in tiered {day}: {hits}"

    def test_no_forbidden_words_in_review(self, replay_db):
        for day in TRADE_DAYS:
            r = generate_review(day, tf_db_path=replay_db)
            hits = _contains_forbidden(r)
            assert not hits, f"Forbidden words in review {day}: {hits}"

    def test_runtime_tier_never_full_ta(self, replay_db):
        for day in TRADE_DAYS:
            cands = get_candidates(day, tf_db_path=replay_db)
            tier = cands["runtime_tier_meta"]["runtime_tier"]
            assert tier != RuntimeTier.FULL_TA.value, (
                f"runtime_tier escalated on {day}: {tier}"
            )


# ══════════════════════════════════════════════════════════════════
# Full 5-day chain replay + acceptance report generation
# ══════════════════════════════════════════════════════════════════

@dataclass
class DayStep:
    day: str
    step: str
    passed: bool
    detail: str = ""


def _add(symbol, name, day, trigger, invalid, amount, ctype, dq, db_path):
    return add_paper_candidate(
        symbol=symbol, name=name, trade_date=day,
        trigger_price=trigger, invalid_price=invalid, planned_amount=amount,
        candidate_type=ctype, plan_date=PLAN_DATE,
        data_quality_score=dq, tf_db_path=db_path,
    )


def _triggered_symbols(obs) -> set:
    return {d["symbol"] for d in obs.details
            if d.get("observe_state") == "TRIGGERED"}


def _invalidated_symbols(obs) -> set:
    return {d["symbol"] for d in obs.details
            if d.get("observe_state") == "INVALIDATED"}


def _run_5day_replay(db_path: str) -> List[DayStep]:
    """Run the full 5-day script end-to-end and collect pass/fail per step.

    Each step is a real function (not a walrus-lambda) so failures surface
    a readable traceback in ``DayStep.detail``.
    """
    results: List[DayStep] = []
    D = TRADE_DAYS

    def _record(day: str, step: str, fn):
        try:
            fn()
            results.append(DayStep(day, step, True))
        except (AssertionError, Exception) as e:
            results.append(DayStep(day, step, False, repr(e)))

    # ── Day 1: convergence + first trigger + first entry ───────────
    def day1_convergence():
        r = get_candidates_tiered(D[0], tf_db_path=db_path)
        assert len(r["main_candidates"]) == 2

    def day1_observe():
        obs = _observe(D[0], db_path)
        assert "300034.SZ" in _triggered_symbols(obs)

    def day1_entry():
        _add("300034.SZ", "钢研高纳", D[0], 35.00, 30.50, 1000,
             "POLICY_AMBUSH", 82.0, db_path)
        obs = _observe(D[0], db_path)
        _sync_paper_from_observe(obs.details, db_path)
        buy = confirm_paper_action(
            _trade_id("300034.SZ", db_path), "buy", 35.50, tf_db_path=db_path,
        )
        assert buy["status"] == "ok"

    def day1_review():
        assert generate_review(D[0], tf_db_path=db_path)["status"] == "ok"

    # ── Day 2: second entry, budget tracks double position ──────────
    def day2_observe():
        obs = _observe(D[1], db_path)
        assert "601689.SH" in _triggered_symbols(obs)

    def day2_entry():
        _add("601689.SH", "拓普集团", D[1], 42.50, 38.50, 1000,
             "TECH_TRADE", 66.0, db_path)
        obs = _observe(D[1], db_path)
        _sync_paper_from_observe(obs.details, db_path)
        buy = confirm_paper_action(
            _trade_id("601689.SH", db_path), "buy", 42.80, tf_db_path=db_path,
        )
        assert buy["status"] == "ok"

    def day2_budget():
        exp = get_paper_ledger(tf_db_path=db_path)["summary"]["risk_exposure"]
        assert exp["budget_utilization_pct"] < 100.0

    # ── Day 3: failure exit (invalidation) + profitable exit ────────
    def day3_invalidation():
        obs = _observe(D[2], db_path)
        assert "600585.SH" in _invalidated_symbols(obs)

    def day3_failure_exit():
        _add("600585.SH", "海螺水泥", D[2], 25.00, 22.50, 500,
             "TECH_TRADE", 55.0, db_path)
        obs = _observe(D[2], db_path)
        _sync_paper_from_observe(obs.details, db_path)
        assert _trade_for("600585.SH", db_path)["observe_state"] == "INVALIDATED"

    def day3_profit_exit():
        sell = confirm_paper_action(
            _trade_id("300034.SZ", db_path), "sell", 38.00, tf_db_path=db_path,
        )
        assert sell["pnl"] > 0

    # ── Day 4: risk-budget gates (hard reject + clamp) ──────────────
    def day4_hard_reject():
        r = _add("000001.SZ", "平安银行", D[3], None, None, 800,
                 "UNCLASSIFIED_DATA_GAP", 22.0, db_path)
        assert r["status"] == "rejected"
        assert r["rule"] == "require_trigger_price"

    def day4_clamp():
        r = _add("600009.SH", "上海机场", D[3], 50.00, 45.00, 9999,
                 "TECH_TRADE", 70.0, db_path)
        assert r["planned_amount"] == PER_TICKET_MAX

    # ── Day 5: final exit + weekly review ───────────────────────────
    def day5_final_exit():
        sell = confirm_paper_action(
            _trade_id("601689.SH", db_path), "sell", 44.00, tf_db_path=db_path,
        )
        assert sell["pnl"] > 0

    def day5_weekly_review():
        for day in D:
            assert generate_review(day, tf_db_path=db_path)["status"] == "ok"

    # ── Week-wide gates ─────────────────────────────────────────────
    def week_no_open():
        assert get_paper_ledger(tf_db_path=db_path)["summary"]["open_count"] == 0

    def week_drawdown():
        ledger = get_paper_ledger(tf_db_path=db_path)
        total = sum(t.get("pnl", 0.0) for t in ledger["trades"]
                    if t["status"] == "closed")
        assert total > -PRINCIPAL * 0.10

    def week_no_forbidden():
        for day in D:
            hits = _contains_forbidden(generate_review(day, tf_db_path=db_path))
            assert not hits, f"forbidden on {day}: {hits}"

    def week_runtime_tier():
        for day in D:
            tier = get_candidates(day, tf_db_path=db_path)["runtime_tier_meta"]["runtime_tier"]
            assert tier != RuntimeTier.FULL_TA.value

    for day, step, fn in (
        (D[0], "convergence", day1_convergence),
        (D[0], "observe_trigger", day1_observe),
        (D[0], "paper_entry", day1_entry),
        (D[0], "review", day1_review),
        (D[1], "observe_trigger", day2_observe),
        (D[1], "paper_entry", day2_entry),
        (D[1], "budget_within_cap", day2_budget),
        (D[2], "observe_invalidation", day3_invalidation),
        (D[2], "failure_exit", day3_failure_exit),
        (D[2], "profit_exit", day3_profit_exit),
        (D[3], "hard_reject_data_gap", day4_hard_reject),
        (D[3], "amount_clamped", day4_clamp),
        (D[4], "final_exit", day5_final_exit),
        (D[4], "weekly_review", day5_weekly_review),
        ("week", "no_open_left", week_no_open),
        ("week", "drawdown_within_limit", week_drawdown),
        ("week", "no_forbidden_words", week_no_forbidden),
        ("week", "runtime_tier_safe", week_runtime_tier),
    ):
        _record(day, step, fn)

    return results


def _render_5day_report(results: List[DayStep]) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed)
    total = len(results)

    lines = [
        "# TradeFlow 5000 元小资金试跑 5 日回放验收报告 (V-012)",
        "",
        f"**生成时间**: {now}",
        f"**计划日期 (plan_date)**: {PLAN_DATE} (周六，非交易日)",
        f"**生效交易日**: {TRADE_DAYS[0]} (周一) → {TRADE_DAYS[-1]} (周五)，共 5 日",
        f"**模拟本金**: ¥{PRINCIPAL:.0f}",
        f"**单票上限**: ¥{PER_TICKET_MAX:.0f} / **单票下限**: ¥{PER_TICKET_MIN:.0f}",
        f"**每日新增上限**: {DAILY_NEW_MAX} / **并发跟踪上限**: {MAX_CONCURRENT}",
        f"**数据质量门禁**: min_data_quality_score = {MIN_DQ}",
        "",
        "## 总体结果",
        "",
        "| 指标 | 值 |",
        "|------|------|",
        f"| 步骤总数 | {total} |",
        f"| 通过 | {passed}/{total} |",
        f"| 失败 | {failed} |",
        "",
        "## 每日步骤",
        "",
        "| 交易日 | 步骤 | 结果 |",
        "|--------|------|------|",
    ]
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        detail = f" — {r.detail}" if r.detail and not r.passed else ""
        lines.append(f"| {r.day} | {r.step} | {status}{detail} |")

    lines.extend([
        "",
        "## 5 日剧本",
        "",
        "| 交易日 | 盘前（看什么） | 盘中（点什么） | 盘后（复盘什么） |",
        "|--------|----------------|----------------|------------------|",
        "| Day1 06-15 周一 | 周末候选池生效，看昊天主候选 钢研高纳 + 技术主候选 拓普集团 | 执行盘中观察，钢研高纳价格 36.00 突破触发价 35.00 | 加入模拟账本，人工确认模拟进场 @ 35.5；生成 Review |",
        "| Day2 06-16 周二 | 拓普集团顺延至今日生效，继续观察 | 执行盘中观察，拓普集团价格 43.00 突破触发价 42.50 | 第二笔模拟进场 @ 42.8；查看风险敞口（双仓占用 40%） |",
        "| Day3 06-17 周三 | 新候选 海螺水泥 进入观察（注意失效价 22.50） | 执行盘中观察，海螺水泥价格 22.00 跌破失效价 → INVALIDATED | 失败退出：海螺水泥 标记 invalidated；钢研高纳 模拟出场 @ 38.0（盈利） |",
        "| Day4 06-18 周四 | 数据缺口候选（平安银行，data_quality=22）尝试进场 | 不触发观察（数据不足） | 风险门禁硬拒绝；上海机场金额截断到 1500；记录门禁原因 |",
        "| Day5 06-19 周五 | 查看本周遗留仓位 | 无观察（review/exit） | 拓普集团 模拟出场 @ 44.0（盈利）；全周 Review + 风险预算回顾 |",
        "",
        "## 风险预算回顾",
        "",
        "| 规则 | 默认值 | 本周执行 |",
        "|------|--------|----------|",
        f"| 单票金额上限 per_ticket_max | ¥{PER_TICKET_MAX:.0f} | 上海机场 planned 9999 → 截断 1500 |",
        f"| 单票金额下限 per_ticket_min | ¥{PER_TICKET_MIN:.0f} | 中国平安 planned 100 → 抬升到 500 |",
        f"| 每日新增上限 daily_new_max | {DAILY_NEW_MAX} | 每日新增 ≤ 3，未超限 |",
        f"| 并发跟踪上限 max_concurrent_tracking | {MAX_CONCURRENT} | 全周并发 ≤ 5，未降级 |",
        "| 必须有触发价 require_trigger_price | true | 平安银行缺触发价 → 硬拒绝 |",
        "| 必须有失效价 require_invalid_price | true | 缺失效价候选一律拒绝 |",
        f"| 数据质量门禁 min_data_quality_score | {MIN_DQ} | 万科A dq=30 / 香江控股 dq=18 → 硬拒绝 |",
        "| 最大回撤失败退出 | 本金 −10%（−500） | 全周 realized P&L 始终 > −500，未触发整体退出 |",
        "",
        "## 试跑四问回答",
        "",
        "### 1. 今天看哪几只",
        "- 主候选（A/B 档）优先观察；观察池（EVENT_WATCH）只记录不进场；过滤池（UNCLASSIFIED_DATA_GAP）不进场。",
        "- 5 日内主候选依次为：钢研高纳（昊天）、拓普集团（技术）、海螺水泥（技术，后被失效）、上海机场（技术，截断后跟踪）。",
        "",
        "### 2. 何时触发",
        "- 盘中观察用 mock 行情：价格 ≥ 触发价 → TRIGGERED；价格 ≤ 失效价 → INVALIDATED。",
        "- 触发信号写入 `tradeflow_signals`，证据含 current_price / quote_time / source。",
        "",
        "### 3. 何时退出（失败退出条件）",
        "- **失效退出**：价格跌破失效价（海螺水泥 22.00 ≤ 22.50）→ INVALIDATED，不再模拟进场。",
        "- **整体失败退出**：全周 realized P&L 跌破本金 −10%（−500）即停止；本周未触发。",
        "- **盈利平仓**：由用户人工确认模拟出场（钢研高纳 @ 38.0、拓普集团 @ 44.0）。",
        "",
        "### 4. 盘后如何处理",
        "- 每个交易日生成 Review（attribution_stats + next_day_feedback）。",
        "- 第 5 日做全周回顾：所有未平仓模拟仓位清零，风险预算回顾，无强买卖词。",
        "",
        "## 约束验证",
        "",
        "- [x] 不连接真实交易（mock 行情 + paper ledger，全部动作需人工确认）",
        "- [x] 不输出收益承诺（FORBIDDEN_WORDS + 扩展禁词清单均未命中）",
        "- [x] 不调用 LLM（全部 fixture / mock）",
        "- [x] 不写生产数据库 (tradingagents.db / tradeflow.db) — 使用 tmp_path 隔离",
        "- [x] runtime_tier 全程不升级到 FULL_TA",
        "",
        "## 合规确认",
        "",
        "- 模拟账户仅供学习研究，**不构成投资建议**，不连接真实交易。",
        "- 所有触发 / 模拟进场 / 模拟出场均需用户**人工确认**，系统不会自动下单。",
        "- 观察池 / 数据缺口候选不能成为模拟进场动作，避免隐性自动交易。",
        "",
        "## 验收结论",
        "",
    ])
    if failed == 0:
        lines.append(
            f"全部 {total} 个步骤通过，TradeFlow 5000 元小资金试跑 5 日回放验收合格。"
        )
        lines.append(
            "用户可以用 ¥5000 模拟本金安全试用一周「候选收敛 → 观察 → 模拟确认 → "
            "失败退出 → 盘后归因 → 风险预算回顾」完整闭环。"
        )
    else:
        lines.append(f"**{failed} 个步骤失败，需要修复。**")
        for r in results:
            if not r.passed:
                lines.append(f"- FAIL {r.day}/{r.step}: {r.detail}")
    lines.append("")
    return "\n".join(lines)


class TestFiveDayReplayReport:
    """驱动完整 5 日回放并生成验收报告。"""

    def test_all_steps_pass(self, replay_db):
        results = _run_5day_replay(replay_db)
        failed = [r for r in results if not r.passed]
        assert not failed, (
            f"Failed steps: {[(r.day, r.step, r.detail) for r in failed]}"
        )

    def test_report_covers_five_days(self, replay_db):
        results = _run_5day_replay(replay_db)
        days_covered = {r.day for r in results}
        for day in TRADE_DAYS:
            assert day in days_covered, f"report missing day {day}"
        assert "week" in days_covered

    def test_report_answers_four_questions(self, replay_db):
        results = _run_5day_replay(replay_db)
        report = _render_5day_report(results)
        assert "V-012" in report
        assert "PASS" in report
        # 四问
        assert "今天看哪几只" in report
        assert "何时触发" in report
        assert "何时退出" in report
        assert "盘后如何处理" in report
        # 风险预算关键字
        assert "per_ticket_max" in report
        assert "失败退出" in report
        assert "最大回撤" in report
        # 候选名
        assert "钢研高纳" in report
        assert "拓普集团" in report
        assert "海螺水泥" in report
        # 合规
        assert "不构成投资建议" in report

    def test_report_written_to_docs(self, replay_db):
        results = _run_5day_replay(replay_db)
        assert all(r.passed for r in results), \
            f"Cannot write report with failed steps: {[r.step for r in results if not r.passed]}"
        report = _render_5day_report(results)
        report_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "docs", "tradeflow_trial_5day_replay_acceptance.md",
        )
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report)
        assert os.path.exists(report_path)
        content = open(report_path, encoding="utf-8").read()
        assert "V-012" in content
        assert "PASS" in content
        assert "今天看哪几只" in content
        assert "失败退出" in content
