from __future__ import annotations

from datetime import date
from unittest.mock import Mock, patch

import pytest

from tradingagents.dataflows.providers.cn_eastmoney_financial_provider import (
    CnEastmoneyFinancialProvider,
)


def _response(rows, *, pages=1):
    response = Mock()
    response.raise_for_status.return_value = None
    for row in rows:
        if isinstance(row, dict):
            row.setdefault("SECURITY_CODE", "600487")
    response.json.return_value = {
        "success": True,
        "message": "ok",
        "result": {"data": rows, "pages": pages},
    }
    return response


def _payload_response(payload):
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = payload
    return response


def test_income_statement_maps_eastmoney_fields_to_normalized_markdown():
    rows = [
        {
            "SECURITY_CODE": "600487",
            "SECURITY_NAME_ABBR": "亨通光电",
            "INDUSTRY_NAME": "通信设备",
            "NOTICE_DATE": "2026-04-25 00:00:00",
            "REPORT_DATE": "2026-03-31 00:00:00",
            "TOTAL_OPERATE_INCOME": 17790560069.79,
            "PARENT_NETPROFIT": 1105364232.68,
            "DEDUCT_PARENT_NETPROFIT": 1000000000,
        }
    ]
    with patch("requests.get", return_value=_response(rows)) as request:
        provider = CnEastmoneyFinancialProvider(observed_on=date(2026, 7, 30))
        text = provider.get_income_statement("600487.SH", curr_date="2026-07-30")
    assert "2026-03-31" in text
    assert "2026-04-25" in text
    assert "营业总收入" in text
    assert "17790560069.79" in text
    assert "扣除非经常性损益后的净利润" in text
    assert request.call_args.kwargs["params"]["filter"] == '(SECURITY_CODE="600487")'


def test_as_of_filter_removes_future_report_period():
    rows = [
        {
            "NOTICE_DATE": "2026-09-15 00:00:00",
            "REPORT_DATE": "2026-08-31 00:00:00",
            "NETCASH_OPERATE": 10,
        },
        {
            "NOTICE_DATE": "2026-04-25 00:00:00",
            "REPORT_DATE": "2026-03-31 00:00:00",
            "NETCASH_OPERATE": 5,
        },
    ]
    with patch("requests.get", return_value=_response(rows)):
        text = CnEastmoneyFinancialProvider(
            observed_on=date(2026, 7, 30)
        ).get_cashflow(
            "600487.SH", curr_date="2026-07-30"
        )
    assert "2026-08-31" not in text
    assert "2026-03-31" in text


def test_live_endpoint_rejects_historical_as_of_even_with_old_notice_date():
    rows = [
        {
            "NOTICE_DATE": "2026-04-25 00:00:00",
            "REPORT_DATE": "2025-12-31 00:00:00",
            "TOTAL_OPERATE_INCOME": 10,
        }
    ]
    with patch("requests.get") as request:
        with pytest.raises(RuntimeError, match="archived immutable fact bundle"):
            CnEastmoneyFinancialProvider(
                observed_on=date(2026, 7, 30)
            ).get_income_statement("600487.SH", curr_date="2026-01-15")
    request.assert_not_called()


def test_fundamentals_exposes_identity_and_industry():
    rows = [
        {
            "SECURITY_CODE": "600487",
            "SECURITY_NAME_ABBR": "亨通光电",
            "INDUSTRY_NAME": "通信设备",
            "NOTICE_DATE": "2026-04-25 00:00:00",
            "REPORT_DATE": "2026-03-31 00:00:00",
        }
    ]
    with patch("requests.get", return_value=_response(rows)):
        text = CnEastmoneyFinancialProvider(
            observed_on=date(2026, 7, 30)
        ).get_fundamentals(
            "600487.SH", curr_date="2026-07-30"
        )
    assert "| 股票简称 | 亨通光电 |" in text
    assert "| 股票代码 | 600487 |" in text
    assert "| 所属行业 | 通信设备 |" in text


def test_invalid_symbol_is_rejected_before_network_call():
    with patch("requests.get") as request:
        with pytest.raises(ValueError, match="invalid A-share symbol"):
            CnEastmoneyFinancialProvider().get_income_statement("BAD")
    request.assert_not_called()


def test_exchange_suffix_mismatch_is_rejected_before_network_call():
    with patch("requests.get") as request:
        with pytest.raises(ValueError, match="exchange suffix mismatch"):
            CnEastmoneyFinancialProvider().get_income_statement("000001.SH")
    request.assert_not_called()


def test_rows_without_notice_date_fail_instead_of_looking_like_no_data():
    rows = [
        {
            "REPORT_DATE": "2026-03-31 00:00:00",
            "TOTAL_OPERATE_INCOME": 10,
        }
    ]
    with patch("requests.get", return_value=_response(rows)):
        with pytest.raises(RuntimeError, match="missing NOTICE_DATE"):
            CnEastmoneyFinancialProvider(
                observed_on=date(2026, 7, 30)
            ).get_income_statement(
                "600487.SH", curr_date="2026-07-30"
            )


def test_unsupported_b_share_prefix_is_rejected_before_network_call():
    with patch("requests.get") as request:
        with pytest.raises(ValueError, match="unsupported A-share symbol"):
            CnEastmoneyFinancialProvider().get_income_statement("900901.SH")
    request.assert_not_called()


def test_fetch_reads_all_pages_before_returning_rows():
    recent = {
        "NOTICE_DATE": "2026-04-25 00:00:00",
        "REPORT_DATE": "2026-03-31 00:00:00",
        "TOTAL_OPERATE_INCOME": 20,
    }
    historical = {
        "NOTICE_DATE": "2020-04-25 00:00:00",
        "REPORT_DATE": "2020-03-31 00:00:00",
        "TOTAL_OPERATE_INCOME": 10,
    }
    with patch(
        "requests.get",
        side_effect=[
            _response([recent], pages=2),
            _response([historical], pages=2),
        ],
    ) as request:
        rows = CnEastmoneyFinancialProvider(
            observed_on=date(2026, 7, 30)
        )._fetch("600487.SH", "income")

    assert rows == [recent, historical]
    assert [call.kwargs["params"]["pageNumber"] for call in request.call_args_list] == [
        "1",
        "2",
    ]


@pytest.mark.parametrize(
    "result",
    [
        None,
        {},
        {"data": None, "pages": 1},
    ],
)
def test_fetch_rejects_missing_result_or_rows(result):
    response = _payload_response(
        {"success": True, "message": "ok", "result": result}
    )
    with patch("requests.get", return_value=response):
        with pytest.raises(RuntimeError, match="missing (result|rows)"):
            CnEastmoneyFinancialProvider()._fetch("600487.SH", "income")


@pytest.mark.parametrize("pages", [None, 0, "bad"])
def test_fetch_rejects_missing_or_invalid_page_count(pages):
    row = {
        "SECURITY_CODE": "600487",
        "NOTICE_DATE": "2026-04-25 00:00:00",
        "REPORT_DATE": "2026-03-31 00:00:00",
        "TOTAL_OPERATE_INCOME": 20,
    }
    response = _payload_response(
        {
            "success": True,
            "message": "ok",
            "result": {"data": [row], "pages": pages},
        }
    )
    with patch("requests.get", return_value=response):
        with pytest.raises(RuntimeError, match="page count"):
            CnEastmoneyFinancialProvider()._fetch("600487.SH", "income")


@pytest.mark.parametrize("row", ["not-a-row", None, ["nested"]])
def test_fetch_rejects_malformed_rows(row):
    response = _payload_response(
        {
            "success": True,
            "message": "ok",
            "result": {"data": [row], "pages": 1},
        }
    )
    with patch("requests.get", return_value=response):
        with pytest.raises(RuntimeError, match="malformed row"):
            CnEastmoneyFinancialProvider()._fetch("600487.SH", "income")


@pytest.mark.parametrize("returned_code", [None, "", "000001"])
def test_fetch_rejects_missing_or_mismatched_issuer(returned_code):
    row = {
        "SECURITY_CODE": returned_code,
        "NOTICE_DATE": "2026-04-25 00:00:00",
        "REPORT_DATE": "2026-03-31 00:00:00",
        "TOTAL_OPERATE_INCOME": 20,
    }
    response = _payload_response(
        {
            "success": True,
            "message": "ok",
            "result": {"data": [row], "pages": 1},
        }
    )
    with patch("requests.get", return_value=response):
        with pytest.raises(RuntimeError, match="issuer mismatch"):
            CnEastmoneyFinancialProvider()._fetch("600487.SH", "income")
