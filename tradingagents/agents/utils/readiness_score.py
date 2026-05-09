# [C-008] readiness_score
"""报告质量评分（简化版：数据完整度 + 置信度）"""

from enum import Enum
from typing import Optional


class ConfidenceLevel(Enum):
    """置信度等级"""
    HIGH = "高"
    MEDIUM = "中"
    LOW = "低"


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
    # 基础分 = 数据完整度
    base_score = data_completeness

    # 扣分项
    if has_contradictory_signals:
        base_score -= 20

    if analyst_agreement_level is not None and analyst_agreement_level < 0.5:
        base_score -= 15

    if event_risk_active:
        base_score -= 10

    # 判定等级
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


def format_readiness_score(score: dict) -> str:
    """格式化 readiness score 为报告文本"""
    return (
        f"\n\n📋 [C-008] 执行就绪度评分\n"
        f"- 数据完整度：{score['data_completeness']}%\n"
        f"- 置信度：{score['confidence']}\n"
        f"- 总结：{score['summary']}"
    )
