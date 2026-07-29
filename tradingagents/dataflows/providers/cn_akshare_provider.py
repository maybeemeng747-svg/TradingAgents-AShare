import os
import re
import time
import threading
import contextvars
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
from stockstats import wrap

from .base import BaseMarketDataProvider
from ..trade_calendar import cn_market_phase, cn_no_data_reason, cn_today_str, is_cn_trading_day


# ── akshare 并发控制 ──
# 总并发上限 5（防反爬 + akshare 全局状态安全）
# 定时任务最多占 3 个槽位，保证前端至少有 2 个槽位可用
#
# 关键设计：僵尸线程回收
# _run_job 超时后不会 cancel 内部线程（避免 cancel 卡在 to_thread），
# 导致僵尸线程可能永远持有 semaphore permit。_AkshareLock 通过追踪每个
# permit 的持有时间，在超过 STALE_TIMEOUT 后自动回收，防止锁被耗尽。

_is_scheduled_task: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "is_scheduled_task", default=False,
)


def set_scheduled_task_context(value: bool = True) -> contextvars.Token:
    """标记当前上下文为定时任务（会通过 asyncio.to_thread 自动传播到工作线程）"""
    return _is_scheduled_task.set(value)


def reset_scheduled_task_context(token: contextvars.Token) -> None:
    """Restore the scheduled-task marker to its previous ContextVar value."""
    _is_scheduled_task.reset(token)


import logging as _logging

_lock_logger = _logging.getLogger(__name__)


def _format_value_range(lower: Any, upper: Any) -> str:
    """Render an optional numeric range without leaking pandas NaN values."""
    lower_valid = pd.notna(lower)
    upper_valid = pd.notna(upper)
    if lower_valid and upper_valid:
        if lower == upper:
            return str(lower)
        return f"{lower} - {upper}"
    if lower_valid:
        return str(lower)
    if upper_valid:
        return str(upper)
    return ""


class _AkshareLock:
    """akshare 并发锁：前端优先 + 僵尸线程自动回收。

    - 总并发上限 ``total``（防反爬）
    - 定时任务额外受 ``scheduled_max`` 限制，为前端保留带宽
    - 持锁超过 ``stale_timeout`` 秒的线程视为僵尸，permit 被自动回收
    - 僵尸线程最终退出 ``with`` 块时不会 double-release（已被回收）
    """

    ACQUIRE_TIMEOUT = 60   # 等待 slot 的最大秒数
    STALE_TIMEOUT = 120    # 单次 akshare 调用不应超过 2 分钟，超过视为僵尸

    def __init__(self, total: int = 5, scheduled_max: int = 3):
        self._total = threading.Semaphore(total)
        self._scheduled = threading.Semaphore(scheduled_max)
        self._holders: dict[int, tuple[float, bool]] = {}   # tid -> (mono_time, is_scheduled)
        self._mu = threading.Lock()

    # ── 僵尸回收 ──

    def _reclaim_stale(self) -> int:
        """回收超时持有者的 permit，返回回收数量。"""
        now = time.monotonic()
        reclaimed = 0
        with self._mu:
            stale = [
                (tid, is_sched)
                for tid, (t, is_sched) in self._holders.items()
                if now - t > self.STALE_TIMEOUT
            ]
            for tid, is_sched in stale:
                del self._holders[tid]
                self._total.release()
                if is_sched:
                    self._scheduled.release()
                reclaimed += 1
        if reclaimed:
            _lock_logger.warning("[AkshareLock] reclaimed %d stale permits from zombie threads", reclaimed)
        return reclaimed

    # ── context manager ──

    def _acquire_or_reclaim(self, sem: threading.Semaphore, label: str) -> None:
        """尝试获取 semaphore，超时后回收僵尸再重试一次。"""
        if sem.acquire(timeout=self.ACQUIRE_TIMEOUT):
            return
        self._reclaim_stale()
        if sem.acquire(timeout=10):
            return
        raise TimeoutError(f"akshare {label} slot acquire timeout after reclaim")

    def __enter__(self):
        is_scheduled = _is_scheduled_task.get(False)
        try:
            if is_scheduled:
                self._acquire_or_reclaim(self._scheduled, "scheduled")
                try:
                    self._acquire_or_reclaim(self._total, "total")
                except BaseException:
                    self._scheduled.release()
                    raise
            else:
                self._acquire_or_reclaim(self._total, "total")
        except TimeoutError:
            _lock_logger.error("[AkshareLock] acquire timeout (is_scheduled=%s)", is_scheduled)
            raise
        with self._mu:
            self._holders[threading.get_ident()] = (time.monotonic(), is_scheduled)
        return self

    def __exit__(self, *exc_info):
        tid = threading.get_ident()
        with self._mu:
            info = self._holders.pop(tid, None)
        if info is not None:
            _, is_scheduled = info
            self._total.release()
            if is_scheduled:
                self._scheduled.release()
        # info is None → permit 已被 _reclaim_stale 回收，不 double-release


AKSHARE_CALL_LOCK = _AkshareLock(total=5, scheduled_max=3)


class CnAkshareProvider(BaseMarketDataProvider):
    """A-share provider backed by AkShare."""

    INDICATOR_DESCRIPTIONS = {
        "close_50_sma": (
            "50 日均线（SMA）：中期趋势指标。"
            "用途：识别趋势方向，并作为动态支撑/阻力参考。"
        ),
        "close_200_sma": (
            "200 日均线（SMA）：长期趋势基准。"
            "用途：确认大级别趋势，并辅助识别金叉/死叉结构。"
        ),
        "close_10_ema": (
            "10 日指数均线（EMA）：短期响应更快。"
            "用途：捕捉短线动量变化与潜在入场时机。"
        ),
        "macd": "MACD：趋势与动量综合指标。",
        "macds": "MACD 信号线（Signal）。",
        "macdh": "MACD 柱状图（Histogram）。",
        "rsi": "RSI：衡量超买/超卖的动量指标。",
        "boll": "布林中轨（20 日均线）。",
        "boll_ub": "布林上轨。",
        "boll_lb": "布林下轨。",
        "atr": "ATR：真实波动幅度均值，用于波动与风控。",
        "vwma": "VWMA：成交量加权均线。",
        "mfi": "MFI：资金流量指标。",
    }

    @property
    def name(self) -> str:
        return "cn_akshare"

    def _ak(self):
        try:
            import akshare as ak  # type: ignore
        except ImportError as exc:
            raise NotImplementedError(
                "cn_akshare requires 'akshare'. Install it with: pip install akshare"
            ) from exc
        return ak

    def _locked(self, func, *args, **kwargs):
        with AKSHARE_CALL_LOCK:
            return func(*args, **kwargs)

    def _normalize_symbol(self, symbol: str) -> str:
        s = symbol.strip().lower()
        m = re.search(r"(\d{6})", s)
        if not m:
            raise NotImplementedError(
                f"cn_akshare only supports A-share 6-digit symbols, got: {symbol}"
            )
        return m.group(1)

    def _sina_symbol(self, symbol: str) -> str:
        code = self._normalize_symbol(symbol)
        if code.startswith(("5", "6", "9")):
            return f"sh{code}"
        return f"sz{code}"

    def _xq_symbol(self, symbol: str) -> str:
        code = self._normalize_symbol(symbol)
        if code.startswith(("5", "6", "9")):
            return f"SH{code}"
        return f"SZ{code}"

    def _is_likely_etf_symbol(self, symbol: str) -> bool:
        code = self._normalize_symbol(symbol)
        # 常见 A 股 ETF 代码段：5xxxxx(沪市) / 15xxxx,16xxxx,18xxxx(深市)
        return code.startswith(("5", "15", "16", "18"))

    def _normalize_hist_df(self, raw_df: pd.DataFrame) -> pd.DataFrame:
        if raw_df is None or raw_df.empty:
            return pd.DataFrame()

        col_map = {
            "日期": "Date",
            "date": "Date",
            "Date": "Date",
            "开盘": "Open",
            "open": "Open",
            "Open": "Open",
            "最高": "High",
            "high": "High",
            "High": "High",
            "最低": "Low",
            "low": "Low",
            "Low": "Low",
            "收盘": "Close",
            "close": "Close",
            "Close": "Close",
            "成交量": "Volume",
            "volume": "Volume",
            "Volume": "Volume",
            "amount": "Volume",
            "Amount": "Volume",
        }
        df = raw_df.rename(columns=col_map).copy()
        required = ["Date", "Open", "High", "Low", "Close", "Volume"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            raise ValueError(f"hist dataframe missing columns: {missing}")

        out = df[required].copy()
        out["Date"] = pd.to_datetime(out["Date"], errors="coerce")
        out = out.dropna(subset=["Date"]).sort_values("Date")

        for c in ["Open", "High", "Low", "Close", "Volume"]:
            out[c] = pd.to_numeric(out[c], errors="coerce")
        out = out.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
        out["Volume"] = out["Volume"].astype(float)

        return out

    def _format_ak_hist(self, df: pd.DataFrame, symbol: str, start: str, end: str) -> str:
        patch_info = getattr(df, "attrs", {}) or {}
        current_day_status = patch_info.get("_current_day_status")
        if df is None or df.empty:
            no_data = f"No data found for symbol '{symbol}' between {start} and {end}"
            if not current_day_status:
                return no_data
            return (
                "# [INTRADAY-DAYBAR] "
                f"current_day_status={current_day_status}, "
                "action=excluded_from_daily_ohlcv\n"
                f"{no_data}"
            )
        out = self._normalize_hist_df(df)
        out["Dividends"] = 0.0
        out["Stock Splits"] = 0.0
        out["Date"] = pd.to_datetime(out["Date"]).dt.strftime("%Y-%m-%d")

        header = f"# Stock data for {symbol} from {start} to {end}\n"
        header += f"# Total records: {len(out)}\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        header += f"# [DATA-P0-603629] adjustment=前复权(qfq)\n"  # [DATA-P0-603629] astock_source_fallback
        # [G-005] realtime_ohlcv_patch: record patch info in header
        if current_day_status:
            header += (
                "# [INTRADAY-DAYBAR] "
                f"current_day_status={current_day_status}, "
                "action=excluded_from_daily_ohlcv\n"
            )
        if patch_info.get("_realtime_patched"):
            info = patch_info.get("_realtime_patch_info", {})
            header += f"# [G-005] is_realtime_patched=True, source={info.get('source')}, quote_time={info.get('quote_time')}\n"
        elif patch_info.get("_realtime_patch_status") in ("FAILED", "STALE"):
            header += f"# [G-005] is_realtime_patched=False, status={patch_info['_realtime_patch_status']}\n"
        header += "\n"
        return header + out.to_csv(index=False)

    @staticmethod
    def _slice_hist_df(df: pd.DataFrame, start_date: str, end_date: str) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame()
        start_dt = pd.to_datetime(start_date, errors="coerce")
        end_dt = pd.to_datetime(end_date, errors="coerce")
        if pd.isna(start_dt) or pd.isna(end_dt):
            return df
        out = df.copy()
        out["Date"] = pd.to_datetime(out["Date"], errors="coerce")
        out = out.dropna(subset=["Date"])
        out = out[(out["Date"] >= start_dt) & (out["Date"] <= end_dt)]
        return out.sort_values("Date").reset_index(drop=True)

    @staticmethod
    def _shrink_table(df: pd.DataFrame, max_rows: int = 12, max_cols: int = 16) -> pd.DataFrame:
        if df is None or df.empty:
            return df
        rows = min(max_rows, len(df))
        cols = min(max_cols, len(df.columns))
        return df.head(rows).iloc[:, :cols]

    def _fetch_hist_df(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        with AKSHARE_CALL_LOCK:
            ak = self._ak()
            code = self._normalize_symbol(symbol)
            symbol_with_market = self._sina_symbol(symbol)
            start_yyyymmdd = start_date.replace("-", "")
            end_yyyymmdd = end_date.replace("-", "")

            # ETF 优先：Sina 历史接口稳定且不依赖东财
            if self._is_likely_etf_symbol(symbol):
                etf_errors = []
                try:
                    df = ak.fund_etf_hist_sina(symbol=symbol_with_market)
                    out = self._normalize_hist_df(df)
                    out = self._slice_hist_df(out, start_date, end_date)
                    if not out.empty:
                        return self._maybe_append_realtime_row(symbol, out, end_date, assume_locked=True)
                    etf_errors.append("fund_etf_hist_sina: empty after date filter")
                except Exception as exc:
                    etf_errors.append(f"fund_etf_hist_sina: {type(exc).__name__}")

                try:
                    df = ak.fund_etf_hist_em(
                        symbol=code,
                        period="daily",
                        start_date=start_yyyymmdd,
                        end_date=end_yyyymmdd,
                        adjust="qfq",
                    )
                    out = self._normalize_hist_df(df)
                    if not out.empty:
                        return self._maybe_append_realtime_row(symbol, out, end_date, assume_locked=True)
                    etf_errors.append("fund_etf_hist_em: empty dataframe")
                except Exception as exc:
                    etf_errors.append(f"fund_etf_hist_em: {type(exc).__name__}")

            # Source 1: Eastmoney (default)
            em_last_exc = None
            for i in range(2):
                try:
                    df = ak.stock_zh_a_hist(
                        symbol=code,
                        period="daily",
                        start_date=start_yyyymmdd,
                        end_date=end_yyyymmdd,
                        adjust="qfq",
                    )
                    out = self._normalize_hist_df(df)
                    return self._maybe_append_realtime_row(symbol, out, end_date, assume_locked=True)
                except Exception as exc:
                    em_last_exc = exc
                    if i < 1:
                        time.sleep(0.6 * (i + 1))

            # Source 2: Sina
            try:
                df = ak.stock_zh_a_daily(
                    symbol=symbol_with_market,
                    start_date=start_yyyymmdd,
                    end_date=end_yyyymmdd,
                    adjust="qfq",
                )
                out = self._normalize_hist_df(df)
                return self._maybe_append_realtime_row(symbol, out, end_date, assume_locked=True)
            except Exception:
                pass

            # Source 3: Tencent
            try:
                df = ak.stock_zh_a_hist_tx(
                    symbol=symbol_with_market,
                    start_date=start_yyyymmdd,
                    end_date=end_yyyymmdd,
                    adjust="qfq",
                )
                out = self._normalize_hist_df(df)
                return self._maybe_append_realtime_row(symbol, out, end_date, assume_locked=True)
            except Exception:
                pass

            raise NotImplementedError(
                f"cn_akshare is temporarily unavailable for price history (eastmoney/sina/tencent all failed): {em_last_exc}"
            ) from em_last_exc

    def _fetch_realtime_row_unlocked(self, symbol: str) -> pd.DataFrame:
        # [G-005] realtime_ohlcv_patch: keep old Xueqiu path as last-resort fallback
        ak = self._ak()
        spot = ak.stock_individual_spot_xq(
            symbol=self._xq_symbol(symbol),
            token=os.getenv("XQ_A_TOKEN"),
        )
        if spot is None or spot.empty:
            return pd.DataFrame()
        if not {"item", "value"}.issubset(set(spot.columns)):
            return pd.DataFrame()
        kv = dict(zip(spot["item"].astype(str), spot["value"]))

        date_val = pd.to_datetime(kv.get("时间"), errors="coerce")
        if pd.isna(date_val):
            date_val = pd.to_datetime(cn_today_str())
        row = {
            "Date": pd.to_datetime(date_val).normalize(),
            "Open": pd.to_numeric(kv.get("今开"), errors="coerce"),
            "High": pd.to_numeric(kv.get("最高"), errors="coerce"),
            "Low": pd.to_numeric(kv.get("最低"), errors="coerce"),
            "Close": pd.to_numeric(kv.get("现价"), errors="coerce"),
            "Volume": pd.to_numeric(kv.get("成交量"), errors="coerce"),
        }
        rt = pd.DataFrame([row]).dropna(subset=["Open", "High", "Low", "Close", "Volume"])
        return rt

    def _fetch_realtime_row(self, symbol: str) -> pd.DataFrame:
        with AKSHARE_CALL_LOCK:
            return self._fetch_realtime_row_unlocked(symbol)

    # [G-005] realtime_ohlcv_patch
    def _fetch_realtime_ohlcv_from_quotes(
        self,
        symbol: str,
        *,
        assume_locked: bool = False,
    ) -> tuple[pd.DataFrame, dict]:
        """Fetch today's OHLCV from get_realtime_quotes (Sina/Eastmoney).

        Returns (DataFrame with one row, info_dict) or (empty DataFrame, info_dict).
        info_dict contains quote_time, source, is_realtime_patched, status.
        """
        import json as _json

        info = {"quote_time": None, "source": None, "is_realtime_patched": True, "status": "FAILED"}

        code = self._normalize_symbol(symbol)
        upper_symbol = symbol.strip().upper()
        if not upper_symbol.endswith((".SH", ".SZ", ".SS")):
            if code.startswith(("5", "6", "9")):
                upper_symbol = f"{code}.SH"
            else:
                upper_symbol = f"{code}.SZ"

        try:
            raw_json = self._fetch_quotes_sina({code: upper_symbol})
            data = _json.loads(raw_json)
        except Exception:
            data = {}

        if not data or upper_symbol not in data:
            try:
                if assume_locked:
                    ak = self._ak()
                    em_df = ak.stock_zh_a_spot_em()
                else:
                    with AKSHARE_CALL_LOCK:
                        ak = self._ak()
                        em_df = ak.stock_zh_a_spot_em()
                if em_df is not None and not em_df.empty:
                    raw_json = self._build_quotes_from_em(em_df, {code: upper_symbol})
                    data = _json.loads(raw_json)
            except Exception:
                pass

        if not data or upper_symbol not in data:
            info["status"] = "FAILED"
            return pd.DataFrame(), info

        q = data[upper_symbol]
        price = q.get("price")
        if price is None or price == 0:
            info["status"] = "STALE"
            return pd.DataFrame(), info

        open_val = q.get("open") or price
        high_val = q.get("high") or price
        low_val = q.get("low") or price
        volume = q.get("volume", 0) or 0
        amount = q.get("amount", 0) or 0

        if volume > 0 and amount > 0:
            avg_price = amount / volume
            if avg_price > 500:
                volume = volume * 100

        today = pd.to_datetime(cn_today_str())
        row = {
            "Date": today.normalize(),
            "Open": float(open_val),
            "High": float(high_val),
            "Low": float(low_val),
            "Close": float(price),
            "Volume": float(volume),
        }
        info.update({
            "quote_time": q.get("quote_time"),
            "source": q.get("source", "unknown"),
            "is_realtime_patched": True,
            "status": "HAS_DATA",
        })
        rt = pd.DataFrame([row])
        return rt, info

    def _maybe_append_realtime_row(
        self,
        symbol: str,
        hist_df: pd.DataFrame,
        end_date: str,
        *,
        assume_locked: bool = False,
    ) -> pd.DataFrame:
        # [G-005] realtime_ohlcv_patch
        if hist_df is None:
            hist_df = pd.DataFrame()
        try:
            end_dt = pd.to_datetime(end_date, errors="coerce")
            if pd.isna(end_dt):
                return hist_df
            today = pd.to_datetime(cn_today_str())

            if end_dt.normalize() < today:
                return hist_df
            if not is_cn_trading_day(today.strftime("%Y-%m-%d")):
                return hist_df

            phase = cn_market_phase()
            if phase != "post_close":
                # A provider may expose today's still-forming row before the
                # close. Daily indicators must only consume completed bars;
                # realtime price/turnover remain available through the
                # independent realtime_quote evidence channel.
                completed = hist_df.copy()
                completed.attrs.update(getattr(hist_df, "attrs", {}) or {})
                if not completed.empty and "Date" in completed.columns:
                    dates = pd.to_datetime(completed["Date"], errors="coerce")
                    completed = completed.loc[dates.dt.normalize() != today].copy()
                completed.attrs["_current_day_status"] = (
                    "PARTIAL_INTRADAY"
                    if phase in ("in_session", "lunch_break")
                    else "NOT_FINAL"
                )
                completed.attrs["_realtime_patch_status"] = "SKIPPED_NOT_FINAL"
                return completed

            has_today = False
            if not hist_df.empty:
                has_today = (pd.to_datetime(hist_df["Date"]).dt.normalize() == today).any()
            if has_today:
                return hist_df

            rt, info = self._fetch_realtime_ohlcv_from_quotes(
                symbol,
                assume_locked=assume_locked,
            )

            if rt.empty:
                _lock_logger.warning(
                    "[G-005] realtime OHLCV patch failed for %s, status=%s",
                    symbol, info.get("status"),
                )
                hist_df.attrs["_realtime_patch_status"] = info.get("status", "FAILED")
                return hist_df

            hist_vol_median = 0.0
            if not hist_df.empty and len(hist_df) > 0:
                hist_vol_median = hist_df["Volume"].median()
            if hist_vol_median > 0:
                rt_vol = rt.iloc[0]["Volume"]
                ratio = rt_vol / hist_vol_median
                if ratio > 100:
                    rt.iloc[0, rt.columns.get_loc("Volume")] = rt_vol / 100
                elif ratio < 0.01:
                    rt.iloc[0, rt.columns.get_loc("Volume")] = rt_vol * 100

            merged = pd.concat([hist_df, rt], ignore_index=True)
            merged = merged.sort_values("Date").drop_duplicates(subset=["Date"], keep="last")
            merged = merged.reset_index(drop=True)

            merged.attrs["_realtime_patched"] = True
            merged.attrs["_realtime_patch_info"] = info

            _lock_logger.info(
                "[G-005] realtime OHLCV patched for %s, source=%s, quote_time=%s",
                symbol, info.get("source"), info.get("quote_time"),
            )
            return merged
        except Exception as exc:
            _lock_logger.warning("[G-005] realtime OHLCV patch exception for %s: %s", symbol, exc)
            hist_df.attrs["_realtime_patch_status"] = "FAILED"
            return hist_df

    def get_stock_data(self, symbol: str, start_date: str, end_date: str) -> str:
        df = self._fetch_hist_df(symbol, start_date, end_date)
        return self._format_ak_hist(df, symbol, start_date, end_date)

    # [G-005] realtime_ohlcv_patch
    def get_realtime_patch_info(self, symbol: str, start_date: str, end_date: str) -> dict:
        """Return realtime patch metadata without re-fetching data.

        Callers can use this to get is_realtime_patched status after get_stock_data.
        """
        df = self._fetch_hist_df(symbol, start_date, end_date)
        attrs = getattr(df, "attrs", {}) or {}
        if attrs.get("_realtime_patched"):
            info = attrs.get("_realtime_patch_info", {})
            info["is_realtime_patched"] = True
            return info
        return {
            "is_realtime_patched": False,
            "status": attrs.get("_realtime_patch_status", "NO_PATCH"),
        }

    def get_indicators(
        self, symbol: str, indicator: str, curr_date: str, look_back_days: int
    ) -> str:
        if indicator not in self.INDICATOR_DESCRIPTIONS:
            raise ValueError(
                f"Indicator {indicator} is not supported. "
                f"Please choose from: {list(self.INDICATOR_DESCRIPTIONS.keys())}"
            )

        curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
        start_dt = curr_dt - timedelta(days=max(look_back_days, 260))
        df = self._fetch_hist_df(symbol, start_dt.strftime("%Y-%m-%d"), curr_date)
        if df is None or df.empty:
            return f"No data found for {symbol} for indicator {indicator}"

        ind_df = df.rename(
            columns={
                "Date": "date",
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume",
            }
        )[["date", "open", "high", "low", "close", "volume"]].copy()
        ind_df["date"] = pd.to_datetime(ind_df["date"], errors="coerce")
        ind_df = ind_df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)

        ss = wrap(ind_df)
        indicator_series = ss[indicator]

        values_by_date = {}
        for idx, dt_val in enumerate(ind_df["date"]):
            date_str = pd.to_datetime(dt_val).strftime("%Y-%m-%d")
            val = indicator_series.iloc[idx]
            values_by_date[date_str] = "N/A" if pd.isna(val) else str(val)

        begin = curr_dt - timedelta(days=look_back_days)
        lines = []
        d = curr_dt
        while d >= begin:
            key = d.strftime("%Y-%m-%d")
            if key in values_by_date:
                value = values_by_date[key]
                if value == "N/A":
                    value = cn_no_data_reason(key)
            else:
                value = cn_no_data_reason(key)
            lines.append(f"{key}: {value}")
            d -= timedelta(days=1)

        result = (
            f"## {indicator} 指标值（{begin.strftime('%Y-%m-%d')} 至 {curr_date}）：\n\n"
            + "\n".join(lines)
            + "\n\n"
            + self.INDICATOR_DESCRIPTIONS[indicator]
        )
        return result

    def get_fundamentals(self, ticker: str, curr_date: str = None) -> str:
        with AKSHARE_CALL_LOCK:
            ak = self._ak()
            code = self._normalize_symbol(ticker)
            errors = []

            info_df = None
            try:
                info_df = ak.stock_individual_info_em(symbol=code)
            except Exception as exc:
                errors.append(f"stock_individual_info_em: {type(exc).__name__}")

            if info_df is None or info_df.empty:
                try:
                    info_df = ak.stock_individual_basic_info_xq(symbol=self._xq_symbol(ticker))
                    if not info_df.empty and set(info_df.columns) >= {"item", "value"}:
                        info_df = info_df.rename(columns={"item": "item", "value": "value"})
                except Exception as exc:
                    errors.append(f"stock_individual_basic_info_xq: {type(exc).__name__}")

            # cninfo fallback: 仅当公司画像身份字段缺失时才调用巨潮
            existing_items: set[str] = set()
            if (
                info_df is not None
                and not info_df.empty
                and {"item", "value"}.issubset(info_df.columns)
            ):
                for _, profile_row in info_df[["item", "value"]].iterrows():
                    value = str(profile_row["value"]).strip()
                    if value and value.lower() not in {"nan", "none", "null", "-", "—"}:
                        existing_items.add(str(profile_row["item"]).strip())
            required_aliases = (
                {"股票代码", "证券代码", "代码"},
                {"股票简称", "公司名称", "名称"},
                {"所属行业", "行业"},
                {"主营业务", "主营"},
            )
            needs_cninfo = not all(existing_items & aliases for aliases in required_aliases)
            cninfo_df = None
            if needs_cninfo:
                try:
                    cninfo_df = ak.stock_profile_cninfo(symbol=code)
                except Exception as exc:
                    errors.append(f"stock_profile_cninfo: {type(exc).__name__}")

            cninfo_profile_df = None
            if cninfo_df is not None and not cninfo_df.empty:
                # 巨潮返回单行宽表，列为字段名
                row = cninfo_df.iloc[0]
                cninfo_items: dict[str, str] = {}
                for col in cninfo_df.columns:
                    val = str(row[col]).strip()
                    if val and val.lower() not in {"nan", "none", "-", ""}:
                        cninfo_items[col] = val

                # 主营业务优先于经营范围
                main_biz = cninfo_items.get("主营业务") or cninfo_items.get("经营范围")
                cninfo_name = cninfo_items.get("A股简称") or cninfo_items.get("公司名称")
                cninfo_industry = cninfo_items.get("所属行业")
                cninfo_code = cninfo_items.get("A股代码")

                # Keep CNInfo as an independent profile section.  Flattening
                # providers into one table would discard a mismatched CNInfo
                # code while retaining that other company's business fields.
                rows = []
                cninfo_code_match = re.search(r"(\d{6})", cninfo_code or "")
                normalized_cninfo_code = cninfo_code_match.group(1) if cninfo_code_match else ""
                if normalized_cninfo_code and normalized_cninfo_code != code:
                    # Fail closed before raw fundamentals reach the LLM.  Keep
                    # only enough information for the identity contract to
                    # detect and audit the mismatch; never expose the other
                    # company's name, industry, or business description.
                    rows.extend(
                        [
                            {"item": "股票代码", "value": normalized_cninfo_code},
                            {
                                "item": "身份冲突",
                                "value": f"requested={code}, returned={normalized_cninfo_code}",
                            },
                        ]
                    )
                    cninfo_name = None
                    cninfo_industry = None
                    main_biz = None
                    scope = None
                else:
                    scope = cninfo_items.get("经营范围")

                if cninfo_code and not rows:
                    rows.append({"item": "股票代码", "value": cninfo_code})
                if cninfo_name:
                    rows.append({"item": "股票简称", "value": cninfo_name})
                if cninfo_industry:
                    rows.append({"item": "所属行业", "value": cninfo_industry})
                if main_biz:
                    rows.append({"item": "主营业务", "value": main_biz})
                if scope:
                    rows.append({"item": "经营范围", "value": scope})
                if rows:
                    cninfo_profile_df = pd.DataFrame(rows)

            abstract_df = None
            try:
                abstract_df = ak.stock_financial_abstract(symbol=code)
            except Exception as exc:
                errors.append(f"stock_financial_abstract: {type(exc).__name__}")

            parts = [f"## Fundamentals for {ticker}"]
            if info_df is not None and not info_df.empty:
                for c in info_df.columns:
                    info_df[c] = info_df[c].astype(str).str.slice(0, 220)
                parts.append("### Company Profile")
                parts.append(info_df.head(40).to_markdown(index=False))
            if cninfo_profile_df is not None and not cninfo_profile_df.empty:
                parts.append("### Company Profile (巨潮资讯)")
                parts.append(cninfo_profile_df.to_markdown(index=False))
            if abstract_df is not None and not abstract_df.empty:
                parts.append("### Financial Abstract (latest available columns)")
                metric_cols = [c for c in abstract_df.columns if c not in ("选项", "指标")]
                top_cols = metric_cols[:8]
                cols = [c for c in ("选项", "指标") if c in abstract_df.columns] + top_cols
                parts.append(self._shrink_table(abstract_df[cols], max_rows=20, max_cols=10).to_markdown(index=False))

            if len(parts) > 1:
                return "\n\n".join(parts)

            raise NotImplementedError(
                "cn_akshare is temporarily unavailable for fundamentals: "
                + "; ".join(errors)
            )

    @staticmethod
    def _select_cashflow_cols(df: pd.DataFrame) -> pd.DataFrame:
        """现金流量表智能列筛选：保留关键汇总列，剔除大量 NaN 的金融行业专用列。"""
        # 优先保留的关键列（按重要性排序）
        priority_cols = [
            "报告日", "经营活动产生的现金流量净额", "经营活动现金流入小计", "经营活动现金流出小计",
            "投资活动产生的现金流量净额", "投资活动现金流入小计", "投资活动现金流出小计",
            "筹资活动产生的现金流量净额", "筹资活动现金流入小计", "筹资活动现金流出小计",
            "现金及现金等价物净增加额", "期末现金及现金等价物余额",
        ]
        selected = [c for c in priority_cols if c in df.columns]
        if len(selected) >= 6:
            return df[selected]
        # 如果关键列不足，回退到剔除全 NaN 列的方式
        non_nan_cols = [c for c in df.columns if df[c].notna().any()]
        return df[non_nan_cols] if non_nan_cols else df

    def _financial_report_sina(self, ticker: str, report_name: str) -> str:
        with AKSHARE_CALL_LOCK:
            ak = self._ak()
            symbol = self._sina_symbol(ticker)
            errors = []
            try:
                df = ak.stock_financial_report_sina(stock=symbol, symbol=report_name)
                if df is None or df.empty:
                    raise ValueError("empty dataframe")
                # 现金流量表特殊处理：智能列筛选
                if "现金流量" in report_name:
                    df = self._select_cashflow_cols(df)
                    return self._shrink_table(df, max_rows=12, max_cols=20).to_markdown(index=False)
                if "利润表" in report_name:
                    df = self._select_income_statement_cols(df)
                    return self._shrink_table(df, max_rows=12, max_cols=20).to_markdown(index=False)
                # 其他报表：剔除全 NaN 列，减少噪音
                non_nan_cols = [c for c in df.columns if df[c].notna().any()]
                if non_nan_cols and len(non_nan_cols) < len(df.columns):
                    df = df[non_nan_cols]
                return self._shrink_table(df, max_rows=12, max_cols=18).to_markdown(index=False)
            except Exception as exc:
                errors.append(f"stock_financial_report_sina: {type(exc).__name__}")

            code = self._normalize_symbol(ticker)
            indicator = "按报告期"
            try:
                # 同花顺摘要表作为备用，口径不完全一致但可作为降级保障
                df = ak.stock_financial_abstract_new_ths(symbol=code, indicator=indicator)
                if df is None or df.empty:
                    raise ValueError("empty dataframe")
                return self._shrink_table(df, max_rows=12, max_cols=18).to_markdown(index=False)
            except Exception as exc:
                errors.append(f"stock_financial_abstract_new_ths: {type(exc).__name__}")

            raise NotImplementedError(
                f"cn_akshare is temporarily unavailable for {report_name}: {'; '.join(errors)}"
            )

    @staticmethod
    def _select_balance_sheet_cols(df: pd.DataFrame) -> pd.DataFrame:
        priority_cols = [
            "报告日",
            "流动资产合计", "货币资金", "应收账款", "存货", "固定资产净额", "资产总计",
            "短期借款", "应付票据", "应付账款", "合同负债", "一年内到期的非流动负债",
            "其他流动负债", "流动负债合计", "长期借款", "应付债券", "非流动负债合计",
            "负债合计",
            "实收资本(或股本)", "资本公积", "未分配利润", "归属于母公司股东权益合计",
            "负债和所有者权益(或股东权益)总计",
        ]
        selected = [c for c in priority_cols if c in df.columns]
        if len(selected) >= 6:
            return df[selected]
        non_nan_cols = [c for c in df.columns if df[c].notna().any()]
        return df[non_nan_cols] if non_nan_cols else df

    @staticmethod
    def _select_income_statement_cols(df: pd.DataFrame) -> pd.DataFrame:
        """Keep the fields required by deterministic profitability checks.

        Taking the first N columns of Sina's wide table can omit net profit,
        leaving revenue growth, profit growth and ROE permanently unavailable.
        """
        priority_cols = [
            "报告日",
            "营业总收入",
            "营业收入",
            "营业总成本",
            "营业成本",
            "营业利润",
            "利润总额",
            "所得税费用",
            "净利润",
            "归属于母公司股东的净利润",
            "归属于母公司所有者的净利润",
            "扣除非经常性损益后的净利润",
            "基本每股收益",
            "稀释每股收益",
        ]
        selected = [column for column in priority_cols if column in df.columns]
        if len(selected) >= 4:
            return df[selected]
        non_nan_cols = [column for column in df.columns if df[column].notna().any()]
        return df[non_nan_cols] if non_nan_cols else df

    def get_balance_sheet(
        self, ticker: str, freq: str = "quarterly", curr_date: str = None
    ) -> str:
        with AKSHARE_CALL_LOCK:
            ak = self._ak()
            symbol = self._sina_symbol(ticker)
            errors = []
            try:
                df = ak.stock_financial_report_sina(stock=symbol, symbol="资产负债表")
                if df is not None and not df.empty:
                    df = self._select_balance_sheet_cols(df)
                    return f"## Balance Sheet ({ticker})\n\n" + self._shrink_table(df, max_rows=6, max_cols=20).to_markdown(index=False)
                raise ValueError("empty dataframe")
            except Exception as exc:
                errors.append(f"stock_financial_report_sina: {type(exc).__name__}")

            code = self._normalize_symbol(ticker)
            try:
                df = ak.stock_financial_abstract_new_ths(symbol=code, indicator="按报告期")
                if df is not None and not df.empty:
                    return f"## Balance Sheet ({ticker})\n\n" + self._shrink_table(df, max_rows=6, max_cols=20).to_markdown(index=False)
                raise ValueError("empty dataframe")
            except Exception as exc:
                errors.append(f"stock_financial_abstract_new_ths: {type(exc).__name__}")

            raise NotImplementedError(
                f"cn_akshare is temporarily unavailable for 资产负债表: {'; '.join(errors)}"
            )

    def get_cashflow(
        self, ticker: str, freq: str = "quarterly", curr_date: str = None
    ) -> str:
        table = self._financial_report_sina(ticker, "现金流量表")
        return f"## Cashflow ({ticker})\n\n{table}"

    def get_income_statement(
        self, ticker: str, freq: str = "quarterly", curr_date: str = None
    ) -> str:
        table = self._financial_report_sina(ticker, "利润表")
        return f"## Income Statement ({ticker})\n\n{table}"

    def get_news(self, ticker: str, start_date: str, end_date: str) -> str:
        with AKSHARE_CALL_LOCK:
            ak = self._ak()
            code = self._normalize_symbol(ticker)
            try:
                df = ak.stock_news_em(symbol=code)
                if df is None or df.empty:
                    return f"No news found for {ticker}"

                date_col = "发布时间" if "发布时间" in df.columns else None
                if date_col is not None:
                    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
                    df = df.dropna(subset=[date_col])
                    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
                    end_dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
                    df = df[(df[date_col] >= start_dt) & (df[date_col] < end_dt)]

                if df.empty:
                    return f"No news found for {ticker} between {start_date} and {end_date}"

                rows = []
                for _, row in df.head(20).iterrows():
                    title = str(row.get("新闻标题", row.get("标题", "No title")))
                    src = str(row.get("文章来源", row.get("来源", "Unknown")))
                    summary = str(row.get("新闻内容", row.get("内容", "")))
                    link = str(row.get("新闻链接", row.get("链接", "")))
                    rows.append(f"### {title} (source: {src})")
                    if summary and summary != "nan":
                        rows.append(summary[:400])
                    if link and link != "nan":
                        rows.append(f"Link: {link}")
                    rows.append("")

                return f"## {ticker} 新闻（{start_date} 至 {end_date}）：\n\n" + "\n".join(rows)
            except Exception as exc:
                raise NotImplementedError(
                    f"cn_akshare is temporarily unavailable for news: {exc}"
                ) from exc

    def get_global_news(
        self, curr_date: str, look_back_days: int = 7, limit: int = 50
    ) -> str:
        with AKSHARE_CALL_LOCK:
            ak = self._ak()
            try:
                if hasattr(ak, "news_cctv"):
                    target_dt = datetime.strptime(curr_date, "%Y-%m-%d")
                    used_date = curr_date
                    df = ak.news_cctv(date=curr_date.replace("-", ""))
                    if df is None or df.empty:
                        # Fallback: if today's feed is empty, try recent 3 days.
                        for back in range(1, 4):
                            probe_dt = target_dt - timedelta(days=back)
                            probe_date = probe_dt.strftime("%Y-%m-%d")
                            probe_df = ak.news_cctv(date=probe_date.replace("-", ""))
                            if probe_df is not None and not probe_df.empty:
                                df = probe_df
                                used_date = probe_date
                                break
                    if df is None or df.empty:
                        return f"{curr_date} 未获取到全球市场新闻（已回看最近3天）"
                    rows = []
                    for _, row in df.head(limit).iterrows():
                        title = str(row.get("title", row.get("标题", "No title")))
                        content = str(row.get("content", row.get("内容", "")))
                        rows.append(f"### {title}")
                        if content and content != "nan":
                            rows.append(content[:300])
                        rows.append("")
                    start = (
                        datetime.strptime(curr_date, "%Y-%m-%d") - timedelta(days=look_back_days)
                    ).strftime("%Y-%m-%d")
                    if used_date != curr_date:
                        return (
                            f"## 全球市场新闻（{start} 至 {curr_date}，当日为空，回退至 {used_date}）：\n\n"
                            + "\n".join(rows)
                        )
                    return f"## 全球市场新闻（{start} 至 {curr_date}）：\n\n" + "\n".join(rows)
                return "当前 cn_akshare 实现暂不支持全球新闻接口。"
            except Exception as exc:
                raise NotImplementedError(
                    f"cn_akshare is temporarily unavailable for global news: {exc}"
                ) from exc

    def get_insider_transactions(
        self, symbol: str, curr_date: str | None = None
    ) -> str:
        ak = self._ak()
        code = self._normalize_symbol(symbol)
        errors = []
        try:
            # stock_ggcg_em 不支持按个股代码查询，默认全市场数据量较大
            with AKSHARE_CALL_LOCK:
                df = ak.stock_main_stock_holder(stock=code)
            if df is not None and not df.empty:
                return (
                    f"## Insider Transactions for {symbol}\n\n"
                    f"{df.head(20).to_markdown(index=False)}"
                )
            errors.append("stock_main_stock_holder: empty dataframe")
        except Exception as exc:
            errors.append(f"stock_main_stock_holder: {type(exc).__name__}")

        try:
            # 退化为最近相关新闻，至少保证接口有可用输出
            end_dt = (
                datetime.strptime(curr_date, "%Y-%m-%d")
                if curr_date
                else datetime.now()
            )
            end_date = end_dt.strftime("%Y-%m-%d")
            start_date = (end_dt - timedelta(days=14)).strftime("%Y-%m-%d")
            news = self.get_news(symbol, start_date, end_date)
            return (
                f"## Insider Transactions for {symbol}\n\n"
                f"未获取到股东交易明细，降级返回近两周公司相关新闻：\n\n{news}"
            )
        except Exception as exc:
            errors.append(f"news_fallback: {type(exc).__name__}")

        raise NotImplementedError(
            f"cn_akshare is temporarily unavailable for insider transactions: {'; '.join(errors)}"
        )

    # TTL cache for stock_zh_a_spot_em to avoid hammering Eastmoney under concurrent load
    _spot_cache: "pd.DataFrame | None" = None
    _spot_cache_ts: float = 0.0
    _SPOT_CACHE_TTL: float = 8.0  # seconds
    _fund_flow_rank_cache: dict[str, tuple[float, "pd.DataFrame"]] = {}
    _FUND_FLOW_RANK_CACHE_TTL: float = 60.0
    _buyback_cache: "pd.DataFrame | None" = None
    _buyback_cache_ts: float = 0.0
    _buyback_cache_source: Any = None
    _BUYBACK_CACHE_TTL: float = 300.0

    def get_realtime_quotes(self, symbols: list[str]) -> str:
        """Fetch real-time A-share quotes. Tries Eastmoney first, falls back to Sina."""
        import json
        import time as _time
        import logging

        logger = logging.getLogger(__name__)

        # Build normalized code → original symbol map
        code_to_original: dict[str, str] = {}
        for s in symbols:
            if not s or not s.strip():
                continue
            try:
                code = self._normalize_symbol(s)
            except NotImplementedError:
                continue
            if code and code not in code_to_original:
                code_to_original[code] = s.strip().upper()

        if not code_to_original:
            return json.dumps({})

        # Try Sina first (lightweight, rarely blocked)
        try:
            result = self._fetch_quotes_sina(code_to_original)
            if result and result != "{}":
                return self._enrich_realtime_quotes(result, symbols)
        except Exception as exc:
            logger.debug("[realtime-quotes] Sina failed, falling back to Eastmoney: %s", exc)

        # Fallback: Eastmoney via akshare (cached)
        now = _time.time()
        if (
            CnAkshareProvider._spot_cache is not None
            and (now - CnAkshareProvider._spot_cache_ts) < CnAkshareProvider._SPOT_CACHE_TTL
        ):
            df = CnAkshareProvider._spot_cache
        else:
            try:
                with AKSHARE_CALL_LOCK:
                    ak = self._ak()
                    df = ak.stock_zh_a_spot_em()
            except TimeoutError as exc:
                _lock_logger.warning("[realtime-quotes] Eastmoney slot timeout: %s", exc)
                return json.dumps({})
            except Exception as exc:
                _lock_logger.warning("[realtime-quotes] Eastmoney fetch failed: %s", exc)
                return json.dumps({})
            CnAkshareProvider._spot_cache = df
            CnAkshareProvider._spot_cache_ts = now

        if df is not None and not df.empty:
            return self._enrich_realtime_quotes(
                self._build_quotes_from_em(df, code_to_original),
                symbols,
            )
        return json.dumps({})

    @staticmethod
    def _enrich_realtime_quotes(raw_json: str, symbols: list[str]) -> str:
        """Fill turnover/volume-ratio gaps from Tencent without replacing price.

        Sina is intentionally kept as the fast price source.  A successful
        but partial Sina response previously stopped the vendor chain before
        Tencent could provide turnover fields.
        """
        import json

        try:
            primary = json.loads(raw_json or "{}")
        except (TypeError, ValueError):
            return raw_json
        if not isinstance(primary, dict) or not primary:
            return raw_json
        if all(
            isinstance(item, dict)
            and item.get("turnover_rate") is not None
            and item.get("volume_ratio") is not None
            for item in primary.values()
        ):
            return raw_json

        try:
            from .cn_astock_provider import CnAstockProvider

            fallback = json.loads(CnAstockProvider().get_realtime_quotes(symbols))
        except Exception:
            return raw_json
        if not isinstance(fallback, dict):
            return raw_json

        enrichment_fields = (
            "turnover_rate",
            "volume_ratio",
            "limit_up",
            "limit_down",
            "market_cap",
            "pe_ttm",
            "pe_static",
            "pb",
        )
        for symbol, quote in primary.items():
            extra = fallback.get(symbol)
            if not isinstance(quote, dict) or not isinstance(extra, dict):
                continue
            enriched = False
            for field in enrichment_fields:
                if quote.get(field) is None and extra.get(field) is not None:
                    quote[field] = extra[field]
                    enriched = True
            if enriched:
                quote["source"] = f"{quote.get('source', 'primary')}+tencent"
        return json.dumps(primary, ensure_ascii=False)

    def _build_quotes_from_em(self, df: "pd.DataFrame", code_to_original: dict[str, str]) -> str:
        import json
        normalized = list(code_to_original.keys())
        df = df[df["代码"].isin(normalized)]
        result: dict[str, dict] = {}
        for _, row in df.iterrows():
            code = str(row.get("代码", ""))
            original = code_to_original.get(code)
            if not original:
                continue
            price = self._safe_float(row.get("最新价"))
            prev_close = self._safe_float(row.get("昨收"))
            change = round(price - prev_close, 4) if price is not None and prev_close else None
            change_pct = round(change / prev_close * 100, 4) if change is not None and prev_close else None
            result[original] = {
                "price": price,
                "open": self._safe_float(row.get("今开")),
                "high": self._safe_float(row.get("最高")),
                "low": self._safe_float(row.get("最低")),
                "previous_close": prev_close,
                "change": change,
                "change_pct": change_pct,
                "volume": self._safe_float(row.get("成交量")),
                "amount": self._safe_float(row.get("成交额")),
                "source": "eastmoney",
            }
        return json.dumps(result, ensure_ascii=False)

    def _fetch_quotes_sina(self, code_to_original: dict[str, str]) -> str:
        """Fetch quotes from Sina Finance hq.sinajs.cn as fallback."""
        import json
        import requests as _requests

        sina_codes = []
        sina_to_original: dict[str, str] = {}
        for code, original in code_to_original.items():
            prefix = "sh" if code.startswith(("5", "6", "9")) else "bj" if code.startswith(("4", "8")) else "sz"
            sina_code = f"{prefix}{code}"
            sina_codes.append(sina_code)
            sina_to_original[sina_code] = original

        if not sina_codes:
            return json.dumps({})

        try:
            resp = _requests.get(
                "https://hq.sinajs.cn/list=" + ",".join(sina_codes),
                headers={"Referer": "https://finance.sina.com.cn/", "User-Agent": "Mozilla/5.0"},
                timeout=5,
            )
            resp.encoding = "gbk"
        except Exception:
            return json.dumps({})

        result: dict[str, dict] = {}
        for line in resp.text.splitlines():
            line = line.strip()
            if not line or '="' not in line:
                continue
            try:
                var_part, data_part = line.split('="', 1)
                sina_code = var_part.split("_")[-1]
                fields = data_part.rstrip('";').split(",")
                if len(fields) < 10:
                    continue
                original = sina_to_original.get(sina_code)
                if not original:
                    continue
                price = self._safe_float(fields[3])
                prev_close = self._safe_float(fields[2])
                change = round(price - prev_close, 4) if price is not None and prev_close else None
                change_pct = round(change / prev_close * 100, 4) if change is not None and prev_close else None
                # Sina fields[30]=date, fields[31]=time
                quote_time = None
                if len(fields) > 31 and fields[30] and fields[31]:
                    quote_time = f"{fields[30]} {fields[31]}"
                result[original] = {
                    "price": price,
                    "open": self._safe_float(fields[1]),
                    "high": self._safe_float(fields[4]),
                    "low": self._safe_float(fields[5]),
                    "previous_close": prev_close,
                    "change": change,
                    "change_pct": change_pct,
                    "volume": self._safe_float(fields[8]),
                    "amount": self._safe_float(fields[9]),
                    "quote_time": quote_time,
                    "source": "sina",
                }
            except (ValueError, IndexError):
                continue
        return json.dumps(result, ensure_ascii=False)

    @staticmethod
    def _safe_float(val) -> float | None:
        if val is None:
            return None
        try:
            f = float(val)
            return f if not pd.isna(f) else None
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _fund_flow_sort_value(value) -> float:
        """Normalize mixed Chinese money strings to a comparable sorting value."""
        if value is None or pd.isna(value):
            return float("-inf")
        text = str(value).replace(",", "").strip()
        match = re.search(r"-?\d+(?:\.\d+)?", text)
        if not match:
            return float("-inf")
        amount = float(match.group())
        if "亿" in text:
            amount *= 10000
        return amount

    def get_board_fund_flow(self) -> str:
        """获取行业板块资金流向排名，主接口失败时尝试 AKShare fallback。"""
        # Primary: stock_fund_flow_industry
        result = self._board_fund_flow_primary()
        if not ("获取失败" in result or "不可用" in result):
            return result

        # Fallback: stock_fund_flow_individual (概念板块资金流)
        try:
            ak = self._ak()
            with AKSHARE_CALL_LOCK:
                df = ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流")
            if df is not None and not df.empty:
                df_sorted = df.reset_index(drop=True)
                df_sorted.insert(0, "排名", range(1, len(df_sorted) + 1))
                total = len(df_sorted)
                result_fb = df_sorted.head(10).to_string(index=False)
                return f"板块资金流向排名（fallback，共{total}个板块，前10名）：\n{result_fb}"
        except Exception as fb_exc:
            pass

        # Both failed
        return f"板块资金流向数据获取失败（主接口和 fallback 均不可用）：{result}"

    def _board_fund_flow_primary(self) -> str:
        """Primary board fund flow via stock_fund_flow_industry."""
        try:
            ak = self._ak()
            with AKSHARE_CALL_LOCK:
                df = ak.stock_fund_flow_industry(symbol="即时")
            if df is None or df.empty:
                return "今日板块资金流向数据暂不可用。"
            sort_col = next(
                (
                    column
                    for column in ("净额", "今日主力净流入-净额", "主力净流入")
                    if column in df.columns
                ),
                None,
            )
            if sort_col:
                df_sorted = (
                    df.assign(
                        _normalized_net=df[sort_col].map(
                            self._fund_flow_sort_value
                        )
                    )
                    .sort_values("_normalized_net", ascending=False)
                    .drop(columns=["_normalized_net"])
                    .reset_index(drop=True)
                )
            else:
                df_sorted = df.reset_index(drop=True)
            df_sorted.insert(0, "排名", range(1, len(df_sorted) + 1))
            total = len(df_sorted)
            result = df_sorted.head(10).to_string(index=False)
            return f"板块资金流向排名（共{total}个板块，前10名）：\n{result}"
        except Exception as exc:
            return f"板块资金流向数据获取失败：{type(exc).__name__}: {exc}"

    def get_individual_fund_flow(self, symbol: str) -> str:
        """获取个股近期主力资金净流向。"""
        primary_error = ""
        try:
            ak = self._ak()
            code = self._normalize_symbol(symbol)
            # 沪市：以 5、6、9 开头；其余为深市
            market = "sh" if code[:1] in ("5", "6", "9") else "sz"
            with AKSHARE_CALL_LOCK:
                df = ak.stock_individual_fund_flow(stock=code, market=market)
            if df is None or df.empty:
                primary_error = "Eastmoney daily endpoint returned no rows"
            else:
                df_recent = df.tail(20)
                return (
                    f"{symbol} 近20日主力资金净流向"
                    "（AKShare stock_individual_fund_flow，单位：元）：\n"
                    f"{df_recent.to_string(index=False)}"
                )
        except Exception as exc:
            primary_error = f"{type(exc).__name__}: {exc}"

        fallback = self._individual_fund_flow_ths(symbol)
        if fallback:
            return (
                f"{symbol} [DATA-024] FUND_FLOW_AGGREGATE_HAS_DATA: "
                "同花顺独立 fallback（当日和5日汇总，非逐日20日序列；"
                "金额保留接口原始单位，禁止据此声称连续逐日流入/流出）。\n"
                f"{fallback}"
            )
        return (
            f"{symbol} [DATA-024] FUND_FLOW_FAILED: 个股资金流向数据获取失败；"
            f"Eastmoney={primary_error or 'unknown'}；THS fallback 无可用记录。"
        )

    @classmethod
    def _fund_flow_rank_frame(cls, ak: Any, indicator: str) -> "pd.DataFrame":
        now = time.time()
        cached = cls._fund_flow_rank_cache.get(indicator)
        if cached and now - cached[0] < cls._FUND_FLOW_RANK_CACHE_TTL:
            return cached[1]
        with AKSHARE_CALL_LOCK:
            frame = ak.stock_fund_flow_individual(symbol=indicator)
        if frame is not None and not frame.empty:
            cls._fund_flow_rank_cache[indicator] = (now, frame)
        return frame

    def _individual_fund_flow_ths(self, symbol: str) -> str:
        """Return THS aggregate rows as an independent, lower-fidelity fallback."""
        try:
            ak = self._ak()
            code = self._normalize_symbol(symbol)
            sections: list[str] = []
            for indicator in ("即时", "5日排行"):
                frame = self._fund_flow_rank_frame(ak, indicator)
                if frame is None or frame.empty:
                    continue
                code_col = next(
                    (
                        column
                        for column in ("股票代码", "代码", "证券代码")
                        if column in frame.columns
                    ),
                    None,
                )
                if code_col is None:
                    continue
                normalized_codes = (
                    frame[code_col]
                    .astype(str)
                    .str.extract(r"(\d{1,6})", expand=False)
                    .str.zfill(6)
                )
                matched = frame[normalized_codes == code]
                if matched.empty:
                    continue
                sections.append(
                    f"【{indicator}】\n{matched.head(1).to_string(index=False)}"
                )
            return "\n".join(sections)
        except Exception:
            return ""

    def get_lhb_detail(self, symbol: str, date: str, *, force: bool = False) -> str:
        """获取龙虎榜数据。

        Args:
            symbol: 股票代码
            date: 查询日期（YYYY-MM-DD）
            force: 是否强制查询。默认 False 时返回提示信息以避免批量调用触发限流。
        """
        if not force:
            return (
                f"{symbol} [G-007] LHB_NOT_QUERIED: 龙虎榜查询未触发（force=False）。"
                "龙虎榜为按需查询接口，仅当检测到资金异动时才应调用，以避免批量日期查询触发 API 限流。"
            )
        try:
            ak = self._ak()
            code = self._normalize_symbol(symbol)
            query_date = date.replace("-", "")
            with AKSHARE_CALL_LOCK:
                df = ak.stock_lhb_detail_em(
                    start_date=query_date,
                    end_date=query_date,
                )
            if df is None or df.empty:
                return f"{symbol} [G-007] LHB_NORMAL_NO_DATA: 在 {date} 无龙虎榜数据（非异动日属正常）。"
            code_col = next(
                (column for column in ("代码", "证券代码", "股票代码") if column in df.columns),
                None,
            )
            if code_col is None:
                return (
                    f"{symbol} [G-007] LHB_FAILED: 龙虎榜返回缺少证券代码列，"
                    f"无法校验个股归属（columns={list(df.columns)}）。"
                )
            matched = df[
                df[code_col].astype(str).str.strip().str.zfill(6) == code
            ]
            if matched.empty:
                return f"{symbol} [G-007] LHB_NORMAL_NO_DATA: 在 {date} 无龙虎榜数据（非异动日属正常）。"
            return f"{symbol} [G-007] LHB_HAS_DATA: 龙虎榜明细（{date}）：\n{matched.head(20).to_string(index=False)}"
        except Exception as exc:
            return f"{symbol} [G-007] LHB_FAILED: 龙虎榜数据获取失败：{type(exc).__name__}: {exc}"

    def get_zt_pool(self, date: str) -> str:
        """获取涨停板情绪池，反映市场整体情绪温度。"""
        try:
            ak = self._ak()
            with AKSHARE_CALL_LOCK:
                df = ak.stock_zt_pool_em(date=date.replace("-", ""))
            if df is None or df.empty:
                return f"{date} 涨停板情绪池数据暂不可用。"
            count = len(df)
            result = f"{date} 涨停家数：{count}\n"
            if "连板数" in df.columns:
                lianban = df["连板数"].value_counts().sort_index()
                result += f"连板分布：\n{lianban.head(10).to_string()}"
            return result
        except Exception as exc:
            return f"涨停板情绪池数据获取失败：{type(exc).__name__}: {exc}"

    def get_hot_stocks_xq(self) -> str:
        """获取雪球热搜股票，反映散户关注度。"""
        try:
            ak = self._ak()
            with AKSHARE_CALL_LOCK:
                df = ak.stock_hot_follow_xq(symbol="最热门")
            if df is None or df.empty:
                return "雪球热搜数据暂不可用。"
            return f"雪球热搜前20：\n{df.head(20).to_string(index=False)}"
        except Exception as exc:
            return f"雪球热搜数据获取失败：{type(exc).__name__}: {exc}"

    def get_margin_trading(self, symbol: str) -> str:
        """获取个股融资融券数据。  # [DATA-010] margin_trading_raw_evidence"""
        code = self._normalize_symbol(symbol)
        try:
            ak = self._ak()
            if code[:1] in ("0", "1", "2", "3"):
                func = "stock_margin_underlying_info_szse"
                with AKSHARE_CALL_LOCK:
                    df = ak.stock_margin_underlying_info_szse(date=datetime.now().strftime("%Y%m%d"))
            else:
                func = "stock_margin_underlying_info_sse"
                with AKSHARE_CALL_LOCK:
                    df = ak.stock_margin_underlying_info_sse(date=datetime.now().strftime("%Y%m%d"))
            if df is None or df.empty:
                return f"{symbol} 融资融券数据暂不可用。"
            code_col = None
            for col in df.columns:
                if "代码" in col or "证券代码" in col:
                    code_col = col
                    break
            if code_col:
                mask = df[code_col].astype(str).str.contains(code, na=False)
                df_filtered = df[mask]
                if df_filtered.empty:
                    return f"{symbol} [DATA-010] MARGIN_NORMAL_NO_DATA: 该股非融资融券标的或当日无数据。"
                return f"{symbol} [DATA-010] MARGIN_HAS_DATA: 融资融券数据（via {func}）：\n{df_filtered.tail(10).to_string(index=False)}"
            return f"{symbol} [DATA-010] MARGIN_HAS_DATA: 融资融券数据（via {func}）：\n{df.tail(20).to_string(index=False)}"
        except Exception as exc:
            return f"{symbol} [DATA-010] MARGIN_FAILED: 融资融券数据获取失败：{type(exc).__name__}: {exc}"

    def get_research_report(self, symbol: str) -> str:
        """获取个股券商研报数据。  # [DATA-011] research_report_raw_evidence"""
        code = self._normalize_symbol(symbol)
        try:
            ak = self._ak()
            with AKSHARE_CALL_LOCK:
                df = ak.stock_research_report_em(symbol=code)
            if df is None or df.empty:
                return f"{symbol} [DATA-011] REPORT_NORMAL_NO_DATA: 该股无券商研报数据。"
            lines = [
                f"{symbol} [DATA-011] REPORT_HAS_DATA: "
                "券商研报元数据（AKShare stock_research_report_em，仅作观点/预期源）："
            ]
            for _, row in df.head(15).iterrows():
                date_val = row.get("日期", row.get("date", ""))
                org = row.get("机构", row.get("研究机构", row.get("org", "")))
                rating = row.get("东财评级", row.get("评级", row.get("rating", "")))
                title = row.get("报告名称", row.get("标题", row.get("title", "")))
                pdf_url = row.get("报告PDF链接", "")
                line = f"- {date_val} | {org} | {rating}"
                if title:
                    line += f" | {str(title)[:60]}"
                if pdf_url:
                    line += f" | PDF: {pdf_url}"
                lines.append(line)
            return "\n".join(lines)
        except AttributeError:
            return f"{symbol} [DATA-011] REPORT_NORMAL_NO_DATA: AKShare 研报接口不可用。"
        except Exception as exc:
            return f"{symbol} [DATA-011] REPORT_FAILED: 研报数据获取失败：{type(exc).__name__}: {exc}"

    def get_ratings(self, symbol: str) -> str:
        """获取个股分析师评级数据。  # [DATA-012] rating_raw_evidence"""
        code = self._normalize_symbol(symbol)
        try:
            ak = self._ak()
            with AKSHARE_CALL_LOCK:
                df = ak.stock_institute_recommend_detail(symbol=code)
            if df is None or df.empty:
                return f"{symbol} [DATA-012] RATINGS_NORMAL_NO_DATA: 该股无分析师评级数据。"
            lines = [f"{symbol} [DATA-012] RATINGS_HAS_DATA: 分析师评级数据（AKShare stock_institute_recommend_detail）："]
            for _, row in df.head(20).iterrows():
                date_val = row.get("评级日期", row.get("date", ""))
                rating = row.get("最新评级", row.get("rating", ""))
                org = row.get("评级机构", row.get("org", ""))
                analyst = row.get("分析师", "")
                target_price = row.get("目标价", "")
                line = f"- {date_val} | {org} | {rating}"
                if analyst:
                    line += f" | 分析师: {analyst}"
                if target_price and str(target_price) != "nan":
                    line += f" | 目标价: {target_price}"
                lines.append(line)
            return "\n".join(lines)
        except AttributeError:
            return f"{symbol} [DATA-012] RATINGS_NORMAL_NO_DATA: AKShare 评级接口不可用。"
        except Exception as exc:
            return f"{symbol} [DATA-012] RATINGS_FAILED: 评级数据获取失败：{type(exc).__name__}: {exc}"

    def get_buybacks(self, symbol: str) -> str:
        """获取个股回购计划/进展数据。  # [DATA-013] buyback_raw_evidence"""
        code = self._normalize_symbol(symbol)
        try:
            ak = self._ak()
            df = self._buyback_frame(ak)
            if df is None or df.empty:
                return f"{symbol} [DATA-013] BUYBACK_NORMAL_NO_DATA: 该股无回购计划或进展数据。"
            code_col = next(
                (col for col in ("股票代码", "证券代码", "代码") if col in df.columns),
                None,
            )
            if not code_col:
                return (
                    f"{symbol} [DATA-013] BUYBACK_FAILED: "
                    "回购数据缺少股票代码字段，无法校验目标公司。"
                )
            normalized_codes = (
                df[code_col]
                .astype(str)
                .str.extract(r"(\d{1,6})", expand=False)
                .str.zfill(6)
            )
            df = df.loc[normalized_codes.eq(code)]
            if df.empty:
                return f"{symbol} [DATA-013] BUYBACK_NORMAL_NO_DATA: 该股无回购计划或进展数据。"
            lines = [f"{symbol} [DATA-013] BUYBACK_HAS_DATA: 回购数据（AKShare stock_repurchase_em）："]
            for _, row in df.head(15).iterrows():
                date_val = row.get("最新公告日期", row.get("公告日期", row.get("date", "")))
                executed_amount = row.get("已回购金额")
                executed_volume = row.get("已回购股份数量")
                amount = (
                    executed_amount
                    if pd.notna(executed_amount)
                    else _format_value_range(
                        row.get("计划回购金额区间-下限"),
                        row.get("计划回购金额区间-上限"),
                    )
                )
                volume = (
                    executed_volume
                    if pd.notna(executed_volume)
                    else _format_value_range(
                        row.get("计划回购数量区间-下限"),
                        row.get("计划回购数量区间-上限"),
                    )
                )
                if not amount:
                    amount = row.get("回购金额", row.get("amount", ""))
                if not volume:
                    volume = row.get("回购数量", row.get("volume", ""))
                progress = row.get("实施进度", row.get("回购进度", row.get("progress", "")))
                purpose = row.get("回购目的", row.get("purpose", ""))
                line = f"- {date_val}"
                if amount is not None and str(amount) != "nan":
                    line += f" | 金额: {amount}"
                if volume is not None and str(volume) != "nan":
                    line += f" | 数量: {volume}"
                if progress is not None and str(progress) != "nan":
                    line += f" | 进度: {progress}"
                if purpose is not None and str(purpose) != "nan":
                    line += f" | 目的: {str(purpose)[:40]}"
                lines.append(line)
            return "\n".join(lines)
        except AttributeError:
            return f"{symbol} [DATA-013] BUYBACK_NORMAL_NO_DATA: AKShare 回购接口不可用。"
        except Exception as exc:
            return f"{symbol} [DATA-013] BUYBACK_FAILED: 回购数据获取失败：{type(exc).__name__}: {exc}"

    @classmethod
    def _buyback_frame(cls, ak: Any) -> "pd.DataFrame":
        """Return the all-market buyback table with a short process-local TTL."""
        now = time.time()
        if (
            cls._buyback_cache is not None
            and cls._buyback_cache_source is ak
            and now - cls._buyback_cache_ts < cls._BUYBACK_CACHE_TTL
        ):
            return cls._buyback_cache
        with AKSHARE_CALL_LOCK:
            now = time.time()
            if (
                cls._buyback_cache is not None
                and cls._buyback_cache_source is ak
                and now - cls._buyback_cache_ts < cls._BUYBACK_CACHE_TTL
            ):
                return cls._buyback_cache
            frame = ak.stock_repurchase_em()
            cls._buyback_cache = frame
            cls._buyback_cache_ts = now
            cls._buyback_cache_source = ak
            return frame
