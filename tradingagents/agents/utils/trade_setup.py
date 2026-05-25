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


def _first_price(text: str, labels: tuple[str, ...], prefer_later: bool = False) -> float | None:
    """Extract the first (or last if prefer_later) price matching any label.

    When prefer_later=True the *last* match **by text position** is returned —
    useful for stop-loss extraction where the risk-manager paragraph appears
    later in the text and should override the trader-level "不适用/—".

    [F-001-fix] Handles bold markdown around both label and number:
    e.g. **止损价**：**36.00元**
    Also handles multi-price lines:
    e.g. 止损价：34.40元（试探仓）和36.00元（主仓）
    """
    result = None
    best_pos = -1
    for label in labels:
        # [F-001-fix] Allow optional ** around the number after colon
        pattern = rf"\*{{0,2}}{re.escape(label)}\*{{0,2}}[：:\s]*(?:[¥￥$])?\*{{0,2}}([0-9]+(?:\.[0-9]+)?)"
        for match in re.finditer(pattern, text):
            if _is_likely_indicator_number(text, match.end(1)) or _is_likely_list_marker(text, match.end(1)):
                continue
            try:
                val = float(match.group(1))
                if not prefer_later:
                    return val
                if match.start() > best_pos:
                    best_pos = match.start()
                    result = val
            except ValueError:
                continue

        # [F-001-fix] Handle multi-price on same line after "和"/"/"
        # e.g. 止损价：34.40元（试探仓）和36.00元（主仓）
        # Only runs when prefer_later=True (used by _extract_stop_loss fallback)
        if prefer_later:
            last_match = None
            for match in re.finditer(pattern, text):
                last_match = match
            if last_match is not None:
                rest = text[last_match.end():]
                # Look for "和 XX[.XX]元" in the remainder of this line
                extra = re.search(
                    r'(?:和|及)\s*\*{0,2}([0-9]+(?:\.[0-9]+)?)\s*元?',
                    rest.split('\n')[0],
                )
                if extra:
                    try:
                        val = float(extra.group(1))
                        abs_pos = last_match.end() + extra.start(1)
                        if abs_pos > best_pos:
                            best_pos = abs_pos
                            result = val
                    except ValueError:
                        pass
    return result


def _find_price_range(text: str) -> str | None:
    """Extract entry price range from text, preferring final/effective ranges.

    [E-008] Priority order:
    1. Ranges labeled "最终有效/唯一有效" — the refined final range
    2. Last occurrence of generic range patterns — later in text = more refined

    [Fix-7] Exclusion rules:
    - "不建议入场/无入场区间" overrides everything (return None)
    - Distinguish 震荡区间/观察区间/阻力区/支撑区 from 建议入场区间
    - Only accept labels that explicitly indicate entry: 入场区间, 买入区间, 建仓区间,
      可在X-Y买入, etc.
    """
    # [Fix-7] Hard exclusion: explicit "no entry" statements
    no_entry_patterns = [
        r'不(?:建议|推荐)(?:入场|买入|建仓)',
        r'(?:无|没有)(?:入场|买入|建仓)区间',
        r'暂[无不](?:建议)?(?:入场|买入|建仓)',
        r'(?:不建议入场|无入场区间|禁止入场)',
    ]
    for p in no_entry_patterns:
        if re.search(p, text, re.IGNORECASE):
            return None

    # [F-001-fix] Range value pattern that handles **bold** markdown around numbers
    range_val = r"\*{0,2}[0-9]+(?:\.[0-9]+)?\*{0,2}\s*(?:元)?\s*[-~至\-]\s*(?:\*{0,2})?(?:元)?\s*\*{0,2}[0-9]+(?:\.[0-9]+)?\*{0,2}(?:元)?"

    # Priority: match ranges explicitly labeled as final/effective
    priority_patterns = [
        r"(?:最终(?:有效)?|唯一有效)(?:的)?(?:入场|买入|建仓|建仓执行)区间[^0-9*]*?" + range_val,
        r"有效(?:入场|买入|建仓|建仓执行)?区间[^0-9*]*?" + range_val,
        r'可在[\d.]+\s*[-~至]\s*[\d.]+(?:元)?(?:买入|建仓|入场)',
    ]
    for pattern in priority_patterns:
        match = re.search(pattern, text)
        if match:
            raw = match.group(0)
            nums = re.findall(r"[0-9]+(?:\.[0-9]+)?", raw)
            if len(nums) >= 2:
                sep = "-" if "-" in raw else ("至" if "至" in raw else "~")
                return f"{nums[0]}{sep}{nums[1]}"

    # [F-001-fix] Extended entry labels including "入场条件"
    generic_patterns = [
        r"(?:入场区间|买入区间|建仓区间)[：:\s]*" + range_val,
        # [F-001-fix] Match patterns like: 入场条件：股价回调至35.50-36.50元区间
        r"(?:入场条件|买入条件|建仓条件)[^\n]*?" + range_val + r"\s*(?:元)?(?:区间|区域)?",
        # [F-001-fix] Match inline: 建议在35.50-36.50元区间建仓 / 可在35.50至36.50元建仓
        r"(?:建议|可|宜)(?:在|于)[^0-9]*?" + range_val + r"\s*(?:元)?(?:区间|区域)?(?:建仓|买入|入场)",
    ]
    for pattern in generic_patterns:
        matches = re.findall(pattern, text)
        if matches:
            raw = matches[-1] if isinstance(matches[-1], str) else matches[-1]
            nums = re.findall(r"[0-9]+(?:\.[0-9]+)?", raw)
            if len(nums) >= 2:
                sep = "-" if "-" in raw else ("至" if "至" in raw else "~")
                return f"{nums[0]}{sep}{nums[1]}"
    return None


def _setup_type(text: str) -> str:
    """[Fix-8] Extract trading setup tags from factual events only.

    Rules:
    - Tags must be derived from factual events, not bullish templates.
    - 净利润大幅下降/不及预期/业绩暴雷 → '业绩承压' or '业绩不及预期'.
    - '业绩超预期' requires explicit positive evidence (超预期/大增/大幅增长).
    """
    tags: list[str] = []

    # [Fix-8] Performance: distinguish positive vs negative explicitly
    if any(k in text for k in ("业绩超预期", "业绩大增", "净利润大幅增长", "超预期")):
        tags.append("业绩超预期")
    elif any(k in text for k in ("业绩不及预期", "净利润大幅下降", "业绩暴雷", "净利润下滑",
                                  "业绩下滑", "营收下降", "盈利下降", "业绩承压")):
        tags.append("业绩承压")
    elif any(k in text for k in ("业绩", "同比", "净利润", "营收")):
        # Mentioned but direction unclear
        tags.append("业绩关注")

    if any(k in text for k in ("AI", "算力", "政策", "国产替代", "板块")):
        tags.append("题材预期")
    if any(k in text for k in ("解禁", "公告", "业绩说明会", "分红", "回购")):
        tags.append("事件驱动")
    if any(k in text for k in ("缩量下跌", "放量反弹", "吸筹", "洗盘", "量价")):
        tags.append("量价低吸")
    if any(k in text for k in ("突破", "站稳", "新高", "均线多头")):
        tags.append("趋势突破")
    if any(k in text for k in ("持仓成本", "当前仓位", "减仓", "止损")):
        tags.append("持仓风控")
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


def _extract_stop_loss(text: str) -> tuple[float | None, str]:
    """[Fix-1] Extract stop-loss with priority to risk manager paragraph.

    Priority order:
    1. "最终交易决策" / "风控委员会" / "风控裁决" sections (final authority)
    2. The last occurrence of stop-loss keywords (later = more authoritative)
    3. Conditional stop-loss patterns

    Returns (price_or_None, label) where label is '' or '条件止损价'.
    """
    # Extended keywords per Fix-1 requirements
    labels = (
        "止损价", "止损位", "初始止损", "硬性止损",
        "止损红线", "止损线", "止损", "失效价", "失效位", "防守位",
    )

    # 1. Try risk-manager / final decision sections first
    rm_patterns = [
        r'(?:最终交易决策|风控委员会|风控裁决|风控结论)[^\n]*\n([\s\S]*?)(?:\n##|\n###|\n---|\Z)',
        r'(?:目标价|止损价)[：:\s]*[^\n]*',  # line containing 目标价/止损价
    ]
    for rm_p in rm_patterns:
        rm_match = re.search(rm_p, text)
        if rm_match:
            rm_text = rm_match.group(1) if rm_match.lastindex else rm_match.group(0)
            price = _first_price(rm_text, labels, prefer_later=True)
            if price is not None:
                return price, ''

    # 2. Conditional stop-loss: "收盘价跌破X元" / "跌破X元止损"
    cond_patterns = [
        r'(?:收盘价)?跌破\s*(\d+(?:\.\d+)?)\s*元?.{0,6}(?:止损|清仓|离场)',
        r'(?:止损|清仓|离场).{0,10}跌破\s*(\d+(?:\.\d+)?)\s*元?',
        r'若?.{0,6}(?:跌破|低于|下破)\s*(\d+(?:\.\d+)?)\s*元?.{0,6}(?:止损|清仓)',
    ]
    for cp in cond_patterns:
        cm = re.search(cp, text)
        if cm:
            try:
                price = float(cm.group(1))
                return price, '条件止损价'
            except ValueError:
                pass

    # 3. Fallback: last occurrence in full text (prefer_later)
    price = _first_price(text, labels, prefer_later=True)
    return price, ''


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
    stop_loss_price, sl_label = _extract_stop_loss(text)
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
    # [P1-3] Stop-loss tolerance: skip stop-loss error for no-position without conditional entry
    current_position = (user_context or {}).get("current_position", 0)
    _has_position = current_position is not None and current_position > 0
    _is_conditional_entry = execution_mode == "条件触发" and any(
        keyword in text for keyword in ("买入", "建仓", "入场", "BUY")
    )
    if stop_loss_price is None:
        if _has_position:
            # Has position: stop-loss is required
            do_not_trade_if.append("无法从报告中解析出明确止损价。")
        elif _is_conditional_entry:
            # No position but conditional entry: stop-loss required for the entry plan
            do_not_trade_if.append("无法从报告中解析出明确止损价。")
        # else: no position, no conditional entry → stop-loss missing is NOT an error
    do_not_trade_if.extend(conflicts)

    return {
        "setup_type": setup_type,
        "execution_mode": execution_mode,
        "action": action,
        "entry_range": entry_range,
        "trigger_price": trigger_price,
        "stop_loss_price": stop_loss_price,
        "stop_loss_label": sl_label,  # '' or '条件止损价'
        "user_constraints": constraints,
        "do_not_trade_if": do_not_trade_if,
        "conflicts": conflicts,
    }


def format_trade_quality_check(check: Mapping[str, Any]) -> str:
    conflicts = check.get("conflicts") or []
    do_not_trade_if = check.get("do_not_trade_if") or []
    sl_display = '—'
    if check.get('stop_loss_price') is not None:
        sl_label = check.get('stop_loss_label', '')
        sl_display = f"{sl_label} " if sl_label else ""
        sl_display += str(check['stop_loss_price'])
    lines = [
        "### 执行质检",
        f"- 打法标签：{check.get('setup_type') or '未识别'}",
        f"- 执行模式：{check.get('execution_mode') or '人工复核'}",
        f"- 系统动作：{check.get('action') or '人工复核'}",
        f"- 入场区间：{check.get('entry_range') or '—'}",
        f"- 触发价：{check.get('trigger_price') if check.get('trigger_price') is not None else '—'}",
        f"- 止损价：{sl_display}",
    ]
    if do_not_trade_if:
        lines.append("- 禁止交易条件：" + "；".join(str(item) for item in do_not_trade_if))
    if conflicts:
        lines.append("- 冲突检查：发现冲突，最终动作降级为等待人工复核。")
    else:
        lines.append("- 冲突检查：未发现明显执行时点冲突。")
    return "\n".join(lines)
