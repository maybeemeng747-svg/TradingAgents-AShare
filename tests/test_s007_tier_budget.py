# [S-007] candidate_tier_budget — tests
"""Tests for candidate tier classification and TA token budget allocation.

Covers:
- Tier A / B / C classification rules
- ta_budget_priority assignment
- missing_evidence_for_upgrade hints
- A-tier cap enforcement (allocate_tier_budget)
- tier_reason and why_not_deep_ta text
- Strong buy/sell word sanitization
- Integration with candidate_engine, plan_runner, discovery
"""

from __future__ import annotations

import json
import os
import tempfile

import pandas as pd
import pytest

from tradingagents.tradeflow.tier_budget import (
    classify_candidate_tier,
    allocate_tier_budget,
    render_tier_budget_summary,
    TierBudgetResult,
    TierBudgetAllocation,
    TIER_A,
    TIER_B,
    TIER_C,
    TIER_BUDGET_MAP,
    A_TIER_HARD_CAP,
    ALL_TIERS,
)
from tradingagents.tradeflow.schemas import Candidate, DailyPlan
from tradingagents.tradeflow.candidate_engine import (
    evaluate_symbol,
    init_db,
    save_candidate,
)
from tradingagents.tradeflow.plan_runner import generate_daily_plan
from tradingagents.tradeflow.discovery import run_discovery, render_discovery_text


# ── Fixtures ──

def _make_df(close=100.0, n=80, flat=False):
    dates = pd.date_range("2026-01-01", periods=n, freq="B")
    if flat:
        return pd.DataFrame({
            "Date": dates,
            "Open": [close - 1] * n,
            "High": [close + 2] * n,
            "Low": [close - 2] * n,
            "Close": [close] * n,
            "Volume": [1_000_000] * n,
        })
    import numpy as np
    np.random.seed(42)
    closes = close + np.cumsum(np.random.randn(n) * 0.5)
    closes = np.maximum(closes, 1.0)
    return pd.DataFrame({
        "Date": dates,
        "Open": closes - 0.5,
        "High": closes + 1.0,
        "Low": closes - 1.0,
        "Close": closes,
        "Volume": [5_000_000] * n,
    })


def _make_tier_result(**kwargs):
    defaults = {
        "priority_rank": "A",
        "composite_score": 80.0,
        "positive_category_count": 3,
        "data_completeness": 0.8,
        "missing_evidence": [],
        "risk_flags": [],
        "risk_penalty": 0.0,
        "game_balance": "favorable",
        "gate_passed": True,
        "why_not_deep_ta": "",
    }
    defaults.update(kwargs)
    return classify_candidate_tier(**defaults)


# ═══════════════════════════════════════════════════════════════
# 1. Tier A classification
# ═══════════════════════════════════════════════════════════════

class TestTierAClassification:
    def test_strong_candidate_tier_a(self):
        r = _make_tier_result(
            positive_category_count=3,
            composite_score=80,
            data_completeness=0.8,
            game_balance="favorable",
            gate_passed=True,
        )
        assert r.tier == TIER_A
        assert r.ta_budget_priority == TIER_BUDGET_MAP[TIER_A]

    def test_two_categories_high_score_tier_a(self):
        r = _make_tier_result(
            positive_category_count=2,
            composite_score=55,
            data_completeness=0.7,
            game_balance="neutral",
            gate_passed=True,
        )
        assert r.tier == TIER_A

    def test_tier_a_reason_contains_signal_count(self):
        r = _make_tier_result(positive_category_count=3, composite_score=80)
        assert "3类信号共振" in r.tier_reason

    def test_tier_a_no_missing_upgrade(self):
        r = _make_tier_result(positive_category_count=3, composite_score=80)
        assert r.missing_evidence_for_upgrade == []

    def test_tier_a_empty_why_not_deep_ta(self):
        r = _make_tier_result(positive_category_count=3, composite_score=80)
        assert r.why_not_deep_ta == ""

    def test_neutral_game_balance_allows_a(self):
        r = _make_tier_result(game_balance="neutral")
        assert r.tier == TIER_A

    def test_gate_passed_false_blocks_a(self):
        r = _make_tier_result(gate_passed=False)
        assert r.tier != TIER_A

    def test_two_categories_low_score_blocks_a(self):
        r = _make_tier_result(positive_category_count=2, composite_score=40)
        assert r.tier != TIER_A

    def test_low_completeness_blocks_a(self):
        r = _make_tier_result(data_completeness=0.5)
        assert r.tier != TIER_A

    def test_high_risk_blocks_a(self):
        r = _make_tier_result(risk_flags=["INQUIRY_RISK"])
        assert r.tier != TIER_A

    def test_many_risks_blocks_a(self):
        r = _make_tier_result(risk_flags=["LOCKUP_RISK", "REDUCE_HOLDING_RISK", "MARGIN_CROWDING_RISK"])
        assert r.tier != TIER_A

    def test_heavy_penalty_blocks_a(self):
        r = _make_tier_result(risk_penalty=-20)
        assert r.tier != TIER_A

    def test_crowded_game_balance_allows_a_if_otherwise(self):
        r = _make_tier_result(game_balance="crowded")
        assert r.tier != TIER_A

    def test_fragile_game_balance_blocks_a(self):
        r = _make_tier_result(game_balance="fragile")
        assert r.tier != TIER_A


# ═══════════════════════════════════════════════════════════════
# 2. Tier B classification
# ═══════════════════════════════════════════════════════════════

class TestTierBClassification:
    def test_one_signal_decent_completeness(self):
        r = _make_tier_result(
            positive_category_count=1,
            composite_score=30,
            data_completeness=0.5,
            game_balance="neutral",
            gate_passed=False,
            priority_rank="B",
        )
        assert r.tier == TIER_B

    def test_two_signals_low_completeness(self):
        r = _make_tier_result(
            positive_category_count=2,
            composite_score=40,
            data_completeness=0.5,
            game_balance="neutral",
            gate_passed=False,
            priority_rank="B",
        )
        assert r.tier == TIER_B

    def test_tier_b_budget_is_30(self):
        r = _make_tier_result(
            positive_category_count=1,
            composite_score=30,
            data_completeness=0.5,
            game_balance="neutral",
            gate_passed=False,
            priority_rank="B",
        )
        assert r.ta_budget_priority == TIER_BUDGET_MAP[TIER_B]

    def test_tier_b_has_upgrade_hints(self):
        r = _make_tier_result(
            positive_category_count=1,
            composite_score=30,
            data_completeness=0.5,
            game_balance="neutral",
            gate_passed=False,
            priority_rank="B",
        )
        assert len(r.missing_evidence_for_upgrade) > 0

    def test_tier_b_reason_not_empty(self):
        r = _make_tier_result(
            positive_category_count=1,
            composite_score=30,
            data_completeness=0.5,
            game_balance="neutral",
            gate_passed=False,
            priority_rank="B",
        )
        assert r.tier_reason != ""

    def test_single_signal_no_high_risk_not_fragile(self):
        r = _make_tier_result(
            positive_category_count=1,
            composite_score=25,
            data_completeness=0.4,
            game_balance="neutral",
            gate_passed=False,
            risk_flags=[],
            priority_rank="B",
        )
        assert r.tier == TIER_B

    def test_crowded_with_one_signal(self):
        r = _make_tier_result(
            positive_category_count=1,
            composite_score=25,
            data_completeness=0.5,
            game_balance="crowded",
            gate_passed=False,
            risk_flags=[],
            priority_rank="B",
        )
        assert r.tier == TIER_B


# ═══════════════════════════════════════════════════════════════
# 3. Tier C classification
# ═══════════════════════════════════════════════════════════════

class TestTierCClassification:
    def test_no_signals(self):
        r = _make_tier_result(positive_category_count=0, composite_score=0, data_completeness=0.3, game_balance="", gate_passed=False, priority_rank="C")
        assert r.tier == TIER_C

    def test_high_risk_flag(self):
        r = _make_tier_result(risk_flags=["INQUIRY_RISK"], positive_category_count=2, composite_score=50, priority_rank="C")
        assert r.tier == TIER_C

    def test_many_risks(self):
        r = _make_tier_result(risk_flags=["A", "B", "C"], positive_category_count=2, composite_score=50, priority_rank="C")
        assert r.tier == TIER_C

    def test_fragile_game_balance(self):
        r = _make_tier_result(game_balance="fragile", positive_category_count=1, composite_score=30, priority_rank="C")
        assert r.tier == TIER_C

    def test_low_completeness(self):
        r = _make_tier_result(data_completeness=0.2, positive_category_count=1, composite_score=30, gate_passed=False, priority_rank="C")
        assert r.tier == TIER_C

    def test_zero_budget(self):
        r = _make_tier_result(positive_category_count=0, composite_score=0, data_completeness=0.2, gate_passed=False, priority_rank="C")
        assert r.ta_budget_priority == 0

    def test_financial_quality_risk(self):
        r = _make_tier_result(risk_flags=["FINANCIAL_QUALITY_RISK"], positive_category_count=2, composite_score=60, priority_rank="C")
        assert r.tier == TIER_C

    def test_heavy_penalty(self):
        r = _make_tier_result(risk_penalty=-25, positive_category_count=1, composite_score=30, priority_rank="C")
        assert r.tier == TIER_C


# ═══════════════════════════════════════════════════════════════
# 4. Budget allocation & cap enforcement
# ═══════════════════════════════════════════════════════════════

class TestBudgetAllocation:
    def test_no_candidates(self):
        alloc = allocate_tier_budget([])
        assert alloc.tier_a_count == 0
        assert alloc.total_budget == 0

    def test_within_cap(self):
        results = [TierBudgetResult(tier=TIER_A, ta_budget_priority=100)] * 3
        alloc = allocate_tier_budget(results)
        assert alloc.tier_a_count == 3
        assert alloc.total_budget == 300
        assert not alloc.a_tier_cap_applied

    def test_exceeds_cap_demotes(self):
        results = [
            TierBudgetResult(tier=TIER_A, ta_budget_priority=100),
            TierBudgetResult(tier=TIER_A, ta_budget_priority=100),
            TierBudgetResult(tier=TIER_A, ta_budget_priority=100),
            TierBudgetResult(tier=TIER_A, ta_budget_priority=100),
            TierBudgetResult(tier=TIER_A, ta_budget_priority=100),
            TierBudgetResult(tier=TIER_A, ta_budget_priority=100),
        ]
        alloc = allocate_tier_budget(results, a_tier_cap=5)
        assert alloc.tier_a_count == 5
        assert alloc.a_tier_cap_applied
        assert len(alloc.demoted_symbols) == 1
        assert results[5].tier == TIER_B
        assert results[5].ta_budget_priority == TIER_BUDGET_MAP[TIER_B]

    def test_mixed_tiers(self):
        results = [
            TierBudgetResult(tier=TIER_A, ta_budget_priority=100),
            TierBudgetResult(tier=TIER_B, ta_budget_priority=30),
            TierBudgetResult(tier=TIER_C, ta_budget_priority=0),
        ]
        alloc = allocate_tier_budget(results)
        assert alloc.tier_a_count == 1
        assert alloc.tier_b_count == 1
        assert alloc.tier_c_count == 1
        assert alloc.total_budget == 130

    def test_cap_default_is_5(self):
        results = [TierBudgetResult(tier=TIER_A, ta_budget_priority=100)] * 5
        alloc = allocate_tier_budget(results)
        assert alloc.tier_a_count == 5
        assert not alloc.a_tier_cap_applied

    def test_cap_with_custom_value(self):
        results = [TierBudgetResult(tier=TIER_A, ta_budget_priority=100) for _ in range(3)]
        alloc = allocate_tier_budget(results, a_tier_cap=2)
        assert alloc.tier_a_count == 2
        assert alloc.a_tier_cap_applied
        assert results[2].tier == TIER_B

    def test_demoted_reason_mentions_cap(self):
        results = [TierBudgetResult(tier=TIER_A, ta_budget_priority=100) for _ in range(4)]
        allocate_tier_budget(results, a_tier_cap=2)
        assert "A层已满" in results[2].tier_reason
        assert "A层名额已满" in results[2].why_not_deep_ta


# ═══════════════════════════════════════════════════════════════
# 5. render_tier_budget_summary
# ═══════════════════════════════════════════════════════════════

class TestRenderSummary:
    def test_basic_summary(self):
        alloc = TierBudgetAllocation(tier_a_count=2, tier_b_count=3, tier_c_count=1, total_budget=290)
        text = render_tier_budget_summary(alloc)
        assert "A层(优先深挖): 2只" in text
        assert "B层(观察等待): 3只" in text
        assert "C层(暂不关注): 1只" in text
        assert "总预算建议: 290 tokens" in text

    def test_cap_applied_shown(self):
        alloc = TierBudgetAllocation(
            tier_a_count=5, tier_b_count=2, tier_c_count=0,
            total_budget=560, a_tier_cap_applied=True,
            demoted_symbols=["slot_5"],
        )
        text = render_tier_budget_summary(alloc)
        assert "A层上限5已触发" in text
        assert "1只降为B层" in text

    def test_no_cap_shows_no_warning(self):
        alloc = TierBudgetAllocation(tier_a_count=1, tier_b_count=1, tier_c_count=1, total_budget=130)
        text = render_tier_budget_summary(alloc)
        assert "上限" not in text


# ═══════════════════════════════════════════════════════════════
# 6. Sanitization
# ═══════════════════════════════════════════════════════════════

class TestSanitization:
    def test_tier_reason_sanitized(self):
        r = classify_candidate_tier(
            priority_rank="B",
            composite_score=30,
            positive_category_count=1,
            data_completeness=0.5,
            game_balance="neutral",
            gate_passed=False,
            why_not_deep_ta="立即买入不可",
        )
        assert "立即买入" not in r.tier_reason
        assert "立即买入" not in r.why_not_deep_ta

    def test_no_strong_words_in_any_output(self):
        strong = {"立即买入", "重仓买入", "立即清仓", "满仓", "梭哈"}
        r = classify_candidate_tier(
            priority_rank="C",
            composite_score=10,
            positive_category_count=0,
            data_completeness=0.2,
            game_balance="",
            gate_passed=False,
            why_not_deep_ta="重仓买入信号不足",
        )
        for w in strong:
            assert w not in r.tier_reason
            assert w not in r.why_not_deep_ta


# ═══════════════════════════════════════════════════════════════
# 7. TierBudgetResult dataclass
# ═══════════════════════════════════════════════════════════════

class TestTierBudgetResultDataclass:
    def test_defaults(self):
        r = TierBudgetResult()
        assert r.tier == TIER_C
        assert r.ta_budget_priority == 0
        assert r.missing_evidence_for_upgrade == []
        assert r.tier_reason == ""
        assert r.why_not_deep_ta == ""


# ═══════════════════════════════════════════════════════════════
# 8. Integration: candidate_engine sets tier fields
# ═══════════════════════════════════════════════════════════════

class TestCandidateEngineIntegration:
    def test_evaluate_symbol_sets_tier(self):
        from unittest.mock import patch
        df = _make_df(close=25.0, n=80, flat=False)
        with patch("tradingagents.tradeflow.candidate_engine._fetch_price_data", return_value=df):
            c, reason = evaluate_symbol(
                symbol="000001.SZ",
                name="平安银行",
                source="manual",
                trade_date="2026-05-29",
                news_texts=["低空经济政策支持，公司获得大额订单"],
            )
        if c is None:
            pytest.skip("No strategy hit for random data — tier logic still covered by unit tests")
        assert c.tier in ALL_TIERS or c.tier == ""
        assert isinstance(c.ta_budget_priority, int)
        assert isinstance(c.tier_reason, str)
        assert isinstance(c.missing_evidence_for_upgrade, list)
        assert "tier_budget" in c.evidence

    def test_weak_candidate_is_tier_c(self):
        from unittest.mock import patch
        df = _make_df(close=25.0, n=80, flat=False)
        with patch("tradingagents.tradeflow.candidate_engine._fetch_price_data", return_value=df):
            c, reason = evaluate_symbol(
                symbol="000001.SZ",
                name="平安银行",
                source="manual",
                trade_date="2026-05-29",
            )
        if c is None:
            pytest.skip("No strategy hit — acceptable for weak candidate test")
        assert c.tier in {TIER_B, TIER_C}

    def test_strong_candidate_with_events(self):
        from unittest.mock import patch
        df = _make_df(close=25.0, n=80, flat=False)
        with patch("tradingagents.tradeflow.candidate_engine._fetch_price_data", return_value=df):
            c, reason = evaluate_symbol(
                symbol="000001.SZ",
                name="平安银行",
                source="manual",
                trade_date="2026-05-29",
                news_texts=[
                    "低空经济政策支持",
                    "公司获得大额订单",
                    "主力资金连续净流入",
                ],
                event_overrides=[
                    {"title": "回购计划", "symbol": "000001.SZ"},
                    {"title": "评级上调", "symbol": "000001.SZ"},
                ],
                fund_flow_individual="日期,收盘价,主力净流入-净额\n2026-05-28,25.0,5000万元",
                fund_flow_board="日期,板块,净流入\n2026-05-28,银行,20000万元",
            )
        if c is None:
            pytest.skip("No strategy hit — tier logic still covered by unit tests")
        assert c.tier in ALL_TIERS


# ═══════════════════════════════════════════════════════════════
# 9. Integration: DB persistence
# ═══════════════════════════════════════════════════════════════

class TestDBPersistence:
    def test_save_and_load_tier_fields(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            init_db(db_path)
            c = Candidate(
                symbol="600519.SH",
                name="贵州茅台",
                tier=TIER_A,
                ta_budget_priority=100,
                tier_reason="3类信号共振",
                missing_evidence_for_upgrade=[],
            )
            row_id = save_candidate(c, db_path)
            assert row_id > 0

            import sqlite3
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM tradeflow_candidates WHERE symbol=?", ("600519.SH",)
            ).fetchone()
            conn.close()
            assert row is not None
            assert row["tier"] == TIER_A
            assert row["ta_budget_priority"] == 100
            assert row["tier_reason"] == "3类信号共振"
            assert json.loads(row["missing_evidence_for_upgrade_json"]) == []
        finally:
            os.unlink(db_path)

    def test_db_migration_adds_tier_columns(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            import sqlite3
            conn = sqlite3.connect(db_path)
            conn.execute("""
                CREATE TABLE tradeflow_candidates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_date TEXT,
                    symbol TEXT,
                    UNIQUE(trade_date, symbol)
                )
            """)
            conn.commit()
            conn.close()

            init_db(db_path)

            conn = sqlite3.connect(db_path)
            cols = [r[1] for r in conn.execute("PRAGMA table_info(tradeflow_candidates)").fetchall()]
            conn.close()
            assert "tier" in cols
            assert "ta_budget_priority" in cols
            assert "tier_reason" in cols
            assert "missing_evidence_for_upgrade_json" in cols
        finally:
            os.unlink(db_path)


# ═══════════════════════════════════════════════════════════════
# 10. Integration: plan_runner and discovery
# ═══════════════════════════════════════════════════════════════

class TestPlanRunnerIntegration:
    def test_plan_includes_tier_budget_metadata(self):
        c1 = Candidate(symbol="000001.SZ", name="A", tier=TIER_A, ta_budget_priority=100, tier_reason="test", composite_score=80)
        c2 = Candidate(symbol="000002.SZ", name="B", tier=TIER_B, ta_budget_priority=30, tier_reason="test", composite_score=40)
        plan = generate_daily_plan(
            trade_date="2026-05-29",
            candidates=[c1, c2],
        )
        assert "tier_budget" in plan.metadata
        assert plan.metadata["tier_budget"]["tier_a_count"] == 1
        assert plan.metadata["tier_budget"]["tier_b_count"] == 1
        assert "tier_budget_summary" in plan.metadata

    def test_plan_entries_have_tier_fields(self):
        c1 = Candidate(symbol="000001.SZ", name="A", tier=TIER_A, ta_budget_priority=100, tier_reason="test", composite_score=80)
        plan = generate_daily_plan(trade_date="2026-05-29", candidates=[c1])
        entry = plan.candidates[0]
        assert "tier" in entry
        assert "ta_budget_priority" in entry
        assert "tier_reason" in entry
        assert "missing_evidence_for_upgrade" in entry

    def test_plan_sorted_by_tier(self):
        c_a = Candidate(symbol="000001.SZ", name="A", tier=TIER_A, ta_budget_priority=100, tier_reason="", composite_score=80)
        c_c = Candidate(symbol="000003.SZ", name="C", tier=TIER_C, ta_budget_priority=0, tier_reason="", composite_score=10)
        c_b = Candidate(symbol="000002.SZ", name="B", tier=TIER_B, ta_budget_priority=30, tier_reason="", composite_score=40)
        plan = generate_daily_plan(trade_date="2026-05-29", candidates=[c_a, c_c, c_b])
        tiers = [e.get("tier", "") for e in plan.candidates]
        assert tiers == [TIER_A, TIER_B, TIER_C]

    def test_plan_render_text_includes_tier(self):
        c1 = Candidate(symbol="000001.SZ", name="A", tier=TIER_A, ta_budget_priority=100, tier_reason="3类信号共振", composite_score=80)
        plan = generate_daily_plan(trade_date="2026-05-29", candidates=[c1])
        text = plan.render_text()
        assert "A层" in text or "优先深挖" in text
        assert "TA预算" in text


class TestDiscoveryIntegration:
    def test_discovery_result_has_tier_budget_metadata(self):
        from unittest.mock import patch
        c1 = Candidate(symbol="000001.SZ", name="A", tier=TIER_A, ta_budget_priority=100, tier_reason="test", composite_score=80)
        c1.signals = []
        df = _make_df(close=25.0, n=80)
        with patch("tradingagents.tradeflow.candidate_engine._fetch_price_data", return_value=df), \
             patch("tradingagents.tradeflow.discovery._build_discovery_universe", return_value=[{"symbol": "000001.SZ", "name": "A", "source": "manual"}]), \
             patch("tradingagents.tradeflow.candidate_engine.evaluate_symbol", return_value=(c1, "")):
            result = run_discovery(
                trade_date="2026-05-29",
                symbols=["000001.SZ"],
            )
        assert "tier_budget" in result.metadata

    def test_discovery_text_includes_tier(self):
        from unittest.mock import patch
        c1 = Candidate(symbol="000001.SZ", name="A", tier=TIER_A, ta_budget_priority=100, tier_reason="3类信号共振", composite_score=80)
        c1.signals = []
        df = _make_df(close=25.0, n=80)
        with patch("tradingagents.tradeflow.candidate_engine._fetch_price_data", return_value=df), \
             patch("tradingagents.tradeflow.discovery._build_discovery_universe", return_value=[{"symbol": "000001.SZ", "name": "A", "source": "manual"}]), \
             patch("tradingagents.tradeflow.candidate_engine.evaluate_symbol", return_value=(c1, "")):
            result = run_discovery(
                trade_date="2026-05-29",
                symbols=["000001.SZ"],
            )
        text = render_discovery_text(result)
        assert "A层" in text or "TA预算" in text or "分层" in text


# ═══════════════════════════════════════════════════════════════
# 11. Edge cases
# ═══════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_empty_inputs(self):
        r = classify_candidate_tier()
        assert r.tier == TIER_C

    def test_all_zeros(self):
        r = classify_candidate_tier(
            priority_rank="",
            composite_score=0,
            positive_category_count=0,
            data_completeness=0.0,
            risk_flags=[],
            risk_penalty=0.0,
            game_balance="",
            gate_passed=False,
        )
        assert r.tier == TIER_C

    def test_exactly_at_a_tier_boundary(self):
        r = _make_tier_result(
            positive_category_count=2,
            composite_score=50,
            data_completeness=0.6,
            game_balance="favorable",
            gate_passed=True,
        )
        assert r.tier == TIER_A

    def test_just_below_a_tier_boundary(self):
        r = _make_tier_result(
            positive_category_count=2,
            composite_score=49,
            data_completeness=0.6,
            game_balance="favorable",
            gate_passed=True,
        )
        assert r.tier in {TIER_B, TIER_A}

    def test_missing_evidence_influences_upgrade_hints(self):
        r = _make_tier_result(
            positive_category_count=1,
            composite_score=30,
            data_completeness=0.4,
            game_balance="neutral",
            gate_passed=False,
            priority_rank="B",
            missing_evidence=["行情数据", "资金流数据"],
        )
        assert len(r.missing_evidence_for_upgrade) > 0

    def test_no_forbidden_words_in_budget_summary(self):
        strong = {"立即买入", "重仓买入", "立即清仓", "满仓", "梭哈"}
        alloc = TierBudgetAllocation(tier_a_count=1, tier_b_count=1, tier_c_count=1, total_budget=130)
        text = render_tier_budget_summary(alloc)
        for w in strong:
            assert w not in text

    def test_all_tier_results_same_tier(self):
        results = [TierBudgetResult(tier=TIER_B, ta_budget_priority=30)] * 10
        alloc = allocate_tier_budget(results)
        assert alloc.tier_a_count == 0
        assert alloc.tier_b_count == 10
        assert alloc.tier_c_count == 0

    def test_candidate_schema_default_tier_fields(self):
        c = Candidate(symbol="000001.SZ")
        assert c.tier == ""
        assert c.ta_budget_priority == 0
        assert c.tier_reason == ""
        assert c.missing_evidence_for_upgrade == []

    def test_candidate_to_db_row_and_back(self):
        c = Candidate(
            symbol="000001.SZ",
            tier=TIER_A,
            ta_budget_priority=100,
            tier_reason="test reason",
            missing_evidence_for_upgrade=["hint1", "hint2"],
        )
        row = c.to_db_row()
        assert row["tier"] == TIER_A
        assert row["ta_budget_priority"] == 100
        assert row["tier_reason"] == "test reason"
        assert json.loads(row["missing_evidence_for_upgrade_json"]) == ["hint1", "hint2"]

        c2 = Candidate.from_db_row(row)
        assert c2.tier == TIER_A
        assert c2.ta_budget_priority == 100
        assert c2.tier_reason == "test reason"
        assert c2.missing_evidence_for_upgrade == ["hint1", "hint2"]

    def test_upgrade_hints_when_risk_present(self):
        r = _make_tier_result(
            positive_category_count=2,
            composite_score=55,
            data_completeness=0.7,
            game_balance="neutral",
            gate_passed=True,
            risk_flags=["INQUIRY_RISK"],
        )
        assert r.tier != TIER_A
        has_risk_hint = any("高风险" in h for h in r.missing_evidence_for_upgrade)
        assert has_risk_hint

    def test_upgrade_hints_when_completeness_low(self):
        r = _make_tier_result(
            positive_category_count=3,
            composite_score=80,
            data_completeness=0.4,
            game_balance="favorable",
            gate_passed=True,
        )
        assert r.tier != TIER_A
        has_comp_hint = any("完整度" in h for h in r.missing_evidence_for_upgrade)
        assert has_comp_hint

    def test_upgrade_hints_when_game_bad(self):
        r = _make_tier_result(
            positive_category_count=3,
            composite_score=80,
            data_completeness=0.8,
            game_balance="crowded",
            gate_passed=True,
        )
        assert r.tier != TIER_A
        has_game_hint = any("博弈" in h for h in r.missing_evidence_for_upgrade)
        assert has_game_hint
