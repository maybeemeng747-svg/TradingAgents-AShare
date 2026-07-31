"""Independent Eastmoney financial-statement provider for A shares."""

from __future__ import annotations

from datetime import date
from typing import Any

import requests

DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://data.eastmoney.com/",
}
_REPORTS = {
    "income": "RPT_DMSK_FN_INCOME",
    "cashflow": "RPT_DMSK_FN_CASHFLOW",
    "balance": "RPT_DMSK_FN_BALANCE",
}
_PAGE_SIZE = 100
_MAX_PAGES = 100
_COLUMNS = {
    "income": (
        ("NOTICE_DATE", "来源公告日期"),
        ("REPORT_DATE", "报告日"),
        ("TOTAL_OPERATE_INCOME", "营业总收入"),
        ("OPERATE_INCOME", "营业收入"),
        ("TOTAL_OPERATE_COST", "营业总成本"),
        ("OPERATE_COST", "营业成本"),
        ("OPERATE_PROFIT", "营业利润"),
        ("TOTAL_PROFIT", "利润总额"),
        ("INCOME_TAX", "所得税费用"),
        ("PARENT_NETPROFIT", "归属于母公司股东的净利润"),
        ("DEDUCT_PARENT_NETPROFIT", "扣除非经常性损益后的净利润"),
    ),
    "cashflow": (
        ("NOTICE_DATE", "来源公告日期"),
        ("REPORT_DATE", "报告日"),
        ("NETCASH_OPERATE", "经营活动产生的现金流量净额"),
        ("NETCASH_INVEST", "投资活动产生的现金流量净额"),
        ("NETCASH_FINANCE", "筹资活动产生的现金流量净额"),
    ),
    "balance": (
        ("NOTICE_DATE", "来源公告日期"),
        ("REPORT_DATE", "报告日"),
        ("TOTAL_ASSETS", "资产总计"),
        ("TOTAL_LIABILITIES", "负债合计"),
        ("TOTAL_EQUITY", "所有者权益合计"),
        ("ACCOUNTS_RECE", "应收账款"),
        ("INVENTORY", "存货"),
        ("FIXED_ASSET", "固定资产"),
    ),
}


def _code(symbol: str) -> str:
    normalized = symbol.strip().upper()
    parts = normalized.split(".", 1)
    bare = parts[0]
    if len(bare) != 6 or not bare.isdigit():
        raise ValueError(f"invalid A-share symbol: {symbol}")
    expected_suffix = (
        "SH"
        if bare.startswith("6")
        else "SZ"
        if bare.startswith(("0", "3"))
        else "BJ"
        if bare.startswith(("4", "8", "92"))
        else None
    )
    if expected_suffix is None:
        raise ValueError(f"unsupported A-share symbol: {symbol}")
    if len(parts) == 2 and parts[1] != expected_suffix:
        raise ValueError(
            f"A-share symbol exchange suffix mismatch: {symbol}; "
            f"expected .{expected_suffix}"
        )
    return bare


def _date_only(value: Any) -> str:
    text = str(value or "").strip()
    return text[:10]


def _format_value(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, float):
        return format(value, ".15g")
    return str(value)


def _markdown(rows: list[dict[str, Any]], columns: tuple[tuple[str, str], ...]) -> str:
    headers = [label for _key, label in columns]
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _header in headers) + "|",
    ]
    for row in rows:
        values: list[str] = []
        for key, _label in columns:
            value = (
                _date_only(row.get(key))
                if key in {"REPORT_DATE", "NOTICE_DATE"}
                else row.get(key)
            )
            values.append(_format_value(value))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


class CnEastmoneyFinancialProvider:
    """Read-only Eastmoney adapter limited to company identity and three statements."""

    name = "cn_eastmoney_financial"
    financial_source_id = "eastmoney_datacenter"

    def __init__(self, *, observed_on: date | None = None):
        self._observed_on = observed_on or date.today()

    def _fetch(self, symbol: str, report_type: str) -> list[dict[str, Any]]:
        code = _code(symbol)
        rows: list[dict[str, Any]] = []
        page_number = 1
        total_pages = 1
        while page_number <= total_pages:
            response = requests.get(
                DATACENTER_URL,
                params={
                    "reportName": _REPORTS[report_type],
                    "columns": "ALL",
                    "filter": f'(SECURITY_CODE="{code}")',
                    "pageNumber": str(page_number),
                    "pageSize": str(_PAGE_SIZE),
                    "sortColumns": "REPORT_DATE",
                    "sortTypes": "-1",
                    "source": "WEB",
                    "client": "WEB",
                },
                headers=_HEADERS,
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("success") is not True:
                message = payload.get("message") if isinstance(payload, dict) else None
                raise RuntimeError(f"Eastmoney financial API failed: {message}")
            if "result" not in payload or payload.get("result") is None:
                raise RuntimeError(
                    "Eastmoney financial API returned missing result"
                )
            result = payload["result"]
            if not isinstance(result, dict):
                raise RuntimeError("Eastmoney financial API returned invalid result")
            if "data" not in result or result.get("data") is None:
                raise RuntimeError("Eastmoney financial API returned missing rows")
            page_rows = result["data"]
            if not isinstance(page_rows, list):
                raise RuntimeError("Eastmoney financial API returned invalid rows")
            for row in page_rows:
                if not isinstance(row, dict):
                    raise RuntimeError(
                        "Eastmoney financial API returned malformed row"
                    )
                returned_code = str(row.get("SECURITY_CODE") or "").strip()
                if returned_code != code:
                    raise RuntimeError(
                        "Eastmoney financial API issuer mismatch: "
                        f"requested={code}, returned={returned_code or 'missing'}"
                    )
                rows.append(row)

            if "pages" not in result or result.get("pages") is None:
                raise RuntimeError(
                    "Eastmoney financial API returned missing page count"
                )
            raw_pages = result["pages"]
            try:
                total_pages = int(raw_pages)
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    "Eastmoney financial API returned invalid page count"
                ) from exc
            if total_pages < 1 or total_pages > _MAX_PAGES:
                raise RuntimeError(
                    f"Eastmoney financial API page count out of range: {total_pages}"
                )
            page_number += 1
        return rows

    def _assert_replay_supported(self, curr_date: str | None) -> None:
        if curr_date and date.fromisoformat(curr_date) < self._observed_on:
            raise RuntimeError(
                "Eastmoney live financial API cannot establish a historical "
                "payload version; use an archived immutable fact bundle"
            )

    @staticmethod
    def _filter_as_of(
        rows: list[dict[str, Any]], curr_date: str | None
    ) -> list[dict[str, Any]]:
        if not curr_date:
            return rows
        cutoff = date.fromisoformat(curr_date)
        visible: list[dict[str, Any]] = []
        for row in rows:
            available_date = _date_only(row.get("NOTICE_DATE"))
            if not available_date:
                raise RuntimeError(
                    "Eastmoney financial row missing NOTICE_DATE; "
                    "cannot establish historical availability"
                )
            try:
                visible_date = date.fromisoformat(available_date)
            except ValueError as exc:
                raise RuntimeError(
                    "Eastmoney financial row has invalid NOTICE_DATE; "
                    "cannot establish historical availability"
                ) from exc
            if visible_date <= cutoff:
                visible.append(row)
        return visible

    def get_fundamentals(self, ticker: str, curr_date: str | None = None) -> str:
        self._assert_replay_supported(curr_date)
        rows = self._filter_as_of(self._fetch(ticker, "income"), curr_date)
        if not rows:
            return f"No fundamentals found for {ticker}"
        row = rows[0]
        return "\n".join(
            [
                "### Company Profile",
                "| 项目 | 内容 |",
                "|---|---|",
                f"| 股票简称 | {row.get('SECURITY_NAME_ABBR') or ''} |",
                f"| 股票代码 | {row.get('SECURITY_CODE') or _code(ticker)} |",
                f"| 主营业务 | |",
                f"| 所属行业 | {row.get('INDUSTRY_NAME') or ''} |",
            ]
        )

    def _statement(
        self,
        ticker: str,
        report_type: str,
        curr_date: str | None,
    ) -> str:
        self._assert_replay_supported(curr_date)
        rows = self._filter_as_of(self._fetch(ticker, report_type), curr_date)
        if not rows:
            return f"No {report_type} data found for {ticker}"
        return _markdown(rows, _COLUMNS[report_type])

    def get_income_statement(
        self, ticker: str, freq: str = "quarterly", curr_date: str | None = None
    ) -> str:
        return self._statement(ticker, "income", curr_date)

    def get_cashflow(
        self, ticker: str, freq: str = "quarterly", curr_date: str | None = None
    ) -> str:
        return self._statement(ticker, "cashflow", curr_date)

    def get_balance_sheet(
        self, ticker: str, freq: str = "quarterly", curr_date: str | None = None
    ) -> str:
        return self._statement(ticker, "balance", curr_date)
