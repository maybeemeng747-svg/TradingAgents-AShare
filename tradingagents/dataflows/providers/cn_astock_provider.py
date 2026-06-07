"""
A-share market data provider backed by direct HTTP APIs.

Data sources (zero third-party data wrapper dependencies):
- 腾讯财经 (qt.gtimg.cn) — real-time quotes, PE/PB/mcap
- 百度股市通 (finance.pae.baidu.com) — K-line with MA
- 东财 datacenter / push2 — financial info, insider data, news
- 新浪财经 (quotes.sina.cn) — financial statements (三表)
- 财联社 (cls.cn) — market news / telegraph
- 东财 search-api-web — stock-specific news
"""

import json
import logging
import re
import time
import uuid
import urllib.request
from datetime import datetime, timedelta

import pandas as pd
import requests
from stockstats import wrap

from .base import BaseMarketDataProvider
from ..trade_calendar import cn_no_data_reason

logger = logging.getLogger(__name__)


# ── Shared constants ──

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

# Chinese financial APIs should bypass proxy (direct connection)
NO_PROXY = {"http": None, "https": None}

# Rate-limit: minimum seconds between identical API calls
_last_call: dict[str, float] = {}
_RATE_LIMIT_INTERVAL = 0.2  # 200ms between calls to same endpoint


def _rate_limit(endpoint_key: str) -> None:
    """Basic rate limiter — sleep if calling the same endpoint too fast."""
    now = time.monotonic()
    last = _last_call.get(endpoint_key, 0.0)
    wait = _RATE_LIMIT_INTERVAL - (now - last)
    if wait > 0:
        time.sleep(wait)
    _last_call[endpoint_key] = time.monotonic()


# ── Ticker normalization ──

def _extract_code(symbol: str) -> str:
    """Extract 6-digit code from any format: '600519', 'SH600519', '600519.SH'."""
    s = symbol.strip().upper()
    m = re.search(r"(\d{6})", s)
    if not m:
        raise ValueError(f"Cannot extract 6-digit code from: {symbol}")
    return m.group(1)


def _get_prefix(code: str) -> str:
    """6-digit code → market prefix (sh/sz/bj)."""
    if code.startswith(("6", "9")):
        return "sh"
    elif code.startswith("8"):
        return "bj"
    else:
        return "sz"


def _tencent_prefixed(code: str) -> str:
    return f"{_get_prefix(code)}{code}"


def _sina_prefixed(code: str) -> str:
    return f"{_get_prefix(code)}{code}"


# ── Eastmoney datacenter shared helper ──

DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"


def _eastmoney_datacenter(
    report_name: str,
    columns: str = "ALL",
    filter_str: str = "",
    page_size: int = 50,
    sort_columns: str = "",
    sort_types: str = "-1",
) -> list[dict]:
    """东财数据中心统一查询 — 龙虎榜/解禁/融资融券/大宗交易/股东户数/分红."""
    _rate_limit(f"em_dc_{report_name}")
    params = {
        "reportName": report_name,
        "columns": columns,
        "filter": filter_str,
        "pageNumber": "1",
        "pageSize": str(page_size),
        "sortColumns": sort_columns,
        "sortTypes": sort_types,
        "source": "WEB",
        "client": "WEB",
    }
    r = requests.get(DATACENTER_URL, params=params, headers={"User-Agent": UA}, timeout=15, proxies=NO_PROXY)
    d = r.json()
    if d.get("result") and d["result"].get("data"):
        return d["result"]["data"]
    return []


class CnAstockProvider(BaseMarketDataProvider):
    """A-share provider backed by direct HTTP APIs (腾讯/东财/新浪/同花顺/财联社)."""

    INDICATOR_DESCRIPTIONS = {
        "close_50_sma": "50 日均线（SMA）：中期趋势指标。",
        "close_200_sma": "200 日均线（SMA）：长期趋势基准。",
        "close_10_ema": "10 日指数均线（EMA）：短期响应更快。",
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

    # ── Provider identity ──

    @property
    def name(self) -> str:
        return "cn_astock"

    # ── Internal helpers ──

    def _fetch_kline_baidu(self, code: str, start_date: str = "") -> pd.DataFrame:
        """Fetch daily K-line from 百度股市通 (HTTP, returns OHLCV + MA)."""
        _rate_limit("baidu_kline")
        url = "https://finance.pae.baidu.com/selfselect/getstockquotation"
        params = {
            "all": "1", "isIndex": "false", "isBk": "false", "isBlock": "false",
            "isFutures": "false", "isStock": "true", "newFormat": "1",
            "group": "quotation_kline_ab", "finClientType": "pc",
            "code": code, "start_time": start_date, "ktype": "1",
        }
        headers = {
            "User-Agent": UA,
            "Accept": "application/vnd.finance-web.v1+json",
            "Origin": "https://gushitong.baidu.com",
            "Referer": "https://gushitong.baidu.com/",
        }
        r = requests.get(url, params=params, headers=headers, timeout=15, proxies=NO_PROXY)
        d = r.json()
        result = d.get("Result", {})
        md = result.get("newMarketData", {})
        keys = md.get("keys", [])
        rows_str = md.get("marketData", "")
        if not keys or not rows_str:
            return pd.DataFrame()

        records = []
        for row_str in rows_str.split(";"):
            if not row_str.strip():
                continue
            parts = row_str.split(",")
            if len(parts) < 6:
                continue
            records.append(parts)

        if not records:
            return pd.DataFrame()

        df = pd.DataFrame(records, columns=keys[:len(records[0])])
        # Standardize column names
        col_map = {
            "time": "Date", "open": "Open", "close": "Close",
            "high": "High", "low": "Low", "volume": "Volume",
            "amount": "Amount",
        }
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

        for c in ["Open", "High", "Low", "Close", "Volume"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        if "Date" in df.columns:
            df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        df = df.dropna(subset=["Date", "Open", "High", "Low", "Close", "Volume"])
        df = df.sort_values("Date").reset_index(drop=True)
        df.attrs["_adjustment"] = "未知"  # [DATA-P0-603629] baidu kline adjustment type unknown
        return df

    def _fetch_kline_em(self, code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Fallback: fetch daily K-line from 东财 push2his (HTTP).

        # [DATA-P0-603629] astock_source_fallback: fqt=1 means 前复权 (forward-adjusted).
        """
        _rate_limit("em_kline")
        market_code = 1 if code.startswith(("5", "6", "9")) else 0
        secid = f"{market_code}.{code}"
        url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        params = {
            "secid": secid,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": "101",
            "fqt": "1",
            "beg": start_date.replace("-", ""),
            "end": end_date.replace("-", ""),
        }
        headers = {"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"}
        r = requests.get(url, params=params, headers=headers, timeout=15, proxies=NO_PROXY)
        d = r.json()
        klines = d.get("data", {}).get("klines", [])
        if not klines:
            return pd.DataFrame()

        records = []
        for line in klines:
            parts = line.split(",")
            if len(parts) >= 6:
                records.append({
                    "Date": parts[0],
                    "Open": float(parts[1]),
                    "Close": float(parts[2]),
                    "High": float(parts[3]),
                    "Low": float(parts[4]),
                    "Volume": float(parts[5]),
                })
        df = pd.DataFrame(records)
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        df = df.dropna(subset=["Date"]).sort_values("Date").reset_index(drop=True)
        df.attrs["_adjustment"] = "前复权"  # [DATA-P0-603629] astock_source_fallback
        return df

    def _fetch_hist_df(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Fetch historical OHLCV. Tries 百度 first, falls back to 东财."""
        code = _extract_code(symbol)
        errors = []

        # Source 1: 百度股市通 K线
        try:
            df = self._fetch_kline_baidu(code)
            if not df.empty:
                start_dt = pd.to_datetime(start_date, errors="coerce")
                end_dt = pd.to_datetime(end_date, errors="coerce")
                if pd.notna(start_dt) and pd.notna(end_dt):
                    df = df[(df["Date"] >= start_dt) & (df["Date"] <= end_dt)]
                df = df.reset_index(drop=True)
                if not df.empty:
                    return df
            errors.append("baidu: empty result")
        except Exception as exc:
            errors.append(f"baidu: {type(exc).__name__}: {exc}")

        # Source 2: 东财 push2his
        try:
            df = self._fetch_kline_em(code, start_date, end_date)
            if not df.empty:
                return df
            errors.append("eastmoney: empty result")
        except Exception as exc:
            errors.append(f"eastmoney: {type(exc).__name__}: {exc}")

        raise NotImplementedError(
            f"cn_astock failed to fetch K-line for {symbol}: {'; '.join(errors)}"
        )

    # ── Required interface methods ──

    def get_stock_data(self, symbol: str, start_date: str, end_date: str) -> str:
        """Return OHLCV data as CSV string.

        # [DATA-P0-603629] astock_source_fallback: includes adjustment type in header.
        """
        try:
            df = self._fetch_hist_df(symbol, start_date, end_date)
        except NotImplementedError as exc:
            return str(exc)

        if df.empty:
            return f"No data found for symbol '{symbol}' between {start_date} and {end_date}"

        out = df.copy()
        out["Dividends"] = 0.0
        out["Stock Splits"] = 0.0
        out["Date"] = out["Date"].dt.strftime("%Y-%m-%d")
        adjustment = getattr(df, "attrs", {}).get("_adjustment", "未知")  # [DATA-P0-603629]
        header = f"# Stock data for {symbol} from {start_date} to {end_date}\n"
        header += f"# Total records: {len(out)}\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        header += f"# [DATA-P0-603629] adjustment={adjustment}\n"
        header += "\n"
        return header + out.to_csv(index=False)

    def get_indicators(
        self, symbol: str, indicator: str, curr_date: str, look_back_days: int
    ) -> str:
        """Return technical indicator values as formatted text."""
        if indicator not in self.INDICATOR_DESCRIPTIONS:
            raise ValueError(
                f"Indicator {indicator} is not supported. "
                f"Please choose from: {list(self.INDICATOR_DESCRIPTIONS.keys())}"
            )

        curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
        start_dt = curr_dt - timedelta(days=max(look_back_days, 260))
        df = self._fetch_hist_df(symbol, start_dt.strftime("%Y-%m-%d"), curr_date)
        if df.empty:
            return f"No data found for {symbol} for indicator {indicator}"

        ind_df = df.rename(
            columns={
                "Date": "date", "Open": "open", "High": "high",
                "Low": "low", "Close": "close", "Volume": "volume",
            }
        )[["date", "open", "high", "low", "close", "volume"]].copy()

        ss = wrap(ind_df)
        indicator_series = ss[indicator]

        values_by_date: dict[str, str] = {}
        for idx, dt_val in enumerate(ind_df["date"]):
            date_str = pd.to_datetime(dt_val).strftime("%Y-%m-%d")
            val = indicator_series.iloc[idx]
            values_by_date[date_str] = "N/A" if pd.isna(val) else str(val)

        begin = curr_dt - timedelta(days=look_back_days)
        lines = []
        d = curr_dt
        while d >= begin:
            key = d.strftime("%Y-%m-%d")
            value = values_by_date.get(key)
            if value is None or value == "N/A":
                value = cn_no_data_reason(key)
            lines.append(f"{key}: {value}")
            d -= timedelta(days=1)

        return (
            f"## {indicator} 指标值（{begin.strftime('%Y-%m-%d')} 至 {curr_date}）：\n\n"
            + "\n".join(lines)
            + "\n\n"
            + self.INDICATOR_DESCRIPTIONS[indicator]
        )

    def get_fundamentals(self, ticker: str, curr_date: str = None) -> str:
        """Return company fundamentals from 东财 push2 + 新浪财报摘要."""
        code = _extract_code(ticker)
        parts = [f"## Fundamentals for {ticker}"]
        errors = []

        # Source 1: 东财个股基本面 (push2)
        try:
            info = self._eastmoney_stock_info(code)
            if info:
                parts.append("### Company Profile (东财)")
                info_lines = []
                for k, v in info.items():
                    info_lines.append(f"- **{k}**: {v}")
                parts.append("\n".join(info_lines))
        except Exception as exc:
            errors.append(f"eastmoney push2: {type(exc).__name__}")

        # Source 2: 腾讯实时估值
        try:
            quotes = self._tencent_quote([code])
            if code in quotes:
                q = quotes[code]
                parts.append("### Valuation Snapshot (腾讯)")
                parts.append(
                    f"- **现价**: {q['price']}  **昨收**: {q['last_close']}\n"
                    f"- **PE(TTM)**: {q['pe_ttm']}  **PE(静)**: {q['pe_static']}\n"
                    f"- **PB**: {q['pb']}  **总市值**: {q['mcap_yi']}亿\n"
                    f"- **换手率**: {q['turnover_pct']}%  **量比**: {q['vol_ratio']}"
                )
        except Exception as exc:
            errors.append(f"tencent quote: {type(exc).__name__}")

        if len(parts) > 1:
            return "\n\n".join(parts)

        raise NotImplementedError(
            f"cn_astock is temporarily unavailable for fundamentals: {'; '.join(errors)}"
        )

    def _eastmoney_stock_info(self, code: str) -> dict:
        """东财个股基本面 — 行业/总股本/流通股/市值/上市日期."""
        _rate_limit("em_stock_info")
        market_code = 1 if code.startswith(("5", "6", "9")) else 0
        url = "https://push2.eastmoney.com/api/qt/stock/get"
        params = {
            "fltt": "2", "invt": "2",
            "fields": "f57,f58,f84,f85,f127,f116,f117,f189,f43",
            "secid": f"{market_code}.{code}",
        }
        headers = {"User-Agent": UA}
        r = requests.get(url, params=params, headers=headers, timeout=10, proxies=NO_PROXY)
        d = r.json().get("data", {})
        if not d:
            return {}
        return {
            "代码": d.get("f57", ""),
            "名称": d.get("f58", ""),
            "行业": d.get("f127", ""),
            "总股本": d.get("f84", 0),
            "流通股": d.get("f85", 0),
            "总市值(元)": d.get("f116", 0),
            "流通市值(元)": d.get("f117", 0),
            "上市日期": str(d.get("f189", "")),
            "现价": d.get("f43", 0),
        }

    def _tencent_quote(self, codes: list[str]) -> dict[str, dict]:
        """腾讯财经实时行情 — PE/PB/市值/换手率."""
        _rate_limit("tencent_quote")
        prefixed = []
        for c in codes:
            code = _extract_code(c) if len(c) != 6 else c
            prefixed.append(_tencent_prefixed(code))

        url = "https://qt.gtimg.cn/q=" + ",".join(prefixed)
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "Mozilla/5.0")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        resp = opener.open(req, timeout=10)
        data = resp.read().decode("gbk")

        result: dict[str, dict] = {}
        for line in data.strip().split(";"):
            if not line.strip() or "=" not in line or '"' not in line:
                continue
            key = line.split("=")[0].split("_")[-1]
            vals = line.split('"')[1].split("~")
            if len(vals) < 53:
                continue
            code = key[2:]
            result[code] = {
                "name": vals[1],
                "price": float(vals[3]) if vals[3] else 0,
                "last_close": float(vals[4]) if vals[4] else 0,
                "open": float(vals[5]) if vals[5] else 0,
                "change_amt": float(vals[31]) if vals[31] else 0,
                "change_pct": float(vals[32]) if vals[32] else 0,
                "high": float(vals[33]) if vals[33] else 0,
                "low": float(vals[34]) if vals[34] else 0,
                "amount_wan": float(vals[37]) if vals[37] else 0,
                "turnover_pct": float(vals[38]) if vals[38] else 0,
                "pe_ttm": float(vals[39]) if vals[39] else 0,
                "amplitude_pct": float(vals[43]) if vals[43] else 0,
                "mcap_yi": float(vals[44]) if vals[44] else 0,
                "float_mcap_yi": float(vals[45]) if vals[45] else 0,
                "pb": float(vals[46]) if vals[46] else 0,
                "limit_up": float(vals[47]) if vals[47] else 0,
                "limit_down": float(vals[48]) if vals[48] else 0,
                "vol_ratio": float(vals[49]) if vals[49] else 0,
                "pe_static": float(vals[52]) if vals[52] else 0,
            }
        return result

    # ── Financial statements (新浪财报三表) ──

    def _sina_financial_report(self, code: str, report_type: str) -> list[dict]:
        """新浪财报三表. report_type: 'fzb'(资产负债表) / 'lrb'(利润表) / 'llb'(现金流量表)."""
        _rate_limit("sina_finance")
        prefix = _get_prefix(code)
        paper_code = f"{prefix}{code}"
        url = "https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022"
        params = {
            "paperCode": paper_code,
            "source": report_type,
            "type": "0",
            "page": "1",
            "num": "20",
        }
        headers = {"User-Agent": UA}
        r = requests.get(url, params=params, headers=headers, timeout=15, proxies=NO_PROXY)
        d = r.json()
        result = d.get("result", {}).get("data", {})
        # V2 format: result.data.report_list is a dict keyed by date
        # Each report has 'data' which is a list of {item_title, item_value, ...}
        report_list = result.get("report_list", {})
        if isinstance(report_list, dict):
            rows = []
            for date_key, report in report_list.items():
                if isinstance(report, dict):
                    items = report.get("data", [])
                    if isinstance(items, list):
                        row = {"report_date": date_key}
                        for item in items:
                            if isinstance(item, dict) and item.get("item_title"):
                                row[item["item_title"]] = item.get("item_value", "")
                        rows.append(row)
            return rows
        # V1 fallback: result.data[report_type] is a list
        items = result.get(report_type, [])
        return items if isinstance(items, list) else []

    @staticmethod
    def _shrink_table(df: pd.DataFrame, max_rows: int = 12, max_cols: int = 16) -> pd.DataFrame:
        if df is None or df.empty:
            return df
        rows = min(max_rows, len(df))
        cols = min(max_cols, len(df.columns))
        return df.head(rows).iloc[:, :cols]

    def get_balance_sheet(
        self, ticker: str, freq: str = "quarterly", curr_date: str = None
    ) -> str:
        code = _extract_code(ticker)
        try:
            items = self._sina_financial_report(code, "fzb")
            if not items:
                return f"No balance sheet data found for {ticker}"
            df = pd.DataFrame(items)
            non_nan = [c for c in df.columns if df[c].notna().any()]
            if non_nan:
                df = df[non_nan]
            return (
                f"## Balance Sheet ({ticker})\n\n"
                + self._shrink_table(df, max_rows=6, max_cols=20).to_markdown(index=False)
            )
        except Exception as exc:
            return f"Balance sheet unavailable for {ticker}: {exc}"

    def get_cashflow(
        self, ticker: str, freq: str = "quarterly", curr_date: str = None
    ) -> str:
        code = _extract_code(ticker)
        try:
            items = self._sina_financial_report(code, "llb")
            if not items:
                return f"No cashflow data found for {ticker}"
            df = pd.DataFrame(items)
            non_nan = [c for c in df.columns if df[c].notna().any()]
            if non_nan:
                df = df[non_nan]
            return (
                f"## Cashflow ({ticker})\n\n"
                + self._shrink_table(df, max_rows=12, max_cols=20).to_markdown(index=False)
            )
        except Exception as exc:
            return f"Cashflow unavailable for {ticker}: {exc}"

    def get_income_statement(
        self, ticker: str, freq: str = "quarterly", curr_date: str = None
    ) -> str:
        code = _extract_code(ticker)
        try:
            items = self._sina_financial_report(code, "lrb")
            if not items:
                return f"No income statement data found for {ticker}"
            df = pd.DataFrame(items)
            non_nan = [c for c in df.columns if df[c].notna().any()]
            if non_nan:
                df = df[non_nan]
            return (
                f"## Income Statement ({ticker})\n\n"
                + self._shrink_table(df, max_rows=12, max_cols=20).to_markdown(index=False)
            )
        except Exception as exc:
            return f"Income statement unavailable for {ticker}: {exc}"

    # ── News ──

    def get_news(self, ticker: str, start_date: str, end_date: str) -> str:
        """东财个股新闻 (search-api-web JSONP)."""
        code = _extract_code(ticker)
        try:
            _rate_limit("em_news")
            cb = "jQuery_news"
            url = "https://search-api-web.eastmoney.com/search/jsonp"
            inner_params = json.dumps({
                "uid": "",
                "keyword": code,
                "type": ["cmsArticleWebOld"],
                "client": "web",
                "clientType": "web",
                "clientVersion": "curr",
                "param": {
                    "cmsArticleWebOld": {
                        "searchScope": "default", "sort": "default",
                        "pageIndex": 1, "pageSize": 20,
                        "preTag": "", "postTag": "",
                    }
                },
            }, separators=(",", ":"))
            params = {"cb": cb, "param": inner_params}
            headers = {"User-Agent": UA, "Referer": "https://so.eastmoney.com/"}
            r = requests.get(url, params=params, headers=headers, timeout=15, proxies=NO_PROXY)

            text = r.text
            json_str = text[text.index("(") + 1:text.rindex(")")]
            d = json.loads(json_str)

            cms = d.get("result", {}).get("cmsArticleWebOld", [])
            # Handle both formats: list directly or dict with 'list' key
            if isinstance(cms, dict):
                articles = cms.get("list", [])
            elif isinstance(cms, list):
                articles = cms
            else:
                articles = []
            if not articles:
                return f"No news found for {ticker}"

            rows = []
            for a in articles[:20]:
                title = re.sub(r"<[^>]+>", "", a.get("title", ""))
                content = re.sub(r"<[^>]+>", "", a.get("content", ""))[:400]
                src = a.get("mediaName", "Unknown")
                link = a.get("url", "")
                pub_time = a.get("date", "")

                # Date filter
                if pub_time:
                    try:
                        pub_dt = datetime.strptime(pub_time[:10], "%Y-%m-%d")
                        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
                        end_dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
                        if pub_dt < start_dt or pub_dt >= end_dt:
                            continue
                    except ValueError:
                        pass

                rows.append(f"### {title} (source: {src})")
                if content and content != "nan":
                    rows.append(content)
                if link and link != "nan":
                    rows.append(f"Link: {link}")
                rows.append("")

            if not rows:
                return f"No news found for {ticker} between {start_date} and {end_date}"
            return f"## {ticker} 新闻（{start_date} 至 {end_date}）：\n\n" + "\n".join(rows)

        except Exception as exc:
            return f"News unavailable for {ticker}: {exc}"

    def get_global_news(
        self, curr_date: str, look_back_days: int = 7, limit: int = 50
    ) -> str:
        """财联社快讯 (cls.cn) + 东财全球资讯."""
        all_news: list[dict] = []
        errors = []

        # Source 1: 财联社电报
        try:
            _rate_limit("cls_telegraph")
            url = "https://www.cls.cn/nodeapi/telegraphList"
            params = {"rn": str(min(limit, 50)), "page": "1"}
            headers = {"User-Agent": UA, "Referer": "https://www.cls.cn/"}
            r = requests.get(url, params=params, headers=headers, timeout=10, proxies=NO_PROXY)
            d = r.json()
            for item in d.get("data", {}).get("roll_data", []):
                ctime = item.get("ctime", "")
                time_str = ""
                if ctime:
                    try:
                        time_str = datetime.fromtimestamp(int(ctime)).strftime("%Y-%m-%d %H:%M")
                    except (ValueError, OSError):
                        time_str = str(ctime)
                all_news.append({
                    "title": item.get("title", "") or item.get("brief", ""),
                    "content": item.get("content", "") or item.get("brief", ""),
                    "time": time_str,
                    "source": "财联社",
                })
        except Exception as exc:
            errors.append(f"cls: {type(exc).__name__}")

        # Source 2: 东财全球资讯 (7x24)
        try:
            _rate_limit("em_global_news")
            url = "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"
            params = {
                "client": "web", "biz": "web_724",
                "fastColumn": "102", "sortEnd": "",
                "pageSize": str(min(limit, 50)),
                "req_trace": str(uuid.uuid4()),
            }
            headers = {"User-Agent": UA, "Referer": "https://kuaixun.eastmoney.com/"}
            r = requests.get(url, params=params, headers=headers, timeout=10, proxies=NO_PROXY)
            d = r.json()
            for item in d.get("data", {}).get("fastNewsList", []):
                all_news.append({
                    "title": item.get("title", ""),
                    "content": item.get("summary", "")[:200],
                    "time": item.get("showTime", ""),
                    "source": "东财7x24",
                })
        except Exception as exc:
            errors.append(f"eastmoney: {type(exc).__name__}")

        if not all_news:
            return f"Global news unavailable: {'; '.join(errors)}" if errors else "No global news found"

        start = (
            datetime.strptime(curr_date, "%Y-%m-%d") - timedelta(days=look_back_days)
        ).strftime("%Y-%m-%d")

        rows = []
        for n in all_news[:limit]:
            rows.append(f"### {n['title']} ({n['source']})")
            if n["content"] and n["content"] != "nan":
                rows.append(n["content"][:300])
            if n["time"]:
                rows.append(f"Time: {n['time']}")
            rows.append("")

        return f"## 全球市场新闻（{start} 至 {curr_date}）：\n\n" + "\n".join(rows)

    # ── Insider transactions ──

    def get_insider_transactions(self, symbol: str) -> str:
        """东财 datacenter — 股东持股变动 (RPT_INSIDER_SHAREHOLDERS)."""
        code = _extract_code(symbol)
        try:
            data = _eastmoney_datacenter(
                "RPT_INSIDER_SHAREHOLDERS",
                filter_str=f'(SECURITY_CODE="{code}")',
                page_size=20,
                sort_columns="CHANGE_DATE",
                sort_types="-1",
            )
            if not data:
                return f"No insider transaction data found for {symbol}"

            rows = []
            for row in data:
                rows.append({
                    "date": str(row.get("CHANGE_DATE", ""))[:10],
                    "holder": row.get("HOLDER_NAME", ""),
                    "change_type": row.get("CHANGE_TYPE", ""),
                    "shares": row.get("CHANGE_SHARES", 0),
                    "avg_price": row.get("AVG_PRICE", 0),
                    "after_shares": row.get("HOLD_SHARES_AFTER", 0),
                })

            lines = [f"## Insider Transactions for {symbol}\n"]
            for r in rows:
                lines.append(
                    f"- {r['date']} | {r['holder']} | {r['change_type']} | "
                    f"变动: {r['shares']}股 | 均价: {r['avg_price']} | "
                    f"变动后: {r['after_shares']}股"
                )
            return "\n".join(lines)

        except Exception as exc:
            return f"Insider transactions unavailable for {symbol}: {exc}"

    # ── Real-time quotes ──

    # ── [DATA-P0-603629] Fund flow via Eastmoney push2his ──

    def get_individual_fund_flow(self, symbol: str) -> str:
        """个股资金流 — 东财 push2his 直连 fallback.  # [DATA-P0-603629] astock_source_fallback

        Returns last 20 trading days of main-capital net flow data.
        Eastmoney push2his fflow/daykline returns values already in 万元.
        """
        code = _extract_code(symbol)
        try:
            _rate_limit("em_fund_flow_individual")
            market_code = 1 if code.startswith(("5", "6", "9")) else 0
            secid = f"{market_code}.{code}"
            url = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
            params = {
                "secid": secid,
                "fields1": "f1,f2,f3,f7",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63",
                "lmt": "20",
                "klt": "101",
            }
            headers = {"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"}
            r = requests.get(url, params=params, headers=headers, timeout=15, proxies=NO_PROXY)
            d = r.json()
            klines = d.get("data", {}).get("klines", [])
            if not klines:
                return f"{symbol} 近期主力资金流向数据暂不可用。"

            header = f"{symbol} 近20日主力资金净流向（Eastmoney push2his，单位：万元）：\n"
            header += "日期 | 主力净流入 | 小单净流入 | 中单净流入 | 大单净流入 | 超大单净流入\n"
            rows = []
            for line in klines:
                parts = line.split(",")
                if len(parts) >= 6:
                    try:
                        v1 = float(parts[1]) / 10000
                        v2 = float(parts[2]) / 10000
                        v3 = float(parts[3]) / 10000
                        v4 = float(parts[4]) / 10000
                        v5 = float(parts[5]) / 10000
                        rows.append(
                            f"{parts[0]} | {v1:.2f} | {v2:.2f} | {v3:.2f} | {v4:.2f} | {v5:.2f}"
                        )
                    except (ValueError, IndexError):
                        rows.append(
                            f"{parts[0]} | {parts[1]} | {parts[2]} | {parts[3]} | {parts[4]} | {parts[5]}"
                        )
            return header + "\n".join(rows)

        except Exception as exc:
            return f"个股资金流向数据获取失败（Eastmoney push2his）：{type(exc).__name__}: {exc}"

    def get_board_fund_flow(self) -> str:
        """行业板块资金流 — 东财 push2 直连.  # [DATA-P0-603629] astock_source_fallback"""
        try:
            _rate_limit("em_fund_flow_board")
            url = "https://push2.eastmoney.com/api/qt/clist/get"
            params = {
                "pn": "1",
                "pz": "15",
                "po": "1",
                "np": "1",
                "fltt": "2",
                "invt": "2",
                "fid": "f62",
                "fs": "m:90+t:2",
                "fields": "f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,f164,f174",
            }
            headers = {"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"}
            r = requests.get(url, params=params, headers=headers, timeout=15, proxies=NO_PROXY)
            d = r.json()
            items = d.get("data", {}).get("diff", [])
            if not items:
                return "板块资金流向数据暂不可用。"

            lines = ["板块资金流向排名（Eastmoney push2）：", "排名 | 板块代码 | 板块名称 | 涨跌幅 | 主力净流入"]
            for i, item in enumerate(items[:10], 1):
                code = item.get("f12", "")
                name = item.get("f14", "")
                pct = item.get("f3", "")
                net = item.get("f62", "")
                lines.append(f"{i} | {code} | {name} | {pct}% | {net}")
            return "\n".join(lines)

        except Exception as exc:
            return f"板块资金流向数据获取失败（Eastmoney push2）：{type(exc).__name__}: {exc}"

    def get_lhb_detail(self, symbol: str, date: str, *, force: bool = False) -> str:
        """个股龙虎榜 — 东财 datacenter.  # [DATA-P0-603629] astock_source_fallback

        This provides a direct Eastmoney fallback for LHB queries,
        independent of AKShare's stock_lhb_detail_em.
        """
        code = _extract_code(symbol)
        if not force:
            return (
                f"{symbol} [G-007] LHB_NOT_QUERIED: 龙虎榜查询未触发（force=False）。"
                "龙虎榜为按需查询接口，仅当检测到资金异动时才应调用，以避免批量日期查询触发 API 限流。"
            )
        try:
            _rate_limit("em_lhb_detail")
            lhb_filter = (
                f'(SECURITY_CODE="{code}")'
                f"(TRADE_DATE>='{date}')"
                f"(TRADE_DATE<='{date}')"
            )
            data = _eastmoney_datacenter(
                "RPT_DAILYBILLBOARD_DETAILSNEW",
                filter_str=lhb_filter,
                page_size=50,
                sort_columns="BILLBOARD_NET_AMT",
                sort_types="-1",
            )
            if not data:
                return f"{symbol} [G-007] LHB_NORMAL_NO_DATA: 在 {date} 无龙虎榜数据（非异动日属正常）。"

            lines = [f"{symbol} [G-007] LHB_HAS_DATA: 龙虎榜明细（{date}，Eastmoney datacenter）："]
            for row in data[:20]:
                reason = row.get("EXPLANATION", "")
                net_buy = (row.get("BILLBOARD_NET_AMT") or 0) / 10000
                buy_amt = (row.get("BUY_AMOUNT") or 0) / 10000
                sell_amt = (row.get("SELL_AMOUNT") or 0) / 10000
                dept_buy = row.get("BUY_BROKER_NAME", "")
                dept_sell = row.get("SELL_BROKER_NAME", "")
                lines.append(
                    f"- {reason} | 净买 {net_buy:.1f}万 | 买入 {buy_amt:.1f}万 | "
                    f"卖出 {sell_amt:.1f}万 | 买方营业部: {dept_buy} | 卖方营业部: {dept_sell}"
                )
            return "\n".join(lines)

        except Exception as exc:
            return f"{symbol} [G-007] LHB_FAILED: 龙虎榜数据获取失败（Eastmoney datacenter）：{type(exc).__name__}: {exc}"

    # ── Real-time quotes ──

    def get_realtime_quotes(self, symbols: list[str]) -> str:
        """腾讯财经实时行情.  # [DATA-P0-603629] astock_source_fallback

        Now includes turnover_rate, volume_ratio, limit_up/down, market_cap, PE/PB.
        """
        if not symbols:
            return json.dumps({})

        code_map: dict[str, str] = {}  # code → original symbol
        for s in symbols:
            if not s or not s.strip():
                continue
            try:
                code = _extract_code(s)
            except ValueError:
                continue
            if code not in code_map:
                code_map[code] = s.strip().upper()

        if not code_map:
            return json.dumps({})

        try:
            quotes = self._tencent_quote(list(code_map.keys()))
        except Exception:
            return json.dumps({})

        result: dict[str, dict] = {}
        for code, original in code_map.items():
            if code not in quotes:
                continue
            q = quotes[code]
            amount_val = q["amount_wan"] * 10000  # [N-002] cn_astock_fallback: 万 → 元
            # [N-002] cn_astock_fallback: sanity check — amount is 成交额(元), NOT volume(股)
            if amount_val < 10000:
                logger.warning(
                    "[N-002] cn_astock_fallback: amount=%s for %s is suspiciously low "
                    "(< 10000) — may be volume mislabeled as amount",
                    amount_val, original,
                )
            result[original] = {
                "price": q["price"],
                "open": q["open"],
                "high": q["high"],
                "low": q["low"],
                "previous_close": q["last_close"],
                "change": q["change_amt"],
                "change_pct": q["change_pct"],
                "volume": q["amount_wan"] * 10000,
                "amount": amount_val,
                "turnover_rate": q["turnover_pct"],
                "volume_ratio": q["vol_ratio"],
                "limit_up": q["limit_up"],
                "limit_down": q["limit_down"],
                "market_cap": q["mcap_yi"],
                "pe_ttm": q["pe_ttm"],
                "pe_static": q["pe_static"],
                "pb": q["pb"],
                "source": "tencent",
            }
        return json.dumps(result, ensure_ascii=False)

    # ── Bonus methods (unique to a-stock-data) ──

    def get_research_report(self, symbol: str) -> str:
        """东财研报列表 (reportapi).  # [DATA-011] research_report_raw_evidence"""
        code = _extract_code(symbol)
        try:
            _rate_limit("em_reports")
            session = requests.Session()
            session.headers.update({"User-Agent": UA, "Referer": "https://data.eastmoney.com/"})

            all_records = []
            for page in range(1, 4):
                params = {
                    "industryCode": "*", "pageSize": "100", "industry": "*",
                    "rating": "*", "ratingChange": "*",
                    "beginTime": "2000-01-01", "endTime": "2030-01-01",
                    "pageNo": str(page), "fields": "", "qType": "0",
                    "orgCode": "", "code": code, "rcode": "",
                    "p": str(page), "pageNum": str(page), "pageNumber": str(page),
                }
                r = session.get(
                    "https://reportapi.eastmoney.com/report/list",
                    params=params, timeout=30,
                )
                d = r.json()
                rows = d.get("data") or []
                if not rows:
                    break
                all_records.extend(rows)
                if page >= (d.get("TotalPage", 1) or 1):
                    break
                time.sleep(0.3)

            if not all_records:
                return f"{symbol} [DATA-011] REPORT_NORMAL_NO_DATA: 该股无券商研报。"

            lines = [f"{symbol} [DATA-011] REPORT_HAS_DATA: 研报数据（Eastmoney reportapi，共 {len(all_records)} 篇）："]
            for r in all_records[:20]:
                date = (r.get("publishDate", ""))[:10]
                org = r.get("orgSName", "未知")
                title = r.get("title", "")[:60]
                rating = r.get("emRatingName", "")
                eps_cur = r.get("predictThisYearEps", "")
                eps_next = r.get("predictNextYearEps", "")
                info_code = r.get("infoCode", "")
                pdf_url = f"https://pdf.dfcfw.com/pdf/H3_{info_code}_1.pdf" if info_code else ""

                line = f"- {date} | {org} | {rating} | {title}"
                if eps_cur:
                    line += f" | 今年EPS: {eps_cur}"
                if eps_next:
                    line += f" | 明年EPS: {eps_next}"
                if pdf_url:
                    line += f" | PDF: {pdf_url}"
                lines.append(line)

            return "\n".join(lines)

        except Exception as exc:
            return f"{symbol} [DATA-011] REPORT_FAILED: 研报数据获取失败（Eastmoney reportapi）：{type(exc).__name__}: {exc}"

    def get_research_reports(self, symbol: str, max_pages: int = 3) -> str:
        """东财研报列表 (reportapi) — 保留向后兼容。"""
        return self.get_research_report(symbol)

    def get_ratings(self, symbol: str) -> str:
        """分析师评级/目标价 — 东财 reportapi 提取评级变更.  # [DATA-012] rating_raw_evidence"""
        code = _extract_code(symbol)
        try:
            _rate_limit("em_ratings")
            session = requests.Session()
            session.headers.update({"User-Agent": UA, "Referer": "https://data.eastmoney.com/"})

            all_records = []
            for page in range(1, 3):
                params = {
                    "industryCode": "*", "pageSize": "50", "industry": "*",
                    "rating": "*", "ratingChange": "*",
                    "beginTime": "2000-01-01", "endTime": "2030-01-01",
                    "pageNo": str(page), "fields": "", "qType": "0",
                    "orgCode": "", "code": code, "rcode": "",
                    "p": str(page), "pageNum": str(page), "pageNumber": str(page),
                }
                r = session.get(
                    "https://reportapi.eastmoney.com/report/list",
                    params=params, timeout=30,
                )
                d = r.json()
                rows = d.get("data") or []
                if not rows:
                    break
                all_records.extend(rows)
                if page >= (d.get("TotalPage", 1) or 1):
                    break
                time.sleep(0.3)

            if not all_records:
                return f"{symbol} [DATA-012] RATINGS_NORMAL_NO_DATA: 该股无分析师评级数据。"

            lines = [f"{symbol} [DATA-012] RATINGS_HAS_DATA: 分析师评级数据（Eastmoney reportapi，共 {len(all_records)} 条）："]
            for rec in all_records[:20]:
                date_val = (rec.get("publishDate", ""))[:10]
                org = rec.get("orgSName", "未知")
                rating = rec.get("emRatingName", "")
                last_rating = rec.get("lastEmRatingName", "")
                target_price = rec.get("indvAimPriceT", "")
                title = rec.get("title", "")[:40]
                line = f"- {date_val} | {org} | {rating}"
                if last_rating:
                    line += f"（前次: {last_rating}）"
                if target_price and str(target_price) != "0":
                    line += f" | 目标价: {target_price}"
                if title:
                    line += f" | {title}"
                lines.append(line)

            return "\n".join(lines)

        except Exception as exc:
            return f"{symbol} [DATA-012] RATINGS_FAILED: 评级数据获取失败（Eastmoney reportapi）：{type(exc).__name__}: {exc}"

    def get_lhb_data(self, date: str, min_net_buy: float = None) -> str:
        """全市场龙虎榜 — 东财 datacenter."""
        try:
            data = _eastmoney_datacenter(
                "RPT_DAILYBILLBOARD_DETAILSNEW",
                filter_str=f"(TRADE_DATE>='{date}')(TRADE_DATE<='{date}')",
                page_size=500,
                sort_columns="BILLBOARD_NET_AMT",
                sort_types="-1",
            )
            if not data:
                return f"{date} 无龙虎榜数据（非交易日或盘后未更新）"

            lines = [f"## 龙虎榜 ({date}) — 共 {len(data)} 条\n"]
            for row in data[:30]:
                net_buy = (row.get("BILLBOARD_NET_AMT") or 0) / 10000
                if min_net_buy is not None and net_buy < min_net_buy:
                    continue
                code = row.get("SECURITY_CODE", "")
                name = row.get("SECURITY_NAME_ABBR", "")
                reason = row.get("EXPLANATION", "")
                change_pct = round(float(row.get("CHANGE_RATE") or 0), 2)
                lines.append(
                    f"- {code} {name}: {reason} | 净买 {net_buy:.1f}万 | 涨跌 {change_pct}%"
                )
            return "\n".join(lines)

        except Exception as exc:
            return f"龙虎榜数据获取失败: {exc}"

    def get_north_flow(self, symbol: str = None) -> str:
        """同花顺北向资金 — hsgtApi 实时分钟流向."""
        try:
            _rate_limit("ths_hsgt")
            HSGT_HEADERS = {
                "User-Agent": UA,
                "Host": "data.hexin.cn",
                "Referer": "https://data.hexin.cn/",
            }
            url = "https://data.hexin.cn/market/hsgtApi/method/dayChart/"
            r = requests.get(url, headers=HSGT_HEADERS, timeout=10, proxies=NO_PROXY)
            d = r.json()
            times = d.get("time", [])
            hgt = d.get("hgt", [])
            sgt = d.get("sgt", [])

            if not times:
                return "北向资金数据暂不可用"

            n = len(times)
            hgt_last = hgt[-1] if hgt else 0
            sgt_last = sgt[-1] if sgt else 0

            lines = [
                "## 北向资金当日流向\n",
                f"- 沪股通累计净买入: {hgt_last} 亿",
                f"- 深股通累计净买入: {sgt_last} 亿",
                f"- 合计: {round((hgt_last or 0) + (sgt_last or 0), 2)} 亿",
                f"- 数据点数: {n}",
                "",
                "### 最近5个时间点:",
            ]
            for i in range(max(0, n - 5), n):
                lines.append(f"  {times[i]}: 沪={hgt[i] if i < len(hgt) else '-'} 深={sgt[i] if i < len(sgt) else '-'}")

            return "\n".join(lines)

        except Exception as exc:
            return f"北向资金数据获取失败: {exc}"

    def get_announcements(self, symbol: str, page_size: int = 20) -> str:
        """巨潮公告全文检索 (cninfo.com.cn)."""
        code = _extract_code(symbol)
        try:
            _rate_limit("cninfo")
            if code.startswith("6"):
                org_id = f"gssh0{code}"
            elif code.startswith(("8", "4")):
                org_id = f"gsbj0{code}"
            else:
                org_id = f"gssz0{code}"

            url = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
            payload = {
                "stock": f"{code},{org_id}",
                "tabName": "fulltext",
                "pageSize": str(page_size),
                "pageNum": "1",
                "column": "", "category": "", "plate": "",
                "seDate": "", "searchkey": "", "secid": "",
                "sortName": "", "sortType": "",
                "isHLtitle": "true",
            }
            headers = {
                "User-Agent": UA,
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": "https://www.cninfo.com.cn/new/disclosure",
                "Origin": "https://www.cninfo.com.cn",
            }
            r = requests.post(url, data=payload, headers=headers, timeout=15)
            d = r.json()

            announcements = d.get("announcements", []) or []
            if not announcements:
                return f"No announcements found for {symbol}"

            lines = [f"## Announcements for {symbol} ({len(announcements)} total)\n"]
            for item in announcements[:page_size]:
                title = item.get("announcementTitle", "")
                atype = item.get("announcementTypeName", "")
                ts = item.get("announcementTime")
                if isinstance(ts, (int, float)):
                    date_str = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
                else:
                    date_str = str(ts)[:10] if ts else ""
                anno_id = item.get("announcementId", "")
                link = f"https://www.cninfo.com.cn/new/disclosure/detail?annoId={anno_id}" if anno_id else ""

                lines.append(f"- {date_str} | {atype} | {title}")
                if link:
                    lines.append(f"  Link: {link}")

            return "\n".join(lines)

        except Exception as exc:
            return f"Announcements unavailable for {symbol}: {exc}"

    def get_margin_trading(self, symbol: str) -> str:
        """个股融资融券 — 东财 datacenter.  # [DATA-010] margin_trading_raw_evidence"""
        code = _extract_code(symbol)
        try:
            _rate_limit("em_margin_trading")
            rzrq_filter = (
                f'(SECURITY_CODE="{code}")'
            )
            data = _eastmoney_datacenter(
                "RPT_RZRQ_LSHJ",
                filter_str=rzrq_filter,
                page_size=20,
                sort_columns="TRADE_DATE",
                sort_types="-1",
            )
            if not data:
                return f"{symbol} [DATA-010] MARGIN_NORMAL_NO_DATA: 该股非融资融券标的或当日无数据。"

            lines = [f"{symbol} [DATA-010] MARGIN_HAS_DATA: 融资融券数据（Eastmoney datacenter）："]
            for row in data[:10]:
                trade_date = row.get("TRADE_DATE", "")[:10] if row.get("TRADE_DATE") else ""
                rzrq_ye = (row.get("RZRQ_YE") or 0) / 10000
                rzrq_mre = (row.get("RZRQ_MRE") or 0) / 10000
                rqye = (row.get("RQYE") or 0) / 10000
                rqmrl = (row.get("RQMRL") or 0) / 10000
                rzrq_jme = (row.get("RZRQ_JME") or 0) / 10000
                lines.append(
                    f"- {trade_date} | 融资余额 {rzrq_ye:.1f}万 | 融资买入 {rzrq_mre:.1f}万 | "
                    f"融券余额 {rqye:.1f}万 | 融券卖出 {rqmrl:.1f}万 | 融资融券净买 {rzrq_jme:.1f}万"
                )
            return "\n".join(lines)

        except Exception as exc:
            return f"{symbol} [DATA-010] MARGIN_FAILED: 融资融券数据获取失败（Eastmoney datacenter）：{type(exc).__name__}: {exc}"

    def get_zt_pool(self, date: str) -> str:
        """涨停池 — 东财 datacenter 直连 fallback.  # [DATA-015] limit_up_pool_fallback"""
        try:
            _rate_limit("em_zt_pool")
            zt_filter = (
                f"(TRADE_DATE='{'-'.join([date[:4], date[4:6], date[6:8]]) if len(date) == 8 else date}')"
            )
            data = _eastmoney_datacenter(
                "RPTA_WEB_ZTZS_ZTPOOL",
                filter_str=zt_filter,
                page_size=200,
                sort_columns="CHANGE_RATE",
                sort_types="-1",
            )

            if not data:
                try:
                    _rate_limit("em_zt_pool_push2")
                    url = "https://push2ex.eastmoney.com/getTopicZTPool"
                    params = {
                        "ut": "7eea3edcaed734bea9tele",
                        "dpt": "wz.ztzt",
                        "Ession": date.replace("-", ""),
                        "date": date.replace("-", ""),
                    }
                    headers = {"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"}
                    r = requests.get(url, params=params, headers=headers, timeout=15, proxies=NO_PROXY)
                    d = r.json()
                    pool = d.get("data", {}).get("pool", [])
                    if not pool:
                        return f"{date} [DATA-015] ZT_POOL_NORMAL_NO_DATA: 当日无涨停股票（非交易日或盘后未更新）。"
                    count = len(pool)
                    lines = [f"{date} [DATA-015] ZT_POOL_HAS_DATA: 涨停池（Eastmoney push2ex，共 {count} 只）："]
                    for item in pool[:30]:
                        code = item.get("c", "")
                        name = item.get("n", "")
                        pct = item.get("zdp", "")
                        lb = item.get("lbc", "")
                        lines.append(f"- {code} {name} | 涨跌幅 {pct}% | 连板 {lb}")
                    return "\n".join(lines)
                except Exception as inner_exc:
                    return f"{date} [DATA-015] ZT_POOL_FAILED: 涨停池数据获取失败（Eastmoney push2ex fallback）：{type(inner_exc).__name__}: {inner_exc}"

            count = len(data)
            lines = [f"{date} [DATA-015] ZT_POOL_HAS_DATA: 涨停池（Eastmoney datacenter，共 {count} 只）："]
            for row in data[:30]:
                code = row.get("SECURITY_CODE", "")
                name = row.get("SECURITY_NAME_ABBR", "")
                reason = row.get("EXPLANATION", "")
                change_pct = round(float(row.get("CHANGE_RATE") or 0), 2)
                lines.append(f"- {code} {name} | {reason} | 涨跌 {change_pct}%")
            return "\n".join(lines)

        except Exception as exc:
            return f"{date} [DATA-015] ZT_POOL_FAILED: 涨停池数据获取失败（Eastmoney）：{type(exc).__name__}: {exc}"

    def get_buybacks(self, symbol: str) -> str:
        """个股回购计划/进展 — 东财 datacenter.  # [DATA-013] buyback_raw_evidence"""
        code = _extract_code(symbol)
        try:
            _rate_limit("em_buyback")
            buyback_filter = (
                f'(SECURITY_CODE="{code}")'
            )
            data = _eastmoney_datacenter(
                "RPT_SHAREBUYBACK_DET",
                filter_str=buyback_filter,
                page_size=20,
                sort_columns="NOTICE_DATE",
                sort_types="-1",
            )
            if not data:
                return f"{symbol} [DATA-013] BUYBACK_NORMAL_NO_DATA: 该股无回购计划或进展数据。"

            lines = [f"{symbol} [DATA-013] BUYBACK_HAS_DATA: 回购数据（Eastmoney datacenter）："]
            for row in data[:15]:
                notice_date = row.get("NOTICE_DATE", "")[:10] if row.get("NOTICE_DATE") else ""
                buyback_amount = (row.get("BUYBACK_AMOUNT") or 0) / 10000
                buyback_volume = row.get("BUYBACK_VOLUME", 0)
                progress = row.get("PROGRESS", "")
                purpose = row.get("PURPOSE", "")
                line = f"- {notice_date}"
                if buyback_amount:
                    line += f" | 金额: {buyback_amount:.1f}万"
                if buyback_volume:
                    line += f" | 数量: {buyback_volume}"
                if progress:
                    line += f" | 进度: {progress}"
                if purpose:
                    line += f" | 目的: {str(purpose)[:40]}"
                lines.append(line)
            return "\n".join(lines)

        except Exception as exc:
            return f"{symbol} [DATA-013] BUYBACK_FAILED: 回购数据获取失败（Eastmoney datacenter）：{type(exc).__name__}: {exc}"
