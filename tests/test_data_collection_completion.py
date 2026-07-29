import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pandas as pd

from api.services.report_service import build_verified_financial_key_metrics
from tradingagents.agents.utils.readiness_score import (
    EvidenceStatus,
    infer_evidence_statuses,
)
from tradingagents.graph.data_collector import DataCollector, _EVIDENCE_KEY_TO_DATA_TYPE
from tradingagents.graph.data_collector import _should_fetch_realtime_quote


def test_research_report_and_buyback_tools_are_exported():
    from tradingagents.agents.utils.agent_utils import get_buybacks, get_research_report

    assert hasattr(get_research_report, "invoke")
    assert hasattr(get_buybacks, "invoke")


def test_collector_contract_includes_newly_queried_sources():
    assert _EVIDENCE_KEY_TO_DATA_TYPE["research_report"] == "report"
    assert _EVIDENCE_KEY_TO_DATA_TYPE["buybacks"] == "buyback"
    assert _EVIDENCE_KEY_TO_DATA_TYPE["realtime_quote"] == "realtime_quotes"

    collector = DataCollector()
    collector._cache["002409.SZ_2026-07-28"] = {
        "research_report": "002409.SZ [DATA-011] REPORT_HAS_DATA: 研报",
        "buybacks": "002409.SZ [DATA-013] BUYBACK_NORMAL_NO_DATA: 无回购",
        "realtime_quote": json.dumps(
            {
                "002409.SZ": {
                    "price": 166.43,
                    "turnover_rate": 18.26,
                    "volume_ratio": 1.24,
                }
            }
        ),
    }
    evidence = collector.build_raw_evidence("002409.SZ", "2026-07-28")

    assert evidence["research_report"]["status"] == "HAS_DATA"
    assert evidence["research_report"]["unit"] == "条"
    assert evidence["buybacks"]["status"] == "NORMAL_NO_DATA"
    assert evidence["realtime_quote"]["status"] == "HAS_DATA"

    statuses = infer_evidence_statuses({}, raw_evidence=evidence)
    assert statuses["turnover_rate"] == EvidenceStatus.HAS_DATA
    assert statuses["volume_ratio"] == EvidenceStatus.HAS_DATA


def test_realtime_quote_enrichment_preserves_primary_price():
    from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider

    primary = json.dumps(
        {"002409.SZ": {"price": 166.43, "source": "sina"}},
        ensure_ascii=False,
    )
    fallback = json.dumps(
        {
            "002409.SZ": {
                "price": 999.0,
                "turnover_rate": 18.26,
                "volume_ratio": 1.24,
                "source": "tencent",
            }
        },
        ensure_ascii=False,
    )
    with patch(
        "tradingagents.dataflows.providers.cn_astock_provider.CnAstockProvider.get_realtime_quotes",
        return_value=fallback,
    ):
        result = json.loads(
            CnAkshareProvider._enrich_realtime_quotes(primary, ["002409.SZ"])
        )

    quote = result["002409.SZ"]
    assert quote["price"] == 166.43
    assert quote["turnover_rate"] == 18.26
    assert quote["volume_ratio"] == 1.24
    assert quote["source"] == "sina+tencent"


def test_fund_flow_uses_independent_ths_fallback():
    from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider

    provider = CnAkshareProvider()
    fake_ak = MagicMock()
    fake_ak.stock_individual_fund_flow.side_effect = ConnectionError("eastmoney down")
    fake_ak.stock_fund_flow_individual.side_effect = [
        pd.DataFrame(
            {
                "股票代码": ["002409"],
                "股票简称": ["雅克科技"],
                "换手率": ["18.26%"],
                "净额": ["-3.60亿"],
            }
        ),
        pd.DataFrame(
            {
                "股票代码": ["002409"],
                "股票简称": ["雅克科技"],
                "连续换手率": ["73.72%"],
                "资金流入净额": ["-12.80亿"],
            }
        ),
    ]
    provider._fund_flow_rank_cache = {}
    with patch.object(provider, "_ak", return_value=fake_ak):
        text = provider.get_individual_fund_flow("002409.SZ")

    assert "FUND_FLOW_AGGREGATE_HAS_DATA" in text
    assert "非逐日20日序列" in text
    assert "雅克科技" in text


def test_akshare_primary_fund_flow_declares_yuan_unit():
    from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider

    provider = CnAkshareProvider()
    fake_ak = MagicMock()
    fake_ak.stock_individual_fund_flow.return_value = pd.DataFrame(
        {
            "日期": ["2026-07-28"],
            "主力净流入-净额": [-667867936.0],
        }
    )

    with patch.object(provider, "_ak", return_value=fake_ak):
        text = provider.get_individual_fund_flow("002409.SZ")

    assert "单位：元" in text
    assert "-667867936" in text


def test_fund_flow_contract_uses_explicit_unit_marker():
    collector = DataCollector()
    collector._cache["002409.SZ_2026-07-28"] = {
        "fund_flow_individual": (
            "002409.SZ 近20日主力资金净流向"
            "（AKShare stock_individual_fund_flow，单位：元）：\n"
            "2026-07-28 -667867936.0"
        )
    }

    with patch(
        "tradingagents.graph.data_collector.get_last_hit_vendor",
        return_value="cn_akshare",
    ):
        evidence = collector.build_raw_evidence("002409.SZ", "2026-07-28")

    assert evidence["fund_flow_individual"]["unit"] == "元"
    assert evidence["fund_flow_individual"]["unit_verified"] is True


def test_fund_flow_contract_without_unit_marker_fails_closed():
    collector = DataCollector()
    collector._cache["002409.SZ_2026-07-28"] = {
        "fund_flow_individual": (
            "002409.SZ 近20日主力资金净流向：\n"
            "2026-07-28 -667867936.0"
        )
    }

    evidence = collector.build_raw_evidence("002409.SZ", "2026-07-28")

    assert evidence["fund_flow_individual"]["unit"] is None
    assert evidence["fund_flow_individual"]["unit_verified"] is False


def test_stock_data_contract_exposes_partial_intraday_status():
    collector = DataCollector()
    collector._cache["002409.SZ_2026-07-29"] = {
        "stock_data": (
            "# Stock data for 002409.SZ from 2026-07-01 to 2026-07-29\n"
            "# [INTRADAY-DAYBAR] current_day_status=PARTIAL_INTRADAY, "
            "action=excluded_from_daily_ohlcv\n\n"
            "Date,Open,High,Low,Close,Volume\n"
            "2026-07-28,160,170,155,166,100000"
        )
    }

    evidence = collector.build_raw_evidence("002409.SZ", "2026-07-29")

    assert evidence["stock_data"]["current_day_status"] == "PARTIAL_INTRADAY"
    assert evidence["stock_data"]["is_realtime_patched"] is False


def test_empty_partial_intraday_stock_data_is_not_counted_as_available():
    collector = DataCollector()
    collector._cache["002409.SZ_2026-07-29"] = {
        "stock_data": (
            "# [INTRADAY-DAYBAR] current_day_status=PARTIAL_INTRADAY, "
            "action=excluded_from_daily_ohlcv\n"
            "No data found for symbol '002409.SZ' between "
            "2026-07-29 and 2026-07-29"
        )
    }

    evidence = collector.build_raw_evidence("002409.SZ", "2026-07-29")

    assert evidence["stock_data"]["status"] == "NORMAL_NO_DATA"
    assert evidence["stock_data"]["record_count"] == 0
    assert evidence["stock_data"]["current_day_status"] == "PARTIAL_INTRADAY"
    statuses = infer_evidence_statuses({}, evidence)
    assert statuses["ohlcv_5d"] == EvidenceStatus.NORMAL_NO_DATA
    assert statuses["volume"] == EvidenceStatus.NORMAL_NO_DATA


def test_fund_flow_ths_fallback_preserves_leading_zero_codes():
    from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider

    provider = CnAkshareProvider()
    fake_ak = MagicMock()
    fake_ak.stock_individual_fund_flow.side_effect = ConnectionError("eastmoney down")
    frame = pd.DataFrame(
        {
            "股票代码": [2409],
            "股票简称": ["雅克科技"],
            "换手率": ["18.26%"],
            "净额": ["-3.60亿"],
        }
    )
    fake_ak.stock_fund_flow_individual.return_value = frame
    provider._fund_flow_rank_cache = {}
    with patch.object(provider, "_ak", return_value=fake_ak):
        text = provider.get_individual_fund_flow("002409.SZ")

    assert "FUND_FLOW_AGGREGATE_HAS_DATA" in text
    assert "雅克科技" in text


def test_empty_realtime_quote_json_is_normal_no_data():
    from tradingagents.graph.data_collector import DataCollector

    assert DataCollector._infer_source_status("{}") == "NORMAL_NO_DATA"
    assert DataCollector._infer_source_status("[]") == "NORMAL_NO_DATA"


def test_partial_realtime_quote_marks_missing_market_fields():
    evidence = {
        "realtime_quote": {
            "status": "HAS_DATA",
            "raw": json.dumps({"002409.SZ": {"price": 166.43}}),
        }
    }

    statuses = infer_evidence_statuses({}, raw_evidence=evidence)

    assert statuses["turnover_rate"] == EvidenceStatus.FIELD_MISSING
    assert statuses["volume_ratio"] == EvidenceStatus.FIELD_MISSING


def test_realtime_quote_is_only_allowed_for_same_day_analysis():
    now = datetime(2026, 7, 28, 16, 5)
    assert _should_fetch_realtime_quote("2026-07-28", now=now) is True
    assert _should_fetch_realtime_quote("2026-07-27", now=now) is False


def test_historical_skipped_quote_does_not_reuse_stale_live_vendor():
    from tradingagents.dataflows import interface

    interface._last_hit_vendor["get_realtime_quotes"] = "cn_astock"
    collector = DataCollector()
    collector._cache["002409.SZ_2026-07-27"] = {
        "realtime_quote": {
            "status": "SKIPPED",
            "raw": "",
            "vendor": "skipped",
            "reason": "historical analysis does not use current realtime quotes",
            "field": "realtime_quote",
            "as_of": "2026-07-27",
        }
    }
    try:
        quote = collector.build_raw_evidence(
            "002409.SZ", "2026-07-27"
        )["realtime_quote"]
    finally:
        interface._last_hit_vendor.clear()

    assert quote["status"] == "SKIPPED"
    assert quote["vendor"] == "skipped"
    assert quote["endpoint"] == ""
    assert quote["fallback_from"] is None


def test_failed_sources_do_not_reuse_stale_success_vendor():
    collector = DataCollector()
    collector._cache["002409.SZ_2026-07-28"] = {
        "realtime_quote": "数据获取失败：RuntimeError: No available vendor",
        "fund_flow_individual": (
            "002409.SZ [DATA-024] FUND_FLOW_FAILED: 上游连接失败"
        ),
        "lhb": "002409.SZ [G-007] LHB_FAILED: 上游连接失败",
        "research_report": (
            "002409.SZ [DATA-011] REPORT_FAILED: 上游连接失败"
        ),
        "ratings": "002409.SZ [DATA-012] RATINGS_FAILED: 上游连接失败",
        "buybacks": "002409.SZ [DATA-013] BUYBACK_FAILED: 上游连接失败",
    }

    with patch(
        "tradingagents.graph.data_collector.get_last_hit_vendor",
        return_value="stale_vendor",
    ):
        evidence = collector.build_raw_evidence("002409.SZ", "2026-07-28")

    for key in (
        "realtime_quote",
        "fund_flow_individual",
        "lhb",
        "research_report",
        "ratings",
        "buybacks",
    ):
        assert evidence[key]["status"] == "FAILED"
        assert evidence[key]["vendor"] == ""
        assert evidence[key]["endpoint"] == ""


def test_historical_skipped_quote_preserves_report_turnover_and_volume_ratio():
    statuses = infer_evidence_statuses(
        {"volume_price_report": "换手率：5.2%，量比：1.3"},
        {"realtime_quote": {"status": "SKIPPED", "raw": ""}},
    )

    assert statuses["turnover_rate"] == EvidenceStatus.HAS_DATA
    assert statuses["volume_ratio"] == EvidenceStatus.HAS_DATA


def test_verified_metrics_use_normalized_facts_and_yi_units():
    facts = [
        {
            "metric": "revenue",
            "report_date": "2026-03-31",
            "period_scope": "Q1_YTD",
            "value": 1.97276e9,
            "unit": "元",
        },
        {
            "metric": "operating_cost",
            "report_date": "2026-03-31",
            "period_scope": "Q1_YTD",
            "value": 1.1e9,
            "unit": "元",
        },
        {
            "metric": "net_profit",
            "report_date": "2026-03-31",
            "period_scope": "Q1_YTD",
            "value": 2.98935e8,
            "unit": "元",
        },
        {
            "metric": "operating_cashflow",
            "report_date": "2026-03-31",
            "period_scope": "Q1_YTD",
            "value": -4.23641e7,
            "unit": "元",
        },
        {
            "metric": "revenue",
            "report_date": "2025-03-31",
            "period_scope": "Q1_YTD",
            "value": 1.8462e9,
            "unit": "元",
        },
        {
            "metric": "operating_cost",
            "report_date": "2025-03-31",
            "period_scope": "Q1_YTD",
            "value": 1.05e9,
            "unit": "元",
        },
        {
            "metric": "net_profit",
            "report_date": "2025-03-31",
            "period_scope": "Q1_YTD",
            "value": 2.67817e8,
            "unit": "元",
        },
        {
            "metric": "total_assets",
            "report_date": "2026-03-31",
            "period_scope": "POINT_IN_TIME",
            "value": 1.7654e10,
            "unit": "元",
        },
        {
            "metric": "total_liabilities",
            "report_date": "2026-03-31",
            "period_scope": "POINT_IN_TIME",
            "value": 6.3021e9,
            "unit": "元",
        },
        {
            "metric": "total_equity",
            "report_date": "2026-03-31",
            "period_scope": "POINT_IN_TIME",
            "value": 1.13519e10,
            "unit": "元",
        },
    ]
    result_data = {
        "metadata": {
            "raw_evidence": {
                "financial_period_facts": {"status": "HAS_DATA", "raw": facts}
            }
        }
    }
    metrics = build_verified_financial_key_metrics(
        result_data,
        [{"name": "总资产", "value": "1.7654元", "status": "neutral"}],
    )
    by_name = {metric["name"]: metric for metric in metrics}

    assert by_name["总资产"]["value"] == "176.54亿元"
    assert by_name["资产负债率"]["value"].startswith("35.7")
    assert by_name["经营现金流净额"]["value"] == "-0.42亿元"
    assert by_name["营收增速（同比）"]["value"] != "数据缺失"
    assert by_name["净利润增速（同比）"]["value"] != "数据缺失"
    assert by_name["ROE（报告期）"]["value"] != "数据缺失"


def test_verified_metrics_preserve_standalone_cashflow_and_assets():
    facts = [
        {
            "metric": "operating_cashflow",
            "report_date": "2026-03-31",
            "period_scope": "Q1_YTD",
            "value": 100_000_000,
            "unit": "元",
            "status": "HAS_DATA",
        },
        {
            "metric": "total_assets",
            "report_date": "2026-03-31",
            "period_scope": "POINT_IN_TIME",
            "value": 17_654_000_000,
            "unit": "元",
            "status": "HAS_DATA",
        },
    ]
    result_data = {
        "metadata": {
            "raw_evidence": {
                "financial_period_facts": {"status": "HAS_DATA", "raw": facts}
            }
        }
    }
    metrics = build_verified_financial_key_metrics(result_data)
    by_name = {metric["name"]: metric for metric in metrics}

    assert by_name["经营现金流净额"]["value"] == "1.00亿元"
    assert by_name["总资产"]["value"] == "176.54亿元"
