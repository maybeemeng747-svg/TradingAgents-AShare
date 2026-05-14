from __future__ import annotations

import re
from typing import Any, Mapping


_PRICE_FOLLOWING_TEXT_RE = re.compile(r"\s*(?:日|周|月)?(?:均线|线|k|K|ma|MA)\b")
_LIST_MARKER_AFTER_NUMBER_RE = re.compile(r"\s*[.)、]\s*(?:[A-Z0-9一二三四五六七八九十]|[*-])")
_GENERATED_QUALITY_SECTION_RE = re.compile(r"\n?###?\s*执行质检[\s\S]*$", re.IGNORECASE)


def _is_likely_indicator_number(text: str, end_index: int) -> bool:
    """Avoid treating MA periods such as 5日线 as executable prices."""
    return bool(_PRICE_FOLLOWING_TEXT_RE.match(text[end_index : end_index + 12]))


def _is_likely_list_marker(text: str, end_index: int) -> bool:
    return bool(_LIST_MARKER_AFTER_NUMBER_RE.match(text[end_index : end_index + 16]))


def _strip_generated_quality_section(text: str) -> str:
    return _GENERATED_QUALITY_SECTION_RE.sub("", text or "")


def _first_price(text: str, labels: tuple[str, ...]) -> float | None:
    for label in labels:
        # Strip optional Markdown bold markers (**) around the label for matching
        pattern = rf"\*{{0,2}}{re.escape(label)}\*{{0,2}}[：:\s]*(?:[¥￥$])?([0-9]+(?:\.[0-9]+)?)"
        for match in re.finditer(pattern, text):
            if _is_likely_indicator_number(text, match.end(1)) or _is_likely_list_marker(text, match.end(1)):
                continue
            try:
                return float(match.group(1))
            except ValueError:
                continue
    return None


def _find_price_range(text: str) -> str | None:
    patterns = [
        r"(?:入场区间|买入区间|建仓区间)[：:\s]*([0-9]+(?:\.[0-9]+)?\s*[-~至]\s*[0-9]+(?:\.[0-9]+)?)",
        r"([0-9]+(?:\.[0-9]+)?\s*[-~至]\s*[0-9]+(?:\.[0-9]+)?)(?:\s*区间|\s*附近)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).replace(" ", "")
    return None


def _setup_type(text: str) -> str:
    tags: list[str] = []
    checks = [
        ("业绩超预期", ("业绩", "同比", "净利润", "营收")),
        ("题材预期", ("AI", "算力", "政策", "国产替代", "板块")),
        ("事件驱动", ("解禁", "公告", "业绩说明会", "分红", "回购")),
        ("量价低吸", ("缩量下跌", "放量反弹", "吸筹", "洗盘", "量价")),
        ("趋势突破", ("突破", "站稳", "新高", "均线多头")),
        ("持仓风控", ("持仓成本", "当前仓位", "减仓", "止损")),
    ]
    for tag, keywords in checks:
        if any(keyword in text for keyword in keywords):
            tags.append(tag)
    return " + ".join(tags[:3]) if tags else "未识别"


def _execution_mode(text: str) -> str:
    if any(keyword in text for keyword in ("等待", "前提条件", "触发条件", "失效条件", "若", "如果")):
        return "条件触发"
    if any(keyword in text for keyword in ("立即", "市价", "马上")):
        return "立即执行"
    return "人工复核"


def _conflicts(text: str) -> list[str]:
    conflicts: list[str] = []
    if "事件前" in text and any(keyword in text for keyword in ("平稳度过", "事件后", "解禁日平稳")):
        conflicts.append("交易计划同时出现“事件前建仓”和“事件平稳后再执行”，执行时点冲突。")
    if "买入" in text and any(keyword in text for keyword in ("不追高", "追高风险")) and not any(
        keyword in text for keyword in ("回调", "入场区间", "站稳", "触发")
    ):
        conflicts.append("买入建议未给出回调/站稳/触发条件，可能违反不追高约束。")
    if any(keyword in text for keyword in ("买入", "加仓")) and not any(keyword in text for keyword in ("止损", "失效")):
        conflicts.append("做多方案缺少明确止损或失效条件。")
    return conflicts


def build_trade_quality_check(
    *,
    investment_plan: str = "",
    trader_plan: str = "",
    final_decision: str = "",
    user_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a deterministic execution checklist from the final reports."""
    text = _strip_generated_quality_section("\n".join([investment_plan or "", trader_plan or "", final_decision or ""]))
    conflicts = _conflicts(text)
    constraints = [str(item) for item in (user_context or {}).get("constraints", [])]
    setup_type = _setup_type(text)
    execution_mode = _execution_mode(text)
    trigger_price = _first_price(text, ("触发价", "触发位", "突破价", "突破位", "站稳", "目标价"))
    stop_loss_price = _first_price(text, ("止损价", "止损位", "止损", "失效价", "失效位", "防守位"))
    entry_range = _find_price_range(text)

    action = "人工复核"
    if conflicts:
        action = "等待人工复核"
    elif execution_mode == "条件触发":
        action = "等待触发"
    elif execution_mode == "立即执行":
        action = "可执行但需核对盘面"

    do_not_trade_if = []
    if constraints:
        do_not_trade_if.append("交易计划违反用户硬约束：" + "；".join(constraints))
    if stop_loss_price is None:
        do_not_trade_if.append("无法从报告中解析出明确止损价。")
    do_not_trade_if.extend(conflicts)

    return {
        "setup_type": setup_type,
        "execution_mode": execution_mode,
        "action": action,
        "entry_range": entry_range,
        "trigger_price": trigger_price,
        "stop_loss_price": stop_loss_price,
        "user_constraints": constraints,
        "do_not_trade_if": do_not_trade_if,
        "conflicts": conflicts,
    }


def format_trade_quality_check(check: Mapping[str, Any]) -> str:
    conflicts = check.get("conflicts") or []
    do_not_trade_if = check.get("do_not_trade_if") or []
    lines = [
        "### 执行质检",
        f"- 打法标签：{check.get('setup_type') or '未识别'}",
        f"- 执行模式：{check.get('execution_mode') or '人工复核'}",
        f"- 系统动作：{check.get('action') or '人工复核'}",
        f"- 入场区间：{check.get('entry_range') or '—'}",
        f"- 触发价：{check.get('trigger_price') if check.get('trigger_price') is not None else '—'}",
        f"- 止损价：{check.get('stop_loss_price') if check.get('stop_loss_price') is not None else '—'}",
    ]
    if do_not_trade_if:
        lines.append("- 禁止交易条件：" + "；".join(str(item) for item in do_not_trade_if))
    if conflicts:
        lines.append("- 冲突检查：发现冲突，最终动作降级为等待人工复核。")
    else:
        lines.append("- 冲突检查：未发现明显执行时点冲突。")
    return "\n".join(lines)
