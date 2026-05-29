# [S-004] candidate_game_balance — tests
"""Tests for game balance assessment module.

Covers:
- game_balance verdict: favorable / neutral / crowded / fragile
- bull_case / bear_case / policy_case / fund_flow_case text generation
- resonance_count across policy / narrative / tech / fund categories
- evidence refs (game_balance_refs) trace back to existing signals
- No strong buy/sell words in output
- Integration with Candidate / evaluate_symbol / plan_runner
- DB persistence round-trip for game balance fields
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from tradingagents.tradeflow.game_balance import (
    assess_game_balance,
    GameBalanceResult,
    GAME_BALANCE_FAVORABLE,
    GAME_BALANCE_NEUTRAL,
    GAME_BALANCE_CROWDED,
    GAME_BALANCE_FRAGILE,
    ALL_GAME_BALANCES,
    _STRONG_BUY_SELL_WORDS,
)
from tradingagents.tradeflow.schemas import (
    Candidate,
    CandidateSignal,
    DailyPlan,
    STRATEGY_VCP,
    STRATEGY_PULLBACK,
    STRATEGY_EVENT,
    STRATEGY_POLICY_VERSION,
    STRATEGY_NARRATIVE,
)
from tradingagents.tradeflow.candidate_engine import init_db, save_candidate


# ═══════════════════════════════════════════════════════════════
# Unit tests: assess_game_balance()
# ═══════════════════════════════════════════════════════════════

class TestGameBalanceBasic:
    def test_empty_inputs_returns_neutral(self):
        r = assess_game_balance()
        assert r.game_balance == GAME_BALANCE_NEUTRAL
        assert r.resonance_count == 0
        assert r.bull_case == "缺乏明确看多信号"

    def test_no_signals_no_risk(self):
        r = assess_game_balance(strategy_tags=[], score=0.0)
        assert r.game_balance == GAME_BALANCE_NEUTRAL
        assert r.resonance_count == 0

    def test_all_game_balances_valid(self):
        assert ALL_GAME_BALANCES == {"favorable", "neutral", "crowded", "fragile"}


class TestResonanceCount:
    def test_policy_only(self):
        r = assess_game_balance(policy_tags=["低空经济"], version_score=10)
        assert r.resonance_count == 1
        assert r.policy_case != "无明确政策版本信号"

    def test_narrative_only(self):
        r = assess_game_balance(narrative_score=25)
        assert r.resonance_count == 1

    def test_tech_only_vcp(self):
        r = assess_game_balance(strategy_tags=["VCP"])
        assert r.resonance_count == 1

    def test_tech_only_pullback(self):
        r = assess_game_balance(strategy_tags=["PULLBACK_SUPPORT"])
        assert r.resonance_count == 1

    def test_fund_flow_positive(self):
        r = assess_game_balance(event_texts=["主力净流入持续加大"])
        assert r.resonance_count == 1

    def test_fund_flow_hot_money_tag(self):
        r = assess_game_balance(strategy_tags=["HOT_MONEY_LHB"])
        assert r.resonance_count == 1

    def test_two_categories_resonate(self):
        r = assess_game_balance(
            strategy_tags=["VCP"],
            policy_tags=["算力"],
            version_score=10,
        )
        assert r.resonance_count == 2

    def test_three_categories_resonate(self):
        r = assess_game_balance(
            strategy_tags=["VCP"],
            policy_tags=["低空经济"],
            version_score=10,
            narrative_score=20,
        )
        assert r.resonance_count == 3

    def test_four_categories_resonate(self):
        r = assess_game_balance(
            strategy_tags=["VCP", "HOT_MONEY_LHB"],
            policy_tags=["人工智能"],
            version_score=12,
            narrative_score=15,
            event_texts=["主力净流入持续加大"],
        )
        assert r.resonance_count == 4


class TestFavorableBalance:
    def test_policy_event_vcp_resonance_favorable(self):
        r = assess_game_balance(
            strategy_tags=["VCP", "EVENT_CATALYST"],
            score=55,
            policy_tags=["低空经济"],
            version_score=10,
            narrative_score=15,
        )
        assert r.game_balance in {GAME_BALANCE_FAVORABLE, GAME_BALANCE_NEUTRAL}

    def test_three_resonance_high_score_favorable(self):
        r = assess_game_balance(
            strategy_tags=["VCP"],
            score=60,
            policy_tags=["算力"],
            version_score=15,
            narrative_score=25,
        )
        assert r.game_balance == GAME_BALANCE_FAVORABLE

    def test_two_resonance_moderate_risk_neutral(self):
        r = assess_game_balance(
            strategy_tags=["VCP"],
            score=40,
            policy_tags=["半导体"],
            version_score=10,
            risk_flags=["LOCKUP_RISK"],
            risk_penalty=-10,
        )
        assert r.game_balance == GAME_BALANCE_NEUTRAL


class TestFragileBalance:
    def test_inquiry_risk_fragile(self):
        r = assess_game_balance(
            strategy_tags=["VCP"],
            score=50,
            risk_flags=["INQUIRY_RISK"],
            risk_penalty=-10,
        )
        assert r.game_balance == GAME_BALANCE_FRAGILE

    def test_financial_quality_risk_fragile(self):
        r = assess_game_balance(
            risk_flags=["FINANCIAL_QUALITY_RISK"],
            risk_penalty=-7,
        )
        assert r.game_balance == GAME_BALANCE_FRAGILE

    def test_many_risks_fragile(self):
        r = assess_game_balance(
            risk_flags=["LOCKUP_RISK", "REDUCE_HOLDING_RISK", "MARGIN_CROWDING_RISK"],
            risk_penalty=-25,
        )
        assert r.game_balance == GAME_BALANCE_FRAGILE

    def test_heavy_penalty_fragile(self):
        r = assess_game_balance(
            risk_flags=["LOCKUP_RISK", "REDUCE_HOLDING_RISK"],
            risk_penalty=-18,
        )
        assert r.game_balance == GAME_BALANCE_FRAGILE

    def test_favorable_downgraded_by_risks(self):
        r = assess_game_balance(
            strategy_tags=["VCP"],
            score=60,
            policy_tags=["算力"],
            version_score=15,
            risk_flags=["LOCKUP_RISK", "LHB_OVERHEAT_RISK"],
            risk_penalty=-16,
        )
        assert r.game_balance != GAME_BALANCE_FAVORABLE


class TestCrowdedBalance:
    def test_fund_conflict_with_risks_crowded(self):
        r = assess_game_balance(
            event_texts=["主力净流入持续加大", "主力净流出趋势"],
            risk_flags=["LHB_OVERHEAT_RISK", "MARGIN_CROWDING_RISK"],
            risk_penalty=-14,
        )
        assert r.game_balance == GAME_BALANCE_CROWDED


class TestBullCase:
    def test_bull_case_with_policy(self):
        r = assess_game_balance(policy_tags=["低空经济"], version_score=10)
        assert "低空经济" in r.bull_case
        assert "政策方向支持" in r.bull_case

    def test_bull_case_with_tech(self):
        r = assess_game_balance(strategy_tags=["VCP", "PULLBACK_SUPPORT"])
        assert "VCP" in r.bull_case
        assert "回踩支撑" in r.bull_case

    def test_bull_case_with_narrative(self):
        r = assess_game_balance(narrative_score=25)
        assert "叙事质量" in r.bull_case

    def test_bull_case_with_fund_positive(self):
        r = assess_game_balance(event_texts=["主力净流入持续加大"])
        assert "资金流入" in r.bull_case


class TestBearCase:
    def test_bear_case_with_risks(self):
        r = assess_game_balance(
            risk_flags=["LOCKUP_RISK", "REDUCE_HOLDING_RISK"],
            risk_reasons=["解禁风险(-10)", "减持风险(-10)"],
        )
        assert "LOCKUP_RISK" in r.bear_case
        assert "REDUCE_HOLDING_RISK" in r.bear_case

    def test_bear_case_heavy_penalty(self):
        r = assess_game_balance(risk_penalty=-18)
        assert "罚分" in r.bear_case

    def test_bear_case_fund_negative(self):
        r = assess_game_balance(event_texts=["主力净流出趋势"])
        assert "资金流出" in r.bear_case

    def test_bear_case_no_risk_no_bull(self):
        r = assess_game_balance()
        assert "多空信号均不明显" in r.bear_case

    def test_bear_case_no_risk_has_bull(self):
        r = assess_game_balance(policy_tags=["低空经济"], version_score=10)
        assert "无明显看空信号" in r.bear_case


class TestPolicyCase:
    def test_policy_case_with_tags(self):
        r = assess_game_balance(policy_tags=["低空经济", "算力"], version_score=15)
        assert "低空经济" in r.policy_case
        assert "+15" in r.policy_case

    def test_policy_case_without_tags(self):
        r = assess_game_balance()
        assert "无明确政策版本信号" in r.policy_case

    def test_policy_case_with_evidence_refs(self):
        r = assess_game_balance(
            policy_tags=["算力"],
            version_score=10,
            policy_evidence_refs=[{"matched_text": "算力基础设施加速建设", "tag": "算力"}],
        )
        assert any(ref["field"] == "policy_case" for ref in r.game_balance_refs)


class TestFundFlowCase:
    def test_fund_positive(self):
        r = assess_game_balance(event_texts=["主力净流入持续加大"])
        assert "资金面偏正面" in r.fund_flow_case

    def test_fund_negative(self):
        r = assess_game_balance(event_texts=["主力净流出趋势"])
        assert "资金面偏负面" in r.fund_flow_case

    def test_fund_both(self):
        r = assess_game_balance(event_texts=["主力净流入持续加大", "主力净流出趋势"])
        assert "偏正面" in r.fund_flow_case
        assert "偏负面" in r.fund_flow_case

    def test_fund_hot_money(self):
        r = assess_game_balance(strategy_tags=["HOT_MONEY_LHB"])
        assert "龙虎榜" in r.fund_flow_case

    def test_fund_no_signal(self):
        r = assess_game_balance()
        assert "无明确资金流信号" in r.fund_flow_case


class TestNoStrongBuySellWords:
    @pytest.mark.parametrize("word", list(_STRONG_BUY_SELL_WORDS))
    def test_bull_case_no_forbidden_words(self, word):
        r = assess_game_balance(
            strategy_tags=["VCP"],
            policy_tags=["低空经济"],
            version_score=15,
            narrative_score=20,
            event_texts=["主力净流入持续加大"],
        )
        assert word not in r.bull_case
        assert word not in r.bear_case
        assert word not in r.policy_case
        assert word not in r.fund_flow_case

    def test_sanitized_if_input_contains_forbidden(self):
        r = assess_game_balance(
            risk_flags=["LOCKUP_RISK"],
            risk_reasons=["立即买入风险(-10)"],
        )
        assert "立即买入" not in r.bear_case


class TestEvidenceRefs:
    def test_refs_trace_policy_evidence(self):
        r = assess_game_balance(
            policy_tags=["算力"],
            version_score=10,
            policy_evidence_refs=[{"matched_text": "算力基础设施政策", "tag": "算力"}],
        )
        policy_refs = [ref for ref in r.game_balance_refs if ref["field"] == "policy_case"]
        assert len(policy_refs) > 0

    def test_refs_trace_narrative(self):
        r = assess_game_balance(
            narrative_score=20,
            narrative_reasons=["产业落地(+)"],
        )
        bull_refs = [ref for ref in r.game_balance_refs if ref["field"] == "bull_case"]
        assert len(bull_refs) > 0

    def test_refs_trace_risks(self):
        r = assess_game_balance(
            risk_flags=["LOCKUP_RISK"],
            risk_reasons=["解禁风险(-10)"],
        )
        bear_refs = [ref for ref in r.game_balance_refs if ref["field"] == "bear_case"]
        assert len(bear_refs) > 0

    def test_refs_trace_fund_flow(self):
        r = assess_game_balance(event_texts=["主力净流入持续加大"])
        fund_refs = [ref for ref in r.game_balance_refs if ref["field"] == "fund_flow_case"]
        assert len(fund_refs) > 0


# ═══════════════════════════════════════════════════════════════
# Integration tests: Candidate + schemas + DB
# ═══════════════════════════════════════════════════════════════

class TestCandidateGameBalanceFields:
    def test_candidate_default_fields(self):
        c = Candidate(symbol="000001.SZ")
        assert c.game_balance == ""
        assert c.bull_case == ""
        assert c.bear_case == ""
        assert c.policy_case == ""
        assert c.fund_flow_case == ""
        assert c.resonance_count == 0
        assert c.game_balance_refs == []

    def test_candidate_to_db_row_has_game_balance(self):
        c = Candidate(
            symbol="000001.SZ",
            game_balance="favorable",
            bull_case="政策方向支持",
            bear_case="无明显看空信号",
            policy_case="政策版本: 低空经济",
            fund_flow_case="资金面偏正面",
            resonance_count=3,
            game_balance_refs=[{"field": "bull_case", "matched_text": "test"}],
        )
        row = c.to_db_row()
        assert row["game_balance"] == "favorable"
        assert row["bull_case"] == "政策方向支持"
        assert row["resonance_count"] == 3

    def test_candidate_from_db_row_game_balance(self):
        row = {
            "symbol": "000001.SZ",
            "name": "平安银行",
            "source": "manual",
            "strategy_tags_json": "[]",
            "primary_strategy": "",
            "score": 50.0,
            "status": "active",
            "trigger_price": None,
            "support_price": None,
            "invalid_price": None,
            "need_deep_ta": 0,
            "evidence_json": "{}",
            "risk_flags_json": "[]",
            "trade_date": "2026-05-29",
            "created_at": "",
            "updated_at": "",
            "policy_tags_json": "[]",
            "version_score": 0.0,
            "policy_evidence_refs_json": "[]",
            "narrative_score": 0.0,
            "narrative_reasons_json": "[]",
            "narrative_evidence_refs_json": "[]",
            "risk_penalty": 0.0,
            "risk_evidence_refs_json": "[]",
            "risk_reasons_json": "[]",
            "game_balance": "favorable",
            "bull_case": "政策方向支持",
            "bear_case": "无明显看空信号",
            "policy_case": "政策版本: 低空经济",
            "fund_flow_case": "无明确资金流信号",
            "resonance_count": 2,
            "game_balance_refs_json": '[{"field":"test"}]',
        }
        c = Candidate.from_db_row(row)
        assert c.game_balance == "favorable"
        assert c.bull_case == "政策方向支持"
        assert c.resonance_count == 2
        assert len(c.game_balance_refs) == 1


class TestDBPersistence:
    def test_init_db_has_game_balance_columns(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            init_db(db_path)
            import sqlite3
            conn = sqlite3.connect(db_path)
            cursor = conn.execute("PRAGMA table_info(tradeflow_candidates)")
            col_names = [row[1] for row in cursor.fetchall()]
            conn.close()
            assert "game_balance" in col_names
            assert "bull_case" in col_names
            assert "bear_case" in col_names
            assert "policy_case" in col_names
            assert "fund_flow_case" in col_names
            assert "resonance_count" in col_names
            assert "game_balance_refs_json" in col_names
        finally:
            os.unlink(db_path)

    def test_save_and_load_game_balance(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            init_db(db_path)
            c = Candidate(
                symbol="600519.SH",
                name="贵州茅台",
                trade_date="2026-05-29",
                game_balance="favorable",
                bull_case="政策方向支持(低空经济)",
                bear_case="无明显看空信号",
                policy_case="政策版本: 低空经济 (+10分)",
                fund_flow_case="无明确资金流信号",
                resonance_count=2,
                game_balance_refs=[{"field": "bull_case", "matched_text": "test"}],
            )
            save_candidate(c, db_path)

            import sqlite3
            conn = sqlite3.connect(db_path)
            row = conn.execute(
                "SELECT * FROM tradeflow_candidates WHERE symbol=?",
                ("600519.SH",),
            ).fetchone()
            assert row is not None
            cols = [desc[0] for desc in conn.execute("SELECT * FROM tradeflow_candidates WHERE symbol=?", ("600519.SH",)).description]
            conn.close()
            assert "game_balance" in cols
        finally:
            os.unlink(db_path)

    def test_save_and_roundtrip_game_balance(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            init_db(db_path)
            refs = [{"field": "policy_case", "matched_text": "低空经济政策支持", "source": "policy_evidence"}]
            c = Candidate(
                symbol="002138.SZ",
                trade_date="2026-05-29",
                game_balance="neutral",
                bull_case="叙事质量+20",
                bear_case="无明显看空信号",
                policy_case="无明确政策版本信号",
                fund_flow_case="无明确资金流信号",
                resonance_count=1,
                game_balance_refs=refs,
            )
            save_candidate(c, db_path)

            import sqlite3
            conn = sqlite3.connect(db_path)
            cursor = conn.execute(
                "SELECT game_balance, bull_case, bear_case, resonance_count, "
                "game_balance_refs_json FROM tradeflow_candidates WHERE symbol=?",
                ("002138.SZ",),
            )
            row = cursor.fetchone()
            conn.close()

            assert row is not None
            assert row[0] == "neutral"
            assert row[1] == "叙事质量+20"
            assert row[2] == "无明显看空信号"
            assert row[3] == 1
            loaded_refs = json.loads(row[4])
            assert len(loaded_refs) == 1
            assert loaded_refs[0]["field"] == "policy_case"
        finally:
            os.unlink(db_path)


class TestDailyPlanRender:
    def test_render_includes_game_balance(self):
        plan = DailyPlan(
            trade_date="2026-05-29",
            candidates=[{
                "symbol": "600519.SH",
                "name": "贵州茅台",
                "action": "OBSERVE",
                "strategies": ["VCP"],
                "score": 50,
                "need_deep_ta": False,
                "risk_flags": [],
                "game_balance": "favorable",
                "bull_case": "政策方向支持(低空经济)",
                "bear_case": "无明显看空信号",
                "policy_case": "政策版本: 低空经济 (+10分)",
                "fund_flow_case": "无明确资金流信号",
                "narrative_score": 0,
                "risk_penalty": 0,
            }],
        )
        text = plan.render_text()
        assert "博弈平衡" in text
        assert "favorable" in text
        assert "多头视角" in text
        assert "空头视角" in text
        assert "政策/监管" in text
        assert "资金结构" in text

    def test_render_no_game_balance(self):
        plan = DailyPlan(
            trade_date="2026-05-29",
            candidates=[{
                "symbol": "600519.SH",
                "name": "贵州茅台",
                "action": "OBSERVE",
                "strategies": ["VCP"],
                "score": 50,
            }],
        )
        text = plan.render_text()
        assert "博弈平衡" not in text

    def test_render_fragile_emoji(self):
        plan = DailyPlan(
            trade_date="2026-05-29",
            candidates=[{
                "symbol": "000001.SZ",
                "name": "平安银行",
                "action": "OBSERVE",
                "risk_flags": ["INQUIRY_RISK"],
                "risk_penalty": -10,
                "game_balance": "fragile",
                "bull_case": "",
                "bear_case": "风险标签: INQUIRY_RISK",
            }],
        )
        text = plan.render_text()
        assert "🔴" in text
        assert "fragile" in text


# ═══════════════════════════════════════════════════════════════
# Integration with evaluate_symbol (mocked)
# ═══════════════════════════════════════════════════════════════

class TestEvaluateSymbolIntegration:
    def test_game_balance_set_on_candidate(self):
        from tradingagents.tradeflow.candidate_engine import evaluate_symbol
        from unittest.mock import patch
        import pandas as pd
        import numpy as np

        dates = pd.date_range("2026-01-01", periods=100, freq="D")
        df = pd.DataFrame({
            "Date": dates,
            "Open": np.random.uniform(10, 12, 100),
            "High": np.random.uniform(12, 14, 100),
            "Low": np.random.uniform(8, 10, 100),
            "Close": np.linspace(10, 15, 100),
            "Volume": np.random.uniform(1e7, 1e8, 100),
        })

        with patch("tradingagents.tradeflow.candidate_engine._fetch_price_data", return_value=df):
            c, reason = evaluate_symbol(
                symbol="000001.SZ",
                name="测试股票",
                news_texts=["低空经济政策支持，产业加速落地"],
                event_overrides=[{"title": "低空经济政策支持"}],
            )
        if c is not None:
            assert c.game_balance in ALL_GAME_BALANCES or c.game_balance == ""
            assert isinstance(c.resonance_count, int)
            assert isinstance(c.bull_case, str)
            assert isinstance(c.bear_case, str)

    def test_game_balance_in_evidence(self):
        from tradingagents.tradeflow.candidate_engine import evaluate_symbol
        from unittest.mock import patch
        import pandas as pd
        import numpy as np

        dates = pd.date_range("2026-01-01", periods=100, freq="D")
        df = pd.DataFrame({
            "Date": dates,
            "Open": np.random.uniform(10, 12, 100),
            "High": np.random.uniform(12, 14, 100),
            "Low": np.random.uniform(8, 10, 100),
            "Close": np.linspace(10, 15, 100),
            "Volume": np.random.uniform(1e7, 1e8, 100),
        })

        with patch("tradingagents.tradeflow.candidate_engine._fetch_price_data", return_value=df):
            c, reason = evaluate_symbol(
                symbol="000001.SZ",
                name="测试股票",
                news_texts=["低空经济政策支持"],
            )
        if c is not None:
            assert "game_balance" in c.evidence
            gb = c.evidence["game_balance"]
            assert "game_balance" in gb
            assert "bull_case" in gb
            assert "bear_case" in gb


class TestPlanRunnerIntegration:
    def test_build_plan_entry_includes_game_balance(self):
        from tradingagents.tradeflow.plan_runner import _build_plan_entry

        c = Candidate(
            symbol="600519.SH",
            name="贵州茅台",
            game_balance="favorable",
            bull_case="政策方向支持(低空经济)",
            bear_case="无明显看空信号",
            policy_case="政策版本: 低空经济 (+10分)",
            fund_flow_case="无明确资金流信号",
            resonance_count=3,
            game_balance_refs=[{"field": "bull_case", "matched_text": "test"}],
        )
        entry = _build_plan_entry(c)
        assert entry["game_balance"] == "favorable"
        assert entry["bull_case"] == "政策方向支持(低空经济)"
        assert entry["bear_case"] == "无明显看空信号"
        assert entry["policy_case"] == "政策版本: 低空经济 (+10分)"
        assert entry["fund_flow_case"] == "无明确资金流信号"
        assert entry["resonance_count"] == 3


class TestResonanceBoostsDeepTA:
    def test_two_resonance_no_risk_boosts_deep_ta(self):
        from tradingagents.tradeflow.candidate_engine import evaluate_symbol
        from unittest.mock import patch
        import pandas as pd
        import numpy as np

        dates = pd.date_range("2026-01-01", periods=100, freq="D")
        df = pd.DataFrame({
            "Date": dates,
            "Open": np.random.uniform(10, 12, 100),
            "High": np.random.uniform(12, 14, 100),
            "Low": np.random.uniform(8, 10, 100),
            "Close": np.linspace(10, 15, 100),
            "Volume": np.random.uniform(1e7, 1e8, 100),
        })

        with patch("tradingagents.tradeflow.candidate_engine._fetch_price_data", return_value=df):
            c, reason = evaluate_symbol(
                symbol="000001.SZ",
                name="测试",
                news_texts=["低空经济政策支持，算力基础设施加速建设"],
            )
        if c is not None and c.resonance_count >= 2 and c.game_balance in {"favorable", "neutral"}:
            assert c.need_deep_ta is True

    def test_fragile_demotes_deep_ta(self):
        from tradingagents.tradeflow.candidate_engine import evaluate_symbol
        from unittest.mock import patch
        import pandas as pd
        import numpy as np

        dates = pd.date_range("2026-01-01", periods=100, freq="D")
        df = pd.DataFrame({
            "Date": dates,
            "Open": np.random.uniform(10, 12, 100),
            "High": np.random.uniform(12, 14, 100),
            "Low": np.random.uniform(8, 10, 100),
            "Close": np.linspace(10, 15, 100),
            "Volume": np.random.uniform(1e7, 1e8, 100),
        })

        with patch("tradingagents.tradeflow.candidate_engine._fetch_price_data", return_value=df):
            c, reason = evaluate_symbol(
                symbol="000001.SZ",
                name="测试",
                news_texts=[
                    "问询函来了",
                    "减持计划公告",
                    "低空经济政策支持",
                ],
            )
        if c is not None and c.game_balance == "fragile" and len(c.risk_flags) >= 2:
            assert c.need_deep_ta is False
