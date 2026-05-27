"""[G-007] Tests for fund flow and LHB provenance calibration.

Covers:
1. build_fund_flow_provenance — individual / board / news_reported fund flow
2. build_lhb_provenance — HAS_DATA / NOT_QUERIED / NORMAL_NO_DATA / FAILED
3. Gate integration: fund flow FAILED + news mentions → strong evidence blocked
4. LHB force=False → NOT_QUERIED, not "无显著资金异动"
5. LHB historical data exists but today not queried → no "无龙虎榜数据"
6. format_fund_lhb_provenance — display correctness
"""
import pytest

from tradingagents.agents.utils.readiness_score import (
    build_fund_flow_provenance,
    build_lhb_provenance,
    format_fund_lhb_provenance,
    get_strong_action_gate,
    infer_evidence_statuses,
    EvidenceStatus,
)


# ── build_fund_flow_provenance ──────────────────────────────────────────

class TestFundFlowProvenance:

    def test_individual_has_data_g006_structured(self):
        raw = {
            "fund_flow_individual": {
                "raw": "600584.SH 近20日主力资金净流向：\n日期 主力净流入",
                "status": "HAS_DATA",
                "unit": "万元",
                "unit_verified": True,
            },
            "fund_flow_board": {
                "raw": "板块资金流...",
                "status": "HAS_DATA",
            },
        }
        result = build_fund_flow_provenance(raw, {})
        assert result["individual_status"] == "HAS_DATA"
        assert result["unit_verified"] is True
        assert result["strong_evidence_allowed"] is True
        assert result["conflict_summary"] == ""

    def test_individual_failed_news_mentions_fund(self):
        raw = {
            "fund_flow_individual": {
                "raw": "个股资金流向数据获取失败：ProxyError",
                "status": "FAILED",
                "unit": None,
                "unit_verified": False,
            },
        }
        reports = {"news_report": "据报道，主力净流出1.2亿元，市场情绪低迷。"}
        result = build_fund_flow_provenance(raw, reports)
        assert result["individual_status"] == "FAILED"
        assert result["strong_evidence_allowed"] is False
        assert result["news_reported_fund_flow"] is True
        assert "新闻转述资金信息仅作弱证据" in result["conflict_summary"]
        assert "个股资金流接口查询失败" in result["conflict_summary"]

    def test_individual_not_queried(self):
        raw = {
            "fund_flow_individual": {
                "raw": None,
                "status": "NOT_QUERIED",
                "unit_verified": False,
            },
        }
        result = build_fund_flow_provenance(raw, {})
        assert result["individual_status"] == "NOT_QUERIED"
        assert result["strong_evidence_allowed"] is False
        assert "个股资金流接口未查询" in result["conflict_summary"]

    def test_individual_normal_no_data(self):
        raw = {
            "fund_flow_individual": {
                "raw": "",
                "status": "NORMAL_NO_DATA",
                "unit_verified": False,
            },
        }
        result = build_fund_flow_provenance(raw, {})
        assert result["individual_status"] == "NORMAL_NO_DATA"
        assert result["strong_evidence_allowed"] is False

    def test_legacy_format_individual_has_data(self):
        raw = {
            "fund_flow_individual": "600584.SH 近20日主力资金净流向：\n日期 净流入\n2026-05-26 12345",
        }
        result = build_fund_flow_provenance(raw, {})
        assert result["individual_status"] == "HAS_DATA"
        assert result["unit_verified"] is True
        assert result["strong_evidence_allowed"] is True

    def test_legacy_format_individual_failed(self):
        raw = {
            "fund_flow_individual": "个股资金流向数据获取失败：ProxyError",
        }
        result = build_fund_flow_provenance(raw, {})
        assert result["individual_status"] == "FAILED"
        assert result["strong_evidence_allowed"] is False

    def test_board_separate_from_individual(self):
        raw = {
            "fund_flow_individual": {
                "raw": "data",
                "status": "HAS_DATA",
                "unit": "万元",
                "unit_verified": True,
            },
            "fund_flow_board": {
                "raw": "board data",
                "status": "HAS_DATA",
            },
        }
        result = build_fund_flow_provenance(raw, {})
        assert result["individual_status"] == "HAS_DATA"
        assert result["board_status"] == "HAS_DATA"
        assert result["not_mixed"] is True

    def test_news_no_fund_mention(self):
        raw = {
            "fund_flow_individual": {
                "raw": "fail data",
                "status": "FAILED",
                "unit_verified": False,
            },
        }
        reports = {"news_report": "今天天气不错，适合户外运动。"}
        result = build_fund_flow_provenance(raw, reports)
        assert result["news_reported_fund_flow"] is False
        assert "新闻转述" not in result["conflict_summary"]

    def test_empty_raw_evidence(self):
        result = build_fund_flow_provenance(None, {})
        assert result["individual_status"] == "NOT_QUERIED"
        assert result["strong_evidence_allowed"] is False

    def test_unit_verified_false_blocks_strong_evidence(self):
        raw = {
            "fund_flow_individual": {
                "raw": "data with unknown unit",
                "status": "HAS_DATA",
                "unit": None,
                "unit_verified": False,
            },
        }
        result = build_fund_flow_provenance(raw, {})
        assert result["individual_status"] == "HAS_DATA"
        assert result["unit_verified"] is False
        assert result["strong_evidence_allowed"] is False


# ── build_lhb_provenance ────────────────────────────────────────────────

class TestLHBProvenance:

    def test_force_false_returns_not_queried(self):
        raw = {
            "lhb": {
                "raw": "600584.SH [G-007] LHB_NOT_QUERIED: 龙虎榜查询未触发（force=False）。",
                "status": "NOT_QUERIED",
                "query_mode": "on_demand",
            },
        }
        result = build_lhb_provenance(raw, {})
        assert result["status"] == "NOT_QUERIED"
        assert result["query_mode"] == "on_demand"
        assert "未查询" in result["display_text"]
        assert "无显著资金异动" not in result["display_text"]

    def test_force_false_legacy_text_query_mode(self):
        result = build_lhb_provenance(
            {"lhb": "600584.SH 龙虎榜查询未触发（force=False）。"},
            {},
        )
        assert result["status"] == "NOT_QUERIED"
        assert result["query_mode"] == "not_queried"

    def test_force_true_no_data_returns_normal_no_data(self):
        raw = {
            "lhb": {
                "raw": "600584.SH [G-007] LHB_NORMAL_NO_DATA: 在 2026-05-26 无龙虎榜数据（非异动日属正常）。",
                "status": "NORMAL_NO_DATA",
                "query_mode": "forced",
            },
        }
        result = build_lhb_provenance(raw, {})
        assert result["status"] == "NORMAL_NO_DATA"
        assert result["query_mode"] == "forced"
        assert "无上榜" in result["display_text"]

    def test_force_true_has_data(self):
        raw = {
            "lhb": {
                "raw": "600584.SH [G-007] LHB_HAS_DATA: 龙虎榜明细（2026-05-22）：\n买入 卖出",
                "status": "HAS_DATA",
                "query_mode": "forced",
            },
        }
        result = build_lhb_provenance(raw, {})
        assert result["status"] == "HAS_DATA"
        assert result["query_mode"] == "forced"

    def test_force_true_failed(self):
        raw = {
            "lhb": {
                "raw": "600584.SH [G-007] LHB_FAILED: 龙虎榜数据获取失败：ProxyError",
                "status": "FAILED",
                "query_mode": "forced",
            },
        }
        result = build_lhb_provenance(raw, {})
        assert result["status"] == "FAILED"
        assert "查询失败" in result["display_text"]

    def test_legacy_text_not_queried(self):
        raw = {
            "lhb": "600584.SH 龙虎榜查询未触发（force=False）。龙虎榜为按需查询接口。",
        }
        result = build_lhb_provenance(raw, {})
        assert result["status"] == "NOT_QUERIED"

    def test_legacy_text_normal_no_data(self):
        raw = {
            "lhb": "600584.SH 在 2026-05-26 无龙虎榜数据（非异动日属正常）。",
        }
        result = build_lhb_provenance(raw, {})
        assert result["status"] == "NORMAL_NO_DATA"

    def test_legacy_text_has_data(self):
        raw = {
            "lhb": "600584.SH 龙虎榜明细（2026-05-22）：\n营业部 买入额",
        }
        result = build_lhb_provenance(raw, {})
        assert result["status"] == "HAS_DATA"

    def test_legacy_text_failed(self):
        raw = {
            "lhb": "龙虎榜数据获取失败：ProxyError: ...",
        }
        result = build_lhb_provenance(raw, {})
        assert result["status"] == "FAILED"

    def test_empty_raw(self):
        result = build_lhb_provenance(None, {})
        assert result["status"] == "NOT_QUERIED"
        assert "未查询" in result["display_text"]

    def test_historical_lhb_today_not_queried_no_wrong_display(self):
        raw = {
            "lhb": {
                "raw": "600584.SH [G-007] LHB_NOT_QUERIED: 龙虎榜查询未触发（force=False）。",
                "status": "NOT_QUERIED",
                "query_mode": "on_demand",
            },
        }
        result = build_lhb_provenance(raw, {})
        assert result["status"] == "NOT_QUERIED"
        assert "无龙虎榜数据" not in result["display_text"]
        assert "未查询" in result["display_text"]


# ── Gate integration ────────────────────────────────────────────────────

class TestGateIntegration:

    def test_fund_flow_failed_news_mentions_blocks_gate(self):
        fund_prov = build_fund_flow_provenance(
            {
                "fund_flow_individual": {
                    "raw": "个股资金流向数据获取失败：ProxyError",
                    "status": "FAILED",
                    "unit_verified": False,
                },
            },
            {"news_report": "主力净流出1.2亿元"},
        )
        assert not fund_prov["strong_evidence_allowed"]

        gate = get_strong_action_gate(
            source_coverage=80,
            evidence_coverage=80,
            fund_flow_unit_verified=fund_prov["strong_evidence_allowed"],
            fund_flow_not_mixed=fund_prov["not_mixed"],
        )
        assert not gate["passed"]
        assert any("资金流单位未校验" in f for f in gate["failures"])

    def test_fund_flow_ok_passes_gate(self):
        fund_prov = build_fund_flow_provenance(
            {
                "fund_flow_individual": {
                    "raw": "data...",
                    "status": "HAS_DATA",
                    "unit": "万元",
                    "unit_verified": True,
                },
            },
            {},
        )
        assert fund_prov["strong_evidence_allowed"]

        gate = get_strong_action_gate(
            source_coverage=80,
            evidence_coverage=80,
            fund_flow_unit_verified=fund_prov["strong_evidence_allowed"],
            fund_flow_not_mixed=fund_prov["not_mixed"],
        )
        assert gate["passed"]

    def test_unit_unverified_blocks_gate(self):
        fund_prov = build_fund_flow_provenance(
            {
                "fund_flow_individual": {
                    "raw": "some data",
                    "status": "HAS_DATA",
                    "unit": None,
                    "unit_verified": False,
                },
            },
            {},
        )
        assert not fund_prov["strong_evidence_allowed"]

        gate = get_strong_action_gate(
            source_coverage=80,
            evidence_coverage=80,
            fund_flow_unit_verified=fund_prov["strong_evidence_allowed"],
        )
        assert not gate["passed"]


# ── infer_evidence_statuses LHB fix ─────────────────────────────────────

class TestInferEvidenceLHB:

    def test_lhb_not_queried_from_g006_structured(self):
        raw = {
            "lhb": {
                "raw": "600584.SH [G-007] LHB_NOT_QUERIED: 龙虎榜查询未触发（force=False）。",
                "status": "NOT_QUERIED",
                "query_mode": "on_demand",
            },
        }
        reports = {"market_report": "", "volume_price_report": "", "smart_money_report": "", "news_report": ""}
        result = infer_evidence_statuses(reports, raw_evidence=raw)
        assert result["lhb_status"] == EvidenceStatus.NOT_QUERIED

    def test_lhb_normal_no_data_from_g006_structured(self):
        raw = {
            "lhb": {
                "raw": "600584.SH [G-007] LHB_NORMAL_NO_DATA: 在 2026-05-26 无龙虎榜数据",
                "status": "NORMAL_NO_DATA",
                "query_mode": "forced",
            },
        }
        reports = {"market_report": "", "volume_price_report": "", "smart_money_report": "", "news_report": ""}
        result = infer_evidence_statuses(reports, raw_evidence=raw)
        assert result["lhb_status"] == EvidenceStatus.NORMAL_NO_DATA

    def test_lhb_failed_from_g006_structured(self):
        raw = {
            "lhb": {
                "raw": "600584.SH [G-007] LHB_FAILED: 龙虎榜数据获取失败：ProxyError",
                "status": "FAILED",
            },
        }
        reports = {"market_report": "", "volume_price_report": "", "smart_money_report": "", "news_report": ""}
        result = infer_evidence_statuses(reports, raw_evidence=raw)
        assert result["lhb_status"] == EvidenceStatus.QUERY_FAILED

    def test_lhb_has_data_from_g006_structured(self):
        raw = {
            "lhb": {
                "raw": "600584.SH [G-007] LHB_HAS_DATA: 龙虎榜明细\n买入 1亿",
                "status": "HAS_DATA",
            },
        }
        reports = {"market_report": "", "volume_price_report": "", "smart_money_report": "", "news_report": ""}
        result = infer_evidence_statuses(reports, raw_evidence=raw)
        assert result["lhb_status"] == EvidenceStatus.HAS_DATA

    def test_lhb_legacy_not_queried_text(self):
        raw = {
            "lhb": "600584.SH 龙虎榜查询未触发（force=False）。龙虎榜为按需查询接口。",
        }
        reports = {"market_report": "", "volume_price_report": "", "smart_money_report": "", "news_report": ""}
        result = infer_evidence_statuses(reports, raw_evidence=raw)
        assert result["lhb_status"] == EvidenceStatus.NOT_QUERIED


# ── format_fund_lhb_provenance ──────────────────────────────────────────

class TestFormatProvenance:

    def test_all_ok_format(self):
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
        assert "个股资金流: ✅ 有数据" in text
        assert "主力资金强证据: ✅ 可用" in text
        assert "龙虎榜: ✅ 龙虎榜有数据" in text
        assert "新闻转述" not in text

    def test_failed_with_news_conflict_format(self):
        fund_prov = {
            "individual_status": "FAILED",
            "board_status": "HAS_DATA",
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
        assert "个股资金流: ❌ 查询失败" in text
        assert "新闻转述资金: ⚠️" in text
        assert "主力资金强证据: ❌ 不可用" in text
        assert "龙虎榜未查询" in text
        assert "新闻转述资金信息仅作弱证据" in text

    def test_lhb_not_queried_display(self):
        fund_prov = {
            "individual_status": "HAS_DATA",
            "board_status": "NOT_QUERIED",
            "news_reported_fund_flow": False,
            "strong_evidence_allowed": True,
            "unit_verified": True,
            "not_mixed": True,
            "conflict_summary": "",
        }
        lhb_prov = {
            "status": "NOT_QUERIED",
            "query_mode": "not_queried",
            "display_text": "龙虎榜未查询（非异动触发）",
        }
        text = format_fund_lhb_provenance(fund_prov, lhb_prov)
        assert "龙虎榜: ⬜ 龙虎榜未查询（非异动触发）" in text
        assert "无显著资金异动" not in text
        assert "无龙虎榜数据" not in text


# ── DataCollector._infer_source_status G-007 fix ────────────────────────

class TestInferSourceStatusG007:

    def test_lhb_not_queried_text(self):
        from tradingagents.graph.data_collector import DataCollector
        status = DataCollector._infer_source_status(
            "600584.SH [G-007] LHB_NOT_QUERIED: 龙虎榜查询未触发（force=False）。"
        )
        assert status == "NOT_QUERIED"

    def test_lhb_normal_no_data_text(self):
        from tradingagents.graph.data_collector import DataCollector
        status = DataCollector._infer_source_status(
            "600584.SH [G-007] LHB_NORMAL_NO_DATA: 在 2026-05-26 无龙虎榜数据（非异动日属正常）。"
        )
        assert status == "NORMAL_NO_DATA"

    def test_lhb_failed_text(self):
        from tradingagents.graph.data_collector import DataCollector
        status = DataCollector._infer_source_status(
            "600584.SH [G-007] LHB_FAILED: 龙虎榜数据获取失败：ProxyError"
        )
        assert status == "FAILED"

    def test_lhb_has_data_text(self):
        from tradingagents.graph.data_collector import DataCollector
        status = DataCollector._infer_source_status(
            "600584.SH [G-007] LHB_HAS_DATA: 龙虎榜明细（2026-05-22）：\n买入 1亿"
        )
        assert status == "HAS_DATA"

    def test_regular_fund_flow_not_affected(self):
        from tradingagents.graph.data_collector import DataCollector
        status = DataCollector._infer_source_status(
            "600584.SH 近20日主力资金净流向：\n日期 净流入"
        )
        assert status == "HAS_DATA"

    def test_none_returns_not_queried(self):
        from tradingagents.graph.data_collector import DataCollector
        status = DataCollector._infer_source_status(None)
        assert status == "NOT_QUERIED"

    def test_empty_returns_not_queried(self):
        from tradingagents.graph.data_collector import DataCollector
        status = DataCollector._infer_source_status("")
        assert status == "NOT_QUERIED"
