# [PLAYBOOK-002] staged_entry_rules
"""
计划仓位上限与上车三笔法规则引擎.

纯确定性规则，不调用 LLM / 数据库 / 网络。所有输入通过 ``StagedEntryContext``
数据类传入，输出通过 ``StagedEntryResult`` 数据类返回，并可直接填入
``PlaybookContract`` 字段。

核心规则
--------
1. **计划仓位上限**：按标的类型（ETF / 龙头 / 题材 / 试错 / 高波动）给默认
   上限，单票绝对上限 40%。
2. **三笔法**：计划仓位拆三等份（试错仓 / 确认仓 / 进攻仓），越确定越买，
   不是越跌越买。
3. **试错仓**：投资假设清晰 + 非极端高位 + 板块未退潮 + 可接受亏损。
4. **确认仓**：产业 / 业绩 / 资金三类证据至少两类增强。
5. **进攻仓**：确认仓之后，回踩缩量不破平台 或 突破放量站稳。
6. **禁止补仓**：跌停 / 放量破位 / 龙头退潮 时 allow_replenish = False。
7. **安全约束**："跌了 / 便宜 / 回调" 单独出现不能触发加仓。

参考契约：``docs/trade_playbook_lifecycle.md`` §3-4
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Dict, List, Optional, Tuple

from tradingagents.tradeflow.playbook_contract import (
    ATTACK_LOT_BREAKOUT,
    ATTACK_LOT_NONE,
    ATTACK_LOT_PULLBACK,
    CONFIRM_LOT_ELIGIBLE,
    CONFIRM_LOT_NONE,
    STAGE_CONFIRM,
    STAGE_TRIAL,
    TRIAL_LOT_NONE,
    PlaybookContract,
)

# [PLAYBOOK-002] staged_entry_rules
# ── 标的类型常量 ──────────────────────────────────────────────────────────────
ASSET_TYPE_ETF = "etf"
ASSET_TYPE_BLUE_CHIP = "blue_chip"         # 硬逻辑主板龙头
ASSET_TYPE_THEME = "theme"                 # 题材弹性股
ASSET_TYPE_UNVERIFIED = "unverified"       # 业绩未验证但想试错
ASSET_TYPE_HIGH_VOLATILITY = "high_vol"    # 高波动 / 监管风险 / 传闻票
ASSET_TYPE_UNKNOWN = "unknown"

ALL_ASSET_TYPES: List[str] = [
    ASSET_TYPE_ETF,
    ASSET_TYPE_BLUE_CHIP,
    ASSET_TYPE_THEME,
    ASSET_TYPE_UNVERIFIED,
    ASSET_TYPE_HIGH_VOLATILITY,
    ASSET_TYPE_UNKNOWN,
]

# ── 计划仓位上限（%，区间取中值作为默认） ────────────────────────────────────
# 对应 docs/trade_playbook_lifecycle.md §3
DEFAULT_MAX_POSITION_BY_TYPE: Dict[str, Tuple[float, float]] = {
    ASSET_TYPE_ETF: (10.0, 20.0),
    ASSET_TYPE_BLUE_CHIP: (10.0, 25.0),
    ASSET_TYPE_THEME: (5.0, 15.0),
    ASSET_TYPE_UNVERIFIED: (3.0, 10.0),
    ASSET_TYPE_HIGH_VOLATILITY: (1.0, 5.0),
    ASSET_TYPE_UNKNOWN: (3.0, 10.0),  # 保守默认
}

#: 默认取区间中值
DEFAULT_MAX_POSITION_MIDPOINT: Dict[str, float] = {
    k: (v[0] + v[1]) / 2.0 for k, v in DEFAULT_MAX_POSITION_BY_TYPE.items()
}

#: 单票绝对上限 (%)
ABSOLUTE_MAX_POSITION_PCT = 40.0

# ── 三笔法比例 ──────────────────────────────────────────────────────────────
TRANCHE_FRACTION = 1.0 / 3.0  # 每笔 1/3

# ── 连续暴涨后高位阈值 ──────────────────────────────────────────────────────
# 当前价 > entry_high * (1 + 此系数) 视为"极端高位"
EXTREME_HIGH_RATIO = 0.15  # 高于区间上限 15%

# ── 试错仓可接受亏损 ─────────────────────────────────────────────────────────
TRIAL_ACCEPTABLE_LOSS_PCT = 8.0  # 5%-8% 取上限

# ── 禁止补仓条件关键词（用于 reason 文本扫描） ──────────────────────────────
_FORBIDDEN_REPLENISH_KEYWORDS = (
    "跌了", "便宜", "回调", "下跌", "打折", "便宜了", "跌到位",
    "补仓", "抄底", "越跌越买",
)


# [PLAYBOOK-002] staged_entry_rules
@dataclass(frozen=True)
class StagedEntryContext:
    """三笔法规则引擎的输入上下文.

    所有字段默认 ``None``，表示"数据缺失"。规则引擎对缺失数据采取
    保守策略：缺失证据不计分，缺失价格不触发。
    """

    # ── 标的识别 ──
    symbol: Optional[str] = None
    name: Optional[str] = None
    strategy_tags: List[str] = field(default_factory=list)

    # ── 价格 ──
    current_price: Optional[float] = None
    entry_low: Optional[float] = None       # 入场区间下沿
    entry_high: Optional[float] = None      # 入场区间上沿
    recent_high: Optional[float] = None     # 近期高点
    recent_low: Optional[float] = None      # 近期低点
    support_price: Optional[float] = None   # 关键支撑位
    invalid_price: Optional[float] = None   # 失效价

    # ── 量价 ──
    volume_ratio: Optional[float] = None    # 量比（今日成交量 / 5日均量）
    volume_breakdown: bool = False          # 放量破位
    volume_shrink_pullback: bool = False    # 回调缩量
    volume_blowoff_top: bool = False        # 爆量冲高回落

    # ── 板块 ──
    sector_retreat: bool = False            # 板块明显退潮
    sector_leader_sync: bool = False        # 板块龙头同步走强
    limit_down: bool = False                # 跌停封死

    # ── 证据评分 (0-5) ──
    industry_evidence_score: Optional[float] = None
    earnings_validation_score: Optional[float] = None
    fund_confirmation_score: Optional[float] = None
    risk_pressure_score: Optional[float] = None

    # ── 仓位 ──
    current_position_pct: Optional[float] = None
    planned_max_position_pct: Optional[float] = None  # 若已知，直接使用
    asset_type: Optional[str] = None                   # 若已知，跳过推断

    # ── 试错仓状态 ──
    trial_lot_built: bool = False           # 已建试错仓
    trial_lot_succeeded: bool = False       # 试错仓成功

    # ── 确认仓状态 ──
    confirm_lot_added: bool = False         # 已加确认仓

    # ── 平台/趋势 ──
    above_support: bool = False             # 价格在支撑位之上
    below_entry_zone: bool = False          # 价格跌破入场区间下沿
    breakout_confirmed: bool = False        # 突破关键平台且站稳
    pullback_shrink_volume: bool = False    # 回调缩量（量价配合）
    first_big_drop: bool = False            # 第一次大跌

    # ── 投资假设 ──
    investment_thesis_clear: bool = False   # 投资假设清晰


# [PLAYBOOK-002] staged_entry_rules
@dataclass
class StagedEntryResult:
    """三笔法规则引擎的输出结果.

    所有字段可直接映射到 ``PlaybookContract``。
    """

    # ── 仓位计算 ──
    asset_type: str = ASSET_TYPE_UNKNOWN
    planned_max_position_pct: Optional[float] = None
    trial_pct: Optional[float] = None       # 试错仓计划占比
    confirm_pct: Optional[float] = None     # 确认仓计划占比
    attack_pct: Optional[float] = None      # 进攻仓计划占比

    # ── 试错仓 ──
    trial_lot_eligible: bool = False
    trial_lot_forbidden: bool = False
    trial_lot_reason: str = ""
    trial_lot_forbidden_reason: str = ""

    # ── 确认仓 ──
    confirm_lot_eligible: bool = False
    confirm_evidence_categories: int = 0
    confirm_lot_reason: str = ""

    # ── 进攻仓 ──
    attack_lot_eligible: bool = False
    attack_type: Optional[str] = None       # "pullback" / "breakout"
    attack_lot_reason: str = ""

    # ── 操作许可 ──
    allow_add: bool = False
    allow_add_reason: str = ""
    allow_replenish: bool = False
    allow_replenish_reason: str = ""
    allow_chase: bool = False
    allow_chase_reason: str = ""

    # ── 安全标记 ──
    forbidden_replenish_detected: bool = False
    forbidden_replenish_keywords: List[str] = field(default_factory=list)

    # ── Notes ──
    notes: str = ""


# [PLAYBOOK-002] staged_entry_rules
# ── 标的类型推断 ──────────────────────────────────────────────────────────────

def classify_asset_type(context: StagedEntryContext) -> str:
    """根据 strategy_tags 推断标的类型.

    优先级：ETF > 高波动 > 题材 > 龙头 > 未验证 > unknown.
    若 ``context.asset_type`` 已设置且合法，直接返回。
    """
    if context.asset_type and context.asset_type in ALL_ASSET_TYPES:
        return context.asset_type

    tags_lower = {t.lower() for t in context.strategy_tags}

    # ETF / 指数类
    if tags_lower & {"etf", "指数", "index", "宽基", "行业etf"}:
        return ASSET_TYPE_ETF

    # 高波动 / 监管风险 / 传闻票
    if tags_lower & {"高波动", "监管风险", "传闻", "st", "*st", "退市风险"}:
        return ASSET_TYPE_HIGH_VOLATILITY

    # 题材弹性股
    if tags_lower & {"题材", "弹性", "短线", "事件驱动", "政策题材", "概念"}:
        return ASSET_TYPE_THEME

    # 硬逻辑主板龙头
    if tags_lower & {"龙头", "主板龙头", "行业龙头", "蓝筹", "白马", "核心资产"}:
        return ASSET_TYPE_BLUE_CHIP

    # 业绩未验证
    if tags_lower & {"未验证", "试错", "业绩未验证", "新股", "次新"}:
        return ASSET_TYPE_UNVERIFIED

    return ASSET_TYPE_UNKNOWN


def compute_planned_max_position(
    asset_type: str,
    *,
    override_pct: Optional[float] = None,
) -> float:
    """计算计划最大仓位 (%).

    若 ``override_pct`` 已提供且在合法范围内，直接使用（尊重用户/系统配置）。
    否则取标的类型默认区间的中值，上限为 ``ABSOLUTE_MAX_POSITION_PCT``。
    """
    if override_pct is not None:
        if not math.isfinite(override_pct):
            return DEFAULT_MAX_POSITION_MIDPOINT.get(asset_type, 6.5)
        clamped = max(0.0, min(override_pct, ABSOLUTE_MAX_POSITION_PCT))
        return clamped

    mid = DEFAULT_MAX_POSITION_MIDPOINT.get(asset_type, 6.5)
    return min(mid, ABSOLUTE_MAX_POSITION_PCT)


def compute_tranche_sizes(
    planned_max_pct: float,
) -> Tuple[float, float, float]:
    """把计划仓位拆三等份（试错 / 确认 / 进攻）.

    返回 ``(trial_pct, confirm_pct, attack_pct)``，每份 = planned_max / 3.
    """
    if not math.isfinite(planned_max_pct) or planned_max_pct <= 0:
        return (0.0, 0.0, 0.0)
    tranche = planned_max_pct * TRANCHE_FRACTION
    return (tranche, tranche, tranche)


# [PLAYBOOK-002] staged_entry_rules
# ── 试错仓评估 ────────────────────────────────────────────────────────────────

def evaluate_trial_lot(context: StagedEntryContext) -> Tuple[bool, str, bool, str]:
    """评估试错仓资格.

    Returns:
        ``(eligible, reason, forbidden, forbidden_reason)``
    """
    # 已建试错仓，不需要重新评估资格
    if context.trial_lot_built:
        return (False, "已建试错仓", False, "")

    # ── 禁止条件（任一命中即禁止） ──
    # 1. 纯传闻，无公告、订单、产业证据
    if not context.investment_thesis_clear:
        return (False, "投资假设不清晰", True, "纯传闻，无公告、订单、产业证据")

    # 2. 连续涨停后高开追入
    if context.volume_blowoff_top:
        return (False, "爆量冲高回落", True, "当天爆量冲高回落，禁止试错")

    # 3. 板块明显退潮
    if context.sector_retreat:
        return (False, "板块退潮", True, "板块明显退潮，禁止试错")

    # 4. 跌停封死
    if context.limit_down:
        return (False, "跌停封死", True, "跌停封死，禁止试错")

    # ── 允许条件（全部满足才允许） ──
    reasons: List[str] = []

    # 1. 投资假设清晰（已检查）
    reasons.append("投资假设清晰")

    # 2. 当前价格不是连续暴涨后的极端高位
    if context.current_price is not None and context.entry_high is not None:
        extreme_threshold = context.entry_high * (1.0 + EXTREME_HIGH_RATIO)
        if context.current_price > extreme_threshold:
            return (False, "价格处于极端高位", False,
                    f"当前价 {context.current_price:.2f} > 入场上沿 "
                    f"{context.entry_high:.2f} × {1 + EXTREME_HIGH_RATIO:.0%}")
        reasons.append("非极端高位")
    else:
        reasons.append("价格信息不足，保守通过")

    # 3. 板块没有明显退潮（已检查）
    reasons.append("板块未退潮")

    # 4. 仓位足够小（通过三笔法自然保证）
    reasons.append("仓位通过三笔法控制")

    return (True, "；".join(reasons), False, "")


# [PLAYBOOK-002] staged_entry_rules
# ── 确认仓评估 ────────────────────────────────────────────────────────────────

def evaluate_confirm_lot(context: StagedEntryContext) -> Tuple[bool, int, str]:
    """评估确认仓资格.

    需要产业 / 业绩 / 资金三类证据中至少两类增强。

    Returns:
        ``(eligible, evidence_categories_met, reason)``
    """
    # 已加确认仓
    if context.confirm_lot_added:
        return (False, 0, "已加确认仓")

    # 必须先建试错仓
    if not context.trial_lot_built:
        return (False, 0, "未建试错仓，不可跳到确认仓")

    # 试错仓必须成功
    if not context.trial_lot_succeeded:
        return (False, 0, "试错仓未成功，不可加确认仓")

    # ── 三类证据评估 ──
    categories_met = 0
    category_details: List[str] = []

    # 1. 产业证据：必需环节、客户认证、订单/中标、产能投放、收入传导
    if context.industry_evidence_score is not None and context.industry_evidence_score >= 3.0:
        categories_met += 1
        category_details.append(f"产业证据({context.industry_evidence_score:.1f}/5)")

    # 2. 业绩确认：预告超预期、财报改善、毛利率改善、主营增长
    if context.earnings_validation_score is not None and context.earnings_validation_score >= 3.0:
        categories_met += 1
        category_details.append(f"业绩确认({context.earnings_validation_score:.1f}/5)")

    # 3. 资金确认：放量上涨未快速砸回、回调缩量、板块龙头同步走强
    if context.fund_confirmation_score is not None and context.fund_confirmation_score >= 3.0:
        categories_met += 1
        category_details.append(f"资金确认({context.fund_confirmation_score:.1f}/5)")

    if categories_met >= 2:
        return (True, categories_met,
                f"证据增强：{', '.join(category_details)}（{categories_met}/3 类）")
    else:
        detail = ', '.join(category_details) if category_details else "无有效证据"
        return (False, categories_met,
                f"证据不足：{detail}（{categories_met}/3 类，需 ≥2）")


# [PLAYBOOK-002] staged_entry_rules
# ── 进攻仓评估 ────────────────────────────────────────────────────────────────

def evaluate_attack_lot(context: StagedEntryContext) -> Tuple[bool, Optional[str], str]:
    """评估进攻仓资格.

    进攻仓必须建立在确认仓之后。分回踩进攻和突破进攻两种。

    Returns:
        ``(eligible, attack_type, reason)``
        ``attack_type``: ``"pullback"`` / ``"breakout"`` / ``None``
    """
    # 必须先加确认仓
    if not context.confirm_lot_added:
        return (False, None, "未加确认仓，不可跳到进攻仓")

    # ── 禁止条件 ──
    # 第一次大跌、跌停封死、放量破位、板块龙头集体退潮
    if context.limit_down:
        return (False, None, "跌停封死，禁止进攻")
    if context.volume_breakdown:
        return (False, None, "放量破位，禁止进攻")
    if context.sector_retreat:
        return (False, None, "板块龙头集体退潮，禁止进攻")

    # ── 回踩进攻 ──
    # 条件：逻辑已确认、上涨后回调、回调缩量、不破平台、再次放量转强、板块健康
    if (context.pullback_shrink_volume
            and context.above_support
            and not context.first_big_drop):
        return (True, "pullback",
                "回踩缩量不破支撑，板块健康，回踩进攻窗口")

    # ── 突破进攻 ──
    # 条件：逻辑已确认、板块同步走强、突破关键平台、放量但不爆量失控
    if (context.breakout_confirmed
            and context.sector_leader_sync
            and not context.volume_blowoff_top):
        return (True, "breakout",
                "突破关键平台，板块同步走强，突破进攻窗口")

    return (False, None, "不满足回踩或突破进攻条件")


# [PLAYBOOK-002] staged_entry_rules
# ── 操作许可 ──────────────────────────────────────────────────────────────────

def check_allow_add(context: StagedEntryContext) -> Tuple[bool, str]:
    """检查是否允许加仓.

    超过计划仓位时不得提示继续加仓，只能提示持有、减仓或等待确认。
    """
    if context.planned_max_position_pct is None:
        # 未设置计划仓位，无法判断；保守不允许
        return (False, "计划仓位未设置，保守不允许加仓")

    if context.current_position_pct is None:
        return (False, "当前仓位未知，保守不允许加仓")

    if context.current_position_pct >= context.planned_max_position_pct:
        return (False,
                f"当前仓位 {context.current_position_pct:.1f}% ≥ 计划上限 "
                f"{context.planned_max_position_pct:.1f}%，不得继续加仓")

    return (True,
            f"当前仓位 {context.current_position_pct:.1f}% < 计划上限 "
            f"{context.planned_max_position_pct:.1f}%，可继续加仓")


def check_allow_replenish(context: StagedEntryContext) -> Tuple[bool, str]:
    """检查是否允许补仓.

    禁止在第一次大跌、跌停封死、放量破位、板块龙头集体退潮时补仓。
    """
    # 跌停封死
    if context.limit_down:
        return (False, "跌停封死，禁止补仓")

    # 放量破位
    if context.volume_breakdown:
        return (False, "放量破位，禁止补仓")

    # 板块龙头集体退潮
    if context.sector_retreat:
        return (False, "板块龙头集体退潮，禁止补仓")

    # 第一次大跌
    if context.first_big_drop:
        return (False, "第一次大跌，禁止补仓")

    return (True, "无禁止条件")


def check_allow_chase(context: StagedEntryContext) -> Tuple[bool, str]:
    """检查是否允许追高.

    爆量冲高回落时禁止追入。
    """
    if context.volume_blowoff_top:
        return (False, "爆量冲高回落，禁止追入")
    return (True, "无禁止追高条件")


def detect_forbidden_replenish_keywords(text: Optional[str]) -> List[str]:
    """检测文本中的禁止补仓关键词.

    "跌了 / 便宜 / 回调" 单独出现不能触发补仓，此处用于扫描上游
    reason / notes 文本，发现时标记 ``forbidden_replenish_detected``。
    """
    if not text:
        return []
    found = [kw for kw in _FORBIDDEN_REPLENISH_KEYWORDS if kw in text]
    return found


# [PLAYBOOK-002] staged_entry_rules
# ── 主入口 ────────────────────────────────────────────────────────────────────

def apply_staged_entry_rules(context: StagedEntryContext) -> StagedEntryResult:
    """应用三笔法规则引擎，计算仓位、资格和操作许可.

    主入口函数。纯确定性，不修改 ``context``，返回新的 ``StagedEntryResult``。
    """
    result = StagedEntryResult()

    # ── 1. 标的类型 ──
    result.asset_type = classify_asset_type(context)

    # ── 2. 计划仓位上限 ──
    result.planned_max_position_pct = compute_planned_max_position(
        result.asset_type,
        override_pct=context.planned_max_position_pct,
    )

    # ── 3. 三笔法仓位拆分 ──
    result.trial_pct, result.confirm_pct, result.attack_pct = compute_tranche_sizes(
        result.planned_max_position_pct,
    )

    # ── 4. 试错仓 ──
    eligible, reason, forbidden, forbidden_reason = evaluate_trial_lot(context)
    result.trial_lot_eligible = eligible
    result.trial_lot_reason = reason
    result.trial_lot_forbidden = forbidden
    result.trial_lot_forbidden_reason = forbidden_reason

    # ── 5. 确认仓 ──
    eligible, cats, reason = evaluate_confirm_lot(context)
    result.confirm_lot_eligible = eligible
    result.confirm_evidence_categories = cats
    result.confirm_lot_reason = reason

    # ── 6. 进攻仓 ──
    eligible, attack_type, reason = evaluate_attack_lot(context)
    result.attack_lot_eligible = eligible
    result.attack_type = attack_type
    result.attack_lot_reason = reason

    # ── 7. 操作许可 ──
    allow, reason = check_allow_add(context)
    result.allow_add = allow
    result.allow_add_reason = reason

    allow, reason = check_allow_replenish(context)
    result.allow_replenish = allow
    result.allow_replenish_reason = reason

    allow, reason = check_allow_chase(context)
    result.allow_chase = allow
    result.allow_chase_reason = reason

    # ── 8. 安全扫描 ──
    # 扫描 reason 文本中是否存在"跌了/便宜/回调"等暗示"因为跌所以买"的关键词
    all_reasons = " ".join([
        result.trial_lot_reason,
        result.confirm_lot_reason,
        result.attack_lot_reason,
        result.allow_add_reason,
        result.allow_replenish_reason,
        result.allow_chase_reason,
    ])
    result.forbidden_replenish_keywords = detect_forbidden_replenish_keywords(all_reasons)
    result.forbidden_replenish_detected = len(result.forbidden_replenish_keywords) > 0

    # ── 9. Notes ──
    notes_parts: List[str] = []
    if result.trial_lot_forbidden:
        notes_parts.append(f"试错仓禁止：{result.trial_lot_forbidden_reason}")
    if not result.allow_replenish:
        notes_parts.append(f"禁止补仓：{result.allow_replenish_reason}")
    result.notes = "；".join(notes_parts) if notes_parts else ""

    return result


# [PLAYBOOK-002] staged_entry_rules
# ── 便捷入口：直接填充 PlaybookContract ──────────────────────────────────────

def apply_rules_to_contract(
    context: StagedEntryContext,
    *,
    existing: Optional[PlaybookContract] = None,
) -> Tuple[PlaybookContract, StagedEntryResult]:
    """应用规则并返回 ``(updated_contract, raw_result)``.

    若 ``existing`` 非空，在其基础上更新；否则创建新 contract。
    只更新本模块负责的字段，不覆盖上游已设置的评分/阶段。
    """
    result = apply_staged_entry_rules(context)

    if existing is not None:
        contract = PlaybookContract(
            playbook_stage=existing.playbook_stage,
            industry_evidence_score=existing.industry_evidence_score,
            earnings_validation_score=existing.earnings_validation_score,
            fund_confirmation_score=existing.fund_confirmation_score,
            risk_pressure_score=existing.risk_pressure_score,
            planned_max_position_pct=result.planned_max_position_pct,
            current_position_pct=existing.current_position_pct,
            unrealized_pnl_pct=existing.unrealized_pnl_pct,
            core_position_qty=existing.core_position_qty,
            tactical_position_qty=existing.tactical_position_qty,
            defensive_cash_required_pct=existing.defensive_cash_required_pct,
            trial_lot_status=existing.trial_lot_status,
            confirm_lot_status=existing.confirm_lot_status,
            attack_lot_status=existing.attack_lot_status,
            allow_add=result.allow_add,
            allow_replenish=result.allow_replenish,
            allow_chase=result.allow_chase,
            add_trigger=existing.add_trigger,
            reduce_trigger=existing.reduce_trigger,
            exit_trigger=existing.exit_trigger,
            investment_thesis=existing.investment_thesis,
            last_operation=existing.last_operation,
            next_action=existing.next_action,
            notes=result.notes or existing.notes,
            stage_updated_at=existing.stage_updated_at,
        )
    else:
        contract = PlaybookContract(
            planned_max_position_pct=result.planned_max_position_pct,
            allow_add=result.allow_add,
            allow_replenish=result.allow_replenish,
            allow_chase=result.allow_chase,
            notes=result.notes or None,
        )

    return contract, result
