"""CNInfo-only company identity provider for financial fact verification."""

from __future__ import annotations

import re

import pandas as pd

from tradingagents.dataflows.network_timeout import install_default_network_timeout


class CninfoIdentityProvider:
    """Read company identity from CNInfo without contributing financial facts."""

    name = "cn_cninfo_identity"
    financial_source_id = "cninfo_identity_only"
    identity_source_id = "cninfo"

    @staticmethod
    def _code(symbol: str) -> str:
        match = re.search(r"(\d{6})", str(symbol or ""))
        if not match:
            raise ValueError(f"invalid A-share symbol: {symbol}")
        return match.group(1)

    def get_fundamentals(self, ticker: str, curr_date: str | None = None) -> str:
        del curr_date
        try:
            import akshare as ak  # type: ignore
        except ImportError as exc:
            raise RuntimeError("AKShare is required for CNInfo identity") from exc

        code = self._code(ticker)
        install_default_network_timeout(20.0)
        frame = ak.stock_profile_cninfo(symbol=code)
        if frame is None or frame.empty:
            raise RuntimeError("CNInfo company profile returned no data")
        row = frame.iloc[0]
        values: dict[str, str] = {}
        missing_sentinels = {"", "nan", "none", "null", "nat", "<na>", "-", "—"}
        for column in frame.columns:
            raw_value = row[column]
            try:
                if bool(pd.isna(raw_value)):
                    continue
            except (TypeError, ValueError):
                continue
            text_value = str(raw_value).strip()
            if text_value.casefold() in missing_sentinels:
                continue
            values[str(column)] = text_value
        raw_returned_code = values.get("A股代码")
        if not raw_returned_code:
            raise RuntimeError("CNInfo company profile lacks returned A-share code")
        returned_code = self._code(raw_returned_code)
        if returned_code != code:
            return (
                "### Company Profile (巨潮资讯)\n"
                "| item | value |\n|---|---|\n"
                f"| 股票代码 | {returned_code} |\n"
                f"| 身份冲突 | requested={code}, returned={returned_code} |"
            )

        security_name = values.get("A股简称") or values.get("公司名称")
        if not security_name:
            raise RuntimeError("CNInfo company profile lacks security name")
        fields = (
            ("股票代码", raw_returned_code),
            ("股票简称", security_name),
            ("所属行业", values.get("所属行业")),
            ("主营业务", values.get("主营业务") or values.get("经营范围")),
        )
        rows = [f"| {label} | {value} |" for label, value in fields if value]
        return (
            "### Company Profile (巨潮资讯)\n"
            "| item | value |\n|---|---|\n" + "\n".join(rows)
        )

    def get_income_statement(
        self, ticker: str, freq: str = "quarterly", curr_date: str | None = None
    ) -> str:
        del ticker, freq, curr_date
        return "No income statement data from identity-only provider"

    def get_cashflow(
        self, ticker: str, freq: str = "quarterly", curr_date: str | None = None
    ) -> str:
        del ticker, freq, curr_date
        return "No cashflow data from identity-only provider"

    def get_balance_sheet(
        self, ticker: str, freq: str = "quarterly", curr_date: str | None = None
    ) -> str:
        del ticker, freq, curr_date
        return "No balance sheet data from identity-only provider"
