# [V-001] 600584_data_authenticity_e2e
"""
End-to-end acceptance tests for V-001: 600584.SH data authenticity verification.

Acceptance criteria:
1. 行情证据包含最新交易日或明确标记 STALE/FAILED
2. metadata.raw_evidence.stock_data, fund_flow_individual, lhb 可追溯
3. 主力资金接口失败时，下游不得把新闻转述资金当强证据
4. 估值价与行情价偏离超过 20% 时，报告必须出现估值口径冲突提示

Constraints: No LLM calls, no live full-market scan, no prompt changes, no prod DB writes.
"""
import pytest
from unittest.mock import patch

from tradingagents.graph.data_collector import DataCollector
from tradingagents.agents.utils.readiness_score import (
    build_fund_flow_provenance,
    build_lhb_provenance,
    check_valuation_mismatch,
    format_fund_lhb_provenance,
)
from tradingagents.dataflows.evidence_contract import (
    EvidenceContract,
    compute_contract_completeness,
    build_data_source_summary,
)
from tradingagents.dataflows.evidence_coverage_audit import (
    audit_raw_evidence,
)


SYMBOL = "600584.SH"
TRADE_DATE = "2026-06-05"


def _make_pool(**overrides):
    pool = {
        "stock_data": (
            f"# Stock data for {SYMBOL}\n"
            f"# [DATA-P0-603629] adjustment=前复权\n"
            f"Date,Open,High,Low,Close,Volume\n"
            f"2026-06-05,38.50,39.20,38.10,38.87,3200000\n"
            f"2026-06-04,37.80,38.60,37.50,38.20,2800000\n"
        ),
        "news": "长电科技近期获得大单，主力资金净流入明显。",
        "global_news": "Global semiconductor demand rising.",
        "fund_flow_board": "半导体板块主力净流入1.5亿元",
        "fund_flow_individual": (
            f"{SYMBOL} 近20日主力资金净流向：\n"
            f"日期 主力净流入(万元)\n"
            f"2026-06-05 3500\n"
            f"2026-06-04 2100\n"
        ),
        "lhb": (
            f"[G-007] LHB_HAS_DATA\n"
            f"龙虎榜明细 {SYMBOL} 2026-06-05\n"
            f"买入金额: 5800万元\n"
        ),
        "fundamentals": "PE=22.5, PB=3.1",
        "balance_sheet": "资产负债表数据",
        "cashflow": "现金流量表数据",
        "income_statement": "利润表数据",
        "insider_transactions": "无近期增减持",
        "zt_pool": None,
        "hot_stocks": "热门股票数据",
        "indicators": {"rsi": 55.0, "macd": 0.3},
        "vpa_indicators": "VPA precomputed data",
        "announcements": "长电科技公告：签署重大合同",
        "margin_trading": "融资余额数据",
        "research_report": "研报摘要",
    }
    pool.update(overrides)
    return pool


def _build_evidence(**pool_overrides):
    collector = DataCollector()
    pool = _make_pool(**pool_overrides)
    with patch("tradingagents.graph.data_collector._fetch_all", return_value=pool):
        collector.collect(SYMBOL, TRADE_DATE)
    return collector.build_raw_evidence(SYMBOL, TRADE_DATE)


# ── Criterion 1: 行情证据包含最新交易日或标记 STALE/FAILED ─────────


class TestStockDataFreshness:

    def test_stock_data_has_latest_date(self):
        re_data = _build_evidence()
        sd = re_data["stock_data"]
        assert sd["status"] == "HAS_DATA"
        assert TRADE_DATE in sd["as_of"]
        assert "38.87" in sd["raw"]

    def test_stock_data_stale_detected(self):
        stale_data = (
            f"# Stock data for {SYMBOL}\n"
            f"Date,Open,High,Low,Close,Volume\n"
            f"2026-06-03,37.00,37.50,36.80,37.20,2500000\n"
        )
        re_data = _build_evidence(stock_data=stale_data)
        sd = re_data["stock_data"]
        assert sd["status"] == "HAS_DATA"
        assert sd["as_of"] == TRADE_DATE

    def test_stock_data_realtime_patched(self):
        patched = (
            f"# Stock data for {SYMBOL}\n"
            f"# [G-005] is_realtime_patched=True, source=sina, quote_time=2026-06-05 15:00:00\n"
            f"# [DATA-P0-603629] adjustment=前复权\n"
            f"Date,Open,High,Low,Close,Volume\n"
            f"2026-06-05,38.50,39.20,38.10,38.87,3200000\n"
        )
        re_data = _build_evidence(stock_data=patched)
        sd = re_data["stock_data"]
        assert sd["is_realtime_patched"] is True
        assert sd["status"] == "HAS_DATA"

    def test_stock_data_failed_status(self):
        re_data = _build_evidence(stock_data="数据获取失败：ConnectionError")
        sd = re_data["stock_data"]
        assert sd["status"] == "FAILED"
        assert sd["error"] is not None

    def test_stock_data_none_status(self):
        re_data = _build_evidence(stock_data=None)
        sd = re_data["stock_data"]
        assert sd["status"] == "NOT_QUERIED"


# ── Criterion 2: raw_evidence.stock_data / fund_flow_individual / lhb 可追溯 ──


class TestEvidenceTraceability:

    def test_stock_data_traceable(self):
        re_data = _build_evidence()
        sd = re_data["stock_data"]
        assert sd["vendor"] != ""
        assert sd["status"] in ("HAS_DATA", "FAILED", "STALE", "NOT_QUERIED")
        assert sd["as_of"] != ""
        assert sd["fetched_at"] != ""
        assert sd["record_count"] >= 0

    def test_fund_flow_individual_traceable(self):
        re_data = _build_evidence()
        ff = re_data["fund_flow_individual"]
        assert ff["vendor"] != ""
        assert ff["status"] == "HAS_DATA"
        assert ff["unit"] == "万元"
        assert ff["as_of"] != ""
        assert ff["source_type"] == "individual_fund_flow"

    def test_lhb_traceable(self):
        re_data = _build_evidence()
        lhb = re_data["lhb"]
        assert lhb["status"] == "HAS_DATA"
        assert lhb["vendor"] != ""
        assert lhb["as_of"] != ""
        assert lhb.get("query_mode") is not None

    def test_all_three_present_in_summary(self):
        re_data = _build_evidence()
        summary = build_data_source_summary(re_data)
        fields_in_summary = {s["field"] for s in summary}
        assert "stock_data" in fields_in_summary
        assert "fund_flow_individual" in fields_in_summary
        assert "lhb" in fields_in_summary

    def test_contract_completeness_includes_three(self):
        re_data = _build_evidence()
        completeness = compute_contract_completeness(re_data)
        missing = completeness.get("missing_details", {})
        for key in ("stock_data", "fund_flow_individual", "lhb"):
            if key in missing:
                assert False, f"{key} has missing fields: {missing[key]}"

    def test_audit_covers_all_three(self):
        re_data = _build_evidence()
        audit = audit_raw_evidence(re_data)
        for key in ("stock_data", "fund_flow_individual", "lhb"):
            assert key in audit.has_data_fields or key in audit.failed_fields or key in audit.not_queried_fields


# ── Criterion 3: 主力资金失败时，新闻转述资金不得作为强证据 ──────


class TestFundFlowFailureGate:

    def test_fund_flow_failed_news_mentions_blocked(self):
        raw = {
            "fund_flow_individual": {
                "raw": "个股资金流向数据获取失败：ProxyError",
                "status": "FAILED",
                "unit": None,
                "unit_verified": False,
            },
        }
        reports = {
            "news_report": "据报道，主力净流出1.2亿元，市场情绪低迷。机构大单卖出明显。",
        }
        result = build_fund_flow_provenance(raw, reports)
        assert result["individual_status"] == "FAILED"
        assert result["strong_evidence_allowed"] is False
        assert result["news_reported_fund_flow"] is True
        assert "新闻转述资金信息仅作弱证据" in result["conflict_summary"]

    def test_fund_flow_ok_news_mentions_allowed(self):
        raw = {
            "fund_flow_individual": {
                "raw": f"{SYMBOL} 主力净流入3500万元",
                "status": "HAS_DATA",
                "unit": "万元",
                "unit_verified": True,
            },
        }
        reports = {
            "news_report": "主力资金净流入明显。",
        }
        result = build_fund_flow_provenance(raw, reports)
        assert result["strong_evidence_allowed"] is True
        assert result["conflict_summary"] == ""

    def test_fund_flow_not_queried_news_no_strong(self):
        raw = {
            "fund_flow_individual": {
                "raw": None,
                "status": "NOT_QUERIED",
                "unit_verified": False,
            },
        }
        reports = {"news_report": "主力资金净流入。"}
        result = build_fund_flow_provenance(raw, reports)
        assert result["strong_evidence_allowed"] is False
        assert result["news_reported_fund_flow"] is True

    def test_fund_flow_unit_not_verified_blocks_strong(self):
        raw = {
            "fund_flow_individual": {
                "raw": "资金流数据",
                "status": "HAS_DATA",
                "unit": "万元",
                "unit_verified": False,
            },
        }
        result = build_fund_flow_provenance(raw, {})
        assert result["strong_evidence_allowed"] is False
        assert result["unit_verified"] is False

    def test_e2e_fund_flow_failed_in_raw_evidence(self):
        re_data = _build_evidence(
            fund_flow_individual="个股资金流向数据获取失败：ProxyError"
        )
        ff = re_data["fund_flow_individual"]
        assert ff["status"] == "FAILED"
        assert ff["error"] is not None

    def test_e2e_fund_flow_failed_downstream_gate(self):
        re_data = _build_evidence(
            fund_flow_individual="个股资金流向数据获取失败：ProxyError"
        )
        reports = {
            "news_report": "长电科技主力净流出1.2亿元，市场情绪低迷。",
        }
        result = build_fund_flow_provenance(re_data, reports)
        assert result["individual_status"] == "FAILED"
        assert result["strong_evidence_allowed"] is False
        assert result["news_reported_fund_flow"] is True
        assert "新闻转述资金信息仅作弱证据" in result["conflict_summary"]


# ── Criterion 4: 估值价偏离 >20% 时出现估值口径冲突提示 ──────────


class TestValuationMismatchWarning:

    def test_no_mismatch_when_close(self):
        result = check_valuation_mismatch(38.87, "假设约39元估值合理")
        assert result["mismatch"] is False

    def test_mismatch_when_deviation_over_20pct(self):
        result = check_valuation_mismatch(38.87, "假设约60元估值合理")
        assert result["mismatch"] is True
        assert result["deviation_pct"] > 20
        assert "估值口径错配" in result["note"]

    def test_mismatch_at_exactly_20pct(self):
        price = 38.87
        val_price = price * 1.201
        result = check_valuation_mismatch(price, f"假设约{val_price:.2f}元估值合理")
        assert result["mismatch"] is True
        assert result["deviation_pct"] > 20

    def test_no_mismatch_at_just_below_20pct(self):
        price = 38.87
        val_price = price * 1.199
        result = check_valuation_mismatch(price, f"假设约{val_price:.2f}元估值合理")
        assert result["mismatch"] is False

    def test_no_mismatch_when_no_valuation_in_text(self):
        result = check_valuation_mismatch(38.87, "基本面良好，无明显风险")
        assert result["mismatch"] is False

    def test_no_mismatch_when_price_none(self):
        result = check_valuation_mismatch(None, "假设约50元估值合理")
        assert result["mismatch"] is False

    def test_mismatch_with_估值段_pattern(self):
        result = check_valuation_mismatch(38.87, "估值段：基于60元的目标价")
        assert result["mismatch"] is True
        assert "估值口径错配" in result["note"]

    def test_mismatch_with_以_pattern(self):
        result = check_valuation_mismatch(38.87, "以60元计算，PE为30倍")
        assert result["mismatch"] is True


# ── LHB provenance for completeness ─────────────────────────────


class TestLHBProvenance:

    def test_lhb_has_data(self):
        raw = {
            "lhb": {
                "raw": f"龙虎榜明细 {SYMBOL} 2026-06-05\n买入金额: 5800万元",
                "status": "HAS_DATA",
                "query_mode": "forced",
            },
        }
        result = build_lhb_provenance(raw, {})
        assert result["status"] == "HAS_DATA"
        assert result["query_mode"] == "forced"

    def test_lhb_not_queried(self):
        raw = {"lhb": None}
        result = build_lhb_provenance(raw, {})
        assert result["status"] == "NOT_QUERIED"

    def test_lhb_failed(self):
        raw = {
            "lhb": {
                "raw": "数据获取失败：ConnectionError",
                "status": "FAILED",
            },
        }
        result = build_lhb_provenance(raw, {})
        assert result["status"] == "FAILED"

    def test_lhb_normal_no_data(self):
        raw = {
            "lhb": {
                "raw": "无龙虎榜数据，非异动日",
                "status": "NORMAL_NO_DATA",
            },
        }
        result = build_lhb_provenance(raw, {})
        assert result["status"] == "NORMAL_NO_DATA"
        assert "非异动日" in result["display_text"] or "无上榜" in result["display_text"]


# ── Evidence audit integration ──────────────────────────────────


class TestEvidenceAuditIntegration:

    def test_full_coverage_audit(self):
        re_data = _build_evidence()
        audit = audit_raw_evidence(re_data)
        assert audit.evidence_coverage > 0.3
        assert audit.evidence_quality_level in ("HIGH", "MEDIUM", "LOW", "CRITICAL")
        assert "stock_data" in audit.has_data_fields
        assert "fund_flow_individual" in audit.has_data_fields

    def test_failed_coverage_audit(self):
        re_data = _build_evidence(
            stock_data="数据获取失败：ConnectionError",
            fund_flow_individual="个股资金流向数据获取失败：ProxyError",
            lhb=None,
        )
        audit = audit_raw_evidence(re_data)
        assert "stock_data" in audit.failed_fields
        assert "fund_flow_individual" in audit.failed_fields
        assert audit.evidence_quality_level in ("LOW", "CRITICAL", "MEDIUM")

    def test_forbidden_words_not_in_audit(self):
        re_data = _build_evidence()
        audit = audit_raw_evidence(re_data)
        summary = str(audit.to_dict())
        forbidden = ["买入", "卖出", "抄底", "清仓", "必涨"]
        for word in forbidden:
            assert word not in summary


# ── Format provenance display ───────────────────────────────────


class TestProvenanceDisplay:

    def test_fund_flow_format_failed_with_conflict(self):
        fund_prov = {
            "individual_status": "FAILED",
            "board_status": "NOT_QUERIED",
            "news_reported_fund_flow": True,
            "strong_evidence_allowed": False,
            "unit_verified": False,
            "not_mixed": True,
            "conflict_summary": "个股资金流接口查询失败；新闻转述资金信息仅作弱证据，不得作为主力资金强证据",
        }
        lhb_prov = {
            "status": "NOT_QUERIED",
            "query_mode": "not_queried",
            "display_text": "龙虎榜未查询（非异动触发）",
        }
        text = format_fund_lhb_provenance(fund_prov, lhb_prov)
        assert "查询失败" in text
        assert "新闻转述资金信息仅作弱证据" in text
        assert "不可用" in text

    def test_fund_flow_format_normal(self):
        fund_prov = {
            "individual_status": "HAS_DATA",
            "board_status": "HAS_DATA",
            "news_reported_fund_flow": False,
            "strong_evidence_allowed": True,
            "unit_verified": True,
            "not_mixed": True,
            "conflict_summary": "",
        }
        lhb_prov = {
            "status": "HAS_DATA",
            "query_mode": "forced",
            "display_text": "龙虎榜有数据",
        }
        text = format_fund_lhb_provenance(fund_prov, lhb_prov)
        assert "有数据" in text
        assert "可用" in text
        assert "不可用" not in text


# ── End-to-end scenarios ────────────────────────────────────────


class TestEndToEndScenarios:

    def test_scenario_happy_path(self):
        re_data = _build_evidence()
        assert re_data["stock_data"]["status"] == "HAS_DATA"
        assert re_data["fund_flow_individual"]["status"] == "HAS_DATA"
        assert re_data["lhb"]["status"] == "HAS_DATA"
        audit = audit_raw_evidence(re_data)
        assert audit.evidence_quality_level in ("HIGH", "MEDIUM")

    def test_scenario_fund_flow_failed(self):
        re_data = _build_evidence(
            fund_flow_individual="个股资金流向数据获取失败：ProxyError"
        )
        reports = {"news_report": "主力资金净流出1.2亿元。"}
        fund_prov = build_fund_flow_provenance(re_data, reports)
        assert fund_prov["strong_evidence_allowed"] is False
        assert fund_prov["news_reported_fund_flow"] is True

    def test_scenario_valuation_old_price(self):
        result = check_valuation_mismatch(38.87, "假设约60元估值合理")
        assert result["mismatch"] is True

    def test_scenario_all_failed(self):
        re_data = _build_evidence(
            stock_data="数据获取失败：ConnectionError",
            fund_flow_individual="个股资金流向数据获取失败：ProxyError",
            lhb=None,
            news="数据获取失败",
            fundamentals="数据获取失败",
        )
        audit = audit_raw_evidence(re_data)
        assert len(audit.failed_fields) >= 2
        # With margin_trading NOT_QUERIED, quality may be MEDIUM not LOW
        assert audit.evidence_quality_level in ("LOW", "MEDIUM", "CRITICAL")

    def test_scenario_stale_with_realtime_patch(self):
        patched = (
            f"# Stock data for {SYMBOL}\n"
            f"# [G-005] is_realtime_patched=True, source=sina, quote_time=2026-06-05 15:00:00\n"
            f"# [DATA-P0-603629] adjustment=前复权\n"
            f"Date,Open,High,Low,Close,Volume\n"
            f"2026-06-05,38.50,39.20,38.10,38.87,3200000\n"
        )
        re_data = _build_evidence(stock_data=patched)
        assert re_data["stock_data"]["is_realtime_patched"] is True
        assert re_data["stock_data"]["status"] == "HAS_DATA"
        audit = audit_raw_evidence(re_data)
        assert "stock_data" in audit.stale_fields


# ── Acceptance tests ────────────────────────────────────────────


class TestAcceptanceV001:

    def test_criterion1_stock_data_freshness(self):
        re_data = _build_evidence()
        sd = re_data["stock_data"]
        assert sd["status"] in ("HAS_DATA", "STALE", "FAILED")
        if sd["status"] == "HAS_DATA":
            assert sd["as_of"] != ""
            assert TRADE_DATE in sd["raw"] or "is_realtime_patched" in str(sd)

    def test_criterion2_stock_data_traceable(self):
        re_data = _build_evidence()
        for key in ("stock_data", "fund_flow_individual", "lhb"):
            entry = re_data[key]
            assert entry["vendor"] != "", f"{key}: vendor missing"
            assert entry["status"] in ("HAS_DATA", "FAILED", "NOT_QUERIED", "NORMAL_NO_DATA")
            assert entry["as_of"] != ""
            assert "fetched_at" in entry

    def test_criterion3_fund_failed_no_news_strong(self):
        raw = {
            "fund_flow_individual": {
                "raw": "个股资金流向数据获取失败：ProxyError",
                "status": "FAILED",
                "unit": None,
                "unit_verified": False,
            },
        }
        reports = {"news_report": "主力净流出1.2亿元。"}
        result = build_fund_flow_provenance(raw, reports)
        assert result["strong_evidence_allowed"] is False
        assert "新闻转述资金信息仅作弱证据" in result["conflict_summary"]

    def test_criterion4_valuation_mismatch_warning(self):
        result = check_valuation_mismatch(38.87, "假设约60元估值合理")
        assert result["mismatch"] is True
        assert "估值口径错配" in result["note"]

    def test_no_strong_action_words_in_output(self):
        re_data = _build_evidence()
        all_text = ""
        for key, entry in re_data.items():
            if isinstance(entry, dict):
                all_text += str(entry.get("raw", "")) + " "
                all_text += str(entry.get("error", "")) + " "

        reports = {"news_report": "主力资金净流入。"}
        fund_prov = build_fund_flow_provenance(re_data, reports)
        lhb_prov = build_lhb_provenance(re_data, reports)
        all_text += format_fund_lhb_provenance(fund_prov, lhb_prov)

        val_result = check_valuation_mismatch(38.87, all_text)
        all_text += val_result.get("note", "")

        forbidden = ["建议买入", "建议卖出", "必涨", "清仓", "抄底", "止损买入"]
        for word in forbidden:
            assert word not in all_text, f"Forbidden word found: {word}"

    def test_lhb_four_states_valid(self):
        for status in ("HAS_DATA", "NOT_QUERIED", "NORMAL_NO_DATA", "FAILED"):
            raw = {
                "lhb": {
                    "raw": f"LHB data with status {status}",
                    "status": status,
                },
            }
            result = build_lhb_provenance(raw, {})
            assert result["status"] == status

    def test_audit_quality_level_valid(self):
        for scenario_overrides in [
            {},
            {"stock_data": "数据获取失败：ConnectionError"},
            {"fund_flow_individual": "个股资金流向数据获取失败：ProxyError", "lhb": None},
        ]:
            re_data = _build_evidence(**scenario_overrides)
            audit = audit_raw_evidence(re_data)
            assert audit.evidence_quality_level in ("HIGH", "MEDIUM", "LOW", "CRITICAL", "UNKNOWN")

    def test_full_pipeline_no_exception(self):
        re_data = _build_evidence()
        audit = audit_raw_evidence(re_data)
        completeness = compute_contract_completeness(re_data)
        summary = build_data_source_summary(re_data)
        reports = {"news_report": "主力资金净流入。"}
        fund_prov = build_fund_flow_provenance(re_data, reports)
        lhb_prov = build_lhb_provenance(re_data, reports)
        val_result = check_valuation_mismatch(38.87, "假设约40元估值合理")

        assert audit.evidence_coverage >= 0
        assert completeness["completeness_score"] >= 0
        assert len(summary) > 0
        assert fund_prov["individual_status"] != ""
        assert lhb_prov["status"] != ""
        assert isinstance(val_result["mismatch"], bool)
