"""TradeFlow Event Source — 从 akshare 获取公告/回购/评级事件。

提供 fetch_daily_events(date) 函数，返回按 symbol 分组的事件列表。
供 event_catalyst 策略的 news_texts 参数使用。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class EventSourceStatus(Enum):
    OK = "OK"
    FAILED = "FAILED"
    STALE = "STALE"
    PARTIAL = "PARTIAL"  # [T-007] event_source_failure_status


@dataclass
class _SubFetchResult:  # [T-007] event_source_failure_status
    """Internal result from a sub-fetch function, tracking success/failure."""
    items: list = field(default_factory=list)
    success: bool = True
    error: str = ""


@dataclass
class EventSourceResult:
    status: EventSourceStatus = EventSourceStatus.OK
    events_map: dict[str, list[str]] = field(default_factory=dict)
    items_by_symbol: dict[str, list[EventItem]] = field(default_factory=dict)
    error_message: str = ""
    event_count: int = 0
    symbols_count: int = 0
    source_statuses: dict[str, str] = field(default_factory=dict)  # [T-007] per-source status
    failed_sources: list[str] = field(default_factory=list)  # [T-007] failed source names

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


def _fetch_notice_events_raw(date: str, symbol_filter: str = "全部") -> list[EventItem]:  # [T-007]
    _bypass_proxy()
    import akshare as ak

    df = ak.stock_notice_report(symbol=symbol_filter, date=date)
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


def _fetch_buyback_events_raw() -> list[EventItem]:  # [T-007]
    _bypass_proxy()
    import akshare as ak

    df = ak.stock_repurchase_em()
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


def _fetch_rating_events_raw(date: str) -> list[EventItem]:  # [T-007]
    _bypass_proxy()
    import akshare as ak

    df = ak.stock_rank_forecast_cninfo(date=date)
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
        return _fetch_notice_events_raw(date, symbol_filter)
    except Exception as e:
        logger.warning("fetch_notice_events failed: %s", e)
        return []


def fetch_buyback_events() -> list[EventItem]:
    """获取回购事件。只取最新公告日期在近 30 天内的，全部标记为 bullish。"""
    try:
        return _fetch_buyback_events_raw()
    except Exception as e:
        logger.warning("fetch_buyback_events failed: %s", e)
        return []


def fetch_rating_events(date: str) -> list[EventItem]:
    """获取分析师评级事件。

    Args:
        date: 'YYYYMMDD'
    """
    try:
        return _fetch_rating_events_raw(date)
    except Exception as e:
        logger.warning("fetch_rating_events failed: %s", e)
        return []


def _fetch_notice_events_detailed(date: str, symbol_filter: str = "全部") -> _SubFetchResult:  # [T-007]
    try:
        items = _fetch_notice_events_raw(date, symbol_filter)
        return _SubFetchResult(items=items, success=True)
    except Exception as e:
        return _SubFetchResult(items=[], success=False, error=str(e))


def _fetch_buyback_events_detailed() -> _SubFetchResult:  # [T-007]
    try:
        items = _fetch_buyback_events_raw()
        return _SubFetchResult(items=items, success=True)
    except Exception as e:
        return _SubFetchResult(items=[], success=False, error=str(e))


def _fetch_rating_events_detailed(date: str) -> _SubFetchResult:  # [T-007]
    try:
        items = _fetch_rating_events_raw(date)
        return _SubFetchResult(items=items, success=True)
    except Exception as e:
        return _SubFetchResult(items=[], success=False, error=str(e))


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


def fetch_daily_events_detailed(date: str) -> EventSourceResult:
    """获取指定日期的全部事件，返回包含完整元数据和状态的结果。  # [T-006] event_source_discovery

    与 fetch_daily_events() 不同，此函数返回 EventSourceResult，
    包含按 symbol 分组的 EventItem 列表（event_type/direction/source/date）
    以及事件源状态（OK/FAILED/STALE/PARTIAL），便于 Discovery 和 Daily Plan 输出
    事件来源、标题摘要、事件类型、去重结果和证据引用。

    [T-007] event_source_failure_status — 子 fetch 失败不再被吞掉：
    - 全部成功（含无事件）= OK
    - 部分失败 = PARTIAL
    - 全部失败 = FAILED

    Args:
        date: 'YYYYMMDD'
    Returns:
        EventSourceResult
    """
    result = EventSourceResult()

    # [T-007] event_source_failure_status — use detailed sub-fetch helpers
    sub_results: dict[str, _SubFetchResult] = {}
    try:
        sub_results["notice"] = _fetch_notice_events_detailed(date)
    except Exception as e:
        sub_results["notice"] = _SubFetchResult(items=[], success=False, error=str(e))
    try:
        sub_results["buyback"] = _fetch_buyback_events_detailed()
    except Exception as e:
        sub_results["buyback"] = _SubFetchResult(items=[], success=False, error=str(e))
    try:
        sub_results["rating"] = _fetch_rating_events_detailed(date)
    except Exception as e:
        sub_results["rating"] = _SubFetchResult(items=[], success=False, error=str(e))

    # Aggregate statuses
    all_items: list[EventItem] = []
    failed_sources: list[str] = []
    source_statuses: dict[str, str] = {}

    for source_name, sr in sub_results.items():
        if sr.success:
            source_statuses[source_name] = "OK"
            all_items.extend(sr.items)
        else:
            source_statuses[source_name] = f"FAILED: {_sanitize_error(sr.error)}"
            failed_sources.append(source_name)
            all_items.extend(sr.items)

    result.source_statuses = source_statuses
    result.failed_sources = failed_sources

    # Determine aggregate status
    n_total = len(sub_results)
    n_failed = len(failed_sources)
    if n_failed == 0:
        result.status = EventSourceStatus.OK
    elif n_failed == n_total:
        result.status = EventSourceStatus.FAILED
        result.error_message = "; ".join(
            f"{k}: {_sanitize_error(v.error)}" for k, v in sub_results.items()
        )
    else:
        result.status = EventSourceStatus.PARTIAL
        result.error_message = "; ".join(
            f"{k}: {_sanitize_error(v.error)}" for k, v in sub_results.items() if not v.success
        )

    # Deduplicate and group by symbol
    seen_titles_by_symbol: dict[str, set[str]] = {}
    for item in all_items:
        sym = item.symbol
        if not sym:
            continue
        if sym not in result.items_by_symbol:
            result.items_by_symbol[sym] = []
            result.events_map[sym] = []
            seen_titles_by_symbol[sym] = set()

        if item.title and item.title != "nan":
            if item.title not in seen_titles_by_symbol[sym]:
                result.items_by_symbol[sym].append(item)
                result.events_map[sym].append(item.title)
                seen_titles_by_symbol[sym].add(item.title)

    result.event_count = sum(len(v) for v in result.items_by_symbol.values())
    result.symbols_count = len(result.items_by_symbol)

    return result


def _sanitize_error(error: str) -> str:  # [T-007] event_source_failure_status
    """Sanitize error message to avoid leaking sensitive info (API keys, tokens)."""
    import re
    cleaned = error
    for pattern in [r'key[=:]\s*\S+', r'token[=:]\s*\S+', r'api[_-]?key[=:]\s*\S+']:
        cleaned = re.sub(pattern, '[REDACTED]', cleaned, flags=re.IGNORECASE)
    return cleaned[:200]


def fetch_events_for_symbol(symbol: str, date: str) -> list[str]:
    """获取单只股票的事件标题列表。用于候选引擎按需查询。"""
    raw_sym = _normalize_symbol(symbol)
    daily = fetch_daily_events(date)
    return daily.get(raw_sym, [])
