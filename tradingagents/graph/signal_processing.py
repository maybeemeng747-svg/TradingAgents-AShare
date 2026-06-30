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

WAIT_REASON_LABELS: dict[str, str] = {
    WAIT_REASON_DATA_MISSING: "关键数据缺口",
    WAIT_REASON_GATE_BLOCKED: "门禁未通过",
    WAIT_REASON_CONFLICT: "结论冲突",
    WAIT_REASON_NO_TRIGGER: "等待触发价",
    WAIT_REASON_RISK_FIRST: "风险优先",
    WAIT_REASON_NORMAL_NO_DATA: "数据正常·暂无触发",
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


def _parse_research_direction_from_verdict(text: str) -> str | None:
    verdict_scope = re.split(r"\n\s*#{1,6}\s*执行质检\b", text, maxsplit=1)[0]
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
    snippet_upper = snippet.upper()
    negated = bool(re.search(r"不(?:建议|宜|应|要|能|可)\s*", snippet))
    sell_kw = ["SELL", "卖出", "减持", "清仓", "空仓", "回避", "看空", "偏空"]
    buy_kw = ["BUY", "买入", "增持", "做多", "看多", "偏多", "谨慎看多", "有条件建仓", "条件建仓", "建仓"]
    hold_kw = ["HOLD", "观望", "持有", "中性"]
    if any(k in snippet_upper for k in sell_kw):
        return "偏空"
    if any(k in snippet_upper for k in buy_kw):
        return "中性" if negated else "偏多"
    if any(k in snippet_upper for k in hold_kw):
        return "中性"
    return None


def _strip_system_overrides(text: str) -> str:
    return re.sub(r"⚠?\s*\[C-\d+\].*", "", text)


def _infer_research_direction(text: str) -> str:
    rd = _parse_research_direction_from_verdict(text)
    if rd:
        return rd

    stripped = re.split(
        r"\n\s*#{1,6}\s*执行质检\b",
        _strip_system_overrides(text),
        maxsplit=1,
    )[0]

    explicit_patterns = [
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


def _has_gate_failure(text: str) -> bool:
    """Check for actual execution-layer gate failures only (no position check)."""
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
    if _execution_layer_overrides_hold(text, has_position=has_position):
        return "HOLD"

    upper = text.upper()

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
        sell_keywords = [
            "SELL",
            "卖出",
            "减持",
            "清仓",
            "空仓",
            "回避",
            "看空",
            "偏空",
        ]
        buy_keywords = [
            "BUY",
            "买入",
            "增持",
            "做多",
            "看多",
            "偏多",
            "谨慎看多",
            "有条件建仓",
            "条件建仓",
            "建仓",
        ]
        hold_keywords = [
            "HOLD",
            "观望",
            "持有",
            "中性",
        ]

        if any(k in snippet_upper for k in buy_keywords):
            return "BUY"
        if any(k in snippet_upper for k in sell_keywords):
            return "SELL"
        if any(k in snippet_upper for k in hold_keywords):
            return "HOLD"
        return None

    verdict_decision = parse_verdict_direction(text)
    if verdict_decision:
        return verdict_decision

    explicit_patterns = [
        r"最终裁决[:：]\s*([^\n*]+)",
        r"风控委员会最终裁决[:：]\s*([^\n*]+)",
        r"最终建议[:：]\s*([^\n*]+)",
        r"方向[:：]\s*([^\n*]+)",
        r"核心定性[:：]\s*([^\n*]+)",
    ]
    for pattern in explicit_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            decision = classify(match.group(1).strip())
            if decision:
                return decision

    headline = "\n".join(text.splitlines()[:20])
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

    action_label = _derive_action_label(
        has_position=has_position,
        research_direction=research_direction,
        execution_action=execution_action,
        trigger_price=trigger_price,
        invalid_price=invalid_price,
    )

    if data_insufficient and execution_action == "WAIT" and research_direction == "中性":
        action_label = "数据不足观察"

    decision = _DECISION_MAP.get(research_direction, "HOLD")
    if gate_blocked:
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

    return DecisionSemantics(
        research_direction=research_direction,
        execution_action=execution_action,
        action_label=action_label,
        decision=decision,
        wait_reason_codes=wait_reason_codes,
    )
