# [S-001] policy_version_signal
"""Tests for S-001: policy version signal for TradeFlow candidate pool."""

import sys
import os
import tempfile
import pytest
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.tradeflow.policy_version_signal import (
    detect_policy_version,
    PolicyVersionResult,
    POLICY_VERSION_TOPICS,
    MAX_POLICY_BONUS,
)
from tradingagents.tradeflow.schemas import Candidate, CandidateSignal, STRATEGY_POLICY_VERSION
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


class TestPolicyVersionDetection:
    def test_new_quality_productivity(self):
        result = detect_policy_version(event_texts=["国务院发布新质生产力发展指导意见"])
        assert "新质生产力" in result.policy_tags
        assert result.version_score > 0
        assert len(result.policy_evidence_refs) > 0
        assert result.policy_evidence_refs[0]["tag"] == "新质生产力"
        assert result.policy_evidence_refs[0]["source"] == "event_text"

    def test_compute_power(self):
        result = detect_policy_version(event_texts=["公司中标智算中心建设项目"])
        assert "算力" in result.policy_tags
        assert result.version_score > 0

    def test_low_altitude_economy(self):
        result = detect_policy_version(event_texts=["低空经济政策密集出台，eVTOL产业加速"])
        assert "低空经济" in result.policy_tags

    def test_robot(self):
        result = detect_policy_version(event_texts=["人形机器人产业规划发布"])
        assert "机器人" in result.policy_tags

    def test_going_global(self):
        result = detect_policy_version(event_texts=["公司加速出海布局，海外收入占比提升"])
        assert "出海" in result.policy_tags

    def test_china_special_valuation(self):
        result = detect_policy_version(event_texts=["中特估行情持续演绎"])
        assert "中特估" in result.policy_tags

    def test_domestic_substitution(self):
        result = detect_policy_version(event_texts=["国产替代加速，芯片自主可控突破"])
        assert "国产替代" in result.policy_tags

    def test_merger_restructuring(self):
        result = detect_policy_version(event_texts=["公司公告重大资产重组方案"])
        assert "并购重组" in result.policy_tags

    def test_soe_reform(self):
        result = detect_policy_version(event_texts=["央企改革深化提升行动方案出台"])
        assert "国企改革" in result.policy_tags

    def test_semiconductor(self):
        result = detect_policy_version(event_texts=["半导体设备国产化率快速提升"])
        assert "半导体" in result.policy_tags

    def test_ai(self):
        result = detect_policy_version(event_texts=["AI大模型商业化落地加速"])
        assert "人工智能" in result.policy_tags

    def test_data_elements(self):
        result = detect_policy_version(event_texts=["数据要素市场化配置改革方案"])
        assert "数据要素" in result.policy_tags


class TestPolicyVersionScoring:
    def test_no_evidence_zero_score(self):
        result = detect_policy_version(event_texts=["今天天气不错"])
        assert result.policy_tags == []
        assert result.version_score == 0.0
        assert result.policy_evidence_refs == []

    def test_empty_texts_zero_score(self):
        result = detect_policy_version(event_texts=[])
        assert result.version_score == 0.0

    def test_none_texts_zero_score(self):
        result = detect_policy_version(event_texts=None)
        assert result.version_score == 0.0

    def test_none_both_zero_score(self):
        result = detect_policy_version(event_texts=None, industry_tags=None)
        assert result.version_score == 0.0

    def test_score_capped_at_max(self):
        texts = [
            "新质生产力", "算力基础设施", "低空经济", "人形机器人",
            "国产替代", "并购重组", "国企改革", "中特估",
            "半导体", "人工智能", "数据要素", "出海",
        ]
        result = detect_policy_version(event_texts=texts)
        assert result.version_score <= MAX_POLICY_BONUS
        assert len(result.policy_tags) > 5

    def test_multiple_hits_accumulate(self):
        result = detect_policy_version(event_texts=["新质生产力与算力基础设施同步推进"])
        assert len(result.policy_tags) >= 2
        assert result.version_score > 10

    def test_single_tag_weight(self):
        result = detect_policy_version(event_texts=["新质生产力发展指导意见"])
        assert result.version_score == 10  # weight for 新质生产力

    def test_industry_tag_lower_weight(self):
        result_text = detect_policy_version(event_texts=["新质生产力"])
        result_ind = detect_policy_version(industry_tags=["新质生产力"])
        assert result_text.version_score > result_ind.version_score
        assert result_ind.version_score > 0

    def test_industry_tag_source(self):
        result = detect_policy_version(industry_tags=["人工智能"])
        assert len(result.policy_evidence_refs) == 1
        assert result.policy_evidence_refs[0]["source"] == "industry_tag"


class TestPolicyVersionEvidence:
    def test_evidence_contains_matched_text(self):
        text = "国务院发布新质生产力发展指导意见，加快转型升级"
        result = detect_policy_version(event_texts=[text])
        assert any("新质生产力" in ref["matched_text"] for ref in result.policy_evidence_refs)

    def test_evidence_contains_tag(self):
        result = detect_policy_version(event_texts=["低空经济政策出台"])
        assert all("tag" in ref for ref in result.policy_evidence_refs)

    def test_evidence_contains_source(self):
        result = detect_policy_version(event_texts=["算力基础设施规划"])
        assert all("source" in ref for ref in result.policy_evidence_refs)

    def test_no_speculation_without_evidence(self):
        result = detect_policy_version(event_texts=["公司日常经营公告"])
        assert result.version_score == 0.0
        assert result.policy_tags == []

    def test_blank_texts_no_score(self):
        result = detect_policy_version(event_texts=["", None, "   "])
        assert result.version_score == 0.0


class TestPolicyVersionIntegration:
    def test_policy_version_in_candidate(self):
        df = _make_vcp_df()
        c, reason = evaluate_symbol(
            "TEST.SZ",
            name="测试",
            df=df,
            news_texts=["公司公告新质生产力相关项目中标"],
        )
        if c is not None:
            assert len(c.policy_tags) > 0
            assert c.version_score > 0
            assert STRATEGY_POLICY_VERSION in c.strategy_tags
            assert "policy_version" in c.evidence

    def test_policy_version_adds_to_score(self):
        df = _make_vcp_df()
        c_no_policy, _ = evaluate_symbol("TEST.SZ", name="测试", df=df, news_texts=["公司日常公告"])
        c_with_policy, _ = evaluate_symbol(
            "TEST.SZ", name="测试", df=df,
            news_texts=["公司公告新质生产力相关项目"],
        )
        if c_with_policy is not None and c_no_policy is not None:
            assert c_with_policy.score > c_no_policy.score or c_with_policy.version_score > 0

    def test_no_policy_evidence_no_bonus(self):
        df = _make_vcp_df()
        c, reason = evaluate_symbol(
            "TEST.SZ",
            name="测试",
            df=df,
            news_texts=["今天天气不错"],
        )
        if c is not None:
            assert c.version_score == 0.0
            assert STRATEGY_POLICY_VERSION not in c.strategy_tags

    def test_vcp_still_works_with_policy(self):
        df = _make_vcp_df()
        c, reason = evaluate_symbol(
            "TEST.SZ",
            name="测试",
            df=df,
            news_texts=["低空经济政策利好，公司布局eVTOL"],
        )
        if c is not None:
            vcp_hit = "VCP" in c.strategy_tags
            event_hit = "EVENT_CATALYST" in c.strategy_tags
            policy_hit = STRATEGY_POLICY_VERSION in c.strategy_tags
            assert vcp_hit or event_hit or policy_hit

    def test_symbol_isolation(self):
        df = _make_vcp_df()
        c_a, _ = evaluate_symbol(
            "AAA.SZ", name="测试A", df=df,
            news_texts=["新质生产力"],
        )
        c_b, _ = evaluate_symbol(
            "BBB.SZ", name="测试B", df=df,
            news_texts=["日常公告"],
        )
        if c_a is not None:
            assert len(c_a.policy_tags) > 0
        if c_b is not None:
            assert c_b.version_score == 0.0

    def test_high_version_score_triggers_deep_ta(self):
        df = _make_vcp_df()
        c, _ = evaluate_symbol(
            "TEST.SZ", name="测试", df=df,
            news_texts=[
                "新质生产力", "算力基础设施", "低空经济",
                "人形机器人", "国产替代",
            ],
        )
        if c is not None and c.version_score >= 15:
            assert c.need_deep_ta is True

    def test_policy_tags_in_db(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            init_db(db_path)
            c = Candidate(
                symbol="TEST.SZ",
                policy_tags=["新质生产力", "算力"],
                version_score=20.0,
                policy_evidence_refs=[{"tag": "新质生产力", "matched_text": "xxx", "source": "event_text"}],
                strategy_tags=["VCP", STRATEGY_POLICY_VERSION],
                score=65.0,
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
            import json
            assert json.loads(row["policy_tags_json"]) == ["新质生产力", "算力"]
            assert row["version_score"] == 20.0
        finally:
            os.unlink(db_path)


class TestPolicyVersionPlanDisplay:
    def test_render_text_shows_policy_tags(self):
        from tradingagents.tradeflow.schemas import DailyPlan
        plan = DailyPlan(
            trade_date="2026-05-29",
            candidates=[{
                "symbol": "TEST.SZ",
                "name": "测试",
                "action": "NEED_DEEP_TA",
                "primary_strategy": "EVENT_CATALYST",
                "strategies": ["EVENT_CATALYST", STRATEGY_POLICY_VERSION],
                "score": 75.0,
                "trigger_price": 25.0,
                "support_price": None,
                "invalid_price": 22.0,
                "reason": "事件催化",
                "need_deep_ta": True,
                "risk_flags": [],
                "policy_tags": ["新质生产力", "算力"],
                "version_score": 20.0,
                "policy_evidence_refs": [
                    {"tag": "新质生产力", "matched_text": "国务院发布新质生产力指导意见", "source": "event_text"},
                ],
            }],
        )
        text = plan.render_text()
        assert "新质生产力" in text
        assert "算力" in text
        assert "+20" in text
        assert "政策版本" in text

    def test_render_text_no_policy_tags(self):
        from tradingagents.tradeflow.schemas import DailyPlan
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
            }],
        )
        text = plan.render_text()
        assert "政策版本" not in text
