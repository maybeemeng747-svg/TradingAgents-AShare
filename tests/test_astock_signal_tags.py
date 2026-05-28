"""[N-004] astock_signal_tags — tests for A-share specific signal tag extraction."""

import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.tradeflow.strategies.event_catalyst import (
    extract_signal_tags,
    score_event_catalyst,
)
from tradingagents.tradeflow.schemas import (
    SIGNAL_TAG_POLICY_CATALYST,
    SIGNAL_TAG_HOT_MONEY_LHB,
    SIGNAL_TAG_LOCKUP_RISK,
    SIGNAL_TAG_BUYBACK_EVENT,
    SIGNAL_TAG_RATING_CHANGE,
)


class TestExtractPolicyCatalyst:
    def test_policy_keywords_detected(self):
        tags, risks = extract_signal_tags(["国务院发布产业政策扶持新能源"])
        assert SIGNAL_TAG_POLICY_CATALYST in tags
        assert SIGNAL_TAG_LOCKUP_RISK not in risks

    def test_subsidy_keyword(self):
        tags, _ = extract_signal_tags(["公司获得工信部补贴支持"])
        assert SIGNAL_TAG_POLICY_CATALYST in tags

    def test_ndrc_keyword(self):
        tags, _ = extract_signal_tags(["发改委批复重大项目"])
        assert SIGNAL_TAG_POLICY_CATALYST in tags

    def test_multiple_policy_keywords(self):
        tags, _ = extract_signal_tags(["国务院重点支持半导体产业政策"])
        assert tags.count(SIGNAL_TAG_POLICY_CATALYST) == 1


class TestExtractBuybackEvent:
    def test_buyback_detected(self):
        tags, _ = extract_signal_tags(["公司发布回购计划"])
        assert SIGNAL_TAG_BUYBACK_EVENT in tags

    def test_buyback_progress(self):
        tags, _ = extract_signal_tags(["关于回购进展公告"])
        assert SIGNAL_TAG_BUYBACK_EVENT in tags


class TestExtractRatingChange:
    def test_rating_upgrade(self):
        tags, _ = extract_signal_tags(["某券商评级上调至买入"])
        assert SIGNAL_TAG_RATING_CHANGE in tags

    def test_target_price_up(self):
        tags, _ = extract_signal_tags(["目标价上调至50元"])
        assert SIGNAL_TAG_RATING_CHANGE in tags

    def test_first_coverage(self):
        tags, _ = extract_signal_tags(["中信证券首次覆盖"])
        assert SIGNAL_TAG_RATING_CHANGE in tags


class TestExtractLockupRisk:
    def test_lockup_only_in_risk_flags(self):
        tags, risks = extract_signal_tags(["限售股解禁公告"])
        assert SIGNAL_TAG_LOCKUP_RISK in risks
        assert SIGNAL_TAG_LOCKUP_RISK not in tags

    def test_lockup_variants(self):
        for kw in ["解禁", "限售股解禁", "解除限售", "首发原股东限售股份上市流通"]:
            tags, risks = extract_signal_tags([kw])
            assert SIGNAL_TAG_LOCKUP_RISK in risks
            assert SIGNAL_TAG_LOCKUP_RISK not in tags

    def test_lockup_never_in_strategy_tags(self):
        tags, risks = extract_signal_tags(["公司限售股解禁，同时发布回购计划"])
        assert SIGNAL_TAG_LOCKUP_RISK not in tags
        assert SIGNAL_TAG_LOCKUP_RISK in risks
        assert SIGNAL_TAG_BUYBACK_EVENT in tags


class TestExtractHotMoneyLHB:
    def test_lhb_with_has_data(self):
        tags, _ = extract_signal_tags(
            ["龙虎榜显示游资买入"], lhb_status="HAS_DATA"
        )
        assert SIGNAL_TAG_HOT_MONEY_LHB in tags

    def test_lhb_not_queried_no_tag(self):
        tags, _ = extract_signal_tags(
            ["龙虎榜显示游资买入"], lhb_status="NOT_QUERIED"
        )
        assert SIGNAL_TAG_HOT_MONEY_LHB not in tags

    def test_lhb_normal_no_data_no_tag(self):
        tags, _ = extract_signal_tags(
            ["龙虎榜显示游资买入"], lhb_status="NORMAL_NO_DATA"
        )
        assert SIGNAL_TAG_HOT_MONEY_LHB not in tags

    def test_lhb_has_data_but_no_keywords(self):
        tags, _ = extract_signal_tags(
            ["公司发布季报"], lhb_status="HAS_DATA"
        )
        assert SIGNAL_TAG_HOT_MONEY_LHB not in tags

    def test_lhb_default_not_queried(self):
        tags, _ = extract_signal_tags(["龙虎榜游资活跃"])
        assert SIGNAL_TAG_HOT_MONEY_LHB not in tags


class TestExtractNoTags:
    def test_irrelevant_text(self):
        tags, risks = extract_signal_tags(["今天天气不错"])
        assert tags == []
        assert risks == []

    def test_empty_texts(self):
        tags, risks = extract_signal_tags([])
        assert tags == []
        assert risks == []


class TestScoreEventCatalystIntegration:
    def test_policy_catalyst_in_evidence(self):
        result = score_event_catalyst(
            "TEST.SZ",
            news_texts=["国务院发布产业政策扶持新能源，公司中标重大项目"],
            latest_close=25.0,
        )
        assert result is not None
        assert SIGNAL_TAG_POLICY_CATALYST in result.evidence.get("astock_tags", [])

    def test_lockup_in_risk_flags_not_tags(self):
        result = score_event_catalyst(
            "TEST.SZ",
            news_texts=["公司限售股解禁公告，控股股东增持计划"],
            latest_close=25.0,
        )
        assert result is not None
        astock_tags = result.evidence.get("astock_tags", [])
        astock_risks = result.evidence.get("astock_risk_flags", [])
        assert SIGNAL_TAG_LOCKUP_RISK not in astock_tags
        assert SIGNAL_TAG_LOCKUP_RISK in astock_risks
        assert SIGNAL_TAG_LOCKUP_RISK in result.risk_flags

    def test_lhb_tag_with_has_data(self):
        result = score_event_catalyst(
            "TEST.SZ",
            news_texts=["龙虎榜显示营业部买入，公司发布业绩预告"],
            latest_close=25.0,
            lhb_status="HAS_DATA",
        )
        assert result is not None
        assert SIGNAL_TAG_HOT_MONEY_LHB in result.evidence.get("astock_tags", [])

    def test_lhb_no_tag_when_not_queried(self):
        result = score_event_catalyst(
            "TEST.SZ",
            news_texts=["龙虎榜显示营业部买入，公司发布业绩预告"],
            latest_close=25.0,
            lhb_status="NOT_QUERIED",
        )
        assert result is not None
        assert SIGNAL_TAG_HOT_MONEY_LHB not in result.evidence.get("astock_tags", [])

    def test_buyback_tag_in_evidence(self):
        result = score_event_catalyst(
            "TEST.SZ",
            news_texts=["公司发布回购计划"],
            latest_close=25.0,
        )
        assert result is not None
        assert SIGNAL_TAG_BUYBACK_EVENT in result.evidence.get("astock_tags", [])

    def test_rating_change_in_evidence(self):
        result = score_event_catalyst(
            "TEST.SZ",
            news_texts=["评级上调至买入，公司业绩预告增长"],
            latest_close=25.0,
        )
        assert result is not None
        assert SIGNAL_TAG_RATING_CHANGE in result.evidence.get("astock_tags", [])


class TestCandidateEngineIntegration:
    def test_lockup_reduces_score(self):
        import pandas as pd
        import numpy as np
        from tradingagents.tradeflow.candidate_engine import evaluate_symbol

        np.random.seed(42)
        n = 120
        dates = pd.date_range("2026-01-01", periods=n, freq="B")
        prices = [20.0]
        for i in range(1, n):
            prices.append(prices[-1] * (1 + 0.003 + np.random.normal(0, 0.02)))
        prices = np.array(prices)
        volumes = np.random.uniform(50_000_000, 200_000_000, n)
        df = pd.DataFrame({
            "Date": dates,
            "Open": prices,
            "High": prices * 1.01,
            "Low": prices * 0.99,
            "Close": prices,
            "Volume": volumes,
        })

        c_normal, _ = evaluate_symbol(
            "TEST.SZ",
            name="测试",
            news_texts=["公司发布业绩预告，净利润增长50%"],
            df=df,
        )

        c_lockup, _ = evaluate_symbol(
            "LOCK.SZ",
            name="解禁",
            news_texts=["公司限售股解禁，业绩预告增长"],
            df=df,
        )

        if c_normal and c_lockup:
            assert c_lockup.score < c_normal.score
            assert SIGNAL_TAG_LOCKUP_RISK in c_lockup.risk_flags
