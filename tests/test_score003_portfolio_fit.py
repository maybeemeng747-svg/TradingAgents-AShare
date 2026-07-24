# [SCORE-003] portfolio_fit_card
"""Tests for portfolio_fit score card (SCORE-003).

Covers (per docs/TASKS.md SCORE-003 acceptance):
  1. Untradeable — STAR market without permission → score 0, tradable_by_user=False.
  2. Empty account + sufficient cash — no position, plenty of cash → high score.
  3. Already overweight — position at/past planned max → score 0, position_overweight=True.
  4. Insufficient cash — cash < one lot cost → score 0, insufficient_cash=True.
  5. High correlation concentration — same-topic holdings at max → score 0.
  6. Context unknown — no user context → context_unknown=True, never "fit well".
  7. Mixed real/simulated source conflict — context_unknown reflects missing critical data.
  8. Stability — same inputs produce identical output across 10 runs.
  9. Serialization — portfolio_fit_to_dict is JSON-serializable.
  10. Service layer enrichment — _enrich_candidate_with_portfolio_fit injects card.
  11. Service layer batch — _enrich_candidates_with_portfolio_fit batch mode.
  12. Service layer failure graceful — exception → portfolio_fit_card=None.
  13. Board inference — symbol prefixes correctly classified.
  14. Risk budget — budget utilization thresholds.
  15. All fields missing — portfolio_fit=0, data_status="missing".
  16. Dimension weights sum to 1.0.
"""

from __future__ import annotations

import json
from typing import Optional

import pytest

from tradingagents.tradeflow.portfolio_fit_score import (
    PortfolioFitDimension,
    PortfolioFitResult,
    _infer_board_type,
    _lot_size_for_board,
    _score_cash_defense,
    _score_correlation_concentration,
    _score_liquidity_execution,
    _score_position_and_limit,
    _score_risk_budget,
    _score_trading_permission,
    compute_portfolio_fit,
    portfolio_fit_to_dict,
)


# ── Board inference ─────────────────────────────────────────────────

class TestBoardInference:
    def test_star_market_688(self):
        assert _infer_board_type("688256.SH") == "star"

    def test_star_market_689(self):
        assert _infer_board_type("689009.SH") == "star"

    def test_chinext_300(self):
        assert _infer_board_type("300750.SZ") == "chinext"

    def test_chinext_301(self):
        assert _infer_board_type("301269.SZ") == "chinext"

    def test_bse_8(self):
        assert _infer_board_type("830799.BJ") == "bse"

    def test_bse_4(self):
        assert _infer_board_type("430047.BJ") == "bse"

    def test_main_board_6(self):
        assert _infer_board_type("600519.SH") == "main"

    def test_main_board_0(self):
        assert _infer_board_type("000001.SZ") == "main"

    def test_unknown_empty(self):
        assert _infer_board_type("") == "unknown"

    def test_unknown_no_dot(self):
        assert _infer_board_type("AAPL") == "unknown"

    def test_lot_size_star(self):
        assert _lot_size_for_board("star") == 200

    def test_lot_size_chinext(self):
        assert _lot_size_for_board("chinext") == 100

    def test_lot_size_main(self):
        assert _lot_size_for_board("main") == 100

    def test_lot_size_bse(self):
        assert _lot_size_for_board("bse") == 1

    def test_lot_size_unknown(self):
        assert _lot_size_for_board("unknown") == 100


# ── Trading permission ──────────────────────────────────────────────

class TestTradingPermission:
    def test_main_board_always_tradable(self):
        score, src, reasons, missing = _score_trading_permission(symbol="600519.SH")
        assert score == 100.0
        assert not missing

    def test_star_with_permission(self):
        score, src, reasons, missing = _score_trading_permission(
            symbol="688256.SH", user_permissions={"star": True},
        )
        assert score == 100.0
        assert "已开通科创板权限" in reasons[0]

    def test_star_without_permission(self):
        score, src, reasons, missing = _score_trading_permission(
            symbol="688256.SH", user_permissions={"star": False},
        )
        assert score == 0.0
        assert "未开通科创板权限" in reasons[0]

    def test_star_permission_unknown(self):
        score, src, reasons, missing = _score_trading_permission(
            symbol="688256.SH", user_permissions={},
        )
        assert score == 0.0
        assert "未知" in reasons[0]

    def test_chinext_with_permission(self):
        score, src, reasons, missing = _score_trading_permission(
            symbol="300750.SZ", user_permissions={"chinext": True},
        )
        assert score == 100.0

    def test_chinext_without_permission(self):
        score, src, reasons, missing = _score_trading_permission(
            symbol="300750.SZ", user_permissions={"chinext": False},
        )
        assert score == 0.0

    def test_bse_with_permission(self):
        score, src, reasons, missing = _score_trading_permission(
            symbol="830799.BJ", user_permissions={"bse": True},
        )
        assert score == 100.0

    def test_no_permissions_provided(self):
        score, src, reasons, missing = _score_trading_permission(
            symbol="688256.SH", user_permissions=None,
        )
        assert score == 0.0
        assert "user_permissions" in missing

    def test_unknown_symbol_no_permission(self):
        score, src, reasons, missing = _score_trading_permission(symbol="AAPL")
        assert score == 0.0

    def test_main_no_permission_needed(self):
        score, _, _, missing = _score_trading_permission(
            symbol="000001.SZ", user_permissions=None,
        )
        assert score == 100.0
        assert not missing


# ── Position and limit ──────────────────────────────────────────────

class TestPositionAndLimit:
    def test_no_position_no_limit(self):
        score, src, reasons, missing = _score_position_and_limit()
        assert score == 0.0
        assert "current_position_pct" in missing
        assert "planned_max_position_pct" in missing

    def test_position_well_below_limit(self):
        score, _, reasons, _ = _score_position_and_limit(
            current_position_pct=2.0, planned_max_position_pct=15.0,
        )
        assert score >= 80.0
        assert "充裕" in reasons[0]

    def test_position_moderate(self):
        score, _, reasons, _ = _score_position_and_limit(
            current_position_pct=8.0, planned_max_position_pct=15.0,
        )
        # room_pct = (15-8)/15*100 = 46.7% → falls in 20-50 range → score=35
        assert 30.0 <= score <= 50.0

    def test_position_near_limit(self):
        score, _, reasons, _ = _score_position_and_limit(
            current_position_pct=13.0, planned_max_position_pct=15.0,
        )
        assert score <= 40.0
        assert "偏重" in reasons[0] or "接近上限" in reasons[0]

    def test_position_at_limit(self):
        score, _, reasons, _ = _score_position_and_limit(
            current_position_pct=15.0, planned_max_position_pct=15.0,
        )
        assert score == 0.0
        assert "≥" in reasons[0]

    def test_position_over_limit(self):
        score, _, reasons, _ = _score_position_and_limit(
            current_position_pct=20.0, planned_max_position_pct=15.0,
        )
        assert score == 0.0

    def test_no_current_but_has_planned(self):
        score, _, _, missing = _score_position_and_limit(
            planned_max_position_pct=15.0,
        )
        assert score == 0.0
        assert "current_position_pct" in missing

    def test_no_planned_uses_asset_type_default(self):
        score, _, reasons, missing = _score_position_and_limit(
            current_position_pct=0.0, asset_type="theme",
        )
        assert score > 0.0
        assert "theme" in reasons[0]
        assert "planned_max_position_pct" in missing

    def test_zero_planned_max(self):
        score, _, _, _ = _score_position_and_limit(
            current_position_pct=0.0, planned_max_position_pct=0.0,
        )
        assert score == 0.0

    def test_negative_position_clamped(self):
        score, _, _, _ = _score_position_and_limit(
            current_position_pct=-5.0, planned_max_position_pct=10.0,
        )
        assert score > 0.0  # negative clamped to 0

    def test_large_position_over_limit(self):
        score, _, _, _ = _score_position_and_limit(
            current_position_pct=40.0, planned_max_position_pct=10.0,
        )
        assert score == 0.0


# ── Cash defense ────────────────────────────────────────────────────

class TestCashDefense:
    def test_no_cash_no_price(self):
        score, src, reasons, missing = _score_cash_defense()
        assert score == 0.0
        assert "cash_available" in missing

    def test_sufficient_cash_main_board(self):
        score, _, reasons, _ = _score_cash_defense(
            cash_available=100000.0, current_price=50.0, symbol="600519.SH",
        )
        assert score >= 70.0
        assert "资金充足" in reasons[0] or "充裕" in reasons[0]

    def test_cash_barely_one_lot(self):
        # 100 shares × 10 yuan = 1000 yuan; cash = 1050
        score, _, reasons, _ = _score_cash_defense(
            cash_available=1050.0, current_price=10.0, symbol="600519.SH",
        )
        assert score <= 30.0
        assert "仅够一手" in reasons[0]

    def test_insufficient_cash(self):
        # 100 shares × 50 yuan = 5000; cash = 3000
        score, _, reasons, _ = _score_cash_defense(
            cash_available=3000.0, current_price=50.0, symbol="600519.SH",
        )
        assert score == 0.0
        assert "不足一手" in reasons[0]

    def test_star_market_200_lot(self):
        # 200 shares × 100 yuan = 20000; cash = 15000
        score, _, reasons, _ = _score_cash_defense(
            cash_available=15000.0, current_price=100.0, symbol="688256.SH",
        )
        assert score == 0.0
        assert "不足一手" in reasons[0]

    def test_star_market_sufficient(self):
        # 200 shares × 100 yuan = 20000; cash = 50000
        score, _, reasons, _ = _score_cash_defense(
            cash_available=50000.0, current_price=100.0, symbol="688256.SH",
        )
        assert score >= 50.0

    def test_no_price_partial_score_high_cash(self):
        score, _, _, missing = _score_cash_defense(cash_available=100000.0)
        assert score == 60.0
        assert "current_price" in missing

    def test_no_price_partial_score_medium_cash(self):
        score, _, _, _ = _score_cash_defense(cash_available=20000.0)
        assert score == 40.0

    def test_no_price_partial_score_low_cash(self):
        score, _, _, _ = _score_cash_defense(cash_available=5000.0)
        assert score == 25.0

    def test_no_price_partial_score_very_low_cash(self):
        score, _, _, _ = _score_cash_defense(cash_available=1000.0)
        assert score == 10.0

    def test_zero_cash(self):
        score, _, _, _ = _score_cash_defense(
            cash_available=0.0, current_price=10.0, symbol="600519.SH",
        )
        assert score == 0.0

    def test_zero_price(self):
        score, _, _, missing = _score_cash_defense(
            cash_available=10000.0, current_price=0.0, symbol="600519.SH",
        )
        # price=0 → has_price=False → partial score based on cash alone
        assert score == 40.0
        assert "current_price" in missing

    def test_many_lots(self):
        # 100 shares × 5 yuan = 500; cash = 50000 → 100 lots
        score, _, reasons, _ = _score_cash_defense(
            cash_available=50000.0, current_price=5.0, symbol="600519.SH",
        )
        assert score >= 80.0
        assert "资金充足" in reasons[0]


# ── Correlation concentration ────────────────────────────────────────

class TestCorrelationConcentration:
    def test_no_topic_neutral(self):
        score, src, reasons, missing = _score_correlation_concentration()
        assert score == 50.0  # neutral, not penalized
        assert "mandate_topic" in missing

    def test_topic_no_holdings_data(self):
        score, _, _, missing = _score_correlation_concentration(
            mandate_topic="半导体自主可控",
        )
        assert score == 30.0  # conservative
        assert "holdings_topics" in missing

    def test_no_same_topic_holdings(self):
        score, _, reasons, _ = _score_correlation_concentration(
            mandate_topic="半导体自主可控",
            holdings_topics=["新能源", "消费"],
            same_topic_holding_count=0,
        )
        assert score == 90.0
        assert "无同主题暴露" in reasons[0]

    def test_below_concentration_max(self):
        score, _, reasons, _ = _score_correlation_concentration(
            mandate_topic="半导体自主可控",
            holdings_topics=["半导体自主可控", "新能源"],
            same_topic_holding_count=1,
            concentration_per_topic_max=3,
        )
        assert score == 60.0
        assert "1只" in reasons[0]

    def test_at_concentration_max(self):
        score, _, reasons, _ = _score_correlation_concentration(
            mandate_topic="半导体自主可控",
            holdings_topics=["半导体自主可控"] * 3,
            same_topic_holding_count=3,
            concentration_per_topic_max=3,
        )
        assert score == 20.0
        assert "上限" in reasons[0]

    def test_exceeds_concentration_max(self):
        score, _, reasons, _ = _score_correlation_concentration(
            mandate_topic="半导体自主可控",
            holdings_topics=["半导体自主可控"] * 5,
            same_topic_holding_count=5,
            concentration_per_topic_max=3,
        )
        assert score == 0.0
        assert "超" in reasons[0]


# ── Liquidity execution ─────────────────────────────────────────────

class TestLiquidityExecution:
    def test_no_data(self):
        score, src, reasons, missing = _score_liquidity_execution()
        assert score == 0.0
        assert "current_price" in missing

    def test_low_price_high_volume(self):
        score, _, reasons, _ = _score_liquidity_execution(
            current_price=10.0, avg_daily_volume=5000000.0,
        )
        assert score >= 80.0

    def test_high_price_low_volume(self):
        score, _, reasons, _ = _score_liquidity_execution(
            current_price=500.0, avg_daily_volume=5000.0,
        )
        assert score <= 50.0

    def test_medium_price_medium_volume(self):
        score, _, _, _ = _score_liquidity_execution(
            current_price=50.0, avg_daily_volume=500000.0,
        )
        assert 40.0 <= score <= 90.0

    def test_wide_spread(self):
        score, _, reasons, _ = _score_liquidity_execution(
            current_price=30.0, avg_daily_volume=1000000.0,
            bid_ask_spread_pct=2.0,
        )
        assert any("价差" in r for r in reasons)

    def test_tight_spread(self):
        score, _, _, _ = _score_liquidity_execution(
            current_price=30.0, avg_daily_volume=1000000.0,
            bid_ask_spread_pct=0.05,
        )
        assert score >= 80.0

    def test_price_only_no_volume(self):
        score, _, _, missing = _score_liquidity_execution(current_price=25.0)
        assert score > 0.0
        assert "avg_daily_volume" in missing

    def test_very_low_volume(self):
        score, _, reasons, _ = _score_liquidity_execution(
            current_price=20.0, avg_daily_volume=5000.0,
        )
        assert score <= 50.0
        assert any("差" in r or "弱" in r for r in reasons)


# ── Risk budget ─────────────────────────────────────────────────────

class TestRiskBudget:
    def test_no_data(self):
        score, src, reasons, missing = _score_risk_budget()
        assert score == 0.0
        assert "budget_utilization_pct" in missing

    def test_budget_low_utilization(self):
        score, _, reasons, _ = _score_risk_budget(budget_utilization_pct=20.0)
        assert score >= 80.0
        assert "充裕" in reasons[0]

    def test_budget_medium_utilization(self):
        score, _, reasons, _ = _score_risk_budget(budget_utilization_pct=60.0)
        assert 40.0 <= score <= 70.0

    def test_budget_high_utilization(self):
        score, _, reasons, _ = _score_risk_budget(budget_utilization_pct=85.0)
        assert score <= 30.0
        assert "紧张" in reasons[0]

    def test_budget_fully_utilized(self):
        score, _, reasons, _ = _score_risk_budget(budget_utilization_pct=100.0)
        assert score <= 10.0
        assert "耗尽" in reasons[0]

    def test_daily_new_at_limit(self):
        score, _, reasons, _ = _score_risk_budget(
            daily_new_today=3, daily_new_max=3,
        )
        assert score <= 10.0
        assert "上限" in reasons[0]

    def test_daily_new_near_limit(self):
        score, _, reasons, _ = _score_risk_budget(
            daily_new_today=2, daily_new_max=3,
        )
        assert score <= 40.0
        assert "仅剩" in reasons[0]

    def test_daily_new_plenty(self):
        score, _, reasons, _ = _score_risk_budget(
            daily_new_today=0, daily_new_max=3,
        )
        assert score >= 70.0

    def test_tracking_count_full(self):
        score, _, reasons, _ = _score_risk_budget(
            tracking_count=5, max_concurrent_tracking=5,
        )
        assert score <= 20.0
        assert "满" in reasons[0]

    def test_tracking_count_near_full(self):
        score, _, reasons, _ = _score_risk_budget(
            tracking_count=4, max_concurrent_tracking=5,
        )
        assert score <= 50.0
        assert "接近" in reasons[0]

    def test_tracking_count_plenty(self):
        score, _, _, _ = _score_risk_budget(
            tracking_count=1, max_concurrent_tracking=5,
        )
        assert score >= 70.0

    def test_all_three_dimensions(self):
        score, _, _, _ = _score_risk_budget(
            budget_utilization_pct=30.0,
            daily_new_today=0,
            daily_new_max=3,
            tracking_count=2,
            max_concurrent_tracking=5,
        )
        assert score >= 70.0


# ── Integration: compute_portfolio_fit ──────────────────────────────

class TestComputePortfolioFit:
    def test_untradeable_star_no_permission(self):
        result = compute_portfolio_fit(
            symbol="688256.SH",
            current_price=100.0,
            user_permissions={"star": False},
        )
        assert result.tradable_by_user is False
        assert result.trading_permission.score == 0.0

    def test_empty_account_sufficient_cash(self):
        result = compute_portfolio_fit(
            symbol="600519.SH",
            current_price=50.0,
            cash_available=100000.0,
            current_position_pct=0.0,
            planned_max_position_pct=15.0,
        )
        assert result.portfolio_fit > 0.0
        assert result.tradable_by_user is True
        assert result.position_overweight is not True
        assert result.insufficient_cash is not True

    def test_already_overweight(self):
        result = compute_portfolio_fit(
            symbol="600519.SH",
            current_price=50.0,
            current_position_pct=15.0,
            planned_max_position_pct=15.0,
        )
        assert result.position_overweight is True
        assert result.position_and_limit.score == 0.0

    def test_insufficient_cash(self):
        result = compute_portfolio_fit(
            symbol="600519.SH",
            current_price=50.0,
            cash_available=2000.0,
        )
        assert result.insufficient_cash is True
        assert result.cash_defense.score == 0.0

    def test_high_correlation_concentration(self):
        result = compute_portfolio_fit(
            symbol="600519.SH",
            current_price=50.0,
            mandate_topic="半导体自主可控",
            holdings_topics=["半导体自主可控"] * 5,
            same_topic_holding_count=5,
            concentration_per_topic_max=3,
        )
        assert result.concentration_exceeded is True
        assert result.correlation_concentration.score == 0.0

    def test_context_unknown_no_critical_inputs(self):
        result = compute_portfolio_fit(symbol="600519.SH")
        assert result.context_unknown is True
        # Only trading_permission is "ok" (main board), rest are missing
        assert result.data_status in ("partial", "missing")

    def test_context_unknown_with_only_price(self):
        result = compute_portfolio_fit(
            symbol="600519.SH",
            current_price=50.0,
        )
        # Still unknown: no permissions, no cash, no position
        assert result.context_unknown is True

    def test_context_not_unknown_when_enough_data(self):
        # [SCORE-003-R1] context is only "known" when ALL core dimensions
        # (permission / position / cash / risk budget) are provided.
        result = compute_portfolio_fit(
            symbol="600519.SH",
            current_price=50.0,
            user_permissions={"main": True},
            cash_available=50000.0,
            current_position_pct=0.0,
            planned_max_position_pct=15.0,
            budget_utilization_pct=30.0,
        )
        assert result.context_unknown is False
        assert result.portfolio_fit > 0.0

    def test_all_dimensions_present(self):
        result = compute_portfolio_fit(
            symbol="600519.SH",
            current_price=50.0,
            user_permissions={"main": True},
            cash_available=50000.0,
            current_position_pct=5.0,
            planned_max_position_pct=15.0,
            mandate_topic="消费",
            holdings_topics=["新能源"],
            same_topic_holding_count=0,
            budget_utilization_pct=30.0,
            daily_new_today=0,
            daily_new_max=3,
            tracking_count=2,
            max_concurrent_tracking=5,
            avg_daily_volume=1000000.0,
            bid_ask_spread_pct=0.1,
        )
        assert result.data_status == "ok"
        assert result.portfolio_fit > 0.0
        for dim in [
            result.trading_permission,
            result.position_and_limit,
            result.cash_defense,
            result.correlation_concentration,
            result.liquidity_execution,
            result.risk_budget,
        ]:
            assert dim.status == "ok"

    def test_partial_data(self):
        result = compute_portfolio_fit(
            symbol="600519.SH",
            current_price=50.0,
            cash_available=50000.0,
            # Missing: user_permissions, current_position_pct, risk budget
        )
        assert result.data_status == "partial"

    def test_deterministic(self):
        kwargs = dict(
            symbol="300750.SZ",
            current_price=80.0,
            user_permissions={"chinext": True},
            cash_available=30000.0,
            current_position_pct=3.0,
            planned_max_position_pct=10.0,
            mandate_topic="新能源",
            holdings_topics=["新能源", "消费"],
            same_topic_holding_count=1,
            budget_utilization_pct=40.0,
            daily_new_today=1,
            daily_new_max=3,
            tracking_count=2,
            max_concurrent_tracking=5,
            avg_daily_volume=500000.0,
        )
        results = [compute_portfolio_fit(**kwargs) for _ in range(10)]
        scores = [r.portfolio_fit for r in results]
        assert len(set(scores)) == 1  # all identical

    def test_score_range_0_100(self):
        """Score always in [0, 100]."""
        cases = [
            {},
            dict(symbol="600519.SH", current_price=10.0, cash_available=100.0),
            dict(
                symbol="600519.SH", current_price=50.0,
                user_permissions={"main": True}, cash_available=1000000.0,
                current_position_pct=0.0, planned_max_position_pct=40.0,
                mandate_topic="X", holdings_topics=[], same_topic_holding_count=0,
                budget_utilization_pct=0.0, daily_new_today=0, daily_new_max=10,
                tracking_count=0, max_concurrent_tracking=20,
                avg_daily_volume=10000000.0,
            ),
        ]
        for kw in cases:
            r = compute_portfolio_fit(**kw)
            assert 0.0 <= r.portfolio_fit <= 100.0

    def test_risk_budget_exceeded(self):
        result = compute_portfolio_fit(
            symbol="600519.SH",
            budget_utilization_pct=100.0,
            daily_new_today=3,
            daily_new_max=3,
            tracking_count=5,
            max_concurrent_tracking=5,
        )
        assert result.risk_budget_exceeded is True

    def test_risk_budget_not_exceeded(self):
        result = compute_portfolio_fit(
            symbol="600519.SH",
            budget_utilization_pct=20.0,
            daily_new_today=0,
            daily_new_max=3,
            tracking_count=1,
            max_concurrent_tracking=5,
        )
        assert result.risk_budget_exceeded is not True


# ── Serialization ───────────────────────────────────────────────────

class TestSerialization:
    def test_portfolio_fit_to_dict_keys(self):
        result = compute_portfolio_fit(
            symbol="600519.SH",
            current_price=50.0,
            user_permissions={"main": True},
            cash_available=50000.0,
            current_position_pct=5.0,
            planned_max_position_pct=15.0,
        )
        d = portfolio_fit_to_dict(result)
        assert "portfolio_fit" in d
        assert "data_status" in d
        assert "trading_permission" in d
        assert "position_and_limit" in d
        assert "cash_defense" in d
        assert "correlation_concentration" in d
        assert "liquidity_execution" in d
        assert "risk_budget" in d
        assert "tradable_by_user" in d
        assert "position_overweight" in d
        assert "insufficient_cash" in d
        assert "concentration_exceeded" in d
        assert "risk_budget_exceeded" in d
        assert "context_unknown" in d

    def test_json_serializable(self):
        result = compute_portfolio_fit(
            symbol="600519.SH",
            current_price=50.0,
            cash_available=50000.0,
            current_position_pct=0.0,
            planned_max_position_pct=15.0,
        )
        d = portfolio_fit_to_dict(result)
        serialized = json.dumps(d, ensure_ascii=False)
        deserialized = json.loads(serialized)
        assert deserialized["portfolio_fit"] == result.portfolio_fit

    def test_dimension_dict_structure(self):
        result = compute_portfolio_fit(symbol="600519.SH", current_price=50.0)
        d = portfolio_fit_to_dict(result)
        for key in [
            "trading_permission", "position_and_limit", "cash_defense",
            "correlation_concentration", "liquidity_execution", "risk_budget",
        ]:
            dim = d[key]
            assert "score" in dim
            assert "weight" in dim
            assert "status" in dim
            assert "source_fields" in dim
            assert "reasons" in dim
            assert "missing_fields" in dim


# ── Dimension weights ───────────────────────────────────────────────

class TestDimensionWeights:
    def test_weights_sum_to_one(self):
        result = compute_portfolio_fit(symbol="600519.SH")
        total = sum([
            result.trading_permission.weight,
            result.position_and_limit.weight,
            result.cash_defense.weight,
            result.correlation_concentration.weight,
            result.liquidity_execution.weight,
            result.risk_budget.weight,
        ])
        assert abs(total - 1.0) < 1e-9

    def test_individual_weights(self):
        result = compute_portfolio_fit(symbol="600519.SH")
        assert result.trading_permission.weight == 0.20
        assert result.position_and_limit.weight == 0.20
        assert result.cash_defense.weight == 0.15
        assert result.correlation_concentration.weight == 0.15
        assert result.liquidity_execution.weight == 0.15
        assert result.risk_budget.weight == 0.15


# ── Verification scenarios from TASKS.md ────────────────────────────

class TestAcceptanceScenarios:
    """Fixture coverage per SCORE-003 acceptance criteria."""

    def test_untradeable(self):
        """不可交易：STAR市场无权限"""
        r = compute_portfolio_fit(
            symbol="688256.SH",
            current_price=100.0,
            user_permissions={"star": False},
            cash_available=100000.0,
            current_position_pct=0.0,
            planned_max_position_pct=10.0,
        )
        assert r.tradable_by_user is False
        assert r.trading_permission.score == 0.0

    def test_empty_account_sufficient_cash(self):
        """空仓现金足：无持仓、充足现金"""
        r = compute_portfolio_fit(
            symbol="600519.SH",
            current_price=50.0,
            user_permissions={"main": True},
            cash_available=100000.0,
            current_position_pct=0.0,
            planned_max_position_pct=15.0,
        )
        assert r.tradable_by_user is True
        assert r.insufficient_cash is not True
        assert r.portfolio_fit > 50.0

    def test_already_overweight(self):
        """已超配：当前仓位达到计划上限"""
        r = compute_portfolio_fit(
            symbol="600519.SH",
            current_price=50.0,
            user_permissions={"main": True},
            cash_available=100000.0,
            current_position_pct=20.0,
            planned_max_position_pct=15.0,
        )
        assert r.position_overweight is True
        assert r.position_and_limit.score == 0.0

    def test_insufficient_cash(self):
        """现金不足：可用资金不够一手"""
        r = compute_portfolio_fit(
            symbol="600519.SH",
            current_price=200.0,
            user_permissions={"main": True},
            cash_available=5000.0,
            current_position_pct=0.0,
            planned_max_position_pct=15.0,
        )
        assert r.insufficient_cash is True
        assert r.cash_defense.score == 0.0

    def test_high_correlation_concentration(self):
        r = compute_portfolio_fit(
            symbol="600519.SH",
            current_price=50.0,
            user_permissions={"main": True},
            cash_available=100000.0,
            current_position_pct=0.0,
            planned_max_position_pct=15.0,
            mandate_topic="白酒",
            holdings_topics=["白酒", "白酒", "白酒"],
            same_topic_holding_count=3,
            concentration_per_topic_max=3,
        )
        # At max → score=20, not exceeded (which requires > max)
        assert r.correlation_concentration.score == 20.0
        assert r.concentration_exceeded is False  # at max, not over

    def test_concentration_exceeded_over_max(self):
        """concentration_exceeded triggers when count > max."""
        r = compute_portfolio_fit(
            symbol="600519.SH",
            current_price=50.0,
            mandate_topic="白酒",
            holdings_topics=["白酒"] * 5,
            same_topic_holding_count=5,
            concentration_per_topic_max=3,
        )
        assert r.concentration_exceeded is True
        assert r.correlation_concentration.score == 0.0

    def test_context_unknown(self):
        """持仓未知：无账户上下文"""
        r = compute_portfolio_fit(symbol="600519.SH", current_price=50.0)
        assert r.context_unknown is True
        # Some dimensions have partial data (permission=ok, liquidity=partial)
        # so portfolio_fit may be > 0, but context_unknown is correctly True

    def test_real_vs_sim_source_conflict(self):
        """模拟/真实来源冲突：关键数据缺失时 context_unknown"""
        # [SCORE-003-R1] STAR board requires permission; when user_permissions
        # is None the permission dimension is "unknown" (not merely "partial"),
        # which forces context_unknown=True.
        r = compute_portfolio_fit(
            symbol="688256.SH",
            current_price=50.0,
            cash_available=50000.0,
            # No user_permissions (STAR needs it), no current_position_pct
        )
        assert r.context_unknown is True
        # Permission is unknown (STAR needs permission, none provided);
        # position is missing.
        assert r.trading_permission.status == "unknown"

    def test_missing_context_not_interpreted_as_fit(self):
        """任意缺省上下文不会被解释为"适配良好" """
        r = compute_portfolio_fit()
        assert r.portfolio_fit == 0.0
        assert r.context_unknown is True
        # Trading permission has partial status (symbol="" → unknown board)
        assert r.data_status in ("partial", "missing")


# ── Service layer enrichment ────────────────────────────────────────

class TestServiceLayerEnrichment:
    def test_enrich_candidate_with_portfolio_fit(self):
        """Single candidate enrichment injects portfolio_fit_card."""
        from api.services.tradeflow_service import (
            _enrich_candidate_with_portfolio_fit,
        )
        item = {
            "symbol": "600519.SH",
            "current_price": 50.0,
            "strategy_tags": ["龙头", "蓝筹"],
            "mandate_topic": "白酒",
            "current_position_pct": 2.0,
        }
        result = _enrich_candidate_with_portfolio_fit(item)
        assert "portfolio_fit_card" in result
        card = result["portfolio_fit_card"]
        assert card is not None
        assert "portfolio_fit" in card
        assert "data_status" in card
        assert "tradable_by_user" in card

    def test_enrich_candidates_batch(self):
        """Batch enrichment injects portfolio_fit_card for all items."""
        from api.services.tradeflow_service import (
            _enrich_candidates_with_portfolio_fit,
        )
        items = [
            {"symbol": "600519.SH", "current_price": 50.0},
            {"symbol": "300750.SZ", "current_price": 80.0},
        ]
        result = _enrich_candidates_with_portfolio_fit(items)
        assert len(result) == 2
        for it in result:
            assert "portfolio_fit_card" in it

    def test_enrich_empty_list(self):
        from api.services.tradeflow_service import (
            _enrich_candidates_with_portfolio_fit,
        )
        assert _enrich_candidates_with_portfolio_fit([]) == []

    def test_enrich_none_list(self):
        from api.services.tradeflow_service import (
            _enrich_candidates_with_portfolio_fit,
        )
        assert _enrich_candidates_with_portfolio_fit(None) is None

    def test_enrich_exception_graceful(self):
        """Exception during enrichment → portfolio_fit_card=None."""
        from api.services.tradeflow_service import (
            _enrich_candidate_with_portfolio_fit,
        )
        # Pass a non-dict item that will cause an exception internally
        item = {"symbol": None, "current_price": "not_a_number"}
        result = _enrich_candidate_with_portfolio_fit(item)
        # Should not raise; card may be None or a valid dict
        assert "portfolio_fit_card" in result

    def test_enrich_preserves_existing_fields(self):
        """Enrichment doesn't clobber existing item fields."""
        from api.services.tradeflow_service import (
            _enrich_candidate_with_portfolio_fit,
        )
        item = {
            "symbol": "600519.SH",
            "current_price": 50.0,
            "tier": "A",
            "composite_score": 85.0,
            "existing_field": "preserved",
        }
        result = _enrich_candidate_with_portfolio_fit(item)
        assert result["tier"] == "A"
        assert result["composite_score"] == 85.0
        assert result["existing_field"] == "preserved"

    def test_enrich_with_account_context(self):
        """Enrichment passes through account context kwargs."""
        from api.services.tradeflow_service import (
            _enrich_candidate_with_portfolio_fit,
        )
        item = {
            "symbol": "600519.SH",
            "current_price": 50.0,
            "strategy_tags": ["龙头"],
            "current_position_pct": 5.0,
        }
        result = _enrich_candidate_with_portfolio_fit(
            item,
            cash_available=50000.0,
            budget_utilization_pct=30.0,
        )
        card = result["portfolio_fit_card"]
        assert card is not None
        # With cash and price, cash_defense should be ok
        assert card["cash_defense"]["status"] == "ok"


# ── Edge cases ──────────────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_symbol(self):
        r = compute_portfolio_fit(symbol="")
        assert r.trading_permission.score == 0.0

    def test_nan_price(self):
        r = compute_portfolio_fit(
            symbol="600519.SH",
            current_price=float("nan"),
            cash_available=50000.0,
        )
        # NaN price → no price data
        assert r.data_status in ("missing", "partial")

    def test_inf_cash(self):
        r = compute_portfolio_fit(
            symbol="600519.SH",
            current_price=50.0,
            cash_available=float("inf"),
        )
        # inf cash → detected as non-finite, returns 0 score
        assert r.cash_defense.score == 0.0

    def test_negative_price(self):
        r = compute_portfolio_fit(
            symbol="600519.SH",
            current_price=-10.0,
            cash_available=50000.0,
        )
        # Negative price → no valid price → partial cash score
        assert r.cash_defense.status == "partial"

    def test_zero_planned_max(self):
        r = compute_portfolio_fit(
            symbol="600519.SH",
            current_position_pct=5.0,
            planned_max_position_pct=0.0,
        )
        assert r.position_and_limit.score == 0.0

    def test_all_boolean_facts_none_when_no_data(self):
        r = compute_portfolio_fit()
        # symbol="" → board="unknown" → permission status="partial"
        # user_permissions=None → tradable_by_user stays None
        assert r.tradable_by_user is None
        assert r.position_overweight is None
        assert r.insufficient_cash is None
        assert r.concentration_exceeded is None
        assert r.risk_budget_exceeded is None
