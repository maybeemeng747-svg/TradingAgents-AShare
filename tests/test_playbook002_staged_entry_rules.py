# [PLAYBOOK-002] staged_entry_rules
"""Tests for PLAYBOOK-002: 计划仓位上限与上车三笔法规则引擎.

Covers:
- Asset type classification (ETF / blue_chip / theme / unverified / high_vol / unknown)
- Planned max position by type + override + absolute cap
- Tranche sizing (1/3 each)
- Trial lot: eligible / forbidden (multiple scenarios)
- Confirm lot: eligible / not eligible (evidence categories, prerequisite chain)
- Attack lot: pullback / breakout / forbidden
- allow_add: over position / under position / unknown
- allow_replenish: limit-down / volume breakdown / sector retreat / first big drop
- allow_chase: blowoff top / normal
- Forbidden keyword detection ("跌了/便宜/回调" alone cannot trigger add)
- Main orchestrator (apply_staged_entry_rules)
- Contract integration (apply_rules_to_contract)
- Edge cases: None inputs, missing data, infinite values, zero position
- Safety: no strong action words in any output text
"""

from __future__ import annotations

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.tradeflow.playbook_contract import (
    FORBIDDEN_STRONG_WORDS,
    PlaybookContract,
    validate_playbook_contract_safety,
)
from tradingagents.tradeflow.staged_entry_rules import (
    ABSOLUTE_MAX_POSITION_PCT,
    ASSET_TYPE_BLUE_CHIP,
    ASSET_TYPE_ETF,
    ASSET_TYPE_HIGH_VOLATILITY,
    ASSET_TYPE_THEME,
    ASSET_TYPE_UNKNOWN,
    ASSET_TYPE_UNVERIFIED,
    DEFAULT_MAX_POSITION_BY_TYPE,
    DEFAULT_MAX_POSITION_MIDPOINT,
    ALL_ASSET_TYPES,
    StagedEntryContext,
    StagedEntryResult,
    apply_rules_to_contract,
    apply_staged_entry_rules,
    check_allow_add,
    check_allow_chase,
    check_allow_replenish,
    classify_asset_type,
    compute_planned_max_position,
    compute_tranche_sizes,
    detect_forbidden_replenish_keywords,
    evaluate_attack_lot,
    evaluate_confirm_lot,
    evaluate_trial_lot,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_trial_context(**overrides) -> StagedEntryContext:
    """试错仓基础 fixture: 投资假设清晰、非极端高位、板块未退潮."""
    defaults = dict(
        symbol="600001",
        name="测试股票",
        strategy_tags=["题材"],
        current_price=10.0,
        entry_low=9.0,
        entry_high=11.0,
        invalid_price=9.3,
        investment_thesis_clear=True,
        sector_retreat=False,
        volume_blowoff_top=False,
        limit_down=False,
    )
    defaults.update(overrides)
    return StagedEntryContext(**defaults)


def _make_confirm_context(**overrides) -> StagedEntryContext:
    """确认仓基础 fixture: 试错仓已建且成功、三类证据充足."""
    defaults = dict(
        symbol="600001",
        name="测试股票",
        strategy_tags=["龙头"],
        current_price=10.5,
        trial_lot_built=True,
        trial_lot_succeeded=True,
        industry_evidence_score=4.0,
        earnings_validation_score=3.5,
        fund_confirmation_score=4.0,
    )
    defaults.update(overrides)
    return StagedEntryContext(**defaults)


def _make_attack_context(**overrides) -> StagedEntryContext:
    """进攻仓基础 fixture: 确认仓已加、回踩缩量不破支撑."""
    defaults = dict(
        symbol="600001",
        name="测试股票",
        strategy_tags=["龙头"],
        current_price=11.0,
        trial_lot_built=True,
        trial_lot_succeeded=True,
        confirm_lot_added=True,
        pullback_shrink_volume=True,
        above_support=True,
        first_big_drop=False,
        limit_down=False,
        volume_breakdown=False,
        sector_retreat=False,
    )
    defaults.update(overrides)
    return StagedEntryContext(**defaults)


# ── Asset Type Classification ─────────────────────────────────────────────────

class TestClassifyAssetType:
    def test_etf(self):
        ctx = StagedEntryContext(strategy_tags=["ETF", "宽基"])
        assert classify_asset_type(ctx) == ASSET_TYPE_ETF

    def test_blue_chip(self):
        ctx = StagedEntryContext(strategy_tags=["龙头", "蓝筹"])
        assert classify_asset_type(ctx) == ASSET_TYPE_BLUE_CHIP

    def test_theme(self):
        ctx = StagedEntryContext(strategy_tags=["题材", "弹性"])
        assert classify_asset_type(ctx) == ASSET_TYPE_THEME

    def test_unverified(self):
        ctx = StagedEntryContext(strategy_tags=["未验证", "试错"])
        assert classify_asset_type(ctx) == ASSET_TYPE_UNVERIFIED

    def test_high_volatility(self):
        ctx = StagedEntryContext(strategy_tags=["高波动", "监管风险"])
        assert classify_asset_type(ctx) == ASSET_TYPE_HIGH_VOLATILITY

    def test_st_tag(self):
        ctx = StagedEntryContext(strategy_tags=["ST", "退市风险"])
        assert classify_asset_type(ctx) == ASSET_TYPE_HIGH_VOLATILITY

    def test_unknown_empty_tags(self):
        ctx = StagedEntryContext(strategy_tags=[])
        assert classify_asset_type(ctx) == ASSET_TYPE_UNKNOWN

    def test_unknown_no_tags_field(self):
        ctx = StagedEntryContext()
        assert classify_asset_type(ctx) == ASSET_TYPE_UNKNOWN

    def test_override_asset_type(self):
        ctx = StagedEntryContext(strategy_tags=["题材"], asset_type=ASSET_TYPE_ETF)
        assert classify_asset_type(ctx) == ASSET_TYPE_ETF

    def test_invalid_override_falls_back(self):
        ctx = StagedEntryContext(strategy_tags=["龙头"], asset_type="bogus")
        assert classify_asset_type(ctx) == ASSET_TYPE_BLUE_CHIP

    def test_priority_etf_over_others(self):
        ctx = StagedEntryContext(strategy_tags=["龙头", "ETF"])
        assert classify_asset_type(ctx) == ASSET_TYPE_ETF

    def test_priority_high_vol_over_theme(self):
        ctx = StagedEntryContext(strategy_tags=["题材", "高波动"])
        assert classify_asset_type(ctx) == ASSET_TYPE_HIGH_VOLATILITY


# ── Max Position ──────────────────────────────────────────────────────────────

class TestComputePlannedMaxPosition:
    def test_all_types_have_default(self):
        for asset_type in ALL_ASSET_TYPES:
            pct = compute_planned_max_position(asset_type)
            assert 0 < pct <= ABSOLUTE_MAX_POSITION_PCT, f"{asset_type} => {pct}"

    @pytest.mark.parametrize("asset_type,lo_hi", DEFAULT_MAX_POSITION_BY_TYPE.items())
    def test_default_is_midpoint(self, asset_type, lo_hi):
        expected = (lo_hi[0] + lo_hi[1]) / 2.0
        assert compute_planned_max_position(asset_type) == pytest.approx(expected)

    def test_override_within_range(self):
        pct = compute_planned_max_position(ASSET_TYPE_THEME, override_pct=12.0)
        assert pct == pytest.approx(12.0)

    def test_override_above_absolute_cap(self):
        pct = compute_planned_max_position(ASSET_TYPE_THEME, override_pct=99.0)
        assert pct == pytest.approx(ABSOLUTE_MAX_POSITION_PCT)

    def test_override_zero(self):
        pct = compute_planned_max_position(ASSET_TYPE_THEME, override_pct=0.0)
        assert pct == pytest.approx(0.0)

    def test_override_negative_clamps_to_zero(self):
        pct = compute_planned_max_position(ASSET_TYPE_THEME, override_pct=-5.0)
        assert pct == pytest.approx(0.0)

    def test_override_nan_uses_default(self):
        pct = compute_planned_max_position(ASSET_TYPE_THEME, override_pct=float("nan"))
        assert pct == pytest.approx(DEFAULT_MAX_POSITION_MIDPOINT[ASSET_TYPE_THEME])

    def test_override_inf_uses_default(self):
        pct = compute_planned_max_position(ASSET_TYPE_THEME, override_pct=float("inf"))
        assert pct == pytest.approx(DEFAULT_MAX_POSITION_MIDPOINT[ASSET_TYPE_THEME])

    def test_etf_range(self):
        lo, hi = DEFAULT_MAX_POSITION_BY_TYPE[ASSET_TYPE_ETF]
        assert lo == 10.0
        assert hi == 20.0

    def test_high_vol_range(self):
        lo, hi = DEFAULT_MAX_POSITION_BY_TYPE[ASSET_TYPE_HIGH_VOLATILITY]
        assert lo == 1.0
        assert hi == 5.0

    def test_absolute_cap_never_exceeded(self):
        for asset_type in ALL_ASSET_TYPES:
            pct = compute_planned_max_position(asset_type)
            assert pct <= ABSOLUTE_MAX_POSITION_PCT


# ── Tranche Sizing ────────────────────────────────────────────────────────────

class TestComputeTrancheSizes:
    def test_three_equal_parts(self):
        t, c, a = compute_tranche_sizes(15.0)
        assert t == pytest.approx(5.0)
        assert c == pytest.approx(5.0)
        assert a == pytest.approx(5.0)

    def test_sum_equals_planned(self):
        planned = 10.0
        t, c, a = compute_tranche_sizes(planned)
        assert t + c + a == pytest.approx(planned)

    def test_zero_planned(self):
        t, c, a = compute_tranche_sizes(0.0)
        assert t == c == a == 0.0

    def test_negative_planned(self):
        t, c, a = compute_tranche_sizes(-5.0)
        assert t == c == a == 0.0

    def test_nan_planned(self):
        t, c, a = compute_tranche_sizes(float("nan"))
        assert t == c == a == 0.0

    def test_small_position(self):
        t, c, a = compute_tranche_sizes(3.0)
        assert t == pytest.approx(1.0)
        assert c == pytest.approx(1.0)
        assert a == pytest.approx(1.0)


# ── Trial Lot ─────────────────────────────────────────────────────────────────

class TestEvaluateTrialLot:
    def test_eligible_basic(self):
        ctx = _make_trial_context()
        eligible, reason, forbidden, f_reason = evaluate_trial_lot(ctx)
        assert eligible is True
        assert forbidden is False
        assert "投资假设清晰" in reason

    def test_forbidden_no_thesis(self):
        ctx = _make_trial_context(investment_thesis_clear=False)
        eligible, reason, forbidden, f_reason = evaluate_trial_lot(ctx)
        assert eligible is False
        assert forbidden is True
        assert "纯传闻" in f_reason

    def test_forbidden_blowoff_top(self):
        ctx = _make_trial_context(volume_blowoff_top=True)
        eligible, reason, forbidden, f_reason = evaluate_trial_lot(ctx)
        assert eligible is False
        assert forbidden is True
        assert "爆量冲高回落" in f_reason

    def test_forbidden_sector_retreat(self):
        ctx = _make_trial_context(sector_retreat=True)
        eligible, reason, forbidden, f_reason = evaluate_trial_lot(ctx)
        assert eligible is False
        assert forbidden is True
        assert "板块" in f_reason and "退潮" in f_reason

    def test_forbidden_limit_down(self):
        ctx = _make_trial_context(limit_down=True)
        eligible, reason, forbidden, f_reason = evaluate_trial_lot(ctx)
        assert eligible is False
        assert forbidden is True
        assert "跌停" in f_reason

    def test_forbidden_extreme_high(self):
        ctx = _make_trial_context(current_price=15.0, entry_high=11.0)
        eligible, reason, forbidden, f_reason = evaluate_trial_lot(ctx)
        assert eligible is False
        assert "极端高位" in reason or "高位" in reason

    def test_already_built(self):
        ctx = _make_trial_context(trial_lot_built=True)
        eligible, reason, forbidden, f_reason = evaluate_trial_lot(ctx)
        assert eligible is False
        assert forbidden is False
        assert "已建" in reason

    def test_no_price_info_cannot_verify_downside(self):
        ctx = StagedEntryContext(investment_thesis_clear=True)
        eligible, reason, forbidden, f_reason = evaluate_trial_lot(ctx)
        assert eligible is False
        assert forbidden is True
        assert "最大下行" in reason

    def test_price_below_extreme_high(self):
        ctx = _make_trial_context(
            current_price=11.5,
            entry_high=11.0,
            invalid_price=10.7,
        )
        eligible, _, forbidden, _ = evaluate_trial_lot(ctx)
        assert eligible is True
        assert forbidden is False

    def test_price_below_extreme_high_boundary(self):
        # 11.0 * 1.15 = 12.65, just below should pass
        ctx = _make_trial_context(
            current_price=12.60,
            entry_high=11.0,
            invalid_price=11.8,
        )
        eligible, _, forbidden, _ = evaluate_trial_lot(ctx)
        assert eligible is True

    def test_price_just_above_extreme_high_boundary(self):
        # 11.0 * 1.15 = 12.65, above should fail
        ctx = _make_trial_context(current_price=12.70, entry_high=11.0)
        eligible, _, forbidden, _ = evaluate_trial_lot(ctx)
        assert eligible is False

    def test_downside_above_eight_percent_is_forbidden(self):
        ctx = _make_trial_context(current_price=10.0, invalid_price=9.19)
        eligible, reason, forbidden, f_reason = evaluate_trial_lot(ctx)
        assert eligible is False
        assert forbidden is True
        assert "8.1%" in reason
        assert "8.0% 上限" in f_reason

    def test_downside_at_eight_percent_is_allowed(self):
        ctx = _make_trial_context(current_price=10.0, invalid_price=9.2)
        eligible, reason, forbidden, _ = evaluate_trial_lot(ctx)
        assert eligible is True
        assert forbidden is False
        assert "8.0%" in reason

    def test_invalid_price_above_current_price_is_forbidden(self):
        ctx = _make_trial_context(current_price=10.0, invalid_price=10.1)
        eligible, reason, forbidden, f_reason = evaluate_trial_lot(ctx)
        assert eligible is False
        assert forbidden is True
        assert "失效价必须低于当前价" in reason
        assert f_reason == reason


# ── Confirm Lot ───────────────────────────────────────────────────────────────

class TestEvaluateConfirmLot:
    def test_eligible_two_categories(self):
        ctx = _make_confirm_context(
            industry_evidence_score=4.0,
            earnings_validation_score=3.5,
            fund_confirmation_score=None,
        )
        eligible, cats, reason = evaluate_confirm_lot(ctx)
        assert eligible is True
        assert cats == 2
        assert "2/3" in reason

    def test_eligible_three_categories(self):
        ctx = _make_confirm_context()
        eligible, cats, reason = evaluate_confirm_lot(ctx)
        assert eligible is True
        assert cats == 3
        assert "3/3" in reason

    def test_not_eligible_one_category(self):
        ctx = _make_confirm_context(
            industry_evidence_score=4.0,
            earnings_validation_score=None,
            fund_confirmation_score=None,
        )
        eligible, cats, reason = evaluate_confirm_lot(ctx)
        assert eligible is False
        assert cats == 1
        assert "不足" in reason

    def test_not_eligible_zero_categories(self):
        ctx = _make_confirm_context(
            industry_evidence_score=None,
            earnings_validation_score=None,
            fund_confirmation_score=None,
        )
        eligible, cats, reason = evaluate_confirm_lot(ctx)
        assert eligible is False
        assert cats == 0

    def test_not_eligible_low_scores(self):
        ctx = _make_confirm_context(
            industry_evidence_score=2.9,
            earnings_validation_score=2.5,
            fund_confirmation_score=2.0,
        )
        eligible, cats, reason = evaluate_confirm_lot(ctx)
        assert eligible is False
        assert cats == 0

    def test_not_built_trial_lot(self):
        ctx = _make_confirm_context(trial_lot_built=False)
        eligible, cats, reason = evaluate_confirm_lot(ctx)
        assert eligible is False
        assert "未建试错仓" in reason

    def test_trial_not_succeeded(self):
        ctx = _make_confirm_context(trial_lot_succeeded=False)
        eligible, cats, reason = evaluate_confirm_lot(ctx)
        assert eligible is False
        assert "试错仓未成功" in reason

    def test_already_added(self):
        ctx = _make_confirm_context(confirm_lot_added=True)
        eligible, cats, reason = evaluate_confirm_lot(ctx)
        assert eligible is False
        assert "已加确认仓" in reason


# ── Attack Lot ────────────────────────────────────────────────────────────────

class TestEvaluateAttackLot:
    def test_pullback_eligible(self):
        ctx = _make_attack_context()
        eligible, attack_type, reason = evaluate_attack_lot(ctx)
        assert eligible is True
        assert attack_type == "pullback"
        assert "回踩" in reason

    def test_breakout_eligible(self):
        ctx = _make_attack_context(
            pullback_shrink_volume=False,
            above_support=False,
            breakout_confirmed=True,
            sector_leader_sync=True,
            volume_blowoff_top=False,
        )
        eligible, attack_type, reason = evaluate_attack_lot(ctx)
        assert eligible is True
        assert attack_type == "breakout"
        assert "突破" in reason

    def test_not_eligible_no_confirm(self):
        ctx = _make_attack_context(confirm_lot_added=False)
        eligible, attack_type, reason = evaluate_attack_lot(ctx)
        assert eligible is False
        assert attack_type is None
        assert "未加确认仓" in reason

    def test_forbidden_limit_down(self):
        ctx = _make_attack_context(limit_down=True)
        eligible, attack_type, reason = evaluate_attack_lot(ctx)
        assert eligible is False
        assert "跌停" in reason

    def test_forbidden_volume_breakdown(self):
        ctx = _make_attack_context(volume_breakdown=True)
        eligible, attack_type, reason = evaluate_attack_lot(ctx)
        assert eligible is False
        assert "放量破位" in reason

    def test_forbidden_sector_retreat(self):
        ctx = _make_attack_context(sector_retreat=True)
        eligible, attack_type, reason = evaluate_attack_lot(ctx)
        assert eligible is False
        assert "退潮" in reason

    def test_pullback_first_big_drop_forbidden(self):
        ctx = _make_attack_context(first_big_drop=True)
        eligible, attack_type, reason = evaluate_attack_lot(ctx)
        assert eligible is False
        assert attack_type is None
        assert "第一次大跌" in reason

    def test_breakout_first_big_drop_forbidden(self):
        ctx = _make_attack_context(
            pullback_shrink_volume=False,
            above_support=False,
            breakout_confirmed=True,
            sector_leader_sync=True,
            first_big_drop=True,
        )
        eligible, attack_type, reason = evaluate_attack_lot(ctx)
        assert eligible is False
        assert attack_type is None
        assert "第一次大跌" in reason

    def test_breakout_blowoff_top_forbidden(self):
        ctx = _make_attack_context(
            pullback_shrink_volume=False,
            breakout_confirmed=True,
            sector_leader_sync=True,
            volume_blowoff_top=True,
        )
        eligible, attack_type, reason = evaluate_attack_lot(ctx)
        assert eligible is False

    def test_no_conditions_met(self):
        ctx = _make_attack_context(
            pullback_shrink_volume=False,
            above_support=False,
            breakout_confirmed=False,
            sector_leader_sync=False,
        )
        eligible, attack_type, reason = evaluate_attack_lot(ctx)
        assert eligible is False
        assert attack_type is None
        assert "不满足" in reason


# ── Allow Add ─────────────────────────────────────────────────────────────────

class TestCheckAllowAdd:
    def test_under_position(self):
        ctx = StagedEntryContext(
            planned_max_position_pct=15.0,
            current_position_pct=5.0,
        )
        allow, reason = check_allow_add(ctx)
        assert allow is True
        assert "5.0%" in reason
        assert "15.0%" in reason

    def test_at_position_limit(self):
        ctx = StagedEntryContext(
            planned_max_position_pct=15.0,
            current_position_pct=15.0,
        )
        allow, reason = check_allow_add(ctx)
        assert allow is False
        assert "≥" in reason

    def test_over_position(self):
        ctx = StagedEntryContext(
            planned_max_position_pct=15.0,
            current_position_pct=20.0,
        )
        allow, reason = check_allow_add(ctx)
        assert allow is False

    def test_no_planned_max(self):
        ctx = StagedEntryContext(current_position_pct=5.0)
        allow, reason = check_allow_add(ctx)
        assert allow is False
        assert "计划仓位未设置" in reason

    def test_no_current_position(self):
        ctx = StagedEntryContext(planned_max_position_pct=15.0)
        allow, reason = check_allow_add(ctx)
        assert allow is False
        assert "当前仓位未知" in reason

    def test_both_none(self):
        ctx = StagedEntryContext()
        allow, reason = check_allow_add(ctx)
        assert allow is False


# ── Allow Replenish ───────────────────────────────────────────────────────────

class TestCheckAllowReplenish:
    def test_all_clear(self):
        ctx = StagedEntryContext()
        allow, reason = check_allow_replenish(ctx)
        assert allow is True
        assert "无禁止" in reason

    def test_limit_down(self):
        ctx = StagedEntryContext(limit_down=True)
        allow, reason = check_allow_replenish(ctx)
        assert allow is False
        assert "跌停" in reason

    def test_volume_breakdown(self):
        ctx = StagedEntryContext(volume_breakdown=True)
        allow, reason = check_allow_replenish(ctx)
        assert allow is False
        assert "放量破位" in reason

    def test_sector_retreat(self):
        ctx = StagedEntryContext(sector_retreat=True)
        allow, reason = check_allow_replenish(ctx)
        assert allow is False
        assert "退潮" in reason

    def test_first_big_drop(self):
        ctx = StagedEntryContext(first_big_drop=True)
        allow, reason = check_allow_replenish(ctx)
        assert allow is False
        assert "第一次大跌" in reason

    def test_multiple_forbidden(self):
        ctx = StagedEntryContext(limit_down=True, volume_breakdown=True)
        allow, reason = check_allow_replenish(ctx)
        assert allow is False
        # First check in priority order is limit_down
        assert "跌停" in reason


# ── Allow Chase ───────────────────────────────────────────────────────────────

class TestCheckAllowChase:
    def test_normal(self):
        ctx = StagedEntryContext()
        allow, reason = check_allow_chase(ctx)
        assert allow is True

    def test_blowoff_top(self):
        ctx = StagedEntryContext(volume_blowoff_top=True)
        allow, reason = check_allow_chase(ctx)
        assert allow is False
        assert "爆量" in reason


# ── Forbidden Keyword Detection ──────────────────────────────────────────────

class TestDetectForbiddenReplenishKeywords:
    def test_none_text(self):
        assert detect_forbidden_replenish_keywords(None) == []

    def test_empty_text(self):
        assert detect_forbidden_replenish_keywords("") == []

    def test_clean_text(self):
        assert detect_forbidden_replenish_keywords("证据增强，确认仓资格") == []

    def test_detect_died(self):
        found = detect_forbidden_replenish_keywords("跌了就买")
        assert "跌了" in found

    def test_detect_pianyi(self):
        found = detect_forbidden_replenish_keywords("现在便宜了")
        assert "便宜" in found or "便宜了" in found

    def test_detect_huidiao(self):
        found = detect_forbidden_replenish_keywords("回调到位可以补")
        assert "回调" in found

    def test_detect_bu_cang(self):
        found = detect_forbidden_replenish_keywords("考虑补仓")
        assert "补仓" in found

    def test_detect_chao_di(self):
        found = detect_forbidden_replenish_keywords("抄底机会")
        assert "抄底" in found

    def test_detect_yue_die_yue_mai(self):
        found = detect_forbidden_replenish_keywords("越跌越买策略")
        assert "越跌越买" in found

    def test_no_false_positive_on_normal_text(self):
        text = "产业证据增强，业绩确认超预期，资金流入明显"
        found = detect_forbidden_replenish_keywords(text)
        assert found == []


# ── Safety: "跌了/便宜/回调" alone cannot trigger add ────────────────────────

class TestDropCheapPullbackSafety:
    """TASKS.md 验收方式: "跌了/便宜/回调" 单独出现不能触发补仓."""

    def test_drop_alone_no_add(self):
        ctx = StagedEntryContext(
            investment_thesis_clear=True,
            current_price=9.0,  # 比 entry_high 跌了
            entry_low=9.0,
            entry_high=11.0,
            planned_max_position_pct=15.0,
            current_position_pct=5.0,
        )
        result = apply_staged_entry_rules(ctx)
        # allow_add is based on position, not price
        # allow_replenish checks limit_down/volume_breakdown etc.
        # The key: price drop alone does not set any lot to eligible
        # without evidence
        assert result.confirm_lot_eligible is False
        assert result.attack_lot_eligible is False

    def test_cheap_alone_no_add(self):
        ctx = StagedEntryContext(
            investment_thesis_clear=True,
            current_price=8.0,  # "便宜"
            planned_max_position_pct=15.0,
            current_position_pct=5.0,
        )
        result = apply_staged_entry_rules(ctx)
        # 便宜 alone does not grant confirm or attack
        assert result.confirm_lot_eligible is False
        assert result.attack_lot_eligible is False

    def test_pullback_alone_no_add(self):
        ctx = StagedEntryContext(
            investment_thesis_clear=True,
            current_price=10.0,  # "回调"
            pullback_shrink_volume=False,
            above_support=False,
            confirm_lot_added=False,
            planned_max_position_pct=15.0,
            current_position_pct=5.0,
        )
        result = apply_staged_entry_rules(ctx)
        assert result.confirm_lot_eligible is False
        assert result.attack_lot_eligible is False


# ── Main Orchestrator ─────────────────────────────────────────────────────────

class TestApplyStagedEntryRules:
    def test_returns_staged_entry_result(self):
        ctx = _make_trial_context()
        result = apply_staged_entry_rules(ctx)
        assert isinstance(result, StagedEntryResult)

    def test_asset_type_populated(self):
        ctx = StagedEntryContext(strategy_tags=["ETF"])
        result = apply_staged_entry_rules(ctx)
        assert result.asset_type == ASSET_TYPE_ETF

    def test_planned_max_populated(self):
        ctx = StagedEntryContext(strategy_tags=["ETF"])
        result = apply_staged_entry_rules(ctx)
        assert result.planned_max_position_pct is not None
        assert result.planned_max_position_pct > 0

    def test_computed_default_plan_is_used_by_allow_add(self):
        ctx = _make_trial_context(
            strategy_tags=["ETF"],
            planned_max_position_pct=None,
            current_position_pct=5.0,
        )
        result = apply_staged_entry_rules(ctx)
        assert result.planned_max_position_pct == 15.0
        assert result.allow_add is True
        assert "15.0%" in result.allow_add_reason

    def test_computed_default_plan_blocks_position_at_cap(self):
        ctx = _make_trial_context(
            strategy_tags=["ETF"],
            planned_max_position_pct=None,
            current_position_pct=15.0,
        )
        result = apply_staged_entry_rules(ctx)
        assert result.planned_max_position_pct == 15.0
        assert result.allow_add is False

    def test_tranches_populated(self):
        ctx = StagedEntryContext(strategy_tags=["ETF"])
        result = apply_staged_entry_rules(ctx)
        assert result.trial_pct is not None
        assert result.confirm_pct is not None
        assert result.attack_pct is not None
        assert result.trial_pct > 0

    def test_trial_eligible_full_flow(self):
        ctx = _make_trial_context()
        result = apply_staged_entry_rules(ctx)
        assert result.trial_lot_eligible is True
        assert result.trial_lot_forbidden is False

    def test_trial_forbidden_full_flow(self):
        ctx = _make_trial_context(investment_thesis_clear=False)
        result = apply_staged_entry_rules(ctx)
        assert result.trial_lot_eligible is False
        assert result.trial_lot_forbidden is True

    def test_confirm_eligible_full_flow(self):
        ctx = _make_confirm_context()
        result = apply_staged_entry_rules(ctx)
        assert result.confirm_lot_eligible is True
        assert result.confirm_evidence_categories == 3

    def test_confirm_not_eligible_full_flow(self):
        ctx = _make_confirm_context(
            industry_evidence_score=2.0,
            earnings_validation_score=2.0,
            fund_confirmation_score=None,
        )
        result = apply_staged_entry_rules(ctx)
        assert result.confirm_lot_eligible is False

    def test_attack_pullback_full_flow(self):
        ctx = _make_attack_context()
        result = apply_staged_entry_rules(ctx)
        assert result.attack_lot_eligible is True
        assert result.attack_type == "pullback"

    def test_attack_breakout_full_flow(self):
        ctx = _make_attack_context(
            pullback_shrink_volume=False,
            above_support=False,
            breakout_confirmed=True,
            sector_leader_sync=True,
        )
        result = apply_staged_entry_rules(ctx)
        assert result.attack_lot_eligible is True
        assert result.attack_type == "breakout"

    def test_limit_down_no_replenish(self):
        ctx = _make_attack_context(limit_down=True)
        result = apply_staged_entry_rules(ctx)
        assert result.allow_replenish is False
        assert "跌停" in result.allow_replenish_reason

    def test_notes_populated_when_forbidden(self):
        ctx = _make_trial_context(investment_thesis_clear=False)
        result = apply_staged_entry_rules(ctx)
        assert result.notes != ""
        assert "禁止" in result.notes or "纯传闻" in result.notes

    def test_notes_empty_when_all_ok(self):
        ctx = _make_trial_context()
        result = apply_staged_entry_rules(ctx)
        # No replenish forbidden, trial allowed → notes may be empty
        assert result.forbidden_replenish_detected is False

    def test_empty_context_conservative(self):
        ctx = StagedEntryContext()
        result = apply_staged_entry_rules(ctx)
        assert result.trial_lot_eligible is False
        assert result.confirm_lot_eligible is False
        assert result.attack_lot_eligible is False
        assert result.allow_add is False


# ── Contract Integration ──────────────────────────────────────────────────────

class TestApplyRulesToContract:
    def test_creates_new_contract(self):
        ctx = _make_trial_context(
            planned_max_position_pct=15.0,
            current_position_pct=5.0,
        )
        contract, result = apply_rules_to_contract(ctx)
        assert isinstance(contract, PlaybookContract)
        assert isinstance(result, StagedEntryResult)
        assert contract.planned_max_position_pct is not None

    def test_preserves_existing_fields(self):
        existing = PlaybookContract(
            playbook_stage="observe",
            industry_evidence_score=4.0,
            investment_thesis="核心逻辑",
        )
        ctx = _make_trial_context(
            planned_max_position_pct=15.0,
            current_position_pct=5.0,
        )
        contract, result = apply_rules_to_contract(ctx, existing=existing)
        assert contract.playbook_stage == "observe"
        assert contract.industry_evidence_score == 4.0
        assert contract.investment_thesis == "核心逻辑"

    def test_updates_allow_fields(self):
        ctx = _make_trial_context(
            planned_max_position_pct=15.0,
            current_position_pct=5.0,
        )
        contract, result = apply_rules_to_contract(ctx)
        assert contract.allow_add is True
        assert contract.allow_replenish is True
        assert contract.allow_chase is True

    def test_over_position_sets_allow_add_false(self):
        ctx = _make_trial_context(
            planned_max_position_pct=10.0,
            current_position_pct=15.0,
        )
        contract, result = apply_rules_to_contract(ctx)
        assert contract.allow_add is False

    def test_safety_contract_no_strong_words(self):
        ctx = _make_trial_context()
        contract, result = apply_rules_to_contract(ctx)
        assert validate_playbook_contract_safety(contract) is True

    def test_safety_contract_with_forbidden(self):
        ctx = _make_trial_context(investment_thesis_clear=False)
        contract, result = apply_rules_to_contract(ctx)
        assert validate_playbook_contract_safety(contract) is True

    def test_notes_no_forbidden_replenish_keywords(self):
        ctx = _make_trial_context(investment_thesis_clear=False)
        contract, result = apply_rules_to_contract(ctx)
        # Notes should describe the restriction, not use forbidden keywords
        # to justify a buy
        keywords = detect_forbidden_replenish_keywords(contract.notes)
        assert keywords == []


# ── Edge Cases ────────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_none_context_all_none(self):
        ctx = StagedEntryContext()
        result = apply_staged_entry_rules(ctx)
        assert result.asset_type == ASSET_TYPE_UNKNOWN
        assert result.trial_lot_eligible is False
        assert result.confirm_lot_eligible is False
        assert result.attack_lot_eligible is False
        assert result.allow_add is False

    def test_infinite_current_price(self):
        ctx = StagedEntryContext(
            current_price=float("inf"),
            investment_thesis_clear=True,
        )
        result = apply_staged_entry_rules(ctx)
        # Should not crash
        assert isinstance(result, StagedEntryResult)

    def test_nan_current_price(self):
        ctx = StagedEntryContext(
            current_price=float("nan"),
            investment_thesis_clear=True,
        )
        result = apply_staged_entry_rules(ctx)
        assert isinstance(result, StagedEntryResult)

    def test_zero_position(self):
        ctx = StagedEntryContext(
            planned_max_position_pct=15.0,
            current_position_pct=0.0,
            investment_thesis_clear=True,
        )
        result = apply_staged_entry_rules(ctx)
        assert result.allow_add is True

    def test_all_scores_zero(self):
        ctx = _make_confirm_context(
            industry_evidence_score=0.0,
            earnings_validation_score=0.0,
            fund_confirmation_score=0.0,
        )
        result = apply_staged_entry_rules(ctx)
        assert result.confirm_lot_eligible is False
        assert result.confirm_evidence_categories == 0

    def test_all_scores_max(self):
        ctx = _make_confirm_context(
            industry_evidence_score=5.0,
            earnings_validation_score=5.0,
            fund_confirmation_score=5.0,
        )
        result = apply_staged_entry_rules(ctx)
        assert result.confirm_lot_eligible is True
        assert result.confirm_evidence_categories == 3

    def test_result_is_immutable_context(self):
        ctx = StagedEntryContext(symbol="600001")
        apply_staged_entry_rules(ctx)
        assert ctx.symbol == "600001"

    def test_deterministic_multiple_runs(self):
        ctx = _make_trial_context()
        r1 = apply_staged_entry_rules(ctx)
        r2 = apply_staged_entry_rules(ctx)
        assert r1.trial_lot_eligible == r2.trial_lot_eligible
        assert r1.planned_max_position_pct == r2.planned_max_position_pct
        assert r1.allow_add == r2.allow_add
        assert r1.allow_replenish == r2.allow_replenish


# ── All Asset Types Have Defaults ─────────────────────────────────────────────

class TestAssetTypeDefaults:
    def test_all_types_in_midpoint_dict(self):
        for asset_type in ALL_ASSET_TYPES:
            assert asset_type in DEFAULT_MAX_POSITION_MIDPOINT

    def test_all_types_in_range_dict(self):
        for asset_type in ALL_ASSET_TYPES:
            assert asset_type in DEFAULT_MAX_POSITION_BY_TYPE

    def test_ranges_ordered(self):
        for asset_type, (lo, hi) in DEFAULT_MAX_POSITION_BY_TYPE.items():
            assert lo <= hi, f"{asset_type}: {lo} > {hi}"

    def test_absolute_cap_respected_by_all_midpoints(self):
        for asset_type, mid in DEFAULT_MAX_POSITION_MIDPOINT.items():
            assert mid <= ABSOLUTE_MAX_POSITION_PCT, f"{asset_type} midpoint {mid} > cap"


# ── No Strong Action Words in Module Output ───────────────────────────────────

class TestNoStrongActionWords:
    def test_all_reason_texts_clean(self):
        """Scan all possible reason strings for forbidden strong words."""
        ctx = _make_trial_context()
        result = apply_staged_entry_rules(ctx)
        text_fields = [
            result.trial_lot_reason,
            result.trial_lot_forbidden_reason,
            result.confirm_lot_reason,
            result.attack_lot_reason,
            result.allow_add_reason,
            result.allow_replenish_reason,
            result.allow_chase_reason,
            result.notes,
        ]
        for text in text_fields:
            if not text:
                continue
            for word in FORBIDDEN_STRONG_WORDS:
                assert word not in text, f"Found '{word}' in: {text}"

    def test_contract_safety(self):
        """Contract from rules must pass safety validation."""
        ctx = _make_trial_context()
        contract, _ = apply_rules_to_contract(ctx)
        assert validate_playbook_contract_safety(contract) is True
