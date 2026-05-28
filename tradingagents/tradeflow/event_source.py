"""TradeFlow Event Source — 从 akshare 获取公告/回购/评级事件。

提供 fetch_daily_events(date) 函数，返回按 symbol 分组的事件列表。
供 event_catalyst 策略的 news_texts 参数使用。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

_BEARISH_NOTICE_TYPES = {"风险提示"}


@dataclass
class EventItem:
    """一个事件条目"""

    symbol: str = ""
    name: str = ""
    event_type: str = ""
    title: str = ""
    direction: str = "neutral"
    date: str = ""
    source: str = ""
    detail: dict = field(default_factory=dict)


def _bypass_proxy() -> None:
    env_keys = ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"]
    for key in env_keys:
        os.environ.pop(key, None)


def _normalize_symbol(code: str) -> str:
    raw = code.strip()
    if "." in raw:
        return raw.split(".")[0]
    return raw


def _date_str(ymd: str) -> str:
    if len(ymd) == 8:
        return f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
    return ymd


def _notice_direction(notice_type: str) -> str:
    if notice_type in _BEARISH_NOTICE_TYPES:
        return "bearish"
    if notice_type in {"重大事项", "资产重组", "融资公告", "持股变动"}:
        return "bullish"
    return "neutral"


def _rating_direction(rating: str) -> str:
    bullish_keywords = {"买入", "增持", "推荐", "强烈推荐", "优于大势"}
    bearish_keywords = {"卖出", "减持", "回避"}
    for kw in bullish_keywords:
        if kw in rating:
            return "bullish"
    for kw in bearish_keywords:
        if kw in rating:
            return "bearish"
    return "neutral"


def fetch_notice_events(
    date: str,
    symbol_filter: str = "全部",
) -> list[EventItem]:
    """获取沪深公告事件。

    Args:
        date: 'YYYYMMDD'
        symbol_filter: '全部' / '重大事项' / '财务报告' / '融资公告' / '风险提示'
    """
    try:
        _bypass_proxy()
        import akshare as ak

        df = ak.stock_notice_report(symbol=symbol_filter, date=date)
    except Exception as e:
        logger.warning("fetch_notice_events failed: %s", e)
        return []

    if df is None or df.empty:
        return []

    items: list[EventItem] = []
    for _, row in df.iterrows():
        code = str(row.get("代码", "")).strip()
        if not code:
            continue
        notice_type = str(row.get("公告类型", ""))
        title = str(row.get("公告标题", ""))
        name = str(row.get("名称", ""))
        pub_date = str(row.get("公告日期", ""))

        items.append(
            EventItem(
                symbol=_normalize_symbol(code),
                name=name,
                event_type="notice",
                title=title,
                direction=_notice_direction(notice_type),
                date=_date_str(date),
                source="eastmoney",
                detail={
                    "notice_type": notice_type,
                    "url": str(row.get("网址", "")),
                    "pub_date": pub_date,
                },
            )
        )
    return items


def fetch_buyback_events() -> list[EventItem]:
    """获取回购事件。只取最新公告日期在近 30 天内的，全部标记为 bullish。"""
    try:
        _bypass_proxy()
        import akshare as ak

        df = ak.stock_repurchase_em()
    except Exception as e:
        logger.warning("fetch_buyback_events failed: %s", e)
        return []

    if df is None or df.empty:
        return []

    import pandas as pd

    cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    items: list[EventItem] = []

    for _, row in df.iterrows():
        code = str(row.get("股票代码", "")).strip()
        if not code:
            continue

        latest_date_raw = str(row.get("最新公告日期", ""))
        try:
            latest_date = pd.Timestamp(latest_date_raw).strftime("%Y-%m-%d")
        except Exception:
            continue

        if latest_date < cutoff:
            continue

        name = str(row.get("股票简称", ""))
        price_range = str(row.get("计划回购价格区间", ""))
        qty_low = str(row.get("计划回购数量区间-下限", ""))
        qty_high = str(row.get("计划回购数量区间-上限", ""))

        desc = f"{name}: 回购计划 价格区间{price_range} 数量{qty_low}-{qty_high}"

        items.append(
            EventItem(
                symbol=_normalize_symbol(code),
                name=name,
                event_type="buyback",
                title=desc,
                direction="bullish",
                date=latest_date,
                source="eastmoney",
                detail={col: str(row.get(col, "")) for col in df.columns},
            )
        )
    return items


def fetch_rating_events(date: str) -> list[EventItem]:
    """获取分析师评级事件。

    Args:
        date: 'YYYYMMDD'
    """
    try:
        _bypass_proxy()
        import akshare as ak

        df = ak.stock_rank_forecast_cninfo(date=date)
    except Exception as e:
        logger.warning("fetch_rating_events failed: %s", e)
        return []

    if df is None or df.empty:
        return []

    items: list[EventItem] = []
    for _, row in df.iterrows():
        code = str(row.get("证券代码", "")).strip()
        if not code:
            continue

        name = str(row.get("证券简称", ""))
        rating = str(row.get("投资评级", ""))
        org = str(row.get("研究机构简称", ""))
        analyst = str(row.get("研究员名称", ""))
        change = str(row.get("评级变化", ""))

        desc = f"{name}: {org} {analyst} 评级{rating}"
        if change and change != "nan":
            desc += f"({change})"

        items.append(
            EventItem(
                symbol=_normalize_symbol(code),
                name=name,
                event_type="rating",
                title=desc,
                direction=_rating_direction(rating),
                date=_date_str(date),
                source="cninfo",
                detail={
                    "rating": rating,
                    "org": org,
                    "analyst": analyst,
                    "change": change,
                },
            )
        )
    return items


def fetch_daily_events(date: str) -> dict[str, list[str]]:
    """获取指定日期的全部事件，返回 {symbol: [title1, title2, ...]} 格式。

    这个格式直接兼容 event_catalyst.score_event_catalyst() 的 news_texts 参数。

    Args:
        date: 'YYYYMMDD'
    Returns:
        {'002138': ['顺络电子:关于回购进展公告', ...], '600519': [...]}
    """
    events_map: dict[str, list[str]] = {}

    notice_items = fetch_notice_events(date)
    buyback_items = fetch_buyback_events()
    rating_items = fetch_rating_events(date)

    all_items = notice_items + buyback_items + rating_items

    for item in all_items:
        sym = item.symbol
        if sym not in events_map:
            events_map[sym] = []
        if item.title and item.title != "nan":
            events_map[sym].append(item.title)

    return events_map


def fetch_events_for_symbol(symbol: str, date: str) -> list[str]:
    """获取单只股票的事件标题列表。用于候选引擎按需查询。"""
    raw_sym = _normalize_symbol(symbol)
    daily = fetch_daily_events(date)
    return daily.get(raw_sym, [])
