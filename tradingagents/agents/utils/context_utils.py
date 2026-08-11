from __future__ import annotations

import csv
import io
import json
import math
import re
from datetime import datetime, timedelta, time
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from tradingagents.dataflows.trade_calendar import (
    CN_TZ,
    cn_market_phase,
    cn_today_str,
    is_cn_symbol,
    is_cn_trading_day,
    previous_cn_trading_day,
)

US_TZ = ZoneInfo("America/New_York")
HK_TZ = ZoneInfo("Asia/Hong_Kong")  # [HK-001] hk_market_boundary

USER_CONTEXT_KEYS = (
    "objective",
    "risk_profile",
    "investment_horizon",
    "cash_available",
    "current_position",
    "current_position_pct",
    "average_cost",
    "max_loss_pct",
    "constraints",
    "user_notes",
)

_MAX_REALTIME_QUOTE_AGE = timedelta(minutes=15)


def market_today_str(symbol: str, now: datetime | None = None) -> str:
    """Return today's date in the instrument's local market timezone."""
    market_country = infer_instrument_context(symbol).get("market_country")
    market_tz = {
        "US": US_TZ,
        "HK": HK_TZ,
        "CN": CN_TZ,
    }.get(str(market_country), CN_TZ)
    current = now or datetime.now(market_tz)
    if current.tzinfo is None:
        current = current.replace(tzinfo=market_tz)
    else:
        current = current.astimezone(market_tz)
    return current.date().isoformat()


def _quote_key_matches_symbol(key: Any, symbol: str) -> bool:
    """Match only an exact or safely normalized quote key to the request."""
    candidate = str(key or "").strip().upper()
    requested = str(symbol or "").strip().upper()
    if not candidate or not requested:
        return False
    if candidate == requested:
        return True

    cn_match = re.fullmatch(r"(\d{6})\.(SH|SZ|BJ)", requested)
    if not cn_match:
        return False
    code, exchange = cn_match.groups()
    return candidate in {
        code,
        f"{exchange}{code}",
        f"{code}.{exchange}",
    }


def infer_instrument_context(symbol: str) -> dict[str, Any]:
    normalized = (symbol or "").strip().upper()
    if is_cn_symbol(normalized):
        exchange = _infer_cn_exchange(normalized)
        return {
            "symbol": normalized,
            "security_name": normalized,
            "market_country": "CN",
            "exchange": exchange,
            "currency": "CNY",
            "asset_type": "equity",
        }

    # [HK-001] hk_market_boundary: HKEX codes (e.g. 0700.HK, 9988.HK) must be
    # recognised before the US-letter regex to avoid misclassification.
    if is_hk_symbol(normalized):
        return {
            "symbol": normalized,
            "security_name": normalized,
            "market_country": "HK",
            "exchange": "HKEX",
            "currency": "HKD",
            "asset_type": "equity",
        }

    if re.fullmatch(
        r"(?:[A-Z][A-Z0-9]{0,10}|[A-Z][A-Z0-9]{0,5}-[A-Z0-9]|"
        r"[A-Z][A-Z0-9]{0,5}\.[A-Z0-9]{1,3})",
        normalized,
    ):
        exchange = normalized.split(".", 1)[1] if "." in normalized else "US"
        return {
            "symbol": normalized,
            "security_name": normalized,
            "market_country": "US",
            "exchange": exchange,
            "currency": "USD",
            "asset_type": "equity",
        }

    return {
        "symbol": normalized,
        "security_name": normalized,
        "market_country": "UNKNOWN",
        "exchange": "UNKNOWN",
        "currency": "UNKNOWN",
        "asset_type": "unknown",
    }


def build_market_context(symbol: str, trade_date: str, now: datetime | None = None) -> dict[str, Any]:
    instrument_context = infer_instrument_context(symbol)
    market_country = instrument_context["market_country"]

    if market_country == "CN":
        context = _build_cn_market_context(trade_date, now)
    elif market_country == "HK":  # [HK-001] hk_market_boundary
        context = _build_hk_market_context(trade_date, now)
    elif market_country == "US":
        context = _build_us_market_context(trade_date, now)
    else:
        context = {
            "trade_date": trade_date,
            "timezone": "UTC",
            "market_session": "unknown",
            "market_is_open": False,
            "analysis_mode": "historical",
            "data_as_of": trade_date,
            "session_note": "无法识别市场归属，未推断交易时段。",
        }

    context["market_country"] = market_country
    context["exchange"] = instrument_context["exchange"]
    return context


def normalize_user_context(raw: Mapping[str, Any] | None = None) -> dict[str, Any]:
    context: dict[str, Any] = {}
    if not raw:
        return context

    numeric_keys = {
        "cash_available",
        "current_position",
        "current_position_pct",
        "average_cost",
        "max_loss_pct",
    }

    for key in USER_CONTEXT_KEYS:
        value = raw.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip()
            if not value:
                continue
        if key in numeric_keys:
            coerced = _coerce_numeric_user_value(value)
            if coerced is None:
                continue
            context[key] = coerced
            continue
        if key == "constraints":
            if isinstance(value, str):
                value = re.split(r"[;,，；\n]+", value)
            constraints = [str(item).strip() for item in value or [] if str(item).strip()]
            if constraints:
                context[key] = constraints
            continue
        context[key] = value

    return context


def _coerce_numeric_user_value(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    multiplier = 1.0
    if "%" not in text:
        if "亿" in text:
            multiplier = 100000000.0
        elif "万" in text:
            multiplier = 10000.0
    normalized = (
        text.replace(",", "")
        .replace("，", "")
        .replace("元", "")
        .replace("股", "")
        .replace("万", "")
        .replace("亿", "")
        .replace("％", "%")
    )
    match = re.search(r"-?\d+(?:\.\d+)?", normalized)
    if not match:
        return None
    try:
        return float(match.group(0)) * multiplier
    except ValueError:
        return None


def summarize_instrument_context(context: Mapping[str, Any] | None) -> str:
    ctx = context or {}
    return "\n".join(
        [
            f"标的代码：{ctx.get('symbol', '—')}",
            f"市场归属：{ctx.get('market_country', '—')}",
            f"交易所：{ctx.get('exchange', '—')}",
            f"币种：{ctx.get('currency', '—')}",
            f"资产类型：{ctx.get('asset_type', '—')}",
        ]
    )


def summarize_market_context(context: Mapping[str, Any] | None) -> str:
    ctx = context or {}
    return "\n".join(
        [
            f"交易日期：{ctx.get('trade_date', '—')}",
            f"时区：{ctx.get('timezone', '—')}",
            f"市场状态：{ctx.get('market_session', '—')}",
            f"当前是否开市：{'是' if ctx.get('market_is_open') else '否'}",
            f"分析模式：{ctx.get('analysis_mode', '—')}",
            f"数据截至：{ctx.get('data_as_of', '—')}",
            f"说明：{ctx.get('session_note', '—')}",
        ]
    )


def summarize_user_context(context: Mapping[str, Any] | None) -> str:
    ctx = context or {}
    if not ctx:
        return "未提供用户持仓或风险约束。"

    lines = [
        f"目标动作：{ctx.get('objective', '未说明')}",
        f"风险偏好：{ctx.get('risk_profile', '未说明')}",
        f"持有周期：{ctx.get('investment_horizon', '未说明')}",
        f"可用资金：{ctx.get('cash_available', '未说明')}",
        f"当前持仓：{ctx.get('current_position', '未说明')}",
        f"当前仓位占比：{ctx.get('current_position_pct', '未说明')}",
        f"持仓成本：{ctx.get('average_cost', '未说明')}",
        f"最大容忍亏损：{ctx.get('max_loss_pct', '未说明')}",
    ]
    constraints = ctx.get("constraints") or []
    if constraints:
        lines.append(f"硬约束：{'; '.join(str(item) for item in constraints)}")
    if ctx.get("user_notes"):
        lines.append(f"用户补充：{ctx['user_notes']}")
    return "\n".join(lines)


def extract_price_snapshot(
    raw_evidence: Mapping[str, Any] | None,
    symbol: str = "",
    trade_date: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Extract completed daily-bar and realtime prices without mixing them."""
    raw = raw_evidence or {}
    result: dict[str, Any] = {
        "completed_bar": None,
        "realtime_quote": None,
    }

    stock_entry = raw.get("stock_data")
    stock_raw = stock_entry.get("raw") if isinstance(stock_entry, Mapping) else stock_entry
    if isinstance(stock_raw, str):
        csv_lines = [
            line for line in stock_raw.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        if len(csv_lines) >= 2:
            try:
                rows = list(csv.DictReader(io.StringIO("\n".join(csv_lines))))
            except (csv.Error, TypeError):
                rows = []
            if rows:
                current_day_status = (
                    str(stock_entry.get("current_day_status") or "")
                    if isinstance(stock_entry, Mapping)
                    else ""
                ).upper()
                for row_index in range(len(rows) - 1, -1, -1):
                    lowered = {
                        str(key).lower(): value for key, value in rows[row_index].items()
                    }
                    bar_date = str(
                        lowered.get("date") or lowered.get("datetime") or ""
                    )[:10]
                    try:
                        datetime.strptime(bar_date, "%Y-%m-%d")
                    except (TypeError, ValueError):
                        continue
                    if trade_date and bar_date > trade_date:
                        continue
                    before_daily_finality = not is_daily_bar_final(
                        symbol, bar_date, now
                    )
                    explicitly_incomplete = (
                        row_index == len(rows) - 1
                        and any(
                            marker in current_day_status
                            for marker in (
                                "INTRADAY", "PARTIAL", "FORMING", "EXCLUDED"
                            )
                        )
                        # A provider-declared partial row never becomes a
                        # completed close merely because this long-running
                        # analysis crosses the market-close wall clock. When
                        # the raw payload says the row was already excluded,
                        # the remaining latest row is the prior completed bar.
                        and "action=excluded_from_daily_ohlcv" not in stock_raw
                    )
                    if before_daily_finality or explicitly_incomplete:
                        continue
                    close = _coerce_valid_price(lowered.get("close"))
                    if close is None:
                        continue
                    result["completed_bar"] = {
                        "date": bar_date,
                        "close": close,
                        "open": _coerce_price_number(lowered.get("open")),
                        "high": _coerce_price_number(lowered.get("high")),
                        "low": _coerce_price_number(lowered.get("low")),
                    }
                    break

    quote_entry = raw.get("realtime_quote")
    quote_raw = quote_entry.get("raw") if isinstance(quote_entry, Mapping) else quote_entry
    if isinstance(quote_raw, str):
        try:
            quote_raw = json.loads(quote_raw)
        except (TypeError, ValueError):
            quote_raw = None
    if isinstance(quote_raw, Mapping) and quote_raw:
        normalized_symbol = str(symbol or "").strip().upper()
        quote = quote_raw.get(normalized_symbol)
        if not isinstance(quote, Mapping):
            quote = next(
                (
                    value
                    for key, value in quote_raw.items()
                    if _quote_key_matches_symbol(key, normalized_symbol)
                    and isinstance(value, Mapping)
                ),
                None,
            )
        if isinstance(quote, Mapping):
            price = _coerce_valid_price(quote.get("price"))
            if price is not None:
                quote_time = str(quote.get("quote_time") or "")
                fetched_at = (
                    str(quote_entry.get("fetched_at") or "")
                    if isinstance(quote_entry, Mapping)
                    else ""
                )
                market_context = build_market_context(
                    symbol,
                    str(trade_date or quote_time[:10]),
                    now=now,
                )
                timezone_name = str(
                    market_context.get("timezone") or "Asia/Shanghai"
                )
                try:
                    market_tz = ZoneInfo(timezone_name)
                except Exception:
                    market_tz = CN_TZ
                quote_timestamp = _parse_market_timestamp(quote_time, market_tz)
                fetched_timestamp = _parse_market_timestamp(fetched_at, market_tz)
                consumed_timestamp = now or datetime.now(market_tz)
                if consumed_timestamp.tzinfo is None:
                    consumed_timestamp = consumed_timestamp.replace(tzinfo=market_tz)
                else:
                    consumed_timestamp = consumed_timestamp.astimezone(market_tz)
                quote_age_seconds = None
                consumption_age_seconds = None
                if quote_timestamp and fetched_timestamp:
                    quote_age_seconds = (
                        fetched_timestamp - quote_timestamp
                    ).total_seconds()
                if quote_timestamp:
                    consumption_age_seconds = (
                        consumed_timestamp - quote_timestamp
                    ).total_seconds()
                pause_snapshot_valid = _quote_valid_during_session_pause(
                    symbol,
                    str(trade_date or quote_time[:10]),
                    quote_timestamp,
                    consumed_timestamp,
                )
                close_snapshot_valid = _quote_valid_after_session_close(
                    symbol,
                    str(trade_date or quote_time[:10]),
                    quote_timestamp,
                    fetched_timestamp,
                    consumed_timestamp,
                )
                timestamp_fresh = bool(
                    quote_age_seconds is not None
                    and (
                        -120
                        <= quote_age_seconds
                        <= _MAX_REALTIME_QUOTE_AGE.total_seconds()
                        or (
                            pause_snapshot_valid
                            and fetched_timestamp is not None
                            and quote_age_seconds >= -120
                            and fetched_timestamp
                            <= consumed_timestamp + timedelta(minutes=2)
                        )
                        or close_snapshot_valid
                    )
                    and consumption_age_seconds is not None
                    and (
                        -120
                        <= consumption_age_seconds
                        <= _MAX_REALTIME_QUOTE_AGE.total_seconds()
                        or pause_snapshot_valid
                        or close_snapshot_valid
                    )
                )
                same_trade_date = bool(
                    quote_time
                    and (not trade_date or quote_time[:10] == str(trade_date)[:10])
                    and quote_time[:10] == consumed_timestamp.date().isoformat()
                    and timestamp_fresh
                )
                result["realtime_quote"] = {
                    "price": price,
                    "quote_time": quote_time or None,
                    "fetched_at": fetched_at or None,
                    "quote_age_seconds": quote_age_seconds,
                    "consumption_age_seconds": consumption_age_seconds,
                    "same_trade_date": same_trade_date,
                    "is_pause_snapshot": pause_snapshot_valid,
                    "is_closing_snapshot": close_snapshot_valid,
                    "date_inferred_from_collection": False,
                    "open": _coerce_price_number(quote.get("open")),
                    "high": _coerce_price_number(quote.get("high")),
                    "low": _coerce_price_number(quote.get("low")),
                    "previous_close": _coerce_price_number(quote.get("previous_close")),
                    "change_pct": _coerce_price_number(quote.get("change_pct")),
                    "turnover_rate": _coerce_price_number(quote.get("turnover_rate")),
                    "volume_ratio": _coerce_price_number(quote.get("volume_ratio")),
                    "source": quote.get("source"),
                }
    return result


def _coerce_price_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _parse_market_timestamp(
    value: Any,
    market_tz: ZoneInfo = CN_TZ,
) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=market_tz)
    return parsed.astimezone(market_tz)


def is_daily_bar_final(
    symbol: str,
    bar_date: str,
    now: datetime | None = None,
) -> bool:
    market_context = build_market_context(symbol, bar_date, now=now)
    if market_context.get("analysis_mode") == "historical":
        return True

    market_country = market_context.get("market_country")
    timezone_name = str(market_context.get("timezone") or "Asia/Shanghai")
    try:
        market_tz = ZoneInfo(timezone_name)
    except Exception:
        market_tz = CN_TZ
    now_local = now or datetime.now(market_tz)
    if now_local.tzinfo is None:
        now_local = now_local.replace(tzinfo=market_tz)
    else:
        now_local = now_local.astimezone(market_tz)
    local_date = now_local.date().isoformat()
    if bar_date < local_date:
        return True
    if bar_date > local_date:
        return False
    if market_context.get("market_session") not in {"post_close", "closed"}:
        return False

    finality_cutoffs = {
        "CN": time(15, 5),
        "HK": time(16, 5),
        "US": time(16, 5),
    }
    cutoff = finality_cutoffs.get(str(market_country))
    return cutoff is None or now_local.time() >= cutoff


def _quote_valid_during_session_pause(
    symbol: str,
    trade_date: str,
    quote_timestamp: datetime | None,
    consumed_timestamp: datetime,
) -> bool:
    if quote_timestamp is None:
        return False
    market_context = build_market_context(
        symbol,
        trade_date,
        now=consumed_timestamp,
    )
    if market_context.get("market_session") != "lunch_break":
        return False

    market_country = str(market_context.get("market_country") or "")
    timezone_name = str(market_context.get("timezone") or "Asia/Shanghai")
    try:
        market_tz = ZoneInfo(timezone_name)
    except Exception:
        market_tz = CN_TZ
    quote_local = quote_timestamp.astimezone(market_tz)
    consumed_local = consumed_timestamp.astimezone(market_tz)
    if quote_local.date() != consumed_local.date():
        return False

    pause_starts = {
        "CN": time(11, 30),
        "HK": time(12, 0),
    }
    pause_start = pause_starts.get(market_country)
    if pause_start is None:
        return False
    pause_start_dt = datetime.combine(
        consumed_local.date(),
        pause_start,
        tzinfo=market_tz,
    )
    return (
        pause_start_dt - timedelta(minutes=1)
        <= quote_local
        <= pause_start_dt + timedelta(minutes=1)
    )


def _quote_valid_after_session_close(
    symbol: str,
    trade_date: str,
    quote_timestamp: datetime | None,
    fetched_timestamp: datetime | None,
    consumed_timestamp: datetime,
) -> bool:
    if quote_timestamp is None or fetched_timestamp is None:
        return False
    market_context = build_market_context(
        symbol,
        trade_date,
        now=consumed_timestamp,
    )
    if market_context.get("market_session") != "post_close":
        return False

    market_country = str(market_context.get("market_country") or "")
    timezone_name = str(market_context.get("timezone") or "Asia/Shanghai")
    try:
        market_tz = ZoneInfo(timezone_name)
    except Exception:
        market_tz = CN_TZ
    quote_local = quote_timestamp.astimezone(market_tz)
    fetched_local = fetched_timestamp.astimezone(market_tz)
    consumed_local = consumed_timestamp.astimezone(market_tz)
    expected_date = str(trade_date or "")[:10]
    if not expected_date or any(
        timestamp.date().isoformat() != expected_date
        for timestamp in (quote_local, fetched_local, consumed_local)
    ):
        return False

    close_times = {
        "CN": time(15, 0),
        "HK": time(16, 0),
        "US": time(16, 0),
    }
    close_time = close_times.get(market_country)
    if close_time is None:
        return False
    close_dt = datetime.combine(
        consumed_local.date(),
        close_time,
        tzinfo=market_tz,
    )
    return bool(
        close_dt - timedelta(minutes=2)
        <= quote_local
        <= close_dt + timedelta(minutes=5)
        and quote_local - timedelta(minutes=2)
        <= fetched_local
        <= consumed_local + timedelta(minutes=2)
    )


def _coerce_valid_price(value: Any) -> float | None:
    parsed = _coerce_price_number(value)
    return parsed if parsed is not None and parsed > 0 else None


def summarize_price_snapshot(
    raw_evidence: Mapping[str, Any] | None,
    symbol: str = "",
    trade_date: str = "",
) -> str:
    snapshot = extract_price_snapshot(raw_evidence, symbol, trade_date)
    completed = snapshot.get("completed_bar")
    realtime = snapshot.get("realtime_quote")
    if not completed and not realtime:
        return "未提供可校验的已完成日线或实时行情。"

    lines: list[str] = []
    if completed:
        lines.append(
            f"最近已完成日线：{completed.get('date') or '日期未知'}，"
            f"收盘价 {completed['close']:.2f}。技术指标只能基于该日及更早的完整日线。"
        )
    if realtime:
        extras: list[str] = []
        if realtime.get("change_pct") is not None:
            extras.append(f"涨跌幅 {realtime['change_pct']:.2f}%")
        if realtime.get("turnover_rate") is not None:
            extras.append(f"换手率 {realtime['turnover_rate']:.2f}%")
        if realtime.get("volume_ratio") is not None:
            extras.append(f"量比 {realtime['volume_ratio']:.2f}")
        suffix = f"；{'，'.join(extras)}" if extras else ""
        quote_label = realtime.get("quote_time")
        if not quote_label and realtime.get("date_inferred_from_collection"):
            quote_label = f"{trade_date}（数据源未返回时分秒）"
        lines.append(
            f"实时行情快照：{quote_label or '时间未知'}，"
            f"最新价 {realtime['price']:.2f}{suffix}。"
        )
        if realtime.get("same_trade_date") and realtime.get("is_closing_snapshot"):
            lines.append(
                "该快照是当日收盘附近采集的收盘执行参考；日线技术指标仍只能使用"
                "数据源确认已完成的日线。"
            )
        elif realtime.get("same_trade_date"):
            lines.append(
                "实时行情快照是盘中状态，不是当日收盘价；不得把上一完整日线的收盘价"
                "标成今日收盘，也不得用盘中快照重算日线技术指标。"
            )
        else:
            lines.append(
                "该行情快照缺少与分析日一致的时间戳，只能作为过期参考，"
                "不得作为当前执行价格。"
            )
    return "\n".join(lines)


def build_agent_context_view(state: Mapping[str, Any], role: str) -> dict[str, str]:
    instrument_context = state.get("instrument_context", {})
    market_context = state.get("market_context", {})
    user_context = state.get("user_context", {})
    metadata = state.get("metadata") or {}
    raw_evidence = (
        metadata.get("raw_evidence")
        if isinstance(metadata, Mapping)
        else None
    ) or state.get("raw_evidence") or {}
    symbol = str(
        state.get("company_of_interest")
        or instrument_context.get("symbol")
        or ""
    )
    trade_date = str(state.get("trade_date") or "")

    user_summary = summarize_user_context(user_context)
    return {
        "instrument_context_summary": summarize_instrument_context(instrument_context),
        "market_context_summary": summarize_market_context(market_context),
        "user_context_summary": user_summary,
        "price_snapshot_summary": summarize_price_snapshot(
            raw_evidence,
            symbol,
            trade_date,
        ),
    }


def build_prompt_context_block(state: Mapping[str, Any], role: str = "agent") -> str:
    """Build a compact context block shared by analyst, research, trader, and risk prompts."""
    context_view = build_agent_context_view(state, role)
    return "\n".join(
        [
            "【标的上下文】",
            context_view["instrument_context_summary"],
            "",
            "【市场上下文】",
            context_view["market_context_summary"],
            "",
            "【价格口径】",
            context_view["price_snapshot_summary"],
            "",
            "【用户上下文】",
            context_view["user_context_summary"],
            "",
            "【执行要求】",
            "所有结论必须尊重用户上下文；若用户有硬约束，入场、加仓、止损、减仓和观望条件都不得与硬约束冲突。",
        ]
    )


def is_hk_symbol(symbol: str) -> bool:
    """[HK-001] hk_market_boundary: detect HKEX tickers such as 0700.HK / 9988.HK."""
    normalized = (symbol or "").strip().upper()
    return bool(re.fullmatch(r"\d{1,5}\.HK", normalized))


def _build_hk_market_context(trade_date: str, now: datetime | None = None) -> dict[str, Any]:
    # [HK-001] hk_market_boundary: lightweight session inference for HKEX.
    now_dt = (now or datetime.now(HK_TZ)).astimezone(HK_TZ)
    today = now_dt.date().strftime("%Y-%m-%d")
    is_trade_day = _is_hk_trading_day(trade_date)

    if trade_date == today:
        market_session = _hk_market_phase(now_dt) if is_trade_day else "closed"
    elif trade_date < today and is_trade_day:
        market_session = "post_close"
    elif trade_date > today and is_trade_day:
        market_session = "pre_open"
    else:
        market_session = "closed"

    analysis_mode = _determine_hk_analysis_mode(trade_date, today, market_session)
    return {
        "trade_date": trade_date,
        "timezone": "Asia/Hong_Kong",
        "market_session": market_session,
        "market_is_open": trade_date == today and market_session == "in_session",
        "analysis_mode": analysis_mode,
        "data_as_of": trade_date if trade_date <= today else today,
        "session_note": _hk_session_note(trade_date, today, market_session, is_trade_day),
    }


def _build_cn_market_context(trade_date: str, now: datetime | None = None) -> dict[str, Any]:
    now_dt = (now or datetime.now(CN_TZ)).astimezone(CN_TZ)
    today = now_dt.date().strftime("%Y-%m-%d")
    is_trade_day = is_cn_trading_day(trade_date)

    if trade_date == today:
        market_session = cn_market_phase(now_dt) if is_trade_day else "closed"
    elif trade_date < today and is_trade_day:
        market_session = "post_close"
    elif trade_date > today and is_trade_day:
        market_session = "pre_open"
    else:
        market_session = "closed"

    analysis_mode = _determine_cn_analysis_mode(trade_date, today, market_session)
    return {
        "trade_date": trade_date,
        "timezone": "Asia/Shanghai",
        "market_session": market_session,
        "market_is_open": trade_date == today and market_session == "in_session",
        "analysis_mode": analysis_mode,
        "data_as_of": _cn_data_as_of(trade_date, today, market_session),
        "session_note": _cn_session_note(trade_date, today, market_session, is_trade_day),
    }


def _build_us_market_context(trade_date: str, now: datetime | None = None) -> dict[str, Any]:
    now_dt = (now or datetime.now(US_TZ)).astimezone(US_TZ)
    today = now_dt.date().strftime("%Y-%m-%d")
    is_trade_day = _is_us_trading_day(trade_date)

    if trade_date == today:
        market_session = _us_market_phase(now_dt) if is_trade_day else "closed"
    elif trade_date < today and is_trade_day:
        market_session = "post_close"
    elif trade_date > today and is_trade_day:
        market_session = "pre_open"
    else:
        market_session = "closed"

    analysis_mode = _determine_us_analysis_mode(trade_date, today, market_session)
    return {
        "trade_date": trade_date,
        "timezone": "America/New_York",
        "market_session": market_session,
        "market_is_open": trade_date == today and market_session == "in_session",
        "analysis_mode": analysis_mode,
        "data_as_of": trade_date if trade_date <= today else today,
        "session_note": _us_session_note(trade_date, today, market_session, is_trade_day),
    }


def _infer_cn_exchange(symbol: str) -> str:
    parts = symbol.split(".", 1)
    if len(parts) == 2:
        suffix = parts[1]
        if suffix == "SS":
            return "SH"
        return suffix

    code = parts[0]
    if code.startswith(("4", "8", "920")):
        return "BJ"
    if code.startswith(("5", "6", "9")):
        return "SH"
    return "SZ"


def _determine_cn_analysis_mode(trade_date: str, today: str, market_session: str) -> str:
    if trade_date == today:
        if market_session == "pre_open":
            return "pre_market"
        if market_session in {"in_session", "lunch_break"}:
            return "intraday"
        if market_session == "post_close":
            return "post_market"
        return "closed"

    if trade_date == previous_cn_trading_day(today):
        return "t_plus_1"
    if trade_date > today:
        return "forward_look"
    return "historical"


def _determine_us_analysis_mode(trade_date: str, today: str, market_session: str) -> str:
    if trade_date == today:
        if market_session == "pre_open":
            return "pre_market"
        if market_session in {"in_session", "lunch_break"}:
            return "intraday"
        if market_session == "post_close":
            return "post_market"
        return "closed"

    if trade_date == _previous_us_trading_day(today):
        return "t_plus_1"
    if trade_date > today:
        return "forward_look"
    return "historical"


def _cn_data_as_of(trade_date: str, today: str, market_session: str) -> str:
    if trade_date > today:
        return today
    if trade_date == today and market_session in {"pre_open", "in_session", "lunch_break"}:
        return today
    return trade_date


def _cn_session_note(trade_date: str, today: str, market_session: str, is_trade_day: bool) -> str:
    if not is_trade_day:
        return "请求日期为 A 股非交易日。"
    if trade_date > today:
        return "请求日期晚于当前日期，按最新可用市场状态推断。"
    if trade_date < today:
        return "请求日期为历史 A 股交易日，市场已收盘。"
    if market_session == "pre_open":
        return "A 股盘前时段。"
    if market_session == "lunch_break":
        return "A 股午间休市，盘中数据可能仍在变化。"
    if market_session == "in_session":
        return "A 股当前处于交易时段。"
    return "A 股已收盘，部分数据源可能仍在更新。"


def _is_us_trading_day(date_str: str) -> bool:
    return datetime.strptime(date_str, "%Y-%m-%d").weekday() < 5


def _previous_us_trading_day(date_str: str) -> str:
    current = datetime.strptime(date_str, "%Y-%m-%d")
    while True:
        current -= timedelta(days=1)
        if current.weekday() < 5:
            return current.strftime("%Y-%m-%d")


def _us_market_phase(now_dt: datetime) -> str:
    local = now_dt.astimezone(US_TZ)
    current_time = local.time()
    if current_time < time(9, 30):
        return "pre_open"
    if time(9, 30) <= current_time < time(16, 0):
        return "in_session"
    return "post_close"


def _us_session_note(trade_date: str, today: str, market_session: str, is_trade_day: bool) -> str:
    if not is_trade_day:
        return "请求日期为美股非交易日。"
    if trade_date > today:
        return "请求日期晚于当前日期，按最新可用市场状态推断。"
    if trade_date < today:
        return "请求日期为历史美股交易日，市场已收盘。"
    if market_session == "pre_open":
        return "美股当前处于盘前时段。"
    if market_session == "in_session":
        return "美股当前处于交易时段。"
    return "美股已收盘。"


# [HK-001] hk_market_boundary: HKEX session helpers.
# 港股暂不支持完整 TA，这里只做轻量交易时段推断，不接 A 股资金/LHB/融资融券门禁。
def _is_hk_trading_day(date_str: str) -> bool:
    return datetime.strptime(date_str, "%Y-%m-%d").weekday() < 5


def _determine_hk_analysis_mode(trade_date: str, today: str, market_session: str) -> str:
    if trade_date == today:
        if market_session == "pre_open":
            return "pre_market"
        if market_session in {"in_session", "lunch_break"}:
            return "intraday"
        if market_session == "post_close":
            return "post_market"
        return "closed"
    if trade_date > today:
        return "forward_look"
    return "historical"


def _hk_market_phase(now_dt: datetime) -> str:
    local = now_dt.astimezone(HK_TZ)
    current_time = local.time()
    # HKEX: 09:30-12:00, 13:00-16:00 (lunch break 12:00-13:00)
    if current_time < time(9, 30):
        return "pre_open"
    if time(9, 30) <= current_time < time(12, 0):
        return "in_session"
    if time(12, 0) <= current_time < time(13, 0):
        return "lunch_break"
    if time(13, 0) <= current_time < time(16, 0):
        return "in_session"
    return "post_close"


def _hk_session_note(trade_date: str, today: str, market_session: str, is_trade_day: bool) -> str:
    if not is_trade_day:
        return "请求日期为港股非交易日（周末/节假日简化判断）。"
    if trade_date > today:
        return "请求日期晚于当前日期，按最新可用市场状态推断。"
    if trade_date < today:
        return "请求日期为历史港股交易日，市场已收盘。"
    if market_session == "pre_open":
        return "港股当前处于盘前时段。"
    if market_session == "lunch_break":
        return "港股午间休市。"
    if market_session == "in_session":
        return "港股当前处于交易时段。"
    return "港股已收盘。"
