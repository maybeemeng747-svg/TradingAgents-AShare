# TradingAgents/graph/signal_processing.py

import re
import json

from langchain_openai import ChatOpenAI
from tradingagents.dataflows.config import get_config
from tradingagents.prompts import get_prompt


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
            # [P1-1] Map HOLD → WAIT when user has no position
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
            # [P1-1] Map HOLD → WAIT when user has no position
            if response == "HOLD" and has_position is False:
                return "WAIT"
            return response
        if has_position is False:
            return "WAIT"
        return "HOLD"


def _execution_layer_overrides_hold(text: str, *, has_position: bool | None = None) -> bool:
    """Check whether the execution layer signals a non-actionable state.

    If the execution block (Strong Action Gate / trade quality check / C-001)
    says the system should wait / hold / review, the final signal must be
    HOLD regardless of what VERDICT says.

    [P1-1] When has_position=False, non-actionable state should return WAIT
    instead of HOLD. The caller (process_signal) handles the mapping.
    """
    # Strong Action Gate failed
    if re.search(r"Strong Action Gate[：:]\s*未通过", text, re.IGNORECASE):
        return True

    # Execution quality check: system action is non-actionable
    m = re.search(r"系统动作[：:]\s*(.+)", text)
    if m:
        action = m.group(1).strip()
        if action in ("等待人工复核", "等待触发", "人工复核"):
            return True

    # C-001 position validation: auto-converted to WAIT
    if "[C-001]" in text and ("观望" in text or "WAIT" in text.upper()):
        return True

    # [Fix-4] No-position indicators
    if re.search(r"未持仓|no_position|current_position.*(0|空)", text, re.IGNORECASE):
        # If the text explicitly says 未持仓 + WAIT/观察, confirm non-actionable
        if re.search(r"WAIT|观察|观望|禁止买入|禁止开仓", text, re.IGNORECASE):
            if not re.search(r"(?:确认建仓|积极建仓|条件试仓|买入|BUY)", text):
                return True

    # [P1-1] Explicit no-position override from caller
    if has_position is False:
        return True

    return False


def _extract_decision_keyword(text: str, *, has_position: bool | None = None) -> str | None:
    """Rule-based decision extraction to keep UI consistent with final decision text."""
    # P0: Execution layer takes priority over VERDICT
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
