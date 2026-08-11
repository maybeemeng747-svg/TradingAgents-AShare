from __future__ import annotations

import re
from typing import Any, Mapping


_PRICE_FOLLOWING_TEXT_RE = re.compile(r"\s*(?:日|周|月)?(?:均线|线|k|K|ma|MA)")
_LIST_MARKER_AFTER_NUMBER_RE = re.compile(r"\s*[.)、]\s*(?:[A-Z0-9一二三四五六七八九十]|[*-])")
_ACTION_ENUM_REFERENCE_RE = re.compile(
    r"(?im)(?:"
    r"(?:^\s*[-*]\s*)?\*{0,2}(?:分类标签|动作枚举|决策枚举|可选动作|动作集合|动作标签)"
    r"\s*(?:[:：]\s*\*{0,2}|\*{0,2}\s*[:：]\s*\*{0,2})"
    r"(?:BUY\s*[/／]\s*SELL\s*[/／]\s*HOLD|"
    r"ENTER\s*[/／]\s*WAIT\s*[/／]\s*HOLD\s*[/／]\s*REDUCE\s*[/／]\s*EXIT)\*{0,2}"
    r"|(?:对应|英文动作(?:为)?|动作代码(?:为)?)\s*"
    r"(?:BUY\s*[/／]\s*SELL\s*[/／]\s*HOLD|"
    r"ENTER\s*[/／]\s*WAIT\s*[/／]\s*HOLD\s*[/／]\s*REDUCE\s*[/／]\s*EXIT)"
    r"|(?:BUY\s*[/／]\s*SELL\s*[/／]\s*HOLD|"
    r"ENTER\s*[/／]\s*WAIT\s*[/／]\s*HOLD\s*[/／]\s*REDUCE\s*[/／]\s*EXIT)"
    r"\s*(?:三分类标签|分类标签|动作枚举|决策枚举|可选动作|动作集合|动作标签)"
    r")"
)
_ENGLISH_FINAL_VERDICT_RE = re.compile(
    r"FINAL\s+TRANSACTION\s+PROPOSAL\s*[:：]\s*\*{0,2}"
    r"(?P<action>BUY|SELL|HOLD)\*{0,2}",
    re.IGNORECASE,
)


def _is_likely_indicator_number(text: str, end_index: int) -> bool:
    """Avoid treating MA periods such as 5日线 as executable prices."""
    return bool(_PRICE_FOLLOWING_TEXT_RE.match(text[end_index : end_index + 12]))


def _is_likely_list_marker(text: str, end_index: int) -> bool:
    return bool(_LIST_MARKER_AFTER_NUMBER_RE.match(text[end_index : end_index + 16]))


def _is_percentage_value(text: str, end_index: int) -> bool:
    """Percentage risk limits are not executable stop/trigger prices."""
    return bool(
        re.match(
            r"\s*(?:[%％]|个百分点|个点)",
            text[end_index : end_index + 12],
        )
    )


def _strip_generated_quality_section(text: str) -> str:
    """Keep model-authored headings in scope for decision parsing.

    ``build_trade_quality_check`` runs before the application appends its
    deterministic diagnostics. A model can write the same heading, so it is
    not a trust boundary and must not hide a later WAIT/no-buy verdict.
    """
    return text or ""


def _strip_action_enum_references(text: str) -> str:
    """Remove documentation-only action enums before ordering directives."""
    return _ACTION_ENUM_REFERENCE_RE.sub("", text or "")


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
        for match in re.finditer(pattern, text, re.IGNORECASE):
            if (
                _is_likely_indicator_number(text, match.end(1))
                or _is_likely_list_marker(text, match.end(1))
                or _is_percentage_value(text, match.end(1))
            ):
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
            for match in re.finditer(pattern, text, re.IGNORECASE):
                last_match = match
            if last_match is not None:
                rest = text[last_match.end():]
                # Look for "和 XX[.XX]元" in the remainder of this line
                extra = re.search(
                    r'(?:和|及)\s*\*{0,2}([0-9]+(?:\.[0-9]+)?)\s*元?',
                    rest.split('\n')[0],
                )
                if extra:
                    if _is_percentage_value(rest, extra.end(1)):
                        continue
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
        match = re.search(pattern, text, re.IGNORECASE)
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
        r"(?:entry|buy)\s*range\s*[:：]\s*" + range_val,
    ]
    for pattern in generic_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        if matches:
            raw = matches[-1] if isinstance(matches[-1], str) else matches[-1]
            nums = re.findall(r"[0-9]+(?:\.[0-9]+)?", raw)
            if len(nums) >= 2:
                sep = "-" if "-" in raw else ("至" if "至" in raw else "~")
                return f"{nums[0]}{sep}{nums[1]}"
    return None


def _price_range_bounds(value: str | None) -> tuple[float, float] | None:
    """Return normalized, non-degenerate bounds for an executable range."""
    if not value:
        return None
    match = re.search(
        r"([0-9]+(?:\.[0-9]+)?)\s*[-–—~～至到]\s*([0-9]+(?:\.[0-9]+)?)",
        value,
    )
    if not match:
        return None
    first, second = float(match.group(1)), float(match.group(2))
    lower, upper = sorted((first, second))
    if lower <= 0 or lower == upper:
        return None
    return lower, upper


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


_ENTRY_ACTION_TEXT = (
    r"(?:买入|购入|建仓|入场|开仓|试仓|试多|低吸|加仓|补仓|增持|介入|参与|"
    r"抄底|追涨|配置(?:仓位)?|买(?:一点|一些|少量|点仓位)|条件试(?:探)?仓|"
    r"试探性轻仓|轻仓试错|分批建立仓位|\bBUY\b|\bENTER\b)"
)
_ENTRY_ACTION_RE = re.compile(_ENTRY_ACTION_TEXT, re.IGNORECASE)
_NEGATED_ENTRY_RE = re.compile(
    rf"(?:"
    rf"(?:并不是|不是|并非)\s*"
    rf"(?:(?:建议|推荐|选择|决定|主张|让(?:你|用户|投资者)?)\s*)?"
    rf"{_ENTRY_ACTION_TEXT}"
    rf"|"
    rf"(?:不建议|不推荐|禁止|不得|不可|不能|不应|不宜|暂不|无需|避免|回避|放弃|无(?:明确)?)"
    rf"[^。！？；;，,\n]{{0,16}}?{_ENTRY_ACTION_TEXT}"
    rf"|(?:不考虑|不准备|不打算|不计划|不选择|不想|不愿|不会|无意)"
    rf"[^。！？；;，,\n]{{0,8}}?{_ENTRY_ACTION_TEXT}"
    rf"|不要\s*{_ENTRY_ACTION_TEXT}"
    rf"|(?<!不是)(?<!并非)不\s*{_ENTRY_ACTION_TEXT}"
    rf")",
    re.IGNORECASE,
)
_META_ENTRY_RE = re.compile(
    rf"(?:"
    r"(?:是否|能否|可否|提到|提及|关注|描述|记录|引用|标签|风险提示|重新评估是否)"
    rf"[^。！？；;，,\n]{{0,16}}{_ENTRY_ACTION_TEXT}"
    r"|(?:讨论|分析|评估|研究问题(?:是)?)"
    rf"[^。！？；;，,\n]{{0,16}}(?:是否|能否|可否|何时){_ENTRY_ACTION_TEXT}"
    r"|(?:讨论|分析|评估|研究问题(?:是)?)"
    rf"[^。！？；;，,\n]{{0,16}}{_ENTRY_ACTION_TEXT}"
    r"[^。！？；;，,\n]{0,12}(?:是否|能否|可否|何时|可行性|合适|适合)"
    r")",
    re.IGNORECASE,
)
_ENTRY_VETO_RE = re.compile(
    rf"(?:{_ENTRY_ACTION_TEXT})(?:条件|前提)?(?:尚|仍|暂时|目前|当前)?"
    r"(?:不成立|未成立|未满足|不满足|不足|不可行|不合适|不具备|未具备)"
    r"|(?:不具备|未具备|缺少|没有)[^。！？；;，,\n]{0,10}(?:买入|建仓|入场)(?:条件|前提|资格)",
    re.IGNORECASE,
)
_ENTRY_WAIT_RE = re.compile(
    r"(?:"
    r"(?:最终建议|最终意见|最终交易建议|最终交易决策|最终裁决|最终结论|"
    r"风控结论|交易建议|交易意见|处理意见|操作建议|交易员动作|系统指令|"
    r"系统动作|当前动作|当前操作|当前结论|我的意见|我们的意见|决策|动作)"
    r"\s*[:：]?\s*"
    r"(?:WAIT\b|HOLD\b|等待|观望|观察|回避|持有|暂不操作|不交易|"
    r"保持空仓|维持空仓|继续空仓|空仓观望)"
    r"|(?:WAIT\b|HOLD\b|继续观望|保持观望|暂时观望|当前观望|继续观察|保持观察|"
    r"继续持有|保持持有|暂时持有|当前持有|回避为主|回避|暂不操作|不交易|"
    r"保持空仓|维持空仓|继续空仓|空仓观望)"
    r"|^\s*(?:(?:当前|现在|目前|建议|当前建议|综合判断后|以)\s*)?"
    r"(?:WAIT\b|HOLD\b|等待|观望|观察|持有|暂不操作|不交易|"
    r"保持空仓|维持空仓|继续空仓|空仓观望)(?:为主)?\s*$"
    r")",
    re.IGNORECASE,
)
_ENTRY_DIRECTIVE_RE = re.compile(
    rf"(?:"
    rf"(?:最终建议|最终意见|最终交易建议|最终交易决策|最终裁决|最终结论|"
    rf"风控结论|交易建议|交易意见|处理意见|操作建议|交易员动作|系统指令|"
    rf"系统动作|当前动作|当前操作|当前结论|我的意见|我们的意见|决策|动作|"
    rf"入场条件|买入条件|建仓条件|触发条件)"
    rf"[^。！？；;，,\n]{{0,24}}{_ENTRY_ACTION_TEXT}"
    rf"|(?:建议|推荐|可|可以|考虑|允许|应|应该|应当|需|需要|计划|准备|决定|选择|执行|进行|采取|拟|宜|立即|立刻|马上)"
    rf"[^。！？；;，,\n]{{0,12}}{_ENTRY_ACTION_TEXT}"
    rf"|(?:条件型|试探性|轻仓|小仓位|分批|逐步)\s*{_ENTRY_ACTION_TEXT}"
    rf"|(?:我|本人|我们|本基金|本账户)\s*"
    rf"(?:会|将|打算|准备|决定|选择|建议|考虑|倾向于|主张)\s*{_ENTRY_ACTION_TEXT}"
    rf"|(?:若|如果|如|一旦|待|当)[^。！？；;，,\n]{{0,48}}"
    rf"(?:(?:再|择机|适时|伺机|逢低|轻仓|小仓位|分批|今日|今天|可|可以)\s*)*{_ENTRY_ACTION_TEXT}"
    rf"|(?:若|如果|如|一旦|待|当)?[^。！？；;，,\n]{{0,36}}(?:(?<!立)(?:即可|即)|就|便|后|时|则)\s*"
    rf"(?:(?:再|择机|适时|伺机|逢低|轻仓|小仓位|分批|今日|今天|可|可以)\s*)*{_ENTRY_ACTION_TEXT}"
    rf"|^\s*{_ENTRY_ACTION_TEXT}(?:\s|[:：]|$)"
    rf")",
    re.IGNORECASE,
)
_CONDITIONAL_ENTRY_DIRECTIVE_RE = re.compile(
    rf"(?:"
    rf"(?:若|如果|如|一旦|待|当)[^。！？；;\n]{{0,48}}{_ENTRY_ACTION_TEXT}"
    rf"|(?:入场条件|买入条件|建仓条件|触发条件)[^。！？；;\n]{{0,40}}{_ENTRY_ACTION_TEXT}"
    rf"|(?:(?:价格|股价|收盘价|标的)\s*)?"
    rf"(?:突破|站稳|回踩|触及|达到|高于|站上)\s*"
    rf"[0-9]+(?:\.[0-9]+)?\s*(?:元|块)?\s*(?:附近|以上|下方|上方)?\s*"
    rf"(?:(?:再|择机|适时|伺机|逢低|轻仓|小仓位|分批|可|可以|建议|考虑)\s*)*"
    rf"{_ENTRY_ACTION_TEXT}"
    rf"|[^。！？；;，,\n]{{0,36}}(?:(?<!立)(?:即可|即)|就|便|后|时|则)\s*"
    rf"(?:(?:再|择机|适时|伺机|逢低|轻仓|小仓位|分批|今日|今天|可|可以)\s*)*{_ENTRY_ACTION_TEXT}"
    rf")",
    re.IGNORECASE,
)
_THIRD_PARTY_SUBJECT = (
    r"(?:公司(?:董事会)?|本公司|董事会|控股股东|实际控制人|大股东|股东|"
    r"员工持股计划|员工持股平台|高管|董监高|管理层|董事|监事|"
    r"北向资金|主力资金|机构(?:资金)?|游资|(?<!本)基金|外资)"
)
_THIRD_PARTY_ENTRY_ACTIVITY_RE = re.compile(
    rf"{_THIRD_PARTY_SUBJECT}"
    r"(?![^。！？；;，,\n]{0,12}(?:建议|推荐|主张|认为|判断))"
    rf"[^。！？；;，,\n后则时]{{0,30}}?{_ENTRY_ACTION_TEXT}"
    r"(?:了|股份|股票|公司股票|[\d,.万亿元股手%]*)?",
    re.IGNORECASE,
)
_THIRD_PARTY_CONDITIONAL_ENTRY_ACTIVITY_RE = re.compile(
    rf"{_THIRD_PARTY_SUBJECT}"
    r"(?![^。！？；;，,\n]{0,30}(?:建议|推荐|主张|认为|判断))"
    r"(?:(?:承诺|宣布|计划|拟|将|决定)\s*)?"
    r"(?:(?:在|于|若|如果|待|当)\s*)?"
    r"[^。！？；;，,\n]{0,30}?(?:后|时|则)\s*"
    r"(?!(?:可|可以|建议|推荐|考虑|允许|应|应该|应当|需|需要)\s*)"
    rf"{_ENTRY_ACTION_TEXT}"
    r"(?:了|股份|股票|公司股票|[\d,.万亿元股手%]*)?",
    re.IGNORECASE,
)
_THIRD_PARTY_ENTRY_FACT_RE = re.compile(
    rf"{_THIRD_PARTY_SUBJECT}"
    r"(?![^。！？；;，,\n]{0,30}(?:建议|推荐|可|可以|考虑|应|应该|应当|等待|若|如果)"
    rf"[^。！？；;，,\n]{{0,12}}{_ENTRY_ACTION_TEXT})"
    rf"(?=[^。！？；;，,\n]{{0,30}}{_ENTRY_ACTION_TEXT})"
    r"(?=[^。！？；;，,\n]{0,30}(?:今日|今天|昨日|昨天|已|已经|累计|净|"
    r"决定|公告|宣布|披露|拟|计划|将))"
    rf"[^。！？；;，,\n]{{0,30}}?{_ENTRY_ACTION_TEXT}"
    r"(?:了|股份|股票|公司股票|[\d,.万亿元股手%]*)?",
    re.IGNORECASE,
)
_COMPLETED_ENTRY_FACT_RE = re.compile(
    rf"(?:用户|投资者|我|本人)?\s*(?:"
    rf"(?:已经|已|刚刚|刚)\s*(?:在\s*\d+(?:\.\d+)?\s*元?\s*)?"
    rf"{_ENTRY_ACTION_TEXT}(?:了|[\d,.万亿元股手%]*)?"
    rf"|(?:昨日|昨天|前天|上周|上月|去年)\s*"
    rf"(?:在\s*\d+(?:\.\d+)?\s*元?\s*)?"
    rf"{_ENTRY_ACTION_TEXT}(?:了|[\d,.万亿元股手%]+)"
    rf"|(?:今日|今天)\s*(?:在\s*\d+(?:\.\d+)?\s*元?\s*)?"
    rf"{_ENTRY_ACTION_TEXT}了"
    rf")",
    re.IGNORECASE,
)
_ANALYTICAL_ENTRY_RE = re.compile(
    r"(?:"
    r"(?:维持(?:原)?|重申)?(?:买入|增持)评级"
    r"(?:机构共有\d+家|维持不变|不变|为[^。！？；;，,\n]{0,12})?"
    r"|买入(?:成交额|资金(?:占比)?|意愿|盘)"
    r"[^。！？；;，,\n]{0,20}?"
    r"(?=(?:建议|推荐|可|可以|考虑|应|应该|应当|等待)"
    rf"[^。！？；;，,\n]{{0,12}}{_ENTRY_ACTION_TEXT})"
    r"|买入(?:逻辑|信号|风险|压力|意愿)(?:尚|仍|暂时|目前|当前|才|可能|已经|已)*"
    r"(?:不充分|不足|缺失|未出现|出现|待验证|待确认|成立|不成立|有效|无效|"
    r"明确|不明确|增强|减弱|上升|下降|转强|转弱|改善|恶化)"
    r"|买入(?:成交额|资金(?:占比)?|意愿|盘)"
    r"(?![^。！？；;，,\n]{0,20}(?:建议|推荐|可|可以|考虑|应|应该|应当|等待)"
    rf"[^。！？；;，,\n]{{0,12}}{_ENTRY_ACTION_TEXT})"
    r"(?:为|是)?"
    r"[^。！？；;，,\n]{0,16}"
    r")",
    re.IGNORECASE,
)

_EXIT_ACTION_TEXT = r"(?:卖出|减仓|清仓|止损|退出|止盈|\bSELL\b|\bREDUCE\b|\bEXIT\b)"
_NEGATED_EXIT_RE = re.compile(
    rf"(?:(?:不建议|不推荐|禁止|不得|不可|不能|不应|不宜|暂不|无需|避免|"
    rf"不要|不允许)[^。！？；;，,\n]{{0,16}}{_EXIT_ACTION_TEXT}"
    rf"|(?:不考虑|不准备|不打算|不计划|不选择|不想|不愿|不会|无意)"
    rf"[^。！？；;，,\n]{{0,8}}{_EXIT_ACTION_TEXT})",
    re.IGNORECASE,
)
_ANALYTICAL_EXIT_RE = re.compile(
    r"(?:卖出|减仓|清仓|止损|退出|止盈)"
    r"(?:逻辑|信号|风险|压力|意愿)(?:尚|仍|暂时|目前|当前|才|可能|已经|已)*"
    r"(?:不充分|不足|缺失|未出现|出现|待验证|待确认|成立|不成立|有效|无效|"
    r"明确|不明确|增强|减弱|上升|下降|转强|转弱|改善|恶化)",
    re.IGNORECASE,
)
_NON_EXECUTABLE_STOP_FIELD_RE = re.compile(
    r"(?:"
    r"(?:(?:最终(?:决定|结论|建议)?|当前|目前|暂时|现阶段)\s*)"
    r"不(?:再)?(?:设置|设定|设|采用|使用|执行)\s*"
    r"(?:止损价|止损位|初始止损|硬性止损|止损红线|止损线|止损|"
    r"失效价|失效位|防守位)"
    r"|(?:止损价|止损位|初始止损|硬性止损|止损红线|止损线|止损|"
    r"失效价|失效位|防守位)\s*"
    r"(?:(?:暂时|暂|目前|当前|尚|仍)\s*)?"
    r"不(?:再)?(?:设置|设定|设|采用|使用|执行)"
    r"|(?:Stop-loss price|Stop loss price|Stop-loss|Stop loss)\s*"
    r"[:：]?\s*(?:not applicable|cancelled|canceled|withdrawn|unset|"
    r"not set|TBD|N/?A|none)"
    r")",
    re.IGNORECASE,
)

_NON_TRADE_PARTICIPATION_RE = re.compile(
    r"(?:参与|配置)[^。！？；;，,\n]{0,16}(?:股东大会(?:投票)?|股东会|"
    r"业绩说明会|业绩交流会|电话会议|路演|公司治理|投票|会议|"
    r"讨论|研究|分析|评估|文件|参数|环境)",
    re.IGNORECASE,
)
_EXIT_DIRECTIVE_RE = re.compile(
    rf"(?:"
    rf"(?:最终建议|最终裁决|最终结论|交易建议|操作建议|系统动作|当前动作|当前结论)"
    rf"[^。！？；;，,\n]{{0,24}}{_EXIT_ACTION_TEXT}"
    rf"|(?:建议|推荐|可|可以|考虑|允许|应|应该|应当|需|需要|计划|准备|决定|选择|执行|进行|采取|拟|宜)"
    rf"[^。！？；;，,\n]{{0,12}}{_EXIT_ACTION_TEXT}"
    rf"|(?:立即|立刻|马上|果断|尽快|适度|逐步|分批|部分|逢高|反弹)\s*{_EXIT_ACTION_TEXT}"
    rf"|(?:若|如果|如|一旦|待|当)?[^。！？；;，,\n]{{0,36}}(?:后|时|则)\s*"
    rf"(?:(?:再|择机|适时|伺机|逢高|反弹|适度|逐步|分批|部分)\s*)*{_EXIT_ACTION_TEXT}"
    rf"|{_EXIT_ACTION_TEXT}\s*(?:为主|优先|离场|出局)"
    rf")",
    re.IGNORECASE,
)

_FINAL_BEARISH_ENTRY_VETO_RE = re.compile(
    r"(?:最终建议|最终裁决|最终结论|交易建议|操作建议|系统动作|当前动作|当前结论|方向)"
    r"\s*[:：]?\s*(?:看空|偏空|回避|不宜参与|禁止参与|SELL\b|REDUCE\b|EXIT\b|"
    r"卖出|减仓|清仓|止损|退出|止盈)",
    re.IGNORECASE,
)
_FUTURE_POST_ENTRY_EXIT_RE = re.compile(
    rf"(?:买入|建仓|入场|开仓|持仓)后"
    rf"[^。！？；;，,\n]{{0,40}}{_EXIT_ACTION_TEXT}",
    re.IGNORECASE,
)
_CONDITIONAL_PROTECTIVE_EXIT_RE = re.compile(
    rf"(?:若|如果|如|一旦|当)?[^。！？；;，,\n]{{0,24}}"
    rf"(?:跌破|低于|下破|触及止损)[^。！？；;，,\n]{{0,16}}"
    rf"(?:止损|卖出|减仓|清仓|退出|离场)",
    re.IGNORECASE,
)


def _has_current_exit_directive(text: str) -> bool:
    """Ignore future protective exits that only apply after a new entry."""
    return _current_exit_directive_state(text) is True


def _current_exit_directive_state(text: str) -> bool | None:
    """Return the last current-position exit directive.

    A final HOLD/WAIT supersedes an earlier reduce/exit suggestion, while a
    later explicit exit still wins. Protective exits that only apply after a
    future entry remain outside the current-position decision.
    """
    state: bool | None = None
    current_scope = _strip_action_enum_references(text or "")
    current_scope = _FUTURE_POST_ENTRY_EXIT_RE.sub("", current_scope)
    current_scope = _CONDITIONAL_PROTECTIVE_EXIT_RE.sub("", current_scope)
    current_scope = _NON_EXECUTABLE_STOP_FIELD_RE.sub("", current_scope)
    for clause in re.split(r"[。！？；;，,\n]+", current_scope):
        directive_clause = _NEGATED_EXIT_RE.sub("", clause)
        directive_clause = _ANALYTICAL_EXIT_RE.sub("", directive_clause)
        events: list[tuple[int, bool]] = [
            (match.start(), True)
            for match in _EXIT_DIRECTIVE_RE.finditer(directive_clause)
        ]
        events.extend(
            (match.start(), False)
            for match in _ENTRY_WAIT_RE.finditer(directive_clause)
        )
        for match in _ENGLISH_FINAL_VERDICT_RE.finditer(directive_clause):
            action = match.group("action").upper()
            if action in {"SELL", "HOLD"}:
                events.append((match.start(), action == "SELL"))
        if events:
            state = max(events, key=lambda item: item[0])[1]
    return state


def _has_conditional_entry_directive(text: str) -> bool:
    """Return whether the text conditions an entry, not only its future exit."""
    entry_scope = _FUTURE_POST_ENTRY_EXIT_RE.sub("", text or "")
    entry_scope = _CONDITIONAL_PROTECTIVE_EXIT_RE.sub("", entry_scope)
    entry_scope = _NON_TRADE_PARTICIPATION_RE.sub("", entry_scope)
    entry_scope = _THIRD_PARTY_CONDITIONAL_ENTRY_ACTIVITY_RE.sub("", entry_scope)
    entry_scope = _THIRD_PARTY_ENTRY_ACTIVITY_RE.sub("", entry_scope)
    return bool(_CONDITIONAL_ENTRY_DIRECTIVE_RE.search(entry_scope))


_IMMEDIATE_ENTRY_DIRECTIVE_RE = re.compile(
    rf"(?:立即|立刻|马上)[^。！？；;，,\n]{{0,8}}{_ENTRY_ACTION_TEXT}",
    re.IGNORECASE,
)


def _entry_directive_state(text: str) -> bool | None:
    """Return the last explicit opening directive; facts return ``None``."""
    state: bool | None = None
    semantic_text = _strip_action_enum_references(text or "")
    semantic_text = _FUTURE_POST_ENTRY_EXIT_RE.sub("", semantic_text)
    semantic_text = _CONDITIONAL_PROTECTIVE_EXIT_RE.sub("", semantic_text)
    for clause in re.split(r"[。！？；;，,\n]+", semantic_text):
        directive_clause = _THIRD_PARTY_CONDITIONAL_ENTRY_ACTIVITY_RE.sub("", clause)
        directive_clause = _THIRD_PARTY_ENTRY_ACTIVITY_RE.sub("", directive_clause)
        directive_clause = _THIRD_PARTY_ENTRY_FACT_RE.sub("", directive_clause)
        directive_clause = _COMPLETED_ENTRY_FACT_RE.sub("", directive_clause)
        directive_clause = _ANALYTICAL_ENTRY_RE.sub("", directive_clause)
        directive_clause = _NON_TRADE_PARTICIPATION_RE.sub("", directive_clause)
        # Remove only the non-executable question/discussion span. A final
        # verdict later in the same clause remains authoritative.
        directive_clause = _META_ENTRY_RE.sub("", directive_clause)
        wait_matches = list(_ENTRY_WAIT_RE.finditer(directive_clause))
        if not _ENTRY_ACTION_RE.search(directive_clause):
            if wait_matches:
                state = False
            continue
        if re.search(
            rf"(?:{_ENTRY_ACTION_TEXT})"
            r"(?:逻辑|条件|信号|门禁|规则|限制|禁令|字段|标签|风险)(?:$|\s|[:：])",
            directive_clause,
        ):
            continue
        negative_matches = list(_NEGATED_ENTRY_RE.finditer(directive_clause))
        veto_matches = list(_ENTRY_VETO_RE.finditer(directive_clause))
        blocked_spans = [
            match.span() for match in (*negative_matches, *veto_matches)
        ]
        entry_events: list[tuple[int, bool]] = []
        for match in _ENTRY_DIRECTIVE_RE.finditer(directive_clause):
            action_matches = list(_ENTRY_ACTION_RE.finditer(match.group(0)))
            if not action_matches:
                continue
            action_pos = match.start() + action_matches[-1].start()
            if any(start <= action_pos < end for start, end in blocked_spans):
                continue
            entry_events.append((action_pos, True))
        events = (
            [(match.start(), False) for match in wait_matches]
            + [(match.start(), False) for match in negative_matches]
            + [(match.start(), False) for match in veto_matches]
            + entry_events
        )
        if events:
            # A later positive instruction may supersede an earlier negation;
            # a later WAIT/veto still wins. Embedded positive matches inside a
            # negated phrase are filtered above.
            state = max(events, key=lambda item: item[0])[1]
    return state


def _has_exit_directive(text: str) -> bool:
    """Return whether the plan contains an executable sell-side directive."""
    for clause in re.split(r"[。！？；;，,\n]+", text or ""):
        directive_clause = _NEGATED_EXIT_RE.sub("", clause)
        directive_clause = _ANALYTICAL_EXIT_RE.sub("", directive_clause)
        if _EXIT_DIRECTIVE_RE.search(directive_clause):
            return True
    return False


def _has_executable_constraint_use(text: str, patterns: tuple[str, ...]) -> bool:
    """Detect an actual prohibited plan, not a discussion of its risk."""
    for pattern in patterns:
        for match in re.finditer(pattern, text or "", re.IGNORECASE):
            prefix = (text or "")[max(0, match.start() - 12):match.start()]
            if re.search(
                r"(?:禁止|不得|不允许|不可|不能|不要|不建议|不接受|避免|无需|不)\s*$",
                prefix,
            ):
                continue
            return True
    return False


def _derive_entry_constraint_violations(
    constraints: list[str],
    text: str,
    *,
    entry_intent: bool,
    has_position: bool,
) -> list[str]:
    violations: list[str] = []
    for constraint in constraints:
        normalized = constraint.strip()
        recognized = False
        if re.search(
            r"(?:禁止|不得|不允许|不可|不能|暂不|不要|不建议)\s*"
            r"(?:买入|建仓|入场|开仓|试仓|试多|低吸|新增仓位|"
            r"加仓|补仓|增持|做多|追高|追涨)",
            normalized,
        ):
            recognized = True
            if entry_intent:
                violations.append(normalized)
        elif re.search(
            r"(?:只|仅)\s*(?:观察|观望)|保持空仓",
            normalized,
        ):
            recognized = True
            if entry_intent:
                violations.append(normalized)
        elif "不追高" in normalized:
            recognized = True
            if _has_executable_constraint_use(
                text,
                (
                    r"(?:建议|推荐|可|可以|考虑|允许|应|应该|应当|需|需要|计划|准备|决定|选择|执行|进行|采取)\s*(?:立即|择机)?\s*(?:追高|追涨)",
                    r"(?:追高|追涨)\s*(?:买入|建仓|入场|开仓)",
                ),
            ):
                violations.append(normalized)
        elif normalized in ("不融资", "不加杠杆"):
            recognized = True
            if _has_executable_constraint_use(
                text,
                (
                    r"(?:建议|推荐|可|可以|考虑|允许|应|应该|应当|需|需要|计划|准备|决定|选择|执行|进行|采取)\s*(?:使用|采用|通过|用)?\s*(?:融资|杠杆|借钱|借款)[^。！？；;，,\n]{0,8}(?:买入|建仓|入场|开仓)",
                    r"(?:使用|采用|通过|用)\s*(?:融资|杠杆|借钱|借款)[^。！？；;，,\n]{0,8}(?:买入|建仓|入场|开仓)",
                    r"(?:融资|杠杆|借钱|借款)(?:买入|建仓|入场|开仓)",
                ),
            ):
                violations.append(normalized)
        elif "不能补仓" in normalized:
            recognized = True
            if _has_executable_constraint_use(
                text,
                (
                    r"(?:建议|推荐|可|可以|考虑|允许|应|应该|应当|需|需要|计划|准备|决定|选择|执行|进行|采取)\s*(?:继续|分批|逢低|择机)?\s*补仓",
                    r"(?:若|如果|回踩|跌至|跌到)[^。！？；;，,\n]{0,20}补仓",
                ),
            ):
                violations.append(normalized)
        elif "不接受隔夜" in normalized:
            recognized = True
            # A-share purchases are T+1 sellable. Any new entry necessarily
            # crosses a session boundary, even if the prose promises an
            # impossible same-day exit.
            if entry_intent or _has_executable_constraint_use(
                text,
                (
                    r"(?:建议|推荐|可|可以|考虑|允许|应|应该|应当|需|需要|计划|准备|决定|选择|将|会|拟)\s*(?:持有|持仓)?\s*(?:隔夜|跨日)",
                    r"(?:买入|建仓|入场|开仓)[^。！？；;，,\n]{0,12}(?:持有|持仓)\s*(?:\d+|一|两|三|数)\s*(?:天|周|月)",
                    r"(?:计划|准备|决定|选择|将|会|拟)\s*(?:持有|持仓)\s*(?:至\s*)?(?:次日|明日|后续交易日)",
                ),
            ):
                violations.append(normalized)
        elif normalized.lower() == "只做t+1":
            recognized = True
            if _has_executable_constraint_use(
                text,
                (
                    r"(?:做|执行|采用|进行|计划|建议|可|可以)[^。！？；;，,\n]{0,8}T\+?0",
                    r"日内回转",
                    r"当日买入[^。！？；;，,\n]{0,12}当日卖出",
                ),
            ):
                violations.append(normalized)
        elif re.search(
            r"(?:禁止|不得|不允许|不可|不能|暂不|不要|不建议)\s*"
            r"(?:卖出|减仓|清仓|止损|退出|止盈)",
            normalized,
        ):
            recognized = True
            if has_position and _has_exit_directive(text):
                violations.append(normalized)
        if (entry_intent or has_position) and normalized and not recognized:
            violations.append(f"无法自动验证用户硬约束：{normalized}")
    return violations


def _conflicts(text: str) -> list[str]:
    conflicts: list[str] = []
    has_entry_directive = _entry_directive_state(text) is True
    if "事件前" in text and any(keyword in text for keyword in ("平稳度过", "事件后", "解禁日平稳")):
        conflicts.append("交易计划同时出现“事件前建仓”和“事件平稳后再执行”，执行时点冲突。")
    if has_entry_directive and any(keyword in text for keyword in ("不追高", "追高风险")) and not any(
        keyword in text for keyword in ("回调", "入场区间", "站稳", "触发")
    ):
        conflicts.append("买入建议未给出回调/站稳/触发条件，可能违反不追高约束。")
    if has_entry_directive and not any(keyword in text for keyword in ("止损", "失效")):
        conflicts.append("做多方案缺少明确止损或失效条件。")
    return conflicts


def _extract_stop_loss(
    text: str,
    *,
    prefer_later: bool = False,
) -> tuple[float | None, str]:
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
        "Stop-loss price", "Stop loss price", "Stop-loss", "Stop loss",
    )

    if prefer_later:
        label_pattern = "|".join(
            re.escape(label) for label in sorted(labels, key=len, reverse=True)
        )
        candidates: list[tuple[int, float, str]] = []
        explicit_pattern = re.compile(
            rf"(?:{label_pattern})\s*(?:[：:]\s*)?(?:[¥￥$])?"
            r"([0-9]+(?:\.[0-9]+)?)",
            re.IGNORECASE,
        )
        qualified_pattern = re.compile(
            rf"(?:{label_pattern})[^。！？；;\n]{{0,24}}?"
            r"(?:仍为|维持(?:在|为)?|调整为|改为|设为)\s*(?:[¥￥$])?"
            r"([0-9]+(?:\.[0-9]+)?)",
            re.IGNORECASE,
        )
        conditional_patterns = (
            r'(?:收盘价)?跌破\s*(\d+(?:\.\d+)?)\s*元?.{0,6}(?:止损|清仓|离场)',
            r'(?:止损|清仓|离场).{0,10}跌破\s*(\d+(?:\.\d+)?)\s*元?',
            r'若?.{0,6}(?:跌破|低于|下破)\s*(\d+(?:\.\d+)?)\s*元?.{0,6}(?:止损|清仓)',
        )

        def add_candidate(match: re.Match[str], label: str) -> None:
            if (
                _is_percentage_value(text, match.end(1))
                or _is_likely_indicator_number(text, match.end(1))
                or _is_likely_list_marker(text, match.end(1))
            ):
                return
            candidates.append((match.start(1), float(match.group(1)), label))

        for match in explicit_pattern.finditer(text):
            add_candidate(match, "")
        for match in qualified_pattern.finditer(text):
            add_candidate(match, "")
        for pattern in conditional_patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                add_candidate(match, "条件止损价")
        if candidates:
            _, price, label = max(candidates, key=lambda item: item[0])
            return price, label

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

    # 2. Qualified final values: "止损价暂无调整，仍为9元". The value may
    # follow explanatory wording, so the strict label-number parser above
    # cannot see it even though the final sentence is authoritative.
    label_pattern = "|".join(
        re.escape(label) for label in sorted(labels, key=len, reverse=True)
    )
    qualified_pattern = re.compile(
        rf"(?:{label_pattern})[^。！？；;\n]{{0,24}}?"
        r"(?:仍为|维持(?:在|为)?|调整为|改为|设为)\s*(?:[¥￥$])?"
        r"([0-9]+(?:\.[0-9]+)?)",
        re.IGNORECASE,
    )
    qualified_matches = list(qualified_pattern.finditer(text))
    for match in reversed(qualified_matches):
        if (
            _is_percentage_value(text, match.end(1))
            or _is_likely_indicator_number(text, match.end(1))
            or _is_likely_list_marker(text, match.end(1))
        ):
            continue
        return float(match.group(1)), ""

    # 3. Conditional stop-loss: "收盘价跌破X元" / "跌破X元止损"
    cond_patterns = [
        r'(?:收盘价)?跌破\s*(\d+(?:\.\d+)?)\s*元?.{0,6}(?:止损|清仓|离场)',
        r'(?:止损|清仓|离场).{0,10}跌破\s*(\d+(?:\.\d+)?)\s*元?',
        r'若?.{0,6}(?:跌破|低于|下破)\s*(\d+(?:\.\d+)?)\s*元?.{0,6}(?:止损|清仓)',
    ]
    for cp in cond_patterns:
        cm = re.search(cp, text)
        if cm:
            if (
                _is_percentage_value(text, cm.end(1))
                or _is_likely_indicator_number(text, cm.end(1))
                or _is_likely_list_marker(text, cm.end(1))
            ):
                continue
            try:
                price = float(cm.group(1))
                return price, '条件止损价'
            except ValueError:
                pass

    # 4. Fallback: last occurrence in full text (prefer_later)
    price = _first_price(text, labels, prefer_later=True)
    return price, ''


def _field_explicitly_invalidated(text: str, labels: tuple[str, ...]) -> bool:
    """Return whether the last authoritative field occurrence withdraws it."""
    label_pattern = "|".join(
        re.escape(label) for label in sorted(labels, key=len, reverse=True)
    )
    source = text or ""
    events: list[tuple[int, bool]] = []
    invalid_pattern = re.compile(
        rf"(?:{label_pattern})\s*(?:[：:]\s*)?"
        r"(?:(?:暂时|暂|目前|当前|尚|仍)\s*)?"
        r"(?:待定|不适用|取消(?:执行)?|撤销(?:执行)?|"
        r"不(?:再)?(?:设置|设定|设|采用|使用|执行)|"
        r"未设置(?:明确|具体)?(?:数值|价格|价位)?|"
        r"未给出(?:明确|具体)?(?:数值|价格|价位)?|"
        r"无法确定(?:明确|具体)?(?:数值|价格|价位)?|"
        r"暂无(?:明确|具体)?(?:数值|价格|价位)?|无|—+|--?|"
        r"not applicable|cancelled|canceled|withdrawn|unset|not set|"
        r"TBD|N/?A|none)"
        r"(?=\s*(?:$|[。！？；;，,\n（(.!?]))"
        rf"|(?:取消|撤销)\s*(?:{label_pattern})"
        rf"|(?:(?:最终(?:决定|结论|建议)?|当前|目前|暂时|现阶段)\s*)"
        rf"不(?:再)?(?:设置|设定|设|采用|使用|执行)\s*(?:{label_pattern})",
        re.IGNORECASE,
    )
    events.extend((match.end(), True) for match in invalid_pattern.finditer(source))
    suffix_invalid_pattern = re.compile(
        rf"(?:{label_pattern})[^。！？；;\n]{{0,24}}?"
        r"(?:[¥￥$])?[0-9]+(?:\.[0-9]+)?\s*元?"
        r"[^。！？；;\n]{0,16}?"
        r"(?:已|已经|现已|当前已|目前已)?\s*"
        r"(?:取消(?:执行)?|撤销(?:执行)?|作废|cancelled|canceled|withdrawn)",
        re.IGNORECASE,
    )
    events.extend(
        (match.end(), True) for match in suffix_invalid_pattern.finditer(source)
    )
    valid_pattern = re.compile(
        rf"(?:{label_pattern})\s*(?:[：:]\s*)?(?:[¥￥$])?"
        r"([0-9]+(?:\.[0-9]+)?)",
        re.IGNORECASE,
    )
    for match in valid_pattern.finditer(source):
        if (
            _is_percentage_value(source, match.end(1))
            or _is_likely_indicator_number(source, match.end(1))
            or _is_likely_list_marker(source, match.end(1))
        ):
            continue
        events.append((match.start(1), False))
    qualified_valid_pattern = re.compile(
        rf"(?:{label_pattern})[^。！？；;\n]{{0,24}}?"
        r"(?:仍为|维持(?:在|为)?|调整为|改为|设为)\s*(?:[¥￥$])?"
        r"([0-9]+(?:\.[0-9]+)?)",
        re.IGNORECASE,
    )
    for match in qualified_valid_pattern.finditer(source):
        if (
            _is_percentage_value(source, match.end(1))
            or _is_likely_indicator_number(source, match.end(1))
            or _is_likely_list_marker(source, match.end(1))
        ):
            continue
        events.append((match.start(1), False))
    conditional_valid_patterns = (
        r'(?:收盘价)?跌破\s*(\d+(?:\.\d+)?)\s*元?.{0,6}(?:止损|清仓|离场)',
        r'(?:止损|清仓|离场).{0,10}跌破\s*(\d+(?:\.\d+)?)\s*元?',
        r'若?.{0,6}(?:跌破|低于|下破)\s*(\d+(?:\.\d+)?)\s*元?.{0,6}(?:止损|清仓)',
    )
    for pattern in conditional_valid_patterns:
        for match in re.finditer(pattern, source, re.IGNORECASE):
            if (
                _is_percentage_value(source, match.end(1))
                or _is_likely_indicator_number(source, match.end(1))
                or _is_likely_list_marker(source, match.end(1))
            ):
                continue
            events.append((match.start(1), False))
    return max(events, key=lambda item: item[0])[1] if events else False


def build_trade_quality_check(
    *,
    investment_plan: str = "",
    trader_plan: str = "",
    final_decision: str = "",
    user_context: Mapping[str, Any] | None = None,
    position_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a deterministic execution checklist from the final reports."""
    text = _strip_generated_quality_section("\n".join([investment_plan or "", trader_plan or "", final_decision or ""]))
    conflicts = _conflicts(text)
    constraints = [str(item) for item in (user_context or {}).get("constraints", [])]
    setup_type = _setup_type(text)
    execution_mode = _execution_mode(text)
    # A target is an analytical objective, not proof that an entry trigger
    # exists. Keep the two concepts separate so observation reports cannot be
    # promoted into conditional entries merely because they quote a target.
    trigger_labels = (
        "触发价", "触发位", "突破价", "突破位", "站稳",
        "Entry trigger", "Trigger price", "Entry price",
    )
    range_labels = (
        "入场区间", "买入区间", "建仓区间", "Entry range", "Buy range",
    )
    stop_labels = (
        "止损价", "止损位", "初始止损", "硬性止损", "止损红线", "止损线",
        "止损", "失效价", "失效位", "防守位",
        "Stop-loss price", "Stop loss price", "Stop-loss", "Stop loss",
    )
    trigger_price = _first_price(text, trigger_labels)
    stop_loss_price, sl_label = _extract_stop_loss(text)
    entry_range = _find_price_range(text)
    final_text = _strip_generated_quality_section(final_decision or "")
    final_semantic_text = _strip_action_enum_references(final_text)
    final_entry_state = _entry_directive_state(final_semantic_text)
    final_exit_state = _current_exit_directive_state(final_semantic_text)
    english_final_verdicts = list(_ENGLISH_FINAL_VERDICT_RE.finditer(final_semantic_text))
    english_final_action: str | None = None
    if english_final_verdicts:
        english_final_action = english_final_verdicts[-1].group("action").upper()
        final_entry_state = english_final_action == "BUY"
    if (
        final_exit_state is True
        or _FINAL_BEARISH_ENTRY_VETO_RE.search(final_semantic_text)
    ):
        final_entry_state = False
    entry_intent = (
        final_entry_state
        if final_entry_state is not None
        else _entry_directive_state(text) is True
    )
    entry_levels_invalidated = False
    stop_level_invalidated = False
    explicit_trigger_labels = (
        "触发价", "触发位", "突破价", "突破位",
        "Entry trigger", "Trigger price", "Entry price",
    )
    final_trigger = _first_price(
        final_semantic_text,
        trigger_labels if final_entry_state is True else explicit_trigger_labels,
        prefer_later=True,
    )
    final_range = _find_price_range(final_semantic_text)
    final_stop, final_sl_label = _extract_stop_loss(
        final_semantic_text,
        prefer_later=True,
    )
    stop_level_invalidated = _field_explicitly_invalidated(
        final_semantic_text, stop_labels,
    )
    # Final execution fields are authoritative even when the final response
    # only revises a level and does not repeat the upstream buy directive.
    entry_levels_invalidated = _field_explicitly_invalidated(
        final_semantic_text, trigger_labels + range_labels,
    )
    if entry_levels_invalidated:
        trigger_price = None
        entry_range = None
    else:
        if final_trigger is not None:
            trigger_price = final_trigger
        if final_range is not None:
            entry_range = final_range
    if stop_level_invalidated:
        stop_loss_price, sl_label = None, ""
    elif final_stop is not None:
        stop_loss_price, sl_label = final_stop, final_sl_label
    entry_scope = final_semantic_text if final_entry_state is not None else text
    conditional_entry_intent = bool(
        entry_intent
        and (
            _has_conditional_entry_directive(entry_scope)
            or bool(entry_range)
            or (english_final_action == "BUY" and trigger_price is not None)
        )
    )
    if conditional_entry_intent:
        execution_mode = "条件触发"
    elif entry_intent and _IMMEDIATE_ENTRY_DIRECTIVE_RE.search(entry_scope):
        execution_mode = "立即执行"
    elif entry_intent and execution_mode == "条件触发":
        # A report-wide future condition (often a post-entry stop) does not
        # turn an unconditional entry instruction into a conditional setup.
        execution_mode = "人工复核"

    action = "人工复核"
    if conflicts:
        action = "等待人工复核"
    elif execution_mode == "条件触发":
        action = "等待触发"
    elif execution_mode == "立即执行":
        action = "可执行但需核对盘面"

    do_not_trade_if = []
    explicit_constraint_violations = [
        str(item)
        for item in (user_context or {}).get("constraint_violations", [])
        if str(item).strip()
    ]
    context = user_context or {}
    current_position = context.get("current_position")
    current_position_pct = context.get("current_position_pct")
    _has_position = bool(
        (current_position is not None and current_position > 0)
        or (current_position_pct is not None and current_position_pct > 0)
        or (
            isinstance(position_context, Mapping)
            and position_context.get("has_position") is True
        )
    )
    constraint_violations = list(dict.fromkeys(
        _derive_entry_constraint_violations(
            constraints,
            text,
            entry_intent=entry_intent,
            has_position=_has_position,
        )
        + explicit_constraint_violations
    ))
    if constraint_violations:
        do_not_trade_if.append(
            "交易计划违反用户硬约束：" + "；".join(constraint_violations)
        )
    if entry_intent and entry_levels_invalidated:
        do_not_trade_if.append("最终决策中的入场触发价或区间尚未确认。")
    if stop_level_invalidated and (entry_intent or _has_position):
        do_not_trade_if.append("最终决策中的止损价已撤销或尚未确认。")
    # [P1-3] Stop-loss tolerance: skip stop-loss error for no-position without conditional entry
    _is_conditional_entry = conditional_entry_intent
    if _is_conditional_entry and stop_loss_price is not None:
        entry_reference = trigger_price
        if entry_reference is None and entry_range:
            range_bounds = _price_range_bounds(entry_range)
            if range_bounds is None:
                do_not_trade_if.append("入场区间必须包含两个不同的有效价格。")
            else:
                entry_reference = range_bounds[0]
        if stop_loss_price <= 0:
            do_not_trade_if.append("止损价必须为正数。")
        elif entry_reference is not None and stop_loss_price >= entry_reference:
            do_not_trade_if.append("止损价必须低于入场触发价。")
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
        "entry_intent": entry_intent,
        "exit_intent": final_exit_state is True,
        "conditional_entry_intent": conditional_entry_intent,
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
