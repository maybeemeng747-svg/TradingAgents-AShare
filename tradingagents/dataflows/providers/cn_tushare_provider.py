"""Tushare Pro provider for authenticated A-share structured data.

The provider is deliberately strongest on company identity and financial
statements.  Historical prices and market microstructure are exposed as
fallbacks, while real-time quotes, news and announcement text remain owned by
the existing providers.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable

import pandas as pd

from .base import BaseMarketDataProvider


_TUSHARE_CALL_LOCK = threading.Lock()
_DATE_RE = re.compile(r"^\d{8}$")
_REPURCHASE_ROW_LIMIT = 2000


def _date_digits(value: str | None, *, default: str | None = None) -> str:
    text = str(value or default or "").strip().replace("-", "").replace("/", "")
    if not _DATE_RE.fullmatch(text):
        raise ValueError(f"invalid date: {value!r}")
    return text


def _date_iso(value: Any) -> str:
    text = str(value or "").strip().replace("-", "").replace("/", "")
    if not _DATE_RE.fullmatch(text):
        return str(value or "")
    return f"{text[:4]}-{text[4:6]}-{text[6:]}"


def _date_windows(start: str, end: str, *, max_days: int = 180) -> list[tuple[str, str]]:
    """Split an inclusive date range into bounded upstream query windows."""
    if max_days < 1:
        raise ValueError("max_days must be positive")
    cursor = datetime.strptime(_date_digits(start), "%Y%m%d")
    end_date = datetime.strptime(_date_digits(end), "%Y%m%d")
    if cursor > end_date:
        raise ValueError("start date must not be after end date")
    windows: list[tuple[str, str]] = []
    while cursor <= end_date:
        window_end = min(cursor + timedelta(days=max_days - 1), end_date)
        windows.append((cursor.strftime("%Y%m%d"), window_end.strftime("%Y%m%d")))
        cursor = window_end + timedelta(days=1)
    return windows


def _normalize_ts_code(symbol: str) -> str:
    text = str(symbol or "").strip().upper()
    match = re.fullmatch(r"(\d{6})(?:\.(SH|SZ|BJ))?", text)
    if not match:
        raise ValueError(f"invalid A-share symbol: {symbol!r}")
    code, suffix = match.groups()
    expected_suffix = None
    if code.startswith(("60", "68", "90")):
        expected_suffix = "SH"
    elif code.startswith(("00", "30", "20")):
        expected_suffix = "SZ"
    elif code.startswith(("43", "82", "83", "87", "88", "92")):
        expected_suffix = "BJ"
    if expected_suffix is None:
        raise ValueError(f"cannot infer exchange for A-share symbol: {symbol!r}")
    if suffix is not None and suffix != expected_suffix:
        raise ValueError(
            f"exchange suffix {suffix} conflicts with A-share code {code}"
        )
    suffix = expected_suffix
    return f"{code}.{suffix}"


def _present(value: Any) -> bool:
    if value is None:
        return False
    try:
        if bool(pd.isna(value)):
            return False
    except (TypeError, ValueError):
        pass
    text = str(value).strip().lower()
    return bool(text and text not in {"nan", "none", "null", "-", "--", "—"})


def _safe_error(exc: Exception, *secrets: str) -> str:
    message = str(exc)
    candidates = {os.getenv("TUSHARE_TOKEN", "").strip()}
    candidates.update(str(secret or "").strip() for secret in secrets)
    for secret in candidates:
        if secret:
            message = message.replace(secret, "[REDACTED]")
    return f"{type(exc).__name__}: {message[:300]}"


class CnTushareProvider(BaseMarketDataProvider):
    """Authenticated Tushare Pro adapter with deterministic as-of filtering."""

    _cache_ttl_seconds = 300.0

    def __init__(
        self,
        token: str | None = None,
        *,
        query_fn: Callable[..., pd.DataFrame] | None = None,
    ) -> None:
        self._token = (token or os.getenv("TUSHARE_TOKEN", "")).strip()
        self._query_fn = query_fn
        self._client = None
        self._cache: dict[tuple[Any, ...], tuple[float, pd.DataFrame]] = {}

    @property
    def name(self) -> str:
        return "cn_tushare"

    @property
    def configured(self) -> bool:
        return bool(self._token or self._query_fn)

    def _pro(self):
        if self._client is not None:
            return self._client
        if not self._token:
            raise NotImplementedError("cn_tushare requires TUSHARE_TOKEN")
        try:
            import tushare as ts
        except ImportError as exc:
            raise NotImplementedError("cn_tushare requires the tushare package") from exc
        self._client = ts.pro_api(self._token)
        return self._client

    def _query(self, endpoint: str, **kwargs: Any) -> pd.DataFrame:
        key = (endpoint, tuple(sorted((name, str(value)) for name, value in kwargs.items())))
        cached = self._cache.get(key)
        now = time.monotonic()
        if cached and now - cached[0] <= self._cache_ttl_seconds:
            return cached[1].copy()

        with _TUSHARE_CALL_LOCK:
            try:
                if self._query_fn is not None:
                    frame = self._query_fn(endpoint, **kwargs)
                else:
                    frame = self._pro().query(endpoint, **kwargs)
            except Exception as exc:
                raise RuntimeError(_safe_error(exc, self._token)) from None
        if frame is None:
            frame = pd.DataFrame()
        if not isinstance(frame, pd.DataFrame):
            frame = pd.DataFrame(frame)
        self._cache[key] = (now, frame.copy())
        return frame

    def _optional_query(
        self,
        endpoint: str,
        *,
        unavailable: list[str] | None = None,
        **kwargs: Any,
    ) -> pd.DataFrame:
        """Return an empty frame when an enrichment endpoint is unavailable.

        Identity and statement routing must not fail only because an optional
        valuation, indicator or forecast endpoint is temporarily gated.
        Required identity and statement endpoints use ``_query`` directly so
        the normal provider fallback chain can handle their failures.
        """
        try:
            return self._query(endpoint, **kwargs)
        except Exception:
            if unavailable is not None:
                unavailable.append(endpoint)
            return pd.DataFrame()

    @staticmethod
    def _filter_as_of(frame: pd.DataFrame, as_of: str) -> pd.DataFrame:
        if frame.empty:
            return frame.copy()
        result = frame.copy()
        effective = None
        for column in ("f_ann_date", "ann_date", "trade_date", "report_date"):
            if column not in result.columns:
                continue
            values = result[column].astype(str).str.replace(r"\D", "", regex=True)
            effective = values if effective is None else effective.where(effective.str.len() == 8, values)
        if effective is None:
            return result
        return result[effective.str.len().eq(8) & effective.le(as_of)].copy()

    @classmethod
    def _statement_rows(cls, frame: pd.DataFrame, as_of: str) -> pd.DataFrame:
        result = cls._filter_as_of(frame, as_of)
        if result.empty:
            return result
        if "report_type" in result.columns:
            report_type = result["report_type"].astype(str)
            # Tushare type 1 is the original consolidated cumulative report;
            # type 4 is its adjusted consolidated revision. Single-quarter
            # and parent-only rows must not enter cumulative-statement logic.
            consolidated = result[report_type.isin({"1", "4"})]
            if consolidated.empty:
                return result.iloc[0:0].copy()
            result = consolidated
        result["_effective_date"] = ""
        for column in ("f_ann_date", "ann_date"):
            if column in result.columns:
                values = result[column].fillna("").astype(str).str.replace(r"\D", "", regex=True)
                result["_effective_date"] = result["_effective_date"].where(
                    result["_effective_date"].str.len().eq(8), values
                )
        if "update_flag" not in result.columns:
            result["update_flag"] = "0"
        result["_update_rank"] = pd.to_numeric(result["update_flag"], errors="coerce").fillna(0)
        result["_report_type_rank"] = (
            result.get("report_type", pd.Series(index=result.index, dtype=object))
            .astype(str)
            .map({"4": 1, "1": 0})
            .fillna(-1)
        )
        result = result.sort_values(
            ["_effective_date", "_update_rank", "_report_type_rank"],
            ascending=[False, False, False],
        )
        if "end_date" in result.columns:
            result = result.drop_duplicates(subset=["end_date"], keep="first")
        return result.drop(
            columns=["_effective_date", "_update_rank", "_report_type_rank"],
            errors="ignore",
        )

    @staticmethod
    def _rename_and_select(frame: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
        columns = [column for column in mapping if column in frame.columns]
        if not columns:
            return pd.DataFrame()
        result = frame[columns].rename(columns=mapping).copy()
        for column in (
            "Date",
            "公告日期",
            "实际公告日期",
            "报告日",
            "交易日期",
            "预披露日期",
            "实际披露日期",
        ):
            if column in result.columns:
                result[column] = result[column].map(_date_iso)
        return result

    @staticmethod
    def _markdown(title: str, frame: pd.DataFrame, *, endpoint: str, as_of: str, unit: str) -> str:
        if frame.empty:
            raise NotImplementedError(f"cn_tushare {endpoint} returned no data as of {as_of}")
        return (
            f"## {title}\n\n"
            f"<!-- source=tushare endpoint={endpoint} as_of={_date_iso(as_of)} unit={unit} -->\n\n"
            + frame.to_markdown(index=False)
        )

    def get_stock_data(self, symbol: str, start_date: str, end_date: str) -> str:
        ts_code = _normalize_ts_code(symbol)
        start = _date_digits(start_date)
        end = _date_digits(end_date)
        daily = self._query("daily", ts_code=ts_code, start_date=start, end_date=end)
        if daily.empty:
            raise NotImplementedError(f"cn_tushare daily returned no data for {ts_code}")
        factors = self._query("adj_factor", ts_code=ts_code, start_date=start, end_date=end)
        if factors.empty or not {"trade_date", "adj_factor"}.issubset(factors.columns):
            raise NotImplementedError(
                f"cn_tushare adjustment factors unavailable for {ts_code}"
            )
        result = daily.copy()
        result = result.merge(
            factors[["trade_date", "adj_factor"]], on="trade_date", how="left"
        )
        factor = pd.to_numeric(result["adj_factor"], errors="coerce")
        if factor.isna().any() or (factor <= 0).any():
            raise NotImplementedError(
                f"cn_tushare adjustment factors incomplete for {ts_code}"
            )
        latest_index = result["trade_date"].astype(str).idxmax()
        latest_factor = factor.loc[latest_index]
        for column in ("open", "high", "low", "close", "pre_close"):
            if column in result.columns:
                result[column] = (
                    pd.to_numeric(result[column], errors="coerce")
                    * factor
                    / latest_factor
                )
        adjustment = "qfq_as_of_end_date"
        # Tushare daily uses lots (100 shares) and thousand CNY.  Convert here
        # so every OHLCV provider exposes the project's canonical units.
        if "vol" in result.columns:
            result["vol"] = pd.to_numeric(result["vol"], errors="coerce") * 100
        if "amount" in result.columns:
            result["amount"] = pd.to_numeric(result["amount"], errors="coerce") * 1000
        result = result.sort_values("trade_date")
        output = self._rename_and_select(
            result,
            {
                "trade_date": "Date",
                "open": "Open",
                "high": "High",
                "low": "Low",
                "close": "Close",
                "vol": "Volume",
                "amount": "Amount",
            },
        )
        output["Dividends"] = 0.0
        output["Stock Splits"] = 0.0
        return (
            f"# Stock data for {ts_code} from {_date_iso(start)} to {_date_iso(end)}\n"
            f"# Total records: {len(output)}\n"
            f"# [DATA-P0-603629] adjustment={adjustment}\n"
            "# source=tushare endpoints=daily,adj_factor "
            "volume_unit=shares amount_unit=CNY source_volume_unit=100_shares "
            "source_amount_unit=thousand_CNY\n\n"
            + output.to_csv(index=False)
        )

    def get_indicators(
        self, symbol: str, indicator: str, curr_date: str, look_back_days: int
    ) -> str:
        raise NotImplementedError("cn_tushare delegates technical indicators to local OHLCV calculation")

    def get_fundamentals(self, ticker: str, curr_date: str = None) -> str:
        ts_code = _normalize_ts_code(ticker)
        as_of = _date_digits(curr_date, default=datetime.now().strftime("%Y%m%d"))
        parts = [f"## Fundamentals for {ts_code}"]
        optional_unavailable: list[str] = []

        basic = self._query(
            "stock_basic",
            ts_code=ts_code,
            fields="ts_code,symbol,name,area,industry,market,exchange,list_status,list_date",
        )
        company = self._query(
            "stock_company",
            ts_code=ts_code,
            fields=(
                "ts_code,exchange,province,city,setup_date,employees,introduction,"
                "main_business,business_scope"
            ),
        )
        if basic.empty or company.empty:
            raise NotImplementedError(
                f"cn_tushare company identity unavailable for {ts_code}"
            )
        list_dates = (
            basic["list_date"].astype(str).str.replace(r"\D", "", regex=True)
            if "list_date" in basic.columns
            else pd.Series(dtype=str)
        )
        if not list_dates.empty and not list_dates.str.len().eq(8).any():
            raise NotImplementedError(
                f"cn_tushare company identity lacks a valid list date for {ts_code}"
            )
        if not list_dates.empty and list_dates.str.len().eq(8).any():
            first_list_date = list_dates[list_dates.str.len().eq(8)].min()
            if as_of < first_list_date:
                raise NotImplementedError(
                    f"cn_tushare company identity unavailable before listing for {ts_code}"
                )
        today = datetime.now().strftime("%Y%m%d")
        if as_of < today:
            raise NotImplementedError(
                "cn_tushare stock_basic/stock_company are current snapshots and cannot "
                f"prove historical company identity as of {_date_iso(as_of)}"
            )
        profile = basic
        if (
            not basic.empty
            and not company.empty
            and {"ts_code", "exchange"}.issubset(basic.columns)
            and {"ts_code", "exchange"}.issubset(company.columns)
        ):
            profile = basic.merge(company, on=["ts_code", "exchange"], how="left")
        if not profile.empty:
            profile = self._rename_and_select(
                profile,
                {
                    "ts_code": "股票代码",
                    "name": "股票简称",
                    "industry": "所属行业",
                    "market": "市场板块",
                    "exchange": "交易所",
                    "list_status": "上市状态",
                    "list_date": "上市日期",
                    "province": "省份",
                    "city": "城市",
                    "main_business": "主营业务",
                    "business_scope": "经营范围",
                    "introduction": "公司介绍",
                },
            )
            for column in profile.select_dtypes(include="object").columns:
                profile[column] = profile[column].astype(str).str.slice(0, 500)
            # The instrument identity gate consumes the project's established
            # key/value Company Profile contract. A horizontal one-row table
            # would make its first header cell look like the security code.
            profile_row = profile.iloc[0].to_dict()
            profile_table = pd.DataFrame(
                [
                    {"item": label, "value": value}
                    for label, value in profile_row.items()
                    if _present(value)
                ]
            )
            parts.extend(
                [
                    "### Company Profile (Tushare Pro)",
                    "<!-- source=tushare endpoints=stock_basic,stock_company identity_contract=code_name_industry_exchange -->",
                    profile_table.to_markdown(index=False),
                ]
            )

        lookback = (datetime.strptime(as_of, "%Y%m%d") - timedelta(days=45)).strftime("%Y%m%d")
        daily_basic = self._filter_as_of(
            self._optional_query(
                "daily_basic",
                unavailable=optional_unavailable,
                ts_code=ts_code,
                start_date=lookback,
                end_date=as_of,
                fields=(
                    "ts_code,trade_date,close,turnover_rate,volume_ratio,pe,pe_ttm,pb,"
                    "ps_ttm,total_share,float_share,total_mv,circ_mv"
                ),
            ),
            as_of,
        )
        if not daily_basic.empty:
            valuation = self._rename_and_select(
                daily_basic.sort_values("trade_date", ascending=False).head(1),
                {
                    "ts_code": "股票代码",
                    "trade_date": "交易日期",
                    "close": "收盘价(元)",
                    "turnover_rate": "换手率(%)",
                    "volume_ratio": "量比",
                    "pe": "市盈率",
                    "pe_ttm": "市盈率TTM",
                    "pb": "市净率",
                    "ps_ttm": "市销率TTM",
                    "total_share": "总股本(万股)",
                    "float_share": "流通股本(万股)",
                    "total_mv": "总市值(万元)",
                    "circ_mv": "流通市值(万元)",
                },
            )
            parts.extend(
                [
                    "### Valuation Snapshot (Tushare Pro)",
                    "<!-- source=tushare endpoint=daily_basic -->",
                    valuation.to_markdown(index=False),
                ]
            )

        start = (datetime.strptime(as_of, "%Y%m%d") - timedelta(days=1100)).strftime("%Y%m%d")
        indicators = self._statement_rows(
            self._optional_query(
                "fina_indicator",
                unavailable=optional_unavailable,
                ts_code=ts_code,
                start_date=start,
                end_date=as_of,
            ),
            as_of,
        )
        if not indicators.empty:
            metrics = self._rename_and_select(
                indicators.head(8),
                {
                    "ts_code": "股票代码",
                    "ann_date": "公告日期",
                    "end_date": "报告日",
                    "eps": "基本每股收益",
                    "profit_dedt": "扣非净利润",
                    "grossprofit_margin": "毛利率(%)",
                    "netprofit_margin": "净利率(%)",
                    "roe": "ROE(%)",
                    "roe_dt": "扣非ROE(%)",
                    "roa": "ROA(%)",
                    "debt_to_assets": "资产负债率(%)",
                    "current_ratio": "流动比率",
                    "quick_ratio": "速动比率",
                    "ocf_to_np": "经营现金流/净利润",
                    "q_sales_yoy": "单季度营收同比(%)",
                    "q_profit_yoy": "单季度净利润同比(%)",
                },
            )
            parts.extend(
                [
                    "### Financial Indicators (Tushare Pro)",
                    "<!-- source=tushare endpoint=fina_indicator -->",
                    metrics.to_markdown(index=False),
                ]
            )

        forecast = self._filter_as_of(
            self._optional_query(
                "forecast",
                unavailable=optional_unavailable,
                ts_code=ts_code,
                start_date=start,
                end_date=as_of,
            ),
            as_of,
        )
        if not forecast.empty:
            forecast_table = self._rename_and_select(
                forecast.sort_values("ann_date", ascending=False).head(5),
                {
                    "ts_code": "股票代码",
                    "ann_date": "公告日期",
                    "end_date": "报告日",
                    "type": "预告类型",
                    "p_change_min": "净利润变动下限(%)",
                    "p_change_max": "净利润变动上限(%)",
                    "net_profit_min": "预计净利润下限(万元)",
                    "net_profit_max": "预计净利润上限(万元)",
                    "summary": "预告摘要",
                    "change_reason": "公司披露变动原因",
                },
            )
            for column in ("预告摘要", "公司披露变动原因"):
                if column in forecast_table.columns:
                    forecast_table[column] = forecast_table[column].astype(str).str.slice(0, 500)
            parts.extend(
                [
                    "### Official Performance Forecast (Tushare structured filing)",
                    "<!-- source=tushare endpoint=forecast company_explanation_priority=true -->",
                    forecast_table.to_markdown(index=False),
                ]
            )

        if optional_unavailable:
            endpoints = ",".join(dict.fromkeys(optional_unavailable))
            parts.append(
                "<!-- data_quality=PARTIAL optional_endpoint_status=UNAVAILABLE "
                f"optional_endpoints={endpoints} -->"
            )
        if len(parts) == 1:
            raise NotImplementedError(f"cn_tushare returned no fundamentals for {ts_code}")
        return "\n\n".join(parts)

    def _statement(
        self,
        endpoint: str,
        ticker: str,
        curr_date: str | None,
        mapping: dict[str, str],
        title: str,
    ) -> str:
        ts_code = _normalize_ts_code(ticker)
        as_of = _date_digits(curr_date, default=datetime.now().strftime("%Y%m%d"))
        start = (datetime.strptime(as_of, "%Y%m%d") - timedelta(days=1100)).strftime("%Y%m%d")
        frame = self._statement_rows(
            self._query(endpoint, ts_code=ts_code, start_date=start, end_date=as_of), as_of
        )
        if frame.empty:
            raise NotImplementedError(
                f"cn_tushare {endpoint} returned no data as of {as_of}"
            )
        metadata_columns = {
            "ts_code",
            "ann_date",
            "f_ann_date",
            "end_date",
            "report_type",
            "comp_type",
            "end_type",
            "update_flag",
        }
        metric_columns = [
            column
            for column in mapping
            if column not in metadata_columns and column in frame.columns
        ]
        has_metric = bool(metric_columns) and frame[metric_columns].apply(
            lambda column: column.map(_present).any()
        ).any()
        if not has_metric:
            raise NotImplementedError(
                f"cn_tushare {endpoint} returned metadata without accounting metrics as of {as_of}"
            )
        table = self._rename_and_select(frame.head(12), mapping)
        return self._markdown(title, table, endpoint=endpoint, as_of=as_of, unit="元")

    def get_income_statement(
        self, ticker: str, freq: str = "quarterly", curr_date: str = None
    ) -> str:
        return self._statement(
            "income",
            ticker,
            curr_date,
            {
                "ts_code": "证券代码",
                "ann_date": "公告日期",
                "f_ann_date": "实际公告日期",
                "end_date": "报告日",
                "report_type": "报告类型",
                "comp_type": "公司类型",
                "end_type": "期末类型",
                "total_revenue": "营业总收入",
                "revenue": "营业收入",
                "total_cogs": "营业总成本",
                "oper_cost": "营业成本",
                "operate_profit": "营业利润",
                "total_profit": "利润总额",
                "income_tax": "所得税费用",
                "n_income": "净利润",
                "n_income_attr_p": "归属于母公司股东的净利润",
                "basic_eps": "基本每股收益",
                "diluted_eps": "稀释每股收益",
                "rd_exp": "研发费用",
                "update_flag": "更新标记",
            },
            f"Income Statement ({_normalize_ts_code(ticker)})",
        )

    def get_balance_sheet(
        self, ticker: str, freq: str = "quarterly", curr_date: str = None
    ) -> str:
        return self._statement(
            "balancesheet",
            ticker,
            curr_date,
            {
                "ts_code": "证券代码",
                "ann_date": "公告日期",
                "f_ann_date": "实际公告日期",
                "end_date": "报告日",
                "report_type": "报告类型",
                "comp_type": "公司类型",
                "end_type": "期末类型",
                "money_cap": "货币资金",
                "accounts_receiv": "应收账款",
                "inventories": "存货",
                "total_cur_assets": "流动资产合计",
                "fix_assets_total": "固定资产合计",
                "use_right_assets": "使用权资产",
                "total_assets": "资产总计",
                "st_borr": "短期借款",
                "acct_payable": "应付账款",
                "contract_liab": "合同负债",
                "non_cur_liab_due_1y": "一年内到期的非流动负债",
                "total_cur_liab": "流动负债合计",
                "lt_borr": "长期借款",
                "bond_payable": "应付债券",
                "total_ncl": "非流动负债合计",
                "total_liab": "负债合计",
                "total_hldr_eqy_exc_min_int": "归属于母公司股东权益合计",
                "total_hldr_eqy_inc_min_int": "所有者权益合计",
                "update_flag": "更新标记",
            },
            f"Balance Sheet ({_normalize_ts_code(ticker)})",
        )

    def get_cashflow(
        self, ticker: str, freq: str = "quarterly", curr_date: str = None
    ) -> str:
        return self._statement(
            "cashflow",
            ticker,
            curr_date,
            {
                "ts_code": "证券代码",
                "ann_date": "公告日期",
                "f_ann_date": "实际公告日期",
                "end_date": "报告日",
                "report_type": "报告类型",
                "comp_type": "公司类型",
                "end_type": "期末类型",
                "c_fr_sale_sg": "销售商品、提供劳务收到的现金",
                "c_paid_goods_s": "购买商品、接受劳务支付的现金",
                "n_cashflow_act": "经营活动产生的现金流量净额",
                "n_cashflow_inv_act": "投资活动产生的现金流量净额",
                "n_cash_flows_fnc_act": "筹资活动产生的现金流量净额",
                "n_incr_cash_cash_equ": "现金及现金等价物净增加额",
                "c_cash_equ_end_period": "期末现金及现金等价物余额",
                "free_cashflow": "自由现金流",
                "update_flag": "更新标记",
            },
            f"Cashflow ({_normalize_ts_code(ticker)})",
        )

    def get_individual_fund_flow(self, symbol: str) -> str:
        ts_code = _normalize_ts_code(symbol)
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=45)).strftime("%Y%m%d")
        frame = self._filter_as_of(
            self._query("moneyflow", ts_code=ts_code, start_date=start, end_date=end), end
        )
        if frame.empty:
            return f"{ts_code} [DATA-TUSHARE] FUND_FLOW_NORMAL_NO_DATA: 近期无个股资金流记录。"
        frame = frame.copy()
        required = {"buy_lg_amount", "sell_lg_amount", "buy_elg_amount", "sell_elg_amount"}
        if not required.issubset(frame.columns):
            raise NotImplementedError(
                "cn_tushare moneyflow lacks large/extra-large order fields required for main-force flow"
            )
        components = frame[list(required)].apply(pd.to_numeric, errors="coerce")
        complete_rows = components.notna().all(axis=1)
        dropped_rows = int((~complete_rows).sum())
        frame = frame.loc[complete_rows].copy()
        components = components.loc[complete_rows]
        if frame.empty:
            raise NotImplementedError(
                "cn_tushare moneyflow has no rows with complete large/extra-large order amounts"
            )
        frame["main_force_net_amount"] = (
            components["buy_lg_amount"]
            - components["sell_lg_amount"]
            + components["buy_elg_amount"]
            - components["sell_elg_amount"]
        )
        table = self._rename_and_select(
            frame.sort_values("trade_date", ascending=False).head(20),
            {
                "trade_date": "日期",
                "main_force_net_amount": "主力净流入(大单+超大单,万元)",
                "net_mf_amount": "净流入额(L2总口径,万元)",
                "buy_sm_amount": "小单买入额",
                "sell_sm_amount": "小单卖出额",
                "buy_md_amount": "中单买入额",
                "sell_md_amount": "中单卖出额",
                "buy_lg_amount": "大单买入额",
                "sell_lg_amount": "大单卖出额",
                "buy_elg_amount": "超大单买入额",
                "sell_elg_amount": "超大单卖出额",
            },
        )
        return (
            f"{ts_code} [DATA-TUSHARE] FUND_FLOW_HAS_DATA: 近20个交易日个股资金流"
            "（Tushare moneyflow，金额单位：万元；非板块资金流）：\n"
            f"<!-- dropped_incomplete_rows={dropped_rows} -->\n"
            + table.to_markdown(index=False)
        )

    def get_lhb_detail(self, symbol: str, date: str, *, force: bool = False) -> str:
        ts_code = _normalize_ts_code(symbol)
        if not force:
            return (
                f"{ts_code} [G-007] LHB_NOT_QUERIED: 龙虎榜查询未触发（force=False）。"
                "龙虎榜仅在资金/事件异动时按需查询。"
            )
        query_date = _date_digits(date)
        try:
            frame = self._query("top_list", ts_code=ts_code, trade_date=query_date)
            if frame.empty:
                return f"{ts_code} [G-007] LHB_NORMAL_NO_DATA: 在 {_date_iso(query_date)} 无龙虎榜记录（非异动日属正常）。"
            frame = frame.copy()
            # Tushare top_list monetary fields are denominated in CNY. The
            # project standardizes LHB tables to ten-thousand CNY.
            for column in ("amount", "l_buy", "l_sell", "l_amount", "net_amount"):
                if column in frame.columns:
                    frame[column] = pd.to_numeric(frame[column], errors="coerce") / 10000
            table = self._rename_and_select(
                frame,
                {
                    "trade_date": "交易日期",
                    "ts_code": "证券代码",
                    "name": "证券简称",
                    "reason": "上榜原因",
                    "close": "收盘价(元)",
                    "pct_change": "涨跌幅(%)",
                    "turnover_rate": "换手率(%)",
                    "amount": "成交额(万元)",
                    "l_buy": "龙虎榜买入额(万元)",
                    "l_sell": "龙虎榜卖出额(万元)",
                    "net_amount": "龙虎榜净买入额(万元)",
                },
            )
            return f"{ts_code} [G-007] LHB_HAS_DATA: 龙虎榜明细（Tushare top_list）：\n{table.to_markdown(index=False)}"
        except Exception as exc:
            return f"{ts_code} [G-007] LHB_FAILED: 龙虎榜查询失败（Tushare）：{_safe_error(exc, self._token)}"

    def get_margin_trading(self, symbol: str) -> str:
        ts_code = _normalize_ts_code(symbol)
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=45)).strftime("%Y%m%d")
        try:
            frame = self._filter_as_of(
                self._query("margin_detail", ts_code=ts_code, start_date=start, end_date=end), end
            )
            if frame.empty:
                return f"{ts_code} [DATA-010] MARGIN_NORMAL_NO_DATA: 该股非融资融券标的或近期无记录。"
            table = self._rename_and_select(
                frame.sort_values("trade_date", ascending=False).head(20),
                {
                    "trade_date": "交易日期",
                    "ts_code": "证券代码",
                    "rzye": "融资余额(元)",
                    "rqye": "融券余额(元)",
                    "rzmre": "融资买入额(元)",
                    "rzche": "融资偿还额(元)",
                    "rqyl": "融券余量(股)",
                    "rqmcl": "融券卖出量(股)",
                    "rqchl": "融券偿还量(股)",
                    "rzrqye": "融资融券余额(元)",
                },
            )
            return f"{ts_code} [DATA-010] MARGIN_HAS_DATA: 融资融券明细（Tushare）：\n{table.to_markdown(index=False)}"
        except Exception as exc:
            return f"{ts_code} [DATA-010] MARGIN_FAILED: 融资融券查询失败（Tushare）：{_safe_error(exc, self._token)}"

    def get_buybacks(self, symbol: str) -> str:
        ts_code = _normalize_ts_code(symbol)
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=1100)).strftime("%Y%m%d")
        try:
            def query_complete_range(range_start: str, range_end: str) -> list[pd.DataFrame]:
                window_frame = self._filter_as_of(
                    self._query(
                        "repurchase",
                        start_date=range_start,
                        end_date=range_end,
                    ),
                    end,
                )
                if len(window_frame) < _REPURCHASE_ROW_LIMIT:
                    return [window_frame]
                start_dt = datetime.strptime(range_start, "%Y%m%d")
                end_dt = datetime.strptime(range_end, "%Y%m%d")
                if start_dt >= end_dt:
                    raise RuntimeError(
                        "cn_tushare repurchase response reached the row limit "
                        f"for single day {range_start}; completeness cannot be verified"
                    )
                midpoint = start_dt + (end_dt - start_dt) // 2
                right_start = midpoint + timedelta(days=1)
                return query_complete_range(
                    range_start, midpoint.strftime("%Y%m%d")
                ) + query_complete_range(
                    right_start.strftime("%Y%m%d"), range_end
                )

            symbol_frames: list[pd.DataFrame] = []
            for window_start, window_end in _date_windows(start, end):
                for window_frame in query_complete_range(window_start, window_end):
                    if window_frame.empty:
                        continue
                    if "ts_code" not in window_frame.columns:
                        raise NotImplementedError(
                            "cn_tushare repurchase response lacks ts_code for local symbol filtering"
                        )
                    matched = window_frame.loc[
                        window_frame["ts_code"].astype(str).str.upper().eq(ts_code)
                    ].copy()
                    if not matched.empty:
                        symbol_frames.append(matched)
            frame = (
                pd.concat(symbol_frames, ignore_index=True).drop_duplicates()
                if symbol_frames
                else pd.DataFrame()
            )
            if frame.empty:
                return f"{ts_code} [DATA-013] BUYBACK_NORMAL_NO_DATA: 无回购记录。"
            table = self._rename_and_select(
                frame.sort_values("ann_date", ascending=False).head(20),
                {
                    "ts_code": "证券代码",
                    "ann_date": "公告日期",
                    "end_date": "截止日期",
                    "proc": "实施进度",
                    "exp_date": "预计完成日期",
                    "vol": "回购数量(股)",
                    "amount": "回购金额(元)",
                    "high_limit": "回购最高价(元)",
                    "low_limit": "回购最低价(元)",
                },
            )
            return f"{ts_code} [DATA-013] BUYBACK_HAS_DATA: 回购记录（Tushare）：\n{table.to_markdown(index=False)}"
        except Exception as exc:
            return f"{ts_code} [DATA-013] BUYBACK_FAILED: 回购查询失败（Tushare）：{_safe_error(exc, self._token)}"

    def get_news(self, ticker: str, start_date: str, end_date: str) -> str:
        raise NotImplementedError("cn_tushare news permission is not enabled")

    def get_global_news(
        self, curr_date: str, look_back_days: int = 7, limit: int = 50
    ) -> str:
        raise NotImplementedError("cn_tushare news permission is not enabled")

    def get_insider_transactions(
        self, symbol: str, curr_date: str | None = None
    ) -> str:
        raise NotImplementedError("cn_tushare insider adapter is not part of the first integration")

    def get_realtime_quotes(self, symbols: list[str]) -> str:
        raise NotImplementedError("cn_tushare realtime permission is not enabled")
