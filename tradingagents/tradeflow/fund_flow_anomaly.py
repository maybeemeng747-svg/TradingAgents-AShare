# [T-003] fund_flow_anomaly_pool
"""Fund Flow Anomaly Detection — detect capital anomaly for TradeFlow candidates.

Uses individual stock fund flow data (近5-20日) to identify:
- Consecutive main-capital net inflows
- Abnormal capital proportion
- Individual/sector fund flow resonance

Outputs:
- fund_flow_anomaly_score: 0 to 25 bonus points
- fund_flow_anomaly_tags: e.g. CONSECUTIVE_INFLOW, HIGH_PROPORTION, BOARD_RESONANCE
- fund_flow_anomaly_refs: evidence references
- fund_flow_unit_verified: whether unit has been validated
- fund_flow_individual_summary: individual fund flow summary
- fund_flow_board_summary: board fund flow summary (separate from individual)

Design constraints:
- Individual fund flow and board fund flow MUST be recorded separately.
- Fund flow anomaly is only a bonus; does NOT trigger strong conclusions alone.
- When unit is unverified, no high-confidence fund flow signal is output.
- No external LLM calls.
- No strong buy/sell words.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


MAX_FUND_FLOW_BONUS = 25

FUND_FLOW_TAG_CONSECUTIVE_INFLOW = "CONSECUTIVE_INFLOW"
FUND_FLOW_TAG_HIGH_PROPORTION = "HIGH_PROPORTION"
FUND_FLOW_TAG_BOARD_RESONANCE = "BOARD_RESONANCE"
FUND_FLOW_TAG_LARGE_SINGLE_DAY = "LARGE_SINGLE_DAY"
FUND_FLOW_TAG_NET_OUTFLOW_DOMINANT = "NET_OUTFLOW_DOMINANT"
ALL_FUND_FLOW_TAGS = {
    FUND_FLOW_TAG_CONSECUTIVE_INFLOW,
    FUND_FLOW_TAG_HIGH_PROPORTION,
    FUND_FLOW_TAG_BOARD_RESONANCE,
    FUND_FLOW_TAG_LARGE_SINGLE_DAY,
    FUND_FLOW_TAG_NET_OUTFLOW_DOMINANT,
}

_INDIVIDUAL_FUND_FLOW_PATTERN = re.compile(
    r"近\d+日主力资金净流向|主力净流入|净流入-净额|主力净流入-净额"
)
_BOARD_FUND_FLOW_PATTERN = re.compile(
    r"板块资金流向|行业.*资金流|stock_board_industry_fund_flow"
)

_ANOMALY_THRESHOLD_WAN = 50000  # 5000万 = 50,000 万元
_CONSECUTIVE_INFLOW_MIN_DAYS = 3
_HIGH_PROPORTION_THRESHOLD = 5.0  # 5%

_STRONG_BUY_SELL_WORDS = {
    "立即买入", "重仓买入", "立即清仓", "满仓", "梭哈",
    "强力买入", "强烈推荐", "必买", "抄底", "追涨", "杀跌",
}


@dataclass
class FundFlowAnomalyResult:
    fund_flow_anomaly_score: float = 0.0
    fund_flow_anomaly_tags: list[str] = field(default_factory=list)
    fund_flow_anomaly_refs: list[dict] = field(default_factory=list)
    fund_flow_unit_verified: bool = False
    fund_flow_individual_summary: str = ""
    fund_flow_board_summary: str = ""


def _sanitize(text: str) -> str:
    for w in _STRONG_BUY_SELL_WORDS:
        if w in text:
            text = text.replace(w, "***")
    return text


def _extract_net_flows(fund_flow_text: str) -> list[float]:
    """Extract main capital net flow values from individual fund flow text.

    Strategy:
    1. If header has '主力净流入-净额' or '主力净流入' column, extract that column.
    2. Fallback: scan lines after header for large absolute values.

    Returns list of net flow values (positive = inflow, negative = outflow).
    """
    if not fund_flow_text or "获取失败" in fund_flow_text or "不可用" in fund_flow_text:
        return []

    lines = [l.strip() for l in fund_flow_text.split("\n") if l.strip()]
    if not lines:
        return []

    header_line = ""
    header_idx = -1
    for i, line in enumerate(lines):
        if ("主力" in line or "净流入" in line or "净流出" in line) and "净额" in line:
            header_line = line
            header_idx = i
            break

    if header_idx < 0:
        for i, line in enumerate(lines):
            if "主力" in line or "净流入" in line or "净流出" in line:
                parts = line.split()
                if len(parts) >= 4:
                    header_line = line
                    header_idx = i
                    break

    if header_idx < 0:
        return []

    target_col_idx = -1
    parts = header_line.split()
    for j, part in enumerate(parts):
        if "主力净流入" in part:
            target_col_idx = j
            break

    values: list[float] = []

    for line in lines[header_idx + 1:]:
        row_parts = line.split()
        if len(row_parts) < 2:
            continue
        if target_col_idx >= 0 and target_col_idx < len(row_parts):
            raw = row_parts[target_col_idx].replace(',', '').replace('–', '-')
            try:
                values.append(float(raw))
            except ValueError:
                continue
        else:
            for part in row_parts:
                try:
                    val = float(part.replace(',', '').replace('–', '-'))
                    if abs(val) >= 100:
                        values.append(val)
                except ValueError:
                    continue

    return values


def _detect_consecutive_inflow(net_flows: list[float], lookback: int = 5) -> tuple[bool, int, list[dict]]:
    """Detect consecutive main-capital net inflows in recent N days.

    Returns (is_consecutive, streak_length, refs).
    """
    if not net_flows:
        return False, 0, []

    recent = net_flows[:lookback]
    streak = 0
    refs: list[dict] = []

    for i, val in enumerate(recent):
        if val > 0:
            streak += 1
        else:
            break

    if streak >= _CONSECUTIVE_INFLOW_MIN_DAYS:
        refs.append({
            "field": "consecutive_inflow",
            "matched_text": f"近{streak}日连续主力净流入",
            "source": "individual_fund_flow",
        })
        return True, streak, refs

    return False, streak, []


def _detect_large_single_day(net_flows: list[float]) -> tuple[bool, float, list[dict]]:
    """Detect a single day with unusually large net inflow/outflow.

    Returns (is_large, max_abs_value, refs).
    """
    if not net_flows:
        return False, 0.0, []

    max_val = max(net_flows, key=abs)
    if abs(max_val) >= _ANOMALY_THRESHOLD_WAN:
        direction = "净流入" if max_val > 0 else "净流出"
        refs = [{
            "field": "large_single_day",
            "matched_text": f"单日主力{direction} {abs(max_val):.0f} 万元",
            "source": "individual_fund_flow",
        }]
        return True, max_val, refs

    return False, max_val, []


def _detect_high_proportion(fund_flow_text: str) -> tuple[bool, float, list[dict]]:
    """Detect abnormal capital proportion (占比 >= 5%).

    Returns (is_high, max_proportion, refs).
    """
    if not fund_flow_text or "获取失败" in fund_flow_text:
        return False, 0.0, []

    max_pct = 0.0
    refs: list[dict] = []

    explicit_pct = re.findall(r'[-–+]?(\d+\.?\d*)%', fund_flow_text)
    for pct_str in explicit_pct:
        try:
            pct = abs(float(pct_str))
            if pct > max_pct:
                max_pct = pct
        except ValueError:
            continue

    proportion_lines = []
    for line in fund_flow_text.split("\n"):
        if "占比" in line or "净占比" in line:
            proportion_lines.append(line)

    for line in proportion_lines:
        numbers = re.findall(r'[-–+]?[\d,]+\.?\d*', line)
        for num_str in numbers:
            try:
                val = abs(float(num_str.replace(',', '').replace('–', '-')))
                if val >= 1.0 and val < 100 and val > max_pct:
                    max_pct = val
            except ValueError:
                continue

    if max_pct >= _HIGH_PROPORTION_THRESHOLD:
        refs.append({
            "field": "high_proportion",
            "matched_text": f"主力资金占比 {max_pct:.1f}%",
            "source": "individual_fund_flow",
        })
        return True, max_pct, refs

    return False, max_pct, []


def _detect_board_resonance(
    individual_text: str,
    board_text: str,
) -> tuple[bool, list[dict]]:
    """Detect if individual stock fund flow resonates with sector board fund flow.

    Both individual inflow AND sector inflow must be positive.
    Returns (is_resonance, refs).
    """
    if not individual_text or not board_text:
        return False, []
    if "获取失败" in individual_text or "获取失败" in board_text:
        return False, []

    ind_values = _extract_net_flows(individual_text)
    if not ind_values:
        return False, []

    recent_ind = ind_values[:3]
    ind_positive = sum(1 for v in recent_ind if v > 0)
    if ind_positive < 2:
        return False, []

    board_positive_kw = re.compile(r"主力.*净流入|净流入.*\d+")
    board_negative_kw = re.compile(r"主力.*净流出|净流出.*\d+")

    board_has_inflow = False
    board_sector_name = ""
    for line in board_text.split("\n"):
        if board_positive_kw.search(line) and not board_negative_kw.search(line):
            board_has_inflow = True
            parts = line.split()
            if parts:
                board_sector_name = parts[0][:20]
            break

    if not board_has_inflow:
        return False, []

    refs = [{
        "field": "board_resonance",
        "matched_text": f"个股主力流入+板块资金共振(板块: {board_sector_name})",
        "source": "board_fund_flow+individual_fund_flow",
    }]
    return True, refs


def _detect_net_outflow_dominant(net_flows: list[float], lookback: int = 10) -> tuple[bool, int, list[dict]]:
    """Detect if net outflows dominate in recent N days.

    Returns (is_dominant, outflow_count, refs).
    """
    if not net_flows:
        return False, 0, []

    recent = net_flows[:lookback]
    outflow_count = sum(1 for v in recent if v < 0)
    total = len(recent)

    if total >= 5 and outflow_count >= total * 0.7:
        refs = [{
            "field": "net_outflow_dominant",
            "matched_text": f"近{total}日中{outflow_count}日主力净流出",
            "source": "individual_fund_flow",
        }]
        return True, outflow_count, refs

    return False, outflow_count, []


def _verify_unit(fund_flow_text: str) -> bool:
    """Verify the unit of fund flow data.

    Returns True if unit is confirmed as 万元 (the AKShare standard).
    Returns False if unit cannot be confirmed.
    """
    if not fund_flow_text or "获取失败" in fund_flow_text:
        return False

    unit_hints = ("万元", "万", "净额", "主力净流入-净额", "净流入-净额")
    return any(h in fund_flow_text for h in unit_hints)


def _summarize_individual(fund_flow_text: str, net_flows: list[float]) -> str:
    """Create a brief summary of individual fund flow."""
    if not fund_flow_text or "获取失败" in fund_flow_text:
        return "个股资金流数据不可用"

    if not net_flows:
        return "个股资金流无可解析数据"

    total = sum(net_flows[:5])
    pos_days = sum(1 for v in net_flows[:5] if v > 0)
    direction = "净流入" if total > 0 else "净流出"
    return f"个股近{min(5, len(net_flows))}日主力合计{direction} {abs(total):.0f} 万元，{pos_days}/{min(5, len(net_flows))}日为正值"


def _summarize_board(board_text: str) -> str:
    """Create a brief summary of board fund flow."""
    if not board_text or "获取失败" in board_text:
        return "板块资金流数据不可用"

    if "不可用" in board_text:
        return "板块资金流数据暂不可用"

    lines = board_text.strip().split("\n")
    if len(lines) <= 2:
        return "板块资金流数据不足"

    return f"板块资金流可用（{len(lines)}行数据）"


def detect_fund_flow_anomaly(
    fund_flow_individual: Optional[str] = None,
    fund_flow_board: Optional[str] = None,
    lookback_days: int = 5,
) -> FundFlowAnomalyResult:
    """Detect fund flow anomaly from individual and board fund flow data.

    Args:
        fund_flow_individual: Individual stock fund flow text (近5-20日).
        fund_flow_board: Board/sector fund flow text.
        lookback_days: Number of recent days to analyze (5-20).

    Returns:
        FundFlowAnomalyResult with anomaly tags, score, and summaries.
    """
    lookback_days = max(5, min(20, lookback_days))

    individual_text = fund_flow_individual or ""
    board_text = fund_flow_board or ""

    unit_verified = _verify_unit(individual_text)

    net_flows = _extract_net_flows(individual_text)

    tags: list[str] = []
    refs: list[dict] = []
    score = 0.0

    is_consecutive, streak, cons_refs = _detect_consecutive_inflow(net_flows, lookback_days)
    if is_consecutive:
        tags.append(FUND_FLOW_TAG_CONSECUTIVE_INFLOW)
        refs.extend(cons_refs)
        bonus = min(streak * 3, 12)
        score += bonus

    is_large, max_val, large_refs = _detect_large_single_day(net_flows)
    if is_large and unit_verified:
        tags.append(FUND_FLOW_TAG_LARGE_SINGLE_DAY)
        refs.extend(large_refs)
        score += 5

    is_high_prop, max_pct, prop_refs = _detect_high_proportion(individual_text)
    if is_high_prop and unit_verified:
        tags.append(FUND_FLOW_TAG_HIGH_PROPORTION)
        refs.extend(prop_refs)
        score += 5

    is_resonance, resonance_refs = _detect_board_resonance(individual_text, board_text)
    if is_resonance and unit_verified:
        tags.append(FUND_FLOW_TAG_BOARD_RESONANCE)
        refs.extend(resonance_refs)
        score += 5

    is_outflow, outflow_count, outflow_refs = _detect_net_outflow_dominant(net_flows, lookback_days)
    if is_outflow:
        tags.append(FUND_FLOW_TAG_NET_OUTFLOW_DOMINANT)
        refs.extend(outflow_refs)

    if not unit_verified and tags:
        verified_tags = [t for t in tags if t in {
            FUND_FLOW_TAG_CONSECUTIVE_INFLOW,
            FUND_FLOW_TAG_NET_OUTFLOW_DOMINANT,
        }]
        dropped = set(tags) - set(verified_tags)
        tags = verified_tags
        score = min(streak * 3, 12) if is_consecutive else 0
        if dropped:
            refs.append({
                "field": "unit_verification",
                "matched_text": f"单位未校验，降级以下标签: {', '.join(sorted(dropped))}",
                "source": "fund_flow_unit_check",
            })

    score = min(score, MAX_FUND_FLOW_BONUS)

    ind_summary = _summarize_individual(individual_text, net_flows)
    board_summary = _summarize_board(board_text)

    return FundFlowAnomalyResult(
        fund_flow_anomaly_score=round(score, 2),
        fund_flow_anomaly_tags=sorted(tags),
        fund_flow_anomaly_refs=refs,
        fund_flow_unit_verified=unit_verified,
        fund_flow_individual_summary=_sanitize(ind_summary),
        fund_flow_board_summary=_sanitize(board_summary),
    )
