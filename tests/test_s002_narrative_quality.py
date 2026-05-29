# [S-002] narrative_quality_score
"""Tests for S-002: narrative quality scoring for TradeFlow candidates."""

import sys
import os
import tempfile
import json

import pytest
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.tradeflow.narrative_quality import (
    score_narrative_quality,
    NarrativeQualityResult,
    MAX_NARRATIVE_SCORE,
)
from tradingagents.tradeflow.schemas import (
    Candidate,
    CandidateSignal,
    STRATEGY_NARRATIVE,
    STRATEGY_POLICY_VERSION,
    DailyPlan,
)
from tradingagents.tradeflow.candidate_engine import evaluate_symbol, init_db, save_candidate


def _make_vcp_df():
    np.random.seed(123)
    n = 120
    dates = pd.date_range("2026-01-01", periods=n, freq="B")
    prices = [20.0]
    for i in range(1, 80):
        prices.append(prices[-1] * (1 + 0.004 + np.random.normal(0, 0.015)))
    base = prices[-1]
    for i in range(40):
        amp = 0.03 * (1 - i / 40)
        prices.append(base + amp * base * np.sin(i * 0.3) + np.random.normal(0, 0.005) * base)
    prices = np.array(prices[:n])
    volumes = np.concatenate([
        np.random.uniform(8e6, 15e6, 80),
        np.linspace(10e6, 3e6, 40),
    ])
    return pd.DataFrame({
        "Date": dates,
        "Open": prices, "High": prices * 1.01, "Low": prices * 0.99,
        "Close": prices, "Volume": volumes,
    })


# ── Unit tests: dimension scoring ──

class TestPolicyBacking:
    def test_state_council(self):
        r = score_narrative_quality(["国务院发布新质生产力发展意见"])
        assert r.narrative_score > 0
        assert any("POLICY_BACKING" in ref.get("dimension", "") for ref in r.narrative_evidence_refs)

    def test_policy_support(self):
        r = score_narrative_quality(["产业政策重点支持半导体发展"])
        assert r.narrative_score > 0

    def test_subsidy(self):
        r = score_narrative_quality(["公司获得财政专项资金补贴"])
        assert r.narrative_score > 0

    def test_plan_document(self):
        r = score_narrative_quality(["十四五规划实施方案发布"])
        assert r.narrative_score > 0

    def test_no_policy_keywords(self):
        r = score_narrative_quality(["公司更换会计师事务所"])
        assert not any("POLICY_BACKING" in ref.get("dimension", "") for ref in r.narrative_evidence_refs)


class TestIndustryLanding:
    def test_winning_bid(self):
        r = score_narrative_quality(["公司中标5亿元智慧城市项目"])
        assert r.narrative_score > 0
        assert any("INDUSTRY_LANDING" in ref.get("dimension", "") for ref in r.narrative_evidence_refs)

    def test_major_order(self):
        r = score_narrative_quality(["公司签署大单合同金额超10亿"])
        assert r.narrative_score > 0

    def test_revenue_growth(self):
        r = score_narrative_quality(["营收同比增长50%，净利润翻倍"])
        assert r.narrative_score > 0

    def test_market_share(self):
        r = score_narrative_quality(["公司市场份额提升至行业第一"])
        assert r.narrative_score > 0

    def test_commercialization(self):
        r = score_narrative_quality(["技术商业化落地，试点城市启动"])
        assert r.narrative_score > 0


class TestCorporateAction:
    def test_buyback_plan(self):
        r = score_narrative_quality(["公司公告回购计划，拟回购不超过2%股份"])
        assert r.narrative_score > 0
        assert any("CORPORATE_ACTION" in ref.get("dimension", "") for ref in r.narrative_evidence_refs)

    def test_major_contract(self):
        r = score_narrative_quality(["公司签署重大合同，合同金额超8亿"])
        assert r.narrative_score > 0

    def test_merger(self):
        r = score_narrative_quality(["公司公告重大资产重组方案"])
        assert r.narrative_score > 0

    def test_earnings_preview_positive(self):
        r = score_narrative_quality(["业绩预告净利润大幅增长"])
        assert r.narrative_score > 0

    def test_earnings_preview_neutral(self):
        r = score_narrative_quality(["业绩预告披露"])
        assert r.narrative_score > 0

    def test_rating_upgrade(self):
        r = score_narrative_quality(["券商评级上调至买入，目标价上调"])
        assert r.narrative_score > 0

    def test_rating_downgrade_negative(self):
        r = score_narrative_quality(["评级下调，目标价下调"])
        assert any(ref.get("base_score", 0) < 0 for ref in r.narrative_evidence_refs)

    def test_insider_buy(self):
        r = score_narrative_quality(["大股东增持公司股份"])
        assert r.narrative_score > 0

    def test_dividend(self):
        r = score_narrative_quality(["公司特别分红方案公布"])
        assert r.narrative_score > 0

    def test_equity_incentive(self):
        r = score_narrative_quality(["公司推出股权激励计划"])
        assert r.narrative_score > 0


class TestPropagationClarity:
    def test_leader_keyword(self):
        r = score_narrative_quality(["公司是行业龙头，核心标杆企业"])
        assert any("PROPAGATION_CLARITY" in ref.get("dimension", "") for ref in r.narrative_evidence_refs)

    def test_first_unique(self):
        r = score_narrative_quality(["公司首家获得独家技术认证"])
        assert any("PROPAGATION_CLARITY" in ref.get("dimension", "") for ref in r.narrative_evidence_refs)

    def test_breakthrough(self):
        r = score_narrative_quality(["技术突破，首创颠覆性产品"])
        assert any("PROPAGATION_CLARITY" in ref.get("dimension", "") for ref in r.narrative_evidence_refs)

    def test_high_certainty(self):
        r = score_narrative_quality(["行业高景气，持续增长确定性高"])
        assert any("PROPAGATION_CLARITY" in ref.get("dimension", "") for ref in r.narrative_evidence_refs)

    def test_noise_reduces_clarity(self):
        r_clear = score_narrative_quality(["公司是行业龙头"])
        r_noisy = score_narrative_quality(["公司是行业龙头，但可能存在不确定性"])
        assert r_clear.narrative_score >= r_noisy.narrative_score


class TestCrowdingDeduction:
    def test_overheating(self):
        r = score_narrative_quality(["概念炒作过热，暴涨风险加大"])
        assert any("CROWDING_DEDUCTION" in ref.get("dimension", "") for ref in r.narrative_evidence_refs)

    def test_speculation(self):
        r = score_narrative_quality(["游资炒作明显，击鼓传花风险"])
        assert any("CROWDING_DEDUCTION" in ref.get("dimension", "") for ref in r.narrative_evidence_refs)

    def test_concept_hype(self):
        r = score_narrative_quality(["纯概念炒作，蹭热点嫌疑"])
        assert any("CROWDING_DEDUCTION" in ref.get("dimension", "") for ref in r.narrative_evidence_refs)

    def test_risk_warning(self):
        r = score_narrative_quality(["公司收到风险提示函，监管关注异常波动"])
        assert any("CROWDING_DEDUCTION" in ref.get("dimension", "") for ref in r.narrative_evidence_refs)

    def test_crowding_reduces_score(self):
        r_positive = score_narrative_quality(["公司中标5亿元项目"])
        r_crowded = score_narrative_quality(["公司中标5亿元项目，概念炒作过热，游资炒作明显"])
        assert r_crowded.narrative_score < r_positive.narrative_score


# ── Edge case tests ──

class TestEdgeCases:
    def test_empty_list(self):
        r = score_narrative_quality([])
        assert r.narrative_score == 0.0
        assert r.narrative_reasons == []
        assert r.narrative_evidence_refs == []

    def test_none_input(self):
        r = score_narrative_quality(None)
        assert r.narrative_score == 0.0

    def test_blank_strings(self):
        r = score_narrative_quality(["", None, "   "])
        assert r.narrative_score == 0.0

    def test_ordinary_news_low_score(self):
        r = score_narrative_quality(["公司更换会计师事务所"])
        assert r.narrative_score <= 5

    def test_ordinary_announcement_low_score(self):
        r = score_narrative_quality(["关于召开2026年第一次临时股东大会的通知"])
        assert r.narrative_score <= 5

    def test_high_quality_event_high_score(self):
        r = score_narrative_quality([
            "国务院发布新质生产力发展意见",
            "公司中标5亿元大单合同",
            "公司公告回购计划",
        ])
        assert r.narrative_score > 15

    def test_score_capped_at_max(self):
        r = score_narrative_quality([
            "国务院发布新质生产力指导意见",
            "工信部重点扶持算力产业",
            "公司签署重大合同超10亿",
            "公司中标5亿项目并已投产",
            "公司公告回购计划",
            "业绩预告净利润大幅增长",
            "券商首次覆盖给予买入评级",
            "公司是行业龙头",
            "核心技术首创突破",
            "行业高景气持续增长",
        ])
        assert r.narrative_score <= MAX_NARRATIVE_SCORE

    def test_score_never_negative(self):
        r = score_narrative_quality([
            "概念炒作过热",
            "游资炒作明显",
            "暴涨后监管关注",
            "风险提示函",
        ])
        assert r.narrative_score >= 0.0

    def test_duplicate_events_dont_stack(self):
        r1 = score_narrative_quality(["公司公告回购计划"])
        r3 = score_narrative_quality(["公司公告回购计划", "公司公告回购方案", "公司公告回购实施"])
        assert r3.narrative_score <= r1.narrative_score * 2.5

    def test_reasons_populated(self):
        r = score_narrative_quality(["国务院发布新质生产力指导意见，公司中标大单"])
        assert len(r.narrative_reasons) > 0

    def test_evidence_refs_populated(self):
        r = score_narrative_quality(["公司中标5亿元项目"])
        assert len(r.narrative_evidence_refs) > 0
        assert all("matched_text" in ref for ref in r.narrative_evidence_refs)


# ── Integration tests ──

class TestNarrativeIntegration:
    def test_narrative_in_candidate(self):
        df = _make_vcp_df()
        c, reason = evaluate_symbol(
            "TEST.SZ",
            name="测试",
            df=df,
            news_texts=["公司公告回购计划，拟回购2%股份"],
        )
        if c is not None:
            assert c.narrative_score > 0
            assert len(c.narrative_reasons) > 0
            assert STRATEGY_NARRATIVE in c.strategy_tags
            assert "narrative_quality" in c.evidence

    def test_narrative_adds_to_score(self):
        df = _make_vcp_df()
        c_plain, _ = evaluate_symbol(
            "TEST.SZ", name="测试", df=df,
            news_texts=["公司日常经营公告"],
        )
        c_good, _ = evaluate_symbol(
            "TEST.SZ", name="测试", df=df,
            news_texts=["公司公告回购计划，中标5亿大单"],
        )
        if c_good is not None and c_plain is not None:
            assert c_good.score > c_plain.score or c_good.narrative_score > 0

    def test_no_narrative_for_ordinary_news(self):
        df = _make_vcp_df()
        c, reason = evaluate_symbol(
            "TEST.SZ", name="测试", df=df,
            news_texts=["公司日常经营公告"],
        )
        if c is not None:
            assert c.narrative_score == 0.0
            assert STRATEGY_NARRATIVE not in c.strategy_tags

    def test_high_narrative_triggers_deep_ta(self):
        df = _make_vcp_df()
        c, _ = evaluate_symbol(
            "TEST.SZ", name="测试", df=df,
            news_texts=[
                "国务院发布新质生产力发展意见",
                "公司中标5亿元大单合同",
                "公司公告回购计划",
                "业绩预告净利润大幅增长",
                "券商评级上调至买入",
            ],
        )
        if c is not None and c.narrative_score >= 20:
            assert c.need_deep_ta is True

    def test_symbol_isolation_narrative(self):
        df = _make_vcp_df()
        c_a, _ = evaluate_symbol(
            "AAA.SZ", name="测试A", df=df,
            news_texts=["公司公告回购计划，中标大单"],
        )
        c_b, _ = evaluate_symbol(
            "BBB.SZ", name="测试B", df=df,
            news_texts=["公司更换会计师事务所"],
        )
        if c_a is not None:
            assert c_a.narrative_score > 0
        if c_b is not None:
            assert c_b.narrative_score == 0.0

    def test_narrative_coexists_with_policy_version(self):
        df = _make_vcp_df()
        c, _ = evaluate_symbol(
            "TEST.SZ", name="测试", df=df,
            news_texts=[
                "国务院发布新质生产力发展意见",
                "公司公告回购计划",
            ],
        )
        if c is not None:
            assert c.version_score > 0
            assert c.narrative_score > 0
            assert STRATEGY_POLICY_VERSION in c.strategy_tags
            assert STRATEGY_NARRATIVE in c.strategy_tags

    def test_narrative_fields_in_db(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            init_db(db_path)
            c = Candidate(
                symbol="TEST.SZ",
                narrative_score=25.0,
                narrative_reasons=["政策背书(+) ", "公司动作: 回购(+8)"],
                narrative_evidence_refs=[{"dimension": "CORPORATE_ACTION", "matched_text": "回购计划", "label": "回购", "base_score": 8}],
                strategy_tags=["VCP", STRATEGY_NARRATIVE],
                score=75.0,
                trade_date="2026-05-29",
                primary_strategy="VCP",
            )
            save_candidate(c, db_path)

            import sqlite3
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM tradeflow_candidates WHERE symbol='TEST.SZ'"
            ).fetchone()
            conn.close()

            assert row is not None
            assert row["narrative_score"] == 25.0
            assert json.loads(row["narrative_reasons_json"]) == ["政策背书(+) ", "公司动作: 回购(+8)"]
        finally:
            os.unlink(db_path)

    def test_narrative_in_db_roundtrip(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            init_db(db_path)
            c = Candidate(
                symbol="RT.SZ",
                narrative_score=15.0,
                narrative_reasons=["产业落地(+)"],
                narrative_evidence_refs=[{"dimension": "INDUSTRY_LANDING", "matched_text": "中标5亿", "weight": 8}],
                strategy_tags=[STRATEGY_NARRATIVE],
                score=65.0,
                trade_date="2026-05-29",
            )
            save_candidate(c, db_path)

            import sqlite3
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM tradeflow_candidates WHERE symbol='RT.SZ'"
            ).fetchone()
            conn.close()

            restored = Candidate.from_db_row(dict(row))
            assert restored.narrative_score == 15.0
            assert restored.narrative_reasons == ["产业落地(+)"]
            assert len(restored.narrative_evidence_refs) == 1
        finally:
            os.unlink(db_path)


class TestNarrativePlanDisplay:
    def test_render_text_shows_narrative(self):
        plan = DailyPlan(
            trade_date="2026-05-29",
            candidates=[{
                "symbol": "TEST.SZ",
                "name": "测试",
                "action": "NEED_DEEP_TA",
                "primary_strategy": "EVENT_CATALYST",
                "strategies": ["EVENT_CATALYST", STRATEGY_NARRATIVE],
                "score": 75.0,
                "trigger_price": 25.0,
                "support_price": None,
                "invalid_price": 22.0,
                "reason": "事件催化",
                "need_deep_ta": True,
                "risk_flags": [],
                "policy_tags": [],
                "version_score": 0.0,
                "policy_evidence_refs": [],
                "narrative_score": 18.0,
                "narrative_reasons": ["政策背书(+) ", "公司动作: 回购(+8)"],
                "narrative_evidence_refs": [],
            }],
        )
        text = plan.render_text()
        assert "叙事质量" in text
        assert "+18" in text
        assert "回购" in text

    def test_render_text_no_narrative(self):
        plan = DailyPlan(
            trade_date="2026-05-29",
            candidates=[{
                "symbol": "TEST.SZ",
                "name": "测试",
                "action": "OBSERVE",
                "primary_strategy": "VCP",
                "strategies": ["VCP"],
                "score": 50.0,
                "reason": "VCP",
                "need_deep_ta": False,
                "risk_flags": [],
                "policy_tags": [],
                "version_score": 0.0,
                "policy_evidence_refs": [],
                "narrative_score": 0.0,
                "narrative_reasons": [],
                "narrative_evidence_refs": [],
            }],
        )
        text = plan.render_text()
        assert "叙事质量" not in text
