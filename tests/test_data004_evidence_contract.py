"""Tests for DATA-004: raw_evidence 来源契约升级. [DATA-004] raw_evidence_contract"""
import pytest
from unittest.mock import patch

from tradingagents.dataflows.evidence_contract import (
    EvidenceContract,
    resolve_data_type,
    resolve_endpoint,
    resolve_fallback_info,
    compute_contract_completeness,
    build_data_source_summary,
    _EVIDENCE_KEY_TO_DATA_TYPE,
)
from tradingagents.graph.data_collector import DataCollector
from tradingagents.dataflows.interface import _last_hit_vendor


def _make_pool(**overrides):
    pool = {
        "stock_data": "# Stock data for 600584.SH\nDate,Open,High,Low,Close,Volume\n2026-05-25,80.0,81.0,79.5,80.17,150000",
        "news": "Some news data for testing",
        "global_news": "Global news content",
        "fund_flow_board": "Board fund flow data",
        "fund_flow_individual": (
            "600584.SH 近20日主力资金净流向"
            "（Eastmoney push2his，单位：万元）：\n"
            "2026-05-25 | 100.00"
        ),
        "lhb": "LHB data",
        "fundamentals": "Fundamental analysis data",
        "balance_sheet": "Balance sheet data",
        "cashflow": "Cash flow data",
        "income_statement": "Income statement data",
        "insider_transactions": "Insider transaction data",
        "zt_pool": "ZT pool data",
        "hot_stocks": "Hot stocks data",
        "indicators": {"rsi": 65.0, "macd": 0.5},
        "vpa_indicators": "VPA precomputed data",
        "announcements": "Announcement data",
    }
    pool.update(overrides)
    return pool


def _build_evidence(pool_overrides=None):
    collector = DataCollector()
    pool = _make_pool(**(pool_overrides or {}))
    with patch("tradingagents.graph.data_collector._fetch_all", return_value=pool):
        collector.collect("600584.SH", "2026-05-26")
    return collector.build_raw_evidence("600584.SH", "2026-05-26")


# ── EvidenceContract dataclass ─────────────────────────────────────────────

class TestEvidenceContract:

    def test_default_values(self):
        c = EvidenceContract(field="test")
        assert c.field == "test"
        assert c.value is None
        assert c.unit is None
        assert c.vendor == ""
        assert c.endpoint == ""
        assert c.status == "NOT_QUERIED"
        assert c.fallback_from is None
        assert c.source_url is None
        assert c.error is None
        assert c.is_realtime_patched is False
        assert c.current_day_status is None
        assert c.source_type is None
        assert c.unit_verified is None
        assert c.query_mode is None
        assert c.adjustment is None
        assert c.force_reason is None
        assert c.record_count == 0

    def test_to_dict(self):
        c = EvidenceContract(
            field="fund_flow_individual",
            value="some data",
            unit="万元",
            vendor="cn_astock",
            endpoint="push2his.eastmoney.com/fflow",
            status="HAS_DATA",
            fallback_from="cn_akshare",
        )
        d = c.to_dict()
        assert d["field"] == "fund_flow_individual"
        assert d["raw"] == "some data"
        assert d["unit"] == "万元"
        assert d["vendor"] == "cn_astock"
        assert d["endpoint"] == "push2his.eastmoney.com/fflow"
        assert d["status"] == "HAS_DATA"
        assert d["fallback_from"] == "cn_akshare"
        assert d["is_realtime_patched"] is False

    def test_from_dict(self):
        d = {
            "raw": "data",
            "field": "stock_data",
            "unit": "股",
            "vendor": "cn_akshare",
            "endpoint": "stock_zh_a_hist",
            "status": "HAS_DATA",
            "fallback_from": None,
            "source_url": None,
            "error": None,
            "is_realtime_patched": True,
            "source_type": None,
            "unit_verified": True,
            "query_mode": None,
            "adjustment": "前复权",
            "force_reason": None,
            "record_count": 90,
        }
        c = EvidenceContract.from_dict(d)
        assert c.field == "stock_data"
        assert c.value == "data"
        assert c.unit == "股"
        assert c.vendor == "cn_akshare"
        assert c.endpoint == "stock_zh_a_hist"
        assert c.status == "HAS_DATA"
        assert c.is_realtime_patched is True
        assert c.adjustment == "前复权"
        assert c.record_count == 90

    def test_from_dict_minimal(self):
        c = EvidenceContract.from_dict({"status": "FAILED"})
        assert c.status == "FAILED"
        assert c.field == ""
        assert c.vendor == ""

    def test_roundtrip(self):
        c = EvidenceContract(
            field="lhb",
            status="HAS_DATA",
            vendor="cn_akshare",
            endpoint="stock_lhb_detail_em",
            unit="万元",
            query_mode="forced",
            force_reason="异常波动",
        )
        d = c.to_dict()
        c2 = EvidenceContract.from_dict(d)
        assert c2.field == c.field
        assert c2.status == c.status
        assert c2.vendor == c.vendor
        assert c2.endpoint == c.endpoint
        assert c2.query_mode == c.query_mode

    def test_has_data_property(self):
        assert EvidenceContract(field="x", status="HAS_DATA").has_data is True
        assert EvidenceContract(field="x", status="FAILED").has_data is False
        assert EvidenceContract(field="x", status="NOT_QUERIED").has_data is False

    def test_is_failed_property(self):
        assert EvidenceContract(field="x", status="FAILED").is_failed is True
        assert EvidenceContract(field="x", status="HAS_DATA").is_failed is False

    def test_is_fallback_property(self):
        assert EvidenceContract(field="x", fallback_from="cn_akshare").is_fallback is True
        assert EvidenceContract(field="x", fallback_from=None).is_fallback is False
        assert EvidenceContract(field="x", fallback_from="").is_fallback is False

    def test_unit_known_property(self):
        assert EvidenceContract(field="x", unit="万元").unit_known is True
        assert EvidenceContract(field="x", unit=None).unit_known is False
        assert EvidenceContract(field="x", unit="").unit_known is False

    def test_endpoint_known_property(self):
        assert EvidenceContract(field="x", endpoint="stock_zh_a_hist").endpoint_known is True
        assert EvidenceContract(field="x", endpoint="").endpoint_known is False


# ── resolve_data_type ──────────────────────────────────────────────────────

class TestResolveDataType:

    def test_stock_data(self):
        assert resolve_data_type("stock_data") == "ohlcv"

    def test_fund_flow_individual(self):
        assert resolve_data_type("fund_flow_individual") == "fund_flow"

    def test_fund_flow_board(self):
        assert resolve_data_type("fund_flow_board") == "board_fund_flow"

    def test_lhb(self):
        assert resolve_data_type("lhb") == "lhb"

    def test_news(self):
        assert resolve_data_type("news") == "news"

    def test_announcements(self):
        assert resolve_data_type("announcements") == "notice"

    def test_unknown(self):
        assert resolve_data_type("nonexistent") == ""

    def test_all_keys_mapped(self):
        for key in _EVIDENCE_KEY_TO_DATA_TYPE:
            assert resolve_data_type(key) != ""


# ── resolve_endpoint ───────────────────────────────────────────────────────

class TestResolveEndpoint:

    def test_akshare_ohlcv(self):
        ep = resolve_endpoint("cn_akshare", "ohlcv")
        assert ep == "stock_zh_a_hist"

    def test_astock_fund_flow(self):
        ep = resolve_endpoint("cn_astock", "fund_flow")
        assert ep == "push2his.eastmoney.com/fflow"

    def test_unknown_vendor(self):
        ep = resolve_endpoint("nonexistent_vendor", "ohlcv")
        assert ep == "stock_zh_a_hist"

    def test_unknown_data_type(self):
        ep = resolve_endpoint("cn_akshare", "nonexistent_type")
        assert ep == ""

    def test_empty_data_type(self):
        ep = resolve_endpoint("cn_akshare", "")
        assert ep == ""


# ── resolve_fallback_info ──────────────────────────────────────────────────

class TestResolveFallbackInfo:

    def test_primary_returns_none(self):
        result = resolve_fallback_info("cn_akshare", "ohlcv")
        assert result is None

    def test_fallback_returns_primary(self):
        result = resolve_fallback_info("cn_astock", "fund_flow")
        assert result == "cn_tushare"

    def test_unknown_type(self):
        result = resolve_fallback_info("cn_akshare", "nonexistent")
        assert result is None


# ── compute_contract_completeness ──────────────────────────────────────────

class TestComputeContractCompleteness:

    def test_empty_evidence(self):
        result = compute_contract_completeness({})
        assert result["completeness_score"] == 0
        assert result["total_checks"] > 0

    def test_full_evidence(self):
        re_data = _build_evidence()
        result = compute_contract_completeness(re_data)
        assert result["completeness_score"] > 0
        assert result["satisfied_checks"] > 0

    def test_missing_fund_flow(self):
        re_data = _build_evidence({"fund_flow_individual": "数据获取失败：timeout"})
        result = compute_contract_completeness(re_data)
        assert "fund_flow_individual" in result["missing_details"]

    def test_fallback_vendor_shows_actual(self):
        _last_hit_vendor["get_individual_fund_flow"] = "cn_astock"
        try:
            re_data = _build_evidence()
            assert re_data["fund_flow_individual"]["vendor"] == "cn_astock"
            assert re_data["fund_flow_individual"]["fallback_from"] == "cn_tushare"
        finally:
            _last_hit_vendor.pop("get_individual_fund_flow", None)

    def test_unit_unknown_degrades(self):
        re_data = _build_evidence()
        re_data["fund_flow_individual"]["unit"] = None
        result = compute_contract_completeness(re_data)
        assert "unit" in result.get("missing_details", {}).get("fund_flow_individual", [])

    def test_unit_unverified_degrades(self):
        re_data = _build_evidence()
        re_data["fund_flow_individual"]["unit_verified"] = False
        result = compute_contract_completeness(re_data)
        assert "unit_verified" in result.get("missing_details", {}).get("fund_flow_individual", [])


# ── build_data_source_summary ──────────────────────────────────────────────

class TestBuildDataSourceSummary:

    def test_summary_keys(self):
        re_data = _build_evidence()
        summary = build_data_source_summary(re_data)
        assert len(summary) > 0
        fields_in_summary = {s["field"] for s in summary}
        assert "stock_data" in fields_in_summary
        assert "fund_flow_individual" in fields_in_summary

    def test_summary_structure(self):
        re_data = _build_evidence()
        summary = build_data_source_summary(re_data)
        for item in summary:
            assert "field" in item
            assert "vendor" in item
            assert "endpoint" in item
            assert "status" in item
            assert "unit" in item
            assert "unit_verified" in item
            assert "is_fallback" in item
            assert "fallback_from" in item
            assert "is_realtime_patched" in item
            assert "as_of" in item
            assert "record_count" in item
            assert "error" in item

    def test_empty_evidence(self):
        summary = build_data_source_summary({})
        assert summary == []

    def test_non_dict_entry_skipped(self):
        summary = build_data_source_summary({"key": "not a dict"})
        assert summary == []


# ── build_raw_evidence upgraded output ─────────────────────────────────────

class TestBuildRawEvidenceContractFields:

    def setup_method(self):
        _last_hit_vendor.clear()

    def test_all_entries_have_field_key(self):
        re_data = _build_evidence()
        for key, entry in re_data.items():
            assert isinstance(entry, dict), f"{key} is not dict"
            assert "field" in entry, f"{key} missing 'field'"
            assert entry["field"] == key

    def test_all_entries_have_endpoint(self):
        re_data = _build_evidence()
        for key, entry in re_data.items():
            if not isinstance(entry, dict):
                continue
            assert "endpoint" in entry, f"{key} missing 'endpoint'"
            if key in ("stock_data", "fund_flow_individual", "fund_flow_board",
                       "lhb", "news", "announcements"):
                assert entry["endpoint"] != "", f"{key} endpoint empty for known type"

    def test_all_entries_have_fallback_from(self):
        re_data = _build_evidence()
        for key, entry in re_data.items():
            assert "fallback_from" in entry, f"{key} missing 'fallback_from'"

    def test_all_entries_have_source_url(self):
        re_data = _build_evidence()
        for key, entry in re_data.items():
            assert "source_url" in entry, f"{key} missing 'source_url'"

    def test_stock_data_endpoint(self):
        re_data = _build_evidence()
        assert re_data["stock_data"]["endpoint"] == "stock_zh_a_hist"

    def test_fund_flow_endpoint(self):
        re_data = _build_evidence()
        assert re_data["fund_flow_individual"]["endpoint"] == "moneyflow"

    def test_lhb_endpoint(self):
        re_data = _build_evidence()
        assert re_data["lhb"]["endpoint"] == "top_list"

    def test_fallback_detected_when_non_primary(self):
        _last_hit_vendor["get_individual_fund_flow"] = "cn_astock"
        try:
            re_data = _build_evidence()
            ff = re_data["fund_flow_individual"]
            assert ff["vendor"] == "cn_astock"
            assert ff["fallback_from"] == "cn_tushare"
        finally:
            _last_hit_vendor.pop("get_individual_fund_flow", None)

    def test_no_fallback_when_primary(self):
        _last_hit_vendor["get_individual_fund_flow"] = "cn_tushare"
        try:
            re_data = _build_evidence()
            ff = re_data["fund_flow_individual"]
            assert ff["vendor"] == "cn_tushare"
            assert ff["fallback_from"] is None
        finally:
            _last_hit_vendor.pop("get_individual_fund_flow", None)


# ── Acceptance criteria from TASKS.md ──────────────────────────────────────

class TestData004Acceptance:

    def setup_method(self):
        _last_hit_vendor.clear()

    def test_fallback_shows_actual_vendor(self):
        """fallback 命中时 raw_evidence 显示实际 vendor，而不是默认 akshare。"""
        _last_hit_vendor["get_individual_fund_flow"] = "cn_astock"
        try:
            re_data = _build_evidence()
            ff = re_data["fund_flow_individual"]
            assert ff["vendor"] == "cn_astock"
            assert ff["fallback_from"] is not None
        finally:
            _last_hit_vendor.pop("get_individual_fund_flow", None)

    def test_unit_unknown_degrades_completeness(self):
        """单位未知时完整度降级。"""
        re_data = _build_evidence()
        re_data["fund_flow_individual"]["unit"] = None
        result = compute_contract_completeness(re_data)
        assert result["completeness_score"] < 100

    def test_unit_conflict_degrades(self):
        """字段冲突时完整度降级。"""
        re_data = _build_evidence()
        re_data["fund_flow_individual"]["unit_verified"] = False
        result = compute_contract_completeness(re_data)
        assert result["completeness_score"] < 100

    def test_failed_status_zero_completeness_for_key(self):
        re_data = _build_evidence({"fund_flow_individual": "数据获取失败：timeout"})
        result = compute_contract_completeness(re_data)
        assert "fund_flow_individual" in result["missing_details"]

    def test_endpoint_populated_for_known_types(self):
        re_data = _build_evidence()
        assert re_data["stock_data"]["endpoint"] != ""
        assert re_data["fund_flow_individual"]["endpoint"] != ""
        assert re_data["lhb"]["endpoint"] != ""

    def test_contract_completeness_from_readiness(self):
        from tradingagents.agents.utils.readiness_score import calculate_evidence_coverage_from_contract
        re_data = _build_evidence()
        score = calculate_evidence_coverage_from_contract(re_data)
        assert isinstance(score, int)
        assert 0 <= score <= 100

    def test_contract_completeness_empty(self):
        from tradingagents.agents.utils.readiness_score import calculate_evidence_coverage_from_contract
        score = calculate_evidence_coverage_from_contract(None)
        assert score == 0

    def test_contract_completeness_empty_dict(self):
        from tradingagents.agents.utils.readiness_score import calculate_evidence_coverage_from_contract
        score = calculate_evidence_coverage_from_contract({})
        assert score == 0

    def test_fund_flow_unit_verified_in_contract(self):
        re_data = _build_evidence()
        ff = re_data["fund_flow_individual"]
        assert ff["unit"] == "万元"
        assert ff["unit_verified"] is True

    def test_lhb_query_mode_in_contract(self):
        re_data = _build_evidence()
        lhb = re_data["lhb"]
        assert "query_mode" in lhb

    def test_stock_data_unit_in_contract(self):
        re_data = _build_evidence()
        sd = re_data["stock_data"]
        assert sd["unit"] == "股"


# ── Edge cases ─────────────────────────────────────────────────────────────

class TestEdgeCases:

    def setup_method(self):
        _last_hit_vendor.clear()

    def test_none_pool_value(self):
        re_data = _build_evidence({"lhb": None})
        assert re_data["lhb"]["status"] == "NOT_QUERIED"
        assert "endpoint" in re_data["lhb"]

    def test_empty_string_value(self):
        re_data = _build_evidence({"news": ""})
        assert re_data["news"]["status"] == "NOT_QUERIED"

    def test_dict_value(self):
        re_data = _build_evidence({"indicators": {"rsi": 65}})
        assert re_data["indicators"]["status"] == "HAS_DATA"

    def test_list_value(self):
        re_data = _build_evidence({"insider_transactions": [1, 2, 3]})
        assert re_data["insider_transactions"]["status"] == "HAS_DATA"
        assert re_data["insider_transactions"]["record_count"] == 3

    def test_empty_list_value(self):
        re_data = _build_evidence({"insider_transactions": []})
        assert re_data["insider_transactions"]["status"] == "NORMAL_NO_DATA"

    def test_realtime_patched_vendor(self):
        patched_csv = (
            "# Stock data for 600584.SH\n"
            "# [G-005] is_realtime_patched=True, source=sina, quote_time=2026-05-26 15:00:00\n"
            "Date,Open,High,Low,Close,Volume\n"
            "2026-05-26,85.0,89.0,84.5,88.19,50000\n"
        )
        re_data = _build_evidence({"stock_data": patched_csv})
        sd = re_data["stock_data"]
        assert sd["is_realtime_patched"] is True
        assert sd["vendor"] == "sina"
        assert sd["as_of"] == "2026-05-26 15:00:00"
        assert "endpoint" in sd

    def test_adjustment_in_stock_data(self):
        csv_with_adj = (
            "# Stock data for 600584.SH\n"
            "# [DATA-P0-603629] adjustment=前复权\n"
            "Date,Open,High,Low,Close,Volume\n2026-05-25,80.0,81.0,79.5,80.17,150000\n"
        )
        re_data = _build_evidence({"stock_data": csv_with_adj})
        assert re_data["stock_data"]["adjustment"] == "前复权"

    def test_no_api_keys_in_output(self):
        import json
        re_data = _build_evidence()
        serialized = json.dumps(re_data).lower()
        for secret in ["sk-12345", "cookie=abc", "token=xyz", "password", "api_key", "secret_key"]:
            assert secret not in serialized

    def test_backward_compatible_existing_fields(self):
        re_data = _build_evidence()
        for key, entry in re_data.items():
            assert "raw" in entry, f"{key}: missing 'raw'"
            assert "status" in entry
            assert "vendor" in entry
            assert "as_of" in entry
            assert "fetched_at" in entry
            assert "record_count" in entry
            assert "unit" in entry
            assert "error" in entry
            assert "is_realtime_patched" in entry


# ── Integration: infer_evidence_statuses still works ───────────────────────

class TestIntegrationWithReadiness:

    def setup_method(self):
        _last_hit_vendor.clear()

    def test_infer_evidence_with_contract(self):
        from tradingagents.agents.utils.readiness_score import infer_evidence_statuses
        re_data = _build_evidence()
        statuses = infer_evidence_statuses({}, raw_evidence=re_data)
        assert statuses["ohlcv_5d"] == "has_data"
        assert statuses["volume"] == "has_data"

    def test_infer_evidence_with_failed_fund_flow(self):
        from tradingagents.agents.utils.readiness_score import infer_evidence_statuses
        re_data = _build_evidence({"fund_flow_individual": "数据获取失败：timeout"})
        statuses = infer_evidence_statuses({}, raw_evidence=re_data)
        assert statuses["individual_fund_flow"] == "query_failed"

    def test_fund_flow_provenance_with_contract(self):
        from tradingagents.agents.utils.readiness_score import build_fund_flow_provenance
        re_data = _build_evidence()
        result = build_fund_flow_provenance(re_data, {})
        assert result["individual_status"] == "HAS_DATA"
        assert result["unit_verified"] is True

    def test_lhb_provenance_with_contract(self):
        from tradingagents.agents.utils.readiness_score import build_lhb_provenance
        re_data = _build_evidence()
        result = build_lhb_provenance(re_data, {})
        assert result["status"] == "HAS_DATA"
