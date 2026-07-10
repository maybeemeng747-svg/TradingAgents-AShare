# [PLAYBOOK-001] lifecycle_contract
"""
上车—在车上—下车战法字段契约与状态枚举.

定义统一的战法生命周期阶段、证据评分、仓位字段和触发条件，供跟踪看板、
观察仓、TA 报告和 investment-controller 共用。本模块只定义**契约和数据
结构**，不包含任何规则引擎逻辑（规则引擎由 PLAYBOOK-002/003 实现）。

设计原则
--------
1. **纯契约，无副作用**：纯标准库模块，不访问数据库、不联网、不写状态、
   不调用 LLM。便于单测与回放。
2. **全部可选 / 派生接入**：所有字段默认 ``None`` 或空值；旧数据（无
   playbook 字段）不会报错，也不会被误判为任何阶段。
3. **未知阶段绝不回退到 ``hold``**：``normalize_playbook_stage`` 对无法识别
   的值一律返回 ``None``，调用方必须显式区分"已持有"和"未知/未设置"。
   这是本契约最硬的安全约束（TASKS.md:502）。
4. **不输出强动作词**：所有枚举值和渲染文本不得包含"立即买入/重仓/清仓/
   满仓/梭哈/强烈推荐"等强动作词。
5. **不绕过现有动作语义**：playbook_stage 只细化阶段描述，不覆盖
   ``decision`` / ``execution_action`` / Buy Level / Risk Level / 强动作门禁。

参考契约：``docs/trade_playbook_lifecycle.md``
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
import math
from typing import Any, Dict, List, Optional

# [PLAYBOOK-001] lifecycle_contract
# ── 生命周期阶段枚举（字符串常量） ──────────────────────────────────────────
# 7 个阶段，对应 docs/trade_playbook_lifecycle.md §2
STAGE_OBSERVE = "observe"      # 观察：有逻辑线索，证据或位置不足
STAGE_TRIAL = "trial"          # 试错：逻辑像，仓位小，错了不伤账户
STAGE_CONFIRM = "confirm"      # 确认：产业/业绩/资金至少两类证据增强
STAGE_ATTACK = "attack"        # 进攻：已确认后出现高赔率窗口
STAGE_HOLD = "hold"            # 持有：已在车上，逻辑未证伪
STAGE_RISK = "risk"            # 风控：价格或逻辑出现风险暴露
STAGE_EXIT = "exit"            # 退出：投资假设证伪或重大风险

#: 全部合法阶段（有序，不代表优先级；优先级由规则引擎决定）
PLAYBOOK_STAGES: List[str] = [
    STAGE_OBSERVE,
    STAGE_TRIAL,
    STAGE_CONFIRM,
    STAGE_ATTACK,
    STAGE_HOLD,
    STAGE_RISK,
    STAGE_EXIT,
]

#: 阶段中文标签（供前端展示，不含强动作词）
PLAYBOOK_STAGE_LABELS: Dict[str, str] = {
    STAGE_OBSERVE: "观察",
    STAGE_TRIAL: "试错仓",
    STAGE_CONFIRM: "确认仓",
    STAGE_ATTACK: "进攻仓",
    STAGE_HOLD: "持有",
    STAGE_RISK: "风控",
    STAGE_EXIT: "退出",
}

# [PLAYBOOK-001] lifecycle_contract
# ── 三笔法仓位状态枚举 ──────────────────────────────────────────────────────
# 对应 docs/trade_playbook_lifecycle.md §4
TRIAL_LOT_NONE = "none"          # 未建试错仓
TRIAL_LOT_BUILT = "built"        # 已建试错仓
TRIAL_LOT_FAILED = "failed"      # 试错仓止损失败
TRIAL_LOT_SUCCEEDED = "succeeded"  # 试错仓成功，进入确认流程

CONFIRM_LOT_NONE = "none"          # 未到确认阶段
CONFIRM_LOT_ELIGIBLE = "eligible"  # 证据满足，可加确认仓
CONFIRM_LOT_ADDED = "added"        # 已加确认仓
CONFIRM_LOT_CANCELLED = "cancelled"  # 证据证伪，确认仓取消

ATTACK_LOT_NONE = "none"            # 未到进攻阶段
ATTACK_LOT_PULLBACK = "pullback"    # 回踩进攻窗口
ATTACK_LOT_BREAKOUT = "breakout"    # 突破进攻窗口
ATTACK_LOT_ADDED = "added"          # 已加进攻仓
ATTACK_LOT_RETREAT = "retreat"      # 进攻窗口撤退（未站稳/回落）

#: 各 lot_status 的合法值集合
TRIAL_LOT_STATUSES: List[str] = [
    TRIAL_LOT_NONE, TRIAL_LOT_BUILT, TRIAL_LOT_FAILED, TRIAL_LOT_SUCCEEDED,
]
CONFIRM_LOT_STATUSES: List[str] = [
    CONFIRM_LOT_NONE, CONFIRM_LOT_ELIGIBLE, CONFIRM_LOT_ADDED, CONFIRM_LOT_CANCELLED,
]
ATTACK_LOT_STATUSES: List[str] = [
    ATTACK_LOT_NONE, ATTACK_LOT_PULLBACK, ATTACK_LOT_BREAKOUT,
    ATTACK_LOT_ADDED, ATTACK_LOT_RETREAT,
]

# [PLAYBOOK-001] lifecycle_contract
# ── 评分上下界 ──────────────────────────────────────────────────────────────
SCORE_MIN = 0
SCORE_MAX = 5  # 证据评分 0-5（对应 docs §9 TA 输出契约 "x/5"）

# ── 仓位百分比上下界 ────────────────────────────────────────────────────────
POSITION_PCT_MIN = 0.0
POSITION_PCT_MAX = 100.0  # 百分比，0-100

# ── 强动作禁用词（供调用方 / 测试核验契约输出不含违规词） ──────────────────
FORBIDDEN_STRONG_WORDS: tuple = (
    "立即买入", "重仓买入", "重仓", "立即清仓", "清仓", "满仓", "梭哈", "强烈推荐",
)


# [PLAYBOOK-001] lifecycle_contract
@dataclass
class PlaybookContract:
    """战法生命周期字段契约.

    所有字段默认 ``None``，表示"未设置 / 派生未计算"。这与"显式 hold（已
    持有）"严格区分：调用方不能把 ``None`` 当作 ``hold``。

    字段分组：
      - **阶段**：``playbook_stage``
      - **证据评分**（0-5）：``industry_evidence_score`` /
        ``earnings_validation_score`` / ``fund_confirmation_score`` /
        ``risk_pressure_score``
      - **仓位**：``planned_max_position_pct`` / ``current_position_pct`` /
        ``core_position_qty`` / ``tactical_position_qty`` /
        ``defensive_cash_required_pct``
      - **三笔法状态**：``trial_lot_status`` / ``confirm_lot_status`` /
        ``attack_lot_status``
      - **操作许可**（布尔）：``allow_add`` / ``allow_replenish`` /
        ``allow_chase``
      - **触发条件**（文本）：``add_trigger`` / ``reduce_trigger`` /
        ``exit_trigger``
      - **补充**：``investment_thesis`` / ``last_operation`` / ``next_action``
        / ``notes``
    """

    # ── 阶段 ──
    playbook_stage: Optional[str] = None

    # ── 证据评分 (0-5) ──
    industry_evidence_score: Optional[float] = None
    earnings_validation_score: Optional[float] = None
    fund_confirmation_score: Optional[float] = None
    risk_pressure_score: Optional[float] = None

    # ── 仓位 ──
    planned_max_position_pct: Optional[float] = None
    current_position_pct: Optional[float] = None
    unrealized_pnl_pct: Optional[float] = None
    core_position_qty: Optional[int] = None
    tactical_position_qty: Optional[int] = None
    defensive_cash_required_pct: Optional[float] = None

    # ── 三笔法状态 ──
    trial_lot_status: Optional[str] = None
    confirm_lot_status: Optional[str] = None
    attack_lot_status: Optional[str] = None

    # ── 操作许可 ──
    allow_add: Optional[bool] = None
    allow_replenish: Optional[bool] = None
    allow_chase: Optional[bool] = None

    # ── 触发条件（文本说明） ──
    add_trigger: Optional[str] = None
    reduce_trigger: Optional[str] = None
    exit_trigger: Optional[str] = None

    # ── 补充字段 ──
    investment_thesis: Optional[str] = None
    last_operation: Optional[str] = None
    next_action: Optional[str] = None
    notes: Optional[str] = None

    # ── 阶段更新时间（可选，供回放和新鲜度判断） ──
    stage_updated_at: Optional[str] = None


# ── 序列化字段名集合（用于 from_dict 过滤未知键） ──────────────────────────
_PLAYBOOK_CONTRACT_FIELD_NAMES: frozenset = frozenset(
    f.name for f in fields(PlaybookContract)
)


def is_valid_playbook_stage(stage: Any) -> bool:
    """判断值是否为合法的 playbook 阶段.

    ``None`` / 空字符串返回 ``False``（合法但未设置）。
    """
    if stage is None:
        return False
    if not isinstance(stage, str):
        return False
    return stage in PLAYBOOK_STAGES


def normalize_playbook_stage(value: Any) -> Optional[str]:
    """将任意值归一化为合法阶段字符串或 ``None``.

    **安全约束**：无法识别的值一律返回 ``None``，绝不回退到 ``hold``。
    这防止旧数据或脏数据被误判为"已持有"阶段。

    大小写宽容：``"OBSERVE"`` / ``"Observe"`` 都映射到 ``"observe"``。
    前后空白会被 strip。
    """
    if value is None:
        return None
    if isinstance(value, str):
        candidate = value.strip().lower()
        if candidate in PLAYBOOK_STAGES:
            return candidate
    return None


def _clamp_score(value: Any) -> Optional[float]:
    """把证据评分 clamp 到 [0, 5]，None/非法值返回 None."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    if f < SCORE_MIN:
        return float(SCORE_MIN)
    if f > SCORE_MAX:
        return float(SCORE_MAX)
    return f


def _clamp_position_pct(value: Any) -> Optional[float]:
    """把仓位百分比 clamp 到 [0, 100]，None/非法值返回 None."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    if f < POSITION_PCT_MIN:
        return float(POSITION_PCT_MIN)
    if f > POSITION_PCT_MAX:
        return float(POSITION_PCT_MAX)
    return f


def _finite_float(value: Any) -> Optional[float]:
    """把有限数值转为 float；非法或非有限值返回 None."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return f if math.isfinite(f) else None


def _normalize_lot_status(value: Any, allowed: List[str]) -> Optional[str]:
    """把 lot_status 归一化为合法值或 None."""
    if value is None:
        return None
    if isinstance(value, str):
        candidate = value.strip().lower()
        if candidate in allowed:
            return candidate
    return None


def _normalize_bool(value: Any) -> Optional[bool]:
    """把宽松布尔值归一化为 True/False/None."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            return None
        if value == 1:
            return True
        if value == 0:
            return False
        return None
    if isinstance(value, str):
        candidate = value.strip().lower()
        if candidate in ("true", "1", "yes", "y"):
            return True
        if candidate in ("false", "0", "no", "n"):
            return False
    return None


def playbook_contract_from_dict(data: Optional[Dict[str, Any]]) -> PlaybookContract:
    """从 dict 构建 PlaybookContract，忽略未知键并安全归一化.

    缺字段返回 ``None`` 默认值；脏数据（非法阶段/分数超界/类型不匹配）
    被归一化或丢弃，绝不抛异常。这保证旧报告和旧观察仓数据不报错。
    """
    if not isinstance(data, dict):
        return PlaybookContract()

    stage = normalize_playbook_stage(data.get("playbook_stage"))

    return PlaybookContract(
        playbook_stage=stage,
        industry_evidence_score=_clamp_score(data.get("industry_evidence_score")),
        earnings_validation_score=_clamp_score(data.get("earnings_validation_score")),
        fund_confirmation_score=_clamp_score(data.get("fund_confirmation_score")),
        risk_pressure_score=_clamp_score(data.get("risk_pressure_score")),
        planned_max_position_pct=_clamp_position_pct(data.get("planned_max_position_pct")),
        current_position_pct=_clamp_position_pct(data.get("current_position_pct")),
        unrealized_pnl_pct=_finite_float(data.get("unrealized_pnl_pct")),
        core_position_qty=_safe_int(data.get("core_position_qty")),
        tactical_position_qty=_safe_int(data.get("tactical_position_qty")),
        defensive_cash_required_pct=_clamp_position_pct(data.get("defensive_cash_required_pct")),
        trial_lot_status=_normalize_lot_status(data.get("trial_lot_status"), TRIAL_LOT_STATUSES),
        confirm_lot_status=_normalize_lot_status(data.get("confirm_lot_status"), CONFIRM_LOT_STATUSES),
        attack_lot_status=_normalize_lot_status(data.get("attack_lot_status"), ATTACK_LOT_STATUSES),
        allow_add=_normalize_bool(data.get("allow_add")),
        allow_replenish=_normalize_bool(data.get("allow_replenish")),
        allow_chase=_normalize_bool(data.get("allow_chase")),
        add_trigger=_safe_str(data.get("add_trigger")),
        reduce_trigger=_safe_str(data.get("reduce_trigger")),
        exit_trigger=_safe_str(data.get("exit_trigger")),
        investment_thesis=_safe_str(data.get("investment_thesis")),
        last_operation=_safe_str(data.get("last_operation")),
        next_action=_safe_str(data.get("next_action")),
        notes=_safe_str(data.get("notes")),
        stage_updated_at=_safe_str(data.get("stage_updated_at")),
    )


def _safe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        numeric = float(value)
        if not math.isfinite(numeric):
            return None
        return int(numeric)
    except (TypeError, ValueError, OverflowError):
        return None


def _safe_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
        return s if s else None
    return str(value).strip() or None


def playbook_contract_to_dict(contract: PlaybookContract) -> Dict[str, Any]:
    """把 PlaybookContract 序列化为 JSON-safe dict.

    ``None`` 值保留为 ``None``（不转为空字符串），让消费方区分"未设置"
    和"显式空"。这是契约的向后兼容要求。
    """
    if not isinstance(contract, PlaybookContract):
        return {}
    result: Dict[str, Any] = {}
    for f in fields(contract):
        result[f.name] = getattr(contract, f.name)
    return result


def merge_playbook_contract_into_dict(
    target: Dict[str, Any],
    contract: Optional[PlaybookContract],
) -> Dict[str, Any]:
    """把 contract 字段合并进 target dict（返回新 dict）.

    只合并非 ``None`` 的字段，避免覆盖 target 中已有值。用于把 playbook
    字段附加到 observation item / report response dict。
    """
    if not isinstance(target, dict):
        target = {}
    else:
        target = dict(target)  # shallow copy
    if contract is None:
        return target
    contract_payload: Dict[str, Any] = {}
    for f in fields(contract):
        val = getattr(contract, f.name)
        if val is not None:
            if target.get(f.name) is None:
                target[f.name] = val
            contract_payload[f.name] = val
    if contract_payload:
        existing_payload = target.get("playbook_contract")
        if isinstance(existing_payload, dict):
            merged_payload = dict(existing_payload)
            for key, value in contract_payload.items():
                if merged_payload.get(key) is None:
                    merged_payload[key] = value
            target["playbook_contract"] = merged_payload
        else:
            target["playbook_contract"] = contract_payload
    return target


def playbook_contract_is_empty(contract: Optional[PlaybookContract]) -> bool:
    """判断 contract 是否完全未设置（所有字段为 None）.

    用于决定是否向前端透传 playbook 字段：空 contract 不应生成无意义的
    ``playbook_stage: null`` payload。
    """
    if contract is None:
        return True
    return all(getattr(contract, f.name) is None for f in fields(contract))


def playbook_stage_label(stage: Optional[str]) -> str:
    """返回阶段的中文标签；未知/未设置返回空字符串（不回退 hold）."""
    if not is_valid_playbook_stage(stage):
        return ""
    return PLAYBOOK_STAGE_LABELS.get(stage, "")


def playbook_contract_summary(contract: Optional[PlaybookContract]) -> Dict[str, Any]:
    """生成精简的 playbook 摘要 dict（用于 API response 顶层透传）.

    只包含有意义的字段，空 contract 返回 ``{}``。包含：
      - ``playbook_stage`` + ``playbook_stage_label``
      - 4 项证据评分（如有）
      - 仓位百分比（如有）
      - 三笔法状态（如有）
    """
    if playbook_contract_is_empty(contract):
        return {}
    if contract is None:
        return {}
    summary: Dict[str, Any] = {}
    stage = contract.playbook_stage
    if stage is not None:
        summary["playbook_stage"] = stage
        label = playbook_stage_label(stage)
        if label:
            summary["playbook_stage_label"] = label
    for score_field in (
        "industry_evidence_score",
        "earnings_validation_score",
        "fund_confirmation_score",
        "risk_pressure_score",
    ):
        val = getattr(contract, score_field, None)
        if val is not None:
            summary[score_field] = val
    for pos_field in (
        "planned_max_position_pct",
        "current_position_pct",
        "unrealized_pnl_pct",
    ):
        val = getattr(contract, pos_field, None)
        if val is not None:
            summary[pos_field] = val
    for lot_field in ("trial_lot_status", "confirm_lot_status", "attack_lot_status"):
        val = getattr(contract, lot_field, None)
        if val is not None:
            summary[lot_field] = val
    return summary


def assert_no_strong_action_words(text: Optional[str]) -> None:
    """断言文本不含强动作禁用词（供调用方和测试使用）."""
    if not text:
        return
    for word in FORBIDDEN_STRONG_WORDS:
        if word in text:
            raise AssertionError(f"playbook contract text contains forbidden strong word: {word}")


def validate_playbook_contract_safety(contract: Optional[PlaybookContract]) -> bool:
    """检查 contract 的所有文本字段不含强动作词.

    返回 True 表示安全，False 表示发现违规词。用于 CI 守卫。
    """
    if contract is None:
        return True
    text_fields = (
        contract.add_trigger,
        contract.reduce_trigger,
        contract.exit_trigger,
        contract.investment_thesis,
        contract.last_operation,
        contract.next_action,
        contract.notes,
    )
    for text in text_fields:
        if not text:
            continue
        for word in FORBIDDEN_STRONG_WORDS:
            if word in text:
                return False
    return True
