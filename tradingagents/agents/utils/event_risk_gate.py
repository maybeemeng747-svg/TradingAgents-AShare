# [C-007] event_risk_gate
"""重大事件风控门禁 — 重大事件发生时，进入风控优先模式。

功能：
- 从公告/新闻文本检测重大风险事件（解禁、并购、停牌、暴雷、诉讼、监管、减值、控制权变更、ST等）
- 从 raw_evidence 结构化数据提取量化风险指标（解禁比例、杠杆率、净利润变化等）
- 分级风险响应：CRITICAL 阻断所有开仓、HIGH 强制保守、MEDIUM 仅警告
- 风控优先模式：限制强动作输出，强制降级为观察/持有语义
"""

import logging
import re
from typing import Optional

_logger = logging.getLogger(__name__)


# ── 事件类型定义 ─────────────────────────────────────────────────────────────

class EventSeverity:
    """事件严重程度"""
    CRITICAL = "critical"   # 阻断所有开仓
    HIGH = "high"           # 强制保守，禁止新建仓
    MEDIUM = "medium"       # 警告，降权但不阻断


# 需要触发风控优先模式的事件类型及其默认严重程度
RISK_EVENT_TYPES: dict[str, dict] = {
    "large_unlock": {
        "label": "大比例解禁",
        "description": "解禁比例 > 流通股本 5%",
        "default_severity": EventSeverity.HIGH,
    },
    "major_ma": {
        "label": "重大并购/重组",
        "description": "重大并购、资产重组或借壳上市",
        "default_severity": EventSeverity.HIGH,
    },
    "high_leverage": {
        "label": "高杠杆",
        "description": "资产负债率 > 80%",
        "default_severity": EventSeverity.MEDIUM,
    },
    "suspension": {
        "label": "停复牌",
        "description": "当前处于停牌或近期复牌",
        "default_severity": EventSeverity.HIGH,
    },
    "earnings_crash": {
        "label": "业绩暴雷",
        "description": "净利润下降 > 50%",
        "default_severity": EventSeverity.CRITICAL,
    },
    "major_litigation": {
        "label": "重大诉讼/仲裁",
        "description": "涉及重大诉讼、仲裁或赔偿",
        "default_severity": EventSeverity.HIGH,
    },
    "regulatory_investigation": {
        "label": "监管立案/调查",
        "description": "被证监会立案调查或行政处罚",
        "default_severity": EventSeverity.CRITICAL,
    },
    "major_write_off": {
        "label": "大额资产减值",
        "description": "大额商誉减值或资产减值",
        "default_severity": EventSeverity.HIGH,
    },
    "control_change": {
        "label": "控制权变更",
        "description": "实控人变更、要约收购或股权转让",
        "default_severity": EventSeverity.HIGH,
    },
    "risk_warning": {
        "label": "风险警示/ST",
        "description": "被实施ST、*ST或其他风险警示",
        "default_severity": EventSeverity.CRITICAL,
    },
}


# ── 文本检测模式 ─────────────────────────────────────────────────────────────

# 每个事件类型对应多个正则模式，按 (pattern, severity_override) 组织
_TEXT_EVENT_PATTERNS: dict[str, list[tuple[str, Optional[str]]]] = {
    "large_unlock": [
        (r"解禁.{0,10}(?:流通|股本|比例|股数)", None),
        (r"(?:限售|首发).{0,6}(?:解禁|上市流通)", None),
        (r"(?:大规模|大量|巨额).{0,4}(?:减持|解禁)", None),
    ],
    "major_ma": [
        (r"(?:重大|筹划|拟).{0,6}(?:并购|重组|借壳|收购)", None),
        (r"(?:发行股份|现金).{0,6}(?:购买|收购|重组)", None),
        (r"(?:要约收购|吸收合并|资产注入)", None),
    ],
    "suspension": [
        (r"(?:停牌|复牌|停盘|盘中停牌)", None),
        (r"(?:临时停牌|紧急停牌|连续停牌)", None),
    ],
    "earnings_crash": [
        (r"(?:净利润|归母净利润|扣非净利润).{0,15}(?:下降|亏损|减少|预减)", None),
        (r"(?:业绩暴雷|业绩变脸|业绩大幅下滑)", None),
        (r"(?:预亏|预减|大幅下降).{0,10}(?:50%|60%|70%|80%|90%|100%)", None),
        (r"业绩.{0,4}(?:预亏|预减)", None),
        (r"(?:由盈转亏|盈转亏|扭亏为盈)", None),
    ],
    "major_litigation": [
        (r"(?:重大诉讼|重大仲裁|诉讼事项)", None),
        (r"(?:被起诉|被仲裁|诉讼赔偿).{0,10}(?:亿|千万)", None),
        (r"(?:证券虚假陈述|虚假记载|信息披露违规).{0,6}(?:诉讼|赔偿)", None),
    ],
    "regulatory_investigation": [
        (r"(?:立案调查|立案侦查|行政处罚)", None),
        (r"(?:证监会|交易所|证监局).{0,10}(?:调查|处罚|警示|通报)", None),
        (r"(?:涉嫌|存在).{0,6}(?:违规|违法|信息披露违规)", None),
        (r"(?:收到|被).{0,4}(?:监管函|问询函|关注函|警示函)", None),
    ],
    "major_write_off": [
        (r"(?:商誉减值|商誉计提|资产减值).{0,10}(?:亿|巨额|大额)", None),
        (r"(?:计提.{0,6}(?:减值|坏账|损失))", None),
        (r"(?:大额|巨额|大规模).{0,4}(?:减值|核销|计提)", None),
    ],
    "control_change": [
        (r"(?:实控人|实际控制人).{0,8}(?:变更|变更|转让)", None),
        (r"(?:控制权|控股权).{0,6}(?:变更|转移|转让)", None),
        (r"(?:要约收购|协议收购|间接收购)", None),
    ],
    "risk_warning": [
        (r"(?:\*ST|ST(?:\s|$|[^a-zA-Z]))", None),
        (r"(?:实施.{0,4}(?:风险警示|退市警示|其他风险警示))", None),
        (r"(?:退市|暂停上市|终止上市)", None),
    ],
}


# ── 结构化数据阈值 ─────────────────────────────────────────────────────────

_STRUCTURED_THRESHOLDS = {
    "large_unlock": {
        "field": "unlock_ratio",
        "threshold": 0.05,
        "compare": "gt",
        "detail_fmt": "解禁比例 {value:.1%} > 5%",
    },
    "high_leverage": {
        "field": "debt_ratio",
        "threshold": 0.80,
        "compare": "gt",
        "detail_fmt": "资产负债率 {value:.1%} > 80%",
    },
    "earnings_crash": {
        "field": "net_profit_change",
        "threshold": -0.50,
        "compare": "lt",
        "detail_fmt": "净利润下降 {abs_value:.1%} > 50%",
    },
}


# ── 风控优先模式约束 ─────────────────────────────────────────────────────────

# CRITICAL/HIGH 事件时禁止的强买入动作
_RISK_FIRST_FORBIDDEN_ACTIONS = [
    "建议建仓", "建议买入", "建议入场", "积极建仓", "重仓买入",
    "建议加仓", "加仓买入", "追涨买入",
    "BUY", "ENTER", "STRONG_BUY",
]


# ── 核心检测函数 ─────────────────────────────────────────────────────────────

def detect_events_from_text(text: str) -> list[dict]:
    """从公告/新闻文本中检测重大风险事件。

    Args:
        text: 公告、新闻或研报文本

    Returns:
        检测到的事件列表，每项包含 event_type, severity, matched_text, offset
    """
    if not text:
        return []

    events: list[dict] = []
    seen_spans: set[tuple[str, int, int]] = set()

    for event_type, patterns in _TEXT_EVENT_PATTERNS.items():
        meta = RISK_EVENT_TYPES.get(event_type, {})
        default_severity = meta.get("default_severity", EventSeverity.MEDIUM)

        for pattern, severity_override in patterns:
            for m in re.finditer(pattern, text, re.IGNORECASE):
                key = (event_type, m.start(), m.end())
                if key in seen_spans:
                    continue
                seen_spans.add(key)
                events.append({
                    "event_type": event_type,
                    "severity": severity_override or default_severity,
                    "matched_text": m.group(),
                    "offset": m.start(),
                    "source": "text_detection",
                })

    return events


def extract_event_risk_inputs(
    raw_evidence: Optional[dict] = None,
    announcements_text: str = "",
    news_text: str = "",
) -> dict:
    """从 raw_evidence 结构化数据提取事件风险输入。

    类似 extract_financial_anomaly_inputs()，但专注于事件风险相关字段。

    Args:
        raw_evidence: 原始证据字典（包含 announcements, stock_data 等）
        announcements_text: 公告文本（直接传入，优先级低于 raw_evidence）
        news_text: 新闻文本（直接传入，优先级低于 raw_evidence）

    Returns:
        {
            "unlock_ratio": Optional[float],
            "debt_ratio": Optional[float],
            "net_profit_change": Optional[float],
            "is_suspended": bool,
            "has_ma_event": bool,
            "announcements_text": str,
            "news_text": str,
        }
    """
    result = {
        "unlock_ratio": None,
        "debt_ratio": None,
        "net_profit_change": None,
        "is_suspended": False,
        "has_ma_event": False,
        "announcements_text": "",
        "news_text": "",
    }

    # 从 raw_evidence 提取公告文本
    if raw_evidence:
        ann_entry = raw_evidence.get("announcements")
        if isinstance(ann_entry, dict):
            raw_ann = ann_entry.get("raw")
            if isinstance(raw_ann, str):
                result["announcements_text"] = raw_ann
            elif isinstance(raw_ann, list):
                # 列表格式：合并标题+内容
                parts = []
                for item in raw_ann:
                    if isinstance(item, dict):
                        title = item.get("title", "")
                        content = item.get("content", "")
                        parts.append(f"{title} {content}".strip())
                    elif isinstance(item, str):
                        parts.append(item)
                result["announcements_text"] = "\n".join(parts)
        # 从 raw_evidence 提取新闻文本
        news_entry = raw_evidence.get("news")
        if isinstance(news_entry, dict):
            raw_news = news_entry.get("raw")
            if isinstance(raw_news, str):
                result["news_text"] = raw_news

    # 外部传入的文本作为 fallback
    if not result["announcements_text"] and announcements_text:
        result["announcements_text"] = announcements_text
    if not result["news_text"] and news_text:
        result["news_text"] = news_text

    # 从 raw_evidence 提取结构化指标（如果可用）
    # 解禁比例：从公告关键词推断（结构化解禁数据不在标准 raw_evidence 中）
    # 杠杆率：从 financial_period_facts 提取
    if raw_evidence:
        period_entry = raw_evidence.get("financial_period_facts")
        if isinstance(period_entry, dict):
            period_facts = period_entry.get("raw", [])
            if isinstance(period_facts, list):
                result.update(_extract_leverage_from_facts(period_facts))

        # 从 fundamental_explanations 提取业绩变化
        expl_entry = raw_evidence.get("fundamental_explanations")
        if isinstance(expl_entry, dict):
            expl_raw = expl_entry.get("raw", {})
            if isinstance(expl_raw, dict):
                entries = expl_raw.get("entries", [])
                if isinstance(entries, list):
                    result["net_profit_change"] = _extract_profit_change_from_explanations(entries)

    return result


def _extract_leverage_from_facts(facts: list) -> dict:
    """从财务期间事实中提取杠杆率。"""
    result = {}
    for fact in reversed(facts):  # 最新优先
        if not isinstance(fact, dict):
            continue
        metric = fact.get("metric", "")
        if metric == "total_assets":
            assets = fact.get("value")
            # 找同报告日的负债
            report_date = fact.get("report_date")
            for other in facts:
                if (isinstance(other, dict)
                        and other.get("metric") == "total_liabilities"
                        and other.get("report_date") == report_date):
                    liabilities = other.get("value")
                    if assets and liabilities and assets > 0:
                        result["debt_ratio"] = liabilities / assets
                    break
            if "debt_ratio" in result:
                break
    return result


def _extract_profit_change_from_explanations(entries: list) -> Optional[float]:
    """从官方解释条目中提取净利润变化比例。"""
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        # 检查是否有净利润相关的关键指标
        cause_terms = entry.get("cause_terms", [])
        for term in cause_terms:
            if isinstance(term, str) and "净利润" in term:
                # 尝试从上下文中提取变化比例
                context = entry.get("context", "")
                m = re.search(r"(?:下降|减少|下滑)\s*(\d+(?:\.\d+)?)\s*%", context)
                if m:
                    try:
                        return -float(m.group(1)) / 100
                    except ValueError:
                        pass
    return None


# ── 主入口函数 ─────────────────────────────────────────────────────────────

def check_event_risk(
    stock_code: str,
    unlock_ratio: Optional[float] = None,
    debt_ratio: Optional[float] = None,
    net_profit_change: Optional[float] = None,
    is_suspended: bool = False,
    has_ma_event: bool = False,
    announcements_text: str = "",
    news_text: str = "",
    raw_evidence: Optional[dict] = None,
) -> dict:
    """检查是否存在重大风险事件，进入风控优先模式。

    数据来源优先级：
    1. 直接传入的结构化参数（unlock_ratio, debt_ratio 等）
    2. raw_evidence 中的结构化数据
    3. 文本检测（公告/新闻关键词匹配）

    返回:
        {
            "has_risk": bool,
            "risk_level": str,           # "critical" / "high" / "medium" / "none"
            "risk_events": list[str],    # 事件类型列表
            "risk_details": dict,        # 事件详情
            "event_severities": dict,    # 事件类型 → 严重程度
            "block_open": bool,          # 是否阻止开仓
            "risk_first_mode": bool,     # 是否进入风控优先模式
            "forbidden_actions": list[str],  # 风控优先模式下禁止的动作
        }
    """
    risk_events: list[str] = []
    risk_details: dict[str, str] = {}
    event_severities: dict[str, str] = {}

    # ── 1. 结构化数据检测 ──
    # 如果没有直接传入参数，从 raw_evidence 提取
    if raw_evidence is not None:
        inputs = extract_event_risk_inputs(raw_evidence=raw_evidence)
        if unlock_ratio is None:
            unlock_ratio = inputs.get("unlock_ratio")
        if debt_ratio is None:
            debt_ratio = inputs.get("debt_ratio")
        if net_profit_change is None:
            net_profit_change = inputs.get("net_profit_change")
        if not is_suspended:
            is_suspended = inputs.get("is_suspended", False)
        if not has_ma_event:
            has_ma_event = inputs.get("has_ma_event", False)
        if not announcements_text:
            announcements_text = inputs.get("announcements_text", "")
        if not news_text:
            news_text = inputs.get("news_text", "")

    # 解禁检查
    if unlock_ratio is not None and unlock_ratio > 0.05:
        risk_events.append("large_unlock")
        risk_details["large_unlock"] = f"解禁比例 {unlock_ratio:.1%} > 5%"
        event_severities["large_unlock"] = RISK_EVENT_TYPES["large_unlock"]["default_severity"]

    # 高杠杆检查
    if debt_ratio is not None and debt_ratio > 0.80:
        risk_events.append("high_leverage")
        risk_details["high_leverage"] = f"资产负债率 {debt_ratio:.1%} > 80%"
        event_severities["high_leverage"] = RISK_EVENT_TYPES["high_leverage"]["default_severity"]

    # 业绩暴雷检查
    if net_profit_change is not None and net_profit_change < -0.50:
        risk_events.append("earnings_crash")
        risk_details["earnings_crash"] = f"净利润下降 {abs(net_profit_change):.1%} > 50%"
        event_severities["earnings_crash"] = RISK_EVENT_TYPES["earnings_crash"]["default_severity"]

    # 停复牌检查
    if is_suspended:
        risk_events.append("suspension")
        risk_details["suspension"] = "当前处于停牌状态"
        event_severities["suspension"] = RISK_EVENT_TYPES["suspension"]["default_severity"]

    # 重大并购检查
    if has_ma_event:
        risk_events.append("major_ma")
        risk_details["major_ma"] = "存在重大并购/重组事件"
        event_severities["major_ma"] = RISK_EVENT_TYPES["major_ma"]["default_severity"]

    # ── 2. 文本检测 ──
    combined_text = f"{announcements_text}\n{news_text}".strip()
    if combined_text:
        text_events = detect_events_from_text(combined_text)
        for ev in text_events:
            etype = ev["event_type"]
            # 避免与结构化检测重复
            if etype not in risk_events:
                risk_events.append(etype)
                risk_details[etype] = f"文本检测: '{ev['matched_text'][:50]}'"
                event_severities[etype] = ev["severity"]

    # ── 3. 计算风险等级 ──
    has_risk = len(risk_events) > 0

    if not has_risk:
        return {
            "has_risk": False,
            "risk_level": "none",
            "risk_events": [],
            "risk_details": {},
            "event_severities": {},
            "block_open": False,
            "risk_first_mode": False,
            "forbidden_actions": [],
        }

    # 取最高严重程度
    severity_order = {EventSeverity.CRITICAL: 3, EventSeverity.HIGH: 2, EventSeverity.MEDIUM: 1}
    max_severity = max(
        (severity_order.get(event_severities.get(e, EventSeverity.MEDIUM), 1) for e in risk_events),
        default=1,
    )
    risk_level = {3: "critical", 2: "high", 1: "medium"}[max_severity]

    # 阻断逻辑：CRITICAL 和 HIGH 阻止开仓
    block_open = risk_level in ("critical", "high")

    # 风控优先模式：CRITICAL 和 HIGH 进入
    risk_first_mode = risk_level in ("critical", "high")

    # 禁止的动作
    forbidden_actions = _RISK_FIRST_FORBIDDEN_ACTIONS[:] if risk_first_mode else []

    if has_risk:
        _logger.warning(
            "[C-007] event_risk_gate: %s 检测到 %d 个风险事件 (level=%s): %s",
            stock_code,
            len(risk_events),
            risk_level,
            ", ".join(risk_events),
        )

    return {
        "has_risk": has_risk,
        "risk_level": risk_level,
        "risk_events": risk_events,
        "risk_details": risk_details,
        "event_severities": event_severities,
        "block_open": block_open,
        "risk_first_mode": risk_first_mode,
        "forbidden_actions": forbidden_actions,
    }


def format_event_risk_warning(risk_info: dict) -> str:
    """格式化事件风险警告。

    输出内容：
    - 风险等级标识
    - 各事件类型及详情
    - 风控优先模式说明
    - 禁止的动作列表
    """
    if not risk_info.get("has_risk"):
        return ""

    risk_level = risk_info.get("risk_level", "medium")
    level_label = {
        "critical": "🔴 严重",
        "high": "🟠 高风险",
        "medium": "🟡 中风险",
    }.get(risk_level, "🟡 中风险")

    warning = f"\n\n🚨 [C-007] 事件风控警告 — {level_label}\n"

    for event_type in risk_info.get("risk_events", []):
        meta = RISK_EVENT_TYPES.get(event_type, {})
        label = meta.get("label", event_type)
        detail = risk_info.get("risk_details", {}).get(event_type, "")
        severity = risk_info.get("event_severities", {}).get(event_type, "")
        severity_mark = {
            "critical": "🔴",
            "high": "🟠",
            "medium": "🟡",
        }.get(severity, "🟡")
        warning += f"  {severity_mark} {label}\n"
        if detail:
            warning += f"     {detail}\n"

    if risk_info.get("risk_first_mode"):
        warning += "\n⚠️ 已进入风控优先模式：\n"
        warning += "  - 不建议新建仓位，等待事件落地\n"
        warning += "  - 如需特批开仓，需主控 AI 审批\n"
        forbidden = risk_info.get("forbidden_actions", [])
        if forbidden:
            warning += f"  - 禁止动作：{', '.join(forbidden[:5])}\n"

    if risk_info.get("block_open"):
        warning += "\n⛔ 开仓阻断：当前风险等级不允许开新仓。\n"

    return warning


def format_event_risk_block(risk_info: dict) -> str:
    """格式化事件风险区块，用于插入报告的系统标记区域。

    与 format_event_risk_warning 不同，此函数输出结构化区块，
    可被 _split_body() 识别为系统追加内容。
    """
    if not risk_info.get("has_risk"):
        return ""

    risk_level = risk_info.get("risk_level", "medium")
    events_summary = ", ".join(
        RISK_EVENT_TYPES.get(e, {}).get("label", e)
        for e in risk_info.get("risk_events", [])
    )

    block = f"\n\n⚠️ [C-007] 事件风控门禁\n"
    block += f"风险等级: {risk_level}\n"
    block += f"事件类型: {events_summary}\n"

    if risk_info.get("block_open"):
        block += "开仓状态: 阻断\n"
    if risk_info.get("risk_first_mode"):
        block += "风控优先模式: 已激活\n"

    return block
