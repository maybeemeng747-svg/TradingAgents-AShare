# [C-008] readiness_score
"""报告质量评分（数据完整度 + 置信度 + 证据门禁 + 等级输出 + 机会评分）"""

import re
from datetime import datetime
from enum import Enum
from typing import Optional


class ConfidenceLevel(Enum):
    """置信度等级"""
    HIGH = "高"
    MEDIUM = "中"
    LOW = "低"


class EvidenceStatus:
    """原始证据查询状态"""
    HAS_DATA = "has_data"
    NORMAL_NO_DATA = "normal_no_data"
    QUERY_FAILED = "query_failed"
    NOT_QUERIED = "not_queried"
    FIELD_MISSING = "field_missing"


_STRONG_NAME_CONTEXTS = [
    re.compile(r'\d{6}\.(?:SZ|SH)\s*[\(（]\s*([\u4e00-\u9fff]{2,8})\s*[\)）]'),
    re.compile(r'(?:标的|公司|名称|股票名称)\s*[：:]\s*([\u4e00-\u9fff]{2,8})'),
    re.compile(r'关于\s*([\u4e00-\u9fff]{2,8})\s*的'),
    re.compile(r'#\s*\d{6}\.(?:SZ|SH)\s+([\u4e00-\u9fff]{2,8})'),
]


def validate_stock_name(ticker: str, report_text: str) -> dict:
    """Validate that the stock name in the report matches the actual name.

    Only extracts names from strong context patterns (ticker+paren, 标的：,
    公司：, etc.) to avoid false positives from industry words.

    Returns:
        {"name_mismatch": bool, "expected_name": str|None,
         "found_names": list[str], "note": str}
    """
    if not ticker:
        return {"name_mismatch": False, "expected_name": None, "found_names": [], "note": "无 ticker"}

    expected_name = _resolve_name_from_ticker(ticker)
    if not expected_name:
        return {"name_mismatch": False, "expected_name": None, "found_names": [], "note": "名称映射不可用"}

    if not report_text:
        return {"name_mismatch": False, "expected_name": expected_name, "found_names": [], "note": ""}

    found_names = []
    for pattern in _STRONG_NAME_CONTEXTS:
        for m in pattern.finditer(report_text):
            name = m.group(1).strip()
            if name and len(name) >= 2:
                found_names.append(name)

    found_names = list(dict.fromkeys(found_names))

    mismatched = [n for n in found_names if n != expected_name]

    if mismatched:
        return {
            "name_mismatch": True,
            "expected_name": expected_name,
            "found_names": mismatched,
            "note": f"⚠️ [E-002] 股票名称校验失败：代码 {ticker} 实际名称为「{expected_name}」，报告中出现「{', '.join(mismatched)}」",
        }
    return {"name_mismatch": False, "expected_name": expected_name, "found_names": found_names, "note": ""}


def _resolve_name_from_ticker(ticker: str) -> Optional[str]:
    """Resolve a CN ticker like '002138.SZ' to its company name.

    Prefers the pre-warmed cache from api.main; falls back to a direct
    akshare call (static list, no anti-crawl).
    """
    code = ticker.split(".")[0] if "." in ticker else ticker
    if not code.isdigit() or len(code) != 6:
        return None
    try:
        from api.main import _get_reverse_stock_map_cached_only
        rev_map = _get_reverse_stock_map_cached_only()
        for suffix in (".SH", ".SZ"):
            name = rev_map.get(f"{code}{suffix}")
            if name:
                return name
    except Exception:
        pass
    try:
        import akshare as ak
        df = ak.stock_info_a_code_name()
        match = df[df["code"] == code]
        if not match.empty:
            return str(match.iloc[0].get("name", "")).strip()
    except Exception:
        pass
    return None


_VALID_EVIDENCE = {EvidenceStatus.HAS_DATA, EvidenceStatus.NORMAL_NO_DATA}

_RISK_LABELS = {0: "观察", 1: "禁止加仓", 2: "条件减仓", 3: "触发止损", 4: "立即清仓"}
_BUY_LABELS = {0: "禁止买入", 1: "观察等待", 2: "条件试仓", 3: "确认建仓", 4: "积极建仓"}


def calculate_data_completeness(
    has_market_data: bool = False,
    has_sentiment_data: bool = False,
    has_news_data: bool = False,
    has_fundamentals_data: bool = False,
    has_smart_money_data: bool = False,
    has_volume_price_data: bool = False,
    has_user_context: bool = False,
    has_position_data: bool = False,
) -> int:
    """
    计算数据完整度（0-100%）。
    根据可用数据源的数量计算。
    """
    total_fields = 8
    available_fields = sum([
        has_market_data,
        has_sentiment_data,
        has_news_data,
        has_fundamentals_data,
        has_smart_money_data,
        has_volume_price_data,
        has_user_context,
        has_position_data,
    ])

    return int((available_fields / total_fields) * 100)


calculate_source_coverage = calculate_data_completeness


def calculate_evidence_coverage(
    ohlcv_5d: str = "not_queried",
    volume: str = "not_queried",
    turnover_rate: str = "not_queried",
    volume_ratio: str = "not_queried",
    individual_fund_flow: str = "not_queried",
    lhb_status: str = "not_queried",
    margin_trading: str = "not_queried",
    announcements: str = "not_queried",
) -> int:
    """
    计算原始证据覆盖度（0-100%）。

    区分：有数据 / 正常无触发 / 查询失败 / 未查询 / 字段缺失。
    只有 has_data 和 normal_no_data 算有效覆盖。
    """
    items = [
        ohlcv_5d, volume, turnover_rate, volume_ratio,
        individual_fund_flow, lhb_status, margin_trading, announcements,
    ]
    valid = sum(1 for s in items if s in _VALID_EVIDENCE)
    return int((valid / len(items)) * 100)


def assess_confidence(
    data_completeness: int,
    has_contradictory_signals: bool = False,
    analyst_agreement_level: Optional[float] = None,
    event_risk_active: bool = False,
) -> ConfidenceLevel:
    """
    评估置信度。

    参数:
        data_completeness: 数据完整度 (0-100)
        has_contradictory_signals: 分析师信号是否矛盾
        analyst_agreement_level: 分析师一致度 (0-1)，1 表示完全一致
        event_risk_active: 是否有事件风险激活
    """
    base_score = data_completeness

    if has_contradictory_signals:
        base_score -= 20

    if analyst_agreement_level is not None and analyst_agreement_level < 0.5:
        base_score -= 15

    if event_risk_active:
        base_score -= 10

    if base_score >= 70:
        return ConfidenceLevel.HIGH
    elif base_score >= 40:
        return ConfidenceLevel.MEDIUM
    else:
        return ConfidenceLevel.LOW


def generate_readiness_score(
    data_completeness: int,
    confidence: ConfidenceLevel,
) -> dict:
    """
    生成执行就绪度评分。

    返回:
        {
            "data_completeness": int,  # 0-100
            "confidence": str,  # 高/中/低
            "summary": str,  # 一句话总结
        }
    """
    summary_parts = []

    if data_completeness >= 80:
        summary_parts.append("数据完整")
    elif data_completeness >= 50:
        summary_parts.append("数据基本完整")
    else:
        summary_parts.append("数据不完整")

    if confidence == ConfidenceLevel.HIGH:
        summary_parts.append("置信度高")
    elif confidence == ConfidenceLevel.MEDIUM:
        summary_parts.append("置信度中等")
    else:
        summary_parts.append("置信度低，建议人工复核")

    summary = "，".join(summary_parts) + "。"

    return {
        "data_completeness": data_completeness,
        "confidence": confidence.value,
        "summary": summary,
    }


def get_allowed_actions(data_completeness: int) -> dict:
    """
    根据数据完整度，返回允许和禁止的动作。

    规则：
    - 数据完整度 < 50%：禁止主动加仓建议，允许持有和止损触发
    - 数据完整度 50-79%：允许持有、止损、减仓，禁止主动加仓
    - 数据完整度 >= 80%：允许所有动作
    """
    if data_completeness < 50:
        return {
            "level": "low",
            "allowed": ["持有处理", "止损触发", "减仓触发", "观望"],
            "forbidden": ["买入", "加仓", "追涨"],
            "label": "低质量报告",
            "message": "数据完整度不足50%，禁止买入/加仓/追涨建议，仅允许持有和止损触发。",
        }
    elif data_completeness < 80:
        return {
            "level": "medium",
            "allowed": ["持有处理", "止损触发", "减仓触发", "观望"],
            "forbidden": ["加仓", "追涨"],
            "label": "中等质量报告",
            "message": "数据完整度50-79%，禁止主动加仓建议，允许持有和止损触发。",
        }
    else:
        return {
            "level": "high",
            "allowed": ["买入", "加仓", "持有处理", "止损触发", "减仓触发", "卖出", "观望"],
            "forbidden": [],
            "label": "高质量报告",
            "message": "数据完整，允许所有交易建议。",
        }


def get_position_status(user_context: dict) -> str:
    """根据 user_context 判断持仓状态。

    返回:
        "has_position" / "no_position" / "unknown"
    """
    if user_context is None:
        return "unknown"
    pos = user_context.get("current_position")
    if pos is None:
        return "unknown"
    if pos > 0:
        return "has_position"
    return "no_position"


def get_strong_action_gate(
    source_coverage: int,
    evidence_coverage: int,
    fund_flow_unit_verified: bool = True,
    fund_flow_not_mixed: bool = True,
    no_execution_field_conflict: bool = True,
    no_unresolved_analyst_conflict: bool = True,
    position_status: str = "unknown",
    contains_strong_action: bool = False,
    name_mismatch: bool = False,
    no_execution_zone_conflict: bool = True,
) -> dict:
    """强动作门禁检查。

    凡 source_coverage < 70 或 evidence_coverage < 70 或其他条件不满足时，
    禁止输出"立即清仓/重仓买入/立即买入/加仓/追涨"等强动作。

    持仓未知时：如果文本不含强动作（只是观察/中性），gate 通过；
    如果包含强持仓动作，gate 不通过。

    Returns:
        {"passed": bool, "failures": list[str]}
    """
    failures = []

    if source_coverage < 70:
        failures.append(f"source_coverage={source_coverage}% < 70%")
    if evidence_coverage < 70:
        failures.append(f"evidence_coverage={evidence_coverage}% < 70%")
    if not fund_flow_unit_verified:
        failures.append("资金流单位未校验")
    if not fund_flow_not_mixed:
        failures.append("个股资金流与板块资金流混用")
    if not no_execution_field_conflict:
        failures.append("执行层字段冲突")
    if not no_execution_zone_conflict:
        failures.append("入场/减仓区间冲突(execution_zone_conflict)")
    if not no_unresolved_analyst_conflict:
        failures.append("上游分析师结论冲突且未解释")
    if position_status == "unknown" and contains_strong_action:
        failures.append("持仓状态未知，禁止强动作")
    if name_mismatch:
        failures.append("股票名称校验失败(name_mismatch)，Buy/Risk Level 4 被降级")

    return {"passed": len(failures) == 0, "failures": failures}


def calculate_risk_level(
    source_coverage: int,
    evidence_coverage: int,
    broke_support: bool = False,
    main_capital_outflow_days: int = 0,
    volume_breakdown: bool = False,
    has_major_positive_announcement: bool = False,
    position_status: str = "unknown",
    name_mismatch: bool = False,
) -> dict:
    """计算 Risk Level（持仓/卖出侧风控等级 0-4）。

    返回:
        {"level": int, "note": str}
    """
    max_level = 3 if position_status == "unknown" else 4
    if name_mismatch:
        max_level = min(max_level, 3)

    level_4_ok = (
        source_coverage >= 85
        and evidence_coverage >= 85
        and broke_support
        and main_capital_outflow_days >= 2
        and volume_breakdown
        and not has_major_positive_announcement
    )
    if level_4_ok:
        return {"level": min(4, max_level), "note": ""}

    note = "Risk Level 4 条件证据不足" if max_level == 4 else "Risk Level 4 被降级"
    if name_mismatch and max_level <= 3:
        note = "名称校验失败，Risk Level 上限为 3"

    level_3_ok = broke_support and main_capital_outflow_days >= 1
    if level_3_ok:
        return {"level": min(3, max_level), "note": note}

    level_2_ok = main_capital_outflow_days >= 1 or broke_support
    if level_2_ok:
        return {"level": min(2, max_level), "note": note}

    return {"level": 0, "note": ""}


def calculate_buy_level(
    source_coverage: int,
    evidence_coverage: int,
    trend_confirmed: bool = False,
    main_capital_inflow_days: int = 0,
    volume_healthy_expansion: bool = False,
    has_major_negative_announcement: bool = False,
    no_execution_conflict: bool = True,
    no_unresolved_analyst_conflict: bool = True,
    position_status: str = "unknown",
    name_mismatch: bool = False,
) -> dict:
    """计算 Buy Level（买入/建仓侧等级 0-4）。

    返回:
        {"level": int, "note": str}
    """
    max_level = 2 if position_status == "unknown" else 4
    if name_mismatch:
        max_level = min(max_level, 3)

    level_4_ok = (
        source_coverage >= 85
        and evidence_coverage >= 85
        and trend_confirmed
        and main_capital_inflow_days >= 2
        and volume_healthy_expansion
        and not has_major_negative_announcement
        and no_execution_conflict
        and no_unresolved_analyst_conflict
    )
    if position_status == "unknown":
        note = "持仓状态未知，Buy Level 上限为 2"
    elif not level_4_ok:
        note = "Buy Level 4 条件证据不足" if source_coverage >= 85 and evidence_coverage >= 85 else ""
    else:
        note = ""

    if level_4_ok:
        return {"level": min(4, max_level), "note": note}

    level_3_ok = (
        source_coverage >= 70
        and evidence_coverage >= 70
        and trend_confirmed
        and main_capital_inflow_days >= 1
    )
    if level_3_ok:
        return {"level": min(3, max_level), "note": note}

    level_2_ok = trend_confirmed or main_capital_inflow_days >= 1
    if level_2_ok:
        return {"level": min(2, max_level), "note": note}

    return {"level": 1 if source_coverage >= 50 else 0, "note": note}


def calculate_opportunity_score(
    trend_confirmed: bool = False,
    capital_resonance: str = "none",
    catalyst_strength: str = "none",
    risk_reward_ratio: str = "unknown",
    entry_quality: str = "unknown",
    event_risk_active: bool = False,
) -> int:
    """计算 Opportunity Score（0-100）。

    维度：
    - 趋势确认度 (0-25)
    - 资金共振度 (0-20)
    - 催化强度 (0-20)
    - 盈亏比/风险收益比 (0-20)
    - 入场质量 (0-15)
    - 事件风险降权

    注意：Opportunity Score 不能单独决定买入，必须经过 evidence gate。
    """
    score = 0

    score += 25 if trend_confirmed else 0
    score += {"strong": 20, "moderate": 12, "weak": 5}.get(capital_resonance, 0)
    score += {"strong": 20, "moderate": 12, "weak": 5}.get(catalyst_strength, 0)
    score += {"favorable": 20, "neutral": 10, "unfavorable": 0}.get(risk_reward_ratio, 5)
    score += {"good": 15, "acceptable": 8, "poor": 0}.get(entry_quality, 3)

    if event_risk_active:
        score = int(score * 0.6)

    return min(100, max(0, score))


def format_execution_block(
    source_coverage: int,
    evidence_coverage: int,
    confidence: str,
    opportunity_score: int,
    risk_level: int,
    buy_level: int,
    risk_level_note: str = "",
    buy_level_note: str = "",
    strong_action_gate: dict = None,
    position_status: str = "unknown",
) -> str:
    """格式化报告末尾的「执行等级与证据门禁」结构化区块。"""
    gate = strong_action_gate or {"passed": True, "failures": []}

    if opportunity_score >= 85:
        opp_label = "高质量机会"
    elif opportunity_score >= 70:
        opp_label = "较好机会"
    elif opportunity_score >= 40:
        opp_label = "条件观察"
    else:
        opp_label = "机会弱"

    lines = [
        "### 执行等级与证据门禁",
        f"- Source Coverage：{source_coverage}%",
        f"- Evidence Coverage：{evidence_coverage}%",
        f"- Confidence：{confidence}",
        f"- Opportunity Score：{opportunity_score}/100（{opp_label}）",
        f"- Buy Level：Buy Level {buy_level}（{_BUY_LABELS.get(buy_level, '未知')}）"
        + (f" — {buy_level_note}" if buy_level_note else ""),
        f"- Risk Level：Risk Level {risk_level}（{_RISK_LABELS.get(risk_level, '未知')}）"
        + (f" — {risk_level_note}" if risk_level_note else ""),
        f"- Strong Action Gate：{'通过' if gate['passed'] else '未通过'}",
    ]
    if gate["failures"]:
        lines.append("- 降级原因：")
        for f in gate["failures"]:
            lines.append(f"  - {f}")

    pos_notes = {
        "unknown": "⚠️ 持仓状态未知，Buy Level 上限为 2，Risk Level 上限为 3",
        "no_position": "主输出为 Buy Level，持仓相关动作仅作假设性参考",
        "has_position": "主输出为 Risk Level，辅助输出加仓/补仓评估",
    }
    lines.append(f"- {pos_notes.get(position_status, '')}")

    return "\n".join(lines)


def format_readiness_score(score: dict) -> str:
    """格式化 readiness score 为报告文本"""
    actions = get_allowed_actions(score['data_completeness'])
    result = (
        f"\n\n📋 [C-008] 执行就绪度评分\n"
        f"- 数据完整度：{score['data_completeness']}%\n"
        f"- 置信度：{score['confidence']}\n"
        f"- 报告等级：{actions['label']}\n"
        f"- 允许动作：{', '.join(actions['allowed'])}\n"
        f"- 禁止动作：{', '.join(actions['forbidden']) if actions['forbidden'] else '无'}\n"
        f"- 总结：{score['summary']}"
    )
    if actions['forbidden']:
        result += f"\n\n⚠️ {actions['message']}"
    result += _format_version_block()
    return result


def _format_version_block() -> str:
    """Append system version block to the report."""
    try:
        from api.main import _GIT_COMMIT_SHORT, _BACKEND_START_TIME
        commit = _GIT_COMMIT_SHORT
        start_time = _BACKEND_START_TIME
    except Exception:
        commit = "unknown"
        start_time = "unknown"
    report_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return (
        f"\n\n### 系统版本\n"
        f"- Commit: {commit}\n"
        f"- 评分版本: D-001~D-004 + 三轮审核修复\n"
        f"- 后端启动时间: {start_time}\n"
        f"- 报告生成时间: {report_time}"
    )


_SANITIZE_STRONG_SELL = [
    (r'立即清仓', '等待触发条件，暂不执行强清仓'),
    (r'立刻清仓', '等待触发条件，暂不执行强清仓'),
    (r'强制清仓', '等待触发条件，暂不执行强清仓'),
    (r'清仓离场', '等待触发条件，暂不执行强清仓'),
    (r'清仓出局', '等待触发条件，暂不执行强清仓'),
    (r'全部卖出离场', '等待触发条件，暂不执行强清仓'),
]
_SANITIZE_STRONG_BUY = [
    (r'强买', '暂不执行强买入，等待条件确认'),
    (r'重仓买入', '暂不执行强买入，等待条件确认'),
    (r'立即买入', '暂不执行强买入，等待条件确认'),
    (r'立刻买入', '暂不执行强买入，等待条件确认'),
    (r'强烈买入', '暂不执行强买入，等待条件确认'),
    (r'满仓买入', '暂不执行强买入，等待条件确认'),
    (r'重仓布局', '暂不执行强买入，等待条件确认'),
    (r'强力买入', '暂不执行强买入，等待条件确认'),
    (r'建议加仓', '暂不执行强买入，等待条件确认'),
    (r'执行加仓', '暂不执行强买入，等待条件确认'),
    (r'加仓买入', '暂不执行强买入，等待条件确认'),
    (r'可以加仓', '暂不执行强买入，等待条件确认'),
    (r'应该加仓', '暂不执行强买入，等待条件确认'),
    (r'考虑加仓', '暂不执行强买入，等待条件确认'),
    (r'建议追涨', '暂不执行强买入，等待条件确认'),
    (r'可以追涨', '暂不执行强买入，等待条件确认'),
    (r'追涨买入', '暂不执行强买入，等待条件确认'),
]
_SANITIZE_NO_POSITION = [
    (r'减仓', '未持仓-相关持仓动作不适用-仅保留观察/建仓判断'),
    (r'清仓', '未持仓-相关持仓动作不适用-仅保留观察/建仓判断'),
    (r'止盈', '未持仓-相关持仓动作不适用-仅保留观察/建仓判断'),
    (r'止损', '未持仓-相关持仓动作不适用-仅保留观察/建仓判断'),
    (r'卖出', '未持仓-相关持仓动作不适用-仅保留观察/建仓判断'),
]

_SYSTEM_BLOCK_MARKERS = [
    "📊 数据源可用性：",
    "--- 报告质量评分 ---",
    "### 执行等级与证据门禁",
    "⚠️ [D-002]",
]


def _split_llm_body_and_system_blocks(text: str) -> tuple:
    """分离 LLM 原始输出（body）和系统追加区块。

    系统追加区块的起始标记包括：
    📊 数据源可用性、--- 报告质量评分 ---、### 执行等级与证据门禁、⚠️ [D-002]

    Returns:
        (body, system_blocks) where body is the LLM original output
        and system_blocks is everything after the first system marker.
    """
    earliest_idx = len(text)
    for marker in _SYSTEM_BLOCK_MARKERS:
        idx = text.find(marker)
        if idx >= 0 and idx < earliest_idx:
            earliest_idx = idx
    if earliest_idx < len(text):
        return text[:earliest_idx], text[earliest_idx:]
    return text, ""


def sanitize_forbidden_strong_actions(
    text: str,
    gate: dict,
    position_status: str,
    buy_level: int,
    risk_level: int,
) -> tuple:
    """移除或替换最终报告中未被 gate 允许的强动作表达。

    未持仓动作清理（减仓/清仓/止盈/止损/卖出）独立于 gate 检查，
    只要 position_status == "no_position" 就会执行。
    强买/强卖替换仅在 gate 未通过时执行。

    Returns:
        (sanitized_text, list_of_changes)
    """
    changes = []
    result = text

    body, system_blocks = _split_llm_body_and_system_blocks(result)

    if position_status == "no_position":
        for pattern, replacement in _SANITIZE_NO_POSITION:
            matches = list(re.finditer(pattern, body))
            if matches:
                changes.append(f"未持仓动作已降级为「{replacement}」")
                body = re.sub(pattern, replacement, body)

    if not gate.get("passed", True):
        for pattern, replacement in _SANITIZE_STRONG_SELL:
            matches = list(re.finditer(pattern, body))
            if matches:
                changes.append(f"强卖出动作已降级为「{replacement}」")
                body = re.sub(pattern, replacement, body)

        for pattern, replacement in _SANITIZE_STRONG_BUY:
            matches = list(re.finditer(pattern, body))
            if matches:
                changes.append(f"强买入动作已降级为「{replacement}」")
                body = re.sub(pattern, replacement, body)

    if changes:
        if not gate.get("passed", True):
            downgrade_note = "\n\n⚠️ [D-002] 强动作门禁未通过，以下动作已降级：\n"
            for c in changes:
                downgrade_note += f"  - {c}\n"
            downgrade_note += f"  - 降级原因：{'；'.join(gate.get('failures', []))}\n"
        else:
            downgrade_note = "\n\n⚠️ [D-002] 持仓状态校验，以下动作已降级：\n"
            for c in changes:
                downgrade_note += f"  - {c}\n"
        body += downgrade_note

    return body + system_blocks, changes


_OHLC_PATTERNS = [
    r'\d{4}[-/]\d{2}[-/]\d{2}.*?(?:开盘|最高|最低|收盘|Open|High|Low|Close)',
    r'(?:开盘价|最高价|最低价|收盘价)\s*[:：]\s*\d+\.?\d*',
    r'(?:日期|Date).*(?:开|高|低|收).*(?:开|高|低|收)',
    r'\|?\s*\d{4}[-/]\d{1,2}[-/]\d{1,2}\s*\|.*\d+\.\d+\s*\|.*\d+\.\d+\s*\|.*\d+\.\d+\s*\|',
]
_VOLUME_PATTERN = r'(?:成交量|Volume|vol)\s*[:：=]?\s*[\d,.]+\s*(?:万|亿|手|股)?'
_TURNOVER_PATTERN = r'(?:换手率|Turnover)\s*[:：=]?\s*\d+\.?\d*\s*%?'
_VOLUME_RATIO_PATTERN = r'(?:量比|Volume\s*Ratio)\s*[:：=]?\s*\d+\.?\d*'
_FUND_FLOW_PATTERN = r'(?:主力|净流入|净流出|大单|超大单).*?(?:\d+\.?\d*\s*(?:万|亿|元))'
_LHB_NORMAL_PATTERNS = [
    r'龙虎榜.*?(?:无触发|无数据|未上榜|没有.*?上榜|正常)',
    r'(?:未上榜|无龙虎榜|查询未触发)',
]
_LHB_FAILED_PATTERNS = [
    r'龙虎榜.*?(?:获取失败|查询失败|API.*?错误|异常|Error)',
    r'(?:获取失败|查询失败|API.*?错误)',
]
_ANNOUNCEMENT_PATTERN = r'(?:公告|announcement).*(?:\d{4}[-/]\d{2}[-/]\d{2}|原文|标题|摘要)'


def _text_match_any(text: str, patterns: list) -> bool:
    for p in patterns:
        if re.search(p, text, re.IGNORECASE | re.DOTALL):
            return True
    return False


def infer_evidence_statuses(reports: dict, raw_evidence: Optional[dict] = None) -> dict:
    """根据报告文本推断各项证据状态。

    When *raw_evidence* dict is provided (from DataCollector pool), structured
    fields are used directly and text-based regex is skipped for those keys.
    Falls back to text regex for keys without raw evidence.

    Args:
        reports: dict with keys like 'market_report', 'volume_price_report',
                 'smart_money_report', 'news_report' etc.
        raw_evidence: optional dict from DataCollector pool with keys like
                      'stock_data', 'fund_flow_individual', 'lhb', etc.

    Returns:
        dict with evidence field names mapped to EvidenceStatus values.
    """
    market = reports.get("market_report", "") or ""
    volume_price = reports.get("volume_price_report", "") or ""
    smart_money = reports.get("smart_money_report", "") or ""
    news = reports.get("news_report", "") or ""

    combined = f"{market}\n{volume_price}\n{smart_money}\n{news}"

    # ── Structured evidence from raw data pool (E-004) ──
    raw = raw_evidence or {}

    # 1. OHLCV 5d — check stock_data CSV
    ohlcv_5d = EvidenceStatus.NOT_QUERIED
    raw_stock_data = raw.get("stock_data")
    if raw_stock_data and isinstance(raw_stock_data, str) and len(raw_stock_data) > 50:
        ohlcv_5d = EvidenceStatus.HAS_DATA
    elif raw_stock_data is not None:
        ohlcv_5d = EvidenceStatus.QUERY_FAILED if (isinstance(raw_stock_data, str) and "失败" in raw_stock_data) else EvidenceStatus.NORMAL_NO_DATA
    elif market:
        if _text_match_any(market, _OHLC_PATTERNS):
            ohlcv_5d = EvidenceStatus.HAS_DATA
        elif market.strip():
            ohlcv_5d = EvidenceStatus.FIELD_MISSING

    # 2. Volume — derived from stock_data
    volume = EvidenceStatus.NOT_QUERIED
    if raw_stock_data and isinstance(raw_stock_data, str) and len(raw_stock_data) > 50:
        volume = EvidenceStatus.HAS_DATA
    else:
        src = market or volume_price
        if src:
            if re.search(_VOLUME_PATTERN, src, re.IGNORECASE):
                volume = EvidenceStatus.HAS_DATA
            elif src.strip():
                volume = EvidenceStatus.FIELD_MISSING

    # 3. Turnover rate — NOT in raw data pool (needs separate akshare call)
    turnover_rate = EvidenceStatus.NOT_QUERIED
    if volume_price:
        if re.search(_TURNOVER_PATTERN, volume_price, re.IGNORECASE):
            turnover_rate = EvidenceStatus.HAS_DATA
        elif volume_price.strip():
            turnover_rate = EvidenceStatus.FIELD_MISSING

    # 4. Volume ratio — NOT in raw data pool
    volume_ratio = EvidenceStatus.NOT_QUERIED
    if volume_price:
        if re.search(_VOLUME_RATIO_PATTERN, volume_price, re.IGNORECASE):
            volume_ratio = EvidenceStatus.HAS_DATA
        elif volume_price.strip():
            volume_ratio = EvidenceStatus.FIELD_MISSING

    # 5. Individual fund flow — check fund_flow_individual
    individual_fund_flow = EvidenceStatus.NOT_QUERIED
    raw_fund_flow = raw.get("fund_flow_individual")
    if raw_fund_flow is not None:
        if isinstance(raw_fund_flow, str) and len(raw_fund_flow) > 20 and "失败" not in raw_fund_flow:
            individual_fund_flow = EvidenceStatus.HAS_DATA
        elif isinstance(raw_fund_flow, str) and "失败" in raw_fund_flow:
            individual_fund_flow = EvidenceStatus.QUERY_FAILED
        elif raw_fund_flow:
            individual_fund_flow = EvidenceStatus.HAS_DATA
        else:
            individual_fund_flow = EvidenceStatus.NORMAL_NO_DATA
    elif smart_money:
        if re.search(_FUND_FLOW_PATTERN, smart_money, re.IGNORECASE | re.DOTALL):
            individual_fund_flow = EvidenceStatus.HAS_DATA
        elif smart_money.strip():
            individual_fund_flow = EvidenceStatus.FIELD_MISSING

    # 6. LHB (龙虎榜) — check lhb field
    lhb_status = EvidenceStatus.NOT_QUERIED
    raw_lhb = raw.get("lhb")
    if raw_lhb is not None:
        if isinstance(raw_lhb, str) and "失败" in raw_lhb:
            lhb_status = EvidenceStatus.QUERY_FAILED
        elif isinstance(raw_lhb, str) and ("无" in raw_lhb or "未上榜" in raw_lhb or "未触发" in raw_lhb or len(raw_lhb.strip()) == 0):
            lhb_status = EvidenceStatus.NORMAL_NO_DATA
        elif raw_lhb:
            lhb_status = EvidenceStatus.HAS_DATA
        else:
            lhb_status = EvidenceStatus.NORMAL_NO_DATA
    elif combined:
        if _text_match_any(combined, _LHB_FAILED_PATTERNS):
            lhb_status = EvidenceStatus.QUERY_FAILED
        elif _text_match_any(combined, _LHB_NORMAL_PATTERNS):
            lhb_status = EvidenceStatus.NORMAL_NO_DATA
        elif re.search(r'龙虎榜', combined, re.IGNORECASE):
            lhb_status = EvidenceStatus.FIELD_MISSING

    # 7. Margin trading (融资融券) — NOT in raw data pool
    margin_trading = EvidenceStatus.NOT_QUERIED

    # 8. Announcements — check news data
    announcements = EvidenceStatus.NOT_QUERIED
    raw_news = raw.get("news")
    if raw_news is not None:
        if isinstance(raw_news, str) and len(raw_news) > 50:
            announcements = EvidenceStatus.HAS_DATA
        elif isinstance(raw_news, str) and "失败" in raw_news:
            announcements = EvidenceStatus.QUERY_FAILED
        else:
            announcements = EvidenceStatus.NORMAL_NO_DATA
    elif news:
        if re.search(_ANNOUNCEMENT_PATTERN, news, re.IGNORECASE | re.DOTALL):
            announcements = EvidenceStatus.HAS_DATA
        elif news.strip():
            announcements = EvidenceStatus.FIELD_MISSING

    return {
        "ohlcv_5d": ohlcv_5d,
        "volume": volume,
        "turnover_rate": turnover_rate,
        "volume_ratio": volume_ratio,
        "individual_fund_flow": individual_fund_flow,
        "lhb_status": lhb_status,
        "margin_trading": margin_trading,
        "announcements": announcements,
    }


_BROKE_SUPPORT_PATTERNS = [
    r'跌破.*?(?:关键支撑|支撑位|支撑线|均线)',
    r'(?:收盘)?跌破',
    r'放量破位',
    r'破位下(?:行|跌)',
]
_OUTFLOW_PATTERN = r'连续\s*(\d+)\s*日.*?(?:净流出|主力.*?流出|资金流出)'
_INFLOW_PATTERN = r'连续\s*(\d+)\s*日.*?(?:净流入|主力.*?流入|资金流入)'
_VOLUME_BREAKDOWN_PATTERNS = [
    r'放量破位',
    r'放量.*?(?:下跌|下挫|暴跌|跌)',
    r'量能.*?(?:放大|放大.*?下跌)',
]
_TREND_CONFIRMED_PATTERNS = [
    r'趋势确认',
    r'多头排列',
    r'有效突破',
    r'站上.*?(?:关键均线|MA\d+|均线)',
    r'(?:上涨|上升).*?趋势.*?(?:确认|成立|延续)',
]
_VOLUME_HEALTHY_PATTERNS = [
    r'量能.*?(?:健康.*?放大|温和放大|同步放大|配合良好)',
    r'放量.*?(?:上涨|突破|创新高)',
    r'成交量.*?(?:放大|递增|活跃)',
]
_POSITIVE_ANNOUNCEMENT_PATTERNS = [
    r'重大利好',
    r'(?:利好|利好消息|重大公告).*(?:发布|公告|披露)',
    r'(?:业绩预增|业绩大幅|超预期|重大合同|并购|增持)',
]
_NEGATIVE_ANNOUNCEMENT_PATTERNS = [
    r'重大利空',
    r'(?:利空|利空消息).*(?:发布|公告|披露)',
    r'(?:业绩预亏|业绩下滑|重大风险|违规|处罚|减持)',
]
_CONFLICT_PATTERNS = [
    r'(?:冲突|矛盾|不一致)',
    r'止损价.*?缺失',
    r'入场价.*?高于.*?止盈价',
    r'字段.*?(?:冲突|不一致)',
    r'数据.*?(?:矛盾|不一致)',
]
_ANALYST_CONFLICT_PATTERNS = [
    r'(?:分析师|模块|上游).*?(?:分歧|冲突|矛盾)',
    r'verdict.*?分歧',
    r'(?:多空|多空双方).*?(?:分歧|对立)',
]
_CAPITAL_RESONANCE_STRONG = [
    r'(?:主力|资金).*(?:连续.*?流入|大幅流入|共振.*?强)',
    r'(?:多维度|多模块).*?(?:共振|一致看多|同向)',
]
_CAPITAL_RESONANCE_MODERATE = [
    r'(?:主力|资金).*(?:流入|净流入)',
    r'(?:部分|一定).*?(?:共振|一致)',
]
_CAPITAL_RESONANCE_WEAK = [
    r'(?:资金|主力).*(?:小幅|略有).*?(?:流入|关注)',
]
_CATALYST_STRONG = [
    r'(?:强催化剂|重大催化|政策利好|行业拐点)',
    r'(?:重大利好|重大事件).*(?:即将|预期)',
]
_CATALYST_MODERATE = [
    r'(?:催化剂|催化|利好).*(?:一般|中等|有一定)',
    r'(?:政策|行业).*?(?:支持|利好)',
]
_CATALYST_WEAK = [
    r'(?:小幅|轻微|潜在).*(?:催化|利好)',
]
_RISK_REWARD_FAVORABLE = [
    r'(?:盈亏比|风险收益比).*(?:优秀|高|好|大于\s*2|>\s*2|有利)',
    r'(?:上涨空间.*?(?:大于|远大于).*?下跌空间)',
]
_RISK_REWARD_NEUTRAL = [
    r'(?:盈亏比|风险收益比).*(?:中性|一般|适中|约\s*1)',
]
_RISK_REWARD_UNFAVORABLE = [
    r'(?:盈亏比|风险收益比).*(?:差|低|不利|小于\s*1|<\s*1)',
    r'(?:下跌风险.*?(?:大于|高于).*?上涨)',
]
_ENTRY_QUALITY_GOOD = [
    r'(?:入场点|买点).*(?:优秀|好|合理|精确)',
    r'(?:支撑位.*?附近|回调.*?买入)',
]
_ENTRY_QUALITY_ACCEPTABLE = [
    r'(?:入场点|买点).*(?:一般|尚可|可接受)',
]
_ENTRY_QUALITY_POOR = [
    r'(?:入场点|买点).*(?:差|不明确|模糊|难以判断)',
]

_STOP_LOSS_KEYWORDS = [
    r'止损红线\s*[:：]?\s*\d+\.?\d*',
    r'止损位\s*[:：]?\s*\d+\.?\d*',
    r'止损价\s*[:：]?\s*\d+\.?\d*',
    r'清仓线\s*[:：]?\s*\d+\.?\d*',
    r'止损线\s*[:：]?\s*\d+\.?\d*',
    r'止损\s*[:：]?\s*\d+\.?\d*',
]

_ENTRY_ZONE_KEYWORDS = [
    r'入场区间\s*[:：]?\s*[\d.]+\s*[-–—]\s*[\d.]+',
    r'买入区间\s*[:：]?\s*[\d.]+\s*[-–—]\s*[\d.]+',
    r'建仓区间\s*[:：]?\s*[\d.]+\s*[-–—]\s*[\d.]+',
    r'入场价\s*[:：]?\s*\d+\.?\d*',
    r'买入价位\s*[:：]?\s*\d+\.?\d*',
    r'建仓价\s*[:：]?\s*\d+\.?\d*',
]

_REDUCE_ZONE_KEYWORDS = [
    r'减仓区间\s*[:：]?\s*[\d.]+\s*[-–—]\s*[\d.]+',
    r'减仓位\s*[:：]?\s*\d+\.?\d*',
    r'减仓价位\s*[:：]?\s*\d+\.?\d*',
    r'部分止盈位\s*[:：]?\s*\d+\.?\d*',
]

_PRICE_NUMBER_RE = re.compile(r'(\d+\.?\d*)')


def _match_first_group(text: str, pattern: str) -> int:
    m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    if m and m.group(1):
        try:
            return int(m.group(1))
        except (ValueError, IndexError):
            pass
    return 0


def _classify_level(text: str, strong: list, moderate: list, weak: list) -> str:
    if _text_match_any(text, strong):
        return "strong"
    if _text_match_any(text, moderate):
        return "moderate"
    if _text_match_any(text, weak):
        return "weak"
    return "none"


def _extract_first_price(text: str, patterns: list) -> Optional[float]:
    """Extract the first numeric price from matching pattern."""
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            nums = _PRICE_NUMBER_RE.findall(m.group(0))
            if nums:
                try:
                    return float(nums[0])
                except ValueError:
                    pass
    return None


def _extract_price_range(text: str, patterns: list) -> Optional[tuple]:
    """Extract (low, high) price range from text using range patterns."""
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            nums = _PRICE_NUMBER_RE.findall(m.group(0))
            if len(nums) >= 2:
                try:
                    lo, hi = float(nums[0]), float(nums[1])
                    if lo > hi:
                        lo, hi = hi, lo
                    return (lo, hi)
                except ValueError:
                    pass
            elif len(nums) == 1:
                try:
                    v = float(nums[0])
                    return (v, v)
                except ValueError:
                    pass
    return None


def extract_execution_signals(state: dict, final_response: str, reports: dict) -> dict:
    """从报告文本中保守提取交易信号。

    Args:
        state: LangGraph state dict
        final_response: 最终输出文本
        reports: dict with analyst report strings

    Returns:
        dict with signal fields
    """
    all_text_parts = []
    for key in ("market_report", "sentiment_report", "news_report",
                "fundamentals_report", "smart_money_report", "volume_price_report"):
        val = reports.get(key, "") or state.get(key, "") or ""
        if val:
            all_text_parts.append(val)
    all_text_parts.append(final_response)
    text = "\n".join(all_text_parts)

    broke_support = _text_match_any(text, _BROKE_SUPPORT_PATTERNS)

    main_capital_outflow_days = _match_first_group(text, _OUTFLOW_PATTERN)
    main_capital_inflow_days = _match_first_group(text, _INFLOW_PATTERN)

    volume_breakdown = _text_match_any(text, _VOLUME_BREAKDOWN_PATTERNS)
    trend_confirmed = _text_match_any(text, _TREND_CONFIRMED_PATTERNS)
    volume_healthy_expansion = _text_match_any(text, _VOLUME_HEALTHY_PATTERNS)

    has_major_positive_announcement = _text_match_any(text, _POSITIVE_ANNOUNCEMENT_PATTERNS)
    has_major_negative_announcement = _text_match_any(text, _NEGATIVE_ANNOUNCEMENT_PATTERNS)

    no_execution_conflict = not _text_match_any(text, _CONFLICT_PATTERNS)

    no_unresolved_analyst_conflict = not _text_match_any(text, _ANALYST_CONFLICT_PATTERNS)

    capital_resonance = _classify_level(text, _CAPITAL_RESONANCE_STRONG, _CAPITAL_RESONANCE_MODERATE, _CAPITAL_RESONANCE_WEAK)
    catalyst_strength = _classify_level(text, _CATALYST_STRONG, _CATALYST_MODERATE, _CATALYST_WEAK)

    if _text_match_any(text, _RISK_REWARD_FAVORABLE):
        risk_reward_ratio = "favorable"
    elif _text_match_any(text, _RISK_REWARD_NEUTRAL):
        risk_reward_ratio = "neutral"
    elif _text_match_any(text, _RISK_REWARD_UNFAVORABLE):
        risk_reward_ratio = "unfavorable"
    else:
        risk_reward_ratio = "unknown"

    if _text_match_any(text, _ENTRY_QUALITY_GOOD):
        entry_quality = "good"
    elif _text_match_any(text, _ENTRY_QUALITY_ACCEPTABLE):
        entry_quality = "acceptable"
    elif _text_match_any(text, _ENTRY_QUALITY_POOR):
        entry_quality = "poor"
    else:
        entry_quality = "unknown"

    # [E-003] Stop-loss keyword detection
    has_stop_loss = _text_match_any(text, _STOP_LOSS_KEYWORDS)

    # [E-003] Entry zone vs reduce zone detection
    has_entry_zone = _text_match_any(text, _ENTRY_ZONE_KEYWORDS)
    has_reduce_zone = _text_match_any(text, _REDUCE_ZONE_KEYWORDS)

    execution_zone_conflict = False

    stop_loss_price = _extract_first_price(text, _STOP_LOSS_KEYWORDS)
    entry_price = _extract_first_price(text, _ENTRY_ZONE_KEYWORDS)

    if stop_loss_price is not None and entry_price is not None:
        if stop_loss_price > entry_price:
            execution_zone_conflict = True

    if has_entry_zone and has_reduce_zone:
        entry_range = _extract_price_range(text, _ENTRY_ZONE_KEYWORDS)
        reduce_range = _extract_price_range(text, _REDUCE_ZONE_KEYWORDS)
        if entry_range and reduce_range:
            entry_lo, entry_hi = entry_range
            reduce_lo, reduce_hi = reduce_range
            if max(entry_lo, reduce_lo) < min(entry_hi, reduce_hi):
                execution_zone_conflict = True

    return {
        "broke_support": broke_support,
        "main_capital_outflow_days": main_capital_outflow_days,
        "main_capital_inflow_days": main_capital_inflow_days,
        "volume_breakdown": volume_breakdown,
        "trend_confirmed": trend_confirmed,
        "volume_healthy_expansion": volume_healthy_expansion,
        "has_major_positive_announcement": has_major_positive_announcement,
        "has_major_negative_announcement": has_major_negative_announcement,
        "no_execution_conflict": no_execution_conflict,
        "no_unresolved_analyst_conflict": no_unresolved_analyst_conflict,
        "capital_resonance": capital_resonance,
        "catalyst_strength": catalyst_strength,
        "risk_reward_ratio": risk_reward_ratio,
        "entry_quality": entry_quality,
        "has_stop_loss": has_stop_loss,
        "execution_zone_conflict": execution_zone_conflict,
    }
