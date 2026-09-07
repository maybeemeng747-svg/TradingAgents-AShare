"""DataCollector: fetch all data once, serve windowed views to analyst agents."""
from __future__ import annotations

from concurrent.futures import Future, wait as futures_wait
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
import json
import os
import queue
import re
import threading
import time
import pandas as pd
from stockstats import wrap
import io
import logging

from tradingagents.agents.utils.agent_utils import (
    get_stock_data,
    get_realtime_quotes,
    get_indicators,
    get_fundamentals,
    get_balance_sheet,
    get_cashflow,
    get_income_statement,
    get_news,
    get_global_news,
    get_insider_transactions,
    get_board_fund_flow,
    get_individual_fund_flow,
    get_lhb_detail,
    get_zt_pool,
    get_hot_stocks_xq,
    get_announcements,
    get_margin_trading,  # [DATA-010] margin_trading_raw_evidence
    get_ratings,  # [DATA-012A] rating_data_collector_wiring
    get_research_report,  # [DATA-011A] research_report_collector_wiring
    get_buybacks,  # [DATA-013A] buyback_collector_wiring
)
from tradingagents.dataflows.interface import (  # [N-003] cn_astock_raw_evidence
    clear_current_thread_hit_vendor,
    get_current_thread_hit_vendor,
    get_last_hit_vendor,
)
from tradingagents.dataflows.evidence_contract import (  # [DATA-004] raw_evidence_contract
    resolve_data_type as _resolve_data_type_contract,
    resolve_endpoint as _resolve_endpoint_contract,
    resolve_fallback_info as _resolve_fallback_info_contract,
)
from tradingagents.agents.utils.context_utils import (  # [HK-001] hk_market_boundary
    infer_instrument_context,
    is_daily_bar_final,
    market_today_str,
)
from tradingagents.dataflows.financial_periods import (  # [FUND-002] financial_period_normalizer
    derive_single_quarters,
    normalize_financial_markdown,
)
from tradingagents.dataflows.instrument_identity import (  # [FUND-001] instrument_identity_gate
    build_instrument_identity,
    extract_profile_from_fundamentals,
)

INDICATORS = [
    "close_50_sma", "close_200_sma", "close_10_ema",
    "rsi", "macd", "boll", "boll_ub", "boll_lb", "atr", "vwma",
]
SHORT_DAYS = 14
LONG_DAYS = 90
# [UPSTREAM-081-001] Hard cap for one whole fetch round. A stuck provider
# (network stall beyond the socket timeout, vendor-internal retry loops)
# must not pin the per-key lock and drain the asyncio default executor
# forever.
FETCH_ALL_TIMEOUT = float(os.getenv("TA_DATA_FETCH_TIMEOUT", "300"))
# [UPSTREAM-081-001] The per-key lock must outlast one whole fetch round
# (+ margin) so queueing collectors do not fail while a round is still inside
# its budget. The timeout is DERIVED from the fetch budget: a custom
# TA_DATA_FETCH_LOCK_TIMEOUT is honoured only when it keeps that margin, so an
# incompatible env pair can no longer violate the invariant at import time.
FETCH_LOCK_TIMEOUT_MARGIN = 60.0
_FETCH_LOCK_TIMEOUT_ENV = float(os.getenv("TA_DATA_FETCH_LOCK_TIMEOUT", "360"))
FETCH_LOCK_TIMEOUT = max(
    _FETCH_LOCK_TIMEOUT_ENV, FETCH_ALL_TIMEOUT + FETCH_LOCK_TIMEOUT_MARGIN
)
# [UPSTREAM-081-001] Process-wide bounded worker pool for all fetch rounds
# (see _BoundedFetchPool). Cap is the whole-process ceiling for leaked
# (abandoned, socket-bound) fetch threads.
FETCH_POOL_MAX_WORKERS = max(
    1, int(os.getenv("TA_DATA_FETCH_POOL_WORKERS", "16"))
)
# [UPSTREAM-081-001 fix round3] Hard cap on the shared pool's queue depth.
# A saturated pool must REJECT new submits (explicit degradation) instead of
# silently enqueuing work that would only execute after its round deadline.
FETCH_POOL_MAX_QUEUED = max(
    1, int(os.getenv("TA_DATA_FETCH_POOL_MAX_QUEUED", "64"))
)

import numpy as np

_OHLCV_COLS = ["date", "open", "high", "low", "close", "volume"]

_logger = logging.getLogger(__name__)

if FETCH_LOCK_TIMEOUT > _FETCH_LOCK_TIMEOUT_ENV:
    _logger.warning(
        "TA_DATA_FETCH_LOCK_TIMEOUT=%g violates the invariant "
        "FETCH_LOCK_TIMEOUT >= TA_DATA_FETCH_TIMEOUT(%g) + %g; "
        "derived FETCH_LOCK_TIMEOUT=%g",
        _FETCH_LOCK_TIMEOUT_ENV,
        FETCH_ALL_TIMEOUT,
        FETCH_LOCK_TIMEOUT_MARGIN,
        FETCH_LOCK_TIMEOUT,
    )


class _FetchPoolSaturated(RuntimeError):
    """[UPSTREAM-081-001 fix round3] submit() rejected: the bounded fetch
    queue is full. Callers must degrade explicitly — never enqueue silently,
    because a task left queued past its round deadline must not execute."""


class _BoundedFetchPool:
    """Process-wide bounded daemon worker pool shared by all fetch rounds.

    [UPSTREAM-081-001] Each fetch round used to build its own
    ``ThreadPoolExecutor``; abandoned (stuck) workers cannot be cancelled, so
    every round that hit the deadline leaked one live thread + connection per
    timed-out source, and threads accumulated without bound as rounds piled
    up. All rounds now share this pool: the leak is hard-capped at
    ``FETCH_POOL_MAX_WORKERS`` daemon threads for the whole process, threads
    carry the ``ta-data-fetch`` name prefix for dumps, and leak/backlog
    gauges are exposed via :func:`get_fetch_pool_stats` (surfaced on
    ``/healthz``).

    [UPSTREAM-081-001 fix round3] The work queue is bounded
    (``FETCH_POOL_MAX_QUEUED``): ``submit`` raises :class:`_FetchPoolSaturated`
    on saturation so callers degrade explicitly instead of growing an
    unbounded backlog of tasks that would execute only after their round
    deadline expired. Deadline expiry itself cancels queued futures — a
    cancelled task is guaranteed never to run (the worker re-checks
    ``set_running_or_notify_cancel`` after dequeue); only tasks already
    running on a worker stay bounded by the worker cap + socket timeout.
    """

    def __init__(self, max_workers: int, max_queued: Optional[int] = None) -> None:
        self._max_workers = max(1, int(max_workers))
        self._max_queued = (
            FETCH_POOL_MAX_QUEUED if max_queued is None else max(1, int(max_queued))
        )
        self._work: "queue.Queue" = queue.Queue(maxsize=self._max_queued)
        self._lock = threading.Lock()
        self._active = 0
        self._abandoned = 0
        self._cancelled = 0
        self._rejected = 0
        self._threads: List[threading.Thread] = []
        for idx in range(self._max_workers):
            thread = threading.Thread(
                target=self._worker, name=f"ta-data-fetch-{idx}", daemon=True
            )
            thread.start()
            self._threads.append(thread)

    def _worker(self) -> None:
        while True:
            fn, future = self._work.get()
            if not future.set_running_or_notify_cancel():
                # Cancelled while queued: guaranteed never to execute.
                continue
            with self._lock:
                self._active += 1
            try:
                result = fn()
            except BaseException as exc:  # propagate to the waiting round
                future.set_exception(exc)
            else:
                future.set_result(result)
            finally:
                with self._lock:
                    self._active -= 1

    def submit(self, fn) -> "Future":
        future: Future = Future()
        try:
            # [UPSTREAM-081-001 fix round3] Bounded queue: saturation raises
            # instead of silently growing the backlog without bound.
            self._work.put_nowait((fn, future))
        except queue.Full:
            with self._lock:
                self._rejected += 1
            raise _FetchPoolSaturated(
                f"fetch pool saturated: {self._max_queued} tasks already queued"
            )
        return future

    def mark_abandoned(self, count: int = 1) -> None:
        """Count workers left running past their round deadline (leak gauge)."""
        with self._lock:
            self._abandoned += count

    def mark_cancelled(self, count: int = 1) -> None:
        """[fix round3] Count queued tasks cancelled before a worker started
        them: they are guaranteed never to execute."""
        with self._lock:
            self._cancelled += count

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "max_workers": self._max_workers,
                "max_queued": self._max_queued,
                "threads": len(self._threads),
                "active": self._active,
                "queued": self._work.qsize(),
                "abandoned_total": self._abandoned,
                "cancelled_total": self._cancelled,
                "rejected_total": self._rejected,
            }


_fetch_pool: Optional[_BoundedFetchPool] = None
_fetch_pool_lock = threading.Lock()


def get_fetch_pool() -> "_BoundedFetchPool":
    """Lazily create the shared fetch pool so importing this module stays
    side-effect free (scheduler/tests import it without fetching)."""
    global _fetch_pool
    if _fetch_pool is None:
        with _fetch_pool_lock:
            if _fetch_pool is None:
                _fetch_pool = _BoundedFetchPool(FETCH_POOL_MAX_WORKERS)
    return _fetch_pool


def get_fetch_pool_stats() -> Dict[str, int]:
    """Leak/backlog gauges for the shared fetch pool (``/healthz`` observability).

    Never forces pool creation; reports the configured ceiling with zeroed
    counters until the first fetch round runs.
    """
    pool = _fetch_pool
    if pool is None:
        return {
            "max_workers": FETCH_POOL_MAX_WORKERS,
            "max_queued": FETCH_POOL_MAX_QUEUED,
            "threads": 0,
            "active": 0,
            "queued": 0,
            "abandoned_total": 0,
            "cancelled_total": 0,
            "rejected_total": 0,
        }
    return pool.stats()


_EVIDENCE_KEY_TO_DATA_TYPE: Dict[str, str] = {
    "stock_data": "ohlcv",
    "realtime_quote": "realtime_quotes",
    "news": "news",
    "global_news": "global_news",
    "fund_flow_board": "board_fund_flow",
    "fund_flow_individual": "fund_flow",
    "lhb": "lhb",
    "fundamentals": "financials",
    "company_profile": "financials",  # [FUND-001] instrument_identity_gate
    "balance_sheet": "financials",
    "cashflow": "financials",
    "income_statement": "financials",
    "insider_transactions": "insider",
    "zt_pool": "zt_pool",
    "hot_stocks": "hot_stocks",
    "indicators": "ohlcv",
    "vpa_indicators": "ohlcv",
    "announcements": "notice",
    "research_report": "report",
    "ratings": "rating",  # [DATA-012A] rating_data_collector_wiring
    "buybacks": "buyback",
}


def _resolve_data_type_for_key(key: str) -> str:  # [DATA-004] raw_evidence_contract
    return _EVIDENCE_KEY_TO_DATA_TYPE.get(key, "")


def _resolve_endpoint_for_vendor(vendor: str, data_type: str) -> str:  # [DATA-004]
    if not data_type:
        return ""
    return _resolve_endpoint_contract(vendor, data_type)


def _resolve_fallback_for_vendor(vendor: str, data_type: str) -> Optional[str]:  # [DATA-004]
    if not data_type:
        return None
    return _resolve_fallback_info_contract(vendor, data_type)


def _parse_csv_to_dataframe(raw_csv: str) -> Optional[pd.DataFrame]:
    """Parse raw CSV string into a normalized OHLCV DataFrame.

    Returns None if parsing fails or the CSV is too short/empty.
    """
    if not isinstance(raw_csv, str) or len(raw_csv) <= 50:
        return None
    try:
        df = pd.read_csv(io.StringIO(raw_csv), on_bad_lines='skip', comment='#')
    except Exception:
        return None
    if df.empty:
        return None
    cols_map = {c.lower(): c for c in df.columns}
    rename_dict = {}
    for target in _OHLCV_COLS:
        if target in cols_map:
            rename_dict[cols_map[target]] = target
    df = df.rename(columns=rename_dict)
    return df


def _exclude_forming_daily_bar(
    raw_csv: Any,
    ticker: str,
    *,
    now: Optional[datetime] = None,
) -> Any:
    """Remove a provider's current forming row before indicator calculation."""
    df = _parse_csv_to_dataframe(raw_csv)
    if df is None or "date" not in df.columns:
        return raw_csv

    parsed_dates = pd.to_datetime(df["date"], errors="coerce")
    valid_dates = parsed_dates.dropna()
    if valid_dates.empty:
        return raw_csv
    latest_date = valid_dates.max().strftime("%Y-%m-%d")
    if is_daily_bar_final(ticker, latest_date, now=now):
        return raw_csv

    completed = df.loc[parsed_dates.dt.strftime("%Y-%m-%d") != latest_date].copy()
    original_headers = [
        line
        for line in str(raw_csv).splitlines()
        if line.lstrip().startswith("#")
        and "[INTRADAY-DAYBAR]" not in line
    ]
    header = (
        "# [INTRADAY-DAYBAR] "
        "current_day_status=PARTIAL_INTRADAY, "
        "action=excluded_from_daily_ohlcv\n"
    )
    if original_headers:
        header += "\n".join(original_headers) + "\n"
    if completed.empty:
        return (
            header
            + f"No data found for symbol '{ticker}' after excluding forming bar "
            + latest_date
        )
    return header + completed.to_csv(index=False)


# ── VPA (Volume Price Analysis) 预计算 ──────────────────────────


def _compute_vpa_indicators(df: pd.DataFrame, window: int = 20) -> str:
    """Pre-compute Volume Price Analysis indicators from OHLCV DataFrame.

    Returns a human-readable text block for the VPA analyst agent.
    All numerical comparisons are done here so the LLM only needs to
    interpret the results, not do arithmetic.
    """
    required = {"open", "high", "low", "close", "volume"}
    if not required.issubset(set(df.columns)):
        return "VPA 数据不足：缺少 OHLCV 列"

    df = df.copy()
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
    df["open"] = pd.to_numeric(df["open"], errors="coerce")
    df["high"] = pd.to_numeric(df["high"], errors="coerce")
    df["low"] = pd.to_numeric(df["low"], errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close", "volume"])

    if len(df) < window + 5:
        return "VPA 数据不足：历史 K 线数量不够"

    # ── 派生指标 ──
    df["vol_ma"] = df["volume"].rolling(window).mean()
    df["volume_ratio"] = df["volume"] / df["vol_ma"]

    hl_range = df["high"] - df["low"]
    df["bar_spread"] = hl_range / df["close"]  # 实体相对大小
    df["close_position"] = np.where(
        hl_range > 0,
        (df["close"] - df["low"]) / hl_range,
        0.5,
    )
    df["bar_type"] = np.where(
        df["close"] > df["open"], "阳线",
        np.where(df["close"] < df["open"], "阴线", "十字星"),
    )

    # 上下影线比例
    df["upper_shadow"] = np.where(
        hl_range > 0,
        (df["high"] - np.maximum(df["open"], df["close"])) / hl_range,
        0.0,
    )
    df["lower_shadow"] = np.where(
        hl_range > 0,
        (np.minimum(df["open"], df["close"]) - df["low"]) / hl_range,
        0.0,
    )

    # 价格变化率
    df["pct_change"] = df["close"].pct_change()

    # 量能趋势 (5日均量 vs 20日均量)
    df["vol_ma5"] = df["volume"].rolling(5).mean()
    df["vol_trend_ratio"] = df["vol_ma5"] / df["vol_ma"]

    # 量价一致性
    df["vp_harmony"] = np.where(
        (df["pct_change"] > 0) & (df["volume_ratio"] > 1.0), "一致(涨+放量)",
        np.where(
            (df["pct_change"] < 0) & (df["volume_ratio"] > 1.0), "一致(跌+放量)",
            np.where(
                (df["pct_change"] > 0) & (df["volume_ratio"] < 0.8), "背离(涨+缩量)",
                np.where(
                    (df["pct_change"] < 0) & (df["volume_ratio"] < 0.8), "背离(跌+缩量)",
                    "中性",
                ),
            ),
        ),
    )

    # OBV (On Balance Volume) 简易趋势 — vectorized
    close_diff = df["close"].diff()
    obv_sign = np.where(close_diff > 0, 1, np.where(close_diff < 0, -1, 0))
    obv_sign[0] = 0
    df["obv"] = (obv_sign * df["volume"].values).cumsum()
    obv_ma = df["obv"].rolling(10).mean()
    obv_trend = "上升" if len(obv_ma.dropna()) >= 2 and obv_ma.iloc[-1] > obv_ma.iloc[-5] else "下降"

    # ── 格式化输出（取最近 N 天）──
    output_days = min(30, len(df) - window)
    recent = df.tail(output_days).copy()

    lines = []
    lines.append(f"## VPA 预计算指标（基于 {window} 日均量基准）\n")
    lines.append(f"**OBV 趋势（10日）**: {obv_trend}")

    # 量能概况
    last = recent.iloc[-1]
    vol_5d = recent["volume"].tail(5).mean()
    vol_20d = last["vol_ma"] if pd.notna(last["vol_ma"]) else 0
    vol_summary = "放量" if vol_5d > vol_20d * 1.2 else ("缩量" if vol_5d < vol_20d * 0.8 else "平稳")
    lines.append(f"**近5日量能趋势**: {vol_summary}（5日均量/20日均量 = {last.get('vol_trend_ratio', 0):.2f}）\n")

    lines.append("### 逐日量价数据\n")
    lines.append("| 日期 | 类型 | 涨跌幅 | 实体大小 | 收盘位置 | 上影线 | 下影线 | 量比 | 量价关系 |")
    lines.append("|------|------|--------|----------|----------|--------|--------|------|----------|")

    for _, row in recent.iterrows():
        dt = row.get("date", "")
        if hasattr(dt, "strftime"):
            dt = dt.strftime("%m-%d")
        else:
            dt = str(dt)[-5:]

        pct = row["pct_change"] * 100 if pd.notna(row["pct_change"]) else 0
        spread_label = "宽" if row["bar_spread"] > 0.03 else ("窄" if row["bar_spread"] < 0.015 else "中")
        cp = row["close_position"]
        cp_label = "高位" if cp > 0.7 else ("低位" if cp < 0.3 else "中位")
        vr = row["volume_ratio"] if pd.notna(row["volume_ratio"]) else 0
        vr_label = f"{vr:.1f}"
        if vr > 2.0:
            vr_label += "(巨量)"
        elif vr > 1.5:
            vr_label += "(明显放量)"
        elif vr > 1.0:
            vr_label += "(温和放量)"
        elif vr < 0.5:
            vr_label += "(极度缩量)"
        elif vr < 0.8:
            vr_label += "(缩量)"

        lines.append(
            f"| {dt} | {row['bar_type']} | {pct:+.1f}% | {spread_label}({row['bar_spread']:.3f}) "
            f"| {cp_label}({cp:.2f}) | {row['upper_shadow']:.2f} | {row['lower_shadow']:.2f} "
            f"| {vr_label} | {row['vp_harmony']} |"
        )

    # ── 关键模式识别 ──
    lines.append("\n### 关键量价模式识别\n")

    # 量价背离检测（近5天）
    last5 = recent.tail(5)
    price_up = (last5["close"].iloc[-1] > last5["close"].iloc[0])
    vol_down = (last5["volume"].iloc[-1] < last5["volume"].iloc[0])
    price_down = (last5["close"].iloc[-1] < last5["close"].iloc[0])
    vol_up = (last5["volume"].iloc[-1] > last5["volume"].iloc[0])

    if price_up and vol_down:
        lines.append("- **⚠ 顶部背离信号**: 近5日价格上涨但成交量递减，上涨动能可能衰竭")
    if price_down and vol_up:
        lines.append("- **⚠ 底部放量信号**: 近5日价格下跌但成交量递增，可能是恐慌抛售或换手")
    if price_down and vol_down:
        lines.append("- **卖压衰竭信号**: 近5日价格下跌且成交量递减，空方力量可能枯竭")
    if price_up and vol_up:
        lines.append("- **健康上涨信号**: 近5日价格上涨且成交量配合递增")

    # Selling climax 检测
    for i in range(-3, 0):
        if i < -len(recent):
            continue
        row = recent.iloc[i]
        if (row.get("volume_ratio", 0) > 2.0
                and row.get("pct_change", 0) < -0.03
                and row.get("close_position", 0.5) > 0.5):
            lines.append(f"- **卖出高潮(Selling Climax)**: {str(row.get('date', ''))[-5:]} 急跌巨量但收盘收回过半，可能是恐慌见底")

    # 高位放量滞涨
    for i in range(-3, 0):
        if i < -len(recent):
            continue
        row = recent.iloc[i]
        if (row.get("volume_ratio", 0) > 1.8
                and abs(row.get("pct_change", 0)) < 0.01
                and row.get("bar_spread", 0) < 0.015):
            lines.append(f"- **放量滞涨**: {str(row.get('date', ''))[-5:]} 巨量但价格几乎不动（窄实体），多空分歧大")

    if not any("**" in l for l in lines[-5:]):
        lines.append("- 近期无显著量价异常模式")

    return "\n".join(lines)


def make_cache_key(ticker: str, trade_date: str) -> str:
    return f"{ticker}_{trade_date}"


def _should_fetch_realtime_quote(
    trade_date: str,
    *,
    symbol: str = "",
    now: Optional[datetime] = None,
) -> bool:
    """Only attach a quote for the instrument's current market-local date."""
    return trade_date == market_today_str(symbol, now=now)


def _safe(tool, payload: dict) -> Any:
    start_t = time.time()
    try:
        res = tool.invoke(payload)
        duration = time.time() - start_t
        # 仅在耗时较长时输出
        if duration > 0.5:
            print(f"  [Timer] {getattr(tool, 'name', str(tool))} took {duration:.2f}s")
        return res
    except Exception as exc:
        print(f"  [Error] {getattr(tool, 'name', str(tool))} failed: {exc}")
        return f"数据获取失败：{type(exc).__name__}: {exc}"


def _safe_with_vendor(tool, payload: dict) -> tuple[Any, str]:
    """Invoke one tool and return the vendor captured in the same worker."""
    method = getattr(tool, "name", str(tool))
    clear_current_thread_hit_vendor(method)
    result = _safe(tool, payload)
    return result, get_current_thread_hit_vendor(method)


_TUSHARE_ENDPOINTS_RE = re.compile(r"\bendpoints?=([A-Za-z0-9_,]+)")


def _extract_tushare_endpoints(raw_value: Any) -> str:
    """Extract exact endpoint provenance embedded by the Tushare provider."""
    if not isinstance(raw_value, str):
        return ""
    endpoints: list[str] = []
    for match in _TUSHARE_ENDPOINTS_RE.finditer(raw_value):
        for endpoint in match.group(1).split(","):
            endpoint = endpoint.strip()
            if endpoint and endpoint not in endpoints:
                endpoints.append(endpoint)
    return " + ".join(endpoints)


def _detect_fund_flow_anomaly(fund_flow_text: str) -> bool:
    """Detect if individual fund flow shows significant capital anomaly.

    Triggers when main capital net flow exceeds threshold in recent days,
    indicating potential need for LHB (龙虎榜) forced query.

    Returns False when fund flow data is unavailable (failure/empty),
    but callers MUST NOT use this as the sole gate for LHB force query.
    """
    import re
    if not fund_flow_text or "获取失败" in fund_flow_text or "不可用" in fund_flow_text:
        return False
    anomaly_threshold = 50000
    fund_keywords = ("主力", "净流入", "净流出", "超大", "大单")
    for line in fund_flow_text.split("\n"):
        if not any(kw in line for kw in fund_keywords):
            continue
        numbers = re.findall(r'[-–]?[\d,]+\.?\d*', line)
        for num_str in numbers:
            try:
                val = float(num_str.replace(',', '').replace('–', '-'))
                if abs(val) >= anomaly_threshold:
                    return True
            except ValueError:
                continue
    return False


def _should_force_lhb(news_text: str, stock_data_text: str) -> bool:
    """Check if LHB should be force-queried based on anomaly conditions.

    # [DATA-P0-603629] astock_source_fallback: force LHB query when:
    - News mentions 龙虎榜, 严重异常波动, 连续涨跌停
    - Stock data shows limit-up/down patterns

    # [DATA-P1-LHB-FUND-DECOUPLE] This is INDEPENDENT of fund flow status.
    Fund flow failure does NOT prevent this check from running.
    """
    import re

    force_keywords = [
        r"龙虎榜",
        r"严重?异常波动",
        r"连续\s*[\d一二三四五六七八九十]+\s*(?:涨停|跌停)",
        r"一字(?:涨停|跌停)",
        r"涨跌幅偏离",
        r"换手率\s*超过\s*\d+",
        r"成交额?\s*(?:超|破|逾)\s*\d+",
        r"量比\s*超过?\s*\d+",
    ]
    combined = f"{news_text}\n{stock_data_text or ''}"
    for pattern in force_keywords:
        if re.search(pattern, combined):
            return True
    return False


def _compute_lhb_force_decision(
    fund_flow_text: str,
    news_text: str,
    stock_data_text: str,
    announcements_text: str = "",
) -> tuple:
    """[DATA-P1-LHB-FUND-DECOUPLE] Compute LHB force decision.

    Decouples LHB force from fund flow: fund flow failure only affects
    the fund_flow_anomaly condition, NOT the anomaly_condition check.

    Returns (force_needed: bool, force_reason: str).
    """
    ff_anomaly = _detect_fund_flow_anomaly(fund_flow_text)
    anomaly_cond = _should_force_lhb(news_text, stock_data_text)

    if ff_anomaly and anomaly_cond:
        return (True, "fund_flow_anomaly_and_anomaly_condition")
    if ff_anomaly:
        return (True, "fund_flow_anomaly")
    if anomaly_cond:
        return (True, "anomaly_condition")

    if announcements_text:
        import re
        if re.search(r"龙虎榜", announcements_text):
            return (True, "announcement_mentions_lhb")

    return (False, "")


def _run_bounded_fetch(
    tasks: Dict[str, tuple],
    deadline: Optional[float] = None,
) -> tuple[Dict[str, Any], Dict[str, str]]:
    """[UPSTREAM-081-001] Run every fetch task in parallel under a whole-round
    hard cap (``FETCH_ALL_TIMEOUT``).

    Tasks that finish within the cap return their results normally. Timed-out
    sources get an explicit ``数据获取失败`` message so ``_infer_source_status``
    classifies them as FAILED instead of hanging the analysis.

    Workers run on the process-wide bounded pool (``_BoundedFetchPool``), not
    a per-round executor: a stuck provider keeps its thread until the
    socket-bounded call gives up, but the leak is capped at
    ``FETCH_POOL_MAX_WORKERS`` threads for the whole process instead of
    growing by one thread per abandoned worker per round. Callers pass a
    ``time.monotonic()`` deadline so follow-up work (the LHB force upgrade)
    consumes the same budget. Backlog/leak gauges: ``get_fetch_pool_stats()``.

    [UPSTREAM-081-001 fix round3] Deadline semantics are enforced, not just
    waited: when the round expires, queued (not yet started) futures are
    ``cancel()``-ed so they can never execute — not even once older workers
    free up. Submits into an already saturated pool raise
    ``_FetchPoolSaturated`` and degrade to explicit FAILED status text
    instead of silently joining an unbounded backlog.
    """
    results: Dict[str, Any] = {}
    provider_hits: Dict[str, str] = {}
    if deadline is None:
        deadline = time.monotonic() + FETCH_ALL_TIMEOUT
    pool = get_fetch_pool()
    future_to_key: Dict["Future", str] = {}
    for key, (tool, payload) in tasks.items():
        try:
            future = pool.submit(
                lambda tool=tool, payload=payload: _safe_with_vendor(tool, payload)
            )
        except _FetchPoolSaturated:
            # Explicit degraded status (``_infer_source_status`` → FAILED):
            # never a silent enqueue that could run after the deadline.
            results[key] = (
                f"数据获取失败：{key} 数据拉取被拒（线程池饱和），"
                "本轮分析跳过该数据源"
            )
            print(f"  [Warning] {key} fetch rejected: fetch pool saturated")
            continue
        future_to_key[future] = key
    done, not_done = futures_wait(
        set(future_to_key), timeout=max(0.0, deadline - time.monotonic())
    )
    for future in done:
        key = future_to_key[future]
        result, vendor = future.result()
        results[key] = result
        if vendor:
            provider_hits[key] = vendor
    if not_done:
        # [UPSTREAM-081-001 fix round3] Queued-but-not-started futures are
        # cancelled: after the whole-round deadline they must never execute.
        # Already-running futures cannot be cancelled; they stay bounded by
        # the shared pool's worker cap + the socket default timeout
        # (round2 semantics).
        cancelled = 0
        running = 0
        for future in not_done:
            try:
                was_queued = future.cancel()
            except Exception:
                was_queued = False
            if was_queued:
                cancelled += 1
            else:
                running += 1
        if cancelled:
            pool.mark_cancelled(cancelled)
        if running:
            # Abandoned workers die once their socket-bounded network call
            # gives up; never join them here or the hard cap would be
            # meaningless. The shared pool keeps the leak bounded and
            # observable.
            pool.mark_abandoned(running)
    for future in not_done:
        key = future_to_key[future]
        results[key] = (
            f"数据获取失败：{key} 数据拉取超时（>{FETCH_ALL_TIMEOUT:g}s），"
            "本轮分析跳过该数据源"
        )
        print(
            f"  [Warning] {key} fetch timed out after "
            f"{FETCH_ALL_TIMEOUT:g}s, skipped"
        )
    return results, provider_hits


def _apply_forced_lhb(
    results: Dict[str, Any],
    provider_hits: Dict[str, str],
    ticker: str,
    trade_date: str,
    deadline: float,
    force_reason: str,
) -> None:
    """[E-003] Upgrade LHB to force=True strictly inside the whole-round budget.

    [UPSTREAM-081-001] The forced query used to run after ``_run_bounded_fetch``
    with no deadline at all: a stuck provider could hold a worker thread
    indefinitely past the round cap. It now consumes whatever budget remains
    of the same deadline the round started with:

    - budget exhausted → never submitted, explicit degraded status text
      (``_lhb_query_mode="forced_skipped_budget"``);
    - submitted but the remaining wait expires → the future is cancelled
      (fix round3): a queued (not yet started) force query is guaranteed
      never to execute, so ``forced_timeout`` really ends all force activity
      for the round; a query already running on a worker cannot be cancelled
      and stays bounded by the shared pool cap + socket default timeout
      (round2 semantics). Both cases write explicit degraded status text
      (``_lhb_query_mode="forced_timeout"``; ``_infer_source_status`` →
      FAILED, never a fake success);
    - pool saturated → never enqueued, explicit degraded status text
      (``_lhb_query_mode="forced_rejected_saturated"``, fix round3).
    """
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        results["lhb"] = (
            "数据获取失败：lhb 强制查询因整轮预算已用尽未执行"
            f"（>{FETCH_ALL_TIMEOUT:g}s），本轮分析跳过该数据源"
        )
        results["_lhb_query_mode"] = "forced_skipped_budget"  # [G-007] provenance
        results["_lhb_force_reason"] = force_reason  # [DATA-P0-603629]
        print("  [E-003] lhb force query skipped: whole-round budget exhausted")
        return
    pool = get_fetch_pool()
    try:
        future = pool.submit(
            lambda: _safe_with_vendor(
                get_lhb_detail, {"symbol": ticker, "date": trade_date, "force": True}
            )
        )
    except _FetchPoolSaturated:
        # [fix round3] Never silently enqueue work that would escape the
        # whole-round deadline; degrade explicitly instead.
        results["lhb"] = (
            "数据获取失败：lhb 强制查询被拒（线程池饱和），本轮分析跳过该数据源"
        )
        results["_lhb_query_mode"] = "forced_rejected_saturated"  # [G-007]
        results["_lhb_force_reason"] = force_reason  # [DATA-P0-603629]
        print("  [E-003] lhb force query rejected: fetch pool saturated")
        return
    _done, not_done = futures_wait({future}, timeout=remaining)
    if not_done:
        # [UPSTREAM-081-001 fix round3] Cancel the future: a queued (not yet
        # started) force query must never execute once forced_timeout is
        # returned — previously it stayed in the queue and ran later,
        # escaping the whole-round deadline. Only a query already running on
        # a worker is abandoned (bounded by pool cap + socket timeout).
        try:
            was_queued = future.cancel()
        except Exception:
            was_queued = False
        if was_queued:
            pool.mark_cancelled(1)
        else:
            pool.mark_abandoned(1)
        results["lhb"] = (
            f"数据获取失败：lhb 强制查询超时（>{remaining:.0f}s），"
            "本轮分析跳过该数据源"
        )
        results["_lhb_query_mode"] = "forced_timeout"  # [G-007] provenance
        results["_lhb_force_reason"] = force_reason  # [DATA-P0-603629]
        print(
            f"  [E-003] lhb force query timed out after {remaining:.0f}s, skipped"
        )
        return
    lhb_forced, lhb_vendor = future.result()
    results["lhb"] = lhb_forced
    if lhb_vendor:
        provider_hits["lhb"] = lhb_vendor
    results["_lhb_query_mode"] = "forced"  # [G-007] fund_lhb_provenance
    results["_lhb_force_reason"] = force_reason  # [DATA-P0-603629]


def _fetch_all(ticker: str, trade_date: str) -> Dict[str, Any]:
    """Fetch all data sources in parallel.

    Always fetches full data including financial statements, regardless of horizon.
    The horizon only affects the analysis window, not data collection.
    """
    lookback = LONG_DAYS
    end_dt = datetime.strptime(trade_date, "%Y-%m-%d")
    # 为了计算指标准确（如 200 SMA），需要比分析窗口更长的历史数据
    fetch_lookback = 365
    start_str = (end_dt - timedelta(days=fetch_lookback)).strftime("%Y-%m-%d")

    tasks: Dict[str, tuple] = {
        "stock_data": (get_stock_data, {"symbol": ticker, "start_date": start_str, "end_date": trade_date}),
        "news": (get_news, {"ticker": ticker, "start_date": (end_dt - timedelta(days=lookback)).strftime("%Y-%m-%d"), "end_date": trade_date}),
        "global_news": (get_global_news, {"curr_date": trade_date, "look_back_days": lookback, "limit": 30}),
        "fund_flow_board": (get_board_fund_flow, {}),
        "fund_flow_individual": (get_individual_fund_flow, {"symbol": ticker}),
        "lhb": (get_lhb_detail, {"symbol": ticker, "date": trade_date, "force": False}),
        "insider_transactions": (
            get_insider_transactions,
            {"ticker": ticker, "curr_date": trade_date},
        ),
        "zt_pool": (get_zt_pool, {"date": trade_date}),
        "hot_stocks": (get_hot_stocks_xq, {}),
        "announcements": (get_announcements, {"symbol": ticker}),  # [DATA-P0-603629] astock_source_fallback
        "margin_trading": (get_margin_trading, {"symbol": ticker}),  # [DATA-010] margin_trading_raw_evidence
        "ratings": (get_ratings, {"symbol": ticker}),  # [DATA-012A] rating_data_collector_wiring
        "research_report": (get_research_report, {"symbol": ticker}),
        "buybacks": (get_buybacks, {"symbol": ticker}),
    }
    is_current_trade_date = _should_fetch_realtime_quote(trade_date, symbol=ticker)
    if is_current_trade_date:
        tasks["realtime_quote"] = (get_realtime_quotes, {"symbols": [ticker]})

    # [HK-001] hk_market_boundary: 港股只走 yfinance 轻量行情/新闻/财报，
    # 禁用 A 股特有的资金流/龙虎榜/涨停池/融资融券/评级/公告/热门股门禁，
    # 避免港股请求被 A 股数据缺失误判为强动作阻断。
    instrument_ctx = infer_instrument_context(ticker)
    is_hk_market = instrument_ctx.get("market_country") == "HK"
    hk_skipped_keys: tuple[str, ...] = ()
    if is_hk_market:
        hk_skipped_keys = (
            "realtime_quote",
            "fund_flow_board",
            "fund_flow_individual",
            "lhb",
            "zt_pool",
            "hot_stocks",
            "announcements",
            "margin_trading",
            "ratings",
            "research_report",
            "buybacks",
        )
        for skip_key in hk_skipped_keys:
            tasks.pop(skip_key, None)

    # 财务报表类数据始终拉取，Research Manager 根据 horizon 自行判断权重
    tasks.update({
        "fundamentals": (get_fundamentals, {"ticker": ticker, "curr_date": trade_date}),
        "balance_sheet": (get_balance_sheet, {"ticker": ticker, "freq": "quarterly", "curr_date": trade_date}),
        "cashflow": (get_cashflow, {"ticker": ticker, "freq": "quarterly", "curr_date": trade_date}),
        "income_statement": (get_income_statement, {"ticker": ticker, "freq": "quarterly", "curr_date": trade_date}),
    })

    results: Dict[str, Any] = {}
    provider_hits: Dict[str, str] = {}
    fetch_start = time.time()
    stock_data_fetch_started_at = datetime.now(timezone.utc)
    # [UPSTREAM-081-001] bounded fetch round: one deadline covers both the
    # parallel round and the conditional LHB force upgrade below, so no
    # provider call can outlive the whole-round budget.
    round_deadline = time.monotonic() + FETCH_ALL_TIMEOUT
    results, provider_hits = _run_bounded_fetch(tasks, deadline=round_deadline)
    results["_provider_hits"] = provider_hits

    # [HK-001] hk_market_boundary: 为被跳过的 A 股专属字段写入显式 NOT_AVAILABLE
    # 结构化条目，让 raw_evidence / readiness 识别为"已跳过（低危）"而非"未查询/缺失"，
    # 避免港股触发 A 股门禁的误导性结论。
    if is_hk_market:
        for skip_key in hk_skipped_keys:
            results[skip_key] = {
                "status": "NOT_AVAILABLE",
                "raw": "",
                "vendor": "skipped",
                "reason": "HK market not supported for A-share-only data source",
                "field": skip_key,
                "as_of": trade_date,
            }
        results["_hk_light_mode"] = True  # [HK-001] hk_market_boundary
    elif not is_current_trade_date:
        results["realtime_quote"] = {
            "status": "SKIPPED",
            "raw": "",
            "vendor": "skipped",
            "reason": "historical analysis does not use current realtime quotes",
            "field": "realtime_quote",
            "as_of": trade_date,
        }

    # Apply one provider-independent finality contract. Some fallback vendors
    # include today's forming daily row even while the market is open.
    results["stock_data"] = _exclude_forming_daily_bar(
        results.get("stock_data"),
        ticker,
        now=stock_data_fetch_started_at,
    )

    # ── [E-003] 资金流异动时自动升级 LHB 查询 ─────────────────────────
    # [DATA-P0-603629] astock_source_fallback: LHB force conditions expanded
    # [DATA-P1-LHB-FUND-DECOUPLE] 资金流失败不阻断异常条件 force 检查
    # [HK-001] hk_market_boundary: 港股不触发 A 股龙虎榜强制查询链路。
    results["_lhb_query_mode"] = "on_demand"  # [G-007] default: force=False
    ff_text = results.get("fund_flow_individual", "") or ""
    news_text = results.get("news", "") or ""

    if not is_hk_market:
        lhb_force_needed, lhb_force_reason = _compute_lhb_force_decision(
            ff_text, news_text, results.get("stock_data", ""),
            results.get("announcements", ""),
        )

        if lhb_force_needed:
            print(f"  [E-003] 龙虎榜强制查询触发 (reason={lhb_force_reason})，升级 LHB force=True")
            _apply_forced_lhb(
                results, provider_hits, ticker, trade_date,
                deadline=round_deadline, force_reason=lhb_force_reason,
            )
    else:
        results["_lhb_query_mode"] = "skipped_hk"  # [HK-001] hk_market_boundary

    # ── Parse CSV once, reuse for indicators and VPA ──────────────────
    raw_csv = results.get("stock_data", "")
    df = _parse_csv_to_dataframe(raw_csv)

    # ── 核心加速：本地计算所有技术指标 ──────────────────
    indicators_res = {}
    try:
        if df is not None and "close" in df.columns:
            ss = wrap(df.copy())

            calc_map = {
                "close_50_sma": "close_50_sma",
                "close_200_sma": "close_200_sma",
                "close_10_ema": "close_10_ema",
                "rsi": "rsi_14",
                "macd": "macd",
                "boll": "close_20_sma",
                "boll_ub": "boll_ub",
                "boll_lb": "boll_lb",
                "atr": "atr",
                "vwma": "vwma"
            }

            for key, ss_key in calc_map.items():
                try:
                    val = ss[ss_key].iloc[-1]
                    indicators_res[key] = round(float(val), 2) if isinstance(val, (int, float)) else str(val)
                except Exception:
                    indicators_res[key] = "N/A"
        else:
            print(f"  [Warning] No valid stock_data for indicator calculation.")
    except Exception as e:
        print(f"  [Error] Local indicator calculation failed: {e}")

    for ind in INDICATORS:
        if ind not in indicators_res:
            indicators_res[ind] = "无数据"

    results["indicators"] = indicators_res

    # ── VPA 预计算指标 ──────────────────────────────
    try:
        if df is not None:
            results["vpa_indicators"] = _compute_vpa_indicators(df.copy())
        else:
            results["vpa_indicators"] = "VPA 数据不足"
    except Exception as e:
        results["vpa_indicators"] = f"VPA 计算失败：{e}"

    # [FUND-001] instrument_identity_gate — the profile must be explicit.
    # A financial abstract alone is not a company profile and is never treated
    # as sufficient evidence for business/industry analysis.
    profile = extract_profile_from_fundamentals(results.get("fundamentals"))
    fundamentals_vendor = provider_hits.get("fundamentals") or "provider"
    identity = build_instrument_identity(
        ticker,
        profile,
        source=(
            f"{fundamentals_vendor}_company_profile"
            if profile
            else "provider_profile_missing"
        ),
        as_of=trade_date,
    )
    results["instrument_identity"] = identity.to_dict()
    results["company_profile"] = {
        "status": identity.status,
        "raw": profile,
        "vendor": fundamentals_vendor if profile else "provider",
        "field": "company_profile",
        "endpoint": "stock_basic + stock_company" if fundamentals_vendor == "cn_tushare" else "",
        "as_of": trade_date,
        "missing_fields": list(identity.missing_fields),
        "reason": identity.conflict_reason,
    }

    # [FUND-002] financial_period_normalizer — preserve raw provider tables
    # and add a replayable sidecar with only deterministic period semantics.
    normalized_facts = []
    for key, statement_type in (
        ("income_statement", "income_statement"),
        ("cashflow", "cashflow"),
        ("balance_sheet", "balance_sheet"),
    ):
        normalized_facts.extend(
            normalize_financial_markdown(
                results.get(key), statement_type=statement_type, source=f"raw:{key}"
            )
        )
    normalized_facts.extend(derive_single_quarters(normalized_facts))
    results["financial_period_facts"] = [fact.to_dict() for fact in normalized_facts]
    from tradingagents.agents.utils.fundamental_integrity import build_official_explanation_context
    _explanation = build_official_explanation_context(
        announcements=results.get("announcements"), half_year_facts=None
    )
    _explanation["as_of"] = trade_date
    results["fundamental_explanations"] = _explanation

    print(f"[Timer] Total Data Collection for {ticker} took {time.time() - fetch_start:.2f}s")
    return results


class DataCollector:
    """Collect and cache data, thread-safe and shareable across jobs."""

    def __init__(self):
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._locks: Dict[str, threading.Lock] = {}
        self._meta_lock = threading.Lock()
        self._refcounts: Dict[str, int] = {}

    def _get_key_lock(self, key: str) -> threading.Lock:
        with self._meta_lock:
            if key not in self._locks:
                self._locks[key] = threading.Lock()
            return self._locks[key]

    def collect(self, ticker: str, trade_date: str, horizons: Optional[List[str]] = None) -> Dict[str, Any]:
        """Fetch all data and store in cache.

        Thread-safe: concurrent calls for the same ticker+date will block
        on a per-key lock, so data is fetched only once.
        """
        key = make_cache_key(ticker, trade_date)
        key_lock = self._get_key_lock(key)
        if not key_lock.acquire(timeout=FETCH_LOCK_TIMEOUT):
            raise TimeoutError(
                f"等待 {key} 数据抓取锁超时（>{FETCH_LOCK_TIMEOUT:g}s），"
                "可能存在卡死的数据源任务"
            )
        try:
            if key not in self._cache:
                self._cache[key] = _fetch_all(ticker, trade_date)
            return self._cache[key]
        finally:
            key_lock.release()

    def get(self, ticker: str, trade_date: str) -> Optional[Dict[str, Any]]:
        """Retrieve cached pool, or None if not collected yet."""
        return self._cache.get(make_cache_key(ticker, trade_date))

    def get_window(
        self,
        pool: Dict[str, Any],
        horizon: str,
        trade_date: str,
    ) -> Dict[str, Any]:
        """Return pool copy annotated with horizon window metadata."""
        days = SHORT_DAYS if horizon == "short" else LONG_DAYS
        result = dict(pool)
        result["_data_window"] = f"{days}天"
        result["_horizon"] = horizon
        return result

    def ref(self, ticker: str, trade_date: str) -> None:
        """Increment reference count (call before using cached data)."""
        key = make_cache_key(ticker, trade_date)
        with self._meta_lock:
            self._refcounts[key] = self._refcounts.get(key, 0) + 1

    def evict(self, ticker: str, trade_date: str) -> None:
        """Decrement refcount and remove cached data when no one needs it."""
        key = make_cache_key(ticker, trade_date)
        with self._meta_lock:
            count = self._refcounts.get(key, 1) - 1
            if count <= 0:
                self._cache.pop(key, None)
                self._refcounts.pop(key, None)
            else:
                self._refcounts[key] = count

    # [G-006] raw_evidence_snapshot
    @staticmethod
    def _infer_source_status(raw_value: Any) -> str:
        # [HK-001] hk_market_boundary: honour explicit structured status written
        # by the collector itself (e.g. A-share-only fields skipped for HK).
        if isinstance(raw_value, dict) and "status" in raw_value:
            explicit = str(raw_value.get("status") or "").upper()
            if explicit in {
                "NOT_AVAILABLE", "SKIPPED", "NOT_QUERIED",
                "NORMAL_NO_DATA", "HAS_DATA", "FAILED",
            }:
                return explicit
        if raw_value is None:
            return "NOT_QUERIED"
        if isinstance(raw_value, str):
            val = raw_value.strip()
            if not val:
                return "NOT_QUERIED"
            if val.startswith(("{", "[")):
                try:
                    structured = json.loads(val)
                except (TypeError, ValueError):
                    structured = None
                if structured == {} or structured == []:
                    return "NORMAL_NO_DATA"
            if "RATINGS_NORMAL_NO_DATA" in val or "无分析师评级" in val:
                return "NORMAL_NO_DATA"
            if "RATINGS_FAILED" in val:
                return "FAILED"
            if "RATINGS_HAS_DATA" in val:
                return "HAS_DATA"
            if "REPORT_NORMAL_NO_DATA" in val:
                return "NORMAL_NO_DATA"
            if "REPORT_FAILED" in val:
                return "FAILED"
            if "REPORT_HAS_DATA" in val:
                return "HAS_DATA"
            if "BUYBACK_NORMAL_NO_DATA" in val:
                return "NORMAL_NO_DATA"
            if "BUYBACK_FAILED" in val:
                return "FAILED"
            if "BUYBACK_HAS_DATA" in val:
                return "HAS_DATA"
            if "FUND_FLOW_AGGREGATE_HAS_DATA" in val:
                return "HAS_DATA"
            if "FUND_FLOW_NORMAL_NO_DATA" in val:
                return "NORMAL_NO_DATA"
            if "FUND_FLOW_FAILED" in val:
                return "FAILED"
            if "MARGIN_NORMAL_NO_DATA" in val:
                return "NORMAL_NO_DATA"
            if "MARGIN_FAILED" in val:
                return "FAILED"
            if "MARGIN_HAS_DATA" in val:
                return "HAS_DATA"
            if "[G-007] LHB_NOT_QUERIED" in val or "查询未触发" in val:
                return "NOT_QUERIED"
            if "[G-007] LHB_NORMAL_NO_DATA" in val or "无龙虎榜数据" in val:
                return "NORMAL_NO_DATA"
            if "[G-007] LHB_FAILED" in val:
                return "FAILED"
            if "[G-007] LHB_HAS_DATA" in val or "龙虎榜明细" in val:
                return "HAS_DATA"
            if "No announcements found" in val or "未查询到公告" in val:
                return "NORMAL_NO_DATA"
            if "获取失败" in val or "不可用" in val or "error" in val.lower():
                return "FAILED"
            if val.startswith("N/A") or val == "VPA 数据不足" or val == "VPA 计算失败":
                return "NORMAL_NO_DATA"
            return "HAS_DATA"
        if isinstance(raw_value, dict):
            return "HAS_DATA" if raw_value else "NOT_QUERIED"
        if isinstance(raw_value, list):
            return "HAS_DATA" if raw_value else "NORMAL_NO_DATA"
        return "HAS_DATA"

    @staticmethod
    def _count_records(raw_value: Any) -> int:
        if raw_value is None:
            return 0
        if isinstance(raw_value, str):
            lines = raw_value.strip().split("\n")
            return max(0, len(lines) - 1) if len(lines) > 1 else 0
        if isinstance(raw_value, (list, dict)):
            return len(raw_value)
        return 1

    def build_raw_evidence(self, ticker: str, trade_date: str) -> Dict[str, Any]:
        # [G-006] raw_evidence_snapshot
        # [DATA-004] raw_evidence_contract: upgraded with endpoint/fallback_from/source_url
        pool = self.get(ticker, trade_date)
        if not pool:
            return {}

        # Preserve the producer timezone. Docker commonly runs in UTC while
        # quote timestamps are Asia/Shanghai; a naive value makes a fresh quote
        # look eight hours stale at the consumer.
        now_iso = datetime.now().astimezone().isoformat(timespec="seconds")

        data_source_keys = [
            "stock_data", "realtime_quote", "news", "global_news",
            "fund_flow_board", "fund_flow_individual", "lhb",
            "fundamentals", "balance_sheet", "cashflow", "income_statement",
            "company_profile",  # [FUND-001] instrument_identity_gate
            "insider_transactions", "zt_pool", "hot_stocks",
            "indicators", "vpa_indicators",
            "announcements",  # [DATA-P0-603629] astock_source_fallback
            "margin_trading",  # [DATA-010] margin_trading_raw_evidence
            "research_report",  # [DATA-011] research_report_raw_evidence
            "ratings",  # [DATA-012A] rating_data_collector_wiring
            "buybacks",  # [DATA-013A] buyback_collector_wiring
        ]
        provider_hits = pool.get("_provider_hits") or {}

        method_for_key = {
            "stock_data": "get_stock_data",
            "realtime_quote": "get_realtime_quotes",
            "fund_flow_board": "get_board_fund_flow",
            "fund_flow_individual": "get_individual_fund_flow",
            "lhb": "get_lhb_detail",
            "announcements": "get_announcements",
            "fundamentals": "get_fundamentals",
            "balance_sheet": "get_balance_sheet",
            "cashflow": "get_cashflow",
            "income_statement": "get_income_statement",
            "margin_trading": "get_margin_trading",
            "research_report": "get_research_report",
            "ratings": "get_ratings",
            "buybacks": "get_buybacks",
        }

        def _vendor_for(key: str) -> str:
            current_vendor = str(provider_hits.get(key) or "").strip()
            if current_vendor:
                return current_vendor
            method = method_for_key.get(key)
            return get_last_hit_vendor(method) if method else ""

        raw_evidence: Dict[str, Any] = {}
        for key in data_source_keys:
            raw_value = pool.get(key)
            provider_metadata = raw_value if isinstance(raw_value, dict) else {}
            if key == "company_profile" and isinstance(raw_value, dict):
                raw_value = raw_value.get("raw")
            status = self._infer_source_status(raw_value)
            if key == "company_profile":
                identity = pool.get("instrument_identity") or {}
                status = str(identity.get("status") or status)

            non_provider_status = status in {
                "NOT_QUERIED",
                "SKIPPED",
                "NOT_AVAILABLE",
            }
            has_current_provider_result = status in {
                "HAS_DATA",
                "NORMAL_NO_DATA",
            }
            entry: Dict[str, Any] = {
                "status": status,
                "vendor": "" if status == "FAILED" else "akshare",
                "endpoint": "",  # [DATA-004] raw_evidence_contract
                "fallback_from": None,  # [DATA-004] raw_evidence_contract
                "source_url": None,  # [DATA-004] raw_evidence_contract
                "field": key,  # [DATA-004] raw_evidence_contract
                "as_of": trade_date,
                "fetched_at": now_iso,
                "record_count": self._count_records(raw_value),
                "unit": None,
                "error": None,
                "is_realtime_patched": False,
                "current_day_status": None,
            }
            if non_provider_status and isinstance(raw_value, dict):
                entry["vendor"] = str(raw_value.get("vendor") or "skipped")
                entry["as_of"] = str(raw_value.get("as_of") or trade_date)
                entry["error"] = raw_value.get("reason") or raw_value.get("error")

            data_type = _resolve_data_type_for_key(key)  # [DATA-004]

            if key == "stock_data" and isinstance(raw_value, str):
                partial_without_completed_bar = (
                    "[INTRADAY-DAYBAR]" in raw_value
                    and "action=excluded_from_daily_ohlcv" in raw_value
                    and "No data found for symbol" in raw_value
                )
                if partial_without_completed_bar:
                    entry["status"] = "NORMAL_NO_DATA"
                    entry["record_count"] = 0
                actual_vendor = _vendor_for(key) if has_current_provider_result else None
                if actual_vendor:
                    entry["vendor"] = actual_vendor
                if "is_realtime_patched=True" in raw_value:
                    entry["is_realtime_patched"] = True
                    for line in raw_value.split("\n"):
                        if "source=" in line and "[G-005]" in line:
                            parts = line.split("source=")
                            if len(parts) > 1:
                                entry["vendor"] = parts[1].split(",")[0].strip()
                        if "quote_time=" in line and "[G-005]" in line:
                            parts = line.split("quote_time=")
                            if len(parts) > 1:
                                entry["as_of"] = parts[1].strip()
                if "[INTRADAY-DAYBAR]" in raw_value:
                    for line in raw_value.split("\n"):
                        if (
                            "[INTRADAY-DAYBAR]" in line
                            and "current_day_status=" in line
                        ):
                            entry["current_day_status"] = (
                                line.split("current_day_status=", 1)[1]
                                .split(",", 1)[0]
                                .strip()
                            )
                if "adjustment=" in raw_value:  # [DATA-P0-603629] astock_source_fallback
                    for line in raw_value.split("\n"):
                        if "adjustment=" in line and "[DATA-P0-603629]" in line:
                            parts = line.split("adjustment=")
                            if len(parts) > 1:
                                entry["adjustment"] = parts[1].strip().split("\n")[0].strip()

            if key == "fund_flow_individual":
                is_aggregate_fallback = (
                    isinstance(raw_value, str)
                    and "FUND_FLOW_AGGREGATE_HAS_DATA" in raw_value
                )
                raw_text = raw_value if isinstance(raw_value, str) else ""
                if is_aggregate_fallback:
                    entry["unit"] = "接口原始金额（万元/亿元文本）"
                    entry["unit_verified"] = False
                elif any(
                    marker in raw_text
                    for marker in ("单位：万元", "单位:万元", "（万元）", "(万元)")
                ):
                    entry["unit"] = "万元"
                    entry["unit_verified"] = True
                elif any(
                    marker in raw_text
                    for marker in ("单位：元", "单位:元", "（元）", "(元)")
                ):
                    entry["unit"] = "元"
                    entry["unit_verified"] = True
                else:
                    entry["unit"] = None
                    entry["unit_verified"] = False
                entry["source_type"] = "individual_fund_flow"
                if is_aggregate_fallback:
                    entry["granularity"] = "aggregate_current_and_5d"
                actual_vendor = _vendor_for(key) if has_current_provider_result else None
                if actual_vendor:
                    entry["vendor"] = actual_vendor
            elif key == "fund_flow_board":
                entry["source_type"] = "board_fund_flow"
                actual_vendor = _vendor_for(key) if has_current_provider_result else None
                if actual_vendor:
                    entry["vendor"] = actual_vendor
            elif key == "lhb":
                entry["query_mode"] = pool.get("_lhb_query_mode", "on_demand")
                entry["force_reason"] = pool.get("_lhb_force_reason")  # [DATA-P0-603629]
                actual_vendor = _vendor_for(key) if has_current_provider_result else None
                if actual_vendor:
                    entry["vendor"] = actual_vendor
            elif key in ("stock_data",):
                entry["unit"] = "股"
            elif key == "realtime_quote":
                actual_vendor = _vendor_for(key) if has_current_provider_result else None
                if actual_vendor:
                    entry["vendor"] = actual_vendor
                entry["unit"] = "结构化行情"
            elif key == "announcements":  # [DATA-P0-603629] astock_source_fallback
                actual_vendor = _vendor_for(key) if has_current_provider_result else None
                if actual_vendor:
                    entry["vendor"] = actual_vendor
            elif key in ("fundamentals", "balance_sheet", "cashflow", "income_statement"):
                actual_vendor = _vendor_for(key) if has_current_provider_result else None
                if actual_vendor:
                    entry["vendor"] = actual_vendor
            elif key == "company_profile":
                profile_vendor = str(provider_metadata.get("vendor") or "").strip()
                if profile_vendor:
                    entry["vendor"] = profile_vendor
            elif key == "margin_trading":  # [DATA-010] margin_trading_raw_evidence
                actual_vendor = _vendor_for(key) if has_current_provider_result else None
                if actual_vendor:
                    entry["vendor"] = actual_vendor
            elif key == "research_report":  # [DATA-011] research_report_raw_evidence
                actual_vendor = _vendor_for(key) if has_current_provider_result else None
                if actual_vendor:
                    entry["vendor"] = actual_vendor
                entry["unit"] = "条"
            elif key == "ratings":  # [DATA-012A] rating_data_collector_wiring
                actual_vendor = _vendor_for(key) if has_current_provider_result else None
                if actual_vendor:
                    entry["vendor"] = actual_vendor
                entry["unit"] = "条"
            elif key == "buybacks":  # [DATA-013A] buyback_collector_wiring
                actual_vendor = _vendor_for(key) if has_current_provider_result else None
                if actual_vendor:
                    entry["vendor"] = actual_vendor
                entry["unit"] = "接口原始金额"

            # [DATA-004] raw_evidence_contract: resolve endpoint and fallback info
            if has_current_provider_result:
                entry["endpoint"] = _resolve_endpoint_for_vendor(
                    entry["vendor"], data_type
                )
                entry["fallback_from"] = _resolve_fallback_for_vendor(
                    entry["vendor"], data_type
                )
                if entry["vendor"] == "cn_tushare":
                    exact_endpoints = _extract_tushare_endpoints(raw_value)
                    if exact_endpoints:
                        entry["endpoint"] = exact_endpoints
                    elif key == "company_profile":
                        entry["endpoint"] = str(
                            provider_metadata.get("endpoint") or entry["endpoint"]
                        )

            if status == "FAILED" and isinstance(raw_value, str):
                entry["error"] = raw_value[:200]

            raw_evidence[key] = {
                "raw": raw_value,
                "field": entry["field"],  # [DATA-004]
                "status": entry["status"],
                "vendor": entry["vendor"],
                "endpoint": entry["endpoint"],  # [DATA-004]
                "as_of": entry["as_of"],
                "fetched_at": entry["fetched_at"],
                "record_count": entry["record_count"],
                "unit": entry["unit"],
                "error": entry["error"],
                "fallback_from": entry["fallback_from"],  # [DATA-004]
                "source_url": entry["source_url"],  # [DATA-004]
                "is_realtime_patched": entry["is_realtime_patched"],
                "current_day_status": entry.get("current_day_status"),
                "source_type": entry.get("source_type"),
                "unit_verified": entry.get("unit_verified", None),
                "query_mode": entry.get("query_mode", None),
                "adjustment": entry.get("adjustment", None),  # [DATA-P0-603629] astock_source_fallback
                "force_reason": entry.get("force_reason", None),  # [DATA-P0-603629]
                "granularity": entry.get("granularity", None),
            }

        # [FUND-001/FUND-002] These are deterministic sidecars, not provider
        # substitutes.  Keeping them in raw_evidence makes the later risk gate
        # independent from a particular LangGraph node's local cache.
        identity = pool.get("instrument_identity") or {}
        raw_evidence["instrument_identity"] = {
            "raw": identity,
            "field": "instrument_identity",
            "status": str(identity.get("status") or "MISSING"),
            "vendor": identity.get("source", "provider"),
            "endpoint": "",
            "as_of": identity.get("as_of") or trade_date,
            "fetched_at": now_iso,
            "record_count": 1 if identity else 0,
            "unit": None,
            "error": identity.get("conflict_reason"),
            "fallback_from": None,
            "source_url": None,
            "is_realtime_patched": False,
        }
        facts = pool.get("financial_period_facts") or []
        raw_evidence["financial_period_facts"] = {
            "raw": facts,
            "field": "financial_period_facts",
            "status": "HAS_DATA" if facts else "NORMAL_NO_DATA",
            "vendor": "deterministic_normalizer",
            "endpoint": "",
            "as_of": trade_date,
            "fetched_at": now_iso,
            "record_count": len(facts),
            "unit": None,
            "error": None,
            "fallback_from": None,
            "source_url": None,
            "is_realtime_patched": False,
        }

        # [KB-003] local_knowledge_raw_evidence — wiki 不依赖 data collector pool，
        # 单独注入；失败不阻塞主链路。
        try:
            from tradingagents.dataflows.local_knowledge_provider import (
                build_raw_evidence_entry as _lk_build_entry,
                query_failed_entry as _lk_failed_entry,
                query_local_knowledge as _lk_query,
            )
            from tradingagents.dataflows.local_knowledge_audit import (
                default_knowledge_root as _lk_default_root,
            )

            kb_root = _lk_default_root()
            lk_result = _lk_query(kb_root, symbol=ticker)
            raw_evidence["local_knowledge"] = _lk_build_entry(
                lk_result, trade_date, now_iso
            )
        except Exception as exc:  # pragma: no cover - 容错：KB 不可用不阻塞主链路
            raw_evidence["local_knowledge"] = _lk_failed_entry(
                trade_date, now_iso, f"{type(exc).__name__}: {exc}"
            )

        # [HY-004] half_year_facts_raw_evidence — Tree Work 半年报事实表只读查询，
        # 与 local_knowledge 独立注入；失败不阻塞主链路，也不改动作语义。
        try:
            from tradingagents.dataflows.half_year_facts_provider import (
                build_half_year_facts_raw_evidence_entry as _hy_build_entry,
                query_failed_entry as _hy_failed_entry,
                query_half_year_facts as _hy_query,
            )
            from tradingagents.dataflows.local_knowledge_audit import (
                default_knowledge_root as _hy_default_root,
            )

            hy_root = _hy_default_root()
            hy_result = _hy_query(hy_root, symbol=ticker)
            raw_evidence["half_year_facts"] = _hy_build_entry(
                hy_result, trade_date, now_iso
            )
        except Exception as exc:  # pragma: no cover - 容错：半年报不可用不阻塞主链路
            raw_evidence["half_year_facts"] = _hy_failed_entry(
                trade_date, now_iso, f"{type(exc).__name__}: {exc}"
            )

        # [FUND-003] official_explanation_context — assembled only from
        # announcement / structured fact sources, never from an LLM opinion.
        from tradingagents.agents.utils.fundamental_integrity import build_official_explanation_context
        _explanation = build_official_explanation_context(
            announcements=raw_evidence.get("announcements", {}).get("raw"),
            half_year_facts=raw_evidence.get("half_year_facts", {}).get("raw"),
        )
        _explanation["as_of"] = trade_date
        raw_evidence["fundamental_explanations"] = {
            "raw": _explanation,
            "field": "fundamental_explanations",
            "status": _explanation.get("data_status", "NORMAL_NO_DATA"),
            "vendor": "deterministic_evidence_context",
            "endpoint": "",
            "as_of": trade_date,
            "fetched_at": now_iso,
            "record_count": len(_explanation.get("entries") or []),
            "unit": None,
            "error": None,
            "fallback_from": None,
            "source_url": None,
            "is_realtime_patched": False,
        }

        return raw_evidence
