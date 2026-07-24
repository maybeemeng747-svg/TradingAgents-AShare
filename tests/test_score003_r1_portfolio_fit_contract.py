# [SCORE-003-R1] portfolio_fit_contract
"""Tests for SCORE-003-R1: portfolio_fit contract, unknown context & partial budget.

Covers the three R1 fixes:
  1. Account unknown → context_unknown=True when ANY core dimension
     (permission / cash / position / risk budget) is unknown.
  2. Risk budget amount (budget_utilization_pct) vs count (daily_new /
     tracking) compared within same unit, not flat-averaged across units.
  3. portfolio_fit normalized against total weight 1.0 (unknown contributes 0),
     so sparse context cannot inflate into a high fit score.

Plus the structured response contract: portfolio_fit_card is a typed model.
"""

from __future__ import annotations

import json

import pytest

from tradingagents.tradeflow.portfolio_fit_score import (
    PortfolioFitResult,
    _score_risk_budget,
    compute_portfolio_fit,
    portfolio_fit_to_dict,
)


# ── Fixtures ────────────────────────────────────────────────────────

def _full_account_kwargs() -> dict:
    """A fully-specified healthy account context."""
    return dict(
        symbol="600519.SH",
        current_price=50.0,
        user_permissions={"main": True},
        cash_available=100000.0,
        current_position_pct=0.0,
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
    )


# ── Unknown account context ─────────────────────────────────────────

class TestUnknownAccountContext:
    """[SCORE-003-R1] any single unknown core dimension → context_unknown."""

    def test_no_account_context_unknown(self) -> None:
        r = compute_portfolio_fit(symbol="600519.SH", current_price=50.0)
        assert r.context_unknown is True

    def test_unknown_cash_forces_unknown(self) -> None:
        """Even with permission + position + budget, missing cash → unknown."""
        kw = _full_account_kwargs()
        del kw["cash_available"]
        r = compute_portfolio_fit(**kw)
        assert r.context_unknown is True
        assert r.cash_defense.status == "unknown"

    def test_unknown_position_forces_unknown(self) -> None:
        kw = _full_account_kwargs()
        del kw["current_position_pct"]
        r = compute_portfolio_fit(**kw)
        assert r.context_unknown is True
        assert r.position_and_limit.status == "unknown"

    def test_unknown_risk_budget_forces_unknown(self) -> None:
        """Missing risk budget is now a core dimension → context_unknown."""
        kw = _full_account_kwargs()
        del kw["budget_utilization_pct"]
        del kw["daily_new_today"]
        del kw["tracking_count"]
        r = compute_portfolio_fit(**kw)
        assert r.context_unknown is True
        assert r.risk_budget.status == "unknown"

    def test_star_no_permission_unknown(self) -> None:
        """STAR board without user_permissions → permission 'unknown'."""
        r = compute_portfolio_fit(
            symbol="688256.SH", current_price=100.0,
            cash_available=100000.0, current_position_pct=0.0,
            planned_max_position_pct=10.0, budget_utilization_pct=30.0,
        )
        assert r.trading_permission.status == "unknown"
        assert r.context_unknown is True

    def test_star_missing_permission_key_is_unknown(self) -> None:
        """An incomplete permission map is unknown for the target board."""
        r = compute_portfolio_fit(
            symbol="688256.SH",
            current_price=100.0,
            user_permissions={},
            cash_available=100000.0,
            current_position_pct=0.0,
            planned_max_position_pct=10.0,
            budget_utilization_pct=30.0,
        )
        assert r.trading_permission.status == "unknown"
        assert r.context_unknown is True

    def test_full_context_not_unknown(self) -> None:
        """All core dimensions provided → context_unknown False."""
        r = compute_portfolio_fit(**_full_account_kwargs())
        assert r.context_unknown is False
        for dim in (r.trading_permission, r.position_and_limit, r.cash_defense,
                    r.risk_budget):
            assert dim.status == "ok"

    def test_main_board_no_permissions_still_ok(self) -> None:
        """Main board needs no permission; context known when other cores present."""
        r = compute_portfolio_fit(
            symbol="600519.SH", current_price=50.0,
            cash_available=50000.0, current_position_pct=0.0,
            planned_max_position_pct=15.0, budget_utilization_pct=30.0,
        )
        # main board: permission is ok even without user_permissions
        assert r.trading_permission.status == "ok"
        assert r.context_unknown is False

    def test_known_zero_cash_is_constrained_not_unknown(self) -> None:
        """An explicit zero balance is known account state, not missing data."""
        kw = _full_account_kwargs()
        kw["cash_available"] = 0.0
        r = compute_portfolio_fit(**kw)

        assert r.cash_defense.status == "ok"
        assert r.cash_defense.score == 0.0
        assert r.insufficient_cash is True
        assert r.context_unknown is False
        assert "无法新增仓位" in r.cash_defense.reasons[0]

    @pytest.mark.parametrize("invalid_cash", [-1.0, float("nan"), float("inf")])
    def test_invalid_cash_fails_closed(self, invalid_cash: float) -> None:
        """Corrupt cash values must remain unknown, not usable context."""
        kw = _full_account_kwargs()
        kw["cash_available"] = invalid_cash
        r = compute_portfolio_fit(**kw)

        assert r.cash_defense.status == "missing"
        assert r.cash_defense.source_fields == []
        assert r.cash_defense.missing_fields == ["cash_available"]
        assert r.insufficient_cash is None
        assert r.context_unknown is True
        assert r.data_status == "partial"


# ── Score normalization (no inflation) ──────────────────────────────

class TestScoreNormalization:
    """[SCORE-003-R1] portfolio_fit normalized against total weight, not available."""

    def test_unknown_context_not_inflated(self) -> None:
        """Sparse context must not produce a high fit score."""
        # Only permission (main board ok) + liquidity (price) present.
        r = compute_portfolio_fit(symbol="600519.SH", current_price=50.0)
        # Permission weight 0.20 + liquidity 0.15 = 0.35 of total weight usable,
        # rest contribute 0 → score must be well below a full account.
        assert r.portfolio_fit < 50.0
        full = compute_portfolio_fit(**_full_account_kwargs())
        assert r.portfolio_fit < full.portfolio_fit

    def test_missing_risk_budget_lowers_score(self) -> None:
        """Dropping risk budget must lower the composite (was previously free)."""
        full = compute_portfolio_fit(**_full_account_kwargs())
        kw = _full_account_kwargs()
        del kw["budget_utilization_pct"]
        del kw["daily_new_today"]
        del kw["tracking_count"]
        sparse = compute_portfolio_fit(**kw)
        assert sparse.portfolio_fit < full.portfolio_fit

    def test_zero_score_when_nothing_provided(self) -> None:
        r = compute_portfolio_fit()
        assert r.portfolio_fit == 0.0

    def test_score_in_range(self) -> None:
        for kw in [
            {},
            dict(symbol="600519.SH", current_price=50.0),
            _full_account_kwargs(),
        ]:
            r = compute_portfolio_fit(**kw)
            assert 0.0 <= r.portfolio_fit <= 100.0

    def test_full_account_scores_high(self) -> None:
        """Normal path: a healthy full account still scores high."""
        r = compute_portfolio_fit(**_full_account_kwargs())
        assert r.portfolio_fit >= 70.0
        assert r.data_status == "ok"


# ── Risk budget unit pairing ────────────────────────────────────────

class TestRiskBudgetUnitPairing:
    """[SCORE-003-R1] amount-budget vs count-budget not flat-averaged."""

    def test_amount_only_not_diluted_by_missing_count(self) -> None:
        """When only budget_utilization_pct is given, score reflects amount only."""
        # amount budget is comfortable (20% utilized → 90)
        score_amount, _, reasons, _ = _score_risk_budget(budget_utilization_pct=20.0)
        # Previously, flat average over [90] = 90. Grouped average also 90.
        assert score_amount == 90.0

    def test_count_only_not_diluted_by_missing_amount(self) -> None:
        """When only count budget is given, score reflects count only."""
        score_count, _, _, _ = _score_risk_budget(
            daily_new_today=0, daily_new_max=3,
            tracking_count=2, max_concurrent_tracking=5,
        )
        # Both count sub-scores are 80 → count_mean 80 → group_means=[80] → 80.
        assert score_count == 80.0

    def test_amount_and_count_averaged_as_groups(self) -> None:
        """Mixed amount + count: average the two group means, not flat average."""
        # amount=90 (util 20%), daily_new=80 (0/3), tracking=10 (5/5 full)
        # amount_mean = 90, count_mean = (80+10)/2 = 45 → group avg = 67.5
        score, _, reasons, _ = _score_risk_budget(
            budget_utilization_pct=20.0,
            daily_new_today=0, daily_new_max=3,
            tracking_count=5, max_concurrent_tracking=5,
        )
        # Flat average would be (90+80+10)/3 = 60; grouped = (90 + 45)/2 = 67.5
        assert score == 67.5
        assert score != 60.0  # explicitly different from flat average

    def test_exhausted_amount_dominates_within_amount_group(self) -> None:
        """Exhausted amount budget is 0 within its group, but count can still be ok."""
        score, _, _, _ = _score_risk_budget(
            budget_utilization_pct=100.0,  # amount = 0
            daily_new_today=0, daily_new_max=3,  # count = 80
            tracking_count=2, max_concurrent_tracking=5,  # count = 80
        )
        # amount_mean=0, count_mean=80 → group avg = 40
        assert score == 40.0

    def test_full_count_group_with_no_amount(self) -> None:
        """All count budgets comfortable but no amount → count group 80, amount absent."""
        score, src, _, missing = _score_risk_budget(
            daily_new_today=0, daily_new_max=3,
            tracking_count=1, max_concurrent_tracking=5,
        )
        # only count group present → 80
        assert score == 80.0
        assert "budget_utilization_pct" in missing

    def test_unit_pairing_no_regression_on_all_three(self) -> None:
        """All three comfortable → high score (regression guard)."""
        score, _, _, _ = _score_risk_budget(
            budget_utilization_pct=20.0,
            daily_new_today=0, daily_new_max=3,
            tracking_count=2, max_concurrent_tracking=5,
        )
        # amount=90, count=(80+80)/2=80 → (90+80)/2 = 85
        assert score >= 70.0


# ── Response contract ───────────────────────────────────────────────

class TestPortfolioFitSchemaContract:
    """[SCORE-003-R1] portfolio_fit_card is a structured model."""

    def test_card_validates_as_model(self) -> None:
        from api.tradeflow_schemas import PortfolioFitCard
        r = compute_portfolio_fit(**_full_account_kwargs())
        d = portfolio_fit_to_dict(r)
        card = PortfolioFitCard.model_validate(d)
        assert card.portfolio_fit == r.portfolio_fit
        assert card.context_unknown == r.context_unknown
        assert card.tradable_by_user == r.tradable_by_user

    def test_candidate_item_has_typed_portfolio_card(self) -> None:
        from api.tradeflow_schemas import PortfolioFitCard, TradeFlowCandidateItem
        from api.services.tradeflow_service import _enrich_candidate_with_portfolio_fit
        item = {
            "symbol": "600519.SH", "current_price": 50.0,
            "strategy_tags": ["龙头"], "current_position_pct": 0.0,
        }
        enriched = _enrich_candidate_with_portfolio_fit(
            item, cash_available=50000.0, budget_utilization_pct=30.0,
        )
        ci = TradeFlowCandidateItem.model_validate(enriched)
        assert isinstance(ci.portfolio_fit_card, PortfolioFitCard)

    def test_unknown_context_card_serializes(self) -> None:
        """Unknown context card round-trips through JSON."""
        r = compute_portfolio_fit(symbol="600519.SH", current_price=50.0)
        d = portfolio_fit_to_dict(r)
        s = json.dumps(d, ensure_ascii=False)
        back = json.loads(s)
        assert back["context_unknown"] is True
        assert back["portfolio_fit"] < 50.0

    def test_status_values_include_unknown(self) -> None:
        """Core dimensions can carry 'unknown' status in the dict."""
        r = compute_portfolio_fit(symbol="688256.SH", current_price=100.0)
        d = portfolio_fit_to_dict(r)
        statuses = {d[k]["status"] for k in (
            "trading_permission", "position_and_limit", "cash_defense", "risk_budget",
        )}
        assert "unknown" in statuses
