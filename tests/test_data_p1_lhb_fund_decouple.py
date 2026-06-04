"""[DATA-P1-LHB-FUND-DECOUPLE] Tests for LHB and fund flow trigger chain decoupling.

Covers:
1. _compute_lhb_force_decision — fund flow failure does NOT block anomaly_condition
2. _should_force_lhb_from_news — standalone anomaly condition check
3. data_collector._fetch_all — LHB force chain uses independent checks
4. smart_money_analyst — pool path and fallback path both check anomaly conditions
5. LHB 4-state raw_evidence: NOT_QUERIED / NORMAL_NO_DATA / FAILED / HAS_DATA
6. Acceptance: all task verification criteria
"""
import pytest
from unittest.mock import patch, MagicMock


# ── _compute_lhb_force_decision ────────────────────────────────────────

class TestComputeLHBForceDecision:

    def test_fund_flow_anomaly_only(self):
        from tradingagents.graph.data_collector import _compute_lhb_force_decision
        needed, reason = _compute_lhb_force_decision(
            "主力净流入 80000 万元",
            "normal news",
            "normal stock data",
        )
        assert needed is True
        assert reason == "fund_flow_anomaly"

    def test_anomaly_condition_only(self):
        from tradingagents.graph.data_collector import _compute_lhb_force_decision
        needed, reason = _compute_lhb_force_decision(
            "正常资金流数据",
            "该股出现严重异常波动公告",
            "normal stock data",
        )
        assert needed is True
        assert reason == "anomaly_condition"

    def test_fund_flow_failed_anomaly_condition_triggers(self):
        from tradingagents.graph.data_collector import _compute_lhb_force_decision
        needed, reason = _compute_lhb_force_decision(
            "个股资金流向数据获取失败：ProxyError",
            "该股连续3个交易日涨停，龙虎榜数据显示...",
            "normal stock data",
        )
        assert needed is True
        assert "anomaly_condition" in reason

    def test_fund_flow_failed_no_anomaly_no_force(self):
        from tradingagents.graph.data_collector import _compute_lhb_force_decision
        needed, reason = _compute_lhb_force_decision(
            "个股资金流向数据获取失败：ProxyError",
            "今日大盘平稳，个股表现一般",
            "stock data normal",
        )
        assert needed is False
        assert reason == ""

    def test_both_triggers_combined_reason(self):
        from tradingagents.graph.data_collector import _compute_lhb_force_decision
        needed, reason = _compute_lhb_force_decision(
            "主力净流入 120000 万元",
            "该股严重异常波动",
            "stock data",
        )
        assert needed is True
        assert "fund_flow_anomaly" in reason
        assert "anomaly_condition" in reason

    def test_empty_fund_flow_with_anomaly_condition(self):
        from tradingagents.graph.data_collector import _compute_lhb_force_decision
        needed, reason = _compute_lhb_force_decision(
            "",
            "龙虎榜数据显示买入金额1.2亿",
            "",
        )
        assert needed is True
        assert reason == "anomaly_condition"

    def test_empty_all_no_force(self):
        from tradingagents.graph.data_collector import _compute_lhb_force_decision
        needed, reason = _compute_lhb_force_decision("", "", "")
        assert needed is False
        assert reason == ""

    def test_announcement_mentions_lhb(self):
        from tradingagents.graph.data_collector import _compute_lhb_force_decision
        needed, reason = _compute_lhb_force_decision(
            "正常资金流",
            "一般新闻",
            "一般行情",
            "公司发布关于龙虎榜交易风险提示公告",
        )
        assert needed is True
        assert reason == "announcement_mentions_lhb"

    def test_announcement_no_lhb_keyword(self):
        from tradingagents.graph.data_collector import _compute_lhb_force_decision
        needed, reason = _compute_lhb_force_decision(
            "正常资金流",
            "一般新闻",
            "一般行情",
            "公司发布年度分红公告",
        )
        assert needed is False


# ── _should_force_lhb_from_news (smart_money_analyst) ───────────────────

class TestShouldForceLHBFromNews:

    def test_news_mentions_lhb(self):
        from tradingagents.agents.analysts.smart_money_analyst import _should_force_lhb_from_news
        assert _should_force_lhb_from_news("龙虎榜数据显示买入1.2亿", "") is True

    def test_stock_data_severe_anomaly(self):
        from tradingagents.agents.analysts.smart_money_analyst import _should_force_lhb_from_news
        assert _should_force_lhb_from_news("", "严重异常波动公告") is True

    def test_consecutive_limit_up(self):
        from tradingagents.agents.analysts.smart_money_analyst import _should_force_lhb_from_news
        assert _should_force_lhb_from_news("该股连续3涨停", "") is True

    def test_consecutive_limit_up_with_ge(self):
        from tradingagents.agents.analysts.smart_money_analyst import _should_force_lhb_from_news
        assert _should_force_lhb_from_news("该股连续三涨停", "") is True

    def test_one_word_limit_up(self):
        from tradingagents.agents.analysts.smart_money_analyst import _should_force_lhb_from_news
        assert _should_force_lhb_from_news("一字涨停封板", "") is True

    def test_normal_text_no_trigger(self):
        from tradingagents.agents.analysts.smart_money_analyst import _should_force_lhb_from_news
        assert _should_force_lhb_from_news("今日大盘平稳", "成交量正常") is False

    def test_none_inputs(self):
        from tradingagents.agents.analysts.smart_money_analyst import _should_force_lhb_from_news
        assert _should_force_lhb_from_news(None, None) is False


# ── _fetch_all LHB force chain decoupling ──────────────────────────────

class TestFetchAllLHBDecoupling:

    def test_fund_flow_failed_news_triggers_force(self):
        from tradingagents.graph.data_collector import _compute_lhb_force_decision

        ff_text = "个股资金流向数据获取失败：ProxyError"
        news_text = "该股连续3个交易日涨停，龙虎榜买入1.2亿"
        needed, reason = _compute_lhb_force_decision(ff_text, news_text, "")
        assert needed is True

    def test_fund_flow_empty_stock_anomaly_triggers_force(self):
        from tradingagents.graph.data_collector import _compute_lhb_force_decision

        ff_text = ""
        news_text = ""
        stock_text = "该股涨跌幅偏离值超过15%，严重异常波动"
        needed, reason = _compute_lhb_force_decision(ff_text, news_text, stock_text)
        assert needed is True
        assert "anomaly_condition" in reason


# ── smart_money_analyst LHB trigger logic ──────────────────────────────

class TestSmartMoneyLHBTrigger:

    def test_pool_path_ff_failed_news_triggers(self):
        from tradingagents.agents.analysts.smart_money_analyst import (
            _check_fund_flow_anomaly,
            _should_force_lhb_from_news,
        )
        ff_text = "个股资金流向数据获取失败：ProxyError"
        assert _check_fund_flow_anomaly(ff_text) is False
        assert _should_force_lhb_from_news("龙虎榜买入1.2亿", "") is True

    def test_pool_path_ff_normal_news_triggers(self):
        from tradingagents.agents.analysts.smart_money_analyst import (
            _check_fund_flow_anomaly,
            _should_force_lhb_from_news,
        )
        ff_text = "正常资金流无异常"
        news_text = "该股严重异常波动"
        ff_anomaly = _check_fund_flow_anomaly(ff_text)
        anomaly_cond = _should_force_lhb_from_news(news_text, "")
        assert ff_anomaly is False
        assert anomaly_cond is True
        assert ff_anomaly or anomaly_cond

    def test_pool_path_no_trigger(self):
        from tradingagents.agents.analysts.smart_money_analyst import (
            _check_fund_flow_anomaly,
            _should_force_lhb_from_news,
        )
        ff_text = "正常资金流无异常"
        news_text = "今日大盘平稳"
        ff_anomaly = _check_fund_flow_anomaly(ff_text)
        anomaly_cond = _should_force_lhb_from_news(news_text, "")
        assert ff_anomaly is False
        assert anomaly_cond is False


# ── LHB 4-state provenance ─────────────────────────────────────────────

class TestLHBFourStates:

    def test_not_queried_status(self):
        from tradingagents.agents.utils.readiness_score import build_lhb_provenance
        raw = {
            "lhb": {
                "raw": "[G-007] LHB_NOT_QUERIED: 龙虎榜查询未触发（force=False）。",
                "status": "NOT_QUERIED",
                "query_mode": "on_demand",
            },
        }
        result = build_lhb_provenance(raw, {})
        assert result["status"] == "NOT_QUERIED"
        assert "未查询" in result["display_text"]
        assert "无龙虎榜数据" not in result["display_text"]

    def test_normal_no_data_status(self):
        from tradingagents.agents.utils.readiness_score import build_lhb_provenance
        raw = {
            "lhb": {
                "raw": "[G-007] LHB_NORMAL_NO_DATA: 在 2026-06-04 无龙虎榜数据（非异动日属正常）。",
                "status": "NORMAL_NO_DATA",
                "query_mode": "forced",
            },
        }
        result = build_lhb_provenance(raw, {})
        assert result["status"] == "NORMAL_NO_DATA"
        assert "无上榜" in result["display_text"]
        assert result["query_mode"] == "forced"

    def test_failed_status(self):
        from tradingagents.agents.utils.readiness_score import build_lhb_provenance
        raw = {
            "lhb": {
                "raw": "[G-007] LHB_FAILED: 龙虎榜数据获取失败：ProxyError",
                "status": "FAILED",
                "query_mode": "forced",
            },
        }
        result = build_lhb_provenance(raw, {})
        assert result["status"] == "FAILED"
        assert "查询失败" in result["display_text"]

    def test_has_data_status(self):
        from tradingagents.agents.utils.readiness_score import build_lhb_provenance
        raw = {
            "lhb": {
                "raw": "[G-007] LHB_HAS_DATA: 龙虎榜明细（2026-06-04）：\n买入 1.2亿",
                "status": "HAS_DATA",
                "query_mode": "forced",
            },
        }
        result = build_lhb_provenance(raw, {})
        assert result["status"] == "HAS_DATA"
        assert "龙虎榜有数据" in result["display_text"]

    def test_not_queried_vs_normal_no_data_different(self):
        from tradingagents.agents.utils.readiness_score import build_lhb_provenance
        raw_nq = {
            "lhb": {
                "raw": "[G-007] LHB_NOT_QUERIED: 查询未触发",
                "status": "NOT_QUERIED",
                "query_mode": "on_demand",
            },
        }
        raw_nnd = {
            "lhb": {
                "raw": "[G-007] LHB_NORMAL_NO_DATA: 无龙虎榜数据",
                "status": "NORMAL_NO_DATA",
                "query_mode": "forced",
            },
        }
        r1 = build_lhb_provenance(raw_nq, {})
        r2 = build_lhb_provenance(raw_nnd, {})
        assert r1["status"] != r2["status"]
        assert "未查询" in r1["display_text"]
        assert "无上榜" in r2["display_text"]


# ── format_fund_lhb_provenance display ────────────────────────────────

class TestFormatProvenanceDisplay:

    def test_not_queried_shows_neutral(self):
        from tradingagents.agents.utils.readiness_score import format_fund_lhb_provenance
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
            "status": "NOT_QUERIED",
            "query_mode": "not_queried",
            "display_text": "龙虎榜未查询（非异动触发）",
        }
        text = format_fund_lhb_provenance(fund_prov, lhb_prov)
        assert "⬜" in text
        assert "未查询" in text
        assert "无龙虎榜数据" not in text

    def test_normal_no_data_shows_neutral_not_red(self):
        from tradingagents.agents.utils.readiness_score import format_fund_lhb_provenance
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
            "status": "NORMAL_NO_DATA",
            "query_mode": "forced",
            "display_text": "龙虎榜查询正常，当日无上榜记录",
        }
        text = format_fund_lhb_provenance(fund_prov, lhb_prov)
        assert "⬜" in text
        assert "❌" not in text.split("龙虎榜")[1].split("\n")[0]

    def test_failed_shows_red(self):
        from tradingagents.agents.utils.readiness_score import format_fund_lhb_provenance
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
            "status": "FAILED",
            "query_mode": "forced",
            "display_text": "龙虎榜查询失败",
        }
        text = format_fund_lhb_provenance(fund_prov, lhb_prov)
        assert "❌" in text


# ── infer_evidence_statuses LHB 4-state ────────────────────────────────

class TestInferEvidenceLHBDecouple:

    def test_not_queried_g007_structured(self):
        from tradingagents.agents.utils.readiness_score import (
            infer_evidence_statuses,
            EvidenceStatus,
        )
        raw = {
            "lhb": {
                "raw": "[G-007] LHB_NOT_QUERIED: 查询未触发",
                "status": "NOT_QUERIED",
                "query_mode": "on_demand",
            },
        }
        reports = {"market_report": "", "volume_price_report": "", "smart_money_report": "", "news_report": ""}
        result = infer_evidence_statuses(reports, raw_evidence=raw)
        assert result["lhb_status"] == EvidenceStatus.NOT_QUERIED

    def test_normal_no_data_g007_structured(self):
        from tradingagents.agents.utils.readiness_score import (
            infer_evidence_statuses,
            EvidenceStatus,
        )
        raw = {
            "lhb": {
                "raw": "[G-007] LHB_NORMAL_NO_DATA: 无龙虎榜数据",
                "status": "NORMAL_NO_DATA",
                "query_mode": "forced",
            },
        }
        reports = {"market_report": "", "volume_price_report": "", "smart_money_report": "", "news_report": ""}
        result = infer_evidence_statuses(reports, raw_evidence=raw)
        assert result["lhb_status"] == EvidenceStatus.NORMAL_NO_DATA

    def test_failed_g007_structured(self):
        from tradingagents.agents.utils.readiness_score import (
            infer_evidence_statuses,
            EvidenceStatus,
        )
        raw = {
            "lhb": {
                "raw": "[G-007] LHB_FAILED: 获取失败",
                "status": "FAILED",
            },
        }
        reports = {"market_report": "", "volume_price_report": "", "smart_money_report": "", "news_report": ""}
        result = infer_evidence_statuses(reports, raw_evidence=raw)
        assert result["lhb_status"] == EvidenceStatus.QUERY_FAILED

    def test_has_data_g007_structured(self):
        from tradingagents.agents.utils.readiness_score import (
            infer_evidence_statuses,
            EvidenceStatus,
        )
        raw = {
            "lhb": {
                "raw": "[G-007] LHB_HAS_DATA: 龙虎榜明细\n买入 1.2亿",
                "status": "HAS_DATA",
            },
        }
        reports = {"market_report": "", "volume_price_report": "", "smart_money_report": "", "news_report": ""}
        result = infer_evidence_statuses(reports, raw_evidence=raw)
        assert result["lhb_status"] == EvidenceStatus.HAS_DATA

    def test_not_queried_distinct_from_normal_no_data(self):
        from tradingagents.agents.utils.readiness_score import (
            infer_evidence_statuses,
            EvidenceStatus,
        )
        raw_nq = {
            "lhb": {
                "raw": "[G-007] LHB_NOT_QUERIED: 查询未触发",
                "status": "NOT_QUERIED",
            },
        }
        raw_nnd = {
            "lhb": {
                "raw": "[G-007] LHB_NORMAL_NO_DATA: 无龙虎榜数据",
                "status": "NORMAL_NO_DATA",
            },
        }
        reports = {"market_report": "", "volume_price_report": "", "smart_money_report": "", "news_report": ""}
        r1 = infer_evidence_statuses(reports, raw_evidence=raw_nq)
        r2 = infer_evidence_statuses(reports, raw_evidence=raw_nnd)
        assert r1["lhb_status"] != r2["lhb_status"]
        assert r1["lhb_status"] == EvidenceStatus.NOT_QUERIED
        assert r2["lhb_status"] == EvidenceStatus.NORMAL_NO_DATA


# ── DataCollector._infer_source_status LHB 4-state ─────────────────────

class TestInferSourceStatusDecouple:

    def test_not_queried(self):
        from tradingagents.graph.data_collector import DataCollector
        status = DataCollector._infer_source_status(
            "[G-007] LHB_NOT_QUERIED: 龙虎榜查询未触发（force=False）。"
        )
        assert status == "NOT_QUERIED"

    def test_normal_no_data(self):
        from tradingagents.graph.data_collector import DataCollector
        status = DataCollector._infer_source_status(
            "[G-007] LHB_NORMAL_NO_DATA: 在 2026-06-04 无龙虎榜数据（非异动日属正常）。"
        )
        assert status == "NORMAL_NO_DATA"

    def test_failed(self):
        from tradingagents.graph.data_collector import DataCollector
        status = DataCollector._infer_source_status(
            "[G-007] LHB_FAILED: 龙虎榜数据获取失败：ProxyError"
        )
        assert status == "FAILED"

    def test_has_data(self):
        from tradingagents.graph.data_collector import DataCollector
        status = DataCollector._infer_source_status(
            "[G-007] LHB_HAS_DATA: 龙虎榜明细（2026-06-04）：\n买入 1.2亿"
        )
        assert status == "HAS_DATA"


# ── Acceptance: all verification criteria ──────────────────────────────

class TestAcceptanceDataP1LHBFundDecouple:

    def test_fund_flow_failed_news_lhb_still_forced(self):
        """资金流失败 + 新闻出现龙虎榜，仍 force 查询 LHB。"""
        from tradingagents.graph.data_collector import _compute_lhb_force_decision
        needed, reason = _compute_lhb_force_decision(
            "个股资金流向数据获取失败：ProxyError",
            "该股龙虎榜买入金额超1亿元",
            "",
        )
        assert needed is True
        assert "anomaly_condition" in reason

    def test_force_true_no_record_normal_no_data(self):
        """force=True 但无记录，状态为 NORMAL_NO_DATA。"""
        from tradingagents.agents.utils.readiness_score import build_lhb_provenance
        raw = {
            "lhb": {
                "raw": "[G-007] LHB_NORMAL_NO_DATA: 无龙虎榜数据",
                "status": "NORMAL_NO_DATA",
                "query_mode": "forced",
            },
        }
        result = build_lhb_provenance(raw, {})
        assert result["status"] == "NORMAL_NO_DATA"

    def test_force_false_not_queried(self):
        """force=False，状态为 NOT_QUERIED。"""
        from tradingagents.agents.utils.readiness_score import build_lhb_provenance
        raw = {
            "lhb": {
                "raw": "[G-007] LHB_NOT_QUERIED: 查询未触发",
                "status": "NOT_QUERIED",
                "query_mode": "on_demand",
            },
        }
        result = build_lhb_provenance(raw, {})
        assert result["status"] == "NOT_QUERIED"

    def test_api_exception_failed(self):
        """接口异常，状态为 FAILED。"""
        from tradingagents.agents.utils.readiness_score import build_lhb_provenance
        raw = {
            "lhb": {
                "raw": "[G-007] LHB_FAILED: 龙虎榜数据获取失败：ProxyError",
                "status": "FAILED",
                "query_mode": "forced",
            },
        }
        result = build_lhb_provenance(raw, {})
        assert result["status"] == "FAILED"

    def test_normal_no_data_display_not_red(self):
        """NORMAL_NO_DATA 显示为'非异动日无龙虎榜'，不显示红色失败。"""
        from tradingagents.agents.utils.readiness_score import format_fund_lhb_provenance
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
            "status": "NORMAL_NO_DATA",
            "query_mode": "forced",
            "display_text": "龙虎榜查询正常，当日无上榜记录",
        }
        text = format_fund_lhb_provenance(fund_prov, lhb_prov)
        assert "⬜" in text
        lhb_line = [l for l in text.split("\n") if "龙虎榜" in l][0]
        assert "❌" not in lhb_line

    def test_not_queried_display_not_no_data(self):
        """NOT_QUERIED 显示为'未触发查询'，不说'无龙虎榜数据'。"""
        from tradingagents.agents.utils.readiness_score import format_fund_lhb_provenance
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
            "status": "NOT_QUERIED",
            "query_mode": "not_queried",
            "display_text": "龙虎榜未查询（非异动触发）",
        }
        text = format_fund_lhb_provenance(fund_prov, lhb_prov)
        assert "未查询" in text
        assert "无龙虎榜数据" not in text

    def test_decouple_no_strong_words(self):
        """输出无强买卖词。"""
        from tradingagents.agents.utils.readiness_score import format_fund_lhb_provenance
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
        forbidden = ["买入", "卖出", "清仓", "满仓", "梭哈"]
        for word in forbidden:
            assert word not in text, f"Forbidden word '{word}' found in provenance output"

    def test_force_conditions_cover_minimum_set(self):
        """force 条件至少覆盖: 新闻龙虎榜/连续涨跌停/一字板/异常波动/涨跌幅偏离/量比异常/资金流异动。"""
        from tradingagents.graph.data_collector import _compute_lhb_force_decision

        cases = [
            ("normal", "龙虎榜数据显示", "", ""),
            ("normal", "该股严重异常波动公告", "", ""),
            ("normal", "连续3涨停", "", ""),
            ("normal", "一字涨停封板", "", ""),
            ("normal", "涨跌幅偏离值达15%", "", ""),
            ("normal", "量比超过5倍", "", ""),
            ("normal", "成交额超10亿", "", ""),
            ("主力净流入 80000", "normal news", "", ""),
        ]
        for ff, news, stock, ann in cases:
            needed, _ = _compute_lhb_force_decision(ff, news, stock, ann)
            assert needed is True, f"Expected force for: ff={ff[:30]}, news={news[:30]}"

    def test_fund_flow_failure_only_affects_fund_evidence(self):
        """资金流失败只影响资金流证据，不阻止 LHB force 条件继续判断。"""
        from tradingagents.graph.data_collector import _compute_lhb_force_decision

        needed, reason = _compute_lhb_force_decision(
            "个股资金流向数据获取失败",
            "该股严重异常波动",
            "",
        )
        assert needed is True
        assert "anomaly_condition" in reason
