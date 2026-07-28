# [C-006] financial_validator
"""财报数据异常检测（一期 + 二期：规则检测）

Phase 1: 毛利率跳变、现金流/利润背离、资产负债率异常、大额投资/筹资异动。
Phase 2: ROE 异常、营收-成本剪刀差、现金流质量、应收账款/营收背离、存货/成本背离。
"""

import logging
from typing import Optional

_logger = logging.getLogger(__name__)


def check_financial_anomalies(
    stock_code: str,
    # Phase 1 fields
    gross_margin: Optional[float] = None,
    gross_margin_prev: Optional[float] = None,
    operating_cashflow: Optional[float] = None,
    net_profit: Optional[float] = None,
    debt_ratio: Optional[float] = None,
    debt_ratio_prev: Optional[float] = None,
    total_invest_cashflow: Optional[float] = None,
    total_finance_cashflow: Optional[float] = None,
    # Phase 2 fields
    roe: Optional[float] = None,
    roe_prev: Optional[float] = None,
    revenue_growth: Optional[float] = None,
    operating_cost_growth: Optional[float] = None,
    accounts_receivable_growth: Optional[float] = None,
    inventory_growth: Optional[float] = None,
    # Deterministic report-card fields.  They currently do not add anomaly
    # rules, but remain accepted because callers pass the normalized input
    # dictionary as a whole.
    revenue_growth_yoy: Optional[float] = None,
    net_profit_growth_yoy: Optional[float] = None,
    total_assets: Optional[float] = None,
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

    # ── Phase 1 ──────────────────────────────────────────────────────────────

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

    # ── Phase 2 ──────────────────────────────────────────────────────────────

    # 5. ROE 异常：极端值或大幅跳变
    if roe is not None:
        if abs(roe) > 50:
            anomalies.append("extreme_roe")
            anomaly_details["extreme_roe"] = (
                f"净资产收益率 {roe:.1f}% 极端（|ROE| > 50%）"
            )
        if roe_prev is not None:
            roe_change = abs(roe - roe_prev)
            if roe_change > 20:
                anomalies.append("roe_jump")
                anomaly_details["roe_jump"] = (
                    f"ROE 从 {roe_prev:.1f}% 跳变至 {roe:.1f}%，"
                    f"变化 {roe_change:.1f} 个百分点"
                )

    # 6. 营收-成本剪刀差：营收增长但毛利率下降（成本增速 > 营收增速）
    if (
        revenue_growth is not None
        and operating_cost_growth is not None
        and gross_margin is not None
        and gross_margin_prev is not None
    ):
        if revenue_growth > 0 and operating_cost_growth > revenue_growth and gross_margin < gross_margin_prev:
            cost_excess = operating_cost_growth - revenue_growth
            margin_drop = gross_margin_prev - gross_margin
            if cost_excess > 5 and margin_drop > 3:
                anomalies.append("margin_squeeze")
                anomaly_details["margin_squeeze"] = (
                    f"营收增长 {revenue_growth:.1f}% 但成本增长 {operating_cost_growth:.1f}%，"
                    f"毛利率下降 {margin_drop:.1f} 个百分点"
                )

    # 7. 现金流质量：经营现金流/净利润比值异常
    if operating_cashflow is not None and net_profit is not None and net_profit != 0:
        cf_ratio = operating_cashflow / net_profit
        if cf_ratio < 0:
            anomalies.append("negative_cashflow_quality")
            anomaly_details["negative_cashflow_quality"] = (
                f"经营现金流/净利润 = {cf_ratio:.2f}，"
                f"利润未转化为实际现金流入"
            )
        elif cf_ratio > 5:
            anomalies.append("excessive_cashflow_quality")
            anomaly_details["excessive_cashflow_quality"] = (
                f"经营现金流/净利润 = {cf_ratio:.2f}，"
                f"现金流远超利润，可能存在非经常性因素"
            )

    # 8. 应收账款/营收背离：应收增速远超营收增速
    if (
        accounts_receivable_growth is not None
        and revenue_growth is not None
        and accounts_receivable_growth > 30
        and revenue_growth < 10
    ):
        ar_excess = accounts_receivable_growth - revenue_growth
        anomalies.append("ar_revenue_divergence")
        anomaly_details["ar_revenue_divergence"] = (
            f"应收账款增长 {accounts_receivable_growth:.1f}% 但营收仅增长 {revenue_growth:.1f}%，"
            f"差异 {ar_excess:.1f} 个百分点"
        )

    # 9. 存货/成本背离：存货增速远超营业成本增速
    if (
        inventory_growth is not None
        and operating_cost_growth is not None
        and inventory_growth > 30
        and operating_cost_growth < 10
    ):
        inv_excess = inventory_growth - operating_cost_growth
        anomalies.append("inventory_cost_divergence")
        anomaly_details["inventory_cost_divergence"] = (
            f"存货增长 {inventory_growth:.1f}% 但营业成本仅增长 {operating_cost_growth:.1f}%，"
            f"差异 {inv_excess:.1f} 个百分点"
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
