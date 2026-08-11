# TradingAgents/graph/signal_processing.py

import re
import json
from dataclasses import dataclass
from typing import Optional

from langchain_openai import ChatOpenAI
from tradingagents.dataflows.config import get_config
from tradingagents.prompts import get_prompt


# [REPORT-UX-003] wait_reason_codes
WAIT_REASON_DATA_MISSING = "DATA_MISSING"
WAIT_REASON_GATE_BLOCKED = "GATE_BLOCKED"
WAIT_REASON_CONFLICT = "CONFLICT"
WAIT_REASON_NO_TRIGGER = "NO_TRIGGER"
WAIT_REASON_RISK_FIRST = "RISK_FIRST"
WAIT_REASON_NORMAL_NO_DATA = "NORMAL_NO_DATA"
WAIT_REASON_ACTION_NOT_APPLICABLE = "ACTION_NOT_APPLICABLE"

WAIT_REASON_LABELS: dict[str, str] = {
    WAIT_REASON_DATA_MISSING: "关键数据缺口",
    WAIT_REASON_GATE_BLOCKED: "门禁未通过",
    WAIT_REASON_CONFLICT: "结论冲突",
    WAIT_REASON_NO_TRIGGER: "等待触发价",
    WAIT_REASON_RISK_FIRST: "风险优先",
    WAIT_REASON_NORMAL_NO_DATA: "数据正常·暂无触发",
    WAIT_REASON_ACTION_NOT_APPLICABLE: "持仓动作不适用",
}

# Blocker keys whose failure genuinely blocks a directional verdict, vs. aux
# sources that are normally no-data on most trading days.
_CRITICAL_BLOCKER_KEYS = {"ohlcv_5d", "individual_fund_flow"}
_SEVERE_BLOCKER_STATUSES = {"query_failed", "field_missing"}

# Text patterns reused from readiness_score for conflict detection (kept local
# to avoid a circular import; signal_processing must stay dependency-light).
_CONFLICT_TEXT_PATTERNS = [
    r"(?:冲突|矛盾|不一致)",
    r"(?:多空|多空双方).*?(?:分歧|对立)",
    r"(?:分析师|模块|上游).*?(?:分歧|冲突|矛盾)",
    r"verdict.*?分歧",
    r"(?:短中线|中线.*?短线).*?(?:冲突|矛盾|分歧)",
]


def _text_has_conflict(text: str) -> bool:
    if not text:
        return False
    return any(re.search(p, text, re.IGNORECASE) for p in _CONFLICT_TEXT_PATTERNS)


def compute_wait_reason_codes(
    *,
    execution_action: Optional[str],
    research_direction: Optional[str],
    action_label: Optional[str] = None,
    has_position: Optional[bool] = None,
    trigger_price: Optional[float] = None,
    gate_blocked: bool = False,
    data_insufficient: bool = False,
    data_blockers: Optional[list] = None,
    has_conflict: bool = False,
) -> list[str]:
    """[REPORT-UX-003] wait_reason_codes — decompose a WAIT/观察 verdict into
    one or more explainable reason codes.

    The user-facing complaint was that every recent report collapsed into the
    single catch-all label "数据不足观察". This helper keeps the strong action
    gate intact and instead explains *why* the action is WAIT:

    - ``DATA_MISSING``: a critical field (行情/K线、主力资金) has a severe
      blocker (``query_failed`` / ``field_missing``), or the decision text
      itself declares the evidence insufficient.
    - ``GATE_BLOCKED``: the Strong Action Gate failed, forcing WAIT even when
      a direction was otherwise available.
    - ``CONFLICT``: 中线/短线 or 分析师结论冲突 detected in the decision text.
    - ``NO_TRIGGER``: 方向偏多 but no trigger_price / target price was produced
      (waiting for an entry condition).
    - ``RISK_FIRST``: 方向偏空/看空 and the user is not holding — risk avoidance
      takes priority (maps to the "回避" label).
    - ``NORMAL_NO_DATA``: data sources are healthy and no conflict exists, but
      there is simply no actionable signal yet (neutral watch).

    Returns an empty list for non-WAIT actions so the frontend can treat the
    absence of codes as "no wait reason to explain".
    """
    if (execution_action or "").upper() != "WAIT":
        return []

    codes: list[str] = []

    has_critical_blocker = False
    if isinstance(data_blockers, list):
        for blocker in data_blockers:
            if not isinstance(blocker, dict):
                continue
            if (
                blocker.get("key") in _CRITICAL_BLOCKER_KEYS
                and blocker.get("status") in _SEVERE_BLOCKER_STATUSES
            ):
                has_critical_blocker = True
                break

    if has_critical_blocker or data_insufficient:
        codes.append(WAIT_REASON_DATA_MISSING)

    if gate_blocked:
        codes.append(WAIT_REASON_GATE_BLOCKED)

    if has_conflict:
        codes.append(WAIT_REASON_CONFLICT)

    # RISK_FIRST — 偏空/看空 + 未持仓 → "回避" 路径，风险优先于入场。
    if research_direction in ("偏空", "看空") and (
        has_position is False or has_position is None
    ):
        codes.append(WAIT_REASON_RISK_FIRST)

    # NO_TRIGGER — 偏多/看多 but no trigger price supplied.
    if research_direction in ("偏多", "看多") and not trigger_price:
        codes.append(WAIT_REASON_NO_TRIGGER)

    # Fallback: data is fine and there is no conflict/directional reason —
    # the system simply has no actionable signal to act on this round.
    if not codes:
        codes.append(WAIT_REASON_NORMAL_NO_DATA)

    return codes


@dataclass
class DecisionSemantics:
    research_direction: str
    execution_action: str
    action_label: str
    decision: str
    # [REPORT-UX-003] wait_reason_codes — populated when execution_action == WAIT.
    wait_reason_codes: Optional[list] = None

    def to_dict(self) -> dict:
        return {
            "research_direction": self.research_direction,
            "execution_action": self.execution_action,
            "action_label": self.action_label,
            "decision": self.decision,
            "wait_reason_codes": self.wait_reason_codes or [],
        }


_RESEARCH_DIRECTION_MAP = {
    "看多": "看多",
    "BULLISH": "看多",
    "BUY": "看多",
    "偏多": "偏多",
    "看空": "看空",
    "BEARISH": "看空",
    "SELL": "看空",
    "偏空": "偏空",
    "中性": "中性",
    "NEUTRAL": "中性",
    "HOLD": "中性",
    "谨慎": "中性",
    "CAUTIOUS": "中性",
}

_DECISION_MAP = {
    "看多": "BUY",
    "偏多": "BUY",
    "中性": "HOLD",
    "偏空": "SELL",
    "看空": "SELL",
}

_NEGATION_PATTERNS = (
    r"不(?:建议|宜|应|要|能|可|考虑|允许|支持)",
    r"无需",
    r"别",
    r"切勿",
    r"勿",
    r"避免",
    r"谨防",
    r"暂不",
    r"回避",
    r"禁止",
)


def _negation_scope_prefix(prefix_window: str) -> str:
    """Return the active semantic prefix, treating list commas conservatively."""
    prefix = re.split(
        r"(?:但是|不过|然而|而是|但)|[\n，。；;！？!?]",
        prefix_window,
    )[-1]
    if "、" in prefix:
        before, after = prefix.rsplit("、", 1)
        has_prior_negation = re.search(
            r"(?:回避|避免|谨防|禁止|切勿|勿|别|无需|暂不|"
            r"不(?:建议|宜|应|要|能|可|考虑|允许|支持))[^、]*$",
            before,
        )
        starts_new_instruction = re.match(
            r"\s*(?:(?:仍|继续|转为|改为)\s*)?"
            r"(?:谨慎看多|谨慎看空|谨慎$|看多|看空|偏多|偏空|中性|观望|等待|"
            r"回避|避免|禁止|切勿|勿|别|无需|暂不|建议|可以|可|应该|应|"
            r"考虑|回调后|反弹后|企稳后|突破后|确认后|条件建仓|条件买入|"
            r"低吸|逢低布局|减仓|卖出|清仓|止损|建仓|买入)",
            after,
        )
        # Only an explicit new instruction ends the earlier negation. A list of
        # conditions such as "不建议在业绩披露、资金改善前建仓" stays one clause.
        if has_prior_negation and starts_new_instruction:
            return after
    return prefix


def _iter_keyword_occurrences(snippet: str, keyword: str):
    """Yield semantic keyword occurrences without matching inside ASCII words."""
    if keyword.isascii():
        # BUY/SELL are action tokens only when they stand alone. Without the
        # bounded match, prose such as "buyback" or "sell-side rating" can
        # override a structured VERDICT.
        pattern = re.compile(
            rf"(?<![A-Z0-9_-]){re.escape(keyword.upper())}(?![A-Z0-9_-])"
        )
        for match in pattern.finditer(snippet.upper()):
            yield match.start(), match.end()
        return

    start = 0
    while True:
        index = snippet.find(keyword, start)
        if index == -1:
            return
        yield index, index + len(keyword)
        start = index + 1


def _keyword_negation_states(snippet: str, keywords: list[str]):
    """Yield whether each keyword occurrence is negated by its local clause."""
    snippet_upper = snippet.upper()
    for keyword in keywords:
        key_upper = keyword.upper()
        for index, end in _iter_keyword_occurrences(snippet_upper, key_upper):
            prefix_window = snippet_upper[:index]
            # Contrastive conjunctions start a new semantic clause even when
            # the model omits punctuation: "不建议买入但仍看多".
            prefix = _negation_scope_prefix(prefix_window)
            suffix = snippet_upper[end:]
            rejected_after_keyword = bool(
                re.match(
                    r"^[^，、。；;！？!?\n]{0,8}"
                    r"(?:不成立|未成立|难成立|无法成立|已?失效|已?证伪|被否定|不可持续)",
                    suffix,
                )
            )
            negated = rejected_after_keyword or prefix.endswith(("不", "非")) or any(
                re.search(
                    rf"{pattern}[^，。；;！？!?\n]*$",
                    prefix,
                )
                for pattern in _NEGATION_PATTERNS
            )
            yield negated


def _has_non_negated_keyword(snippet: str, keywords: list[str]) -> bool:
    """Return True when a keyword is present without a nearby negation marker."""
    return any(not negated for negated in _keyword_negation_states(snippet, keywords))


def _has_negated_keyword(snippet: str, keywords: list[str]) -> bool:
    """Return True when a keyword is explicitly negated in its local clause."""
    return any(_keyword_negation_states(snippet, keywords))


def _has_non_negated_action_keyword(snippet: str, keywords: list[str]) -> bool:
    """Match executable actions while excluding noun phrases such as 卖出压力."""
    snippet_upper = snippet.upper()
    noun_suffix = re.compile(
        r"^(?:压力|盘|信号|金额|数据|意愿|力量|成交|占比|行为|记录|"
        r"交易|计划|公告|传闻|风险|预期|价格|价位|成本|区间|条件|"
        r"机会|时机|潮)"
    )
    action_context = re.compile(
        r"(?:建议|应当|应该|需|需要|可|可以|考虑|立即|择机|分批|执行|"
        r"继续|维持|转为|改为|"
        r"触发|当前)[^，、。；;！？!?\n]{0,16}$"
    )
    timing_context = re.compile(r"[^，、。；;！？!?\n]{0,16}(?:后|时|则|再)\s*$")
    generic_actions = {"买入", "卖出", "减持", "减仓", "清仓", "空仓", "退出"}

    for keyword in keywords:
        key_upper = keyword.upper()
        states = iter(_keyword_negation_states(snippet, [keyword]))
        for index, end in _iter_keyword_occurrences(snippet_upper, key_upper):
            negated = next(states, True)
            suffix = snippet[end:].lstrip()
            prefix = re.split(
                r"(?:但是|不过|然而|而是|但)|[\n，、。；;！？!?]",
                snippet[:index],
            )[-1].strip()
            is_ascii_action = keyword.isascii()
            is_compound_action = keyword not in generic_actions
            if (
                not negated
                and not noun_suffix.match(suffix)
                and (
                    not prefix
                    or is_ascii_action
                    or is_compound_action
                    or action_context.search(prefix)
                    or timing_context.search(prefix)
                )
            ):
                return True
    return False


def _has_explicit_avoidance_action(snippet: str) -> bool:
    """Recognize advice to avoid the instrument, not prose about avoiding risk."""
    return bool(
        re.search(
            r"(?:建议|应当|应该|需|需要|继续|维持|选择|当前建议)"
            r"[^，、。；;！？!?\n]{0,8}回避"
            r"(?:\s*(?:该股|标的|个股|股票|参与|介入|买入))?"
            r"(?=[，、。；;！？!?\n]|$)",
            snippet,
            re.IGNORECASE,
        )
    )


def _parse_research_direction_from_verdict(text: str) -> str | None:
    verdict_scope = _semantic_model_scope(text)
    matches = list(re.finditer(r"<!--\s*VERDICT:\s*(\{.*?\})\s*-->", verdict_scope, re.IGNORECASE | re.DOTALL))
    if not matches:
        return None
    try:
        payload = json.loads(matches[-1].group(1))
    except Exception:
        return None
    direction = str(payload.get("direction", "")).strip()
    return _RESEARCH_DIRECTION_MAP.get(direction.upper()) or _RESEARCH_DIRECTION_MAP.get(direction)


def _classify_research_direction(snippet: str) -> str | None:
    sell_action_kw = ["SELL", "卖出", "减持", "减仓", "清仓", "空仓"]
    sell_direction_kw = ["回避", "看空", "偏空"]
    buy_action_kw = ["BUY", "买入", "增持", "做多", "有条件建仓", "条件建仓", "建仓"]
    buy_direction_kw = ["看多", "偏多", "谨慎看多"]
    hold_kw = ["HOLD", "观望", "持有", "中性"]
    if _has_explicit_avoidance_action(snippet):
        return "偏空"
    if _has_non_negated_keyword(snippet, buy_direction_kw):
        return "偏多"
    if _has_non_negated_action_keyword(snippet, buy_action_kw):
        return "偏多"
    if _has_non_negated_keyword(snippet, sell_direction_kw):
        return "偏空"
    if _has_non_negated_action_keyword(snippet, sell_action_kw):
        return "偏空"
    if _has_non_negated_keyword(snippet, hold_kw):
        return "中性"
    return None


def _strip_system_overrides(text: str) -> str:
    stripped = re.sub(r"⚠?\s*\[C-\d+\].*", "", text)
    # Explicit enum documentation is useful to readers but is not a directional
    # signal. Keep it in the report while excluding it from semantic parsing.
    return re.sub(
        r"(?im)(?:"
        r"(?:^\s*[-*]\s*)?\*{0,2}"
        r"(?:分类标签|动作枚举|决策枚举|可选动作|动作集合|动作标签)\s*"
        r"(?:[:：]\s*\*{0,2}|\*{0,2}\s*[:：]\s*\*{0,2})"
        r"(?:BUY\s*[/／]\s*SELL\s*[/／]\s*HOLD|"
        r"ENTER\s*[/／]\s*WAIT\s*[/／]\s*HOLD\s*[/／]\s*REDUCE\s*[/／]\s*EXIT)"
        r"\*{0,2}"
        r"|"
        r"(?:BUY\s*[/／]\s*SELL\s*[/／]\s*HOLD|"
        r"ENTER\s*[/／]\s*WAIT\s*[/／]\s*HOLD\s*[/／]\s*REDUCE\s*[/／]\s*EXIT)"
        r"\s*(?:三分类标签|分类标签|动作枚举|决策枚举|可选动作|动作集合|动作标签)"
        r")",
        "",
        stripped,
    )


def _semantic_model_scope(text: str) -> str:
    """Exclude only a validated application diagnostics tail from semantics."""
    source = text or ""
    trusted_tail_removed = False
    marker_matches = list(
        re.finditer(re.escape(_APPLICATION_DIAGNOSTICS_MARKER), source)
    )
    for marker in reversed(marker_matches):
        tail = source[marker.end():]
        quality = re.search(r"(?m)^#{1,6}\s*执行质检\s*$", tail)
        summary = re.search(r"(?m)^#{1,6}\s*系统执行结论\s*$", tail)
        if quality and summary and quality.start() < summary.start():
            source = source[:marker.start()]
            trusted_tail_removed = True
            break
    if not trusted_tail_removed:
        # Legacy reports may append history after an untrusted quality heading.
        # Remove only the known generated/history lines; keep later model-authored
        # final recommendations so a heading cannot hide a WAIT/no-buy veto.
        has_quality_heading = bool(
            re.search(r"(?m)^#{1,6}\s*执行质检\s*$", source)
        )
        if has_quality_heading:
            source = re.sub(
                r"(?ms)^#{1,6}\s*系统执行结论\s*$.*\Z",
                "",
                source,
            )
            source = re.sub(r"(?m)^\s*[-*]\s*系统动作[：:].*$", "", source)
        source = re.sub(r"(?m)^\s*上一版结论[：:].*$", "", source)
        source = re.sub(r"(?m)^.*\[C-005\].*$", "", source)
    return _strip_system_overrides(source)


def _infer_research_direction(text: str) -> str:
    rd = _parse_research_direction_from_verdict(text)
    if rd:
        return rd

    stripped = _semantic_model_scope(text)

    explicit_patterns = [
        r"系统动作[:：]\s*([^\n*]+)",
        r"最终裁决[:：]\s*([^\n*]+)",
        r"风控委员会最终裁决[:：]\s*([^\n*]+)",
        r"最终建议[:：]\s*([^\n*]+)",
        r"方向[:：]\s*([^\n*]+)",
        r"核心定性[:：]\s*([^\n*]+)",
    ]
    for pattern in explicit_patterns:
        match = re.search(pattern, stripped, re.IGNORECASE)
        if match:
            rd = _classify_research_direction(match.group(1).strip())
            if rd:
                return rd

    headline = "\n".join(stripped.splitlines()[:20])
    rd = _classify_research_direction(headline)
    if rd:
        return rd

    rd = _classify_research_direction(stripped.upper())
    if rd:
        return rd

    return "中性"


def _latest_explicit_recommendation(text: str) -> str | None:
    """Return the highest-priority recommendation before execution QA.

    Final verdict/recommendation fields are execution instructions. Broad
    ``方向`` and ``核心定性`` fields are only fallbacks; a later diagnostic
    direction must not override an earlier explicit "do not buy/sell" action.
    """
    stripped = _semantic_model_scope(text)
    decision_patterns = [
        r"最终裁决[:：]\s*([^\n*]+)",
        r"风控委员会最终裁决[:：]\s*([^\n*]+)",
        r"最终建议[:：]\s*([^\n*]+)",
    ]
    direction_patterns = [
        r"方向[:：]\s*([^\n*]+)",
        r"核心定性[:：]\s*([^\n*]+)",
    ]
    for patterns in (decision_patterns, direction_patterns):
        matches = [
            match
            for pattern in patterns
            for match in re.finditer(pattern, stripped, re.IGNORECASE)
        ]
        if matches:
            return max(matches, key=lambda match: match.start()).group(1).strip()
    return None


def _infer_explicit_sell_action(
    text: str,
    *,
    has_position: Optional[bool],
) -> str | None:
    """Extract an explicit reduce/exit instruction without changing research direction."""
    snippet = _latest_explicit_recommendation(text)
    if not snippet:
        return None
    exit_keywords = ["SELL", "卖出", "清仓", "空仓", "退出", "止损离场"]
    reduce_keywords = ["减持", "减仓"]

    if _has_explicit_avoidance_action(snippet):
        return "REDUCE" if has_position is True else "WAIT"
    if _has_non_negated_action_keyword(snippet, exit_keywords):
        return "EXIT" if has_position is True else "WAIT"
    if _has_non_negated_action_keyword(snippet, reduce_keywords):
        return "REDUCE" if has_position is True else "WAIT"
    return None


def _infer_explicit_hold_action(
    text: str,
    *,
    has_position: Optional[bool],
) -> str | None:
    """Honor an explicit instruction not to buy or sell.

    Research direction can remain bullish or bearish, but a latest explicit
    negation must prevent the structured execution layer from reconstructing
    ENTER/REDUCE/EXIT from that direction.
    """
    snippet = _latest_explicit_recommendation(text)
    if not snippet:
        return None

    buy_action_keywords = [
        "BUY",
        "买入",
        "增持",
        "做多",
        "有条件建仓",
        "条件建仓",
        "建仓",
    ]
    sell_action_keywords = [
        "SELL",
        "卖出",
        "减持",
        "减仓",
        "清仓",
        "空仓",
        "退出",
        "止损离场",
    ]
    action_keywords = buy_action_keywords + sell_action_keywords

    # A later executable instruction such as "不建议追高，回调后条件建仓"
    # takes precedence over the earlier caveat.
    if _has_non_negated_action_keyword(snippet, action_keywords):
        return None
    if _has_negated_keyword(snippet, action_keywords):
        return "HOLD" if has_position is True else "WAIT"
    return None


def _infer_explicit_legacy_override(
    text: str,
    *,
    has_position: Optional[bool],
) -> str | None:
    """Map explicit execution advice before consulting research-direction VERDICT."""
    snippet = _latest_explicit_recommendation(text)
    if not snippet:
        return None

    sell_action_keywords = [
        "SELL",
        "卖出",
        "减持",
        "减仓",
        "清仓",
        "空仓",
        "退出",
        "止损离场",
    ]
    buy_action_keywords = ["BUY", "买入", "增持", "做多", "有条件建仓", "条件建仓", "建仓"]
    sell_direction_keywords = ["回避", "看空", "偏空"]

    if _has_explicit_avoidance_action(snippet):
        return "HOLD" if has_position is False else "SELL"
    if _has_non_negated_action_keyword(snippet, sell_action_keywords):
        return "HOLD" if has_position is False else "SELL"
    if _has_non_negated_action_keyword(snippet, buy_action_keywords):
        return "BUY"
    if _has_negated_keyword(snippet, buy_action_keywords):
        if _has_non_negated_keyword(snippet, sell_direction_keywords):
            return "SELL"
        return "HOLD"
    if _has_negated_keyword(snippet, sell_action_keywords):
        return "HOLD"
    return None


def _is_data_insufficient(text: str) -> bool:
    indicators = [
        r"数据\s*(不足|缺失|不全|有限)",
        r"(?:无法|未能).{0,10}(?:判断|确认|给出)",
        r"证据.{0,6}不足",
        r"source[_\s]?coverage.{0,10}(?:<|低|不足)",
        r"evidence[_\s]?coverage.{0,10}(?:<|低|不足)",
    ]
    return any(re.search(p, text, re.IGNORECASE) for p in indicators)


def _derive_action_label(
    has_position: Optional[bool],
    research_direction: str,
    execution_action: str,
    trigger_price: Optional[float] = None,
    invalid_price: Optional[float] = None,
) -> str:
    if execution_action == "WAIT":
        if research_direction == "中性" and not trigger_price:
            if has_position is False or has_position is None:
                return "数据不足观察" if _is_data_insufficient("") else "观望"
            return "数据不足观察"
        if research_direction == "偏多":
            return "等待触发"
        if research_direction == "看多":
            return "等待触发" if not trigger_price else "条件入场"
        if research_direction in ("偏空", "看空"):
            if has_position is False or has_position is None:
                return "回避"
            return "数据不足观察"
        return "观望"
    if execution_action == "ENTER":
        if trigger_price:
            return "条件入场"
        return "入场"
    if execution_action == "HOLD":
        return "持有"
    if execution_action == "REDUCE":
        return "条件减仓"
    if execution_action == "EXIT":
        return "条件清仓"
    return execution_action


def _resolve_execution_action(
    has_position: Optional[bool],
    research_direction: str,
    trigger_price: Optional[float] = None,
    invalid_price: Optional[float] = None,
    *,
    gate_blocked: bool = False,
    data_insufficient: bool = False,
) -> str:
    if gate_blocked:
        return "WAIT"
    if data_insufficient:
        return "WAIT"

    if research_direction == "看多":
        if has_position is False:
            return "ENTER" if trigger_price else "WAIT"
        return "HOLD"

    if research_direction == "偏多":
        if has_position is False:
            return "ENTER" if trigger_price else "WAIT"
        return "HOLD"

    if research_direction == "中性":
        if has_position:
            return "HOLD"
        return "WAIT"

    if research_direction == "偏空":
        if has_position:
            return "REDUCE" if trigger_price else "REDUCE"
        return "WAIT"

    if research_direction == "看空":
        if has_position:
            return "EXIT"
        return "WAIT"

    return "WAIT"


class SignalProcessor:
    """Processes trading signals to extract actionable decisions."""

    def __init__(self, quick_thinking_llm: ChatOpenAI):
        """Initialize with an LLM for processing."""
        self.quick_thinking_llm = quick_thinking_llm

    def process_signal(self, full_signal: str, *, has_position: bool | None = None) -> str:
        """
        Process a full trading signal to extract the core decision.

        Args:
            full_signal: Complete trading signal text
            has_position: Whether the user currently holds the stock.
                When False, HOLD is mapped to WAIT/观察 (P1-1).

        Returns:
            Extracted decision (BUY, SELL, HOLD, or WAIT)
        """
        if not full_signal:
            return "WAIT" if has_position is False else "HOLD"

        decision = _extract_decision_keyword(full_signal, has_position=has_position)
        if decision:
            if decision == "HOLD" and has_position is False:
                return "WAIT"
            return decision

        messages = [
            (
                "system",
                get_prompt("signal_extractor_system", config=get_config()),
            ),
            ("human", full_signal),
        ]

        response = str(self.quick_thinking_llm.invoke(messages).content).strip().upper()
        if response in {"BUY", "SELL", "HOLD"}:
            if response == "HOLD" and has_position is False:
                return "WAIT"
            return response
        if has_position is False:
            return "WAIT"
        return "HOLD"


_APPLICATION_DIAGNOSTICS_MARKER = "<!-- TA_SYSTEM_DIAGNOSTICS_START -->"


def _extract_generated_execution_block(text: str) -> str | None:
    """Return the last application-owned summary block.

    Headings alone are model-authored text and are never a trust boundary. The
    risk manager appends the diagnostics marker after the model body; using the
    last marker also prevents a copied marker earlier in that body from winning.
    """
    marker_matches = list(re.finditer(re.escape(_APPLICATION_DIAGNOSTICS_MARKER), text))
    for marker in reversed(marker_matches):
        trusted = text[marker.end():]
        quality_matches = list(
            re.finditer(r"(?m)^#{1,6}\s*执行质检\s*$", trusted)
        )
        summary_matches = list(
            re.finditer(r"(?m)^#{1,6}\s*系统执行结论\s*$", trusted)
        )
        if not quality_matches or not summary_matches:
            continue

        summary = summary_matches[-1]
        if not any(match.start() < summary.start() for match in quality_matches):
            continue

        tail = trusted[summary.end():]
        next_heading = re.search(r"(?m)^#{1,6}\s+", tail)
        return tail[:next_heading.start()] if next_heading else tail
    return None


def _extract_generated_execution_action(text: str) -> tuple[str, str] | None:
    """Read the last application-owned execution summary after quality checks.

    A model may emit a similarly named heading in its own body.  The backend
    summary is distinguishable because it is appended after ``执行质检`` and is
    the last such heading in the report.
    """
    block = _extract_generated_execution_block(text)
    if block is None:
        return None
    action_match = re.search(r"(?m)^\s*-\s*系统动作[：:]\s*(.+?)\s*$", block)
    if not action_match:
        return None

    label = action_match.group(1).strip()
    upper = label.upper()
    if "立即清仓" in label:
        return "EXIT", "立即清仓"
    if "触发止损" in label:
        return "EXIT", "触发止损"
    if "条件减仓" in label:
        return "REDUCE", "条件减仓"
    if "条件入场" in label or "条件建仓" in label:
        return "ENTER", "条件入场"
    if upper.startswith("ENTER") or label in {"入场", "建仓", "买入"}:
        return "ENTER", "入场"
    if upper.startswith("EXIT") or label in {"清仓", "卖出"}:
        return "EXIT", "条件清仓"
    if upper.startswith("REDUCE") or label in {"减仓", "止盈"}:
        return "REDUCE", "条件减仓"
    if upper.startswith("HOLD") or label == "持有":
        return "HOLD", "持有"
    if upper.startswith("WAIT") or any(
        phrase in label for phrase in ("观察", "观望", "等待触发", "人工复核")
    ):
        if "人工复核" in label:
            return "WAIT", "等待人工复核"
        if "等待触发" in label:
            return "WAIT", "等待触发"
        return "WAIT", "观望"
    return None


def _extract_generated_gate_state(text: str) -> bool | None:
    """Return the gate state from the last application-owned summary."""
    block = _extract_generated_execution_block(text)
    if block is None:
        return None
    match = re.search(
        r"(?im)^\s*-\s*(?:强动作门禁|Strong Action Gate)[：:]\s*(通过|未通过)\s*$",
        block,
    )
    if not match:
        return None
    return match.group(1) == "通过"


def _has_gate_failure(text: str) -> bool:
    """Check for actual execution-layer gate failures only (no position check)."""
    generated_gate_state = _extract_generated_gate_state(text)
    if generated_gate_state is not None:
        return not generated_gate_state

    if re.search(r"Strong Action Gate[：:]\s*未通过", text, re.IGNORECASE):
        return True

    m = re.search(r"系统动作[：:]\s*(.+)", text)
    if m:
        action = m.group(1).strip()
        if action in ("等待人工复核", "等待触发", "人工复核"):
            return True

    if "[C-001]" in text and ("观望" in text or "WAIT" in text.upper()):
        return True

    if re.search(r"未持仓|no_position|current_position.*(0|空)", text, re.IGNORECASE):
        if re.search(r"WAIT|观察|观望|禁止买入|禁止开仓", text, re.IGNORECASE):
            if not re.search(r"(?:确认建仓|积极建仓|条件试仓|买入|BUY)", text):
                return True

    return False


def _execution_layer_overrides_hold(text: str, *, has_position: bool | None = None) -> bool:
    """Check whether the execution layer signals a non-actionable state.

    If the execution block (Strong Action Gate / trade quality check / C-001)
    says the system should wait / hold / review, the final signal must be
    HOLD regardless of what VERDICT says.

    [P1-1] When has_position=False, non-actionable state should return WAIT
    instead of HOLD. The caller (process_signal) handles the mapping.
    """
    if _has_gate_failure(text):
        return True

    if has_position is False:
        return True

    return False


def _extract_decision_keyword(text: str, *, has_position: bool | None = None) -> str | None:
    """Rule-based decision extraction to keep UI consistent with final decision text."""
    generated_action = _extract_generated_execution_action(text)
    if generated_action:
        action, _label = generated_action
        if action == "ENTER":
            return "BUY"
        if action in {"REDUCE", "EXIT"}:
            return "SELL"
        return "HOLD"

    if _execution_layer_overrides_hold(text, has_position=has_position):
        return "HOLD"

    semantic_text = _semantic_model_scope(text)
    upper = semantic_text.upper()

    def parse_verdict_direction(raw_text: str) -> str | None:
        match = re.search(r"<!--\s*VERDICT:\s*(\{.*?\})\s*-->", raw_text, re.IGNORECASE | re.DOTALL)
        if not match:
            return None
        try:
            payload = json.loads(match.group(1))
        except Exception:
            return None
        direction = str(payload.get("direction", "")).strip().upper()
        direction_map = {
            "看多": "BUY",
            "偏多": "BUY",
            "BULLISH": "BUY",
            "BUY": "BUY",
            "看空": "SELL",
            "偏空": "SELL",
            "BEARISH": "SELL",
            "SELL": "SELL",
            "中性": "HOLD",
            "NEUTRAL": "HOLD",
            "HOLD": "HOLD",
            "谨慎": "HOLD",
            "CAUTIOUS": "HOLD",
        }
        return direction_map.get(direction)

    def classify(snippet: str) -> str | None:
        snippet_upper = snippet.upper()
        sell_action_keywords = [
            "SELL",
            "卖出",
            "减持",
            "清仓",
            "空仓",
        ]
        sell_direction_keywords = [
            "回避",
            "看空",
            "偏空",
        ]
        buy_action_keywords = [
            "BUY",
            "买入",
            "增持",
            "做多",
            "有条件建仓",
            "条件建仓",
            "建仓",
        ]
        buy_direction_keywords = [
            "看多",
            "偏多",
            "谨慎看多",
        ]
        hold_keywords = [
            "HOLD",
            "观望",
            "持有",
            "中性",
        ]

        # Explicit execution actions take precedence over contextual direction.
        # Example: "长期看多，但当前建议减持" must remain a sell action.
        if _has_non_negated_action_keyword(snippet_upper, sell_action_keywords):
            return "SELL"
        # A later explicit entry condition overrides an earlier anti-chase
        # caveat: "不建议追高买入，回调后条件建仓".
        if _has_non_negated_action_keyword(snippet_upper, buy_action_keywords):
            return "BUY"
        # A negated entry action is an explicit instruction not to enter. Keep
        # bullish research context separate and map the execution decision to HOLD,
        # unless the same clause explicitly says to avoid the instrument.
        if _has_negated_keyword(snippet_upper, buy_action_keywords):
            if _has_non_negated_keyword(snippet_upper, sell_direction_keywords):
                return "SELL"
            return "HOLD"
        # "不建议卖出/不宜减持" is an explicit hold-style instruction. A
        # separate non-negated buy action above may still override it.
        if _has_negated_keyword(snippet_upper, sell_action_keywords):
            return "HOLD"
        if _has_non_negated_keyword(snippet_upper, buy_direction_keywords):
            return "BUY"
        if _has_non_negated_keyword(snippet_upper, sell_direction_keywords):
            return "SELL"
        if _has_non_negated_keyword(snippet_upper, hold_keywords):
            return "HOLD"
        return None

    explicit_override = _infer_explicit_legacy_override(
        text,
        has_position=has_position,
    )
    if explicit_override:
        return explicit_override

    verdict_decision = parse_verdict_direction(semantic_text)
    if verdict_decision:
        return verdict_decision

    explicit_patterns = [
        r"系统动作[:：]\s*([^\n*]+)",
        r"最终裁决[:：]\s*([^\n*]+)",
        r"风控委员会最终裁决[:：]\s*([^\n*]+)",
        r"最终建议[:：]\s*([^\n*]+)",
        r"方向[:：]\s*([^\n*]+)",
        r"核心定性[:：]\s*([^\n*]+)",
    ]
    for pattern in explicit_patterns:
        match = re.search(pattern, semantic_text, re.IGNORECASE)
        if match:
            decision = classify(match.group(1).strip())
            if decision:
                return decision

    headline = "\n".join(semantic_text.splitlines()[:20])
    decision = classify(headline)
    if decision:
        return decision

    decision = classify(upper)
    if decision:
        return decision

    return "UNKNOWN"


def _extract_decision_semantics(
    text: str,
    *,
    has_position: Optional[bool] = None,
    trigger_price: Optional[float] = None,
    invalid_price: Optional[float] = None,
    data_blockers: Optional[list] = None,
) -> DecisionSemantics:
    """Extract 3-layer structured decision semantics from signal text.

    Returns a DecisionSemantics with:
      - research_direction: 看多/偏多/中性/偏空/看空
      - execution_action: WAIT/ENTER/HOLD/REDUCE/EXIT
      - action_label: human-readable action label
      - decision: BUY/SELL/HOLD (backward compat)
      - wait_reason_codes: [REPORT-UX-003] explainable codes when WAIT
    """
    research_direction = _infer_research_direction(text)
    generated_action = _extract_generated_execution_action(text)
    gate_blocked = _has_gate_failure(text)
    data_insufficient = _is_data_insufficient(text)
    has_conflict = _text_has_conflict(text)

    execution_action = _resolve_execution_action(
        has_position=has_position,
        research_direction=research_direction,
        trigger_price=trigger_price,
        invalid_price=invalid_price,
        gate_blocked=gate_blocked,
        data_insufficient=data_insufficient,
    )
    explicit_sell_action = None
    explicit_hold_action = None
    explicit_recommendation = _latest_explicit_recommendation(text) or ""
    explicit_avoidance = _has_explicit_avoidance_action(explicit_recommendation)
    if not gate_blocked and not data_insufficient:
        explicit_sell_action = _infer_explicit_sell_action(
            text,
            has_position=has_position,
        )
        if explicit_sell_action:
            execution_action = explicit_sell_action
        else:
            explicit_hold_action = _infer_explicit_hold_action(
                text,
                has_position=has_position,
            )
            if explicit_hold_action:
                execution_action = explicit_hold_action

    if generated_action:
        execution_action = generated_action[0]

    action_label = _derive_action_label(
        has_position=has_position,
        research_direction=research_direction,
        execution_action=execution_action,
        trigger_price=trigger_price,
        invalid_price=invalid_price,
    )
    if explicit_sell_action == "WAIT":
        if explicit_avoidance:
            action_label = "回避"
        elif research_direction in ("偏多", "看多"):
            action_label = "观望"
    if explicit_hold_action:
        action_label = "持有" if execution_action == "HOLD" else "观望"
    if generated_action:
        action_label = generated_action[1]

    if (
        not generated_action
        and data_insufficient
        and execution_action == "WAIT"
        and research_direction == "中性"
    ):
        action_label = "数据不足观察"

    decision = _DECISION_MAP.get(research_direction, "HOLD")
    if execution_action in ("REDUCE", "EXIT"):
        decision = "SELL"
    elif explicit_hold_action:
        decision = "HOLD"
    elif explicit_sell_action == "WAIT":
        # Avoidance is a directional risk verdict even when there is no
        # position to sell. Pure holding-only actions remain non-applicable.
        decision = (
            "SELL"
            if explicit_avoidance or has_position is not False
            else "HOLD"
        )
    if generated_action:
        decision = {
            "ENTER": "BUY",
            "REDUCE": "SELL",
            "EXIT": "SELL",
            "HOLD": "HOLD",
            "WAIT": "HOLD",
        }[execution_action]
    elif gate_blocked:
        decision = "HOLD"

    # [REPORT-UX-003] wait_reason_codes — decompose the WAIT verdict so the UI
    # can show *why* the action is 观察 / 数据不足观察 instead of a flat label.
    wait_reason_codes = compute_wait_reason_codes(
        execution_action=execution_action,
        research_direction=research_direction,
        action_label=action_label,
        has_position=has_position,
        trigger_price=trigger_price,
        gate_blocked=gate_blocked,
        data_insufficient=data_insufficient,
        data_blockers=data_blockers,
        has_conflict=has_conflict,
    )
    if explicit_sell_action == "WAIT":
        wait_reason_codes = [
            code for code in wait_reason_codes
            if code != WAIT_REASON_NO_TRIGGER
        ]
        if explicit_avoidance:
            if WAIT_REASON_RISK_FIRST not in wait_reason_codes:
                wait_reason_codes.append(WAIT_REASON_RISK_FIRST)
        elif WAIT_REASON_ACTION_NOT_APPLICABLE not in wait_reason_codes:
            wait_reason_codes.append(WAIT_REASON_ACTION_NOT_APPLICABLE)

    return DecisionSemantics(
        research_direction=research_direction,
        execution_action=execution_action,
        action_label=action_label,
        decision=decision,
        wait_reason_codes=wait_reason_codes,
    )
