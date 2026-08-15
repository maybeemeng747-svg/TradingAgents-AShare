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
    SKIPPED = "skipped"
    NOT_AVAILABLE = "not_available"  # Data source does not provide this field


# [DATA-021] report_data_blockers
_DATA_BLOCKER_FIELDS = {
    "ohlcv_5d": {
        "label": "行情/K线",
        "reason": "最近开高低收或基础行情证据不可用",
        "impact": "无法校验价格位置、趋势和触发区间，强动作需要降级或等待确认。",
    },
    "individual_fund_flow": {
        "label": "主力资金",
        "reason": "个股主力资金流证据不可用",
        "impact": "资金面不能支撑买入/减仓强结论，相关判断置信度下调。",
    },
    "lhb_status": {
        "label": "龙虎榜",
        "reason": "龙虎榜证据不可用",
        "impact": "无法确认异动席位和游资结构；若为未上榜，通常只作为正常无数据记录。",
    },
    "announcements": {
        "label": "公告/新闻",
        "reason": "公告或事件证据不可用",
        "impact": "无法验证重大利好/利空是否对冲技术或资金信号。",
    },
    "ratings": {
        "label": "分析师评级",
        "reason": "分析师评级证据不可用",
        "impact": "外部一致预期缺口，只能作为辅助信息降权，不单独触发交易动作。",
    },
    "buybacks": {
        "label": "回购",
        "reason": "回购事件证据不可用",
        "impact": "无法确认公司层面的主动托底或股东回报信号。",
    },
    "research_report": {
        "label": "券商研报",
        "reason": "券商研报证据不可用",
        "impact": "中线逻辑和盈利预测缺少外部验证，趋势结论需要更多原始证据支撑。",
    },
    "margin_trading": {
        "label": "融资融券",
        "reason": "融资融券证据不可用",
        "impact": "杠杆资金变化无法交叉验证风险偏好。",
    },
    "turnover_rate": {
        "label": "换手率",
        "reason": "换手率字段缺失",
        "impact": "无法判断筹码交换强度，短线触发信号需要降权。",
    },
    "volume_ratio": {
        "label": "量比",
        "reason": "量比字段缺失",
        "impact": "盘中/短线量能确认不足，突破或破位信号需要等待补证。",
    },
}

_BLOCKER_STATUS_META = {
    EvidenceStatus.NORMAL_NO_DATA: {
        "status": "normal_no_data",
        "severity": "info",
        "status_label": "正常无数据",
    },
    EvidenceStatus.QUERY_FAILED: {
        "status": "query_failed",
        "severity": "high",
        "status_label": "查询失败",
    },
    EvidenceStatus.NOT_QUERIED: {
        "status": "not_queried",
        "severity": "medium",
        "status_label": "未查询",
    },
    EvidenceStatus.FIELD_MISSING: {
        "status": "field_missing",
        "severity": "medium",
        "status_label": "字段缺失",
    },
    EvidenceStatus.NOT_AVAILABLE: {
        "status": "skipped",
        "severity": "low",
        "status_label": "已跳过",
    },
    EvidenceStatus.SKIPPED: {
        "status": "skipped",
        "severity": "low",
        "status_label": "已跳过",
    },
}


_STRONG_NAME_CONTEXTS = [
    re.compile(r'\d{6}\.(?:SZ|SH|BJ)\s*[\(（]\s*([\u4e00-\u9fff]{2,8})\s*[\)）]'),
    re.compile(r'(?:标的|公司|名称|股票名称)\s*[：:]\s*([\u4e00-\u9fff]{2,8})'),
    re.compile(r'关于\s*([\u4e00-\u9fff]{2,8})\s*的'),
    re.compile(r'#\s*\d{6}\.(?:SZ|SH|BJ)\s+([\u4e00-\u9fff]{2,8})'),
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
        for suffix in (".SH", ".SZ", ".BJ"):
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
_EXCLUDE_FROM_COVERAGE = {EvidenceStatus.NOT_AVAILABLE}

# [F-001-fix] Evidence weight: HAS_DATA = full, NORMAL_NO_DATA = partial (0.5)
_EVIDENCE_WEIGHTS = {
    EvidenceStatus.HAS_DATA: 1.0,
    EvidenceStatus.NORMAL_NO_DATA: 0.5,
}

_RISK_LABELS = {0: "风险观察", 1: "禁止开仓", 2: "条件减仓", 3: "触发止损", 4: "立即清仓"}
_BUY_LABELS = {0: "禁止买入", 1: "观察", 2: "条件试仓", 3: "确认建仓", 4: "积极建仓"}

# [Fix-9] + [C-003] A-share short-selling filter patterns
# [C-003-R1] Split into Chinese (literal) and English (case-insensitive)
# pattern groups. English LLM output commonly uses uppercase action tokens
# (e.g. "OPEN SHORT", "Short Selling"), which plain re.sub would miss.
_SHORT_SELLING_PATTERNS_CN = [
    (r'做空策略', '看空信号'),
    (r'空头开仓', '离场信号'),
    (r'若做空', '若确认偏空'),
    (r'做空[，,。]', '回避信号，'),
    (r'做空\)', '回避)'),
    (r'考虑做空', '考虑减仓/止损'),
    (r'建议做空', '建议回避'),
    (r'可以做空', '应考虑离场'),
    # [C-003] expanded patterns
    (r'反手做空', '反手离场'),
    (r'开空仓', '离场观望'),
    (r'融券做空', '回避'),
    (r'融券卖出', '回避'),
    (r'逢高做空', '逢高减仓'),
    (r'加空[仓]?', '减仓'),
    (r'空头加仓', '减仓'),
    (r'试空', '试探离场'),
    (r'平空', '平仓离场'),
    (r'做空仓位', '空仓观望'),
]

# [C-003-R1] Explicit whitelist of legitimate long-side exit vocabulary.
# These must NEVER be stripped — they are risk-reduction actions for an
# existing long position, not short-selling. This list makes the protection
# explicit rather than relying solely on patterns not matching by coincidence.
_LEGIT_SELL_EXIT_KEYWORDS = frozenset({
    # Chinese
    "卖出", "减仓", "清仓", "止盈", "止损", "退出", "离场", "减持股",
    # English (case-insensitive comparison)
    "sell", "exit", "reduce", "trim",
})


def _is_legit_sell_exit_phrase(text: str) -> bool:
    """[C-003-R1] Check if text is a legitimate long-side exit action.

    Returns True only when the text contains a legit sell/exit keyword AND
    does NOT contain any short-selling-specific compound. This guards against
    overly broad future patterns误杀合法建议.

    Note: English legit words use word-boundary matching to avoid false
    positives (e.g. "sell" in "short selling" must NOT be treated as legit).
    """
    text_stripped = text.strip()
    text_lower = text_stripped.lower()

    # Chinese: substring match is safe because Chinese keywords are distinct
    cn_legit = ("卖出", "减仓", "清仓", "止盈", "止损", "退出", "离场", "减持股")
    has_cn_legit = any(kw in text_stripped for kw in cn_legit)

    # English: word-boundary match to avoid "sell" matching inside "short selling"
    has_en_legit = bool(re.search(r'\b(sell|exit|reduce|trim)\b', text_lower))

    if not has_cn_legit and not has_en_legit:
        return False

    # If a shorting compound is present, it takes priority
    shorting_compounds = ("融券卖出", "空头开仓", "试空", "平空", "加空",
                          "做空", "开空仓", "反手做空", "逢高做空")
    if any(sc in text_stripped for sc in shorting_compounds):
        return False
    # English shorting phrases (case-insensitive)
    en_shorting = re.search(r'\bshort\b', text_lower)
    if en_shorting:
        return False

    return True
# [C-003-R1] English patterns evaluated with re.IGNORECASE.
_SHORT_SELLING_PATTERNS_EN = [
    (r'short\s*position', '回避'),
    (r'short\s*sel(?:l|ling)?', '回避'),
    (r'open\s*(?:a\s*)?short', '回避'),
]

# [C-003] Backwards-compatible combined list (CN first, then EN).
# NOTE: callers that need case-insensitive English matching should use
# _sanitize_short_selling_text / ShortSellingStreamFilter rather than
# iterating this list with plain re.sub.
_SHORT_SELLING_PATTERNS = _SHORT_SELLING_PATTERNS_CN + _SHORT_SELLING_PATTERNS_EN

# [C-003-R1] Legitimate long-side exit vocabulary that must NOT be filtered
# as short-selling. These are valid for an already-held long position.
_LEGIT_LONG_EXIT_KEYWORDS = (
    '卖出', '减仓', '止损', '止盈', '清仓', 'EXIT', 'SELL', 'REDUCE',
)


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


def calculate_evidence_coverage_from_contract(raw_evidence: Optional[dict] = None) -> int:
    """[DATA-004] raw_evidence_contract: calculate evidence coverage directly from contract.

    Uses the structured contract fields (status, unit, vendor, endpoint) rather
    than text-based regex. Falls back to 0 if no raw_evidence available.

    Unit unknown or field conflict causes coverage downgrade.
    """
    if not raw_evidence:
        return 0

    from tradingagents.dataflows.evidence_contract import compute_contract_completeness
    result = compute_contract_completeness(raw_evidence)
    return result["completeness_score"]


def calculate_evidence_coverage(
    ohlcv_5d: str = "not_queried",
    volume: str = "not_queried",
    turnover_rate: str = "not_queried",
    volume_ratio: str = "not_queried",
    individual_fund_flow: str = "not_queried",
    lhb_status: str = "not_queried",
    margin_trading: str = "not_queried",
    announcements: str = "not_queried",
    research_report: str = "not_queried",
    ratings: str = "not_available",
    buybacks: str = "not_available",  # [DATA-013] buyback_raw_evidence
) -> int:
    """
    计算原始证据覆盖度（0-100%）。

    区分：有数据 / 正常无触发 / 查询失败 / 未查询 / 字段缺失。
    [F-001-fix] Weighted: HAS_DATA = 1.0, NORMAL_NO_DATA = 0.5.
    This prevents NORMAL_NO_DATA from inflating coverage when data completeness is low.
    """
    items = [
        ohlcv_5d, volume, turnover_rate, volume_ratio,
        individual_fund_flow, lhb_status, margin_trading, announcements,
        research_report, ratings, buybacks,
    ]
    # Exclude NOT_AVAILABLE fields from denominator (data source doesn't provide them)
    counted = [s for s in items if s not in _EXCLUDE_FROM_COVERAGE]
    # [F-001-fix] Weighted sum: HAS_DATA counts fully, NORMAL_NO_DATA counts 0.5
    weighted = sum(_EVIDENCE_WEIGHTS.get(s, 0) for s in counted)
    return int((weighted / len(counted)) * 100) if counted else 0


def assess_confidence(
    data_completeness: int,
    has_contradictory_signals: bool = False,
    analyst_agreement_level: Optional[float] = None,
    event_risk_active: bool = False,
    evidence_coverage: Optional[int] = None,  # [DATA-P0-603629] astock_source_fallback
    fundamental_integrity_valid: bool = True,
) -> ConfidenceLevel:
    """
    评估置信度。

    参数:
        data_completeness: 数据完整度 (0-100)
        has_contradictory_signals: 分析师信号是否矛盾
        analyst_agreement_level: 分析师一致度 (0-1)，1 表示完全一致
        event_risk_active: 是否有事件风险激活
        evidence_coverage: 原始证据覆盖度 (0-100)。
            当 < 70% 时，confidence 不得为 HIGH。
        fundamental_integrity_valid: 基本面语义门禁是否通过。
            未通过时，中线报告置信度不得为 HIGH。
    """
    base_score = data_completeness

    if has_contradictory_signals:
        base_score -= 20

    if analyst_agreement_level is not None and analyst_agreement_level < 0.5:
        base_score -= 15

    if event_risk_active:
        base_score -= 10

    # [DATA-P0-603629] astock_source_fallback: evidence_coverage < 70 → cap at MEDIUM
    if evidence_coverage is not None and evidence_coverage < 70:
        base_score = min(base_score, 65)

    if not fundamental_integrity_valid:
        base_score = min(base_score, 65)

    if base_score >= 70:
        return ConfidenceLevel.HIGH
    elif base_score >= 40:
        return ConfidenceLevel.MEDIUM
    else:
        return ConfidenceLevel.LOW


def generate_readiness_score(
    data_completeness: int,
    confidence: ConfidenceLevel,
    blockers: Optional[list[str]] = None,
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
        "blockers": list(blockers or []),
    }


def get_allowed_actions(data_completeness: int, position_status: str = "unknown") -> dict:
    """根据数据完整度和持仓状态，返回允许和禁止的动作。

    G-001 动作映射规则：
    - 未持仓 (no_position)：允许 Level 0-2（观察/禁止追高/条件入场），禁止 HOLD/REDUCE/EXIT
    - 已持仓 (has_position)：允许 Level 0-4（观察/禁止加仓/条件减仓/触发止损/立即清仓）
    - 持仓未知 (unknown)：保守策略，禁止强动作

    加仓条件：中线不差+短线转强+资金无持续流出+未跌破支撑+数据完整度>=75%
    """
    if position_status == "no_position":
        # 未持仓：禁止任何持仓操作
        if data_completeness < 50:
            return {
                "level": "low",
                "allowed": ["观察"],
                "forbidden": ["买入", "加仓", "追涨", "HOLD", "REDUCE", "EXIT", "减仓", "清仓", "止损"],
                "label": "低质量报告 · 未持仓",
                "message": "未持仓且数据完整度不足50%，仅允许观察。",
            }
        elif data_completeness < 75:
            return {
                "level": "medium",
                "allowed": ["观察", "条件试仓"],
                "forbidden": ["买入", "加仓", "追涨", "HOLD", "REDUCE", "EXIT", "减仓", "清仓", "止损", "确认建仓", "积极建仓"],
                "label": "中等质量报告 · 未持仓",
                "message": "未持仓，数据50-74%，仅允许观察和条件试仓，禁止买入/加仓/追涨。",
            }
        else:
            return {
                "level": "high",
                "allowed": ["观察", "条件试仓", "确认建仓"],
                "forbidden": ["加仓", "追涨", "HOLD", "REDUCE", "EXIT", "减仓", "清仓", "止损", "积极建仓"],
                "label": "高质量报告 · 未持仓",
                "message": "未持仓，数据完整，允许条件入场，禁止持仓操作(HOLD/减仓/清仓/止损)。",
            }
    elif position_status == "has_position":
        # 已持仓：允许持仓操作，加仓需高门槛
        if data_completeness < 50:
            return {
                "level": "low",
                "allowed": ["持有", "止损触发", "减仓触发"],
                "forbidden": ["买入", "加仓", "追涨"],
                "label": "低质量报告 · 已持仓",
                "message": "已持仓但数据完整度不足50%，禁止买入/加仓/追涨，允许持有和止损触发。",
            }
        elif data_completeness < 75:
            return {
                "level": "medium",
                "allowed": ["持有", "止损触发", "减仓触发", "观察"],
                "forbidden": ["加仓", "追涨"],
                "label": "中等质量报告 · 已持仓",
                "allowed_levels": [0, 1, 2],  # 观察/禁止加仓/条件减仓
                "message": "已持仓，数据50-74%，禁止主动加仓，允许持有/止损/减仓。",
            }
        else:
            return {
                "level": "high",
                "allowed": ["持有", "止损触发", "减仓触发", "加仓", "观察"],
                "forbidden": ["追涨"],
                "label": "高质量报告 · 已持仓",
                "allowed_levels": [0, 1, 2, 3, 4],  # 全部允许
                "message": "已持仓且数据完整，允许持有/止损/减仓/加仓。",
            }
    else:
        # 持仓未知：保守策略
        if data_completeness < 50:
            return {
                "level": "low",
                "allowed": ["观察"],
                "forbidden": ["买入", "加仓", "追涨", "HOLD", "REDUCE", "EXIT"],
                "label": "低质量报告 · 持仓未知",
                "message": "持仓状态未知且数据不足50%，禁止所有主动操作。",
            }
        elif data_completeness < 80:
            return {
                "level": "medium",
                "allowed": ["持有处理", "止损触发", "减仓触发", "观察"],
                "forbidden": ["加仓", "追涨"],
                "label": "中等质量报告 · 持仓未知",
                "message": "持仓状态未知，禁止主动加仓建议，允许持有和止损触发。",
            }
        else:
            return {
                "level": "high",
                "allowed": ["买入", "加仓", "持有处理", "止损触发", "减仓触发", "卖出", "观望"],
                "forbidden": [],
                "label": "高质量报告 · 持仓未知",
                "message": "数据完整，允许所有交易建议。",
            }


def get_position_status(
    user_context: dict,
    position_context: dict | None = None,
) -> str:
    """根据 user_context 判断持仓状态。

    返回:
        "has_position" / "no_position" / "unknown"
    """
    user_context = user_context or {}
    pos = user_context.get("current_position")
    if pos is None:
        position_pct = user_context.get("current_position_pct")
        if position_pct is not None:
            return "has_position" if position_pct > 0 else "no_position"
        if isinstance(position_context, dict) and isinstance(
            position_context.get("has_position"), bool
        ):
            if position_context["has_position"]:
                return "has_position"
            if position_context.get("position_status_explicit") is not False:
                return "no_position"
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

    [Fix-5] For no-position: level 0-1 should use '风险观察/禁止开仓' semantics.
    Level 0 = '风险观察' (not '观察') for no-position.

    返回:
        {"level": int, "note": str}
    """
    max_level = 3 if position_status == "unknown" else 4
    if name_mismatch:
        max_level = min(max_level, 3)

    # [Fix-5] No-position: cap at level 1 (禁止开仓)
    if position_status == "no_position":
        max_level = min(max_level, 1)
        base_note = "未持仓，Risk Level 上限为 1（禁止开仓）"
    else:
        base_note = ""

    level_4_ok = (
        source_coverage >= 85
        and evidence_coverage >= 85
        and broke_support
        and main_capital_outflow_days >= 2
        and volume_breakdown
        and not has_major_positive_announcement
    )
    if level_4_ok:
        return {"level": min(4, max_level), "note": base_note}

    note = base_note or ("Risk Level 4 条件证据不足" if max_level == 4 else "Risk Level 4 被降级")
    if name_mismatch and max_level <= 3:
        note = (base_note + "；" if base_note else "") + "名称校验失败，Risk Level 上限为 3"

    level_3_ok = broke_support and main_capital_outflow_days >= 1
    if level_3_ok:
        return {"level": min(3, max_level), "note": note}

    level_2_ok = main_capital_outflow_days >= 1 or broke_support
    if level_2_ok:
        return {"level": min(2, max_level), "note": note}

    return {"level": 0, "note": base_note}


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
    research_bearish: bool = False,
    event_risk_active: bool = False,  # [C-007-R1]
    event_risk_level: str = "none",  # [C-007-R1] "critical"/"high"/"medium"/"none"
    execution_price_available: bool = True,
) -> dict:
    """计算 Buy Level（买入/建仓侧等级 0-4）。

    [Fix-5] For no-position when entry conditions are NOT met:
    - Buy Level should be 0 (禁止买入) or 1 (观察)
    - '未来满足条件后可重新评估' should not elevate the current level
    - "条件试仓" (level 2) requires actual entry conditions to be met

    [P1-2] For no-position + research bearish (研究经理偏空/不建议入场):
    - Buy Level forced to 0 (禁止买入)

    [C-007-R1] 事件门禁真降级：
    - critical 风险事件 → Buy Level 强制为 0（禁止买入）
    - high 风险事件 → Buy Level 上限 1（仅观察，禁止新建仓）
    - medium 风险事件 → Buy Level 上限 2（条件试仓，降权但不阻断）

    返回:
        {"level": int, "note": str}
    """
    # [F-001-R1] A no-position signal is an entry trial, never an aggressive
    # add-position instruction. Even perfect entry evidence is capped at
    # Level 2; Level 3/4 remain available only when a position already exists.
    max_level = 2 if position_status in {"unknown", "no_position"} else 4
    if name_mismatch:
        max_level = min(max_level, 3)

    # [C-007-R1] 事件门禁真降级 — 在所有其它判定之前生效
    event_note = ""
    if event_risk_active:
        if event_risk_level == "critical":
            return {
                "level": 0,
                "note": "[C-007-R1] 检测到 critical 级事件风险，Buy Level 强制为 0（禁止买入）",
            }
        if event_risk_level == "high":
            max_level = min(max_level, 1)
            event_note = "事件风险 high，Buy Level 上限 1（仅观察）"
        elif event_risk_level == "medium":
            max_level = min(max_level, 2)
            event_note = "事件风险 medium，Buy Level 上限 2（条件试仓）"

    price_note = ""
    if not execution_price_available:
        max_level = min(max_level, 1)
        price_note = "可执行价格不可用，Buy Level 上限 1（仅观察）"

    # [P1-2] No-position + research bearish → force level 0
    if position_status == "no_position" and research_bearish:
        note_parts = ["未持仓且研究经理偏空/不建议入场，Buy Level 降为 0（禁止买入）"]
        if event_note:
            note_parts.append(event_note)
        return {"level": 0, "note": "；".join(note_parts)}

    # [Fix-5] No-position: cap at level 1 unless real entry conditions met
    if position_status == "no_position":
        # Only allow level 2 if ALL actual entry conditions are confirmed.
        real_entry_ready = (
            trend_confirmed
            and main_capital_inflow_days >= 1
            and no_execution_conflict
            and no_unresolved_analyst_conflict
            and not has_major_negative_announcement
        )
        if not real_entry_ready:
            max_level = min(max_level, 1)
            note_prefix = "未持仓且入场条件未满足，Buy Level 上限为 1（观察）"
        else:
            max_level = min(max_level, 2)
            note_prefix = "未持仓但入场条件已确认，Buy Level 上限为 2（条件试仓）"
    elif position_status == "unknown":
        note_prefix = "持仓状态未知，Buy Level 上限为 2"
    else:
        note_prefix = ""

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
    if not level_4_ok:
        note_suffix = "Buy Level 4 条件证据不足" if source_coverage >= 85 and evidence_coverage >= 85 else ""
    else:
        note_suffix = ""

    # [C-007-R1] 合并事件风险提示到 note
    _note_parts = [
        p for p in (note_prefix, note_suffix, event_note, price_note) if p
    ]
    note = "；".join(_note_parts)

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
    # [Fix-3] Cap parameters
    gate_passed: bool = True,
    final_direction_bearish: bool = False,
    trader_disallows_buy: bool = False,
    data_completeness: int = 100,
    position_status: str = "unknown",
) -> int:
    """计算 Opportunity Score（0-100）。

    维度：
    - 趋势确认度 (0-25)
    - 资金共振度 (0-20)
    - 催化强度 (0-20)
    - 盈亏比/风险收益比 (0-20)
    - 入场质量 (0-15)
    - 事件风险降权

    [Fix-3] Cap rules (multiple conditions → take the lowest cap):
    - Strong Action Gate 未通过: cap 60
    - 最终方向偏空/观望: cap 60
    - 交易员明确不允许买入: cap 50
    - 数据完整度 <80%: cap 70
    - 持仓未知: cap 70

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

    # [Fix-3] Apply caps
    caps = []
    if not gate_passed:
        caps.append(60)
    if final_direction_bearish:
        caps.append(60)
    if trader_disallows_buy:
        caps.append(50)
    if data_completeness < 80:
        caps.append(70)
    if position_status == "unknown":
        caps.append(70)

    if caps:
        upper = min(caps)
        score = min(score, upper)

    return min(100, max(0, score))


def _sanitize_short_selling_text(text: str) -> tuple:
    """[Fix-9] Replace A-share short-selling language with appropriate alternatives.

    When can_short=false (A-share default), replace:
    - 做空策略 → 看空信号
    - 空头开仓 → 离场信号
    - 若做空 → 若确认偏空
    - etc.

    [C-003-R1] English patterns are matched case-insensitively so that
    uppercase LLM action tokens like "OPEN SHORT" / "Short Selling" are
    caught.

    [C-003-R1] Legitimate long-side exit vocabulary (卖出/减仓/止损/SELL/EXIT)
    is explicitly protected by ``_LEGIT_SELL_EXIT_KEYWORDS`` — even if a future
    pattern is overly broad, ``_is_legit_sell_exit_phrase`` ensures these are
    never stripped.

    Returns (sanitized_text, list_of_changes)
    """
    changes = []
    result = text

    # [C-003-R1] Pure long-side exits need no rewriting. Mixed text must still
    # pass through the short-selling patterns so a valid "减仓" cannot mask
    # a later "空头加仓" instruction in the same response.
    has_short_pattern = any(
        re.search(pattern, text)
        for pattern, _ in _SHORT_SELLING_PATTERNS_CN
    ) or any(
        re.search(pattern, text, re.IGNORECASE)
        for pattern, _ in _SHORT_SELLING_PATTERNS_EN
    )
    if _is_legit_sell_exit_phrase(text.strip()) and not has_short_pattern:
        return result, changes

    # [C-003-R1] Chinese patterns: literal match.
    for pattern, replacement in _SHORT_SELLING_PATTERNS_CN:
        matches = list(re.finditer(pattern, result))
        if matches:
            changes.append(f'"{matches[0].group(0)}" → "{replacement}"')
            result = re.sub(pattern, replacement, result)
    # [C-003-R1] English patterns: case-insensitive match.
    for pattern, replacement in _SHORT_SELLING_PATTERNS_EN:
        matches = list(re.finditer(pattern, result, re.IGNORECASE))
        if matches:
            changes.append(f'"{matches[0].group(0)}" → "{replacement}"')
            result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    return result, changes


def filter_short_strategy(text: str, *, can_short: bool = False) -> dict:
    """[C-003] Filter short-selling strategies from output text.

    When can_short=False, replaces all short-selling language with safe
    alternatives. Returns a dict with the filtered text and metadata.

    Args:
        text: The output text to filter.
        can_short: Whether short selling is allowed. Default False (A-share).

    Returns:
        {"text": str, "filtered": bool, "changes": list[str]}
    """
    if can_short or not text:
        return {"text": text, "filtered": False, "changes": []}
    sanitized, changes = _sanitize_short_selling_text(text)
    return {"text": sanitized, "filtered": bool(changes), "changes": changes}


# ── [C-003-R1] streaming short-selling filter ──────────────────────────────
# The audit (round 1) found that when can_short=false the manager/risk nodes
# streamed each raw LLM token via emit_debate_token / _emit_token *before* the
# post-processing filter ran, so short-selling language leaked to the SSE /
# frontend. The class below does true server-side blocking at generation time:
# it buffers a small tail of every chunk so that short-selling keywords
# (including those split across chunk boundaries) are sanitized before any
# token is emitted, while legitimate long-side exits (卖出/减仓/止损/EXIT/SELL)
# pass through untouched.

# Keep a raw trailing window large enough for the supported cross-chunk
# phrases and ordinary surrounding whitespace.
_SHORT_STREAM_HOLD_LEN = 64
_SHORT_STREAM_ENGLISH_TARGETS = (
    "shortposition",
    "shortsel",
    "shortsell",
    "shortselling",
    "openshort",
    "openashort",
)


def _pending_english_short_start(text: str, release_to: int) -> int:
    """Move a raw release boundary before an incomplete English short phrase.

    The non-streaming regexes intentionally accept unbounded whitespace. A
    fixed tail alone therefore cannot protect ``open`` + many spaces +
    ``short``. Compacting whitespace lets us recognize any suffix that is
    still a prefix of a supported phrase and retain it until completion or
    disqualification.
    """
    lowered = text.lower()
    for start in range(len(lowered)):
        if lowered[start] not in ("s", "o"):
            continue
        compact = re.sub(r"\s+", "", lowered[start:])
        if compact and any(target.startswith(compact) for target in _SHORT_STREAM_ENGLISH_TARGETS):
            release_to = min(release_to, start)
    return release_to


class ShortSellingStreamFilter:
    """[C-003-R1] Stateful, cross-chunk short-selling sanitizer.

    Wrap an LLM token stream so that no raw short-selling keyword ever reaches
    the SSE / frontend. Each ``feed(chunk)`` returns a *safe-to-emit* string
    (already sanitized). A small tail is held back internally so a keyword
    split across chunks (e.g. "做" + "空策略") is caught before emission.

    Algorithm: retain a raw trailing window and consume only a prefix that
    cannot contain a keyword completed by a later chunk. The raw prefix is
    sanitized exactly once before emission. This is important because a
    replacement may be shorter than its source phrase; offsets into repeatedly
    sanitized text can otherwise rewind, duplicate, or drop output.

    When ``can_short`` is True the filter is a passthrough (no filtering).

    Legitimate long-side exit vocabulary (卖出/减仓/止损/止盈/清仓/EXIT/SELL/
    REDUCE) is never stripped — only true short-selling language is replaced.
    """

    def __init__(self, *, can_short: bool = False):
        self.can_short = bool(can_short)
        self._buf = ""
        self.changes: list[str] = []

    def feed(self, chunk: str) -> str:
        """Append a streaming chunk and return text safe to emit now.

        Returns "" when the only safe output is nothing (e.g. the buffered tail
        is still ambiguous and must wait for the next chunk / finalize()).
        """
        if self.can_short or chunk is None:
            return chunk or ""
        if not isinstance(chunk, str):
            chunk = str(chunk)

        self._buf += chunk

        release_to = max(0, len(self._buf) - _SHORT_STREAM_HOLD_LEN)
        if release_to == 0:
            return ""

        # If the tentative cut crosses a complete match, retain that entire
        # match. It will be sanitized on the next feed or during finalize().
        for pattern, _replacement in _SHORT_SELLING_PATTERNS_CN:
            for match in re.finditer(pattern, self._buf):
                if match.start() < release_to < match.end():
                    release_to = match.start()
        for pattern, _replacement in _SHORT_SELLING_PATTERNS_EN:
            for match in re.finditer(pattern, self._buf, re.IGNORECASE):
                if match.start() < release_to < match.end():
                    release_to = match.start()
        release_to = _pending_english_short_start(self._buf, release_to)

        raw_prefix = self._buf[:release_to]
        self._buf = self._buf[release_to:]
        sanitized, ch = _sanitize_short_selling_text(raw_prefix)
        for change in ch:
            if change not in self.changes:
                self.changes.append(change)
        return sanitized

    def finalize(self) -> str:
        """Flush any buffered tail. Call once when the stream ends."""
        if self.can_short:
            flushed = self._buf
            self._buf = ""
            return flushed
        sanitized, ch = _sanitize_short_selling_text(self._buf)
        if ch:
            for c in ch:
                if c not in self.changes:
                    self.changes.append(c)
        self._buf = ""
        return sanitized


def check_valuation_mismatch(
    current_price: float | None,
    report_text: str,
) -> dict:
    """[Fix-2] Check if valuation assumptions in fundamentals match the current price.

    If the price referenced in valuation paragraphs differs from the report's
    current price by >20%, the valuation is flagged as unreliable.

    Returns:
        {"mismatch": bool, "current_price": float|None, "valuation_price": float|None,
         "deviation_pct": float|None, "note": str}
    """
    if current_price is None:
        return {
            "mismatch": False,
            "current_price": None,
            "valuation_price": None,
            "deviation_pct": None,
            "price_unavailable": True,
            "note": (
                "⚠️ [G-008] 当前可执行价格不可用或已过期，"
                "无法核验任何估值或交易价位；强动作必须降级。"
            ),
        }

    if not report_text:
        return {"mismatch": False, "current_price": current_price,
                "valuation_price": None, "deviation_pct": None, "note": "",
                "price_unavailable": False}

    # Extract valuation paragraph prices: "假设约X元" / "PE~Y倍" / "假设股价X元"
    val_price = None
    val_patterns = [
        r'假设[约]?(?:股价|价格)?[为约]?\s*(\d+(?:\.\d+)?)\s*元',
        r'假设[约]\s*(\d+(?:\.\d+)?)\s*元',
        r'以[约]?\s*(\d+(?:\.\d+)?)\s*元',
        r'(?:估值|估值段).{0,30}?(\d+(?:\.\d+)?)\s*元',
    ]
    for p in val_patterns:
        m = re.search(p, report_text)
        if m:
            try:
                val_price = float(m.group(1))
                break
            except ValueError:
                pass

    if val_price is None:
        return {"mismatch": False, "current_price": current_price,
                "valuation_price": None, "deviation_pct": None, "note": "",
                "price_unavailable": False}

    deviation = abs(current_price - val_price) / current_price * 100
    mismatch = deviation > 20
    note = ""
    if mismatch:
        note = (f"⚠️ [Fix-2] 估值口径错配：报告当前价 {current_price:.2f}元，"
                f"估值段引用价格 {val_price:.2f}元，偏差 {deviation:.1f}%。"
                f"估值推导不可作为结论证据，基本面估值维度权重已降低。")

    return {
        "mismatch": mismatch,
        "current_price": current_price,
        "valuation_price": val_price,
        "deviation_pct": round(deviation, 1),
        "note": note,
        "price_unavailable": False,
    }


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
    # [Fix-2] valuation mismatch flag
    valuation_mismatch: bool = False,
    # G-001: three-layer decision context
    analysis_intent: str = "watch",
    position_context: dict = None,
    horizon: str = "short",
    # G-001: optional dual-horizon conflict signals
    short_bullish: bool | None = None,
    medium_bullish: bool | None = None,
    fundamental_integrity_valid: bool = True,
) -> str:
    """格式化报告末尾的「执行等级与证据门禁」结构化区块。

    G-001: 新增三层决策区（中线逻辑层 / 短线时点层 / 持仓执行层）
    和当前交易目的区块。
    """
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
    ]
    # [Fix-2] Append valuation warning if mismatch detected
    if valuation_mismatch:
        lines.append("- ⚠️ 估值口径需人工复核/估值数据错配")
    lines.extend([
        f"- Opportunity Score：{opportunity_score}/100（{opp_label}）",
    ])

    # [F-001-fix] For unknown/no-position: clarify current action vs conditional candidate
    if position_status in ("no_position", "unknown"):
        lines.append(
            f"- Buy Level：当前动作 WAIT/观察 | 条件候选：Buy Level {buy_level}（{_BUY_LABELS.get(buy_level, '未知')}）"
            + (f" — {buy_level_note}" if buy_level_note else "")
        )
    else:
        lines.append(
            f"- Buy Level：Buy Level {buy_level}（{_BUY_LABELS.get(buy_level, '未知')}）"
            + (f" — {buy_level_note}" if buy_level_note else "")
        )

    if position_status in ("no_position", "unknown"):
        lines.append(
            f"- Risk Level：未来持仓后止损条件 | Risk Level {risk_level}（{_RISK_LABELS.get(risk_level, '未知')}）"
            + (f" — {risk_level_note}" if risk_level_note else "")
        )
    else:
        lines.append(
            f"- Risk Level：Risk Level {risk_level}（{_RISK_LABELS.get(risk_level, '未知')}）"
            + (f" — {risk_level_note}" if risk_level_note else "")
        )

    lines.append(f"- Strong Action Gate：{'通过' if gate['passed'] else '未通过'}")
    if gate["failures"]:
        lines.append("- 降级原因：")
        for f in gate["failures"]:
            lines.append(f"  - {f}")

    pos_notes = {
        "unknown": "⚠️ 持仓状态未知，Buy Level 上限为 2，Risk Level 上限为 3",
        "no_position": "未持仓，主输出为 Buy Level（观察/禁止买入），持仓相关动作仅作假设性参考",
        "has_position": "主输出为 Risk Level，辅助输出加仓/补仓评估",
    }
    lines.append(f"- {pos_notes.get(position_status, '')}")

    # ── G-001: 三层决策区 ──
    # 当前交易目的区块
    intent_labels = {
        "watch": "观察",
        "entry": "寻找入场机会",
        "holding": "持仓处理",
        "add": "评估加仓条件",
        "reduce": "评估减仓条件",
        "stop_loss": "止损/清仓评估",
    }
    intent_label = intent_labels.get(analysis_intent, analysis_intent)

    pos_ctx = position_context or {}
    has_pos = pos_ctx.get("has_position", False) or position_status == "has_position"
    pos_display = "已持仓" if has_pos else "未持仓"

    # 禁止动作
    forbidden_actions = []
    if not has_pos:
        forbidden_actions = ["HOLD", "REDUCE", "EXIT", "减仓", "清仓", "止损"]
    else:
        if analysis_intent == "watch":
            forbidden_actions = []

    lines.append("")
    lines.append("### 三层决策")
    lines.append(f"- 状态：{pos_display}")
    lines.append(f"- 交易目的：{intent_label}")
    horizon_labels = {"short": "短线", "medium": "中线", "long": "长线"}
    lines.append(f"- 周期：{horizon_labels.get(horizon, horizon)}")
    if forbidden_actions:
        lines.append(f"- 禁止动作：{', '.join(forbidden_actions)}")

    # G-001: 短中线冲突裁决（仅在双周期信号均可用时执行）
    if short_bullish is not None and medium_bullish is not None:
        conflict = resolve_horizon_conflict(
            medium_bullish=medium_bullish,
            short_bullish=short_bullish,
            has_position=has_pos,
        )
        if conflict["conflict_type"] != "none":
            lines.append(f"- ⚠️ 短中线冲突：{conflict['note']}")
            lines.append(f"- 冲突裁决：{conflict['action']}")
    elif horizon == "short" and medium_bullish is None:
        # 单短线周期，不做短中线冲突裁决
        pass
    elif horizon == "medium" and short_bullish is None:
        # 单中线周期，不做短中线冲突裁决
        pass

    # 中线逻辑层
    lines.append("")
    lines.append("#### 中线逻辑层")
    if not fundamental_integrity_valid:
        lines.append("- ⚠️ 基本面语义门禁未通过，中线判断仅供人工复核")
    elif evidence_coverage >= 70:
        lines.append("- 数据支撑充分，中线判断可信")
    else:
        lines.append("- ⚠️ 数据完整度不足，中线判断仅供参考")

    # 短线时点层
    lines.append("")
    lines.append("#### 短线时点层")
    lines.append(f"- Opportunity Score: {opportunity_score}/100")
    lines.append(f"- Buy Level: {buy_level}（{_BUY_LABELS.get(buy_level, '未知')}）")

    # 持仓执行层
    lines.append("")
    lines.append("#### 持仓执行层")
    lines.append(f"- Risk Level: {risk_level}（{_RISK_LABELS.get(risk_level, '未知')}）")
    if has_pos and pos_ctx.get("avg_cost") is not None:
        lines.append(f"- 持仓成本: {pos_ctx['avg_cost']}")

    return "\n".join(lines)


def format_readiness_score(
    score: dict,
    position_status: str = "unknown",
    entry_gate_blocked: bool = False,
    buy_level: int | None = None,
    risk_level: int | None = None,
) -> str:
    """格式化 readiness score 为报告文本"""
    action_completeness = score["data_completeness"]
    if score["confidence"] == ConfidenceLevel.MEDIUM.value:
        action_completeness = min(action_completeness, 74)
    elif score["confidence"] == ConfidenceLevel.LOW.value:
        action_completeness = min(action_completeness, 49)
    actions = get_allowed_actions(action_completeness, position_status)
    if buy_level is not None:
        entry_actions = ["买入", "加仓", "追涨", "条件试仓", "确认建仓", "积极建仓"]
        if position_status == "no_position":
            allowed = ["观察"]
            if buy_level >= 2:
                allowed.append("条件试仓")
            actions = {
                **actions,
                "allowed": allowed,
                "forbidden": list(
                    dict.fromkeys(
                        actions["forbidden"]
                        + [item for item in entry_actions if item not in allowed]
                    )
                ),
                "message": (
                    "未持仓，当前动作权限以最终 Buy Level 为准；"
                    "禁止持仓动作和超等级入场动作。"
                ),
            }
        elif position_status == "unknown":
            allowed = [
                item for item in actions["allowed"] if item not in entry_actions
            ]
            if buy_level >= 2:
                allowed.append("条件试仓")
            actions = {
                **actions,
                "allowed": list(dict.fromkeys(allowed)),
                "forbidden": list(
                    dict.fromkeys(
                        actions["forbidden"]
                        + [item for item in entry_actions if item not in allowed]
                    )
                ),
                "message": (
                    "持仓状态未知，入场权限以最终 Buy Level 为准；"
                    "Buy Level 2 仅允许条件试仓，不授权直接买入或加仓。"
                ),
            }
        elif buy_level < 3:
            actions = {
                **actions,
                "allowed": [
                    item for item in actions["allowed"] if item not in entry_actions
                ],
                "forbidden": list(
                    dict.fromkeys(actions["forbidden"] + entry_actions)
                ),
                "message": (
                    "当前 Buy Level 尚未达到确认入场等级，不支持新增或加仓；"
                    "持仓风控动作仍按 Risk Level 执行。"
                ),
            }
    risk_entry_blocked = False
    if risk_level is not None:
        if position_status == "no_position" and risk_level >= 1:
            risk_entry_blocked = True
        elif position_status == "unknown" and risk_level >= 1:
            risk_entry_blocked = True
        elif position_status == "has_position" and risk_level >= 2:
            risk_entry_blocked = True
    if entry_gate_blocked or risk_entry_blocked:
        entry_actions = ["买入", "加仓", "追涨", "条件试仓", "确认建仓", "积极建仓"]
        if position_status == "no_position":
            allowed = ["观察"]
            forbidden = entry_actions + [
                "HOLD", "REDUCE", "EXIT", "减仓", "清仓", "止损",
            ]
            message = (
                "未持仓且入场门禁未通过，仅允许观察。"
                if entry_gate_blocked
                else "未持仓且 Risk Level 限制开仓，仅允许观察。"
            )
        else:
            allowed = [item for item in actions["allowed"] if item not in entry_actions]
            forbidden = list(dict.fromkeys(actions["forbidden"] + entry_actions))
            message = (
                "入场门禁未通过，禁止新增或加仓；持仓风控动作仍按 Risk Level 执行。"
                if entry_gate_blocked
                else "Risk Level 限制新增或加仓；持仓风控动作仍按 Risk Level 执行。"
            )
        actions = {
            **actions,
            "allowed": allowed,
            "forbidden": forbidden,
            "label": (
                f"{actions['label']} · 入场门禁未通过"
                if entry_gate_blocked
                else f"{actions['label']} · 风险等级限制开仓"
            ),
            "message": message,
        }
    result = (
        f"\n\n📋 [C-008] 执行就绪度评分\n"
        f"- 数据完整度：{score['data_completeness']}%\n"
        f"- 置信度：{score['confidence']}\n"
        f"- 报告等级：{actions['label']}\n"
        f"- 允许动作：{', '.join(actions['allowed'])}\n"
        f"- 禁止动作：{', '.join(actions['forbidden']) if actions['forbidden'] else '无'}\n"
        f"- 总结：{score['summary']}"
    )
    blockers = score.get("blockers") or []
    if blockers:
        result += "\n- 置信度降级原因：" + "、".join(blockers)
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
        f"- 评分版本: E-001~E-005 + E系列修复\n"
        f"- 后端启动时间: {start_time}\n"
        f"- 报告生成时间: {report_time}"
    )


_SANITIZE_STRONG_SELL = [
    # Machine-readable action labels occasionally survive into the rendered
    # report. They are executable only in an explicit label context, so enum
    # and documentation references elsewhere remain intact.
    (
        r'(?i)(?:(?:final|current)\s+action|recommendation|decision)\s*[:：]\s*'
        r'(?:EXIT|SELL|REDUCE)\b',
        '等待触发条件，暂不执行强清仓',
    ),
    (
        r'(?P<label>(?:(?:最终|当前)(?:动作|建议|决策|操作)|交易建议|执行动作)\s*[:：]\s*)'
        r'(?:'
        r'(?i:EXIT|SELL|REDUCE)\b|'
        r'(?:(?:立即|立刻|马上|务必|必须|果断|无条件|直接)?\s*'
        r'(?:全部|全数|悉数)?\s*'
        r'(?:清仓|平仓|卖出(?:离场)?|止损离场|减仓|'
        r'止损(?!价|位|条件|线|红线|失效|风险|机制|纪律|参考)|退出|割肉|斩仓|抛售))'
        r')',
        r'\g<label>等待触发条件，暂不执行强清仓',
    ),
    (
        r'(?<!不)(?<!非)(?<!未)'
        r'(?:(?:最终|当前)?(?:建议|推荐|应当|应该|需要|需|计划|决定)\s*)?'
        r'(?:立即|立刻|马上|务必|必须|果断|无条件|直接|现在|当前|今日|今天|下一步)\s*'
        r'(?:(?:就|即)\s*)?'
        r'(?:全部|全数|悉数)?\s*'
        r'(?:清仓|平仓|卖出(?:离场)?|止损离场|减仓|'
        r'止损(?!价|位|条件|线|红线|失效|风险|机制|纪律|参考)|退出|割肉|斩仓|抛售)',
        '等待触发条件，暂不执行强清仓',
    ),
    (
        r'(?<!不)(?<!非)(?<!未)'
        r'(?:(?:最终|当前)?(?:建议|推荐|应当|应该|需要|需|计划|决定)\s*)'
        r'(?:全部|全数|悉数)?\s*'
        r'(?:清仓|平仓|全部卖出(?:离场)?|止损离场|减仓|'
        r'止损(?!价|位|条件|线|红线|失效|风险|机制|纪律|参考)|退出|割肉|斩仓|抛售)',
        '等待触发条件，暂不执行强清仓',
    ),
    (
        r'(?:全部|全数|悉数)\s*(?:清仓|平仓|卖出(?:离场)?|退出|抛售)',
        '等待触发条件，暂不执行强清仓',
    ),
    (r'立即清仓', '等待触发条件，暂不执行强清仓'),
    (r'立刻清仓', '等待触发条件，暂不执行强清仓'),
    (r'强制清仓', '等待触发条件，暂不执行强清仓'),
    (r'清仓离场', '等待触发条件，暂不执行强清仓'),
    (r'清仓出局', '等待触发条件，暂不执行强清仓'),
    (r'全部卖出离场', '等待触发条件，暂不执行强清仓'),
]
_STRONG_TRIAL_ACTION_PATTERN = (
    r'(?:重仓|满仓|大仓位|强烈|强力|无条件|果断|立即|立刻|直接|积极)\s*'
    r'(?:(?:建议|推荐|可以|可|考虑|计划|准备|执行|进行|采取|开展)\s*)?'
    r'(?:条件试(?:探)?仓|轻仓试错|小仓位试错|试探性轻仓(?:策略)?|'
    r'轻仓试多|小仓位试多|试仓|试多)'
)
_SIZED_TRIAL_ACTION_PATTERN = (
    r'(?:(?:建议|推荐|可以|可|考虑|计划|准备|执行|进行|采取)\s*)?'
    r'(?:(?:以|用|按|投入)\s*)?'
    r'(?:重仓|满仓|全仓|半仓|大仓位|全部仓位|'
    r'[0-9０-９]+(?:[\.．][0-9０-９]+)?\s*[%％]\s*(?:的)?仓位|'
    r'[一二两三四五六七八九十]+成(?:仓位)?)\s*'
    r'(?:(?:方式|比例)\s*)?'
    r'(?:(?:[，,；;]\s*)(?:随后|然后|再)?\s*)?'
    r'(?:(?:执行|进行|采取|开展|分批|逐步|做)\s*)*'
    r'(?:条件试(?:探)?仓|轻仓试错|小仓位试错|试探性轻仓(?:策略)?|'
    r'轻仓试多|小仓位试多|试仓|试多)'
)
_TRAILING_SIZED_TRIAL_ACTION_PATTERN = (
    r'(?:条件试(?:探)?仓|轻仓试错|小仓位试错|试探性轻仓(?:策略)?|'
    r'轻仓试多|小仓位试多|试仓|试多)\s*'
    r'(?:'
    r'[0-9０-９]+(?:[\.．][0-9０-９]+)?\s*[%％]\s*(?:的)?仓位|'
    r'[一二两三四五六七八九十]+成(?:仓位)?|'
    r'(?:[，,；;]\s*)?仓位\s*(?:为|约|至|到)?\s*'
    r'(?:[0-9０-９]+(?:[\.．][0-9０-９]+)?\s*[%％]|'
    r'[一二两三四五六七八九十]+成(?:仓位)?|'
    r'重仓|满仓|全仓|半仓|大仓位|全部仓位)|'
    r'(?:[，,；;]\s*)?(?:随后|然后|再)?\s*'
    r'(?:投入|配置|增配(?:至|到)?|加码(?:至|到)?)\s*'
    r'(?:[0-9０-９]+(?:[\.．][0-9０-９]+)?\s*[%％]\s*(?:的)?仓位|'
    r'[一二两三四五六七八九十]+成(?:仓位)?|'
    r'重仓|满仓|全仓|半仓|大仓位|全部仓位)|'
    r'(?:[，,；;]\s*)?(?:随后|然后|再)\s*'
    r'(?:重仓|满仓|全仓|半仓|大仓位|全部仓位)'
    r'(?!布局|建仓|买入|入场|加仓|补仓|增持)'
    r')'
)

_TARGET_SIZED_TRIAL_ACTION_PATTERN = (
    r'(?:目标|最终|初始)?\s*仓位\s*(?:为|约|至|到|[:：])?\s*'
    r'(?:[0-9０-９]+(?:[\.．][0-9０-９]+)?\s*[%％]|'
    r'[一二两三四五六七八九十]+成(?:仓位)?|重仓|满仓|全仓|半仓|全部仓位)'
    r'\s*(?:[，,]\s*)?(?:执行|进行|采取|开展|先)?\s*'
    r'(?:条件试(?:探)?仓|轻仓试错|小仓位试错|试探性轻仓(?:策略)?|'
    r'轻仓试多|小仓位试多|试仓|试多)'
    r'|(?:满仓|全仓|半仓|全部仓位)\s*(?:作为|为|是)?\s*'
    r'[^。！？；;\n]{0,16}?(?:目标|仓位)\s*(?:[，,]\s*)?'
    r'(?:执行|进行|采取|开展|先)?\s*'
    r'(?:条件试(?:探)?仓|轻仓试错|小仓位试错|试探性轻仓(?:策略)?|'
    r'轻仓试多|小仓位试多|试仓|试多)'
    r'|(?:条件试(?:探)?仓|轻仓试错|小仓位试错|试探性轻仓(?:策略)?|'
    r'轻仓试多|小仓位试多|试仓|试多)\s*(?:[，,]\s*)?'
    r'(?:后续|最终)\s*(?:增至|增加至|配置(?:为|至|到)?)\s*'
    r'(?:重仓|满仓|全仓|半仓|全部仓位|'
    r'[0-9０-９]+(?:[\.．][0-9０-９]+)?\s*[%％])'
)


_SANITIZE_STRONG_BUY = [
    (
        r'(?:提交|创建|下达|执行|发出|挂出)\s*'
        r'(?:一笔|新的)?\s*买入订单'
        r'(?:\s*(?:数量|金额|规模)?\s*\d+(?:\.\d+)?\s*(?:股|手|元|万元|亿元)?)?',
        '等待条件确认，暂不提交订单',
    ),
    (
        r'(?:最终建议|系统指令|当前操作|下一步)\s*[:：]\s*'
        r'(?:提交|创建|下达|执行|发出|挂出)?\s*买入订单'
        r'(?:\s*(?:数量|金额|规模)?\s*\d+(?:\.\d+)?\s*(?:股|手|元|万元|亿元)?)?',
        '等待条件确认，暂不提交订单',
    ),
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
    (_STRONG_TRIAL_ACTION_PATTERN, '暂不执行强买入，等待条件确认'),
]
_SANITIZE_EVENT_RISK_BUY = [
    # C-007 event-risk gate forbids opening/adding actions, including ordinary
    # (not only "strong") buy wording. Keep this list aligned with
    # event_risk_gate._RISK_FIRST_FORBIDDEN_ACTIONS.
    (r'重仓买入', '等待风险解除'),
    (r'建议加仓', '等待风险解除'),
    (r'加仓买入', '等待风险解除'),
    (r'追涨买入', '等待风险解除'),
    (r'建议建仓', '等待风险解除'),
    (r'建议买入', '等待风险解除'),
    (r'建议入场', '等待风险解除'),
    (r'积极建仓', '等待风险解除'),
    (r'确认建仓', '等待风险解除'),
    (r'(?:建议|可以|考虑|执行|开始|建立|进行)(?:条件)?试(?:探)?仓', '等待风险解除'),
    (r'条件试(?:探)?仓', '等待风险解除'),
    (r'试探性轻仓(?:策略)?', '等待风险解除'),
    (r'(?:轻仓|小仓位)试(?:多|错)', '等待风险解除'),
    (r'(?i)\bSTRONG_BUY\b', 'WAIT'),
    # Known classification enum references are masked before these patterns
    # run.  Slash-delimited text elsewhere may still be an executable choice
    # (for example ``最终交易建议：BUY/HOLD``), so it must fail closed.
    (r'(?i)\bBUY\b', 'WAIT'),
    (r'(?i)\bENTER\b', 'WAIT'),
]
_ACTION_SIZE_VALUE = (
    r"(?:(?:约为|大约|大概|约|近)?\s*"
    r"(?:[\d.]+|[零〇一二两三四五六七八九十百千万半]+)"
    r"\s*(?:%|万元|元|成|仓)?|全仓|满仓|半仓|小仓位|轻仓|重仓)"
)
_ACTION_LABEL_CONTEXT = (
    r'(?:\*{0,2})'
    r'(?:系统指令|当前操作|操作|动作|我的动作|下一步|交易方向|方向|结论|意见|最终意见|交易意见|处理意见|策略|执行|建议|决策类型|最终交易建议|'
    r'最终交易决策|最终决策|交易决策|最终结论|决策|我的意见|我们的意见|我的选择|我的决定|投资结论|'
    r'风控结论|风控意见|交易计划|仓位计划|执行方案|交易员动作|最终结果)'
    r'(?:如下)?'
    r'(?:(?:\*{0,2})\s*[:：]|[:：]\s*(?:\*{0,2})|'
    r'(?:\*{0,2})\s*(?:定为|确定为|选择|选|为|是))\s*(?:\*{0,2})'
)
_ACTION_LINE_CONTEXT = (
    rf'(?:(?:{_ACTION_LABEL_CONTEXT})|'
    r'^\s*(?:(?:#{1,6}\s+)|(?:>\s*)|(?:[-*]\s*)|(?:\d+[.)、]\s*)|'
    r'(?:[（(]\d+[）)]\s*))?(?:\*{0,2}))'
)
_ACTION_LEADING_DIRECTIVES = (
    r'(?:(?:我|本人|我们|本基金|本账户|用户|投资者|客户|持币者|大家|'
    r'系统|交易员|本策略|策略|模型|风控|投资委员会)(?:的)?\s*)?'
    r'(?:(?:当前|现在|目前)\s*)?'
    r'(?:(?:认为|觉得|判断(?:是|为)?|(?:个人)?观点(?:是|为)?|'
    r'结论(?:是|为)?|考虑后)\s*)?'
    r'(?:(?:建议|推荐|应|应当|应该|需要|需|务必|必须|请|坚决|'
    r'无条件|果断|可|可以|允许|计划|准备|打算|拟|将|要|宜|适宜|主张|'
    r'继续|维持|倾向于|倾向|适合|更适合|后续宜|后续可(?:以)?|会)\s*)?'
    r'(?:(?:供\s*)?(?:用户|投资者|客户|持币者|大家)\s*)?'
)

_CURRENT_ACTION_LABEL = (
    r'(?:最终|当前)(?:交易)?(?:建议|结论|动作|意见|决策|裁决)'
    r'|操作建议|系统指令|风控(?:结论|裁决)|交易员(?:动作|结论|建议)'
    r'|(?:现在|目前|当前)(?:建议|应|应该|需|需要|决定|计划|准备|打算|将|会|立刻|立即)'
)

_EXPLICIT_CURRENT_EXIT_DIRECTIVE = re.compile(
    rf'(?:{_CURRENT_ACTION_LABEL})'
    r'|(?:(?:用户|投资者|客户|本人|我|我们)\s*)?'
    r'(?:最终|最后|当前|现在|目前|今日|今天|明日|明天|下一步|只|仅|转为|改为)\s*'
    r'(?:(?:建议|推荐|应|应该|需|需要|决定|计划|准备|打算|将|会|立刻|立即)\s*)?'
    r'(?=(?:减仓|清仓|止盈|卖出|止损))'
    r'|由[^。！？；;，,\n]{0,16}转为\s*'
    r'(?=(?:减仓|清仓|止盈|卖出|止损))'
    r'|(?:结论|意见|决策|执行方案|交易计划|下一步|操作|动作|建议)'
    r'(?=\s*[:：]\s*'
    r'(?:(?:建议|推荐|应|应该|需|需要|决定|计划|准备|打算|将|会|立刻|立即)\s*)?'
    r'(?:减仓|清仓|止盈|卖出|止损))'
    r'|(?:现在|目前|当前)操作(?=\s*[:：]?\s*'
    r'(?:(?:建议|推荐|应|应该|需|需要|决定|计划|准备|打算|将|会|立刻|立即)\s*)?'
    r'(?:减仓|清仓|止盈|卖出|止损))'
)

_HYPOTHETICAL_HOLDING_PLAN = re.compile(
    r'(?:(?:若|如果|假如|假设)[^。！？；;\n]{0,40})?'
    r'(?:未来持仓|未来(?:买入|建仓|入场|开仓)(?:后)?|'
    r'以后(?:买入|建仓|入场|开仓)(?:后)?|持仓后|'
    r'(?:买入|建仓|入场|开仓)后(?=\s*(?:计划|准备|打算|拟|将|'
    r'若|如果|假如|一旦|跌破|触及|达到|反弹|风险))|'
    r'(?:买入|建仓|入场|开仓)成功(?:的情况下)?)'
    rf'(?:(?!(?:{_CURRENT_ACTION_LABEL}))[^。！？；;\n]){{0,40}}'
    r'(?:(?:计划|准备|打算|拟|将)\s*)?'
    rf'(?:(?!(?:{_CURRENT_ACTION_LABEL}))[^。！？；;\n]){{0,24}}?'
    r'(?:减仓|清仓|止盈|卖出|止损)'
    r'(?!上限|条件|额度|限制|规则|禁令|线|位|价|比例|阈值)'
)

_CONDITIONAL_MODAL_HOLDING_PLAN = re.compile(
    r'(?:若|如果|假如|假设)[^。！？；;\n]{0,40}'
    r'(?:买入|建仓|入场|开仓)后\s*'
    r'(?:(?:建议|推荐|应|应该|应当|需|需要|考虑|计划|准备|打算|拟|将)\s*)?'
    # A contrast/current-action marker ends the hypothetical scope. It must
    # remain visible to the no-position sanitizer instead of being restored as
    # part of a future holding plan.
    r'(?:(?!(?:(?:但(?:是)?|不过|然而|可是|却|同时|并且)\s*)?'
    r'(?:当前|现在|目前|今日|今天|明日|明天|最终|下一步)'
    r'\s*(?:建议|推荐|应|应该|应当|需|需要|决定|计划|准备|打算|立即|立刻)?)'
    r'[^。！？；;\n]){0,24}?'
    r'(?:减仓|清仓|止盈|卖出|止损)'
    r'(?!上限|条件|额度|限制|规则|禁令|线|位|价|比例|阈值)'
)

_NON_EXECUTABLE_ENTRY_DISCUSSION = re.compile(
    r'(?:'
    r'(?:不确定|需判断|需要判断|讨论|研究问题|尚不能确定)'
    r'[^。！？；;\n]{0,48}?'
    r'(?:'
    r'(?:是否|能否|可否|何时)[^。！？；;\n]{0,12}?'
    r'(?:买入|建仓|入场|加仓|开仓|增持|做多|配置(?:仓位)?|'
    r'增配(?:该股|标的)?|加码(?:该股|标的)?|申购(?:该股|标的)?|上车|入市)'
    r'|'
    r'(?:买入|建仓|入场|加仓|开仓|增持|做多|配置(?:仓位)?|'
    r'增配(?:该股|标的)?|加码(?:该股|标的)?|申购(?:该股|标的)?|上车|入市)'
    r'[^。！？；;\n]{0,12}?(?:是否|能否|可否|何时|可行性|合适|适合)'
    r')'
    r'|'
    r'如需特批(?:买入|建仓|入场|加仓|开仓|增持|做多)'
    r'[^。！？；;\n]{0,24}(?:审批|批准)'
    r'|'
    r'(?:买入|购入|建仓|入场|加仓|补仓|开仓|增持|做多|介入|参与)'
    r'(?:的)?(?:是否|能否|可否|何时|可行性|适宜性)'
    r'[^。！？；;\n]{0,24}?'
    r'(?:合适|适合|可行|成立|仍需|需要|待)?'
    r'(?:讨论|研究|评估|判断|验证)?'
    r'|'
    r'(?:买入|购入|建仓|入场|加仓|补仓|开仓|增持|做多|介入|参与)'
    r'(?:策略|方案)[^。！？；;\n]{0,16}(?:回测|研究|评估|验证)'
    r')'
)

_ANALYTICAL_ENTRY_FACT = re.compile(
    r'(?:'
    r'(?:维持(?:原)?|重申)?买入评级'
    r'(?:机构[^。！？；;，,\n]{0,20}|维持不变|不变|为[^。！？；;，,\n]{0,12})?'
    r'|买入(?:成交额|资金(?:占比)?|意愿|盘)'
    r'[^。！？；;，,\n]{0,20}?'
    r'(?=(?:建议|推荐|可|可以|考虑|应|应该|应当|等待)'
    r'[^。！？；;，,\n]{0,12}(?:买入|购入|建仓|入场|开仓|加仓|补仓|增持|介入|参与))'
    r'|买入(?:成交额|资金(?:占比)?|意愿|盘)'
    r'(?![^。！？；;，,\n]{0,20}(?:建议|推荐|可|可以|考虑|应|应该|应当|等待)'
    r'[^。！？；;，,\n]{0,12}(?:买入|购入|建仓|入场|开仓|加仓|补仓|增持|介入|参与))'
    r'[^。！？；;，,\n]{0,20}'
    r'|买入(?:逻辑|信号)(?:尚|仍|暂时|目前|当前|才|可能|已经|已)*'
    r'(?:不充分|不足|缺失|未出现|待验证|待确认|成立|不成立|有效|无效|明确|不明确)'
    r'|(?:回测|历史回测|历史上|回测中|过往(?:样本|案例|表现)?)'
    r'[^。！？；;，,\n]{0,24}?'
    r'(?:买入|购入|建仓|入场|开仓|加仓|补仓|增持|做多|介入|参与)'
    r'[^。！？；;，,\n]{0,20}?'
    r'(?:胜率|收益|回报|表现|失败|成功)'
    r'|(?:历史上|历史回测|回测中|回测|过往(?:样本|案例|表现)?|策略回测\s*[:：]?)'
    r'(?![^。！？；;，,\n]{0,32}(?:当前|现在|目前|今日|今天|明日|明天|'
    r'最终|下一步|因此|所以|由此|据此|故而))'
    r'[^。！？；;，,\n]{0,32}?'
    r'(?:建议|推荐)?\s*'
    r'(?:买入|购入|建仓|入场|开仓|加仓|补仓|增持|做多|介入|参与|'
    r'条件试(?:探)?仓|确认建仓|积极建仓|轻仓试错|小仓位试错|'
    r'试探性轻仓(?:策略)?|轻仓试多|小仓位试多)'
    r'(?:后|并|随后|再)\s*'
    r'(?:持有|观察|等待|卖出|退出|止盈|止损)'
    r'(?:\s*(?:约)?(?:\d+(?:\.\d+)?|[一二两三四五六七八九十]+)'
    r'\s*(?:日|天|周|月|年))?'
    r'|(?:历史上|历史回测|回测中|回测|过往(?:样本|案例|表现)?|策略回测\s*[:：]?)'
    r'(?![^。！？；;，,\n]{0,32}(?:当前|现在|目前|今日|今天|明日|明天|'
    r'最终|下一步|因此|所以|由此|据此|故而|建议|推荐|可以|可|应当|应该|应|'
    r'需要|需|计划|准备|打算|决定|选择|立即|立刻|马上|强烈|强力|重仓|满仓|积极))'
    r'[^。！？；;，,\n]{0,32}?'
    r'(?:建议买入|买入|购入|建仓|入场|开仓|加仓|补仓|增持|做多|介入|参与|'
    r'条件试(?:探)?仓|确认建仓|积极建仓|轻仓试错|小仓位试错|'
    r'试探性轻仓(?:策略)?|轻仓试多|小仓位试多)'
    r'(?:(?:后|并|随后|再)?\s*'
    r'(?:持有|观察|等待|卖出|退出|止盈|止损)'
    r'[^。！？；;，,\n]{0,16})?'
    r'|(?:历史上|历史回测|回测中|过往(?:样本|案例|表现)?)'
    r'[^。！？；;，,\n]{0,20}?'
    r'(?:条件试(?:探)?仓|确认建仓|积极建仓|轻仓试错|小仓位试错|'
    r'试探性轻仓(?:策略)?|轻仓试多|小仓位试多)'
    r'[^。！？；;，,\n]{0,20}'
    r'|(?:员工持股计划|员工持股平台|公司股权激励计划)'
    r'[^。！？；;，,\n]{0,20}?'
    r'(?:条件试(?:探)?仓|确认建仓|积极建仓|轻仓试错|小仓位试错|'
    r'试探性轻仓(?:策略)?|轻仓试多|小仓位试多)'
    r')'
)

_ANALYTICAL_ACTION_NOUN = re.compile(
    r'(?:'
    r'(?:讨论|研究|评估|分析)\s*是否[^。！？；;，,\n]{0,12}?'
    r'(?:离场|退出|出局|降险)'
    r'|'
    r'(?:止损|止盈)(?:并|或)?\s*(?:离场|退出|出局)\s*'
    r'(?:机制|风险|信号|概念|术语|策略|规则|条件|标准|纪律|方案|效果|'
    r'有效|失效|尚未|未出现|的?(?:历史|概率|统计|回测))'
    r'|'
    r'(?:买入|购入|建仓|入场|开仓|加仓|补仓|增持|介入|卖出|减仓|清仓|止损|止盈)'
    r'(?:行为|需求|意愿|人数|人气|方(?:力量)?|力量|资金(?:流|占比)?|盘|情绪|压力|风险|'
    r'信号|逻辑|评级|成本|回报|收益|成交|评级机构)'
    r'|(?:买入|购入|卖出|减仓|清仓|止损|止盈)订单'
    r'(?=数量|金额|规模|占比|增长|下降|变化|上升|减少|增加|统计|数据|'
    r'已|已经|刚刚|刚|完成|成交|撤单|取消)'
    r')',
    re.IGNORECASE,
)

_ANALYTICAL_STRONG_TRIAL_TERM = re.compile(
    r'(?:'
    r'(?:分析|评估|讨论|研究|定义|说明)\s*'
    r'(?:重仓|满仓|大仓位|强烈|强力|无条件|果断|立即|立刻|直接|积极)\s*'
    r'(?:买入|购入|建仓|入场|开仓|加仓|补仓|增持|做多|介入|参与)'
    r'(?:的)?(?:风险|策略|方案|机制|规则|术语|定义|概念|历史|回测|胜率|效果)'
    r'|'
    r'[“”"\']'
    r'(?:重仓|满仓|大仓位|强烈|强力|无条件|果断|立即|立刻|直接|积极)\s*'
    r'(?:买入|购入|建仓|入场|开仓|加仓|补仓|增持|做多|介入|参与)'
    r'[“”"\'](?=\s*(?:只是|属于|是)?\s*(?:术语|定义|概念|策略|方案|机制|规则))'
    r'|'
    r'(?:分析|评估|讨论|研究|定义|说明|模型对|报告讨论是否应该)\s*'
    r'(?:重仓|满仓|大仓位|强烈|强力|无条件|果断|立即|立刻|直接|积极)\s*'
    r'(?:条件试(?:探)?仓|轻仓试错|小仓位试错|试探性轻仓(?:策略)?|'
    r'轻仓试多|小仓位试多|试仓|试多)'
    r'(?:的)?(?:风险|策略|方案|机制|规则|术语|定义|概念|历史|回测|胜率|效果)?'
    r'|'
    r'[“”"\']'
    r'(?:重仓|满仓|大仓位|强烈|强力|无条件|果断|立即|立刻|直接|积极)\s*'
    r'(?:条件试(?:探)?仓|轻仓试错|小仓位试错|试探性轻仓(?:策略)?|'
    r'轻仓试多|小仓位试多|试仓|试多)'
    r'[“”"\']'
    r'|'
    r'(?:重仓|满仓|大仓位|强烈|强力|无条件|果断|立即|立刻|直接|积极)\s*'
    r'(?:条件试(?:探)?仓|轻仓试错|小仓位试错|试探性轻仓(?:策略)?|'
    r'轻仓试多|小仓位试多|试仓|试多)'
    r'(?=\s*(?:策略|方案|机制|规则|术语|定义|概念|'
    r'的(?:风险|历史|回测|胜率|效果)|只是|属于))'
    r')',
    re.IGNORECASE,
)


def _mask_analytical_action_nouns(text: str) -> tuple[str, dict[str, str]]:
    """Protect action-shaped analytical nouns from imperative sanitizers."""
    protected: dict[str, str] = {}

    def _replace(match: re.Match[str]) -> str:
        prefix = text[max(0, match.start() - 40):match.start()]
        suffix = text[match.end():match.end() + 24]
        imperative_plan = re.search(
            r'(?:(?:当前|现在|目前|最终|下一步)\s*)?'
            r'(?:(?:建议|推荐|立即|立刻|应该|应当|应|需要|需|务必|必须|'
            r'可|可以|允许|计划|准备|打算|拟|将|要|宜|适宜|主张|决定|选择)\s*)+'
            r'(?:(?:采取|执行|进行|使用|采用)\s*)*$',
            prefix,
        )
        broad_imperative_plan = re.search(
            r'(?:(?:当前|现在|目前|最终|下一步)\s*)?'
            r'(?:建议|推荐|请|立即|立刻|应该|应当|应|需要|需|务必|必须|坚决|'
            r'无条件|果断|可|可以|允许|计划|准备|打算|拟|将|要|宜|适宜|主张|'
            r'决定|选择|考虑|倾向于|倾向|首选|最好|继续|维持|后续宜|后续可)'
            r'[^。！？；;，,\n]{0,20}$',
            prefix,
        )
        bare_imperative_plan = re.search(
            r'(?:^|[。！？；;，,\n])\s*'
            r'(?:(?:[-*>]|\d+[.)、])\s*)?'
            r'(?:采取|执行|进行|使用|采用|部署|建立|启动|制定|设置)'
            r'[^。！？；;，,\n]{0,12}$',
            prefix,
        )
        labeled_directive = re.search(rf'{_ACTION_LABEL_CONTEXT}\s*$', prefix)
        current_verdict = _EXPLICIT_CURRENT_EXIT_DIRECTIVE.search(prefix)
        if (
            broad_imperative_plan
            or bare_imperative_plan
            or imperative_plan
            or labeled_directive
            or current_verdict
        ):
            return match.group(0)
        imperative = re.search(
            r'(?:提交|创建|下达|执行|发出|挂出)\s*$',
            prefix,
        )
        if imperative:
            before_verb = prefix[:imperative.start()].rstrip()
            if not before_verb.endswith(("已", "已经")):
                return match.group(0)
        ambiguous_exit_plan = bool(
            re.search(
                r"(?:止损|止盈)(?:并|或)?\s*(?:离场|退出|出局)\s*"
                r"(?:机制|策略|规则|条件|标准|纪律|方案)$",
                match.group(0),
            )
        )
        clearly_analytical = bool(
            re.search(
                r"(?:风险|信号|概念|术语|效果|有效|失效|尚未|未出现|"
                r"历史|概率|统计|回测)$",
                match.group(0),
            )
            or re.search(
                r"(?:历史|回测|模型|统计|概率|研究|分析|评估|定义|概念|"
                r"术语|说明|数据显示|显示|表明|指出|测试|验证|复盘|描述|讨论)",
                prefix,
            )
            or re.match(
                r"\s*(?:有效|无效|仍需评估|需要评估|的历史|的概率|的统计|的回测)",
                suffix,
            )
        )
        imperative_suffix = bool(
            re.match(
                r"\s*(?:[，,；;]\s*)?"
                r"(?:(?:并且|并|且|因此|所以|故而|结论是)\s*)?"
                r"(?:建议|推荐|应|应该|应当|需|需要|必须|务必|立即|立刻|将|要|"
                r"宜|最好)?\s*(?:立即|立刻)?\s*"
                r"(?:执行|实施|采用|启用|启动|落实|下达)"
                r"(?!\s*(?:了|过|完成|结束|"
                r"(?:\d+|[一二两三四五六七八九十]+)\s*次))",
                suffix,
            )
        )
        if ambiguous_exit_plan and (not clearly_analytical or imperative_suffix):
            return match.group(0)
        token = f"__TA_ANALYTICAL_ACTION_NOUN_{len(protected)}__"
        protected[token] = match.group(0)
        return token

    return _ANALYTICAL_ACTION_NOUN.sub(_replace, text), protected


def _restore_analytical_action_nouns(
    text: str,
    protected: dict[str, str],
) -> str:
    for token, original in protected.items():
        text = text.replace(token, original)
    return text


def _mask_hypothetical_holding_plans(text: str) -> tuple[str, dict[str, str]]:
    """Protect future holding scenarios from current no-position rewrites."""
    protected: dict[str, str] = {}

    if "\n" in text or "\r" in text:
        masked_lines: list[str] = []
        for line in text.splitlines(keepends=True):
            content = line.rstrip("\r\n")
            ending = line[len(content):]
            masked, local_protected = _mask_hypothetical_holding_plans(content)
            for local_token, original in local_protected.items():
                global_token = f"__TA_HYPOTHETICAL_HOLDING_BODY_{len(protected)}__"
                masked = masked.replace(local_token, global_token)
                protected[global_token] = original
            masked_lines.append(masked + ending)
        return "".join(masked_lines), protected

    def _replace(match: re.Match[str]) -> str:
        prefix = match.string[max(0, match.start() - 24):match.start()]
        direct_entry = re.match(
            r'(?P<entry>买入|建仓|入场|开仓)(?P<future_plan>后.+)',
            match.group(0),
        )
        if direct_entry and re.search(
            r'(?:当前|现在|目前|今日|今天|最终|下一步|建议|推荐|应|应该|应当|'
            r'需要|需|计划|准备|打算|决定|选择|立即|立刻|马上)\s*$',
            prefix,
        ):
            # This is a current entry order followed by a future stop, not a
            # standalone future-position plan. Keep the entry text visible to
            # the gate while protecting only the later future holding clause.
            # The explicit delimiter and ``后续持仓`` bridge avoid a dangling
            # ``确认后若`` fragment after the entry order is replaced.
            token = f"__TA_HYPOTHETICAL_HOLDING_{len(protected)}__"
            protected[token] = direct_entry.group("future_plan").removeprefix("后")
            return direct_entry.group("entry") + "；后续持仓" + token
        token = f"__TA_HYPOTHETICAL_HOLDING_{len(protected)}__"
        protected[token] = match.group(0)
        return token

    # Risk-manager output can express a complete future-position plan in one
    # bullet (for example ``右侧持仓：...止损``). Protect only the hypothetical
    # portion. A later explicit current directive must remain visible to the
    # no-position sanitizer.
    explicit_scenario = re.match(
        r"^\s*(?:(?:#{1,6}\s*)|(?:(?:[-*>]|\d+[.)、])\s*))?\*{0,2}"
        r"(?:(?:右侧|左侧)(?:持仓|建仓)(?:者|后)?|任何持仓若)"
        r"\*{0,2}\s*[:：]?",
        text,
    )
    if explicit_scenario:
        # Protect the labelled first clause and only clearly conditional
        # continuation clauses. Bare or explicitly current clauses after a
        # semicolon remain available to the sanitizer.
        parts = re.split(r"([；;。！？])", text)
        for index in range(0, len(parts), 2):
            clause = parts[index]
            if not clause:
                continue
            search_from = explicit_scenario.end() if index == 0 else 0
            current_directive = _EXPLICIT_CURRENT_EXIT_DIRECTIVE.search(
                clause[search_from:]
            )
            split_at = (
                search_from + current_directive.start()
                if current_directive
                else len(clause)
            )
            candidate = clause[search_from:split_at]
            risk_action_term = (
                r"(?:减半仓|减仓|清仓|止盈|卖出|"
                r"止损(?!线|位|价|条件|阈值|规则)(?:(?:并|或)?\s*离场)?|"
                r"降险(?:或\s*离场)?|离场)"
            )
            risk_action_pattern = re.compile(
                rf"{risk_action_term}"
                rf"(?:(?:\s*(?:并|或|再|然后)\s*){risk_action_term})*",
            )
            risk_actions = list(risk_action_pattern.finditer(candidate))
            if split_at <= 0 or not risk_actions:
                continue
            previous_action_end = 0
            protect_end_in_candidate = None
            for action_index, risk_action in enumerate(risk_actions):
                condition_scope = candidate[previous_action_end:risk_action.start()]
                has_threshold_trigger = bool(
                    re.search(
                        r"(?:跌破|突破|失守|站稳|触发|触及|达到|回落至?|回撤至?|反弹至?)\s*"
                        r"(?:\d|关键|支撑|阻力|止损|前低|前高|平台|均线|中轨|下轨|上轨)",
                        condition_scope,
                    )
                )
                has_future_position_marker = bool(
                    re.search(
                        r"(?:未来持仓|后续持仓|持仓后|买入后|建仓后|入场后|开仓后|"
                        r"未来(?:买入|建仓|入场|开仓)(?:后)?|"
                        r"(?:买入|建仓|入场|开仓)成功)",
                        condition_scope,
                    )
                )
                # ``建议买入后...`` contains a current entry recommendation,
                # not merely a hypothetical risk plan.  It must remain visible
                # to the no-position and evidence gates instead of using the
                # later stop clause to bypass them.
                entry_directive_before_future_plan = bool(
                    re.search(
                        r"(?:建议|推荐|应|应该|应当|可|可以|计划|准备|决定|"
                        r"选择|立即|立刻|现在|当前|目前|今日|今天|明日|明天|"
                        r"下一步|马上|直接)\s*"
                        r"(?:买入|建仓|入场|开仓)后",
                        condition_scope,
                    )
                )
                has_conditional_marker = bool(
                    re.search(r"(?:若|如果|假如|一旦)", condition_scope)
                )
                has_future_suffix = bool(
                    re.search(
                        r"(?:后|时|则)\s*"
                        r"(?:建议|考虑|立即|立刻|应|应该|需|需要|先|直接|分批|部分|逐步)?\s*$",
                        condition_scope,
                    )
                )
                describes_current_fact = bool(
                    re.search(
                        r"(?:当前|目前|现在|今日|当日|昨日|昨天|现价|刚刚|刚|"
                        r"早盘|盘中|已经|已然|已)\s*",
                        condition_scope,
                    )
                )
                # Evaluate each trigger/action pair independently. This keeps
                # a second future stop in ``若...减仓，若...清仓`` protected,
                # while contrast/current directives still terminate the chain.
                diversion_match = re.search(
                        r"(?:观察|观望|等待|暂缓|保持|持有|买入|建仓|入场|开仓|"
                        r"试仓|加仓|增持|研究|评估|预警|不操作|不动作|"
                        r"但(?:是)?|不过|然而|可是|同时|并且)",
                        condition_scope,
                    )
                hard_diversion = re.search(
                    r"(?:观望|等待|暂缓|保持|持有|买入|建仓|入场|开仓|"
                    r"试仓|加仓|增持|不操作|不动作|但(?:是)?|不过|然而|"
                    r"可是|同时|并且)",
                    condition_scope,
                )
                condition_diverted = bool(diversion_match) or (
                    action_index == 0
                    and condition_scope.count("，") + condition_scope.count(",") > 1
                )
                if (
                    has_future_position_marker
                    and re.search(
                        r"(?:买入|建仓|入场|开仓)后",
                        condition_scope,
                    )
                    and not entry_directive_before_future_plan
                    and not describes_current_fact
                ):
                    # Inside an explicit ``左侧/右侧持仓`` scenario this is
                    # the future-position marker, not a present entry order.
                    condition_diverted = False
                if (
                    diversion_match
                    and has_threshold_trigger
                    and has_conditional_marker
                    and diversion_match.group(0) in {"观察", "研究", "评估", "预警"}
                    and not hard_diversion
                ):
                    condition_diverted = False
                if (
                    diversion_match
                    and has_threshold_trigger
                    and has_conditional_marker
                    and diversion_match.group(0) in {"同时", "并且"}
                    and not describes_current_fact
                ):
                    condition_diverted = False
                inherits_previous_trigger = bool(
                    action_index > 0
                    and protect_end_in_candidate is not None
                    and re.fullmatch(
                        r"\s*[，,]?\s*(?:随后|继而|接着|然后|再|届时|之后|"
                        r"必要时|后续)\s*"
                        r"(?:建议|考虑|立即|立刻|应|应该|需|需要|先|直接|"
                        r"分批|部分|逐步)?\s*",
                        condition_scope,
                    )
                )
                inherits_conditional_continuation = bool(
                    action_index > 0
                    and protect_end_in_candidate is not None
                    and not describes_current_fact
                    and re.fullmatch(
                        r"\s*[，,]?\s*(?:若|如果|假如|一旦)"
                        r"[^。！？；;，,\n]{1,20}(?:则|时)\s*"
                        r"(?:建议|考虑|立即|立刻|应|应该|需|需要|先|直接|"
                        r"分批|部分|逐步)?\s*",
                        condition_scope,
                    )
                )
                has_qualitative_trigger = bool(
                    has_conditional_marker
                    and not describes_current_fact
                    and re.search(
                        r"(?:若|如果|假如|一旦)"
                        r"[^。！？；;\n]{1,40}(?:则|，|,)",
                        condition_scope,
                    )
                )
                has_trigger = (
                    (
                        has_threshold_trigger
                        and (has_conditional_marker or has_future_suffix)
                        and not describes_current_fact
                    )
                    or (
                        has_future_position_marker
                        and not entry_directive_before_future_plan
                    )
                    or (
                        action_index == 0
                        and index == 0
                        and "任何持仓若" in explicit_scenario.group(0)
                        and not describes_current_fact
                    )
                    or inherits_previous_trigger
                    or inherits_conditional_continuation
                    or has_qualitative_trigger
                ) and not condition_diverted
                if not has_trigger:
                    break
                protect_end_in_candidate = risk_action.end()
                previous_action_end = risk_action.end()
            if protect_end_in_candidate is None:
                continue
            protect_end = search_from + protect_end_in_candidate
            token = f"__TA_HYPOTHETICAL_HOLDING_LINE_{len(protected)}__"
            protected[token] = clause[:protect_end]
            parts[index] = token + clause[protect_end:]
        # An explicit scenario can also use a future-position phrase instead
        # of a price trigger (for example ``未来持仓后计划减仓``). Apply the
        # established fallback to portions that were not protected above.
        masked = _CONDITIONAL_MODAL_HOLDING_PLAN.sub(
            _replace,
            "".join(parts),
        )
        return _HYPOTHETICAL_HOLDING_PLAN.sub(_replace, masked), protected

    masked = _CONDITIONAL_MODAL_HOLDING_PLAN.sub(_replace, text)
    return _HYPOTHETICAL_HOLDING_PLAN.sub(_replace, masked), protected


def _restore_hypothetical_holding_plans(
    text: str,
    protected: dict[str, str],
) -> str:
    for token, original in protected.items():
        text = text.replace(token, original)
    return text


def _mask_non_executable_entry_discussion(
    text: str,
) -> tuple[str, dict[str, str]]:
    """Protect research questions and plans explicitly deferred by a gate."""
    protected: dict[str, str] = {}

    def _replace(match: re.Match[str]) -> str:
        token = f"__TA_NON_EXECUTABLE_ENTRY_{len(protected)}__"
        protected[token] = match.group(0)
        return token

    return _NON_EXECUTABLE_ENTRY_DISCUSSION.sub(_replace, text), protected


def _restore_non_executable_entry_discussion(
    text: str,
    protected: dict[str, str],
) -> str:
    for token, original in protected.items():
        text = text.replace(token, original)
    return text


def _mask_analytical_entry_facts(text: str) -> tuple[str, dict[str, str]]:
    """Protect analytical labels and measurements that are not directives."""
    protected: dict[str, str] = {}

    def _replace(match: re.Match[str]) -> str:
        prefix = text[max(0, match.start() - 40):match.start()]
        quote_led = match.group(0).lstrip().startswith(("“", '"', "'"))
        if quote_led and re.search(
            rf'(?:{_ACTION_LABEL_CONTEXT}|'
            r'(?:建议|推荐|执行|采取|采用|使用|启用|实施|落实|'
            r'选择|决定|计划|准备))\s*$',
            prefix,
        ):
            return match.group(0)
        token = f"__TA_ANALYTICAL_ENTRY_FACT_{len(protected)}__"
        protected[token] = match.group(0)
        return token

    text = _ANALYTICAL_STRONG_TRIAL_TERM.sub(_replace, text)
    return _ANALYTICAL_ENTRY_FACT.sub(_replace, text), protected


def _restore_analytical_entry_facts(
    text: str,
    protected: dict[str, str],
) -> str:
    for token, original in protected.items():
        text = text.replace(token, original)
    return text


def _display_action_replacement(replacement: str) -> str:
    """Remove regex backreferences from user-facing audit descriptions."""
    return re.sub(r"\\g<[^>]+>", "", replacement)


_ACTION_SEQUENCE_MODIFIERS = (
    r'(?:(?:先|就|下一步|本次|优先|直接|立即|立刻|马上|确认|果断|强烈|强力|无条件|'
    r'重仓|满仓|全仓|半仓|全部仓位|大仓位|梭哈|逐步|分批|轻仓|小仓位|少量|'
    r'适度|适当|酌情|谨慎|趁机|顺势|择机|适时|伺机|后续|逢高|反弹|逢低|'
    r'维持|予以|做)\s*|'
    r'等待[^。！？；;，,\n]{1,20}(?:后|时)\s*)*'
)
_ACTION_EXECUTABLE_CONTEXT = (
    rf'{_ACTION_LINE_CONTEXT}'
    rf'{_ACTION_LEADING_DIRECTIVES}'
    rf'{_ACTION_SEQUENCE_MODIFIERS}'
)
_ACTION_MARKDOWN_SUFFIX = r'(?:\*{0,2})'
_ACTION_CAUSAL_PREFIX = r'(?:因此|所以|由此|据此|故而|故)'
_ACTION_CAUSAL_ACTOR_AND_MODAL = (
    r'(?:(?:我|本人|我们|本基金|本账户|用户|投资者|客户|持币者|大家|'
    r'系统|交易员|本策略|策略|模型|风控|投资委员会)(?:的)?\s*)?'
    r'(?:(?:现在|当前|目前)\s*)?'
    r'(?:(?:会|将|计划|准备|打算|拟|建议|推荐|可|可以|应|应当|应该|'
    r'需|需要|选择|决定|值得|适合)\s*)?'
)
_ACTION_CONDITIONAL_SUBJECT_PREFIX = (
    r'[^。！？；;，,\n]{1,36}(?:后|时)\s*'
    r'(?:我|本人|我们|本基金|本账户)(?:的)?\s*'
    r'(?:(?:认为|觉得|判断(?:是|为)?|(?:个人)?观点(?:是|为)?|'
    r'结论(?:是|为)?|考虑后)\s*)?'
    r'(?:会|将|计划|准备|打算|拟|决定|选择|建议|应|应当|应该|需要|需)\s*'
)
_ACTION_EXPLICIT_VERDICT_PREFIX = (
    r'(?P<directive>'
    r'(?:(?:最终|当前)(?:建议|裁决|结论|动作)?|今日|今天|明日|下周|'
    r'收盘前|开盘即|只|仅|转为|改为|'
    r'由[^。！？；;，,\n]{1,12}转为)'
    r'\s*[:：]?\s*)'
)
_CURRENT_ENTRY_DIRECTIVE = re.compile(
    r'(?:当前|现在|目前|今日|今天|明日|明天|下一步|马上|立即|立刻|直接)\s*'
    r'(?:(?:建议|推荐|可|可以|考虑|应|应该|需要|需|计划|准备|执行|进行|采取)\s*)?'
    r'(?:买入|购入|建仓|入场|进场|开仓|加仓|补仓|增持|做多|介入|参与)'
    r'(?!价|价格|条件|区间|计划|触发|信号|逻辑|评级|点|上限|额度|比例|限制|禁令)'
)
_CURRENT_ENTRY_BEFORE_FUTURE_PLAN = re.compile(
    r'(?:当前|现在|目前|今日|今天|明日|明天|下一步|马上|立即|立刻|直接)\s*'
    r'(?:(?:建议|推荐|可|可以|考虑|应|应该|需要|需|计划|准备|执行|进行|采取)\s*)?'
    r'(?:买入|购入|建仓|入场|进场|开仓|加仓|补仓|增持|做多|介入|参与)'
    r'后(?=(?:若|如果|假如|一旦))'
)
_SANITIZE_FUNDAMENTAL_VETO_BUY = [
    (
        r'(?:(?:我|本人|我们|本基金|本账户|系统|模型|风控|投资委员会)\s*)?'
        r'(?:赞成|支持|同意|认可)\s*'
        r'(?:买入|购入|建仓|入场|开仓|加仓|补仓|增持|做多|介入|参与)'
        r'(?!评级|信号|逻辑|条件|观点|建议|风险)',
        '等待基本面证据复核',
    ),
    (
        r'(?:提交|创建|下达|执行|发出|挂出)\s*'
        r'(?:一笔|新的)?\s*买入订单'
        r'(?:\s*(?:数量|金额|规模)?\s*\d+(?:\.\d+)?\s*(?:股|手|元|万元|亿元)?)?',
        '等待基本面证据复核',
    ),
    (
        rf'(?P<label>{_ACTION_LABEL_CONTEXT})'
        r'(?:(?:建议|推荐|可以|可|考虑|计划|准备|执行|进行|采取|决定|选择)\s*)?'
        r'(?:(?:确认|果断|强烈|强力|无条件|立即|立刻|直接|积极|'
        r'重仓|满仓|大仓位|逐步|分批|轻仓|小仓位|少量)\s*)*'
        r'(?:买入|购入|建仓|入场|加仓|补仓|开仓|增持|做多)'
        r'(?!价|价格|条件|区间|计划|触发|信号|逻辑|评级|点|上限|额度|比例|限制|禁令|风险|建议)',
        r'\g<label>等待基本面证据复核',
    ),
    (
        r'(?:(?:用户|投资者|客户|持币者|大家)\s*)?'
        r'(?:建议|推荐|可以|可|考虑|应|应当|应该|需|需要|计划|准备|'
        r'执行|进行|采取|决定|选择|值得|适合)\s*'
        r'(?:(?:确认|果断|强烈|强力|无条件|立即|立刻|直接|积极|'
        r'重仓|满仓|大仓位|逐步|分批|轻仓|小仓位|少量)\s*)*'
        r'(?:建立(?:底仓|仓位|头寸)|建底仓|买进|吸纳|吸筹|抢筹|开多(?:仓)?|'
        r'分批布局|小仓位布局|布局(?:该股|该标的|个股|股票)?|拿先手|拿底仓|配置(?:仓位)?|介入(?:该股|标的)?|'
        r'参与(?:该股|标的)?|加码(?:该股|标的)?|增配(?:该股|标的)?|'
        r'入市|上车|申购(?:该股|标的)?|布局\s*\d+(?:\.\d+)?\s*%?\s*仓位)'
        r'(?!价|价格|条件|区间|计划|触发|信号|逻辑|评级|点|上限|额度|比例|限制|禁令|风险|建议)',
        '等待基本面证据复核',
    ),
    (
        rf'(?P<label>{_ACTION_LABEL_CONTEXT})'
        r'(?:(?:建议|推荐|可以|可|考虑|计划|准备|执行|进行|采取|决定|选择)\s*)?'
        r'(?:(?:确认|果断|强烈|强力|无条件|立即|立刻|直接|积极|'
        r'重仓|满仓|大仓位|逐步|分批|轻仓|小仓位|少量)\s*)*'
        r'(?:建立(?:底仓|仓位|头寸)|建底仓|买进|吸纳|吸筹|抢筹|开多(?:仓)?|'
        r'分批布局|小仓位布局|布局(?:该股|该标的|个股|股票)?|拿先手|拿底仓|配置(?:仓位)?|介入(?:该股|标的)?|'
        r'参与(?:该股|标的)?|加码(?:该股|标的)?|增配(?:该股|标的)?|'
        r'入市|上车|申购(?:该股|标的)?|布局\s*\d+(?:\.\d+)?\s*%?\s*仓位)'
        r'(?!价|价格|条件|区间|计划|触发|信号|逻辑|评级|点|上限|额度|比例|限制|禁令|风险|建议)',
        r'\g<label>等待基本面证据复核',
    ),
    (
        rf'{_ACTION_EXPLICIT_VERDICT_PREFIX}'
        r'(?:买入|购入|建仓|入场|加仓|开仓|增持|做多)'
        r'(?!价|价格|条件|区间|计划|触发|信号|逻辑|评级|点|上限|额度|比例|限制|禁令|风险|建议)',
        r'\g<directive>等待基本面证据复核',
    ),
    (
        rf'(?P<label>{_ACTION_LABEL_CONTEXT})'
        r'(?:建议|推荐|可以|可|考虑|计划|准备|执行|进行|采取|决定|选择)?\s*'
        r'(?:条件试(?:探)?仓|轻仓试错|小仓位试错|试探性(?:建仓|轻仓(?:策略)?)|'
        r'试探(?:买入|建仓)|轻仓试多|小仓位试多|试仓|试多)'
        r'(?!策略|计划|条件|逻辑|信号|讨论|是否|何时)',
        r'\g<label>等待基本面证据复核',
    ),
] + [
    (pattern, "等待基本面证据复核")
    for pattern, _replacement in _SANITIZE_EVENT_RISK_BUY
] + [
    (
        r'(?P<entry_condition>(?<!何)(?:后|时|则)\s*)'
        r'(?:再\s*)?'
        r'(?:(?:用户|投资者|客户|持币者|大家)\s*)?'
        r'(?:(?:可|可以|建议|推荐|考虑|允许|应当|应该|应|需|需要|计划|准备|决定|选择)\s*)?'
        r'(?:买入|建仓|入场|加仓|开仓|增持|做多)'
        r'(?!价|价格|条件|区间|计划|触发|信号|逻辑|评级|点|上限|额度|比例|限制|禁令|的?可行性|的?可能性|是否|何时)',
        r'\g<entry_condition>等待基本面证据复核',
    ),
    (
        r'(?P<entry_condition>(?<!何)(?:后|时|则)\s*)'
        r'(?:(?:建议|推荐|可以|可|考虑|允许|应当|应该|应|需|需要|计划|准备|决定|选择)\s*)?'
        r'(?:条件试(?:探)?仓|轻仓试错|小仓位试错|试探性(?:建仓|轻仓(?:策略)?)|'
        r'试探(?:买入|建仓)|轻仓试多|小仓位试多|试仓|试多|配置(?:仓位)?)'
        r'(?!策略|计划|条件|逻辑|信号|讨论|是否|何时)',
        r'\g<entry_condition>等待基本面证据复核',
    ),
    (
        rf'{_ACTION_CONDITIONAL_SUBJECT_PREFIX}'
        r'(?:买入|建仓|入场|加仓|开仓|增持|做多)'
        r'(?!价|价格|条件|区间|触发|信号|点|上限|额度|比例|限制|禁令)',
        "等待基本面证据复核",
    ),
    (
        rf'{_ACTION_CAUSAL_PREFIX}\s*'
        rf'{_ACTION_CAUSAL_ACTOR_AND_MODAL}'
        r'(?:买入|建仓|入场|加仓|开仓|增持|做多)'
        r'(?!价|价格|条件|区间|触发|信号|点|上限|额度|比例|限制|禁令)',
        "等待基本面证据复核",
    ),
    (
        r'(?:该股|该标的|本标的)\s*(?:值得|适合)\s*'
        r'(?:买入|建仓|入场|加仓|开仓|增持|做多)'
        r'(?!价|价格|条件|区间|触发|信号|点|上限|额度|比例|限制|禁令)',
        "等待基本面证据复核",
    ),
    (
        r'(?<!是否)(?<!能否)(?<!可否)(?<!不)'
        r'(?:值得|最好|宁可)\s*'
        r'(?:(?:考虑|选择|建议|推荐)\s*)?'
        r'(?:买入|购入|建仓|入场|开仓|试仓|试多|低吸|加仓|补仓|'
        r'增持|介入|参与|抄底|追涨|配置(?:仓位)?)'
        r'(?!价|价格|条件|区间|计划|触发|信号|逻辑|评级|点|上限|'
        r'额度|比例|限制|禁令|的?可行性|的?可能性)',
        "等待基本面证据复核",
    ),
    (
        r'(?:不妨|不如)\s*'
        r'(?:(?:考虑|选择|建议|推荐)\s*)?'
        r'(?:买入|购入|建仓|入场|开仓|试仓|试多|低吸|加仓|补仓|'
        r'增持|介入|参与|抄底|追涨|配置(?:仓位)?)'
        r'(?!价|价格|条件|区间|计划|触发|信号|逻辑|评级|点|上限|'
        r'额度|比例|限制|禁令|的?可行性|的?可能性)',
        "等待基本面证据复核",
    ),
    (
        r'(?:用户|投资者)?\s*'
        r'(?:建议|推荐|计划|考虑|可以|可|允许|执行|开始|进行|应当|应该|必须|务必|需要|需|请|主张|适宜|后续宜|宜|倾向于|倾向)\s*'
        r'(?:用户|投资者)?\s*'
        r'(?:(?:在|于)\s*[^。！？；;，,\n]{1,24}\s*)?'
        r'(?:(?:逢低|逐步|分批|轻仓|小仓位|少量)\s*)*'
        r'(?:买入|建仓|入场|加仓|开仓|增持|做多)'
        r'(?:\d+(?:\.\d+)?\s*(?:%|成仓|股|手|仓位))?',
        "等待基本面证据复核",
    ),
    (
        r'(?:可|可以|建议|推荐|考虑)\s*'
        r'(?:低吸|逢低吸纳|试多|追涨)',
        "等待基本面证据复核",
    ),
    (
        r'(?:昨日|昨天|今日|今天|当前|目前)?\s*'
        r'(?:买入|建仓|入场|加仓|开仓|增持)建议(?:仍然)?有效',
        "等待基本面证据复核",
    ),
    (
        r'(?:将|把)?(?:买入|建仓|入场|加仓)(?:额度|比例|上限|限制)'
        r'(?!字段|规则|阈值|说明)'
        r'(?:提高|提升|增加|调整|调高|设定|设置|设为|设成)'
        rf'(?:至|到|为|[:：])?\s*{_ACTION_SIZE_VALUE}',
        "等待基本面证据复核",
    ),
    (
        r'(?:执行|可|可以|建议|推荐|考虑|允许|开始|进行)?'
        r'(?:动态)?(?:调整|提高|提升|增加|调高|设定|设置)'
        r'(?:买入|建仓|入场|加仓)(?:额度|比例|上限|限制)',
        "等待基本面证据复核",
    ),
    (
        r'(?:执行|可|可以|建议|推荐|考虑|允许|开始|进行)(?:将|把)'
        r'(?:买入|建仓|入场|加仓)(?:额度|比例|上限|限制)'
        r'(?:提高|提升|增加|调整|调高|设定|设置|设为|设成)?'
        rf'(?:至|到|为|[:：])?\s*{_ACTION_SIZE_VALUE}',
        "等待基本面证据复核",
    ),
    (
        r'(?:执行|可|可以|建议|推荐|考虑|允许|开始|进行)'
        r'(?:买入|建仓|入场|加仓)(?!价|价格|条件|区间|触发|信号|点)',
        "等待基本面证据复核",
    ),
    (
        r'(?:执行|可|可以|建议|推荐|考虑|允许|开始|进行)?\s*'
        r'(?:抄底|购入)(?:少量|部分|轻仓|小仓位)?(?:仓位)?'
        r'(?!价|价格|条件|区间|计划|触发|信号|点|上限|额度|比例|限制|禁令)',
        "等待基本面证据复核",
    ),
    (
        r'(?:(?:执行|可|可以|建议|推荐|考虑|允许|开始|进行))?'
        r'(?:买入|建仓|入场|加仓)(?:额度|比例)'
        r'(?:为|[:：]|控制在|控制为|不超过|不高于|至少|不低于|不小于|≤|<|≥|>)?\s*'
        rf'{_ACTION_SIZE_VALUE}',
        "等待基本面证据复核",
    ),
    (
        r'(?:买入|建仓|入场|加仓)(?:额度|比例|上限|限制)'
        r'(?!字段|规则|阈值|说明)'
        r'(?:的)?(?:建议|推荐|计划|目标)'
        r'(?:为|是|设为|设置为|控制在|控制为|提高至|提升至|增加至|'
        r'不超过|不高于|至少|不低于|不小于|≤|<|≥|>|[:：])\s*'
        rf'{_ACTION_SIZE_VALUE}',
        "等待基本面证据复核",
    ),
    (
        r'(?:执行|可|可以|建议|推荐|考虑|允许|开始|进行)'
        r'(?:买入|建仓|入场|加仓)(?=额度|比例|上限|限制)',
        "等待基本面证据复核",
    ),
    (
        rf'{_ACTION_EXECUTABLE_CONTEXT}'
        rf'买入{_ACTION_MARKDOWN_SUFFIX}'
        r'(?!价|价格|条件|区间|触发|信号|点|上限|额度|比例|限制|禁令)',
        "等待基本面证据复核",
    ),
    (
        r'(?:综合判断|整体判断|操作|操作上|策略上|最终结论|当前结论|结论|首选(?:方案)?)'
        r'\s*(?:以|偏向|倾向|为|是|[:：])?\s*'
        r'(?:买入|建仓|入场|加仓|开仓|增持|做多)'
        r'(?:为主|优先|作为首选)?',
        "等待基本面证据复核",
    ),
    (
        r'(?:(?:最终建议|最终裁决|最终结论|交易建议|操作建议|当前结论)\s*[:：]?\s*)?'
        r'(?:建议|推荐|当前最好|首选(?:方案)?(?:是|为)?|我的选择(?:会)?是)?\s*'
        r'(?:以\s*)?'
        r'(?:买入|建仓|入场|加仓|开仓|增持|做多)\s*'
        r'(?:为主|为宜|优先|作为首选)',
        "等待基本面证据复核",
    ),
    (
        r'(?:建议|推荐|可|可以|考虑)?\s*高抛低吸',
        "等待基本面证据复核",
    ),
    (
        r'(?:若|如果|如|一旦|待|当)\s*'
        r'[^。！？；;，,\n]{0,30}'
        r'(?:突破|站稳|回踩|触及|达到|高于|站上)'
        r'[^。！？；;，,\n]{0,24}?'
        r'(?:后|则|时|即可|可以|可|建议|考虑|允许|应当|应该)?\s*'
        r'(?:买入|建仓|入场|加仓|开仓|增持|做多)'
        r'(?!价|价格|条件|区间|计划|触发|信号|点|上限|额度|比例|限制|禁令)',
        "等待基本面证据复核",
    ),
    (
        r'(?:若|如果|如|一旦|待|当)\s*'
        r'[^。！？；;，,\n]{0,48}?'
        r'(?:后|则|时)\s*'
        r'(?:(?:再|择机|适时|伺机|逢低|轻仓|小仓位|分批)\s*)*'
        r'(?:(?:可|可以|建议|推荐|考虑|允许|应当|应该|应|需|需要)\s*)?'
        r'(?:买入|建仓|入场|加仓|开仓|增持|做多)'
        r'(?!价|价格|条件|区间|计划|触发|信号|点|上限|额度|比例|限制|禁令)',
        "等待基本面证据复核",
    ),
    (
        r'(?:建议|推荐|可|可以|考虑|允许|应|应该|应当|需|需要|计划|准备|决定|选择|执行|进行|采取)'
        r'[^。！？；;，,:：\n]{0,12}?'
        r'(?:买入|建仓|入场|加仓|开仓|增持|做多)'
        r'(?!价|价格|条件|区间|计划|触发|信号|点|上限|额度|比例|限制|禁令|的?可行性|的?可能性)',
        "等待基本面证据复核",
    ),
    (
        rf'{_ACTION_LINE_CONTEXT}'
        r'(?:(?:价格|股价|收盘价|指数|标的)\s*)?'
        r'(?:突破|站稳|回踩|触及|达到|高于|站上)'
        r'[^。！？；;，,\n]{0,24}?'
        r'(?:后|则|时|即可|可以|可|建议|考虑|允许|应当|应该)\s*'
        r'(?:买入|建仓|入场|加仓|开仓|增持|做多)'
        r'(?!价|价格|条件|区间|计划|触发|信号|点|上限|额度|比例|限制|禁令)',
        "等待基本面证据复核",
    ),
    (
        r'(?:本次|当前|最终)?\s*(?:决定|选择)\s*'
        r'(?:买入|建仓|入场|加仓|开仓|增持|做多)',
        "等待基本面证据复核",
    ),
    (
        r'(?:现在|当前|目前)?\s*'
        r'(?:可|可以|建议|推荐|考虑|允许|应当|应该|适合)\s*'
        r'买(?!入?价|入?价格|入?条件|入?区间|入?触发|入?信号|入?点)',
        "等待基本面证据复核",
    ),
    (
        rf'{_ACTION_EXECUTABLE_CONTEXT}'
        rf'买{_ACTION_MARKDOWN_SUFFIX}'
        r'(?=\s*(?:\d+(?:\.\d+)?\s*(?:股|手|%|成仓)?|[。！？；;，,、]|$))',
        "等待基本面证据复核",
    ),
    (r'买(?:一点|一些|少量|点仓位)', "等待基本面证据复核"),
    (
        rf'{_ACTION_EXECUTABLE_CONTEXT}'
        r'(?:建仓|入场|进场|加仓|补仓|开仓|增持|做多)'
        rf'{_ACTION_MARKDOWN_SUFFIX}'
        r'(?!价|价格|条件|区间|计划|触发|信号|点|上限|额度|比例|限制|禁令)',
        "等待基本面证据复核",
    ),
    (
        rf'{_ACTION_EXECUTABLE_CONTEXT}'
        r'(?:建立(?:底仓|仓位|头寸)|建底仓|买进|吸纳|吸筹|抢筹|开多(?:仓)?|'
        r'分批布局|小仓位布局|布局(?:该股|该标的|个股|股票)?|拿先手|拿底仓|配置(?:仓位)?|'
        r'介入(?:该股|标的)?|参与(?:该股|标的)?|加码(?:该股|标的)?|'
        r'增配(?:该股|标的)?|入市|上车|申购(?:该股|标的)?|'
        r'布局\s*\d+(?:\.\d+)?\s*%?\s*仓位)'
        rf'{_ACTION_MARKDOWN_SUFFIX}'
        r'(?!价|价格|条件|区间|计划|触发|信号|逻辑|评级|点|上限|额度|比例|限制|禁令|风险)',
        "等待基本面证据复核",
    ),
    (
        r'(?:(?:当前|现在|目前)(?:价格|价位)?\s*)?'
        r'(?:可|可以|建议|推荐|考虑|允许|应当|应该|适合)\s*'
        r'(?:(?:轻仓|小仓位|少量|分批)\s*)?(?:介入|参与)'
        r'(?:该股|标的|个股|股票)?',
        "等待基本面证据复核",
    ),
    (
        r'(?:轻仓|小仓位|少量|分批)\s*(?:介入|参与)'
        r'(?:该股|标的|个股|股票)?',
        "等待基本面证据复核",
    ),
    (
        r'(?:逐步|分批|轻仓|小仓位|少量)\s*建立仓位|'
        r'配置\s*(?:约|不超过|至多)?\s*\d+(?:\.\d+)?\s*%\s*(?:的)?仓位',
        "等待基本面证据复核",
    ),
    (
        r'(?:可|可以|建议|推荐|考虑|允许|应当|应该)\s*'
        r'(?:(?:逢低|分批|轻仓|小仓位)\s*)配置(?:仓位)?',
        "等待基本面证据复核",
    ),
    (
        rf'{_ACTION_EXECUTABLE_CONTEXT}'
        r'(?:追涨|低吸|逢低吸纳|试多)'
        rf'{_ACTION_MARKDOWN_SUFFIX}',
        "等待基本面证据复核",
    ),
    (
        r'(?:这|该结论|结论|判断|证据)\s*'
        r'(?:意味着|表明|支持|指向)\s*'
        r'(?:买入|购入|建仓|入场|开仓|加仓|补仓|增持|做多|介入|参与)'
        r'(?!回报|收益|订单|成交|成本|评级|信号|逻辑|条件|风险)',
        "等待基本面证据复核",
    ),
    (
        r'(?:(?:我|本人|我们|系统|模型)\s*)?'
        r'(?:投票|偏向|倾向|选择|决定)\s*'
        r'(?:买入|购入|建仓|入场|开仓|加仓|补仓|增持|做多|介入|参与)'
        r'(?!回报|收益|订单|成交|成本|评级|信号|逻辑|条件|风险)',
        "等待基本面证据复核",
    ),
    (
        r'(?:明天|明日|今日|今天|后续|下一交易日)\s*'
        r'(?:(?:我|本人|我们|用户|投资者|客户|系统|交易员|模型)\s*)?'
        r'(?:(?:会|将|计划|准备|打算|拟|决定|选择|建议|应|应该|应当|需|需要|要)\s*)?'
        r'(?:择机|适时|伺机|逢低)?\s*'
        r'(?:买入|购入|建仓|入场|开仓|加仓|补仓|增持|做多|介入|参与)'
        r'(?!回报|收益|订单|成交|成本|评级|信号|逻辑|条件|风险)',
        "等待基本面证据复核",
    ),
    (
        r'(?:(?:这个|该)\s*)?(?:位置|价位|价格|区间|区域)\s*'
        r'(?:买入|购入|建仓|入场|开仓|加仓|补仓|增持|做多|介入|参与)\s*'
        r'(?:即可|为宜|较合适|合理)',
        "等待基本面证据复核",
    ),
]
_SANITIZE_NO_POSITION = [
    (
        r'(?P<prefix>^\s*[^。！？；;，,\n]{0,24}?)'
        r'(?:止损|止盈)(?:并|或)?\s*(?:离场|退出|出局)\s*'
        r'(?:机制|策略|规则|条件|标准|纪律|方案)\s*'
        r'[^。！？；;，,\n]{0,16}?'
        r'(?:执行|实施|采用|启用|启动|落实|下达|生效|遵守)',
        r'\g<prefix>该持仓动作不适用，保持观察',
    ),
    (
        r'(?P<prefix>^\s*(?:(?:[-*>]|\d+[.)、])\s*)?'
        r'[^。！？；;，,\n]{0,24}?)'
        r'(?:采取|执行|进行|使用|采用|部署|建立|启动|制定|设置)\s*'
        r'[^。！？；;，,\n]{0,12}?'
        r'(?:止损|止盈)(?:并|或)?\s*(?:离场|退出|出局)\s*'
        r'(?:机制|策略|规则|条件|标准|纪律|方案)',
        r'\g<prefix>该持仓动作不适用，保持观察',
    ),
    (
        r'(?:建议|推荐|立即|立刻|应该|应当|应|需要|需|务必|必须|'
        r'坚决|无条件|果断)\s*'
        r'(?:降险(?:或\s*离场)?|离场)'
        r'(?!\s*(?:机制|策略|规则|条件|标准|纪律|方案|风险|信号|概念|术语|'
        r'效果|有效|失效|尚未|未出现|的?(?:历史|概率|统计|回测)))',
        '该持仓动作不适用，保持观察',
    ),
    (
        r'(?:(?:我|本人|我们|用户|投资者|客户|系统|交易员|模型|风控|投资委员会)\s*)?'
        r'(?:(?:当前|现在|目前|最终|下一步)\s*)?'
        r'(?:(?:建议|推荐|请|立即|立刻|应该|应当|应|需要|需|务必|必须|坚决|'
        r'无条件|果断|可|可以|允许|计划|准备|打算|拟|将|要|宜|适宜|主张|决定|'
        r'选择|考虑|倾向于|倾向|首选|最好|继续|维持|后续宜|后续可)\s*)+'
        r'(?:(?:制定|设置|建立|启动|部署|采取|执行|进行|使用|采用)\s*)?'
        r'[^。！？；;，,\n]{0,12}?'
        r'(?:止损|止盈)(?:并|或)?\s*(?:离场|退出|出局)\s*'
        r'(?:机制|策略|规则|条件|标准|纪律|方案)',
        '该持仓动作不适用，保持观察',
    ),
    (
        rf'(?P<label>{_ACTION_LABEL_CONTEXT})'
        r'(?:(?:先|直接|立即|立刻|建议|推荐|执行|进行|采取|使用|采用|应|应该|'
        r'需|需要|务必|必须|可|可以|允许|计划|准备|打算|拟|将|要|宜|适宜|主张|决定|选择)\s*)*'
        r'(?:止损|止盈)(?:并|或)?\s*(?:离场|退出|出局)\s*'
        r'(?:机制|策略|规则|条件|标准|纪律|方案)',
        r'\g<label>该持仓动作不适用，保持观察',
    ),
    (
        r'(?:(?:我|本人|我们|用户|投资者|客户|系统|交易员|模型|风控|投资委员会)\s*)?'
        r'(?:(?:当前|现在|目前|最终|下一步)\s*)?'
        r'(?:(?:建议|推荐|立即|立刻|应该|应当|应|需要|需|务必|必须|可|可以|'
        r'允许|计划|准备|打算|拟|将|要|宜|适宜|主张|决定|选择)\s*)+'
        r'(?:(?:采取|执行|进行|使用|采用)\s*)*'
        r'(?:止损|止盈)(?:并|或)?\s*(?:离场|退出|出局)\s*'
        r'(?:机制|策略|规则|条件|标准|纪律|方案)',
        '该持仓动作不适用，保持观察',
    ),
    (
        r'(?P<scenario>(?:右侧|左侧)(?:持仓|建仓)(?:者|后)?\s*[:：]\s*)'
        r'(?:(?:先|直接|立即|立刻|建议|执行|应|应该|需|需要)\s*)*'
        r'(?:减仓|清仓|止盈|卖出|止损)'
        r'(?!上限|条件|额度|限制|规则|禁令|红线|线|位|价|比例|阈值|'
        r'失效|压力|信号|逻辑|风险|机制|纪律|参考)',
        r'\g<scenario>该持仓动作不适用，保持观察',
    ),
    (
        r'(?:(?:建议|立即|立刻|应该|应当|应|需要|需|执行|进行|采取)\s*)?'
        r'(?:止损|止盈)(?:并|或)?\s*(?:离场|退出|出局)'
        r'(?!\s*(?:机制|风险|信号|概念|术语|策略|规则|条件|标准|纪律|'
        r'方案|效果|有效|失效|尚未|未出现|的?(?:历史|概率|统计|回测)))',
        '该持仓动作不适用，保持观察',
    ),
    (
        r'(?:(?:我|本人|我们|本基金|本账户|系统|模型|风控|投资委员会)\s*)?'
        r'(?:赞成|支持|同意|认可)\s*'
        r'(?:减仓|清仓|止盈|卖出|止损)'
        r'(?!上限|条件|额度|限制|规则|禁令|红线|线|位|价|比例|阈值|'
        r'失效|压力|信号|逻辑|风险|机制|纪律|参考|评级)',
        '该持仓动作不适用，保持观察',
    ),
    (
        r'(?:提交|创建|下达|执行|发出|挂出)\s*'
        r'(?:一笔|新的)?\s*(?:卖出|减仓|清仓|止损|止盈)订单'
        r'(?:\s*(?:数量|金额|规模)?\s*\d+(?:\.\d+)?\s*(?:股|手|元|万元|亿元)?)?',
        '该持仓动作不适用，保持观察',
    ),
    (
        r'(?:最终建议|系统指令|当前操作|下一步)\s*[:：]\s*'
        r'(?:提交|创建|下达|执行|发出|挂出)?\s*'
        r'(?:卖出|减仓|清仓|止损|止盈)订单'
        r'(?:\s*(?:数量|金额|规模)?\s*\d+(?:\.\d+)?\s*(?:股|手|元|万元|亿元)?)?',
        '该持仓动作不适用，保持观察',
    ),
    (
        rf'(?P<label>{_ACTION_LABEL_CONTEXT})'
        r'(?:减仓|清仓|止盈|卖出|止损)'
        r'(?!上限|条件|额度|限制|规则|禁令|红线|线|位|价|比例|阈值|失效|压力|信号|逻辑|风险|机制|纪律|参考)',
        r'\g<label>该持仓动作不适用，保持观察',
    ),
    (
        rf'{_ACTION_EXPLICIT_VERDICT_PREFIX}'
        r'(?:减仓|清仓|止盈|卖出|止损)'
        r'(?!上限|条件|额度|限制|规则|禁令|红线|线|位|价|比例|阈值|失效|压力|信号|逻辑|风险|机制|纪律|参考)',
        r'\g<directive>该持仓动作不适用，保持观察',
    ),
    (
        rf'{_ACTION_CONDITIONAL_SUBJECT_PREFIX}'
        r'(?:减仓|清仓|止盈|卖出|止损)'
        r'(?!上限|条件|额度|限制|规则|禁令|红线|线|位|价|比例|阈值|失效|压力|信号|逻辑|风险|机制|纪律|参考)',
        '该持仓动作不适用，保持观察',
    ),
    (
        rf'{_ACTION_CAUSAL_PREFIX}\s*'
        rf'{_ACTION_CAUSAL_ACTOR_AND_MODAL}'
        r'(?:减仓|清仓|止盈|卖出|止损)'
        r'(?!上限|条件|额度|限制|规则|禁令|红线|线|位|价|比例|阈值|失效|压力|信号|逻辑|风险|机制|纪律|参考)',
        '该持仓动作不适用，保持观察',
    ),
    (
        r'(?P<holding_condition>(?<!何)(?:后|时|则)\s*)'
        r'(?:再\s*)?'
        r'(?:(?:用户|投资者|客户|持币者|大家)\s*)?'
        r'(?:(?:可|可以|建议|推荐|考虑|应当|应该|应|需|需要|计划|准备|决定|选择)\s*)?'
        r'(?:减仓|清仓|止盈|卖出|止损)'
        r'(?!上限|条件|额度|限制|规则|禁令|红线|线|位|价|比例|阈值|失效|压力|信号|逻辑|风险|机制|纪律|参考)',
        r'\g<holding_condition>该持仓动作不适用，保持观察',
    ),
    (
        rf'{_ACTION_EXECUTABLE_CONTEXT}'
        r'(?:减仓|清仓|止盈|卖出|止损)'
        rf'{_ACTION_MARKDOWN_SUFFIX}'
        r'(?!上限|条件|额度|限制|规则|禁令|红线|线|位|价|比例|阈值|失效|压力|信号|逻辑|风险|机制|纪律|参考)',
        '该持仓动作不适用，保持观察',
    ),
    (
        r'(?:投资者|用户|风险控制方面|仓位管理方面|操作上|策略上)?\s*'
        r'(?:可以|可|建议|推荐|应当|应该|必须|务必|需要|需|请|主张|适宜|后续宜|宜|倾向于|倾向)\s*'
        r'(?:(?:在|于)\s*[^。！？；;，,\n]{1,24}\s*)?'
        r'(?:(?:考虑|小幅|适当|适度|逐步|分批|部分|酌情|果断|尽快|立即|择机|适时|伺机)\s*)*'
        r'(?:执行|进行|采取)?\s*'
        r'(?:减仓|清仓|止盈|卖出|止损)'
        r'(?!上限|条件|额度|限制|规则|禁令|线|位|价|比例|阈值|失效|压力|信号|逻辑|风险|机制|纪律|参考)',
        '该持仓动作不适用，保持观察',
    ),
    # ── [P0-2] Replace action suggestions but preserve field names ──
    # Field names (止损价, 止损位, 止损条件, 止损红线, 止损线) are preserved by
    # using negative lookahead: (?!价|位|条件|线|红线) after each keyword.
    #
    # Replacement text: short sentence "该持仓动作不适用，保持观察"

    (
        r'(?:将|把)?(?:减仓|清仓|止盈|卖出)(?:额度|比例|上限|限制)'
        r'(?!字段|规则|阈值|说明)'
        r'(?:提高|提升|增加|调整|调高|设定|设置|设为|设成)'
        rf'(?:至|到|为|[:：])?\s*{_ACTION_SIZE_VALUE}',
        '该持仓动作不适用，保持观察',
    ),
    (
        r'(?:建议|可|可以|应该|应当|应|需要|需|执行|进行|采取)?'
        r'(?:动态)?(?:调整|提高|提升|增加|调高|设定|设置)'
        r'(?:减仓|清仓|止盈|卖出)(?:额度|比例|上限|限制)',
        '该持仓动作不适用，保持观察',
    ),
    # ── Executable sell sizing; consume the full phrase to avoid leaving
    # dangling text such as "比例为50%" after the action is removed. ──
    (
        r'(?:建议|可|可以|应该|应当|应|需要|需|执行|进行|采取)(?:将|把)'
        r'(?:减仓|清仓|止盈|卖出)(?:额度|比例|上限|限制)'
        r'(?:提高|提升|增加|调整|调高|设定|设置|设为|设成)?'
        rf'(?:至|到|为|[:：])?\s*{_ACTION_SIZE_VALUE}',
        '该持仓动作不适用，保持观察',
    ),
    (
        r'(?:建议|可|可以|应该|应当|应|需要|需|执行|进行|采取)'
        r'(?:小幅|适当|适度|逐步|分批|部分|酌情|果断|尽快|立即)?'
        r'(?:减仓|清仓|止盈|卖出)(?:额度|比例)'
        rf'(?:为|[:：]|不超过|不高于|至少|不低于|不小于|≤|<|≥|>)?\s*{_ACTION_SIZE_VALUE}',
        '该持仓动作不适用，保持观察',
    ),
    (
        r'(?:减仓|清仓|止盈|卖出)(?:额度|比例)'
        r'(?!字段|上限|限制|规则|阈值|说明)'
        r'(?:为|是|设为|设置为|控制在|控制为|[:：]|不超过|不高于|'
        r'至少|不低于|不小于|≤|<|≥|>)?\s*'
        rf'{_ACTION_SIZE_VALUE}',
        '该持仓动作不适用，保持观察',
    ),
    (
        r'(?:减仓|清仓|止盈|卖出)(?:额度|比例|上限|限制)'
        r'(?!字段|规则|阈值|说明)'
        r'(?:的)?(?:建议|推荐|计划|目标)'
        r'(?:为|是|设为|设置为|控制在|控制为|提高至|提升至|增加至|'
        r'不超过|不高于|至少|不低于|不小于|≤|<|≥|>|[:：])\s*'
        rf'{_ACTION_SIZE_VALUE}',
        '该持仓动作不适用，保持观察',
    ),
    # ── Specific action phrases (adverb + action) ──
    (r'建议止损离场', '该持仓动作不适用，保持观察'),
    (r'建议减仓', '该持仓动作不适用，保持观察'),
    (r'建议清仓', '该持仓动作不适用，保持观察'),
    (r'建议止盈', '该持仓动作不适用，保持观察'),
    (r'建议止损', '该持仓动作不适用，保持观察'),
    (r'建议卖出', '该持仓动作不适用，保持观察'),
    (r'立即清仓', '该持仓动作不适用，保持观察'),
    (r'立即减仓', '该持仓动作不适用，保持观察'),
    (r'立即止损', '该持仓动作不适用，保持观察'),
    (r'立即卖出', '该持仓动作不适用，保持观察'),
    (r'可以减仓', '该持仓动作不适用，保持观察'),
    (r'可以清仓', '该持仓动作不适用，保持观察'),
    (r'可以止损', '该持仓动作不适用，保持观察'),
    (r'可以卖出', '该持仓动作不适用，保持观察'),
    (r'应该减仓', '该持仓动作不适用，保持观察'),
    (r'应该清仓', '该持仓动作不适用，保持观察'),
    (r'应该止损', '该持仓动作不适用，保持观察'),
    (r'应该卖出', '该持仓动作不适用，保持观察'),
    (r'需要减仓', '该持仓动作不适用，保持观察'),
    (r'需要清仓', '该持仓动作不适用，保持观察'),
    (r'需要止损', '该持仓动作不适用，保持观察'),
    (r'需要卖出', '该持仓动作不适用，保持观察'),
    (r'果断止损', '该持仓动作不适用，保持观察'),
    (r'适度减仓', '该持仓动作不适用，保持观察'),
    (r'尽快清仓', '该持仓动作不适用，保持观察'),
    (r'逐步减仓', '该持仓动作不适用，保持观察'),
    # Conditional or modified imperatives. Match the action phrase only so
    # clauses such as "跌破支撑" remain readable, while noun fields such as
    # "减仓上限/条件" are not touched.
    (
        r'(?:建议|则|应当|应|需|务必|可|可以|后|时|就|考虑)'
        r'(?:考虑|小幅|适当|适度|逐步|分批|部分|酌情|果断|尽快|立即)?'
        r'(?:执行|进行|采取)?减仓(?!上限|条件|额度|限制|规则|禁令)',
        '该持仓动作不适用，保持观察',
    ),
    (
        r'(?:建议|推荐|可|可以|考虑|应|应该|应当|需|需要|计划|准备|决定|选择|执行|进行|采取)'
        r'[^。！？；;，,\n]{0,12}?'
        r'(?:减仓|清仓|止盈|卖出|止损)'
        r'(?!上限|条件|额度|限制|规则|禁令|线|位|价|比例|阈值|失效|风险|机制|纪律|参考)',
        '该持仓动作不适用，保持观察',
    ),
    (
        r'(?:建议|则|应当|应|需|务必|可|可以|后|时|就|考虑)'
        r'(?:考虑|小幅|适当|适度|逐步|分批|部分|酌情|果断|尽快|立即)?'
        r'(?:执行|进行|采取)?清仓(?!上限|条件|额度|限制|规则|禁令)',
        '该持仓动作不适用，保持观察',
    ),
    (
        r'(?:建议|则|应当|应|需|务必|可|可以|后|时|就|考虑)'
        r'(?:考虑|小幅|适当|适度|逐步|分批|部分|酌情|果断|尽快|立即)?'
        r'(?:执行|进行|采取)?止盈(?!上限|条件|额度|限制|规则|禁令)',
        '该持仓动作不适用，保持观察',
    ),
    (
        r'(?:建议|则|应当|应|需|务必|可|可以|后|时|就|考虑)'
        r'(?:考虑|小幅|适当|适度|逐步|分批|部分|酌情|果断|尽快|立即)?'
        r'(?:执行|进行|采取)?卖出(?!上限|条件|额度|限制|规则|禁令)',
        '该持仓动作不适用，保持观察',
    ),
    (
        r'(?:小幅|适当|适度|逐步|分批|部分|酌情|果断|尽快|立即)'
        r'(?:执行|进行|采取)?'
        r'(?:减仓|清仓|止盈|卖出)(?!上限|条件|额度|限制|规则|禁令)',
        '该持仓动作不适用，保持观察',
    ),
    # ── Compound action phrases that must be fully replaced ──
    (
        r'(?:全部|全数)\s*(?:卖出|清仓|减仓|止盈)'
        r'|(?:(?:剩余|当前|现有|全部|所有)\s*)?'
        r'(?:仓位|持仓|股票|股份)\s*(?:全部|全数)?\s*'
        r'(?:卖出|清仓|减仓|止盈)'
        r'(?!上限|条件|额度|限制|规则|禁令|线|位|价|比例|阈值|压力|信号|逻辑|风险)',
        '该持仓动作不适用，保持观察',
    ),
    (
        r'(?:止损|止盈)(?:离场|退出|出局)'
        r'(?!\s*(?:机制|风险|信号|概念|术语|策略|规则|条件|标准|纪律|'
        r'方案|效果|有效|失效|尚未|未出现|的?(?:历史|概率|统计|回测)))',
        '该持仓动作不适用，保持观察',
    ),
    # [P0-2] 触发止损 + action verb combos — must match BEFORE bare 触发止损
    (r'触发止损.{0,4}(?:离场|清仓|卖出|减仓|出局)', '该持仓动作不适用，保持观察'),
    (r'触发止损', '该持仓动作不适用，保持观察'),
    (r'减仓观察', '该持仓动作不适用，保持观察'),
    (r'清仓离场', '该持仓动作不适用，保持观察'),
    (r'清仓出局', '该持仓动作不适用，保持观察'),
    # Explicit imperative forms only.  A generic ``\w{0,4}减仓`` style
    # pattern consumed surrounding Chinese text and corrupted risk clauses.
    (r'(?:执行|进行|采取)减仓', '该持仓动作不适用，保持观察'),
    (r'(?:执行|进行|采取)清仓', '该持仓动作不适用，保持观察'),
    (r'(?:执行|进行|采取)止盈', '该持仓动作不适用，保持观察'),
    (r'(?:执行|进行|采取)卖出', '该持仓动作不适用，保持观察'),
    # Bare nouns inside risk explanations and future plans are descriptive,
    # not current executable actions.  Only sanitize an explicit action label
    # or a line whose first token is the action; imperative phrases above still
    # cover suggestions such as ``建议减仓`` and sizing instructions.
    (
        rf'{_ACTION_EXECUTABLE_CONTEXT}'
        r'(?:减仓|清仓|止盈|卖出)'
        rf'{_ACTION_MARKDOWN_SUFFIX}'
        r'(?!上限|条件|额度|限制|规则|禁令|线|位|价|比例|阈值|压力|信号|逻辑|风险)',
        '该持仓动作不适用，保持观察',
    ),
    (
        r'(?:仓位方面|风险应对|操作上|策略上|最终结论|当前结论|结论|首选|当前仓位)'
        r'\s*(?:以|偏向|倾向|为|是|宜|[:：])?\s*'
        r'(?:减仓|清仓|止盈|卖出|止损)'
        r'(?:为主|优先|作为首选)?'
        r'(?!上限|条件|额度|限制|规则|禁令|线|位|价|比例|阈值)',
        '该持仓动作不适用，保持观察',
    ),
    (
        r'(?:(?:最终建议|最终裁决|最终结论|交易建议|操作建议|当前结论)\s*[:：]?\s*)?'
        r'(?:建议|推荐|当前最好|首选(?:方案)?(?:是|为)?|我的选择(?:会)?是)?\s*'
        r'(?:以\s*)?'
        r'(?:减仓|清仓|止盈|卖出|止损)\s*'
        r'(?:为主|为宜|优先|作为首选)',
        '该持仓动作不适用，保持观察',
    ),
    (
        r'(?:这|该结论|结论|判断|证据)\s*'
        r'(?:意味着|表明|支持|指向)\s*'
        r'(?:减仓|清仓|止盈|卖出|止损)',
        '该持仓动作不适用，保持观察',
    ),
    (
        r'(?:(?:我|本人|我们|系统|模型)\s*)?'
        r'(?:投票|偏向|倾向|选择|决定)\s*'
        r'(?:减仓|清仓|止盈|卖出|止损)',
        '该持仓动作不适用，保持观察',
    ),
    (
        r'(?:明天|明日|今日|今天|后续|下一交易日)\s*'
        r'(?:(?:我|本人|我们|用户|投资者|客户|系统|交易员|模型)\s*)?'
        r'(?:(?:会|将|计划|准备|打算|拟|决定|选择|建议|应|应该|应当|需|需要|要)\s*)?'
        r'(?:择机|适时|伺机|逢高|反弹)?\s*'
        r'(?:减仓|清仓|止盈|卖出|止损)',
        '该持仓动作不适用，保持观察',
    ),
    (
        r'(?:当前)?仓位\s*(?:应|应该|需|需要|建议)?\s*'
        r'(?:减至|降至|降低至)\s*'
        rf'{_ACTION_SIZE_VALUE}',
        '该持仓动作不适用，保持观察',
    ),
    (
        r'(?:建议|推荐|可|可以|考虑)?\s*(?:逢高|反弹)?\s*高抛'
        r'(?!低吸)',
        '该持仓动作不适用，保持观察',
    ),
    # ── Standalone 止损 with negative lookahead to preserve field names ──
    # Matches: 止损, 止损离场, 止损。 but NOT 止损价, 止损位, 止损条件, 止损红线, 止损线
    (
        rf'{_ACTION_EXECUTABLE_CONTEXT}'
        rf'止损{_ACTION_MARKDOWN_SUFFIX}'
        r'(?!价|位|条件|线|红线|失效|风险|机制|纪律|参考)',
        '该持仓动作不适用，保持观察',
    ),
    (
        rf'{_ACTION_EXECUTABLE_CONTEXT}'
        r'(?:离场|退出|出局|降险)'
        rf'{_ACTION_MARKDOWN_SUFFIX}'
        r'(?!条件|规则|机制|策略|方案|风险|信号|概率|统计|历史|回测)',
        '该持仓动作不适用，保持观察',
    ),
    (
        r'(?:本次|当前|最终)?\s*(?:决定|选择)\s*'
        r'(?:减仓|清仓|止盈|卖出|止损)',
        '该持仓动作不适用，保持观察',
    ),
    # [Fix-4] No-position: HOLD should become WAIT/观察
    (r'HOLD/等待触发', 'WAIT/等待触发'),
    (r'\bHOLD\b(?!.*(?:等待|观察|条件))', 'WAIT/观察'),
    # [Fix-4] '条件减仓' should not appear as main action for no-position
    (r'(?:作为|建议).{0,6}条件减仓', '该持仓动作不适用，保持观察'),
]

_NEGATIVE_ACTION_CONTEXT = re.compile(
    r"(?:禁止|不得|不应|不建议|不允许|未允许|不推荐|未推荐|不可|不能|"
    r"不支持|不适合|并非|不是|并不|避免|无需|不要|暂不|不需(?:要)?|"
    r"不(?=(?:考虑|准备|打算|计划|选择|想|愿意|会)\s*"
    r"(?:买入|购入|建仓|开仓|入场|加仓|补仓|增持|做多|追涨|低吸|抄底|试多|介入|参与|配置|卖出|减仓|清仓|止盈|止损))|"
    r"勿|严禁|不宜|不(?=(?:首选|优先|妨|如))|"
    r"不\s*(?:买入|购入|建仓|开仓|入场|加仓|补仓|增持|做多|追涨|低吸|抄底|试多|介入|参与|配置|卖出|减仓|清仓|止盈|止损)|"
    r"无(?:明确)?(?:买入|购入|建仓|开仓|入场|加仓|增持|做多|追涨|低吸|抄底|试多|介入|参与|配置)|"
    r"(?:尚未|还没|没有|未曾)\s*(?:买|买入|购入|建仓|开仓|入场|加仓|增持|做多|追涨|低吸|抄底|试多|介入|参与|配置))"
)

_NEGATED_ACTION_TAIL = re.compile(
    r"\s*(?:(?:考虑|建议|推荐|计划|选择|打算|准备|尝试|执行|进行|采取|"
    r"确认|在|于|以|按|这个|该|位置|价位|价格|附近|区域|区间|继续|要|想|逢低|低吸|"
    r"首选|优先|值得|适合|最好|"
    r"立即|立刻|马上|贸然|盲目|轻易|随意|急于|过早|现在|当前|目前|"
    r"重仓|满仓|全仓|半仓|全部|全数|悉数|轻仓|少量|分批|"
    r"试探性|试探|小仓位|大仓位|将|把|调整|提高|提升|增加|调高|"
    r"设定|设置)\s*)*"
    r"(?:买入|购入|建仓|开仓|入场|加仓|补仓|增持|做多|追涨|低吸|抄底|试多|介入|参与|配置|卖出|减仓|清仓|止盈|止损|离场|退出|出局|降险)"
    r"(?:\s*(?:买入|购入|建仓|开仓|入场|加仓|补仓|增持|做多|追涨|低吸|抄底|试多|介入|参与|配置|卖出|减仓|清仓|止盈|止损))*"
    r"(?:额度|比例|上限|限制)?"
    r"(?:\s*(?:[/／、]|或(?:者)?)\s*"
    r"(?:(?:考虑|建议|推荐|计划|选择|打算|准备|尝试|执行|进行|采取|"
    r"在|于|继续|要|想|逢低|低吸|立即|立刻|马上)\s*)*"
    r"(?:买入|购入|建仓|开仓|入场|加仓|补仓|增持|做多|追涨|低吸|抄底|试多|介入|参与|配置|卖出|减仓|清仓|止盈|止损|离场|退出|出局|降险))*"
)


def _mask_negated_action_contexts(clause: str) -> tuple[str, dict[str, str]]:
    """Mask negated actions while leaving earlier/later positive actions visible."""
    chunks: list[str] = []
    protected: dict[str, str] = {}
    cursor = 0
    token_index = 0
    while True:
        negative_match = _NEGATIVE_ACTION_CONTEXT.search(clause, cursor)
        if negative_match is None:
            chunks.append(clause[cursor:])
            break
        protected_end = negative_match.end()
        negated_action = _NEGATED_ACTION_TAIL.match(clause[protected_end:])
        if negated_action:
            protected_end += negated_action.end()
        token = f"__TA_NEGATED_ACTION_{token_index}__"
        chunks.append(clause[cursor:negative_match.start()])
        chunks.append(token)
        protected[token] = clause[negative_match.start():protected_end]
        cursor = protected_end
        token_index += 1
    return "".join(chunks), protected


def _restore_negated_action_contexts(
    clause: str,
    protected: dict[str, str],
) -> str:
    for token, original in protected.items():
        clause = clause.replace(token, original)
    return clause


_COMPLETED_PURCHASE_ACTION = re.compile(r"买入|购入|买(?!入)")
_COMPLETED_PURCHASE_MARKER = re.compile(
    r"(?:用户|我|本人)?\s*"
    r"(?:已经|已|刚刚|刚|昨天|昨日|前天|上周(?:[一二三四五六日天])?|"
    r"上个月|去年|(?:\d+|[一二两三四五六七八九十]+)天前)\s*"
    r"(?:用户|我|本人)?\s*"
    r"(?:(?:在|于)\s*\d+(?:\.\d+)?\s*元?\s*)?$"
)
_PROSPECTIVE_PURCHASE_MARKER = re.compile(
    r"(?:建议|推荐|计划|打算|准备|考虑|可以|可|应当|应该|等待|若|如果|"
    r"目标|拟|预计)"
)


def _mask_completed_purchase_facts(text: str) -> tuple[str, dict[str, str]]:
    """Protect completed purchase/cost facts from action sanitizers."""
    chunks: list[str] = []
    protected: dict[str, str] = {}
    cursor = 0
    token_index = 0
    boundaries = ("。", "，", ",", "；", ";", "！", "!", "？", "?", "\n")
    for match in _COMPLETED_PURCHASE_ACTION.finditer(text):
        prefix = text[:match.start()]
        clause_start = max(prefix.rfind(mark) for mark in boundaries) + 1
        clause_prefix = text[clause_start:match.start()]
        suffix = text[match.end():]
        prospective = bool(_PROSPECTIVE_PURCHASE_MARKER.search(clause_prefix))
        prospective_suffix = bool(
            re.match(
                r"\s*(?:计划|信号|建议|条件|机会|意图|动作|理由|资格|"
                r"能力|方案|盘点|评级)",
                suffix,
            )
        )
        completed_marker = bool(_COMPLETED_PURCHASE_MARKER.search(clause_prefix))
        priced_completed_fact = bool(
            not prospective
            and re.search(
                r"(?:用户|我|本人)\s*(?:在|于)\s*\d+(?:\.\d+)?\s*元?\s*$",
                clause_prefix,
            )
            and re.match(r"\s*(?:了\s*)?\d+(?:\.\d+)?\s*(?:股|手)(?:\b|$)", suffix)
        )
        purchase_fact_field = bool(
            re.match(r"\s*(?:成本|均价|记录|日期|时间)", suffix)
        )
        if not (
            (completed_marker and not prospective_suffix)
            or priced_completed_fact
            or (purchase_fact_field and not prospective)
        ):
            continue
        token = f"__TA_COMPLETED_PURCHASE_{token_index}__"
        chunks.append(text[cursor:match.start()])
        chunks.append(token)
        protected[token] = match.group()
        cursor = match.end()
        token_index += 1
    chunks.append(text[cursor:])
    return "".join(chunks), protected


def _restore_completed_purchase_facts(
    text: str,
    protected: dict[str, str],
) -> str:
    for token, original in protected.items():
        text = text.replace(token, original)
    return text


_THIRD_PARTY_ENTRY_FACT = re.compile(
    r'(?:公司(?:董事会)?|本公司|董事会|控股股东|实际控制人|大股东|股东|'
    r'员工持股计划|员工持股平台|高管|董监高|管理层|董事|监事|'
    r'北向资金|主力资金|机构(?:资金)?|游资|(?<!本)基金|外资)'
    r'(?![^。！？；;，,\n]{0,30}(?:建议|推荐|可|可以|考虑|应|应该|应当|等待|若|如果)'
    r'[^。！？；;，,\n]{0,12}(?:买入|增持))'
    r'(?=[^。！？；;，,\n]{0,36}(?:买入|增持|加仓))'
    r'(?=[^。！？；;，,\n]{0,36}(?:今日|今天|明日|明天|昨日|昨天|已|已经|累计|净|'
    r'决定|公告|宣布|披露|拟|计划|将))'
    r'[^。！？；;，,\n]{0,36}?'
    r'(?:买入|增持|加仓)(?!评级|信号|逻辑|建议)'
    r'(?:了|股份|股票|公司股票|[\d,.万亿元股手%]*)?',
    re.IGNORECASE,
)

_THIRD_PARTY_EXIT_FACT = re.compile(
    r'(?:公司(?:董事会)?|本公司|董事会|控股股东|实际控制人|大股东|股东|'
    r'员工持股计划|员工持股平台|高管|董监高|管理层|董事|监事|'
    r'北向资金|主力资金|机构(?:资金)?|游资|(?<!本)基金|外资)'
    r'(?=[^。！？；;，,\n]{0,36}(?:卖出|减持|减仓|清仓))'
    r'(?=[^。！？；;，,\n]{0,36}(?:今日|今天|明日|明天|昨日|昨天|已|已经|累计|净|'
    r'决定|公告|宣布|披露|拟|计划|将))'
    r'[^。！？；;，,\n]{0,36}?'
    r'(?:卖出|减持|减仓|清仓)(?!评级|信号|逻辑|建议)'
    r'(?:了|股份|股票|公司股票|[\d,.万亿元股手%]*)?',
    re.IGNORECASE,
)

_ANALYTICAL_EXIT_FACT = re.compile(
    r'(?:'
    # Historical and backtest results describe past performance; they are not
    # an instruction for the current account.
    r'(?:历史上|历史回测|回测中|策略回测中|策略回测\s*[:：]?)'
    r'(?![^。！？；;\n]{0,36}(?:当前|现在|目前|今日|今天|明日|明天|最终|'
    r'下一步|因此|所以|由此|据此|建议|推荐|应当|应该|立即执行))'
    r'[^。！？；;\n]{0,36}?'
    r'(?:(?:立即|立刻|尽快)?(?:全部|全数|悉数)?\s*'
    r'(?:清仓|卖出离场|止损离场|清仓离场))'
    r'[^。！？；;\n]{0,36}?(?:回撤|胜率|收益|回报|表现|统计|概率|方案|策略)'
    r'|'
    # Keep common report phrasing explicit.  This avoids relying on the
    # optional action modifiers above to consume an entire backtest sentence.
    r'(?:历史回测|策略回测中)'
    r'(?![^。！？；;\n]{0,36}(?:当前|现在|目前|今日|今天|明日|明天|最终|'
    r'建议|推荐|应当|应该|立即执行))'
    r'[^。！？；;\n]{0,48}?'
    r'(?:立即清仓|立刻清仓|全部卖出离场|止损离场|清仓离场)'
    r'[^。！？；;\n]{0,48}?(?:回撤|胜率|收益|回报|表现|统计|概率|方案|策略)'
    r'|'
    # A completed historical exit is evidence, not a fresh order. The exit
    # itself must be complete and terminal; a prior review/analysis is not
    # enough to mask a later current exit recommendation.
    r'(?:昨日|昨天|此前|之前)\s*(?:已|已经)\s*'
    r'(?:完成(?:了)?\s*)?'
    r'(?:(?:立即|立刻|尽快)?(?:全部|全数|悉数)?\s*)?'
    r'(?:清仓|卖出离场|止损离场|清仓离场)\s*'
    r'(?:一半|部分|半仓)(?=\s*(?:[，,。！？；;\n]|$))'
    r'|'
    r'(?:昨日|昨天|此前|之前)\s*(?:已|已经)\s*'
    r'(?:完成(?:了)?\s*)?'
    r'(?:(?:立即|立刻|尽快)?(?:全部|全数|悉数)?\s*'
    r'(?:清仓|卖出离场|止损离场|清仓离场))'
    r'(?=\s*(?:[。！？；;\n]|$))'
    r'|'
    # Quoted action names used by a model/rule definition are terminology.
    r'(?:模型|系统|规则|枚举)[^。！？；;\n]{0,24}[“\"]'
    r'[^”\"]*(?:立即清仓|立刻清仓|全部卖出离场|止损离场)[^”\"]*[”\"]'
        # A bare “action” after the quote can be a current command, such as
        # ``系统指令：“立即清仓”作为最终动作``. Only preserve explicit
        # terminology/definition contexts so that command still reaches the
        # Risk Level sanitizer.
        r'[^。！？；;\n]{0,32}?(?:定义(?:为)?|属于|表示|指代|等级|Risk\s*Level|风险级别)'
    r')',
    re.IGNORECASE,
)

_CURRENT_EXIT_AFTER_ANALYTICAL_PREAMBLE = re.compile(
    r'(?:当前|现在|目前|今日|今天|明日|明天|最终|下一步|因此|所以|由此|据此|'
    r'但|不过)\s*'
    r'(?:(?:建议|推荐|应当|应该|需要|需|计划|决定|立即|立刻|马上)\s*)*'
    r'(?:减仓|清仓|止盈|卖出(?:离场)?|止损(?:离场)?)'
)


def _mask_third_party_entry_facts(text: str) -> tuple[str, dict[str, str]]:
    protected: dict[str, str] = {}

    def _replace(match: re.Match[str]) -> str:
        token = f"__TA_THIRD_PARTY_ENTRY_{len(protected)}__"
        protected[token] = match.group(0)
        return token

    return _THIRD_PARTY_ENTRY_FACT.sub(_replace, text), protected


def _restore_third_party_entry_facts(text: str, protected: dict[str, str]) -> str:
    for token, original in protected.items():
        text = text.replace(token, original)
    return text


def _mask_third_party_exit_facts(text: str) -> tuple[str, dict[str, str]]:
    protected: dict[str, str] = {}

    def _replace(match: re.Match[str]) -> str:
        token = f"__TA_THIRD_PARTY_EXIT_{len(protected)}__"
        protected[token] = match.group(0)
        return token

    return _THIRD_PARTY_EXIT_FACT.sub(_replace, text), protected


def _restore_third_party_exit_facts(text: str, protected: dict[str, str]) -> str:
    for token, original in protected.items():
        text = text.replace(token, original)
    return text


def _mask_analytical_exit_facts(text: str) -> tuple[str, dict[str, str]]:
    """Protect historical exit evidence and action terminology from rewrites."""
    protected: dict[str, str] = {}

    def _replace(match: re.Match[str]) -> str:
        # A long historical/backtest preamble may otherwise consume a current
        # exit inside the same match. Current execution always wins over the
        # analytical exception, regardless of preamble length.
        line_end = match.string.find("\n", match.start())
        if line_end < 0:
            line_end = len(match.string)
        if _CURRENT_EXIT_AFTER_ANALYTICAL_PREAMBLE.search(
            match.string[match.start():line_end]
        ):
            return match.group(0)
        token = f"__TA_ANALYTICAL_EXIT_{len(protected)}__"
        protected[token] = match.group(0)
        return token

    return _ANALYTICAL_EXIT_FACT.sub(_replace, text), protected


def _restore_analytical_exit_facts(text: str, protected: dict[str, str]) -> str:
    for token, original in protected.items():
        text = text.replace(token, original)
    return text


_RISK_LEVEL_THREE_CONDITIONAL_STOP = re.compile(
    r'(?:若|如果|假如|一旦|当)[^。！？；;，,\n]{0,52}?'
    r'(?:跌破|失守|触发(?:止损)?|达到止损)[^。！？；;，,\n]{0,40}?'
    r'(?:[，,]\s*|则\s*)'
    r'(?:(?:建议|推荐|应|应该|需|需要|立即|立刻|执行)\s*)?'
    r'(?:止损(?:并|或)?\s*离场|止损离场)'
)


_RISK_LEVEL_TWO_CONDITIONAL_REDUCTION = re.compile(
    r'(?:若|如果|假如|一旦|当)[^。！？；;，,\n]{0,52}?'
    r'(?:跌破|失守|触发|主力[^。！？；;，,\n]{0,16}流出|'
    r'资金[^。！？；;，,\n]{0,16}流出|趋势转弱|公告出现重大利空)'
    r'[^。！？；;，,\n]{0,40}?'
    r'(?:[，,]\s*|则\s*)'
    r'(?:(?:建议|推荐|应|应该|需|需要|执行)\s*)?'
    r'(?:条件\s*)?(?:减半仓|减仓)'
)


def _mask_authorized_level_two_reductions(
    text: str,
) -> tuple[str, dict[str, str]]:
    """Keep explicit conditional reductions that Risk Level 2 authorizes."""
    protected: dict[str, str] = {}

    def _replace(match: re.Match[str]) -> str:
        token = f"__TA_RISK_LEVEL_TWO_REDUCTION_{len(protected)}__"
        protected[token] = match.group(0)
        return token

    return _RISK_LEVEL_TWO_CONDITIONAL_REDUCTION.sub(_replace, text), protected


def _restore_authorized_level_two_reductions(
    text: str,
    protected: dict[str, str],
) -> str:
    for token, original in protected.items():
        text = text.replace(token, original)
    return text


def _mask_authorized_level_three_stops(
    text: str,
) -> tuple[str, dict[str, str]]:
    """Keep Risk Level 3 conditional stops while sanitizing clear orders."""
    protected: dict[str, str] = {}

    def _replace(match: re.Match[str]) -> str:
        token = f"__TA_RISK_LEVEL_THREE_STOP_{len(protected)}__"
        protected[token] = match.group(0)
        return token

    return _RISK_LEVEL_THREE_CONDITIONAL_STOP.sub(_replace, text), protected


def _restore_authorized_level_three_stops(
    text: str,
    protected: dict[str, str],
) -> str:
    for token, original in protected.items():
        text = text.replace(token, original)
    return text


_ACTION_ENUM_REFERENCE = re.compile(
    r'(?im)(?:'
    r'(?:^\s*[-*]\s*)?\*{0,2}'
    r'(?:分类标签|动作枚举|决策枚举|可选动作|动作集合|动作标签)\s*'
    r'(?:[:：]\s*\*{0,2}|\*{0,2}\s*[:：]\s*\*{0,2})'
    r'(?:BUY\s*[/／]\s*SELL\s*[/／]\s*HOLD|'
    r'ENTER\s*[/／]\s*WAIT\s*[/／]\s*HOLD\s*[/／]\s*REDUCE\s*[/／]\s*EXIT)'
    r'\*{0,2}'
    r'|'
    r'(?:BUY\s*[/／]\s*SELL\s*[/／]\s*HOLD|'
    r'ENTER\s*[/／]\s*WAIT\s*[/／]\s*HOLD\s*[/／]\s*REDUCE\s*[/／]\s*EXIT)'
    r'\s*(?:三分类标签|分类标签|动作枚举|决策枚举|可选动作|动作集合|动作标签)'
    r')'
)


def _mask_action_enum_references(text: str) -> tuple[str, dict[str, str]]:
    """Protect explicit classification enums, not arbitrary slash actions."""
    protected: dict[str, str] = {}

    def _replace(match: re.Match[str]) -> str:
        token = f"__TA_ACTION_ENUM_{len(protected)}__"
        protected[token] = match.group(0)
        return token

    return _ACTION_ENUM_REFERENCE.sub(_replace, text), protected


def _restore_action_enum_references(
    text: str,
    protected: dict[str, str],
) -> str:
    for token, original in protected.items():
        text = text.replace(token, original)
    return text


def _sanitize_fundamental_entry_actions(
    text: str,
    replacement_text: str = "等待基本面证据复核",
) -> tuple[str, list[str]]:
    """Downgrade positive entry clauses without corrupting guardrail text."""
    text, protected_enums = _mask_action_enum_references(text)
    text, protected_third_party_facts = _mask_third_party_entry_facts(text)
    text, protected_analytical_facts = _mask_analytical_entry_facts(text)
    text, protected_discussion = _mask_non_executable_entry_discussion(text)
    changes: list[str] = []
    sanitized_lines: list[str] = []
    for line in text.splitlines(keepends=True):
        if re.search(r"禁止动作\s*[:：]", line):
            sanitized_lines.append(line)
            continue
        parts = re.split(
            r"([。！？；;，,、]|(?:或者|或)(?=(?:后续)?(?:建议|可以|可|应当|应该)))",
            line,
        )
        for index in range(0, len(parts), 2):
            clause = parts[index]
            if not clause:
                continue
            scoped_parts = re.split(
                r"((?:但(?:是)?|不过|然而|可是|却|而(?!非)|同时|并且|并(?!非|不)|且))",
                clause,
            )
            for scoped_index in range(0, len(scoped_parts), 2):
                scoped_clause = scoped_parts[scoped_index]
                if not scoped_clause:
                    continue
                # A double negative authorizes the entry action (for example
                # ``并非不能建仓``). Handle it before ordinary negative
                # guardrails are masked and restored verbatim.
                scoped_clause, double_negation_count = re.subn(
                    r"(?:(?:并)?(?:不是|并非)\s*"
                    r"(?:不能|不可|不可以|不应|不应该|不该|不建议|不推荐|"
                    r"不允许|不宜|不需要|无需|不适合)|"
                    r"(?:不能|不可|不可以|没有|并非没有)\s*不)\s*"
                    r"(?:执行|进行|采取)?\s*"
                    r"(?:建仓|买入|购入|入场|加仓|补仓|追涨|低吸|抄底|"
                    r"试多|介入|参与|配置)",
                    replacement_text,
                    scoped_clause,
                )
                if double_negation_count:
                    changes.extend(
                        [
                            f"入场门禁买入动作已降级为「{replacement_text}」"
                        ]
                        * double_negation_count
                    )
                # ``不妨`` / ``不如`` / ``不排除`` are lexical positive
                # recommendations,
                # not negations. Handle them before the generic negative-mask
                # pass so they cannot survive a failed entry gate.
                scoped_clause, lexical_recommendation_count = re.subn(
                    r"(?:不妨|不如|不排除)\s*"
                    r"(?:(?:考虑|选择|建议|推荐)\s*)?"
                    r"(?:买入|购入|建仓|入场|开仓|试仓|试多|低吸|加仓|补仓|"
                    r"增持|介入|参与|抄底|追涨|配置(?:仓位)?)"
                    r"(?!价|价格|条件|区间|计划|触发|信号|逻辑|评级|点|上限|"
                    r"额度|比例|限制|禁令|的?可行性|的?可能性)",
                    replacement_text,
                    scoped_clause,
                )
                if lexical_recommendation_count:
                    changes.extend(
                        [
                            f"入场门禁买入动作已降级为「{replacement_text}」"
                        ]
                        * lexical_recommendation_count
                    )
                scoped_clause, protected = _mask_negated_action_contexts(
                    scoped_clause
                )
                # A present-tense entry order can sit immediately before a
                # future stop plan (for example ``现在买入后若跌破...``).
                # Run this after negated guardrails are protected, so
                # ``不要立即买入`` remains a factual restriction rather than
                # being rewritten as an instruction.
                scoped_clause, current_entry_with_plan_count = (
                    _CURRENT_ENTRY_BEFORE_FUTURE_PLAN.subn(
                        f"{replacement_text}；后续持仓",
                        scoped_clause,
                    )
                )
                if current_entry_with_plan_count:
                    changes.extend(
                        [
                            f"入场门禁买入动作已降级为「{replacement_text}」"
                        ]
                        * current_entry_with_plan_count
                    )
                scoped_clause, current_entry_count = _CURRENT_ENTRY_DIRECTIVE.subn(
                    replacement_text,
                    scoped_clause,
                )
                if current_entry_count:
                    changes.extend(
                        [
                            f"入场门禁买入动作已降级为「{replacement_text}」"
                        ]
                        * current_entry_count
                    )
                for pattern, replacement in _SANITIZE_FUNDAMENTAL_VETO_BUY:
                    def _replace_entry_action(match: re.Match[str]) -> str:
                        prefixes = "".join(
                            match.groupdict().get(name, "") or ""
                            for name in ("label", "directive", "entry_condition")
                        )
                        return f"{prefixes}{replacement_text}"

                    scoped_clause, replacement_count = re.subn(
                        pattern,
                        _replace_entry_action,
                        scoped_clause,
                    )
                    if replacement_count:
                        changes.extend(
                            [
                                f"入场门禁买入动作已降级为「{replacement_text}」"
                            ]
                            * replacement_count
                        )
                scoped_parts[scoped_index] = _restore_negated_action_contexts(
                    scoped_clause, protected
                )
            parts[index] = "".join(scoped_parts)
        sanitized_lines.append("".join(parts))
    sanitized = _restore_action_enum_references(
        "".join(sanitized_lines),
        protected_enums,
    )
    sanitized = _restore_third_party_entry_facts(
        sanitized,
        protected_third_party_facts,
    )
    sanitized = _restore_analytical_entry_facts(
        sanitized,
        protected_analytical_facts,
    )
    sanitized = _restore_non_executable_entry_discussion(
        sanitized,
        protected_discussion,
    )
    return sanitized, changes


_GENERIC_GATE_ENTRY_ACTION = re.compile(
    r'(?:(?:当前操作|最终建议|系统指令|下一步|执行方案)\s*[:：]\s*)?'
    r'(?:(?:建议|推荐|可以|可|考虑|应|应该|需要|需|计划|准备|执行|进行|采取)\s*)?'
    r'(?:条件试(?:探)?仓|确认建仓|积极建仓|轻仓试错|小仓位试错|'
    r'试探性轻仓(?:策略)?|轻仓试多|小仓位试多)'
)

_AUTHORIZED_LEVEL_TWO_ENTRY = re.compile(
    r'(?:'
    r'(?:(?:当前操作|最终建议|系统指令|下一步|执行方案)\s*[:：]\s*)?'
    r'(?:(?:建议|推荐|可以|可|考虑|计划|准备|执行|进行|采取)\s*)?'
    r'(?:条件试(?:探)?仓|轻仓试错|小仓位试错|试探性轻仓(?:策略)?|'
    r'轻仓试多|小仓位试多)'
    r'|'
    r'(?:若|如果|假如|一旦)[^。！？；;\n]{1,36}?(?:则|后|时|，|,)\s*'
    r'(?:(?:建议|推荐|可以|可|考虑|计划|准备|再)\s*)?'
    r'(?:(?:轻仓|小仓位|少量|试探性)\s*)'
    r'(?:买入|建仓|入场|试仓|试多)'
    r')'
)


def _mask_authorized_level_two_entries(
    text: str,
) -> tuple[str, dict[str, str]]:
    """Protect only conditional trial wording authorized by Buy Level 2."""
    protected: dict[str, str] = {}

    def _replace(match: re.Match[str]) -> str:
        # Do not hide a trial from the strong-action sanitizer when a nearby
        # modifier turns it into a full-size/direct instruction. Delimiters
        # such as ``满仓，执行条件试仓`` are presentation, not safety boundaries.
        sentence_start = max(
            match.string.rfind(mark, 0, match.start())
            for mark in ("。", "！", "？", "；", ";", "\n")
        ) + 1
        sentence_end = len(match.string)
        for mark in ("。", "！", "？", "；", ";", "\n"):
            candidate_end = match.string.find(mark, match.end())
            if candidate_end >= 0:
                sentence_end = min(sentence_end, candidate_end)
        prior_clause = match.string[sentence_start:match.start()]
        sizing_context = match.string[sentence_start:sentence_end]
        if re.search(
            r'(?:重仓|满仓|全仓|半仓|大仓位|全部仓位|'
            r'[0-9０-９]+(?:[\.．][0-9０-９]+)?\s*[%％]\s*(?:的)?仓位|'
            r'[一二两三四五六七八九十]+成(?:仓位)?|'
            r'强烈|强力|无条件|果断|立即|立刻|直接|积极)'
            r'(?:\s*(?:方式|比例))?\s*(?:[，,、]\s*)?'
            r'(?:(?:执行|进行|采取|开展|分批|逐步|做|随后|然后|再)\s*)*$',
            prior_clause,
        ) or re.search(
            r'(?:目标|最终|初始)?\s*仓位\s*(?:为|约|至|到|[:：])?\s*'
            r'(?:[0-9０-９]+(?:[\.．][0-9０-９]+)?\s*[%％]|'
            r'[一二两三四五六七八九十]+成(?:仓位)?|重仓|满仓|全仓|半仓|全部仓位)'
            r'|(?:满仓|全仓|半仓|全部仓位)\s*(?:作为|为|是)?\s*[^。！？；;\n]{0,16}?(?:目标|仓位)'
            r'|投入\s*(?:全部资金|全部仓位)'
            r'|(?:后续|最终)\s*(?:增至|增加至|配置(?:为|至|到)?)\s*'
            r'(?:重仓|满仓|全仓|半仓|全部仓位|'
            r'[0-9０-９]+(?:[\.．][0-9０-９]+)?\s*[%％])',
            sizing_context,
        ):
            return match.group(0)
        token = f"__TA_LEVEL_TWO_ENTRY_{len(protected)}__"
        protected[token] = match.group(0)
        return token

    return _AUTHORIZED_LEVEL_TWO_ENTRY.sub(_replace, text), protected


def _restore_authorized_level_two_entries(
    text: str,
    protected: dict[str, str],
) -> str:
    for token, original in protected.items():
        text = text.replace(token, original)
    return text


_CHAINED_LEVEL_TWO_STRONGER_ACTION = re.compile(
    r'(?P<authorized>__TA_LEVEL_TWO_ENTRY_\d+__)'
    r'(?:\s*(?:(?:[，,；;。:：]\s*)?'
    r'(?:或|/|／|再|然后|随后|继而|接着|并且|并|以及|后|之后|以后)\s*|'
    r'[，,；;。:：/]\s*)'
    r'(?:(?:逐步|分批|重仓|满仓|积极|确认|继续|最终|建议|直接)\s*)*'
    r'(?:加仓|补仓|买入|建仓|入场|开仓|增持|做多|'
    r'重仓布局|满仓布局|追涨(?:买入)?)'
    r'(?:\s*(?:[0-9０-９]+(?:[\.．][0-9０-９]+)?\s*[%％]\s*(?:的)?仓位|'
    r'[一二两三四五六七八九十]+成(?:仓位)?))?)+'
)


def _sanitize_chained_level_two_actions(
    text: str,
) -> tuple[str, list[str]]:
    """Keep the authorized trial but remove chained stronger instructions."""
    text, analytical_facts = _mask_analytical_entry_facts(text)
    text, non_executable_discussion = _mask_non_executable_entry_discussion(text)
    sanitized, count = _CHAINED_LEVEL_TWO_STRONGER_ACTION.subn(
        r'\g<authorized>',
        text,
    )
    sanitized = _restore_non_executable_entry_discussion(
        sanitized,
        non_executable_discussion,
    )
    sanitized = _restore_analytical_entry_facts(sanitized, analytical_facts)
    return sanitized, ["Buy Level 2 串联强动作已移除"] * count


def _sanitize_generic_gate_entry_actions(
    text: str,
    replacement_text: str = "等待门禁条件满足",
) -> tuple[str, list[str]]:
    """Downgrade entry directives while preserving facts and negations.

    Generic failures are not weaker than named failures: they still veto every
    executable entry instruction.  Reuse the protected clause-aware sanitizer
    so historical analysis, third-party facts, and negative guardrails remain
    untouched.
    """
    return _sanitize_fundamental_entry_actions(
        text,
        replacement_text=replacement_text,
    )


def _sanitize_strong_buy_actions(text: str) -> tuple[str, list[str]]:
    """Downgrade strong positive buy clauses without rewriting negations."""
    text, protected_analytical_facts = _mask_analytical_entry_facts(text)
    changes: list[str] = []
    sanitized_lines: list[str] = []
    double_negation_token = "__TA_STRONG_BUY_DOWNGRADED__"
    double_negation_replacement = "暂不执行强买入，等待条件确认"
    for line in text.splitlines(keepends=True):
        if re.search(r"禁止动作\s*[:：]", line):
            sanitized_lines.append(line)
            continue
        # Double negation permits the buy action (for example
        # ``不是不能重仓买入``), so handle it before ordinary negated
        # guardrails are masked and restored verbatim.
        line, double_negation_count = re.subn(
            r"(?:(?:并)?(?:不是|并非)\s*(?:不能|不可|不应|不应该|不该|不建议|不推荐|不允许|不宜|不需要|无需|不适合)|"
            r"(?:不能|不可|没有|并非没有)\s*不)\s*"
            r"(?:执行|进行|采取)?\s*"
            r"(?:强买|重仓买入|立即买入|立刻买入|强烈买入|满仓买入|"
            r"重仓布局|强力买入|建议加仓|执行加仓|加仓买入|可以加仓|"
            r"应该加仓|考虑加仓|加仓|建议追涨|可以追涨|追涨买入)",
            double_negation_token,
            line,
        )
        if double_negation_count:
            changes.extend(
                ["强买入动作已降级为「暂不执行强买入，等待条件确认」"]
                * double_negation_count
            )
        parts = re.split(r"([。！？；;，,、])", line)
        for index in range(0, len(parts), 2):
            clause = parts[index]
            if not clause:
                continue
            scoped_parts = re.split(
                r"((?:但(?:是)?|不过|然而|可是|却|而(?!非)|同时|并且|并(?!非|不)|且))",
                clause,
            )
            for scoped_index in range(0, len(scoped_parts), 2):
                scoped_clause = scoped_parts[scoped_index]
                if not scoped_clause:
                    continue
                scoped_clause, protected = _mask_negated_action_contexts(
                    scoped_clause
                )
                for pattern, replacement in _SANITIZE_STRONG_BUY:
                    if re.search(pattern, scoped_clause):
                        scoped_clause = re.sub(pattern, replacement, scoped_clause)
                        changes.append(f"强买入动作已降级为「{replacement}」")
                scoped_parts[scoped_index] = _restore_negated_action_contexts(
                    scoped_clause, protected
                )
            parts[index] = "".join(scoped_parts)
        sanitized_lines.append(
            "".join(parts).replace(
                double_negation_token,
                double_negation_replacement,
            )
        )
    sanitized = _restore_analytical_entry_facts(
        "".join(sanitized_lines),
        protected_analytical_facts,
    )
    return sanitized, changes


def _sanitize_no_position_actions(
    text: str,
) -> tuple[str, list[str]]:
    """Remove holding-only actions while preserving negated guardrails.

    A raw token replacement turns ``不建议减仓`` into corrupted prose such as
    ``不该持仓动作不适用``.  Scope the replacement to punctuation/contrast
    clauses so a later positive action is still removed without rewriting the
    preceding prohibition.
    """
    changes: list[str] = []
    sanitized_lines: list[str] = []
    for line in text.splitlines(keepends=True):
        # An analytical plan noun can still become an executable directive in
        # the following punctuation-delimited clause. Preserve the noun, but
        # downgrade the current execution instruction for a user with no
        # position.
        line, linked_plan_count = re.subn(
            r'(?P<plan>(?:止损|止盈)(?:并|或)?\s*(?:离场|退出|出局)\s*'
            r'(?:策略|规则|纪律|方案))'
            r'\s*[，,；;]\s*'
            r'(?:(?:并且|并|且|因此|所以|故而|结论是)\s*)?'
            r'(?:(?:建议|推荐|应|应该|应当|需|需要|必须|务必|决定|计划)\s*)?'
            r'(?:立即|立刻)?\s*(?:执行|实施|采用|启用|启动|落实)',
            r'\g<plan>，该持仓动作不适用，保持观察',
            line,
        )
        if linked_plan_count:
            changes.extend(
                ["未持仓动作已降级为「该持仓动作不适用，保持观察」"]
                * linked_plan_count
            )
        line, hypothetical_plans = _mask_hypothetical_holding_plans(line)
        # A double negation permits the holding-only action (for example
        # ``不是不能减仓``). It must therefore be downgraded for a
        # no-position user instead of being protected as a prohibition.
        line, double_negation_count = re.subn(
            r"(?:(?:并)?(?:不是|并非)\s*(?:不能|不可|不应|不应该|不该|不建议|不推荐|不允许|不宜|不需要|无需|不适合)|"
            r"(?:不能|不可|没有|并非没有)\s*不)\s*"
            r"(?:执行|进行|采取)?\s*"
            r"(?:减仓|清仓|止盈|卖出|止损)"
            r"(?!上限|条件|额度|限制|规则|禁令|线|位|价|比例|阈值)",
            "该持仓动作不适用，保持观察",
            line,
        )
        if double_negation_count:
            changes.extend(
                ["未持仓动作已降级为「该持仓动作不适用，保持观察」"]
                * double_negation_count
            )
        parts = re.split(
            r"([。！？；;，,、]|(?:或者|或)(?=(?:后续)?(?:建议|可以|可|应当|应该)))",
            line,
        )
        for index in range(0, len(parts), 2):
            clause = parts[index]
            if not clause:
                continue
            scoped_parts = re.split(
                r"((?:但(?:是)?|不过|然而|可是|却|而(?!非)|同时|并且|并(?!非|不)|且))",
                clause,
            )
            for scoped_index in range(0, len(scoped_parts), 2):
                scoped_clause = scoped_parts[scoped_index]
                if not scoped_clause:
                    continue
                scoped_clause, protected = _mask_negated_action_contexts(
                    scoped_clause
                )
                for pattern, replacement in _SANITIZE_NO_POSITION:
                    if re.search(pattern, scoped_clause):
                        scoped_clause = re.sub(pattern, replacement, scoped_clause)
                        changes.append(
                            "未持仓动作已降级为"
                            f"「{_display_action_replacement(replacement)}」"
                        )
                scoped_parts[scoped_index] = _restore_negated_action_contexts(
                    scoped_clause, protected
                )
            parts[index] = "".join(scoped_parts)
        sanitized_lines.append(
            _restore_hypothetical_holding_plans(
                "".join(parts), hypothetical_plans
            )
        )
    return "".join(sanitized_lines), changes


def _split_llm_body_and_system_blocks(
    text: str,
    trusted_system_offset: int | None = None,
) -> tuple[str, str]:
    """Split model text from caller-appended diagnostics at a trusted offset.

    Marker text is not a trust boundary because the model can emit the same
    headings.  Callers that append deterministic diagnostics must therefore
    pass the exact offset where those diagnostics begin.  Without an offset,
    the complete text remains subject to action sanitation.
    """
    if (
        isinstance(trusted_system_offset, int)
        and 0 <= trusted_system_offset <= len(text)
    ):
        return text[:trusted_system_offset], text[trusted_system_offset:]
    return text, ""


def _dedupe_changes(changes: list[str]) -> list[str]:
    """Keep the audit useful without listing the same rewrite dozens of times."""
    return list(dict.fromkeys(changes))


def sanitize_forbidden_strong_actions(
    text: str,
    gate: dict,
    position_status: str,
    buy_level: int,
    risk_level: int,
    trusted_system_offset: int | None = None,
    return_system_offset: bool = False,
) -> tuple:
    """移除或替换最终报告中未被 gate 允许的强动作表达。

    未持仓动作清理（减仓/清仓/止盈/止损/卖出）独立于 gate 检查，
    只要 position_status == "no_position" 就会执行。
    强买/强卖替换仅在 gate 未通过时执行。

    When ``return_system_offset`` is true, also return the corrected offset at
    which the caller-provided system block starts after body rewrites.  This is
    safer than searching the combined text for a delimiter the model can copy.

    Returns:
        (sanitized_text, list_of_changes), or that pair plus system offset.
    """
    changes = []
    result = text

    body, system_blocks = _split_llm_body_and_system_blocks(
        result,
        trusted_system_offset=trusted_system_offset,
    )
    body, completed_purchase_facts = _mask_completed_purchase_facts(body)
    body, third_party_entry_facts = _mask_third_party_entry_facts(body)
    body, third_party_exit_facts = _mask_third_party_exit_facts(body)
    body, analytical_exit_facts = _mask_analytical_exit_facts(body)
    body, protected_action_enums = _mask_action_enum_references(body)
    body, protected_analytical_action_nouns = _mask_analytical_action_nouns(body)

    if position_status == "no_position":
        body, no_position_changes = _sanitize_no_position_actions(body)
        changes.extend(no_position_changes)

    # The computed Buy/Risk levels are authoritative even when the aggregate
    # evidence gate itself passes.  Enforce them on the visible model body so
    # it cannot disagree with C-008 and the deterministic execution summary.
    level_blocks_entry = (
        position_status in {"no_position", "unknown"}
        and (
            buy_level < 2
            or (position_status in {"no_position", "unknown"} and risk_level >= 1)
        )
    )
    holding_blocks_add = (
        position_status == "has_position"
        and (buy_level < 3 or risk_level >= 2)
    )
    level_two_conditional_only = (
        position_status in {"no_position", "unknown"}
        and buy_level == 2
        and not (position_status in {"no_position", "unknown"} and risk_level >= 1)
    )
    if gate.get("passed", True) and (
        level_blocks_entry or holding_blocks_add or level_two_conditional_only
    ):
        level_replacement = (
            "仅限条件试仓，等待明确触发"
            if level_two_conditional_only
            else "保持观察，等待入场条件确认"
            if position_status in {"no_position", "unknown"}
            else "保持原仓位，等待加仓条件确认"
        )
        body, future_holding_plans = _mask_hypothetical_holding_plans(body)
        authorized_level_two_entries: dict[str, str] = {}
        strong_trial_token = "__TA_LEVEL_TWO_STRONG_TRIAL_DOWNGRADE__"
        level_two_analytical_facts: dict[str, str] = {}
        level_two_discussion: dict[str, str] = {}
        if level_two_conditional_only:
            # Remove strong sizing/urgency before masking the narrow trial
            # phrases that Buy Level 2 is allowed to preserve. Keep the safe
            # replacement behind a token until the other entry sanitizers have
            # run so its own wording cannot be rewritten recursively.
            body, level_two_analytical_facts = _mask_analytical_entry_facts(
                body
            )
            body, level_two_discussion = _mask_non_executable_entry_discussion(
                body
            )
            body, sized_trial_count = re.subn(
                _SIZED_TRIAL_ACTION_PATTERN,
                strong_trial_token,
                body,
            )
            body, trailing_sized_trial_count = re.subn(
                _TRAILING_SIZED_TRIAL_ACTION_PATTERN,
                strong_trial_token,
                body,
            )
            body, target_sized_trial_count = re.subn(
                _TARGET_SIZED_TRIAL_ACTION_PATTERN,
                strong_trial_token,
                body,
            )
            body, strong_trial_count = re.subn(
                _STRONG_TRIAL_ACTION_PATTERN,
                strong_trial_token,
                body,
            )
            changes.extend(
                ["强买入动作已降级为「暂不执行强买入，等待条件确认」"]
                * (
                    sized_trial_count
                    + trailing_sized_trial_count
                    + target_sized_trial_count
                    + strong_trial_count
                )
            )
            body, authorized_level_two_entries = (
                _mask_authorized_level_two_entries(body)
            )
            body, chained_level_two_changes = (
                _sanitize_chained_level_two_actions(body)
            )
            changes.extend(chained_level_two_changes)
        level_replacement_token = "__TA_LEVEL_AUTHORITY_DOWNGRADE__"
        body, level_entry_changes = _sanitize_fundamental_entry_actions(
            body,
            replacement_text=level_replacement_token,
        )
        body = body.replace(level_replacement_token, level_replacement)
        level_entry_changes = [
            change.replace(level_replacement_token, level_replacement)
            for change in level_entry_changes
        ]
        body = _restore_authorized_level_two_entries(
            body,
            authorized_level_two_entries,
        )
        body = _restore_hypothetical_holding_plans(
            body,
            future_holding_plans,
        )
        changes.extend(level_entry_changes)
        body, level_strong_buy_changes = _sanitize_strong_buy_actions(body)
        changes.extend(level_strong_buy_changes)
        body = body.replace(
            strong_trial_token,
            "暂不执行强买入，等待条件确认",
        )
        body = _restore_non_executable_entry_discussion(
            body,
            level_two_discussion,
        )
        body = _restore_analytical_entry_facts(
            body,
            level_two_analytical_facts,
        )

    # A passing evidence gate does not authorize an immediate exit by itself.
    # Risk Level 4 is the sole authority for a visible immediate-clear action.
    if position_status == "has_position" and risk_level < 4:
        authorized_level_two_reductions: dict[str, str] = {}
        authorized_level_three_stops: dict[str, str] = {}
        if risk_level >= 2:
            body, authorized_level_two_reductions = (
                _mask_authorized_level_two_reductions(body)
            )
        if risk_level == 3:
            body, authorized_level_three_stops = (
                _mask_authorized_level_three_stops(body)
            )
        body, protected_low_risk_exit_negations = _mask_negated_action_contexts(
            body
        )
        for pattern, replacement in _SANITIZE_STRONG_SELL:
            body, replacement_count = re.subn(pattern, replacement, body)
            if replacement_count:
                changes.extend(
                    [f"强卖出动作已降级为「{replacement}」"]
                    * replacement_count
                )
        body = _restore_negated_action_contexts(
            body,
            protected_low_risk_exit_negations,
        )
        body = _restore_authorized_level_three_stops(
            body,
            authorized_level_three_stops,
        )
        body = _restore_authorized_level_two_reductions(
            body,
            authorized_level_two_reductions,
        )

    if not gate.get("passed", True):
        failures = list(gate.get("failures", []))
        failed_gate_future_plans: dict[str, str] = {}
        if position_status == "no_position":
            body, failed_gate_future_plans = _mask_hypothetical_holding_plans(body)
        body, strong_buy_changes = _sanitize_strong_buy_actions(body)
        changes.extend(strong_buy_changes)
        strong_buy_downgrade = "暂不执行强买入，等待条件确认"
        strong_buy_token = "__TA_STRONG_BUY_GATE_DOWNGRADE__"
        body = body.replace(strong_buy_downgrade, strong_buy_token)
        if "event_risk_block_open" in failures:
            entry_replacement = "等待风险解除"
        elif "fundamental_semantic_gate" in failures:
            entry_replacement = "等待基本面证据复核"
        elif "估值基准价不可用(valuation_price_unavailable)" in failures:
            entry_replacement = "等待可执行价格确认"
        else:
            entry_replacement = "等待门禁条件满足"
        if entry_replacement == "等待门禁条件满足":
            body, entry_gate_changes = _sanitize_generic_gate_entry_actions(
                body,
                replacement_text=entry_replacement,
            )
        else:
            body, entry_gate_changes = _sanitize_fundamental_entry_actions(
                body,
                replacement_text=entry_replacement,
            )
        body = body.replace(strong_buy_token, strong_buy_downgrade)
        changes.extend(entry_gate_changes)
        buy_only_failures = {
            "event_risk_block_open",
            "fundamental_semantic_gate",
            "估值基准价不可用(valuation_price_unavailable)",
        }
        entry_veto_only = (
            bool(failures)
            and all(reason in buy_only_failures for reason in failures)
        )
        # Entry-only vetoes do not invalidate an independently confirmed exit,
        # but a model-authored forced exit must still be downgraded when the
        # computed Risk Level does not authorize Level 4 action.
        preserve_strong_sell = entry_veto_only and risk_level >= 4
        if not preserve_strong_sell:
            for pattern, replacement in _SANITIZE_STRONG_SELL:
                matches = list(re.finditer(pattern, body))
                if matches:
                    changes.append(f"强卖出动作已降级为「{replacement}」")
                    body = re.sub(pattern, replacement, body)
        body = _restore_hypothetical_holding_plans(
            body,
            failed_gate_future_plans,
        )

    changes = _dedupe_changes(changes)
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
        if system_blocks:
            system_blocks += downgrade_note
        else:
            body += downgrade_note

    body = _restore_action_enum_references(body, protected_action_enums)
    body = _restore_analytical_action_nouns(
        body,
        protected_analytical_action_nouns,
    )
    body = _restore_completed_purchase_facts(body, completed_purchase_facts)
    body = _restore_third_party_entry_facts(body, third_party_entry_facts)
    body = _restore_third_party_exit_facts(body, third_party_exit_facts)
    body = _restore_analytical_exit_facts(body, analytical_exit_facts)
    separator = (
        "\n\n"
        if system_blocks
        and not body.endswith(("\n", "\r"))
        and not system_blocks.startswith(("\n", "\r"))
        else ""
    )
    sanitized = body + separator + system_blocks
    if return_system_offset:
        system_offset = len(body) + len(separator) if system_blocks else None
        return sanitized, changes, system_offset
    return sanitized, changes


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
                      Supports both legacy format (raw values) and G-006 format
                      (dict with 'raw', 'status', 'vendor', etc. keys).

    Returns:
        dict with evidence field names mapped to EvidenceStatus values.
    """

    def _unwrap_raw(val):
        # [G-006] raw_evidence_snapshot: unwrap G-006 structured entries
        if isinstance(val, dict) and "raw" in val and "status" in val:
            return val["raw"]
        return val

    def _structured_evidence_status(val) -> Optional[str]:
        if not isinstance(val, dict) or "status" not in val:
            return None
        struct_status = str(val.get("status") or "").upper()
        if struct_status == "HAS_DATA":
            return EvidenceStatus.HAS_DATA
        if struct_status in {"FAILED", "ERROR", "QUERY_FAILED"}:
            return EvidenceStatus.QUERY_FAILED
        if struct_status in {"NORMAL_NO_DATA", "NO_DATA"}:
            return EvidenceStatus.NORMAL_NO_DATA
        if struct_status in {"SKIPPED", "NOT_AVAILABLE"}:
            return EvidenceStatus.SKIPPED
        if struct_status == "NOT_QUERIED":
            return EvidenceStatus.NOT_QUERIED
        return None

    market = reports.get("market_report", "") or ""
    volume_price = reports.get("volume_price_report", "") or ""
    smart_money = reports.get("smart_money_report", "") or ""
    news = reports.get("news_report", "") or ""

    combined = f"{market}\n{volume_price}\n{smart_money}\n{news}"

    # ── Structured evidence from raw data pool (E-004) ──
    raw = raw_evidence or {}

    # 1. OHLCV 5d — check stock_data CSV
    ohlcv_5d = EvidenceStatus.NOT_QUERIED
    raw_stock_data_entry = raw.get("stock_data")
    stock_struct_status = _structured_evidence_status(raw_stock_data_entry)
    raw_stock_data = _unwrap_raw(raw_stock_data_entry)
    if stock_struct_status:
        ohlcv_5d = stock_struct_status
    elif raw_stock_data and isinstance(raw_stock_data, str) and len(raw_stock_data) > 50:
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
    if stock_struct_status and stock_struct_status != EvidenceStatus.HAS_DATA:
        volume = stock_struct_status
    elif raw_stock_data and isinstance(raw_stock_data, str) and len(raw_stock_data) > 50:
        volume = EvidenceStatus.HAS_DATA
    else:
        src = market or volume_price
        if src:
            if re.search(_VOLUME_PATTERN, src, re.IGNORECASE):
                volume = EvidenceStatus.HAS_DATA
            elif src.strip():
                volume = EvidenceStatus.FIELD_MISSING

    raw_quote_entry = raw.get("realtime_quote")
    quote_struct_status = _structured_evidence_status(raw_quote_entry)
    raw_quote = _unwrap_raw(raw_quote_entry)

    def _quote_has_field(field_name: str) -> bool:
        quote_payload = raw_quote
        if isinstance(quote_payload, str):
            try:
                import json
                quote_payload = json.loads(quote_payload)
            except (TypeError, ValueError):
                return field_name in quote_payload
        if not isinstance(quote_payload, dict):
            return False
        return any(
            isinstance(quote, dict) and quote.get(field_name) is not None
            for quote in quote_payload.values()
        )

    # 3. Turnover rate — prefer structured realtime quote evidence
    # [DATA-P0-603629] astock_source_fallback: cn_astock now provides turnover_rate in realtime
    turnover_rate = EvidenceStatus.NOT_QUERIED
    raw_news_val = _unwrap_raw(raw.get("news"))
    raw_stock_data_val = _unwrap_raw(raw.get("stock_data"))
    if _quote_has_field("turnover_rate"):
        turnover_rate = EvidenceStatus.HAS_DATA
    elif quote_struct_status == EvidenceStatus.QUERY_FAILED:
        turnover_rate = EvidenceStatus.QUERY_FAILED
    elif quote_struct_status == EvidenceStatus.HAS_DATA:
        turnover_rate = EvidenceStatus.FIELD_MISSING
    elif volume_price:
        if re.search(_TURNOVER_PATTERN, volume_price, re.IGNORECASE):
            turnover_rate = EvidenceStatus.HAS_DATA
        elif volume_price.strip():
            turnover_rate = EvidenceStatus.FIELD_MISSING
    elif raw_stock_data_val and isinstance(raw_stock_data_val, str) and "turnover_rate" in raw_stock_data_val:
        turnover_rate = EvidenceStatus.HAS_DATA
    elif quote_struct_status in {
        EvidenceStatus.NORMAL_NO_DATA,
        EvidenceStatus.SKIPPED,
    }:
        turnover_rate = quote_struct_status

    # 4. Volume ratio — check raw_evidence realtime quote data
    # [DATA-P0-603629] astock_source_fallback: cn_astock now provides volume_ratio in realtime
    volume_ratio = EvidenceStatus.NOT_QUERIED
    if _quote_has_field("volume_ratio"):
        volume_ratio = EvidenceStatus.HAS_DATA
    elif quote_struct_status == EvidenceStatus.QUERY_FAILED:
        volume_ratio = EvidenceStatus.QUERY_FAILED
    elif quote_struct_status == EvidenceStatus.HAS_DATA:
        volume_ratio = EvidenceStatus.FIELD_MISSING
    elif volume_price:
        if re.search(_VOLUME_RATIO_PATTERN, volume_price, re.IGNORECASE):
            volume_ratio = EvidenceStatus.HAS_DATA
        elif volume_price.strip():
            volume_ratio = EvidenceStatus.FIELD_MISSING
    elif raw_stock_data_val and isinstance(raw_stock_data_val, str) and "volume_ratio" in raw_stock_data_val:
        volume_ratio = EvidenceStatus.HAS_DATA
    elif quote_struct_status in {
        EvidenceStatus.NORMAL_NO_DATA,
        EvidenceStatus.SKIPPED,
    }:
        volume_ratio = quote_struct_status

    # 5. Individual fund flow — check fund_flow_individual
    individual_fund_flow = EvidenceStatus.NOT_QUERIED
    raw_fund_flow_entry = raw.get("fund_flow_individual")
    fund_struct_status = _structured_evidence_status(raw_fund_flow_entry)
    raw_fund_flow = _unwrap_raw(raw_fund_flow_entry)
    if fund_struct_status:
        individual_fund_flow = fund_struct_status
    elif raw_fund_flow is not None:
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
    raw_lhb_entry = raw.get("lhb")
    lhb_struct_status = _structured_evidence_status(raw_lhb_entry)
    raw_lhb = _unwrap_raw(raw_lhb_entry)
    if lhb_struct_status:
        lhb_status = lhb_struct_status
    elif raw_lhb is not None:
        if isinstance(raw_lhb, str) and "失败" in raw_lhb:
            lhb_status = EvidenceStatus.QUERY_FAILED
        elif isinstance(raw_lhb, str) and ("无" in raw_lhb or "未上榜" in raw_lhb or "未触发" in raw_lhb or len(raw_lhb.strip()) == 0):
            if "查询未触发" in raw_lhb or "LHB_NOT_QUERIED" in raw_lhb or raw_lhb.strip() == "未触发":
                lhb_status = EvidenceStatus.NOT_QUERIED  # [G-007]
            else:
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

    # 7. Margin trading (融资融券) — check raw_evidence margin_trading field
    # [DATA-010] margin_trading_raw_evidence
    margin_trading = EvidenceStatus.NOT_QUERIED
    raw_margin_entry = raw.get("margin_trading")
    # Check structured status first (P2: honor structured contract status)
    margin_struct_status = _structured_evidence_status(raw_margin_entry)
    if margin_struct_status:
        margin_trading = margin_struct_status
    raw_margin = _unwrap_raw(raw_margin_entry)
    if margin_trading == EvidenceStatus.NOT_QUERIED and raw_margin is not None:
        if isinstance(raw_margin, str) and "MARGIN_HAS_DATA" in raw_margin:
            margin_trading = EvidenceStatus.HAS_DATA
        elif isinstance(raw_margin, str) and "MARGIN_FAILED" in raw_margin:
            margin_trading = EvidenceStatus.QUERY_FAILED
        elif isinstance(raw_margin, str) and ("MARGIN_NORMAL_NO_DATA" in raw_margin or "暂不可用" in raw_margin):
            margin_trading = EvidenceStatus.NORMAL_NO_DATA
        elif isinstance(raw_margin, str) and len(raw_margin) > 20:
            margin_trading = EvidenceStatus.HAS_DATA
        elif isinstance(raw_margin, str) and "失败" in raw_margin:
            margin_trading = EvidenceStatus.QUERY_FAILED
        elif raw_margin:
            margin_trading = EvidenceStatus.HAS_DATA
        else:
            margin_trading = EvidenceStatus.NORMAL_NO_DATA

    # 8. Announcements — check news data
    announcements = EvidenceStatus.NOT_QUERIED
    raw_news_entry = raw.get("news")
    news_struct_status = _structured_evidence_status(raw_news_entry)
    raw_news = _unwrap_raw(raw_news_entry)
    if news_struct_status:
        announcements = news_struct_status
    elif raw_news is not None:
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

    # 9. Research reports — check raw_evidence research_report field
    # [DATA-011] research_report_raw_evidence
    research_report = EvidenceStatus.NOT_QUERIED
    raw_report_entry = raw.get("research_report")
    report_struct_status = _structured_evidence_status(raw_report_entry)
    raw_report = _unwrap_raw(raw_report_entry)
    if report_struct_status:
        research_report = report_struct_status
    elif raw_report is not None:
        if isinstance(raw_report, str) and "REPORT_HAS_DATA" in raw_report:
            research_report = EvidenceStatus.HAS_DATA
        elif isinstance(raw_report, str) and "REPORT_FAILED" in raw_report:
            research_report = EvidenceStatus.QUERY_FAILED
        elif isinstance(raw_report, str) and ("REPORT_NORMAL_NO_DATA" in raw_report or "无券商研报" in raw_report):
            research_report = EvidenceStatus.NORMAL_NO_DATA
        elif isinstance(raw_report, str) and len(raw_report) > 20:
            research_report = EvidenceStatus.HAS_DATA
        elif isinstance(raw_report, str) and "失败" in raw_report:
            research_report = EvidenceStatus.QUERY_FAILED
        elif raw_report:
            research_report = EvidenceStatus.HAS_DATA
        else:
            research_report = EvidenceStatus.NORMAL_NO_DATA

    # 10. Ratings — check raw_evidence ratings field
    # [DATA-012] rating_raw_evidence
    ratings = EvidenceStatus.NOT_QUERIED
    raw_ratings_entry = raw.get("ratings")
    rating_struct_status = _structured_evidence_status(raw_ratings_entry)
    if rating_struct_status:
        ratings = rating_struct_status
    raw_ratings = _unwrap_raw(raw_ratings_entry)
    if ratings == EvidenceStatus.NOT_QUERIED and raw_ratings is not None:
        if isinstance(raw_ratings, str) and "RATINGS_HAS_DATA" in raw_ratings:
            ratings = EvidenceStatus.HAS_DATA
        elif isinstance(raw_ratings, str) and "RATINGS_FAILED" in raw_ratings:
            ratings = EvidenceStatus.QUERY_FAILED
        elif isinstance(raw_ratings, str) and ("RATINGS_NORMAL_NO_DATA" in raw_ratings or "无分析师评级" in raw_ratings):
            ratings = EvidenceStatus.NORMAL_NO_DATA
        elif isinstance(raw_ratings, str) and len(raw_ratings) > 20:
            ratings = EvidenceStatus.HAS_DATA
        elif isinstance(raw_ratings, str) and "失败" in raw_ratings:
            ratings = EvidenceStatus.QUERY_FAILED
        elif raw_ratings:
            ratings = EvidenceStatus.HAS_DATA
        else:
            ratings = EvidenceStatus.NORMAL_NO_DATA

    # 10. Buybacks (回购) — check raw_evidence buybacks field
    # [DATA-013] buyback_raw_evidence
    buybacks = EvidenceStatus.NOT_QUERIED
    raw_buyback_entry = raw.get("buybacks")
    buyback_struct_status = _structured_evidence_status(raw_buyback_entry)
    if buyback_struct_status:
        buybacks = buyback_struct_status
    raw_buyback = _unwrap_raw(raw_buyback_entry)
    if buybacks == EvidenceStatus.NOT_QUERIED and raw_buyback is not None:
        if isinstance(raw_buyback, str) and "BUYBACK_HAS_DATA" in raw_buyback:
            buybacks = EvidenceStatus.HAS_DATA
        elif isinstance(raw_buyback, str) and "BUYBACK_FAILED" in raw_buyback:
            buybacks = EvidenceStatus.QUERY_FAILED
        elif isinstance(raw_buyback, str) and ("BUYBACK_NORMAL_NO_DATA" in raw_buyback or "无回购" in raw_buyback):
            buybacks = EvidenceStatus.NORMAL_NO_DATA
        elif isinstance(raw_buyback, str) and len(raw_buyback) > 20:
            buybacks = EvidenceStatus.HAS_DATA
        elif isinstance(raw_buyback, str) and "失败" in raw_buyback:
            buybacks = EvidenceStatus.QUERY_FAILED
        elif raw_buyback:
            buybacks = EvidenceStatus.HAS_DATA
        else:
            buybacks = EvidenceStatus.NORMAL_NO_DATA

    return {
        "ohlcv_5d": ohlcv_5d,
        "volume": volume,
        "turnover_rate": turnover_rate,
        "volume_ratio": volume_ratio,
        "individual_fund_flow": individual_fund_flow,
        "lhb_status": lhb_status,
        "margin_trading": margin_trading,
        "announcements": announcements,
        "research_report": research_report,
        "ratings": ratings,
        "buybacks": buybacks,
    }


def _data_blocker_status_meta(status: str) -> Optional[dict]:
    return _BLOCKER_STATUS_META.get(status)


def _data_blocker_reason(key: str, status: str, default_reason: str) -> str:
    if key == "lhb_status" and status == EvidenceStatus.NORMAL_NO_DATA:
        return "未上龙虎榜或非异动日无龙虎榜数据，属于正常无数据。"
    if key in {"ratings", "buybacks", "research_report"} and status == EvidenceStatus.NORMAL_NO_DATA:
        return f"{_DATA_BLOCKER_FIELDS[key]['label']}当前未返回匹配记录，属于辅助数据正常无数据。"
    if status == EvidenceStatus.QUERY_FAILED:
        return f"{default_reason}，数据源查询失败或返回异常。"
    if status == EvidenceStatus.NOT_QUERIED:
        return f"{default_reason}，本次分析未查询该字段。"
    if status == EvidenceStatus.FIELD_MISSING:
        return f"{default_reason}，报告文本未包含可校验原始值。"
    if status == EvidenceStatus.NOT_AVAILABLE:
        return f"{default_reason}，当前数据源暂不提供该字段。"
    return default_reason


def build_data_blockers(reports: dict, raw_evidence: Optional[dict] = None) -> list:
    """Build field-level data blockers for report metadata.

    [DATA-021] report_data_blockers
    This is an explanatory layer only: it does not change readiness gates or
    trading levels. It makes "数据不足" auditable by separating normal no-data
    cases from query failures, skipped fields and text-only missing evidence.
    """
    statuses = infer_evidence_statuses(reports or {}, raw_evidence=raw_evidence)
    blockers = []
    for key, info in _DATA_BLOCKER_FIELDS.items():
        status = statuses.get(key)
        if status == EvidenceStatus.HAS_DATA:
            continue
        meta = _data_blocker_status_meta(status)
        if not meta:
            continue
        blockers.append({
            "key": key,
            "label": info["label"],
            "status": meta["status"],
            "status_label": meta["status_label"],
            "severity": meta["severity"],
            "reason": _data_blocker_reason(key, status, info["reason"]),
            "impact": info["impact"],
        })
    return blockers


def summarize_data_blockers(blockers: list) -> dict:
    """Return a compact summary for UI/report cards.

    [DATA-021] report_data_blockers
    """
    counts = {
        "query_failed": 0,
        "field_missing": 0,
        "not_queried": 0,
        "normal_no_data": 0,
        "skipped": 0,
    }
    for blocker in blockers or []:
        status = blocker.get("status")
        if status in counts:
            counts[status] += 1

    severe = counts["query_failed"] + counts["field_missing"]
    medium = counts["not_queried"]
    normal = counts["normal_no_data"] + counts["skipped"]
    if not blockers:
        message = "核心数据源未发现字段级缺口。"
        level = "ok"
    elif severe:
        message = f"{severe} 项关键数据查询失败或字段缺失，强结论需降级；另有 {medium} 项未查询、{normal} 项正常无数据/跳过。"
        level = "warning"
    elif medium:
        message = f"{medium} 项数据未查询，结论需保留条件；另有 {normal} 项正常无数据/跳过。"
        level = "caution"
    else:
        message = f"{normal} 项为正常无数据或数据源跳过，不单独构成强降级理由。"
        level = "info"

    return {
        "level": level,
        "message": message,
        "counts": counts,
        "total": len(blockers or []),
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


# ── G-001: 短中线冲突处理 ──────────────────────────────────────────────

def resolve_horizon_conflict(
    medium_bullish: bool,
    short_bullish: bool,
    has_position: bool = False,
) -> dict:
    """G-001: 短中线冲突处理 — 四种组合的降级规则。

    Args:
        medium_bullish: 中线是否偏多
        short_bullish: 短线是否偏多
        has_position: 是否已持仓

    Returns:
        dict with keys:
        - action: 最终建议动作
        - allowed_intents: 允许的 analysis_intent 列表
        - note: 说明文本
        - conflict_type: 冲突类型标识
    """
    if medium_bullish and short_bullish:
        # 中线偏多 + 短线偏强 → 可条件买入/加仓
        return {
            "action": "可条件买入/加仓",
            "allowed_intents": ["entry", "add", "watch"],
            "note": "中短线共振偏多，可考虑条件入场或加仓。",
            "conflict_type": "none",
        }
    elif medium_bullish and not short_bullish:
        # 中线偏多 + 短线偏弱 → 不追买不清仓，等待短线修复
        if has_position:
            return {
                "action": "持有等待",
                "allowed_intents": ["holding", "watch"],
                "note": "中线偏多但短线偏弱，已持仓不追买不清仓，等待短线修复。",
                "conflict_type": "medium_bull_short_weak",
            }
        else:
            return {
                "action": "继续观察",
                "allowed_intents": ["watch"],
                "note": "中线偏多但短线偏弱，未持仓不追买，等待短线修复后再入场。",
                "conflict_type": "medium_bull_short_weak",
            }
    elif not medium_bullish and short_bullish:
        # 中线偏空 + 短线偏强 → 只允许短打观察
        if has_position:
            return {
                "action": "短打观察",
                "allowed_intents": ["holding", "reduce", "watch"],
                "note": "中线偏空但短线偏强，已持仓可短打观察，禁止加仓。",
                "conflict_type": "medium_bear_short_strong",
            }
        else:
            return {
                "action": "短打观察",
                "allowed_intents": ["watch"],
                "note": "中线偏空但短线偏强，未持仓只允许短打观察，禁止入场建仓。",
                "conflict_type": "medium_bear_short_strong",
            }
    else:
        # 中线偏空 + 短线偏弱 → 已持仓减仓/止损观察，未持仓继续观察
        if has_position:
            return {
                "action": "减仓/止损观察",
                "allowed_intents": ["reduce", "stop_loss", "watch"],
                "note": "中短线共振偏空，已持仓建议减仓/止损观察。",
                "conflict_type": "both_bearish",
            }
        else:
            return {
                "action": "继续观察",
                "allowed_intents": ["watch"],
                "note": "中短线共振偏空，未持仓继续观察，禁止入场。",
                "conflict_type": "both_bearish",
            }


def apply_horizon_conflict_to_actions(
    conflict_result: dict,
    allowed_actions: dict,
) -> dict:
    """根据短中线冲突结果，进一步限制允许的动作。

    Args:
        conflict_result: resolve_horizon_conflict 的返回值
        allowed_actions: get_allowed_actions 的返回值

    Returns:
        修改后的 allowed_actions dict
    """
    result = dict(allowed_actions)
    allowed_intents = conflict_result.get("allowed_intents", [])
    forbidden_extra = []

    # 如果冲突类型不为 none，根据 allowed_intents 限制动作
    if conflict_result.get("conflict_type") != "none":
        if "entry" not in allowed_intents:
            forbidden_extra.extend(["买入", "建仓", "确认建仓", "积极建仓"])
        if "add" not in allowed_intents:
            forbidden_extra.extend(["加仓", "补仓"])
        if "reduce" not in allowed_intents:
            forbidden_extra.append("减仓")
        if "stop_loss" not in allowed_intents:
            forbidden_extra.append("止损")

    if forbidden_extra:
        existing_forbidden = result.get("forbidden", [])
        for f in forbidden_extra:
            if f not in existing_forbidden:
                existing_forbidden.append(f)
        result["forbidden"] = existing_forbidden
        # Remove from allowed
        existing_allowed = result.get("allowed", [])
        result["allowed"] = [a for a in existing_allowed if a not in forbidden_extra]
        result["message"] = (result.get("message", "") +
                            f" 短中线冲突({conflict_result.get('conflict_type', '')})：{conflict_result.get('note', '')}")

    return result


# ── [G-007] fund_lhb_provenance ──────────────────────────────────────────

_NEWS_FUND_FLOW_PATTERNS = [
    r'主力.*?(?:净流出|净流入)',
    r'(?:净流出|净流入).*?主力',
    r'大单.*?(?:净流出|净流入)',
    r'资金.*?(?:流出|流入)',
]


def build_fund_flow_provenance(raw_evidence: Optional[dict], reports: dict) -> dict:
    """[G-007] Build fund flow provenance info from raw_evidence and reports.

    Determines whether fund flow data comes from individual fund flow API,
    board fund flow API, or news text mentions.  Only individual_fund_flow
    with HAS_DATA status and verified unit qualifies as strong evidence.

    Returns:
        {
            "individual_status": str,
            "board_status": str,
            "news_reported_fund_flow": bool,
            "strong_evidence_allowed": bool,
            "unit_verified": bool,
            "not_mixed": bool,
            "conflict_summary": str,
        }
    """
    raw = raw_evidence or {}

    def _unwrap_g006(val):
        if isinstance(val, dict) and "status" in val:
            return val
        return None

    def _status_from_g006(val):
        structured = _unwrap_g006(val)
        if structured:
            return structured.get("status", "NOT_QUERIED")
        return None

    def _unwrap_legacy(val):
        if isinstance(val, dict) and "raw" in val and "status" in val:
            return val["raw"]
        return val

    def _status_from_text(val):
        if val is None:
            return "NOT_QUERIED"
        if isinstance(val, str):
            s = val.strip()
            if not s:
                return "NOT_QUERIED"
            if "获取失败" in s or "不可用" in s:
                return "FAILED"
            if len(s) > 20:
                return "HAS_DATA"
            return "NORMAL_NO_DATA"
        return "HAS_DATA" if val else "NOT_QUERIED"

    individual_entry = raw.get("fund_flow_individual")
    individual_status = _status_from_g006(individual_entry)
    individual_unit_verified = False
    if individual_status:
        structured = individual_entry
        individual_unit_verified = structured.get("unit_verified", False) is True
    else:
        raw_ff = _unwrap_legacy(individual_entry)
        individual_status = _status_from_text(raw_ff)
        if individual_status == "HAS_DATA" and isinstance(raw_ff, str):
            individual_unit_verified = bool(
                re.search(
                    r"单位\s*[:：]\s*(?:人民币)?(?:元|万元|亿元)\b|"
                    r"(?:主力)?(?:净流入|净流出|净额|超大单|大单)"
                    r"[^\n，。；]{0,12}[-+]?\d+(?:\.\d+)?\s*(?:万|亿)?元",
                    raw_ff,
                )
            )

    board_status = _status_from_g006(raw.get("fund_flow_board"))
    if not board_status:
        raw_board = _unwrap_legacy(raw.get("fund_flow_board"))
        board_status = _status_from_text(raw_board)

    news_reported_fund_flow = False
    news_text = reports.get("news_report", "") or ""
    if news_text and _text_match_any(news_text, _NEWS_FUND_FLOW_PATTERNS):
        news_reported_fund_flow = True

    strong_evidence_allowed = (
        individual_status == "HAS_DATA" and individual_unit_verified
    )
    unit_gate_applicable = individual_status not in {"NOT_AVAILABLE", "SKIPPED"}
    unit_gate_passed = not unit_gate_applicable or strong_evidence_allowed

    conflict_parts = []
    if individual_status == "FAILED":
        conflict_parts.append("个股资金流接口查询失败")
    elif individual_status == "NOT_QUERIED":
        conflict_parts.append("个股资金流接口未查询")

    if news_reported_fund_flow and not strong_evidence_allowed:
        conflict_parts.append("新闻转述资金信息仅作弱证据，不得作为主力资金强证据")

    conflict_summary = "；".join(conflict_parts) if conflict_parts else ""

    return {
        "individual_status": individual_status,
        "board_status": board_status,
        "news_reported_fund_flow": news_reported_fund_flow,
        "strong_evidence_allowed": strong_evidence_allowed,
        "unit_gate_applicable": unit_gate_applicable,
        "unit_gate_passed": unit_gate_passed,
        "unit_verified": individual_unit_verified,
        "not_mixed": True,
        "conflict_summary": conflict_summary,
    }


def build_lhb_provenance(raw_evidence: Optional[dict], reports: dict) -> dict:
    """[G-007] Build LHB provenance info from raw_evidence and reports.

    Distinguishes: HAS_DATA / NOT_QUERIED / NORMAL_NO_DATA / FAILED.
    NOT_QUERIED  = force=False, no anomaly triggered
    NORMAL_NO_DATA = force=True but stock not on LHB that day
    FAILED = API error
    HAS_DATA = actual LHB records found
    """
    raw = raw_evidence or {}

    lhb_status = "NOT_QUERIED"
    query_mode = "not_queried"

    raw_lhb_entry = raw.get("lhb")

    if isinstance(raw_lhb_entry, dict) and "status" in raw_lhb_entry:
        lhb_status = raw_lhb_entry.get("status", "NOT_QUERIED")
        query_mode = raw_lhb_entry.get("query_mode", "not_queried") or "not_queried"
    else:
        raw_lhb_val = raw_lhb_entry
        if isinstance(raw_lhb_entry, dict) and "raw" in raw_lhb_entry:
            raw_lhb_val = raw_lhb_entry["raw"]
        if raw_lhb_val is None:
            lhb_status = "NOT_QUERIED"
        elif isinstance(raw_lhb_val, str):
            val = raw_lhb_val.strip()
            if not val:
                lhb_status = "NOT_QUERIED"
            elif "获取失败" in val or "LHB_FAILED" in val:
                lhb_status = "FAILED"
            elif "查询未触发" in val or "LHB_NOT_QUERIED" in val:
                lhb_status = "NOT_QUERIED"
            elif "无龙虎榜数据" in val or "非异动日" in val or "LHB_NORMAL_NO_DATA" in val:
                lhb_status = "NORMAL_NO_DATA"
            elif "龙虎榜明细" in val or "LHB_HAS_DATA" in val:
                lhb_status = "HAS_DATA"
            else:
                lhb_status = "NOT_QUERIED"
        else:
            lhb_status = "HAS_DATA" if raw_lhb_val else "NOT_QUERIED"

    if lhb_status == "NOT_QUERIED":
        if query_mode == "not_queried":
            query_mode = "not_queried"
    elif lhb_status in ("NORMAL_NO_DATA", "HAS_DATA", "FAILED"):
        if query_mode == "not_queried":
            query_mode = "forced"

    display_map = {
        "HAS_DATA": "龙虎榜有数据",
        "NOT_QUERIED": "龙虎榜未查询（非异动触发）",
        "NORMAL_NO_DATA": "龙虎榜查询正常，当日无上榜记录",
        "FAILED": "龙虎榜查询失败",
        "FIELD_MISSING": "龙虎榜数据不完整",
    }
    display_text = display_map.get(lhb_status, f"龙虎榜状态未知: {lhb_status}")

    return {
        "status": lhb_status,
        "query_mode": query_mode,
        "display_text": display_text,
    }


def format_fund_lhb_provenance(fund_prov: dict, lhb_prov: dict) -> str:
    """[G-007] Format fund flow and LHB provenance summary for reports."""
    lines = ["\n\n📊 [G-007] 资金流与龙虎榜数据源口径："]

    ff_labels = {
        "HAS_DATA": "✅ 有数据",
        "FAILED": "❌ 查询失败",
        "NOT_QUERIED": "⬜ 未查询",
        "NORMAL_NO_DATA": "⬜ 无数据（正常）",
    }
    lines.append(f"  个股资金流: {ff_labels.get(fund_prov['individual_status'], fund_prov['individual_status'])}")
    if fund_prov['unit_verified']:
        lines.append(f"  个股资金流单位: ✅ 已校验")
    else:
        lines.append(f"  个股资金流单位: ❌ 未校验")
    lines.append(f"  板块资金流: {ff_labels.get(fund_prov['board_status'], fund_prov['board_status'])}")
    if fund_prov['news_reported_fund_flow']:
        lines.append(f"  新闻转述资金: ⚠️ 仅弱证据（不得作为主力资金强证据）")

    lhb_icon = {"HAS_DATA": "✅", "NOT_QUERIED": "⬜", "NORMAL_NO_DATA": "⬜", "FAILED": "❌"}.get(lhb_prov['status'], "⬜")
    lines.append(f"  龙虎榜: {lhb_icon} {lhb_prov['display_text']}")

    if fund_prov['strong_evidence_allowed']:
        lines.append(f"  主力资金强证据: ✅ 可用")
    else:
        lines.append(f"  主力资金强证据: ❌ 不可用（个股资金流接口不可用或单位未校验）")

    if fund_prov['conflict_summary']:
        lines.append(f"  ⚠️ {fund_prov['conflict_summary']}")

    return "\n".join(lines)
