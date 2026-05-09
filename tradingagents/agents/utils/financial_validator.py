# [C-006] financial_validator
"""财报数据异常检测（一期：规则检测）"""

import logging
from typing import Optional

_logger = logging.getLogger(__name__)


def check_financial_anomalies(
    stock_code: str,
    gross_margin: Optional[float] = None,
    gross_margin_prev: Optional[float] = None,
    operating_cashflow: Optional[float] = None,
    net_profit: Optional[float] = None,
    debt_ratio: Optional[float] = None,
    debt_ratio_prev: Optional[float] = None,
    total_invest_cashflow: Optional[float] = None,
    total_finance_cashflow: Optional[float] = None,
) -> dict:
    """
    检测财报数据异常。

    返回:
        {
            "has_anomaly": bool,
            "anomalies": list[str],
            "anomaly_details": dict,
            "needs_manual_review": bool,
        }
    """
    anomalies = []
    anomaly_details = {}

    # 1. 毛利率跳变 > 20%
    if gross_margin is not None and gross_margin_prev is not None:
        margin_change = abs(gross_margin - gross_margin_prev)
        if margin_change > 20:
            anomalies.append("gross_margin_jump")
            anomaly_details["gross_margin_jump"] = (
                f"毛利率从 {gross_margin_prev:.1f}% 跳变至 {gross_margin:.1f}%，"
                f"变化 {margin_change:.1f} 个百分点"
            )

    # 2. 现金流与利润背离（利润增但经营现金流减）
    if operating_cashflow is not None and net_profit is not None:
        if net_profit > 0 and operating_cashflow < 0:
            anomalies.append("cashflow_profit_divergence")
            anomaly_details["cashflow_profit_divergence"] = (
                f"净利润为正（{net_profit:.2f}亿），但经营现金流为负（{operating_cashflow:.2f}亿）"
            )

    # 3. 资产负债率异常
    if debt_ratio is not None:
        if debt_ratio > 70:
            anomalies.append("high_debt_ratio")
            anomaly_details["high_debt_ratio"] = f"资产负债率 {debt_ratio:.1f}% > 70%"
        if debt_ratio_prev is not None:
            debt_change = abs(debt_ratio - debt_ratio_prev)
            if debt_change > 10:
                anomalies.append("debt_ratio_jump")
                anomaly_details["debt_ratio_jump"] = (
                    f"资产负债率从 {debt_ratio_prev:.1f}% 变为 {debt_ratio:.1f}%，"
                    f"变化 {debt_change:.1f} 个百分点"
                )

    # 4. 大额投资/筹资异动（简化判断：绝对值 > 50 亿）
    if total_invest_cashflow is not None and abs(total_invest_cashflow) > 50:
        anomalies.append("large_invest_cashflow")
        anomaly_details["large_invest_cashflow"] = (
            f"投资活动现金流异动：{total_invest_cashflow:.2f}亿"
        )

    if total_finance_cashflow is not None and abs(total_finance_cashflow) > 50:
        anomalies.append("large_finance_cashflow")
        anomaly_details["large_finance_cashflow"] = (
            f"筹资活动现金流异动：{total_finance_cashflow:.2f}亿"
        )

    has_anomaly = len(anomalies) > 0

    if has_anomaly:
        _logger.warning(
            "[C-006] financial_validator: %s 检测到异常: %s",
            stock_code,
            ", ".join(anomalies)
        )

    return {
        "has_anomaly": has_anomaly,
        "anomalies": anomalies,
        "anomaly_details": anomaly_details,
        "needs_manual_review": has_anomaly,
    }


def format_financial_anomaly_warning(anomaly_info: dict) -> str:
    """格式化财务异常警告"""
    if not anomaly_info["has_anomaly"]:
        return ""

    warning = "\n\n📊 [C-006] 财务数据异常检测\n"

    for anomaly_type in anomaly_info["anomalies"]:
        detail = anomaly_info["anomaly_details"].get(anomaly_type, "")
        warning += f"- {detail}\n"

    if anomaly_info["needs_manual_review"]:
        warning += "\n⚠️ 以上异常需人工复核，数据可信度降级。\n"

    return warning
