from langchain_core.tools import tool
from typing import Annotated
from tradingagents.dataflows.interface import route_to_vendor


@tool
def get_board_fund_flow() -> str:
    """获取今日行业板块资金流向排名，用于判断板块轮动信号和个股所在板块的资金吸引力。"""
    return route_to_vendor("get_board_fund_flow")


@tool
def get_individual_fund_flow(
    symbol: Annotated[str, "股票代码，格式如 600519.SH"],
) -> str:
    """获取个股近5日主力资金净流向，判断机构资金进出方向。symbol 格式如 600519.SH。"""
    return route_to_vendor("get_individual_fund_flow", symbol)


@tool
def get_lhb_detail(
    symbol: Annotated[str, "股票代码，格式如 600519.SH"],
    date: Annotated[str, "日期，格式 YYYY-MM-DD"],
    force: Annotated[bool, "是否强制查询，默认 False 按需触发"] = False,
) -> str:
    """获取个股龙虎榜数据，非异动日无数据属正常。symbol 格式如 600519.SH，date 格式 YYYY-MM-DD。
    force=True 时才真正查询 API，否则返回提示信息避免限流。"""
    return route_to_vendor("get_lhb_detail", symbol, date, force=force)


@tool
def get_zt_pool(
    date: Annotated[str, "日期，格式 YYYY-MM-DD"],
) -> str:
    """获取市场涨停板情绪池，反映市场整体情绪温度，date 格式 YYYY-MM-DD。"""
    return route_to_vendor("get_zt_pool", date)


@tool
def get_hot_stocks_xq() -> str:
    """获取雪球热搜股票列表，反映散户当前关注热点。"""
    return route_to_vendor("get_hot_stocks_xq")


@tool  # [DATA-P0-603629] astock_source_fallback
def get_announcements(
    symbol: Annotated[str, "股票代码，格式如 600519.SH"],
) -> str:
    """获取个股近期公告列表（来自巨潮资讯网 cninfo），包括风险提示、减持、问询函等关键公告。symbol 格式如 600519.SH。"""
    return route_to_vendor("get_announcements", symbol)


@tool  # [DATA-010] margin_trading_raw_evidence
def get_margin_trading(
    symbol: Annotated[str, "股票代码，格式如 600519.SH"],
) -> str:
    """获取个股融资融券数据，包括融资余额、融券余额、融资买入额等。symbol 格式如 600519.SH。"""
    return route_to_vendor("get_margin_trading", symbol)


@tool  # [DATA-012A] rating_data_collector_wiring
def get_ratings(
    symbol: Annotated[str, "股票代码，格式如 600519.SH"],
) -> str:
    """获取个股分析师评级数据，包括机构评级、目标价、评级变动等。symbol 格式如 600519.SH。"""
    return route_to_vendor("get_ratings", symbol)


@tool  # [DATA-011A] research_report_collector_wiring
def get_research_report(
    symbol: Annotated[str, "股票代码，格式如 600519.SH"],
) -> str:
    """获取个股券商研报摘要与评级记录。"""
    return route_to_vendor("get_research_report", symbol)


@tool  # [DATA-013A] buyback_collector_wiring
def get_buybacks(
    symbol: Annotated[str, "股票代码，格式如 600519.SH"],
) -> str:
    """获取个股回购计划、金额和实施进度。"""
    return route_to_vendor("get_buybacks", symbol)
