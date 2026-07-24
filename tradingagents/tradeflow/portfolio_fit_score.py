# [SCORE-003] portfolio_fit_card
"""Portfolio Fit Score Card — pure deterministic scoring of whether a candidate
is suitable for the current account context.

Six sub-dimensions:
1. trading_permission    — 板块交易权限 (STAR/ChiNext/BSE board eligibility)
2. position_and_limit    — 仓位与上限 (current position vs planned max)
3. cash_defense          — 现金防守空间 (available cash vs min entry cost)
4. correlation_concentration — 相关性集中度 (same-topic/theme exposure)
5. liquidity_execution   — 流动性执行难度 (lot size, price level, spread)
6. risk_budget           — 账户风险预算 (budget utilization, daily limit)

[SCORE-003-R1] contract & unknown-context fixes:
- unknown_account_context: any core dimension (permission / cash / position /
  risk budget) whose critical input was not provided is marked "unknown";
  context_unknown is True if ANY core dimension is unknown/missing.
- risk_budget_unit_pairing: amount-budget (budget_utilization_pct) and
  count-budget (daily_new / tracking) are scored within same-unit groups and
  the group means averaged, instead of flat-averaging across incompatible units.
- score_normalization: portfolio_fit is normalized against total weight 1.0,
  so unknown/missing dimensions contribute 0 and cannot inflate a sparse
  context into a high fit score.

Design constraints:
- No LLM calls, no network, no prompts modification.
- Purely deterministic mapping from existing fields.
- Same inputs always produce the same output (stable/deterministic).
- Missing account context → context_unknown=True, never assumed "fit well".
- Score only describes fitness; hard vetoes are SCORE-004's job.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Board prefixes that require special trading permission in A-share market
_STAR_MARKET_PREFIXES = ("688", "689")  # 科创板 — requires ≥50万 + 24个月经验
_CHINEXT_PREFIXES = ("300", "301")       # 创业板 — requires ≥10万 + 24个月经验
_BSE_PREFIXES = ("8", "4")              # 北交所 — requires ≥50万

# Minimum lot sizes (shares per lot) by board
_LOT_SIZE_STAR = 200       # 科创板 200 股/手
_LOT_SIZE_CHINEXT = 100    # 创业板 100 股/手
_LOT_SIZE_MAIN = 100       # 主板 100 股/手
_LOT_SIZE_BSE = 1          # 北交所 1 股起

# Absolute max position % (same as staged_entry_rules.ABSOLUTE_MAX_POSITION_PCT)
_ABSOLUTE_MAX_POSITION_PCT = 40.0

# Default asset type position midpoints (from staged_entry_rules)
_ASSET_POSITION_MIDPOINTS = {
    "etf": 15.0,
    "blue_chip": 17.5,
    "theme": 10.0,
    "unverified": 6.5,
    "high_vol": 3.0,
    "unknown": 6.5,
}

# Risk budget thresholds
_BUDGET_UTILIZATION_HIGH = 80.0   # % — above this, risk budget is strained
_BUDGET_UTILIZATION_FULL = 100.0  # % — fully utilized

# Daily new position limits
_DAILY_NEW_SOFT_LIMIT = 3


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class PortfolioFitDimension:
    """Single sub-dimension of the portfolio fit score card."""
    score: float = 0.0
    weight: float = 0.0
    status: str = "missing"  # "ok" / "missing" / "unknown"
    source_fields: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)


@dataclass
class PortfolioFitResult:
    """Full portfolio fit score card."""
    portfolio_fit: float = 0.0
    data_status: str = "missing"  # "ok" / "partial" / "missing" / "unknown"
    trading_permission: PortfolioFitDimension = field(
        default_factory=lambda: PortfolioFitDimension(weight=0.20))
    position_and_limit: PortfolioFitDimension = field(
        default_factory=lambda: PortfolioFitDimension(weight=0.20))
    cash_defense: PortfolioFitDimension = field(
        default_factory=lambda: PortfolioFitDimension(weight=0.15))
    correlation_concentration: PortfolioFitDimension = field(
        default_factory=lambda: PortfolioFitDimension(weight=0.15))
    liquidity_execution: PortfolioFitDimension = field(
        default_factory=lambda: PortfolioFitDimension(weight=0.15))
    risk_budget: PortfolioFitDimension = field(
        default_factory=lambda: PortfolioFitDimension(weight=0.15))
    # Boolean/tristate facts for downstream consumers
    tradable_by_user: Optional[bool] = None
    position_overweight: Optional[bool] = None
    insufficient_cash: Optional[bool] = None
    concentration_exceeded: Optional[bool] = None
    risk_budget_exceeded: Optional[bool] = None
    context_unknown: bool = True


# ---------------------------------------------------------------------------
# Board inference helpers
# ---------------------------------------------------------------------------

def _infer_board_type(symbol: str) -> str:
    """Infer board type from symbol prefix.

    Returns: "star" / "chinext" / "bse" / "main" / "unknown"
    """
    sym = (symbol or "").strip()
    # Extract numeric prefix before any dot
    num_part = sym.split(".")[0] if "." in sym else sym
    if num_part.startswith(_STAR_MARKET_PREFIXES):
        return "star"
    if num_part.startswith(_CHINEXT_PREFIXES):
        return "chinext"
    if num_part.startswith(_BSE_PREFIXES):
        return "bse"
    if num_part and num_part[0].isdigit():
        return "main"
    return "unknown"


def _lot_size_for_board(board: str) -> int:
    if board == "star":
        return _LOT_SIZE_STAR
    if board == "chinext":
        return _LOT_SIZE_CHINEXT
    if board == "bse":
        return _LOT_SIZE_BSE
    return _LOT_SIZE_MAIN


# ---------------------------------------------------------------------------
# Dimension scorers
# ---------------------------------------------------------------------------

def _score_trading_permission(
    *,
    symbol: str = "",
    user_permissions: Optional[dict] = None,
) -> tuple[float, list[str], list[str], list[str]]:
    """交易权限: can user trade this board?

    user_permissions: optional dict like {"star": True, "chinext": True, "bse": False}
    When None (not provided), we cannot confirm → unknown.

    Returns (score, source_fields, reasons, missing_fields).
    """
    source_fields: list[str] = []
    reasons: list[str] = []
    missing_fields: list[str] = []

    board = _infer_board_type(symbol)
    source_fields.append("symbol")

    if board == "main":
        # Main board: no special permission needed
        source_fields.append("user_permissions")
        reasons.append("主板无需特殊权限")
        return 100.0, source_fields, reasons, missing_fields

    if board == "unknown":
        missing_fields.append("symbol")
        reasons.append("无法推断板块类型")
        return 0.0, source_fields, reasons, missing_fields

    # Board requires special permission
    board_names = {
        "star": "科创板",
        "chinext": "创业板",
        "bse": "北交所",
    }
    board_name = board_names.get(board, board)

    if user_permissions is None:
        missing_fields.append("user_permissions")
        reasons.append(f"{board_name}需专项权限，账户权限未知")
        return 0.0, source_fields, reasons, missing_fields

    source_fields.append("user_permissions")
    has_perm = user_permissions.get(board, None)
    if has_perm is True:
        reasons.append(f"已开通{board_name}权限")
        return 100.0, source_fields, reasons, missing_fields
    elif has_perm is False:
        reasons.append(f"未开通{board_name}权限")
        return 0.0, source_fields, reasons, missing_fields
    else:
        reasons.append(f"{board_name}权限状态未知")
        missing_fields.append(f"user_permissions[{board}]")
        return 0.0, source_fields, reasons, missing_fields


def _score_position_and_limit(
    *,
    current_position_pct: Optional[float] = None,
    planned_max_position_pct: Optional[float] = None,
    asset_type: str = "unknown",
) -> tuple[float, list[str], list[str], list[str]]:
    """仓位与上限: how much room before hitting position limit.

    Returns (score, source_fields, reasons, missing_fields).
    """
    source_fields: list[str] = []
    reasons: list[str] = []
    missing_fields: list[str] = []

    has_current = current_position_pct is not None
    has_planned = planned_max_position_pct is not None

    if not has_current and not has_planned:
        missing_fields.extend(["current_position_pct", "planned_max_position_pct"])
        reasons.append("持仓比例与计划上限均未知")
        return 0.0, source_fields, reasons, missing_fields

    # Default planned max from asset type if not provided
    if not has_planned:
        planned_max = _ASSET_POSITION_MIDPOINTS.get(asset_type, 6.5)
        missing_fields.append("planned_max_position_pct")
        reasons.append(f"使用{asset_type}默认上限{planned_max:.1f}%")
    else:
        planned_max = min(float(planned_max_position_pct), _ABSOLUTE_MAX_POSITION_PCT)
        source_fields.append("planned_max_position_pct")

    if planned_max <= 0:
        reasons.append("计划仓位上限为0，不可加仓")
        return 0.0, source_fields, reasons, missing_fields

    if not has_current:
        missing_fields.append("current_position_pct")
        reasons.append("当前仓位未知，无法评估空间")
        return 0.0, source_fields, reasons, missing_fields

    source_fields.extend(["current_position_pct", "planned_max_position_pct"])
    current = max(0.0, float(current_position_pct))

    if current >= planned_max:
        reasons.append(f"当前仓位{current:.1f}% ≥ 计划上限{planned_max:.1f}%")
        return 0.0, source_fields, reasons, missing_fields

    # Room ratio: how much headroom as % of planned max
    room_pct = (planned_max - current) / planned_max * 100.0
    if room_pct >= 80:
        score = 90.0
        reasons.append(f"仓位充裕(已用{current:.1f}%/{planned_max:.1f}%)")
    elif room_pct >= 50:
        score = 65.0
        reasons.append(f"仓位适中(已用{current:.1f}%/{planned_max:.1f}%)")
    elif room_pct >= 20:
        score = 35.0
        reasons.append(f"仓位偏重(已用{current:.1f}%/{planned_max:.1f}%)")
    else:
        score = 10.0
        reasons.append(f"仓位接近上限(已用{current:.1f}%/{planned_max:.1f}%)")

    return round(min(100.0, max(0.0, score)), 1), source_fields, reasons, missing_fields


def _score_cash_defense(
    *,
    cash_available: Optional[float] = None,
    current_price: Optional[float] = None,
    symbol: str = "",
) -> tuple[float, list[str], list[str], list[str]]:
    """现金防守空间: can the account afford at least one lot?

    Returns (score, source_fields, reasons, missing_fields).
    """
    source_fields: list[str] = []
    reasons: list[str] = []
    missing_fields: list[str] = []

    has_price = current_price is not None and current_price > 0

    if cash_available is None:
        missing_fields.append("cash_available")
        reasons.append("可用资金未知")
        return 0.0, source_fields, reasons, missing_fields

    cash = float(cash_available)
    if not math.isfinite(cash) or cash < 0:
        missing_fields.append("cash_available")
        reasons.append("可用资金异常")
        return 0.0, source_fields, reasons, missing_fields
    source_fields.append("cash_available")
    if cash == 0:
        reasons.append("可用资金为0，无法新增仓位")
        return 0.0, source_fields, reasons, missing_fields

    if not has_price:
        missing_fields.append("current_price")
        reasons.append(f"当前价格未知，可用资金{cash:.0f}元")
        # Can still give a partial score based on cash alone
        if cash >= 50000:
            return 60.0, source_fields, reasons, missing_fields
        elif cash >= 10000:
            return 40.0, source_fields, reasons, missing_fields
        elif cash >= 5000:
            return 25.0, source_fields, reasons, missing_fields
        else:
            return 10.0, source_fields, reasons, missing_fields

    source_fields.extend(["current_price", "symbol"])
    price = float(current_price)
    board = _infer_board_type(symbol)
    lot_size = _lot_size_for_board(board)
    min_entry_cost = price * lot_size

    if min_entry_cost <= 0:
        reasons.append("最低入场成本异常")
        return 0.0, source_fields, reasons, missing_fields

    # How many lots can the account afford?
    affordable_lots = cash / min_entry_cost

    if affordable_lots < 1.0:
        reasons.append(f"资金不足一手({lot_size}股×{price:.2f}={min_entry_cost:.0f}元)")
        return 0.0, source_fields, reasons, missing_fields
    elif affordable_lots < 2.0:
        reasons.append(f"仅够一手({min_entry_cost:.0f}元)，无加仓空间")
        score = 25.0
    elif affordable_lots < 5.0:
        reasons.append(f"可买{int(affordable_lots)}手，空间有限")
        score = 50.0
    elif affordable_lots < 10.0:
        reasons.append(f"可买{int(affordable_lots)}手，空间充裕")
        score = 75.0
    else:
        reasons.append(f"可买{int(affordable_lots)}手，资金充足")
        score = 90.0

    return round(min(100.0, max(0.0, score)), 1), source_fields, reasons, missing_fields


def _score_correlation_concentration(
    *,
    mandate_topic: str = "",
    holdings_topics: Optional[list[str]] = None,
    same_topic_holding_count: int = 0,
    concentration_per_topic_max: int = 3,
) -> tuple[float, list[str], list[str], list[str]]:
    """相关性集中度: how much same-theme exposure already exists.

    Returns (score, source_fields, reasons, missing_fields).
    """
    source_fields: list[str] = []
    reasons: list[str] = []
    missing_fields: list[str] = []

    if not mandate_topic:
        missing_fields.append("mandate_topic")
        reasons.append("无主题标签，无法评估集中度")
        return 50.0, source_fields, reasons, missing_fields  # neutral, not penalized

    source_fields.append("mandate_topic")

    if holdings_topics is None:
        missing_fields.append("holdings_topics")
        reasons.append(f"主题={mandate_topic}，持仓主题未知")
        return 30.0, source_fields, reasons, missing_fields  # conservative

    source_fields.extend(["holdings_topics", "concentration_per_topic_max"])
    count = max(0, same_topic_holding_count)

    if count == 0:
        reasons.append(f"主题={mandate_topic}，持仓中无同主题暴露")
        score = 90.0
    elif count < concentration_per_topic_max:
        reasons.append(f"主题={mandate_topic}，已有{count}只同主题持仓")
        score = 60.0
    elif count == concentration_per_topic_max:
        reasons.append(f"主题={mandate_topic}，已达同主题上限({count}只)")
        score = 20.0
    else:
        reasons.append(f"主题={mandate_topic}，已超同主题上限({count}>{concentration_per_topic_max}只)")
        score = 0.0

    return round(min(100.0, max(0.0, score)), 1), source_fields, reasons, missing_fields


def _score_liquidity_execution(
    *,
    current_price: Optional[float] = None,
    avg_daily_volume: Optional[float] = None,
    bid_ask_spread_pct: Optional[float] = None,
    symbol: str = "",
) -> tuple[float, list[str], list[str], list[str]]:
    """流动性执行难度: how easy is it to enter/exit a position.

    Returns (score, source_fields, reasons, missing_fields).
    """
    source_fields: list[str] = []
    reasons: list[str] = []
    missing_fields: list[str] = []

    has_price = current_price is not None and current_price > 0
    has_volume = avg_daily_volume is not None and avg_daily_volume > 0
    has_spread = bid_ask_spread_pct is not None

    if not has_price and not has_volume:
        missing_fields.extend(["current_price", "avg_daily_volume"])
        reasons.append("价格与成交量均未知")
        return 0.0, source_fields, reasons, missing_fields

    components: list[float] = []

    # Price level component: very high price = harder to scale in
    if has_price:
        source_fields.append("current_price")
        price = float(current_price)
        if price <= 0:
            pass
        elif price >= 500:
            components.append(30.0)
            reasons.append(f"高价股({price:.0f}元)，一手成本高")
        elif price >= 100:
            components.append(60.0)
            reasons.append(f"中高价({price:.0f}元)")
        elif price >= 20:
            components.append(80.0)
            reasons.append(f"中价({price:.0f}元)")
        else:
            components.append(90.0)
            reasons.append(f"低价({price:.1f}元)，流动性通常较好")

    # Volume component
    if has_volume:
        source_fields.append("avg_daily_volume")
        vol = float(avg_daily_volume)
        if vol >= 1_000_000:
            components.append(90.0)
            reasons.append(f"日均成交量{vol/10000:.0f}万手，流动性充裕")
        elif vol >= 100_000:
            components.append(70.0)
            reasons.append(f"日均成交量{vol/10000:.1f}万手")
        elif vol >= 10_000:
            components.append(40.0)
            reasons.append(f"日均成交量{vol:.0f}手，流动性偏弱")
        else:
            components.append(15.0)
            reasons.append(f"日均成交量{vol:.0f}手，流动性差")
    else:
        missing_fields.append("avg_daily_volume")

    # Spread component
    if has_spread:
        source_fields.append("bid_ask_spread_pct")
        spread = float(bid_ask_spread_pct)
        if spread <= 0.1:
            components.append(90.0)
        elif spread <= 0.5:
            components.append(70.0)
        elif spread <= 1.0:
            components.append(50.0)
        else:
            components.append(20.0)
            reasons.append(f"买卖价差大({spread:.1f}%)")
    else:
        missing_fields.append("bid_ask_spread_pct")

    if not components:
        return 0.0, source_fields, reasons, missing_fields

    score = sum(components) / len(components)
    return round(min(100.0, max(0.0, score)), 1), source_fields, reasons, missing_fields


def _score_risk_budget(
    *,
    budget_utilization_pct: Optional[float] = None,
    daily_new_today: Optional[int] = None,
    daily_new_max: Optional[int] = None,
    tracking_count: Optional[int] = None,
    max_concurrent_tracking: Optional[int] = None,
) -> tuple[float, list[str], list[str], list[str]]:
    """账户风险预算: how much budget remains for new positions.

    [SCORE-003-R1] risk_budget_unit_pairing — 金额预算 (budget_utilization_pct)
    与 个数预算 (daily_new_today / tracking_count) 单位不同，不得直接混比平均。
    现按单位分组各自评分：
      - amount_components: 金额维度（budget_utilization_pct，0-100%）
      - count_components:  个数维度（daily_new_today、tracking_count）
    最终分 = 两组子均值的平均；仅有一组时取该组均值。这样同单位内成对比较，
    避免金额 vs 个数混比导致单一维度被放大或稀释。

    Returns (score, source_fields, reasons, missing_fields).
    """
    source_fields: list[str] = []
    reasons: list[str] = []
    missing_fields: list[str] = []

    has_budget = budget_utilization_pct is not None
    has_daily = daily_new_today is not None and daily_new_max is not None
    has_tracking = tracking_count is not None and max_concurrent_tracking is not None

    if not has_budget and not has_daily and not has_tracking:
        missing_fields.extend([
            "budget_utilization_pct", "daily_new_today", "tracking_count",
        ])
        reasons.append("风险预算数据未知")
        return 0.0, source_fields, reasons, missing_fields

    # [SCORE-003-R1] keep amount-budget (金额) and count-budget (个数) separate.
    amount_components: list[float] = []
    count_components: list[float] = []

    # Budget utilization (金额维度)
    if has_budget:
        source_fields.append("budget_utilization_pct")
        util = float(budget_utilization_pct)
        if util >= _BUDGET_UTILIZATION_FULL:
            amount_components.append(0.0)
            reasons.append(f"预算已耗尽({util:.0f}%)")
        elif util >= _BUDGET_UTILIZATION_HIGH:
            amount_components.append(20.0)
            reasons.append(f"预算紧张({util:.0f}%)")
        elif util >= 50.0:
            amount_components.append(60.0)
            reasons.append(f"预算中等({util:.0f}%)")
        else:
            amount_components.append(90.0)
            reasons.append(f"预算充裕({util:.0f}%)")
    else:
        missing_fields.append("budget_utilization_pct")

    # Daily new limit (个数维度)
    if has_daily:
        source_fields.extend(["daily_new_today", "daily_new_max"])
        today = max(0, int(daily_new_today))
        limit = max(1, int(daily_new_max))
        if today >= limit:
            count_components.append(0.0)
            reasons.append(f"今日已达新开仓上限({today}/{limit})")
        elif today >= limit - 1:
            count_components.append(30.0)
            reasons.append(f"今日仅剩{limit - today}个新开仓名额")
        else:
            count_components.append(80.0)
            reasons.append(f"今日新开仓{today}/{limit}")
    else:
        missing_fields.extend(["daily_new_today", "daily_new_max"])

    # Concurrent tracking limit (个数维度)
    if has_tracking:
        source_fields.extend(["tracking_count", "max_concurrent_tracking"])
        tc = max(0, int(tracking_count))
        mt = max(1, int(max_concurrent_tracking))
        if tc >= mt:
            count_components.append(10.0)
            reasons.append(f"跟踪标的已满({tc}/{mt})")
        elif tc >= mt * 0.8:
            count_components.append(40.0)
            reasons.append(f"跟踪标的接近上限({tc}/{mt})")
        else:
            count_components.append(80.0)
            reasons.append(f"跟踪标的{tc}/{mt}")
    else:
        missing_fields.extend(["tracking_count", "max_concurrent_tracking"])

    # [SCORE-003-R1] average within each unit group first, then average the
    # group means. This keeps amount-vs-count from being mixed in a single
    # flat average. When only one group is present, use that group's mean.
    group_means: list[float] = []
    if amount_components:
        group_means.append(sum(amount_components) / len(amount_components))
    if count_components:
        group_means.append(sum(count_components) / len(count_components))

    if not group_means:
        return 0.0, source_fields, reasons, missing_fields

    score = sum(group_means) / len(group_means)
    return round(min(100.0, max(0.0, score)), 1), source_fields, reasons, missing_fields


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def compute_portfolio_fit(
    *,
    # Candidate fields
    symbol: str = "",
    current_price: Optional[float] = None,
    asset_type: str = "unknown",
    strategy_tags: Optional[list[str]] = None,
    mandate_topic: str = "",
    avg_daily_volume: Optional[float] = None,
    bid_ask_spread_pct: Optional[float] = None,
    # User context
    user_permissions: Optional[dict] = None,
    cash_available: Optional[float] = None,
    current_position_pct: Optional[float] = None,
    # Portfolio context
    planned_max_position_pct: Optional[float] = None,
    holdings_topics: Optional[list[str]] = None,
    same_topic_holding_count: int = 0,
    concentration_per_topic_max: int = 3,
    # Risk budget context
    budget_utilization_pct: Optional[float] = None,
    daily_new_today: Optional[int] = None,
    daily_new_max: Optional[int] = None,
    tracking_count: Optional[int] = None,
    max_concurrent_tracking: Optional[int] = None,
) -> PortfolioFitResult:
    """Compute portfolio_fit score card from account and candidate context.

    Purely deterministic: same inputs always produce the same output.
    No LLM, no network, no prompts modification.

    Args:
        Candidate fields (symbol, price, asset type, strategy tags, topic,
        volume, spread) and account fields (permissions, cash, position,
        portfolio holdings, risk budget).

    Returns:
        PortfolioFitResult with portfolio_fit 0-100 and six sub-dimensions,
        each carrying status/source_fields/reasons/missing_fields.
        Boolean facts for downstream gate decisions.
    """
    # 1. Trading permission
    perm_score, perm_src, perm_reasons, perm_missing = _score_trading_permission(
        symbol=symbol,
        user_permissions=user_permissions,
    )

    # 2. Position and limit
    pos_score, pos_src, pos_reasons, pos_missing = _score_position_and_limit(
        current_position_pct=current_position_pct,
        planned_max_position_pct=planned_max_position_pct,
        asset_type=asset_type,
    )

    # 3. Cash defense
    cash_score, cash_src, cash_reasons, cash_missing = _score_cash_defense(
        cash_available=cash_available,
        current_price=current_price,
        symbol=symbol,
    )

    # 4. Correlation concentration
    conc_score, conc_src, conc_reasons, conc_missing = _score_correlation_concentration(
        mandate_topic=mandate_topic,
        holdings_topics=holdings_topics,
        same_topic_holding_count=same_topic_holding_count,
        concentration_per_topic_max=concentration_per_topic_max,
    )

    # 5. Liquidity execution
    liq_score, liq_src, liq_reasons, liq_missing = _score_liquidity_execution(
        current_price=current_price,
        avg_daily_volume=avg_daily_volume,
        bid_ask_spread_pct=bid_ask_spread_pct,
        symbol=symbol,
    )

    # 6. Risk budget
    rb_score, rb_src, rb_reasons, rb_missing = _score_risk_budget(
        budget_utilization_pct=budget_utilization_pct,
        daily_new_today=daily_new_today,
        daily_new_max=daily_new_max,
        tracking_count=tracking_count,
        max_concurrent_tracking=max_concurrent_tracking,
    )

    # Build dimension results
    def _dim_status(has_data: bool, missing: list[str]) -> str:
        if has_data and not missing:
            return "ok"
        if has_data:
            return "partial"
        return "missing"

    # [SCORE-003-R1] unknown_account_context — core dimensions whose critical
    # account input was not provided must be marked "unknown" rather than the
    # generic "missing", so context_unknown can fire on any single unknown core
    # dimension (permission / cash / position / risk budget).
    def _core_dim_status(has_data: bool, missing: list[str], critical_unknown: bool) -> str:
        if critical_unknown:
            return "unknown"
        return _dim_status(has_data, missing)

    # Permission is unknown when a special board requires it and the account
    # map is absent, omits that board, or carries a non-boolean placeholder.
    board = _infer_board_type(symbol)
    permission_known = (
        isinstance(user_permissions, dict)
        and isinstance(user_permissions.get(board), bool)
    )
    perm_critical_unknown = (
        board in ("star", "chinext", "bse")
        and not permission_known
    )
    perm_dim = PortfolioFitDimension(
        score=perm_score, weight=0.20,
        status=_core_dim_status(bool(perm_src), perm_missing, perm_critical_unknown),
        source_fields=perm_src, reasons=perm_reasons, missing_fields=perm_missing,
    )
    pos_dim = PortfolioFitDimension(
        score=pos_score, weight=0.20,
        status=_core_dim_status(bool(pos_src), pos_missing, current_position_pct is None),
        source_fields=pos_src, reasons=pos_reasons, missing_fields=pos_missing,
    )
    cash_dim = PortfolioFitDimension(
        score=cash_score, weight=0.15,
        status=_core_dim_status(bool(cash_src), cash_missing, cash_available is None),
        source_fields=cash_src, reasons=cash_reasons, missing_fields=cash_missing,
    )
    conc_dim = PortfolioFitDimension(
        score=conc_score, weight=0.15,
        status=_dim_status(bool(conc_src), conc_missing),
        source_fields=conc_src, reasons=conc_reasons, missing_fields=conc_missing,
    )
    liq_dim = PortfolioFitDimension(
        score=liq_score, weight=0.15,
        status=_dim_status(bool(liq_src), liq_missing),
        source_fields=liq_src, reasons=liq_reasons, missing_fields=liq_missing,
    )
    rb_critical_unknown = (
        budget_utilization_pct is None
        and daily_new_today is None
        and tracking_count is None
    )
    rb_dim = PortfolioFitDimension(
        score=rb_score, weight=0.15,
        status=_core_dim_status(bool(rb_src), rb_missing, rb_critical_unknown),
        source_fields=rb_src, reasons=rb_reasons, missing_fields=rb_missing,
    )

    # [SCORE-003-R1] score_normalization_to_total_weight — normalize the
    # composite against the TOTAL weight (1.0) instead of only the available
    # weight. Unknown / missing dimensions contribute 0, so a sparse context
    # can no longer be inflated into a high fit score by re-normalizing over
    # the surviving dimensions. Only dimensions with usable data ("ok" or
    # "partial") contribute their weighted score; everything else is treated
    # as 0.
    dimensions = [perm_dim, pos_dim, cash_dim, conc_dim, liq_dim, rb_dim]
    total_weight = sum(d.weight for d in dimensions)
    available_weight = sum(d.weight for d in dimensions if d.status in ("ok", "partial"))

    if available_weight <= 0:
        portfolio_fit = 0.0
        data_status = "missing"
    else:
        weighted_sum = sum(d.score * d.weight for d in dimensions if d.status in ("ok", "partial"))
        # Normalize against total weight so missing/unknown dims pull the
        # score down toward 0 rather than being re-normalized away.
        portfolio_fit = round(weighted_sum / total_weight, 1)
        if available_weight < total_weight:
            data_status = "partial"
        else:
            data_status = "ok"

    portfolio_fit = round(min(100.0, max(0.0, portfolio_fit)), 1)

    # Derive boolean facts
    # tradable_by_user: permission score > 0 means at least potentially tradable
    tradable_by_user: Optional[bool] = None
    if perm_dim.status == "ok":
        tradable_by_user = perm_score > 0
    elif user_permissions is not None:
        tradable_by_user = perm_score > 0

    # position_overweight: position score is 0 and we have data
    position_overweight: Optional[bool] = None
    if pos_dim.status == "ok":
        position_overweight = pos_score <= 0.0 and (
            current_position_pct is not None
            and planned_max_position_pct is not None
            and current_position_pct >= planned_max_position_pct
        )

    # insufficient_cash: cash score is 0 and we have price data
    insufficient_cash: Optional[bool] = None
    if cash_dim.status == "ok":
        insufficient_cash = cash_score <= 0.0

    # concentration_exceeded: concentration score is 0
    concentration_exceeded: Optional[bool] = None
    if conc_dim.status == "ok" and mandate_topic:
        concentration_exceeded = conc_score <= 0.0

    # risk_budget_exceeded: risk budget score is very low
    risk_budget_exceeded: Optional[bool] = None
    if rb_dim.status == "ok":
        risk_budget_exceeded = rb_score <= 10.0

    # [SCORE-003-R1] unknown_account_context — context is unknown whenever ANY
    # core account dimension (permission / cash / position / risk budget) is
    # "unknown" or "missing". Previously this only fired when ≥2 of permission /
    # position / cash were non-ok, which let a single missing critical input
    # (e.g. unknown cash) be interpreted as "fit well". risk_budget is now a
    # core dimension too.
    critical_dims = [perm_dim, pos_dim, cash_dim, rb_dim]
    context_unknown = any(d.status in ("unknown", "missing") for d in critical_dims)

    return PortfolioFitResult(
        portfolio_fit=portfolio_fit,
        data_status=data_status,
        trading_permission=perm_dim,
        position_and_limit=pos_dim,
        cash_defense=cash_dim,
        correlation_concentration=conc_dim,
        liquidity_execution=liq_dim,
        risk_budget=rb_dim,
        tradable_by_user=tradable_by_user,
        position_overweight=position_overweight,
        insufficient_cash=insufficient_cash,
        concentration_exceeded=concentration_exceeded,
        risk_budget_exceeded=risk_budget_exceeded,
        context_unknown=context_unknown,
    )


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _dim_to_dict(dim: PortfolioFitDimension) -> dict:
    return {
        "score": dim.score,
        "weight": dim.weight,
        "status": dim.status,
        "source_fields": dim.source_fields,
        "reasons": dim.reasons,
        "missing_fields": dim.missing_fields,
    }


def portfolio_fit_to_dict(result: PortfolioFitResult) -> dict:
    """Serialize PortfolioFitResult to a JSON-safe dict."""
    return {
        "portfolio_fit": result.portfolio_fit,
        "data_status": result.data_status,
        "trading_permission": _dim_to_dict(result.trading_permission),
        "position_and_limit": _dim_to_dict(result.position_and_limit),
        "cash_defense": _dim_to_dict(result.cash_defense),
        "correlation_concentration": _dim_to_dict(result.correlation_concentration),
        "liquidity_execution": _dim_to_dict(result.liquidity_execution),
        "risk_budget": _dim_to_dict(result.risk_budget),
        "tradable_by_user": result.tradable_by_user,
        "position_overweight": result.position_overweight,
        "insufficient_cash": result.insufficient_cash,
        "concentration_exceeded": result.concentration_exceeded,
        "risk_budget_exceeded": result.risk_budget_exceeded,
        "context_unknown": result.context_unknown,
    }
