"""DataCollector: fetch all data once, serve windowed views to analyst agents."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
import threading
import time
import pandas as pd
from stockstats import wrap
import io
import logging

from tradingagents.agents.utils.agent_utils import (
    get_stock_data,
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
)
from tradingagents.dataflows.interface import get_last_hit_vendor  # [N-003] cn_astock_raw_evidence
from tradingagents.dataflows.evidence_contract import (  # [DATA-004] raw_evidence_contract
    resolve_data_type as _resolve_data_type_contract,
    resolve_endpoint as _resolve_endpoint_contract,
    resolve_fallback_info as _resolve_fallback_info_contract,
)

INDICATORS = [
    "close_50_sma", "close_200_sma", "close_10_ema",
    "rsi", "macd", "boll", "boll_ub", "boll_lb", "atr", "vwma",
]
SHORT_DAYS = 14
LONG_DAYS = 90

import numpy as np

_OHLCV_COLS = ["date", "open", "high", "low", "close", "volume"]

_logger = logging.getLogger(__name__)


_EVIDENCE_KEY_TO_DATA_TYPE: Dict[str, str] = {
    "stock_data": "ohlcv",
    "news": "news",
    "global_news": "global_news",
    "fund_flow_board": "board_fund_flow",
    "fund_flow_individual": "fund_flow",
    "lhb": "lhb",
    "fundamentals": "financials",
    "balance_sheet": "financials",
    "cashflow": "financials",
    "income_statement": "financials",
    "insider_transactions": "insider",
    "zt_pool": "zt_pool",
    "hot_stocks": "hot_stocks",
    "indicators": "ohlcv",
    "vpa_indicators": "ohlcv",
    "announcements": "notice",
    "ratings": "rating",  # [DATA-012A] rating_data_collector_wiring
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
        "insider_transactions": (get_insider_transactions, {"ticker": ticker}),
        "zt_pool": (get_zt_pool, {"date": trade_date}),
        "hot_stocks": (get_hot_stocks_xq, {}),
        "announcements": (get_announcements, {"symbol": ticker}),  # [DATA-P0-603629] astock_source_fallback
        "margin_trading": (get_margin_trading, {"symbol": ticker}),  # [DATA-010] margin_trading_raw_evidence
        "ratings": (get_ratings, {"symbol": ticker}),  # [DATA-012A] rating_data_collector_wiring
    }

    # 财务报表类数据始终拉取，Research Manager 根据 horizon 自行判断权重
    tasks.update({
        "fundamentals": (get_fundamentals, {"ticker": ticker, "curr_date": trade_date}),
        "balance_sheet": (get_balance_sheet, {"ticker": ticker, "freq": "quarterly", "curr_date": trade_date}),
        "cashflow": (get_cashflow, {"ticker": ticker, "freq": "quarterly", "curr_date": trade_date}),
        "income_statement": (get_income_statement, {"ticker": ticker, "freq": "quarterly", "curr_date": trade_date}),
    })

    results: Dict[str, Any] = {}
    fetch_start = time.time()
    # 减少并发池大小，避免被反爬
    with ThreadPoolExecutor(max_workers=min(10, len(tasks))) as executor:
        future_to_key = {executor.submit(_safe, tool, payload): key for key, (tool, payload) in tasks.items()}
        for future in future_to_key:
            results[future_to_key[future]] = future.result()

    # ── [E-003] 资金流异动时自动升级 LHB 查询 ─────────────────────────
    # [DATA-P0-603629] astock_source_fallback: LHB force conditions expanded
    # [DATA-P1-LHB-FUND-DECOUPLE] 资金流失败不阻断异常条件 force 检查
    results["_lhb_query_mode"] = "on_demand"  # [G-007] default: force=False
    ff_text = results.get("fund_flow_individual", "") or ""
    news_text = results.get("news", "") or ""

    lhb_force_needed, lhb_force_reason = _compute_lhb_force_decision(
        ff_text, news_text, results.get("stock_data", ""),
        results.get("announcements", ""),
    )

    if lhb_force_needed:
        print(f"  [E-003] 龙虎榜强制查询触发 (reason={lhb_force_reason})，升级 LHB force=True")
        lhb_forced = _safe(get_lhb_detail, {"symbol": ticker, "date": trade_date, "force": True})
        results["lhb"] = lhb_forced
        results["_lhb_query_mode"] = "forced"  # [G-007] fund_lhb_provenance
        results["_lhb_force_reason"] = lhb_force_reason  # [DATA-P0-603629]

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
        with key_lock:
            if key not in self._cache:
                self._cache[key] = _fetch_all(ticker, trade_date)
        return self._cache[key]

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
        if raw_value is None:
            return "NOT_QUERIED"
        if isinstance(raw_value, str):
            val = raw_value.strip()
            if not val:
                return "NOT_QUERIED"
            if "RATINGS_NORMAL_NO_DATA" in val or "无分析师评级" in val:
                return "NORMAL_NO_DATA"
            if "RATINGS_FAILED" in val:
                return "FAILED"
            if "RATINGS_HAS_DATA" in val:
                return "HAS_DATA"
            if "[G-007] LHB_NOT_QUERIED" in val or "查询未触发" in val:
                return "NOT_QUERIED"
            if "[G-007] LHB_NORMAL_NO_DATA" in val or "无龙虎榜数据" in val:
                return "NORMAL_NO_DATA"
            if "[G-007] LHB_FAILED" in val:
                return "FAILED"
            if "[G-007] LHB_HAS_DATA" in val or "龙虎榜明细" in val:
                return "HAS_DATA"
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

        now_iso = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

        data_source_keys = [
            "stock_data", "news", "global_news",
            "fund_flow_board", "fund_flow_individual", "lhb",
            "fundamentals", "balance_sheet", "cashflow", "income_statement",
            "insider_transactions", "zt_pool", "hot_stocks",
            "indicators", "vpa_indicators",
            "announcements",  # [DATA-P0-603629] astock_source_fallback
            "margin_trading",  # [DATA-010] margin_trading_raw_evidence
            "research_report",  # [DATA-011] research_report_raw_evidence
            "ratings",  # [DATA-012A] rating_data_collector_wiring
        ]

        raw_evidence: Dict[str, Any] = {}
        for key in data_source_keys:
            raw_value = pool.get(key)
            status = self._infer_source_status(raw_value)

            entry: Dict[str, Any] = {
                "status": status,
                "vendor": "akshare",
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
            }

            data_type = _resolve_data_type_for_key(key)  # [DATA-004]

            if key == "stock_data" and isinstance(raw_value, str):
                actual_vendor = get_last_hit_vendor("get_stock_data")
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
                if "adjustment=" in raw_value:  # [DATA-P0-603629] astock_source_fallback
                    for line in raw_value.split("\n"):
                        if "adjustment=" in line and "[DATA-P0-603629]" in line:
                            parts = line.split("adjustment=")
                            if len(parts) > 1:
                                entry["adjustment"] = parts[1].strip().split("\n")[0].strip()

            if key == "fund_flow_individual":
                entry["unit"] = "万元"
                entry["source_type"] = "individual_fund_flow"
                entry["unit_verified"] = entry["unit"] is not None
                actual_vendor = get_last_hit_vendor("get_individual_fund_flow")  # [DATA-P0-603629]
                if actual_vendor:
                    entry["vendor"] = actual_vendor
            elif key == "fund_flow_board":
                entry["source_type"] = "board_fund_flow"
                actual_vendor = get_last_hit_vendor("get_board_fund_flow")  # [DATA-P0-603629]
                if actual_vendor:
                    entry["vendor"] = actual_vendor
            elif key == "lhb":
                entry["query_mode"] = pool.get("_lhb_query_mode", "on_demand")
                entry["force_reason"] = pool.get("_lhb_force_reason")  # [DATA-P0-603629]
                actual_vendor = get_last_hit_vendor("get_lhb_detail")  # [DATA-P0-603629]
                if actual_vendor:
                    entry["vendor"] = actual_vendor
            elif key in ("stock_data",):
                entry["unit"] = "股"
            elif key == "announcements":  # [DATA-P0-603629] astock_source_fallback
                actual_vendor = get_last_hit_vendor("get_announcements")
                if actual_vendor:
                    entry["vendor"] = actual_vendor
            elif key == "margin_trading":  # [DATA-010] margin_trading_raw_evidence
                actual_vendor = get_last_hit_vendor("get_margin_trading")
                if actual_vendor:
                    entry["vendor"] = actual_vendor
            elif key == "research_report":  # [DATA-011] research_report_raw_evidence
                actual_vendor = get_last_hit_vendor("get_research_report")
                if actual_vendor:
                    entry["vendor"] = actual_vendor
            elif key == "ratings":  # [DATA-012A] rating_data_collector_wiring
                actual_vendor = get_last_hit_vendor("get_ratings")
                if actual_vendor:
                    entry["vendor"] = actual_vendor

            # [DATA-004] raw_evidence_contract: resolve endpoint and fallback info
            entry["endpoint"] = _resolve_endpoint_for_vendor(
                entry["vendor"], data_type
            )
            entry["fallback_from"] = _resolve_fallback_for_vendor(
                entry["vendor"], data_type
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
                "source_type": entry.get("source_type"),
                "unit_verified": entry.get("unit_verified", None),
                "query_mode": entry.get("query_mode", None),
                "adjustment": entry.get("adjustment", None),  # [DATA-P0-603629] astock_source_fallback
                "force_reason": entry.get("force_reason", None),  # [DATA-P0-603629]
            }

        return raw_evidence
