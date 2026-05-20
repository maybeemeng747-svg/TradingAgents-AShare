"""IntentParser: parse natural language query into structured trading intent."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from tradingagents.agents.utils.context_utils import normalize_user_context
from tradingagents.prompts import get_prompt
from tradingagents.dataflows.config import get_config

_HORIZON_LABELS = {
    "short": "短线（1-2周，技术面主导）",
    "medium": "中线（1-3月，基本面主导）",
}

# Analysis intent constants
ANALYSIS_INTENT_VALUES = ("watch", "entry", "holding", "add", "reduce", "stop_loss")

# Keyword → (analysis_intent, horizon) mapping for rule-based routing
_INTENT_KEYWORD_MAP = [
    # (keywords_regex, intent, horizon)
    # 短线专用规则（优先匹配，防止被通用规则抢先）
    (r"短线.*?(?:买点|入场|建仓)|想.*?短线.*?(?:买|入)|短线机会", "entry", "short"),
    # 中线专用规则（优先匹配）
    (r"中线.*?(?:买点|入场|建仓)|想.*?中线.*?(?:买|入|建仓)", "entry", "medium"),
    (r"中线.*?(?:拿|持有|持仓)|中线.*?(?:卖不卖|走不走)", "holding", "medium"),
    (r"中线.*?(?:机会|走势|行情|空间)", "watch", "medium"),
    # 通用规则（无周期前缀时默认 short）
    (r"买点|入场|建仓|进场|能不能买", "entry", "short"),
    (r"加仓|补仓|追加|买入更多", "add", "short"),
    (r"减仓|部分.*?(?:卖出|离场)|降低仓位", "reduce", "short"),
    (r"止损|割肉|认赔", "stop_loss", "short"),
    (r"清仓|全部.*?(?:卖出|离场)", "stop_loss", "short"),
    (r"继续.*?拿|继续.*?持有|拿着不动|套.*?(?:怎么办|怎么)|被套|持仓.*?(?:怎么办|如何)", "holding", "short"),
    (r"短线.*?(?:机会|走势|行情)", "watch", "short"),
    (r"先观察|先观望|继续观察|先看看|观望", "watch", "short"),
]


def _infer_analysis_intent(query: str) -> tuple[str, str]:
    """Rule-based inference of (analysis_intent, horizon) from query text.

    Returns (intent, horizon) where intent is one of ANALYSIS_INTENT_VALUES
    and horizon is 'short' or 'medium'.
    """
    text = (query or "").strip()
    for pattern, intent, horizon in _INTENT_KEYWORD_MAP:
        if re.search(pattern, text, re.IGNORECASE):
            return intent, horizon
    return "watch", "short"


def _infer_position_context(query: str, user_context: Dict[str, Any]) -> Dict[str, Any]:
    """Infer position_context from query text and user_context.

    Returns a dict with keys: has_position, avg_cost, shares, position_pct, holding_days.
    """
    ctx: Dict[str, Any] = {
        "has_position": False,
        "avg_cost": None,
        "shares": None,
        "position_pct": None,
        "holding_days": None,
    }

    # From explicit user_context fields
    pos = user_context.get("current_position")
    pos_pct = user_context.get("current_position_pct")
    avg_cost = user_context.get("average_cost")

    if pos is not None and float(pos) > 0:
        ctx["has_position"] = True
        ctx["shares"] = float(pos)
    if pos_pct is not None:
        ctx["position_pct"] = float(pos_pct)
        if float(pos_pct) > 0:
            ctx["has_position"] = True
    if avg_cost is not None:
        ctx["avg_cost"] = float(avg_cost)

    # Infer from query keywords if user_context doesn't say
    text = (query or "").strip()
    holding_keywords = ["持有", "持仓", "拿着", "被套", "套牢", "仓位", "减仓", "止损", "加仓", "补仓"]
    if not ctx["has_position"] and any(k in text for k in holding_keywords):
        # If user mentions position-related keywords, assume has_position
        ctx["has_position"] = True

    return ctx


def parse_intent(
    query: str,
    llm,
    fallback_ticker: Optional[str] = None,
) -> Dict[str, Any]:
    """Parse natural language query into structured intent dict.

    Returns dict with keys: ticker, horizons, focus_areas, specific_questions, user_context, raw_query.
    Falls back gracefully to defaults if LLM output is unparseable.
    """
    config = get_config()
    system_msg = get_prompt("intent_parser_system", config=config)
    fallback_user_context = _extract_user_context_fallback(query)

    try:
        result = llm.invoke([
            SystemMessage(content=system_msg),
            HumanMessage(content=query),
        ])
        raw = result.content.strip()
        # Clean markdown code fences more robustly (handle potential whitespace/newlines)
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"\s*```$", "", raw, flags=re.MULTILINE)
        
        # Simple cleanup for common LLM JSON errors
        raw = re.sub(r",\s*([\]}])", r"\1", raw)
        
        parsed = json.loads(raw) or {}
        parsed_user_context = normalize_user_context(parsed.get("user_context") or {})
        merged_context = _merge_inferred_user_context(parsed_user_context, fallback_user_context)

        # G-001: Infer analysis_intent and horizon from query + user_context
        analysis_intent, inferred_horizon = _infer_analysis_intent(query)
        position_context = _infer_position_context(query, merged_context)

        # Determine horizons based on analysis_intent + position_context
        # If user has position, default to holding; otherwise watch/entry
        if position_context["has_position"] and analysis_intent == "watch":
            analysis_intent = "holding"

        horizons = [inferred_horizon]

        return {
            "raw_query": query,
            "ticker": parsed.get("ticker") or fallback_ticker or "",
            "horizons": horizons,
            "analysis_intent": analysis_intent,
            "position_context": position_context,
            "focus_areas": parsed.get("focus_areas") if isinstance(parsed.get("focus_areas"), list) else [],
            "specific_questions": parsed.get("specific_questions") if isinstance(parsed.get("specific_questions"), list) else [],
            "user_context": merged_context,
        }
    except Exception:
        analysis_intent, inferred_horizon = _infer_analysis_intent(query)
        position_context = _infer_position_context(query, fallback_user_context)
        if position_context["has_position"] and analysis_intent == "watch":
            analysis_intent = "holding"
        return {
            "raw_query": query,
            "ticker": fallback_ticker or "",
            "horizons": [inferred_horizon],
            "analysis_intent": analysis_intent,
            "position_context": position_context,
            "focus_areas": [],
            "specific_questions": [],
            "user_context": fallback_user_context,
        }


def build_horizon_context(
    horizon: str,
    focus_areas: List[str],
    specific_questions: List[str],
    agent_type: Optional[str] = None,
    analysis_intent: Optional[str] = None,
    position_context: Optional[Dict[str, Any]] = None,
) -> str:
    """Build the horizon context block to prepend to any agent's system prompt."""
    config = get_config()
    template = get_prompt("horizon_context_block", config=config)

    horizon_label = _HORIZON_LABELS.get(horizon, horizon)
    focus_str = "、".join(focus_areas) if focus_areas else "无特殊关注"
    questions_str = "；".join(specific_questions) if specific_questions else "无"
    weight_hint = _build_weight_hint(horizon, agent_type)

    # G-001: Append three-layer decision context
    intent_block = ""
    if analysis_intent:
        intent_labels = {
            "watch": "观察",
            "entry": "寻找入场机会",
            "holding": "持仓处理",
            "add": "评估加仓条件",
            "reduce": "评估减仓条件",
            "stop_loss": "止损/清仓评估",
        }
        intent_label = intent_labels.get(analysis_intent, analysis_intent)
        intent_block = f"\n- 交易目的：{intent_label}"

    position_block = ""
    if position_context:
        pos = position_context
        has_pos = pos.get("has_position", False)
        pos_status = "已持仓" if has_pos else "未持仓"
        parts = [pos_status]
        if has_pos:
            if pos.get("avg_cost") is not None:
                parts.append(f"成本 {pos['avg_cost']}")
            if pos.get("shares") is not None:
                parts.append(f"持仓 {pos['shares']} 股")
            if pos.get("position_pct") is not None:
                parts.append(f"仓位 {pos['position_pct']}%")
        position_block = "\n- 持仓状态：" + " | ".join(parts)

    return template.format(
        horizon_label=horizon_label,
        focus_areas_str=focus_str,
        specific_questions_str=questions_str,
        weight_hint=weight_hint,
    ) + intent_block + position_block


def _build_weight_hint(horizon: str, agent_type: Optional[str]) -> str:
    """Return a concise weighting hint for single-run mixed-horizon analysis."""
    if not agent_type:
        return ""

    normalized_agent = agent_type.strip().lower()
    if horizon == "short" and normalized_agent in {"fundamentals", "macro"}:
        return "短线决策中该维度作为次要约束使用，重点识别硬风险、估值压力和中期失效条件，不要覆盖量价、资金与市场结构信号。"
    if horizon == "short" and normalized_agent in {"market", "smart_money", "volume_price", "social", "news"}:
        return "短线决策中该维度权重较高，请优先关注1-2周内可验证的触发条件、失效条件和风险收益比。"
    if horizon == "medium" and normalized_agent in {"fundamentals", "macro"}:
        return "中线决策中该维度权重较高，请优先关注盈利质量、估值消化、行业周期和政策约束。"
    return ""


def _merge_inferred_user_context(
    parsed_context: Dict[str, Any],
    fallback_context: Dict[str, Any],
) -> Dict[str, Any]:
    merged = dict(parsed_context)
    for key, value in fallback_context.items():
        if key in {"cash_available", "current_position", "current_position_pct", "average_cost", "max_loss_pct"}:
            merged[key] = value
            continue
        if key == "constraints":
            existing = [str(item).strip() for item in merged.get("constraints", []) if str(item).strip()]
            for item in value:
                text = str(item).strip()
                if text and text not in existing:
                    existing.append(text)
            if existing:
                merged["constraints"] = existing
            continue
        if key not in merged or merged.get(key) in (None, "", []):
            merged[key] = value
    return normalize_user_context(merged)


def _extract_user_context_fallback(query: str) -> Dict[str, Any]:
    text = (query or "").strip()
    if not text:
        return {}

    context: Dict[str, Any] = {}

    objective_patterns = [
        (r"(想|准备|打算|计划).*建仓|想建仓|准备建仓|打算建仓", "建仓"),
        (r"(想|准备|打算|计划|考虑).*加仓|想加仓|准备加仓|考虑加仓", "加仓"),
        (r"(想|准备|打算|计划|考虑).*减仓|想减仓|准备减仓|考虑减仓", "减仓"),
        (r"(想|准备|打算|计划|考虑).*止损|想止损|准备止损|考虑止损", "止损"),
        (r"继续拿着|继续持有|拿着不动|持有中|被套|套牢", "持有处理"),
        (r"先观察|先观望|继续观察|先看看|观望", "观察"),
    ]
    for pattern, label in objective_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            context["objective"] = label
            break

    risk_keywords = {
        "保守": "保守",
        "稳健": "保守",
        "平衡": "平衡",
        "激进": "激进",
        "高风险": "激进",
    }
    for keyword, label in risk_keywords.items():
        if keyword in text:
            context["risk_profile"] = label
            break

    horizon_keywords = {
        "短线": "短线",
        "短期": "短线",
        "波段": "波段",
        "中线": "中线",
        "中期": "中线",
        "长期": "长期",
    }
    for keyword, label in horizon_keywords.items():
        if keyword in text:
            context["investment_horizon"] = label
            break

    position_keywords = {
        "满仓": 100.0,
        "重仓": 80.0,
        "半仓": 50.0,
        "轻仓": 20.0,
        "空仓": 0.0,
    }
    for keyword, pct in position_keywords.items():
        if keyword in text:
            context["current_position_pct"] = pct
            break

    cash_match = re.search(r"(?:可用资金|现金|仓位资金)[^\d]{0,8}(\d+(?:\.\d+)?)(万|亿)?", text, re.IGNORECASE)
    if cash_match:
        amount = cash_match.group(1)
        unit = cash_match.group(2) or ""
        context["cash_available"] = f"{amount}{unit}"

    patterns = {
        "average_cost": r"(?:成本价?|均价|持仓成本|买入价|在高位)\D{0,6}(\d+(?:\.\d+)?)",
        "max_loss_pct": r"(?:最大(?:亏损|回撤)|容忍亏损|止损(?:位)?|最多(?:只能)?亏)[^\d]{0,8}(\d+(?:\.\d+)?)\s*%",
        "current_position": r"(?:持有|现有|目前有)[^\d]{0,8}(\d+(?:\.\d+)?)\s*股",
        "current_position_pct": r"(?:仓位|持仓占比)[^\d]{0,8}(\d+(?:\.\d+)?)\s*%",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            context[key] = match.group(1)

    constraints: List[str] = []
    constraint_keywords = {
        "不加杠杆": "不加杠杆",
        "不融资": "不融资",
        "不追高": "不追高",
        "只做t+1": "只做T+1",
        "只做T+1": "只做T+1",
        "不能补仓": "不能补仓",
        "不接受隔夜": "不接受隔夜",
    }
    lowered = text.lower()
    for keyword, label in constraint_keywords.items():
        if keyword.lower() in lowered and label not in constraints:
            constraints.append(label)
    if constraints:
        context["constraints"] = constraints

    return normalize_user_context(context)
