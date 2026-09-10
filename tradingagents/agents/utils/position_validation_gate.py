# [C-001] position_validation_gate
"""持仓状态动作门禁 — 根据持仓状态约束可输出的动作类型。

每份报告生成前读取持仓状态，确定允许的动作集合，
对报告文本中不符合持仓状态的动作关键词进行降级/替换。
"""

import logging
import re
from typing import Optional

from tradingagents.agents.utils.trade_actions import (
    POSITION_STATE_ACTIONS,
    TradeAction,
)

_logger = logging.getLogger(__name__)

# ── 未持仓时禁止的动作关键词（减仓/清仓/止损/止盈/卖出类） ──
# 匹配 readiness_score.py _SANITIZE_NO_POSITION 的语义，但这里做检测而非替换
_NO_POSITION_FORBIDDEN_PATTERNS: list[tuple[str, str]] = [
    # 具体动作短语
    (r'建议止损离场', '止损离场'),
    (r'建议减仓', '减仓'),
    (r'建议清仓', '清仓'),
    (r'建议止盈', '止盈'),
    (r'建议止损', '止损'),
    (r'建议卖出', '卖出'),
    (r'立即清仓', '清仓'),
    (r'立即减仓', '减仓'),
    (r'立即止损', '止损'),
    (r'立即卖出', '卖出'),
    (r'可以减仓', '减仓'),
    (r'可以清仓', '清仓'),
    (r'可以止损', '止损'),
    (r'可以卖出', '卖出'),
    (r'应该减仓', '减仓'),
    (r'应该清仓', '清仓'),
    (r'应该止损', '止损'),
    (r'应该卖出', '卖出'),
    (r'需要减仓', '减仓'),
    (r'需要清仓', '清仓'),
    (r'需要止损', '止损'),
    (r'需要卖出', '卖出'),
    (r'果断止损', '止损'),
    (r'适度减仓', '减仓'),
    (r'尽快清仓', '清仓'),
    (r'逐步减仓', '减仓'),
    (r'止损离场', '止损'),
    (r'触发止损.{0,4}(?:离场|清仓|卖出|减仓|出局)', '止损'),
    (r'触发止损', '止损'),
    (r'减仓观察', '减仓'),
    (r'清仓离场', '清仓'),
    (r'清仓出局', '清仓'),
    # 通用匹配（带修饰词前缀）
    (r'\w{0,4}减仓', '减仓'),
    (r'\w{0,4}清仓', '清仓'),
    (r'\w{0,4}止盈', '止盈'),
    (r'\w{0,4}卖出', '卖出'),
    # 独立止损，但排除止损价/止损位/止损条件/止损线等字段名
    (r'止损(?!价|位|条件|线|红线)', '止损'),
    # 英文动作
    (r'\b(?:SELL|EXIT|REDUCE)\b', 'EXIT/REDUCE'),
    # HOLD 在未持仓时也不合理；正文带"等待/观察"不得让错误 HOLD 绕过
    # 转换（C-001-R1），HOLD 一律违规后由警告驱动 WAIT 转换
    (r'\bHOLD\b', 'HOLD'),
]

# ── 已持仓时禁止的动作关键词（建仓/买入类） ──
_HAS_POSITION_FORBIDDEN_PATTERNS: list[tuple[str, str]] = [
    (r'建议建仓', '建仓'),
    (r'建议买入', '买入'),
    (r'建议入场', '入场'),
    (r'可以建仓', '建仓'),
    (r'可以买入', '买入'),
    (r'应该建仓', '建仓'),
    (r'应该买入', '买入'),
    (r'考虑建仓', '建仓'),
    (r'考虑买入', '买入'),
    (r'积极建仓', '建仓'),
    (r'条件建仓', '建仓'),
    (r'条件入场', '入场'),
    (r'入场建仓', '建仓'),
    (r'买入建仓', '建仓'),
    (r'加仓买入', '加仓'),
    (r'追涨买入', '追涨'),
    # 英文动作
    (r'\b(?:BUY|ENTER)\b', 'BUY/ENTER'),
]

# 保护字段名：止损价/止损位/止损条件/止损红线/止损线 不算违规
_FIELD_NAME_SAFEGUARD = re.compile(r'止损(?:价|位|条件|线|红线)')


def _detect_violations(
    text: str,
    patterns: list[tuple[str, str]],
    position_status: str,
) -> list[dict]:
    """检测文本中是否存在违反持仓状态的动作关键词。

    跳过系统追加区块（⚠️/📊/---）中的匹配。
    """
    if not text:
        return []

    # 分离 LLM 原始输出和系统追加区块
    body = _split_body(text)

    violations: list[dict] = []
    seen_spans: set[tuple[int, int]] = set()

    for pattern, action_name in patterns:
        for m in re.finditer(pattern, body, re.IGNORECASE):
            span = (m.start(), m.end())
            if span in seen_spans:
                continue
            # 跳过字段名中的止损
            if action_name == '止损' and _FIELD_NAME_SAFEGUARD.search(body[m.start():m.start() + 10]):
                continue
            seen_spans.add(span)
            violations.append({
                "action": action_name,
                "matched_text": m.group(),
                "position_status": position_status,
                "offset": m.start(),
            })

    return violations


def _split_body(text: str) -> str:
    """分离 LLM 原始输出，去掉系统追加区块。"""
    markers = [
        "📊 数据源可用性",
        "--- 报告质量评分 ---",
        "### 执行等级与证据门禁",
        "⚠️ [D-002]",
        "⚠️ [C-001]",
        "⚠️ [C-005]",
        "⚠️ [C-006]",
        "⚠️ [C-007]",
    ]
    earliest = len(text)
    for marker in markers:
        idx = text.find(marker)
        if idx >= 0 and idx < earliest:
            earliest = idx
    return text[:earliest]


def resolve_unified_has_position(
    user_context: Optional[dict] = None,
    position_context: Optional[dict] = None,
) -> Optional[bool]:
    """显式+推断统一持仓解析，优先级对齐入口契约 api.main._resolve_has_position。

    1. position_context.has_position：意图解析折叠了显式数字字段、否定词与
       持仓关键词推断的统一结果；``position_status_explicit=False`` 是无证据
       时的 legacy 默认值，不信任（缺数据不得猜成有持仓）。
    2. user_context.current_position / current_position_pct 显式数值字段。
    3. 均缺失时返回 None（unknown，不约束动作）。
    """
    if isinstance(position_context, dict) and "has_position" in position_context:
        if position_context.get("position_status_explicit") is not False:
            return bool(position_context["has_position"])

    if user_context:
        cp = user_context.get("current_position")
        if cp is not None:
            return cp > 0
        pct = user_context.get("current_position_pct")
        if pct is not None:
            return pct > 0
    return None


def validate_position_actions(
    text: str,
    user_context: Optional[dict] = None,
    position_context: Optional[dict] = None,
) -> dict:
    """根据持仓状态校验报告文本中的动作关键词。

    Args:
        text: 报告文本（研究经理/风控经理的 LLM 输出）
        user_context: 用户上下文，包含 current_position 等字段
        position_context: 意图解析产出的统一持仓上下文
            （has_position / position_status_explicit），用于显式数字
            字段缺失时按推断结果约束动作

    Returns:
        {
            "passed": bool,              # 是否无违规
            "position_status": str,      # has_position / no_position / unknown
            "violations": list[dict],    # 违规详情
            "allowed_actions": list[str],# 当前持仓状态允许的动作
            "forbidden_actions": list[str],  # 当前持仓状态禁止的动作
            "has_position": Optional[bool],  # 统一解析后的持仓布尔值
        }
    """
    # 解析持仓状态（显式 + 推断统一结果）
    has_position = resolve_unified_has_position(user_context, position_context)

    if has_position is True:
        position_status = "has_position"
    elif has_position is False:
        position_status = "no_position"
    else:
        position_status = "unknown"

    # 获取允许/禁止的动作
    allowed = [a.value for a in POSITION_STATE_ACTIONS.get(has_position, [])]
    # unknown 状态：允许所有动作（不约束）
    if has_position is None:
        forbidden_patterns = []
        forbidden_actions = []
    elif not has_position:
        forbidden_patterns = _NO_POSITION_FORBIDDEN_PATTERNS
        forbidden_actions = [a.value for a in TradeAction if a not in POSITION_STATE_ACTIONS[False]]
    else:
        forbidden_patterns = _HAS_POSITION_FORBIDDEN_PATTERNS
        forbidden_actions = [a.value for a in TradeAction if a not in POSITION_STATE_ACTIONS[True]]

    # 检测违规
    violations = _detect_violations(text, forbidden_patterns, position_status)

    passed = len(violations) == 0

    if not passed:
        _logger.warning(
            "[C-001] position_validation_gate: %s 检测到 %d 个违规动作: %s",
            position_status,
            len(violations),
            [v["action"] for v in violations],
        )

    return {
        "passed": passed,
        "position_status": position_status,
        "violations": violations,
        "allowed_actions": allowed,
        "forbidden_actions": forbidden_actions,
        "has_position": has_position,
    }


def format_position_validation_warning(result: dict) -> str:
    """将门禁结果格式化为追加到报告末尾的警告文本。

    只在有违规时输出。
    """
    if result["passed"]:
        return ""

    position_status = result["position_status"]
    violations = result["violations"]

    if position_status == "no_position":
        actions = sorted(set(v["action"] for v in violations))
        return (
            f"\n\n⚠️ [C-001] 未持仓状态，以下持仓动作不适用：{'、'.join(actions)}。"
            f"已自动降级为观察（WAIT）。"
        )
    if position_status == "has_position":
        actions = sorted(set(v["action"] for v in violations))
        return (
            f"\n\n⚠️ [C-001] 已持仓状态，以下建仓动作不适用：{'、'.join(actions)}。"
            f"请参考持有/减仓/清仓建议。"
        )
    return ""
