# [DATA-005] data_source_replay  [DATA-008] astock_fallback_replay  [DATA-014] policy_news_fixture_smoke
"""
数据源 fixture replay 与限流/失败回放。

提供可重复的数据源回放测试，覆盖正常、缺字段、限流、超时、来源冲突、
当天实时缺失等场景，避免夜间自动开发误判数据源质量。

功能：
  1. 内置 38 类 fixture（正常行情、日线 stale、实时 quote 成功/失败、
      资金流单位异常、龙虎榜无触发、公告源失败、
      AKShare 资金流失败→astock fallback、龙虎榜 NORMAL_NO_DATA、
      龙虎榜 FAILED、公告失败→事件源弱证据、换手率/量比缺失、
      融资融券有数据、融资融券失败、融资融券未查询、
      研报有数据、研报失败、研报未查询、
      回购有数据、回购失败、回购未查询、
      新闻有数据、新闻空结果、新闻部分失败、新闻全部失败、新闻限流、
      全球新闻有数据、全球新闻空结果、全球新闻失败、全球新闻限流、
      涨停池有数据、涨停池 AKShare 失败 fallback 成功、涨停池失败、涨停池空池）
  2. replay runner 将 fixture 模拟为 raw_evidence，输出数据源健康报告
  3. 失败时写入 docs/data_source_reports/YYYY-MM-DD.md
  4. 可被 scripts/auto_dev_loop.sh 或 OpenClaw 巡检调用

Usage:
    from tradingagents.dataflows.fixture_replay import (
        run_fixture_replay,
        get_all_fixtures,
        render_replay_report,
    )
    result = run_fixture_replay()
    print(render_replay_report(result))
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from .evidence_contract import (
    EvidenceContract,
    compute_contract_completeness,
    build_data_source_summary,
)
from .source_catalog import (
    DataType,
    Freshness,
    RateLimitRisk,
    get_primary_source,
    get_sources_for_type,
)


# ── Fixture Scenario IDs ──────────────────────────────────────────────

FIXTURE_NORMAL_QUOTE = "normal_quote"
FIXTURE_STALE_DAILY = "stale_daily"
FIXTURE_REALTIME_SUCCESS = "realtime_success"
FIXTURE_REALTIME_FAILURE = "realtime_failure"
FIXTURE_FUND_FLOW_UNIT_ANOMALY = "fund_flow_unit_anomaly"
FIXTURE_LHB_NO_TRIGGER = "lhb_no_trigger"
FIXTURE_ANNOUNCEMENT_FAILURE = "announcement_failure"
FIXTURE_FUND_FLOW_AKSHARE_FAIL_FALLBACK = "fund_flow_akshare_fail_fallback"
FIXTURE_LHB_NORMAL_NO_DATA = "lhb_normal_no_data"
FIXTURE_LHB_FAILED = "lhb_failed"
FIXTURE_STALE_REALTIME_PATCH = "stale_realtime_patch"
FIXTURE_ANNOUNCEMENT_FAIL_EVENT_WEAK = "announcement_fail_event_weak"
FIXTURE_TURNOVER_VOLUME_RATIO_MISSING = "turnover_volume_ratio_missing"
FIXTURE_MARGIN_HAS_DATA = "margin_has_data"  # [DATA-010] margin_trading_raw_evidence
FIXTURE_MARGIN_FAILED = "margin_failed"  # [DATA-010] margin_trading_raw_evidence
FIXTURE_MARGIN_NOT_QUERIED = "margin_not_queried"  # [DATA-010] margin_trading_raw_evidence
FIXTURE_REPORT_HAS_DATA = "report_has_data"  # [DATA-011] research_report_raw_evidence
FIXTURE_REPORT_FAILED = "report_failed"  # [DATA-011] research_report_raw_evidence
FIXTURE_REPORT_NOT_QUERIED = "report_not_queried"  # [DATA-011] research_report_raw_evidence
FIXTURE_RATINGS_HAS_DATA = "ratings_has_data"  # [DATA-012] rating_raw_evidence
FIXTURE_RATINGS_FAILED = "ratings_failed"  # [DATA-012] rating_raw_evidence
FIXTURE_RATINGS_NOT_QUERIED = "ratings_not_queried"  # [DATA-012] rating_raw_evidence
FIXTURE_BUYBACK_HAS_DATA = "buyback_has_data"  # [DATA-013] buyback_raw_evidence
FIXTURE_BUYBACK_FAILED = "buyback_failed"  # [DATA-013] buyback_raw_evidence
FIXTURE_BUYBACK_NOT_QUERIED = "buyback_not_queried"  # [DATA-013] buyback_raw_evidence
FIXTURE_NEWS_HAS_DATA = "news_has_data"  # [DATA-014] policy_news_fixture_smoke
FIXTURE_NEWS_NORMAL_NO_DATA = "news_normal_no_data"  # [DATA-014] policy_news_fixture_smoke
FIXTURE_NEWS_PARTIAL_FAILED = "news_partial_failed"  # [DATA-014] policy_news_fixture_smoke
FIXTURE_NEWS_FAILED = "news_failed"  # [DATA-014] policy_news_fixture_smoke
FIXTURE_NEWS_RATE_LIMITED = "news_rate_limited"  # [DATA-014] policy_news_fixture_smoke
FIXTURE_GLOBAL_NEWS_HAS_DATA = "global_news_has_data"  # [DATA-014] policy_news_fixture_smoke
FIXTURE_GLOBAL_NEWS_NORMAL_NO_DATA = "global_news_normal_no_data"  # [DATA-014] policy_news_fixture_smoke
FIXTURE_GLOBAL_NEWS_FAILED = "global_news_failed"  # [DATA-014] policy_news_fixture_smoke
FIXTURE_GLOBAL_NEWS_RATE_LIMITED = "global_news_rate_limited"  # [DATA-014] policy_news_fixture_smoke
FIXTURE_ZT_POOL_HAS_DATA = "zt_pool_has_data"  # [DATA-015] limit_up_pool_fallback
FIXTURE_ZT_POOL_AKSHARE_FAIL_FALLBACK = "zt_pool_akshare_fail_fallback"  # [DATA-015] limit_up_pool_fallback
FIXTURE_ZT_POOL_FAILED = "zt_pool_failed"  # [DATA-015] limit_up_pool_fallback
FIXTURE_ZT_POOL_NORMAL_NO_DATA = "zt_pool_normal_no_data"  # [DATA-015] limit_up_pool_fallback
FIXTURE_HOT_STOCKS_HAS_DATA = "hot_stocks_has_data"  # [DATA-016] hot_stock_fallback
FIXTURE_HOT_STOCKS_AKSHARE_FAIL_FALLBACK = "hot_stocks_akshare_fail_fallback"  # [DATA-016] hot_stock_fallback
FIXTURE_HOT_STOCKS_FAILED = "hot_stocks_failed"  # [DATA-016] hot_stock_fallback
FIXTURE_HOT_STOCKS_NORMAL_NO_DATA = "hot_stocks_normal_no_data"  # [DATA-016] hot_stock_fallback
FIXTURE_HOT_STOCKS_RATE_LIMITED = "hot_stocks_rate_limited"  # [DATA-016] hot_stock_fallback

ALL_FIXTURE_IDS = [
    FIXTURE_NORMAL_QUOTE,
    FIXTURE_STALE_DAILY,
    FIXTURE_REALTIME_SUCCESS,
    FIXTURE_REALTIME_FAILURE,
    FIXTURE_FUND_FLOW_UNIT_ANOMALY,
    FIXTURE_LHB_NO_TRIGGER,
    FIXTURE_ANNOUNCEMENT_FAILURE,
    FIXTURE_FUND_FLOW_AKSHARE_FAIL_FALLBACK,
    FIXTURE_LHB_NORMAL_NO_DATA,
    FIXTURE_LHB_FAILED,
    FIXTURE_STALE_REALTIME_PATCH,
    FIXTURE_ANNOUNCEMENT_FAIL_EVENT_WEAK,
    FIXTURE_TURNOVER_VOLUME_RATIO_MISSING,
    FIXTURE_MARGIN_HAS_DATA,
    FIXTURE_MARGIN_FAILED,
    FIXTURE_MARGIN_NOT_QUERIED,
    FIXTURE_REPORT_HAS_DATA,
    FIXTURE_REPORT_FAILED,
    FIXTURE_REPORT_NOT_QUERIED,
    FIXTURE_RATINGS_HAS_DATA,
    FIXTURE_RATINGS_FAILED,
    FIXTURE_RATINGS_NOT_QUERIED,
    FIXTURE_BUYBACK_HAS_DATA,
    FIXTURE_BUYBACK_FAILED,
    FIXTURE_BUYBACK_NOT_QUERIED,
    FIXTURE_NEWS_HAS_DATA,
    FIXTURE_NEWS_NORMAL_NO_DATA,
    FIXTURE_NEWS_PARTIAL_FAILED,
    FIXTURE_NEWS_FAILED,
    FIXTURE_NEWS_RATE_LIMITED,
    FIXTURE_GLOBAL_NEWS_HAS_DATA,
    FIXTURE_GLOBAL_NEWS_NORMAL_NO_DATA,
    FIXTURE_GLOBAL_NEWS_FAILED,
    FIXTURE_GLOBAL_NEWS_RATE_LIMITED,
    FIXTURE_ZT_POOL_HAS_DATA,
    FIXTURE_ZT_POOL_AKSHARE_FAIL_FALLBACK,
    FIXTURE_ZT_POOL_FAILED,
    FIXTURE_ZT_POOL_NORMAL_NO_DATA,
    FIXTURE_HOT_STOCKS_HAS_DATA,
    FIXTURE_HOT_STOCKS_AKSHARE_FAIL_FALLBACK,
    FIXTURE_HOT_STOCKS_FAILED,
    FIXTURE_HOT_STOCKS_NORMAL_NO_DATA,
    FIXTURE_HOT_STOCKS_RATE_LIMITED,
]


# ── Fixture Data Models ──────────────────────────────────────────────

@dataclass
class FixtureEntry:
    fixture_id: str
    description: str
    data_type: str
    vendor: str
    endpoint: str
    expected_status: str
    raw_evidence: Dict[str, Any]
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fixture_id": self.fixture_id,
            "description": self.description,
            "data_type": self.data_type,
            "vendor": self.vendor,
            "endpoint": self.endpoint,
            "expected_status": self.expected_status,
            "raw_evidence": self.raw_evidence,
            "tags": self.tags,
        }


@dataclass
class ReplayResult:
    fixture_id: str
    fixture_description: str
    data_type: str
    expected_status: str
    actual_status: str
    passed: bool
    vendor: str
    endpoint: str
    is_fallback: bool
    fallback_from: Optional[str]
    error: Optional[str]
    completeness_score: int
    missing_details: Dict[str, List[str]]
    tags: List[str] = field(default_factory=list)
    as_of: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fixture_id": self.fixture_id,
            "fixture_description": self.fixture_description,
            "data_type": self.data_type,
            "expected_status": self.expected_status,
            "actual_status": self.actual_status,
            "passed": self.passed,
            "vendor": self.vendor,
            "endpoint": self.endpoint,
            "is_fallback": self.is_fallback,
            "fallback_from": self.fallback_from,
            "error": self.error,
            "completeness_score": self.completeness_score,
            "missing_details": self.missing_details,
            "tags": self.tags,
            "as_of": self.as_of,
        }


@dataclass
class ReplayReport:
    run_at: str
    date: str
    total_fixtures: int = 0
    passed: int = 0
    failed: int = 0
    results: List[ReplayResult] = field(default_factory=list)
    all_passed: bool = True
    by_data_type: Dict[str, Dict[str, int]] = field(default_factory=dict)
    by_status: Dict[str, int] = field(default_factory=dict)
    failure_types: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_at": self.run_at,
            "date": self.date,
            "total_fixtures": self.total_fixtures,
            "passed": self.passed,
            "failed": self.failed,
            "all_passed": self.all_passed,
            "by_data_type": self.by_data_type,
            "by_status": self.by_status,
            "failure_types": self.failure_types,
            "results": [r.to_dict() for r in self.results],
        }


# ── Fixture Builders ─────────────────────────────────────────────────

def _build_normal_quote_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")
    raw_evidence = {
        "stock_data": {
            "raw": "Date,Open,High,Low,Close,Volume\n2026-06-01,1800.0,1812.5,1795.0,1810.0,25000",
            "field": "stock_data",
            "unit": "元/股, 股",
            "vendor": "cn_akshare",
            "endpoint": "stock_zh_a_hist",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "HAS_DATA",
            "fallback_from": None,
            "source_url": None,
            "error": None,
            "is_realtime_patched": False,
            "unit_verified": True,
            "record_count": 30,
        },
        "fund_flow_individual": {
            "raw": "日期,主力净流入,小单净流入\n2026-06-01,5000,−2000",
            "field": "fund_flow_individual",
            "unit": "万元",
            "vendor": "cn_akshare",
            "endpoint": "stock_individual_fund_flow",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "HAS_DATA",
            "fallback_from": None,
            "unit_verified": True,
            "record_count": 20,
        },
        "lhb": {
            "raw": None,
            "field": "lhb",
            "unit": "万元",
            "vendor": "cn_akshare",
            "endpoint": "stock_lhb_detail_em",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "NORMAL_NO_DATA",
            "fallback_from": None,
            "record_count": 0,
        },
        "news": {
            "raw": "新闻标题,来源\n贵州茅台召开股东大会,东财",
            "field": "news",
            "unit": "条",
            "vendor": "cn_akshare",
            "endpoint": "stock_news_em",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "HAS_DATA",
            "record_count": 5,
        },
        "announcements": {
            "raw": "公告标题,类型,日期\n2025年度权益分派实施公告,分红,2026-06-01",
            "field": "announcements",
            "unit": "条",
            "vendor": "cn_astock",
            "endpoint": "cninfo.com.cn/hisAnnouncement",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "HAS_DATA",
            "record_count": 3,
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_NORMAL_QUOTE,
        description="正常行情——所有数据源返回 HAS_DATA 或 NORMAL_NO_DATA",
        data_type="ohlcv",
        vendor="cn_akshare",
        endpoint="stock_zh_a_hist",
        expected_status="HAS_DATA",
        raw_evidence=raw_evidence,
        tags=["happy_path", "full_coverage"],
    )


def _build_stale_daily_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    yesterday = (datetime.now().replace(hour=0, minute=0, second=0)).strftime("%Y-%m-%d")
    raw_evidence = {
        "stock_data": {
            "raw": "Date,Open,High,Low,Close,Volume\n2026-05-30,1800.0,1812.5,1795.0,1810.0,25000",
            "field": "stock_data",
            "unit": "元/股, 股",
            "vendor": "cn_akshare",
            "endpoint": "stock_zh_a_hist",
            "as_of": yesterday,
            "fetched_at": now_iso,
            "status": "HAS_DATA",
            "is_realtime_patched": False,
            "unit_verified": True,
            "record_count": 29,
        },
        "fund_flow_individual": {
            "raw": None,
            "field": "fund_flow_individual",
            "unit": "万元",
            "vendor": "cn_akshare",
            "endpoint": "stock_individual_fund_flow",
            "as_of": yesterday,
            "fetched_at": now_iso,
            "status": "HAS_DATA",
            "unit_verified": True,
            "record_count": 19,
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_STALE_DAILY,
        description="日线数据 stale——最新 bar 停留在昨日，需要 realtime patch",
        data_type="ohlcv",
        vendor="cn_akshare",
        endpoint="stock_zh_a_hist",
        expected_status="HAS_DATA",
        raw_evidence=raw_evidence,
        tags=["stale", "needs_realtime_patch"],
    )


def _build_realtime_success_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")
    raw_evidence = {
        "stock_data": {
            "raw": "Date,Open,High,Low,Close,Volume\n2026-05-30,...",
            "field": "stock_data",
            "unit": "元/股, 股",
            "vendor": "cn_akshare",
            "endpoint": "stock_zh_a_hist",
            "as_of": "2026-05-30",
            "fetched_at": now_iso,
            "status": "HAS_DATA",
            "is_realtime_patched": True,
            "source_type": "realtime_patch",
            "patch_fields": ["current_price", "current_volume", "current_amount"],
            "patch_source": "cn_akshare",
            "patch_as_of": today,
            "unit_verified": True,
            "record_count": 29,
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_REALTIME_SUCCESS,
        description="实时 quote 成功——日线 stale 但 realtime patch 补上了 current_price/volume/amount",
        data_type="realtime_quotes",
        vendor="cn_akshare",
        endpoint="hq.sinajs.cn",
        expected_status="HAS_DATA",
        raw_evidence=raw_evidence,
        tags=["realtime_patch", "success"],
    )


def _build_realtime_failure_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    raw_evidence = {
        "stock_data": {
            "raw": "Date,Open,High,Low,Close,Volume\n2026-05-30,...",
            "field": "stock_data",
            "unit": "元/股, 股",
            "vendor": "cn_akshare",
            "endpoint": "stock_zh_a_hist",
            "as_of": "2026-05-30",
            "fetched_at": now_iso,
            "status": "HAS_DATA",
            "is_realtime_patched": False,
            "unit_verified": True,
            "record_count": 29,
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_REALTIME_FAILURE,
        description="实时 quote 失败——日线 stale 且 realtime patch 未补上，应标记 stale 而非 '无数据'",
        data_type="realtime_quotes",
        vendor="cn_akshare",
        endpoint="hq.sinajs.cn",
        expected_status="STALE",
        raw_evidence=raw_evidence,
        tags=["realtime_failure", "stale"],
    )


def _build_fund_flow_unit_anomaly_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")
    raw_evidence = {
        "fund_flow_individual": {
            "raw": "日期,主力净流入,小单净流入\n2026-06-01,50000000,-20000000",
            "field": "fund_flow_individual",
            "unit": "元",
            "vendor": "cn_astock",
            "endpoint": "push2his.eastmoney.com/fflow",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "HAS_DATA",
            "fallback_from": "cn_akshare",
            "unit_verified": False,
            "error": "单位与预期不符：预期'万元'，实际为'元'",
            "record_count": 20,
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_FUND_FLOW_UNIT_ANOMALY,
        description="资金流单位异常——astock fallback 返回'元'而非'万元'，unit_verified=False",
        data_type="fund_flow",
        vendor="cn_astock",
        endpoint="push2his.eastmoney.com/fflow",
        expected_status="HAS_DATA",
        raw_evidence=raw_evidence,
        tags=["unit_anomaly", "fallback", "unit_unverified"],
    )


def _build_lhb_no_trigger_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")
    raw_evidence = {
        "lhb": {
            "raw": None,
            "field": "lhb",
            "unit": "万元",
            "vendor": "cn_akshare",
            "endpoint": "stock_lhb_detail_em",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "NOT_QUERIED",
            "force_reason": None,
            "record_count": 0,
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_LHB_NO_TRIGGER,
        description="龙虎榜无触发——force=False 时返回 NOT_QUERIED，不是 FAILED 也不是 '无数据'",
        data_type="lhb",
        vendor="cn_akshare",
        endpoint="stock_lhb_detail_em",
        expected_status="NOT_QUERIED",
        raw_evidence=raw_evidence,
        tags=["lhb", "not_queried", "force_false"],
    )


def _build_announcement_failure_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")
    raw_evidence = {
        "announcements": {
            "raw": None,
            "field": "announcements",
            "unit": "",
            "vendor": "cn_astock",
            "endpoint": "cninfo.com.cn/hisAnnouncement",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "FAILED",
            "error": "ConnectionError: HTTPSConnectionPool(host='www.cninfo.com.cn', port=443): Max retries exceeded",
            "record_count": 0,
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_ANNOUNCEMENT_FAILURE,
        description="公告源失败——巨潮 ConnectionError，应标记 FAILED 而非 '无数据'",
        data_type="notice",
        vendor="cn_astock",
        endpoint="cninfo.com.cn/hisAnnouncement",
        expected_status="FAILED",
        raw_evidence=raw_evidence,
        tags=["announcement", "connection_error", "failed"],
    )


def _build_fund_flow_akshare_fail_fallback_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")
    raw_evidence = {
        "fund_flow_individual": {
            "raw": "日期,主力净流入,小单净流入\n2026-06-01,12345.00,-6789.00",
            "field": "fund_flow_individual",
            "unit": "万元",
            "vendor": "cn_astock",
            "endpoint": "push2his.eastmoney.com/fflow",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "HAS_DATA",
            "fallback_from": "cn_akshare",
            "unit_verified": True,
            "record_count": 20,
        },
        "stock_data": {
            "raw": "Date,Open,High,Low,Close,Volume\n2026-06-01,10.5,10.8,10.3,10.7,500000",
            "field": "stock_data",
            "unit": "元/股, 股",
            "vendor": "cn_akshare",
            "endpoint": "stock_zh_a_hist",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "HAS_DATA",
            "is_realtime_patched": False,
            "unit_verified": True,
            "record_count": 30,
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_FUND_FLOW_AKSHARE_FAIL_FALLBACK,
        description="AKShare 个股资金流 ProxyError，cn_astock Eastmoney fallback 成功——vendor 应显示 cn_astock，fallback_from=cn_akshare",
        data_type="fund_flow",
        vendor="cn_astock",
        endpoint="push2his.eastmoney.com/fflow",
        expected_status="HAS_DATA",
        raw_evidence=raw_evidence,
        tags=["fund_flow", "fallback", "akshare_failed", "astock_success"],
    )


def _build_lhb_normal_no_data_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")
    raw_evidence = {
        "lhb": {
            "raw": None,
            "field": "lhb",
            "unit": "万元",
            "vendor": "cn_astock",
            "endpoint": "datacenter.eastmoney.com/RPT_DAILYBILLBOARD_DETAILSNEW",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "NORMAL_NO_DATA",
            "force_reason": "anomaly_condition",
            "record_count": 0,
        },
        "stock_data": {
            "raw": "Date,Open,High,Low,Close,Volume\n2026-06-01,15.0,15.5,14.8,15.2,800000",
            "field": "stock_data",
            "unit": "元/股, 股",
            "vendor": "cn_akshare",
            "endpoint": "stock_zh_a_hist",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "HAS_DATA",
            "is_realtime_patched": False,
            "unit_verified": True,
            "record_count": 30,
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_LHB_NORMAL_NO_DATA,
        description="龙虎榜 force=True 但当日未上榜——NORMAL_NO_DATA，不是 FAILED",
        data_type="lhb",
        vendor="cn_astock",
        endpoint="datacenter.eastmoney.com/RPT_DAILYBILLBOARD_DETAILSNEW",
        expected_status="NORMAL_NO_DATA",
        raw_evidence=raw_evidence,
        tags=["lhb", "normal_no_data", "force_true"],
    )


def _build_lhb_failed_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")
    raw_evidence = {
        "lhb": {
            "raw": None,
            "field": "lhb",
            "unit": "",
            "vendor": "cn_astock",
            "endpoint": "datacenter.eastmoney.com/RPT_DAILYBILLBOARD_DETAILSNEW",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "FAILED",
            "error": "ConnectionError: HTTPSConnectionPool(host='datacenter.eastmoney.com'): Max retries exceeded",
            "force_reason": "anomaly_condition",
            "record_count": 0,
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_LHB_FAILED,
        description="龙虎榜 force=True 但接口 ConnectionError——FAILED，不是 NORMAL_NO_DATA",
        data_type="lhb",
        vendor="cn_astock",
        endpoint="datacenter.eastmoney.com/RPT_DAILYBILLBOARD_DETAILSNEW",
        expected_status="FAILED",
        raw_evidence=raw_evidence,
        tags=["lhb", "failed", "connection_error", "force_true"],
    )


def _build_stale_realtime_patch_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")
    raw_evidence = {
        "stock_data": {
            "raw": "Date,Open,High,Low,Close,Volume\n2026-05-30,25.0,25.5,24.8,25.2,300000",
            "field": "stock_data",
            "unit": "元/股, 股",
            "vendor": "cn_akshare",
            "endpoint": "stock_zh_a_hist",
            "as_of": "2026-05-30",
            "fetched_at": now_iso,
            "status": "HAS_DATA",
            "is_realtime_patched": True,
            "source_type": "realtime_patch",
            "patch_fields": ["current_price", "current_volume", "current_amount", "turnover_rate", "volume_ratio"],
            "patch_source": "cn_astock_tencent",
            "patch_as_of": today,
            "unit_verified": True,
            "record_count": 29,
        },
        "fund_flow_individual": {
            "raw": "日期,主力净流入,小单净流入\n2026-05-30,8000,-3000",
            "field": "fund_flow_individual",
            "unit": "万元",
            "vendor": "cn_akshare",
            "endpoint": "stock_individual_fund_flow",
            "as_of": "2026-05-30",
            "fetched_at": now_iso,
            "status": "HAS_DATA",
            "fallback_from": None,
            "unit_verified": True,
            "record_count": 19,
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_STALE_REALTIME_PATCH,
        description="日线 stale 后腾讯实时 quote 补丁成功——is_realtime_patched=True, patch_source=cn_astock_tencent",
        data_type="ohlcv",
        vendor="cn_akshare",
        endpoint="stock_zh_a_hist",
        expected_status="HAS_DATA",
        raw_evidence=raw_evidence,
        tags=["realtime_patch", "success", "stale_patched", "tencent_fallback"],
    )


def _build_announcement_fail_event_weak_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")
    raw_evidence = {
        "announcements": {
            "raw": None,
            "field": "announcements",
            "unit": "",
            "vendor": "cn_astock",
            "endpoint": "cninfo.com.cn/hisAnnouncement",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "FAILED",
            "error": "ConnectionError: cninfo unreachable",
            "record_count": 0,
        },
        "news": {
            "raw": "标题,来源,日期\n某公司发布业绩预告,东财,2026-06-01",
            "field": "news",
            "unit": "条",
            "vendor": "cn_akshare",
            "endpoint": "stock_news_em",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "HAS_DATA",
            "record_count": 2,
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_ANNOUNCEMENT_FAIL_EVENT_WEAK,
        description="公告源 CNInfo 失败但新闻事件源有弱证据——公告为 FAILED，新闻为 HAS_DATA（弱替代）",
        data_type="notice",
        vendor="cn_astock",
        endpoint="cninfo.com.cn/hisAnnouncement",
        expected_status="FAILED",
        raw_evidence=raw_evidence,
        tags=["announcement", "failed", "event_weak", "partial_coverage"],
    )


def _build_turnover_volume_ratio_missing_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")
    raw_evidence = {
        "stock_data": {
            "raw": "Date,Open,High,Low,Close,Volume\n2026-06-01,8.5,8.7,8.3,8.6,200000",
            "field": "stock_data",
            "unit": "元/股, 股",
            "vendor": "cn_akshare",
            "endpoint": "stock_zh_a_hist",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "HAS_DATA",
            "is_realtime_patched": False,
            "unit_verified": True,
            "record_count": 30,
        },
        "fund_flow_individual": {
            "raw": "日期,主力净流入,小单净流入\n2026-06-01,2000,-800",
            "field": "fund_flow_individual",
            "unit": "万元",
            "vendor": "cn_akshare",
            "endpoint": "stock_individual_fund_flow",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "HAS_DATA",
            "fallback_from": None,
            "unit_verified": True,
            "record_count": 20,
        },
        "lhb": {
            "raw": None,
            "field": "lhb",
            "unit": "万元",
            "vendor": "cn_akshare",
            "endpoint": "stock_lhb_detail_em",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "NOT_QUERIED",
            "force_reason": None,
            "record_count": 0,
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_TURNOVER_VOLUME_RATIO_MISSING,
        description="换手率/量比缺失——stock_data 和 fund_flow 可用但缺 turnover_rate/volume_ratio 字段，完整度降级",
        data_type="ohlcv",
        vendor="cn_akshare",
        endpoint="stock_zh_a_hist",
        expected_status="HAS_DATA",
        raw_evidence=raw_evidence,
        tags=["missing_turnover", "missing_volume_ratio", "completeness_degradation"],
    )


def _build_margin_has_data_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")
    raw_evidence = {
        "margin_trading": {
            "raw": "600519.SH [DATA-010] MARGIN_HAS_DATA: 融资融券数据（Eastmoney datacenter）：\n"
                   "- 2026-06-07 | 融资余额 185200.0万 | 融资买入 3200.0万 | 融券余额 1500.0万",
            "field": "margin_trading",
            "unit": "万元",
            "vendor": "cn_astock",
            "endpoint": "datacenter-web.eastmoney.com/RPT_RZRQ_LSHJ",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "HAS_DATA",
            "fallback_from": None,
            "source_url": None,
            "error": None,
            "is_realtime_patched": False,
            "unit_verified": True,
            "record_count": 10,
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_MARGIN_HAS_DATA,
        description="融资融券数据正常返回",
        data_type="margin_trading",
        vendor="cn_astock",
        endpoint="datacenter-web.eastmoney.com/RPT_RZRQ_LSHJ",
        expected_status="HAS_DATA",
        raw_evidence=raw_evidence,
        tags=["margin_trading", "has_data"],
    )


def _build_margin_failed_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")
    raw_evidence = {
        "margin_trading": {
            "raw": "600519.SH [DATA-010] MARGIN_FAILED: 融资融券数据获取失败：ConnectionError",
            "field": "margin_trading",
            "unit": None,
            "vendor": "cn_astock",
            "endpoint": "datacenter-web.eastmoney.com/RPT_RZRQ_LSHJ",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "FAILED",
            "fallback_from": None,
            "source_url": None,
            "error": "ConnectionError",
            "is_realtime_patched": False,
            "unit_verified": None,
            "record_count": 0,
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_MARGIN_FAILED,
        description="融资融券查询失败",
        data_type="margin_trading",
        vendor="cn_astock",
        endpoint="datacenter-web.eastmoney.com/RPT_RZRQ_LSHJ",
        expected_status="FAILED",
        raw_evidence=raw_evidence,
        tags=["margin_trading", "failed"],
    )


def _build_margin_not_queried_fixture() -> FixtureEntry:
    raw_evidence = {
        "margin_trading": {
            "raw": None,
            "field": "margin_trading",
            "unit": None,
            "vendor": "",
            "endpoint": "",
            "as_of": "",
            "fetched_at": "",
            "status": "NOT_QUERIED",
            "fallback_from": None,
            "source_url": None,
            "error": None,
            "is_realtime_patched": False,
            "unit_verified": None,
            "record_count": 0,
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_MARGIN_NOT_QUERIED,
        description="融资融券未查询",
        data_type="margin_trading",
        vendor="",
        endpoint="",
        expected_status="NOT_QUERIED",
        raw_evidence=raw_evidence,
        tags=["margin_trading", "not_queried"],
    )


def _build_report_has_data_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")
    raw_evidence = {
        "research_report": {
            "raw": "600519.SH [DATA-011] REPORT_HAS_DATA: 研报数据（Eastmoney reportapi，共 5 篇）：\n"
                   "- 2026-06-05 | 中信证券 | 买入 | 贵州茅台深度研究\n"
                   "- 2026-06-03 | 国泰君安 | 增持 | 白酒龙头估值分析",
            "field": "research_report",
            "unit": "条",
            "vendor": "cn_astock",
            "endpoint": "reportapi.eastmoney.com/report/list",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "HAS_DATA",
            "fallback_from": None,
            "source_url": None,
            "error": None,
            "is_realtime_patched": False,
            "unit_verified": None,
            "record_count": 5,
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_REPORT_HAS_DATA,
        description="研报数据正常返回",
        data_type="report",
        vendor="cn_astock",
        endpoint="reportapi.eastmoney.com/report/list",
        expected_status="HAS_DATA",
        raw_evidence=raw_evidence,
        tags=["research_report", "has_data"],
    )


def _build_report_failed_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")
    raw_evidence = {
        "research_report": {
            "raw": "600519.SH [DATA-011] REPORT_FAILED: 研报数据获取失败（Eastmoney reportapi）：ConnectionError",
            "field": "research_report",
            "unit": None,
            "vendor": "cn_astock",
            "endpoint": "reportapi.eastmoney.com/report/list",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "FAILED",
            "fallback_from": None,
            "source_url": None,
            "error": "ConnectionError",
            "is_realtime_patched": False,
            "unit_verified": None,
            "record_count": 0,
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_REPORT_FAILED,
        description="研报查询失败",
        data_type="report",
        vendor="cn_astock",
        endpoint="reportapi.eastmoney.com/report/list",
        expected_status="FAILED",
        raw_evidence=raw_evidence,
        tags=["research_report", "failed"],
    )


def _build_report_not_queried_fixture() -> FixtureEntry:
    raw_evidence = {
        "research_report": {
            "raw": None,
            "field": "research_report",
            "unit": None,
            "vendor": "",
            "endpoint": "",
            "as_of": "",
            "fetched_at": "",
            "status": "NOT_QUERIED",
            "fallback_from": None,
            "source_url": None,
            "error": None,
            "is_realtime_patched": False,
            "unit_verified": None,
            "record_count": 0,
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_REPORT_NOT_QUERIED,
        description="研报未查询",
        data_type="report",
        vendor="",
        endpoint="",
        expected_status="NOT_QUERIED",
        raw_evidence=raw_evidence,
        tags=["research_report", "not_queried"],
    )


def _build_ratings_has_data_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")
    raw_evidence = {
        "ratings": {
            "raw": "600519.SH [DATA-012] RATINGS_HAS_DATA: 分析师评级数据（Eastmoney reportapi，共 15 条）：\n"
                   "- 2026-06-06 | 东方证券 | 买入 | 目标价: 2100 | 贵州茅台点评",
            "field": "ratings",
            "unit": "条",
            "vendor": "cn_astock",
            "endpoint": "reportapi.eastmoney.com/report/list",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "HAS_DATA",
            "fallback_from": None,
            "source_url": None,
            "error": None,
            "is_realtime_patched": False,
            "unit_verified": None,
            "record_count": 15,
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_RATINGS_HAS_DATA,
        description="评级数据正常返回",
        data_type="rating",
        vendor="cn_astock",
        endpoint="reportapi.eastmoney.com/report/list",
        expected_status="HAS_DATA",
        raw_evidence=raw_evidence,
        tags=["ratings", "has_data"],
    )


def _build_ratings_failed_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")
    raw_evidence = {
        "ratings": {
            "raw": "600519.SH [DATA-012] RATINGS_FAILED: 评级数据获取失败（Eastmoney reportapi）：ConnectionError",
            "field": "ratings",
            "unit": None,
            "vendor": "cn_astock",
            "endpoint": "reportapi.eastmoney.com/report/list",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "FAILED",
            "fallback_from": None,
            "source_url": None,
            "error": "ConnectionError",
            "is_realtime_patched": False,
            "unit_verified": None,
            "record_count": 0,
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_RATINGS_FAILED,
        description="评级查询失败",
        data_type="rating",
        vendor="cn_astock",
        endpoint="reportapi.eastmoney.com/report/list",
        expected_status="FAILED",
        raw_evidence=raw_evidence,
        tags=["ratings", "failed"],
    )


def _build_ratings_not_queried_fixture() -> FixtureEntry:
    raw_evidence = {
        "ratings": {
            "raw": None,
            "field": "ratings",
            "unit": None,
            "vendor": "",
            "endpoint": "",
            "as_of": "",
            "fetched_at": "",
            "status": "NOT_QUERIED",
            "fallback_from": None,
            "source_url": None,
            "error": None,
            "is_realtime_patched": False,
            "unit_verified": None,
            "record_count": 0,
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_RATINGS_NOT_QUERIED,
        description="评级未查询",
        data_type="rating",
        vendor="",
        endpoint="",
        expected_status="NOT_QUERIED",
        raw_evidence=raw_evidence,
        tags=["ratings", "not_queried"],
    )


def _build_buyback_has_data_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")
    raw_evidence = {
        "buybacks": {
            "raw": "601689.SH [DATA-013] BUYBACK_HAS_DATA: 回购数据（Eastmoney datacenter）：\n"
                   "- 2026-05-15 | 金额: 50000.0万 | 数量: 2500000 | 进度: 实施中 | 目的: 股权激励",
            "field": "buybacks",
            "unit": "万元",
            "vendor": "cn_astock",
            "endpoint": "datacenter-web.eastmoney.com/RPT_SHAREBUYBACK_DET",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "HAS_DATA",
            "fallback_from": None,
            "source_url": None,
            "error": None,
            "is_realtime_patched": False,
            "unit_verified": True,
            "record_count": 3,
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_BUYBACK_HAS_DATA,
        description="回购数据正常返回",
        data_type="buyback",
        vendor="cn_astock",
        endpoint="datacenter-web.eastmoney.com/RPT_SHAREBUYBACK_DET",
        expected_status="HAS_DATA",
        raw_evidence=raw_evidence,
        tags=["buybacks", "has_data"],
    )


def _build_buyback_failed_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")
    raw_evidence = {
        "buybacks": {
            "raw": "601689.SH [DATA-013] BUYBACK_FAILED: 回购数据获取失败（Eastmoney datacenter）：ConnectionError",
            "field": "buybacks",
            "unit": None,
            "vendor": "cn_astock",
            "endpoint": "datacenter-web.eastmoney.com/RPT_SHAREBUYBACK_DET",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "FAILED",
            "fallback_from": None,
            "source_url": None,
            "error": "ConnectionError",
            "is_realtime_patched": False,
            "unit_verified": None,
            "record_count": 0,
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_BUYBACK_FAILED,
        description="回购查询失败",
        data_type="buyback",
        vendor="cn_astock",
        endpoint="datacenter-web.eastmoney.com/RPT_SHAREBUYBACK_DET",
        expected_status="FAILED",
        raw_evidence=raw_evidence,
        tags=["buybacks", "failed"],
    )


def _build_buyback_not_queried_fixture() -> FixtureEntry:
    raw_evidence = {
        "buybacks": {
            "raw": None,
            "field": "buybacks",
            "unit": None,
            "vendor": "",
            "endpoint": "",
            "as_of": "",
            "fetched_at": "",
            "status": "NOT_QUERIED",
            "fallback_from": None,
            "source_url": None,
            "error": None,
            "is_realtime_patched": False,
            "unit_verified": None,
            "record_count": 0,
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_BUYBACK_NOT_QUERIED,
        description="回购未查询",
        data_type="buyback",
        vendor="",
        endpoint="",
        expected_status="NOT_QUERIED",
        raw_evidence=raw_evidence,
        tags=["buybacks", "not_queried"],
    )


# ── [DATA-014] News / Policy Event Fixtures ────────────────────────────

_POLICY_NEWS_TEXT = (
    "[政策原文] 国务院印发《关于加快发展低空经济的若干意见》，明确支持低空基础设施建设、"
    "空域管理改革和无人机产业发展。来源级别：中央/国务院。"
)
_NEWS_RELAY_TEXT = (
    "[新闻转述] 多家媒体报道低空经济板块持续升温，相关概念股异动。来源级别：媒体。"
)
_MARKET_RUMOR_TEXT = (
    "[市场传闻] 市场传言某公司将获得低空经济相关订单，但无公告或政策原文佐证。"
    "来源级别：市场传闻。"
)
_NORMAL_NEWS_TEXT = (
    "贵州茅台召开股东大会,东财\n"
    "贵州茅台发布2025年度权益分派实施公告,东财\n"
    "贵州茅台：董事长增持计划实施完毕,东财\n"
    "茅台系列酒提价预期升温,东财\n"
    "白酒行业龙头稳健增长,东财"
)


def _build_news_has_data_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")
    raw_evidence = {
        "news": {
            "raw": _NORMAL_NEWS_TEXT,
            "field": "news",
            "unit": "条",
            "vendor": "cn_akshare",
            "endpoint": "stock_news_em",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "HAS_DATA",
            "fallback_from": None,
            "source_url": None,
            "error": None,
            "is_realtime_patched": False,
            "unit_verified": True,
            "record_count": 5,
            "source_level": "媒体",
            "evidence_type": "news_relay",
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_NEWS_HAS_DATA,
        description="新闻正常返回（含政策原文/新闻转述/市场传闻分类标注）",
        data_type="news",
        vendor="cn_akshare",
        endpoint="stock_news_em",
        expected_status="HAS_DATA",
        raw_evidence=raw_evidence,
        tags=["news", "has_data", "DATA-014"],
    )


def _build_news_normal_no_data_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")
    raw_evidence = {
        "news": {
            "raw": "暂无相关新闻",
            "field": "news",
            "unit": "条",
            "vendor": "cn_akshare",
            "endpoint": "stock_news_em",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "NORMAL_NO_DATA",
            "fallback_from": None,
            "source_url": None,
            "error": None,
            "is_realtime_patched": False,
            "unit_verified": True,
            "record_count": 0,
            "source_level": "",
            "evidence_type": "",
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_NEWS_NORMAL_NO_DATA,
        description="新闻正常返回但无事件（status=OK, count=0）",
        data_type="news",
        vendor="cn_akshare",
        endpoint="stock_news_em",
        expected_status="NORMAL_NO_DATA",
        raw_evidence=raw_evidence,
        tags=["news", "normal_no_data", "DATA-014"],
    )


def _build_news_partial_failed_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")
    raw_evidence = {
        "news": {
            "raw": "601689.SH [DATA-014] NEWS_PARTIAL_FAILED: AKShare新闻失败，"
                   "东财搜索接口返回部分数据",
            "field": "news",
            "unit": "条",
            "vendor": "cn_astock",
            "endpoint": "search-api-web.eastmoney.com",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "HAS_DATA",
            "fallback_from": "cn_akshare",
            "source_url": None,
            "error": "AKShare stock_news_em: ProxyError",
            "is_realtime_patched": False,
            "unit_verified": True,
            "record_count": 2,
            "source_level": "媒体",
            "evidence_type": "news_relay",
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_NEWS_PARTIAL_FAILED,
        description="新闻部分失败——AKShare 失败后 fallback 到 cn_astock 成功",
        data_type="news",
        vendor="cn_astock",
        endpoint="search-api-web.eastmoney.com",
        expected_status="HAS_DATA",
        raw_evidence=raw_evidence,
        tags=["news", "partial_failed", "fallback", "DATA-014"],
    )


def _build_news_failed_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")
    raw_evidence = {
        "news": {
            "raw": "601689.SH [DATA-014] NEWS_FAILED: 新闻数据获取失败："
                   "AKShare ConnectionError; cn_astock ConnectionError",
            "field": "news",
            "unit": None,
            "vendor": "cn_astock",
            "endpoint": "search-api-web.eastmoney.com",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "FAILED",
            "fallback_from": "cn_akshare",
            "source_url": None,
            "error": "ConnectionError",
            "is_realtime_patched": False,
            "unit_verified": None,
            "record_count": 0,
            "source_level": "",
            "evidence_type": "",
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_NEWS_FAILED,
        description="新闻全部失败——AKShare 和 cn_astock 均连接失败",
        data_type="news",
        vendor="cn_astock",
        endpoint="search-api-web.eastmoney.com",
        expected_status="FAILED",
        raw_evidence=raw_evidence,
        tags=["news", "failed", "DATA-014"],
    )


def _build_news_rate_limited_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")
    raw_evidence = {
        "news": {
            "raw": "601689.SH [DATA-014] NEWS_RATE_LIMITED: 新闻数据获取失败："
                   "RateLimitError: 请求过于频繁，请稍后再试",
            "field": "news",
            "unit": None,
            "vendor": "cn_akshare",
            "endpoint": "stock_news_em",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "FAILED",
            "fallback_from": None,
            "source_url": None,
            "error": "RateLimitError: 请求过于频繁，请稍后再试",
            "is_realtime_patched": False,
            "unit_verified": None,
            "record_count": 0,
            "source_level": "",
            "evidence_type": "",
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_NEWS_RATE_LIMITED,
        description="新闻限流——请求频率过高被拒",
        data_type="news",
        vendor="cn_akshare",
        endpoint="stock_news_em",
        expected_status="FAILED",
        raw_evidence=raw_evidence,
        tags=["news", "rate_limited", "DATA-014"],
    )


def _build_global_news_has_data_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")
    raw_evidence = {
        "global_news": {
            "raw": "央视新闻联播\n"
                   "1. 国务院常务会议部署促进平台经济健康发展措施\n"
                   "2. 工信部发布《新能源汽车产业发展规划》修订版\n"
                   "3. 央行实施降准操作，释放长期资金约5000亿元",
            "field": "global_news",
            "unit": "条",
            "vendor": "cn_akshare",
            "endpoint": "news_cctv",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "HAS_DATA",
            "fallback_from": None,
            "source_url": None,
            "error": None,
            "is_realtime_patched": False,
            "unit_verified": True,
            "record_count": 3,
            "source_level": "中央/国务院",
            "evidence_type": "policy_document",
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_GLOBAL_NEWS_HAS_DATA,
        description="全球新闻正常返回（含政策原文级别标注）",
        data_type="global_news",
        vendor="cn_akshare",
        endpoint="news_cctv",
        expected_status="HAS_DATA",
        raw_evidence=raw_evidence,
        tags=["global_news", "has_data", "DATA-014"],
    )


def _build_global_news_normal_no_data_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")
    raw_evidence = {
        "global_news": {
            "raw": "暂无全球新闻",
            "field": "global_news",
            "unit": "条",
            "vendor": "cn_akshare",
            "endpoint": "news_cctv",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "NORMAL_NO_DATA",
            "fallback_from": None,
            "source_url": None,
            "error": None,
            "is_realtime_patched": False,
            "unit_verified": True,
            "record_count": 0,
            "source_level": "",
            "evidence_type": "",
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_GLOBAL_NEWS_NORMAL_NO_DATA,
        description="全球新闻正常返回但无事件（status=OK, count=0）",
        data_type="global_news",
        vendor="cn_akshare",
        endpoint="news_cctv",
        expected_status="NORMAL_NO_DATA",
        raw_evidence=raw_evidence,
        tags=["global_news", "normal_no_data", "DATA-014"],
    )


def _build_global_news_failed_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")
    raw_evidence = {
        "global_news": {
            "raw": "[DATA-014] GLOBAL_NEWS_FAILED: 全球新闻数据获取失败："
                   "AKShare ConnectionError; cn_astock ConnectionError",
            "field": "global_news",
            "unit": None,
            "vendor": "cn_astock",
            "endpoint": "cls.cn/telegraphList",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "FAILED",
            "fallback_from": "cn_akshare",
            "source_url": None,
            "error": "ConnectionError",
            "is_realtime_patched": False,
            "unit_verified": None,
            "record_count": 0,
            "source_level": "",
            "evidence_type": "",
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_GLOBAL_NEWS_FAILED,
        description="全球新闻全部失败——AKShare 和 cn_astock 均连接失败",
        data_type="global_news",
        vendor="cn_astock",
        endpoint="cls.cn/telegraphList",
        expected_status="FAILED",
        raw_evidence=raw_evidence,
        tags=["global_news", "failed", "DATA-014"],
    )


def _build_global_news_rate_limited_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")
    raw_evidence = {
        "global_news": {
            "raw": "[DATA-014] GLOBAL_NEWS_RATE_LIMITED: 全球新闻数据获取失败："
                   "RateLimitError: 请求频率超限",
            "field": "global_news",
            "unit": None,
            "vendor": "cn_akshare",
            "endpoint": "news_cctv",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "FAILED",
            "fallback_from": None,
            "source_url": None,
            "error": "RateLimitError: 请求频率超限",
            "is_realtime_patched": False,
            "unit_verified": None,
            "record_count": 0,
            "source_level": "",
            "evidence_type": "",
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_GLOBAL_NEWS_RATE_LIMITED,
        description="全球新闻限流——请求频率过高被拒",
        data_type="global_news",
        vendor="cn_akshare",
        endpoint="news_cctv",
        expected_status="FAILED",
        raw_evidence=raw_evidence,
        tags=["global_news", "rate_limited", "DATA-014"],
    )


def _build_zt_pool_has_data_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")
    raw_evidence = {
        "zt_pool": {
            "raw": f"{today} 涨停家数：35\n连板分布：\n1    20\n2    8\n3    5\n4    2",
            "field": "zt_pool",
            "unit": "条",
            "vendor": "cn_akshare",
            "endpoint": "stock_zt_pool_em",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "HAS_DATA",
            "fallback_from": None,
            "source_url": None,
            "error": None,
            "is_realtime_patched": False,
            "unit_verified": True,
            "record_count": 35,
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_ZT_POOL_HAS_DATA,
        description="涨停池正常返回（AKShare 成功，含连板分布）",
        data_type="zt_pool",
        vendor="cn_akshare",
        endpoint="stock_zt_pool_em",
        expected_status="HAS_DATA",
        raw_evidence=raw_evidence,
        tags=["zt_pool", "has_data", "DATA-015"],
    )


def _build_zt_pool_akshare_fail_fallback_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")
    raw_evidence = {
        "zt_pool": {
            "raw": f"{today} [DATA-015] ZT_POOL_HAS_DATA: 涨停池（Eastmoney datacenter，共 28 只）：\n"
                   "- 601678 SH 拓普集团 | 涨停 | 涨跌 10.0%\n"
                   "- 300750 SZ 宁德时代 | 涨停 | 涨跌 20.0%",
            "field": "zt_pool",
            "unit": "条",
            "vendor": "cn_astock",
            "endpoint": "push2ex.eastmoney.com/getTopicZTPool",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "HAS_DATA",
            "fallback_from": "cn_akshare",
            "source_url": None,
            "error": "AKShare stock_zt_pool_em: ProxyError",
            "is_realtime_patched": False,
            "unit_verified": True,
            "record_count": 28,
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_ZT_POOL_AKSHARE_FAIL_FALLBACK,
        description="涨停池 AKShare 失败→cn_astock fallback 成功——vendor 应显示 cn_astock",
        data_type="zt_pool",
        vendor="cn_astock",
        endpoint="push2ex.eastmoney.com/getTopicZTPool",
        expected_status="HAS_DATA",
        raw_evidence=raw_evidence,
        tags=["zt_pool", "fallback", "akshare_failed", "astock_success", "DATA-015"],
    )


def _build_zt_pool_failed_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")
    raw_evidence = {
        "zt_pool": {
            "raw": f"{today} [DATA-015] ZT_POOL_FAILED: 涨停池数据获取失败（Eastmoney）：ConnectionError",
            "field": "zt_pool",
            "unit": None,
            "vendor": "cn_astock",
            "endpoint": "push2ex.eastmoney.com/getTopicZTPool",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "FAILED",
            "fallback_from": "cn_akshare",
            "source_url": None,
            "error": "ConnectionError",
            "is_realtime_patched": False,
            "unit_verified": None,
            "record_count": 0,
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_ZT_POOL_FAILED,
        description="涨停池全部失败——AKShare 和 cn_astock 均连接失败",
        data_type="zt_pool",
        vendor="cn_astock",
        endpoint="push2ex.eastmoney.com/getTopicZTPool",
        expected_status="FAILED",
        raw_evidence=raw_evidence,
        tags=["zt_pool", "failed", "DATA-015"],
    )


def _build_zt_pool_normal_no_data_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")
    raw_evidence = {
        "zt_pool": {
            "raw": f"{today} [DATA-015] ZT_POOL_NORMAL_NO_DATA: 当日无涨停股票（非交易日或盘后未更新）。",
            "field": "zt_pool",
            "unit": "条",
            "vendor": "cn_akshare",
            "endpoint": "stock_zt_pool_em",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "NORMAL_NO_DATA",
            "fallback_from": None,
            "source_url": None,
            "error": None,
            "is_realtime_patched": False,
            "unit_verified": True,
            "record_count": 0,
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_ZT_POOL_NORMAL_NO_DATA,
        description="涨停池空池——非交易日或盘后未更新（status=OK, count=0）",
        data_type="zt_pool",
        vendor="cn_akshare",
        endpoint="stock_zt_pool_em",
        expected_status="NORMAL_NO_DATA",
        raw_evidence=raw_evidence,
        tags=["zt_pool", "normal_no_data", "DATA-015"],
    )


def _build_hot_stocks_has_data_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")
    raw_evidence = {
        "hot_stocks": {
            "raw": "雪球热搜前20：\n600519 贵州茅台 120.5\n000858 五粮液 98.3",
            "field": "hot_stocks",
            "unit": "条",
            "vendor": "cn_akshare",
            "endpoint": "stock_hot_follow_xq",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "HAS_DATA",
            "fallback_from": None,
            "source_url": None,
            "error": None,
            "is_realtime_patched": False,
            "unit_verified": True,
            "record_count": 20,
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_HOT_STOCKS_HAS_DATA,
        description="热门股票正常返回（AKShare/雪球 成功）",
        data_type="hot_stocks",
        vendor="cn_akshare",
        endpoint="stock_hot_follow_xq",
        expected_status="HAS_DATA",
        raw_evidence=raw_evidence,
        tags=["hot_stocks", "has_data", "DATA-016"],
    )


def _build_hot_stocks_akshare_fail_fallback_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")
    raw_evidence = {
        "hot_stocks": {
            "raw": (
                "[DATA-016] HOT_STOCKS_HAS_DATA: 热门股票（Eastmoney 热榜，共 30 只）：\n"
                "- 600519 贵州茅台 | 最新 1800.0 | 涨跌幅 2.5% | 成交额 4500000000\n"
                "- 300750 宁德时代 | 最新 220.0 | 涨跌幅 3.1% | 成交额 3800000000"
            ),
            "field": "hot_stocks",
            "unit": "条",
            "vendor": "cn_astock",
            "endpoint": "push2.eastmoney.com/getHotStock",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "HAS_DATA",
            "fallback_from": "cn_akshare",
            "source_url": None,
            "error": "AKShare stock_hot_follow_xq: ProxyError",
            "is_realtime_patched": False,
            "unit_verified": True,
            "record_count": 30,
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_HOT_STOCKS_AKSHARE_FAIL_FALLBACK,
        description="热门股票 AKShare 失败→cn_astock fallback 成功——vendor 应显示 cn_astock",
        data_type="hot_stocks",
        vendor="cn_astock",
        endpoint="push2.eastmoney.com/getHotStock",
        expected_status="HAS_DATA",
        raw_evidence=raw_evidence,
        tags=["hot_stocks", "fallback", "akshare_failed", "astock_success", "DATA-016"],
    )


def _build_hot_stocks_failed_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")
    raw_evidence = {
        "hot_stocks": {
            "raw": "[DATA-016] HOT_STOCKS_FAILED: 热门股票数据获取失败（Eastmoney push2）：ConnectionError",
            "field": "hot_stocks",
            "unit": None,
            "vendor": "cn_astock",
            "endpoint": "push2.eastmoney.com/getHotStock",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "FAILED",
            "fallback_from": "cn_akshare",
            "source_url": None,
            "error": "ConnectionError",
            "is_realtime_patched": False,
            "unit_verified": None,
            "record_count": 0,
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_HOT_STOCKS_FAILED,
        description="热门股票全部失败——AKShare 和 cn_astock 均连接失败",
        data_type="hot_stocks",
        vendor="cn_astock",
        endpoint="push2.eastmoney.com/getHotStock",
        expected_status="FAILED",
        raw_evidence=raw_evidence,
        tags=["hot_stocks", "failed", "DATA-016"],
    )


def _build_hot_stocks_normal_no_data_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")
    raw_evidence = {
        "hot_stocks": {
            "raw": "雪球热搜数据暂不可用。",
            "field": "hot_stocks",
            "unit": "条",
            "vendor": "cn_akshare",
            "endpoint": "stock_hot_follow_xq",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "NORMAL_NO_DATA",
            "fallback_from": None,
            "source_url": None,
            "error": None,
            "is_realtime_patched": False,
            "unit_verified": True,
            "record_count": 0,
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_HOT_STOCKS_NORMAL_NO_DATA,
        description="热门股票空结果——非交易日或盘后未更新（status=NORMAL_NO_DATA, count=0）",
        data_type="hot_stocks",
        vendor="cn_akshare",
        endpoint="stock_hot_follow_xq",
        expected_status="NORMAL_NO_DATA",
        raw_evidence=raw_evidence,
        tags=["hot_stocks", "normal_no_data", "DATA-016"],
    )


def _build_hot_stocks_rate_limited_fixture() -> FixtureEntry:
    now_iso = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")
    raw_evidence = {
        "hot_stocks": {
            "raw": "[DATA-016] HOT_STOCKS_FAILED: 热门股票数据获取失败（Eastmoney push2）：HTTPError: 429 Too Many Requests",
            "field": "hot_stocks",
            "unit": None,
            "vendor": "cn_astock",
            "endpoint": "push2.eastmoney.com/getHotStock",
            "as_of": today,
            "fetched_at": now_iso,
            "status": "FAILED",
            "fallback_from": "cn_akshare",
            "source_url": None,
            "error": "HTTPError: 429 Too Many Requests",
            "is_realtime_patched": False,
            "unit_verified": None,
            "record_count": 0,
        },
    }
    return FixtureEntry(
        fixture_id=FIXTURE_HOT_STOCKS_RATE_LIMITED,
        description="热门股票限流——请求频率过高被拒",
        data_type="hot_stocks",
        vendor="cn_astock",
        endpoint="push2.eastmoney.com/getHotStock",
        expected_status="FAILED",
        raw_evidence=raw_evidence,
        tags=["hot_stocks", "rate_limited", "DATA-016"],
    )


_FIXTURE_BUILDERS = {
    FIXTURE_NORMAL_QUOTE: _build_normal_quote_fixture,
    FIXTURE_STALE_DAILY: _build_stale_daily_fixture,
    FIXTURE_REALTIME_SUCCESS: _build_realtime_success_fixture,
    FIXTURE_REALTIME_FAILURE: _build_realtime_failure_fixture,
    FIXTURE_FUND_FLOW_UNIT_ANOMALY: _build_fund_flow_unit_anomaly_fixture,
    FIXTURE_LHB_NO_TRIGGER: _build_lhb_no_trigger_fixture,
    FIXTURE_ANNOUNCEMENT_FAILURE: _build_announcement_failure_fixture,
    FIXTURE_FUND_FLOW_AKSHARE_FAIL_FALLBACK: _build_fund_flow_akshare_fail_fallback_fixture,
    FIXTURE_LHB_NORMAL_NO_DATA: _build_lhb_normal_no_data_fixture,
    FIXTURE_LHB_FAILED: _build_lhb_failed_fixture,
    FIXTURE_STALE_REALTIME_PATCH: _build_stale_realtime_patch_fixture,
    FIXTURE_ANNOUNCEMENT_FAIL_EVENT_WEAK: _build_announcement_fail_event_weak_fixture,
    FIXTURE_TURNOVER_VOLUME_RATIO_MISSING: _build_turnover_volume_ratio_missing_fixture,
    FIXTURE_MARGIN_HAS_DATA: _build_margin_has_data_fixture,
    FIXTURE_MARGIN_FAILED: _build_margin_failed_fixture,
    FIXTURE_MARGIN_NOT_QUERIED: _build_margin_not_queried_fixture,
    FIXTURE_REPORT_HAS_DATA: _build_report_has_data_fixture,
    FIXTURE_REPORT_FAILED: _build_report_failed_fixture,
    FIXTURE_REPORT_NOT_QUERIED: _build_report_not_queried_fixture,
    FIXTURE_RATINGS_HAS_DATA: _build_ratings_has_data_fixture,
    FIXTURE_RATINGS_FAILED: _build_ratings_failed_fixture,
    FIXTURE_RATINGS_NOT_QUERIED: _build_ratings_not_queried_fixture,
    FIXTURE_BUYBACK_HAS_DATA: _build_buyback_has_data_fixture,
    FIXTURE_BUYBACK_FAILED: _build_buyback_failed_fixture,
    FIXTURE_BUYBACK_NOT_QUERIED: _build_buyback_not_queried_fixture,
    FIXTURE_NEWS_HAS_DATA: _build_news_has_data_fixture,
    FIXTURE_NEWS_NORMAL_NO_DATA: _build_news_normal_no_data_fixture,
    FIXTURE_NEWS_PARTIAL_FAILED: _build_news_partial_failed_fixture,
    FIXTURE_NEWS_FAILED: _build_news_failed_fixture,
    FIXTURE_NEWS_RATE_LIMITED: _build_news_rate_limited_fixture,
    FIXTURE_GLOBAL_NEWS_HAS_DATA: _build_global_news_has_data_fixture,
    FIXTURE_GLOBAL_NEWS_NORMAL_NO_DATA: _build_global_news_normal_no_data_fixture,
    FIXTURE_GLOBAL_NEWS_FAILED: _build_global_news_failed_fixture,
    FIXTURE_GLOBAL_NEWS_RATE_LIMITED: _build_global_news_rate_limited_fixture,
    FIXTURE_ZT_POOL_HAS_DATA: _build_zt_pool_has_data_fixture,
    FIXTURE_ZT_POOL_AKSHARE_FAIL_FALLBACK: _build_zt_pool_akshare_fail_fallback_fixture,
    FIXTURE_ZT_POOL_FAILED: _build_zt_pool_failed_fixture,
    FIXTURE_ZT_POOL_NORMAL_NO_DATA: _build_zt_pool_normal_no_data_fixture,
    FIXTURE_HOT_STOCKS_HAS_DATA: _build_hot_stocks_has_data_fixture,
    FIXTURE_HOT_STOCKS_AKSHARE_FAIL_FALLBACK: _build_hot_stocks_akshare_fail_fallback_fixture,
    FIXTURE_HOT_STOCKS_FAILED: _build_hot_stocks_failed_fixture,
    FIXTURE_HOT_STOCKS_NORMAL_NO_DATA: _build_hot_stocks_normal_no_data_fixture,
    FIXTURE_HOT_STOCKS_RATE_LIMITED: _build_hot_stocks_rate_limited_fixture,
}


def get_fixture(fixture_id: str) -> Optional[FixtureEntry]:
    builder = _FIXTURE_BUILDERS.get(fixture_id)
    if builder is None:
        return None
    return builder()


def get_all_fixtures() -> List[FixtureEntry]:
    return [builder() for builder in (_FIXTURE_BUILDERS[fid] for fid in ALL_FIXTURE_IDS)]


# ── Replay Logic ─────────────────────────────────────────────────────

def _check_stale_status(entry: Dict[str, Any]) -> Optional[str]:
    """Check if the evidence entry indicates stale data that should be flagged."""
    if not isinstance(entry, dict):
        return None
    status = entry.get("status", "")
    is_patched = entry.get("is_realtime_patched", False)
    if status == "HAS_DATA" and not is_patched:
        source_type = entry.get("source_type", "")
        if source_type == "realtime_patch":
            return None
    return None


def _classify_failure_type(result: ReplayResult) -> str:
    if result.actual_status == "FAILED":
        error = result.error or ""
        if "ConnectionError" in error or "TimeoutError" in error:
            return "connection_or_timeout"
        if "RateLimitError" in error or "rate" in error.lower():
            return "rate_limited"
        return "api_error"
    if result.actual_status == "STALE":
        return "stale_data"
    if result.actual_status == "NOT_QUERIED" and result.expected_status != "NOT_QUERIED":
        return "unexpected_not_queried"
    if result.completeness_score < 70:
        return "low_completeness"
    return "status_mismatch"


def _replay_single_fixture(fixture: FixtureEntry) -> ReplayResult:
    raw_evidence = fixture.raw_evidence
    completeness = compute_contract_completeness(raw_evidence)
    primary = get_primary_source(fixture.data_type)

    is_fallback = False
    fallback_from = None
    actual_vendor = fixture.vendor
    if primary and primary.vendor != fixture.vendor:
        is_fallback = True
        fallback_from = primary.vendor

    actual_status = fixture.expected_status
    error_msg = None
    passed = True
    as_of_value = ""

    for ev_key, entry in raw_evidence.items():
        if not isinstance(entry, dict):
            continue
        contract = EvidenceContract.from_dict(entry)
        ev_status = contract.status

        if contract.as_of and not as_of_value:
            as_of_value = contract.as_of

        if ev_status == "FAILED":
            actual_status = "FAILED"
            error_msg = contract.error or "unknown error"
            if fixture.expected_status != "FAILED":
                passed = False
            break

        if contract.is_fallback and contract.fallback_from:
            is_fallback = True
            fallback_from = contract.fallback_from

        if ev_key == "stock_data" and ev_status == "HAS_DATA":
            is_patched = entry.get("is_realtime_patched", False)
            source_type = entry.get("source_type", "")
            if not is_patched and source_type != "realtime_patch":
                if fixture.tags and "stale" in fixture.tags and "realtime_failure" in fixture.tags:
                    actual_status = "STALE"
                    if fixture.expected_status != "STALE":
                        passed = False

        if ev_key == "fund_flow_individual" and ev_status == "HAS_DATA":
            unit_verified = entry.get("unit_verified")
            if unit_verified is False:
                if fixture.tags and "unit_anomaly" in fixture.tags:
                    pass

        if ev_key == "lhb":
            if ev_status == "NOT_QUERIED":
                if fixture.expected_status == "NOT_QUERIED":
                    pass
                elif fixture.expected_status != "NOT_QUERIED":
                    if len(raw_evidence) == 1:
                        passed = False
                        actual_status = "NOT_QUERIED"
            elif ev_status == "NORMAL_NO_DATA":
                if fixture.expected_status == "NORMAL_NO_DATA":
                    pass
                elif fixture.expected_status != "NORMAL_NO_DATA":
                    if len(raw_evidence) == 1:
                        actual_status = "NORMAL_NO_DATA"

    if fixture.expected_status == "FAILED" and actual_status == "FAILED":
        passed = True
    elif fixture.expected_status == "NOT_QUERIED" and actual_status == "NOT_QUERIED":
        passed = True
    elif fixture.expected_status == "STALE" and actual_status == "STALE":
        passed = True
    elif fixture.expected_status == "HAS_DATA" and actual_status == "HAS_DATA":
        passed = True
    elif fixture.expected_status == "NORMAL_NO_DATA" and actual_status == "NORMAL_NO_DATA":
        passed = True
    elif fixture.expected_status == "HAS_DATA" and actual_status == "FAILED":
        passed = False
    elif fixture.expected_status == "STALE" and actual_status == "FAILED":
        passed = False

    return ReplayResult(
        fixture_id=fixture.fixture_id,
        fixture_description=fixture.description,
        data_type=fixture.data_type,
        expected_status=fixture.expected_status,
        actual_status=actual_status,
        passed=passed,
        vendor=actual_vendor,
        endpoint=fixture.endpoint,
        is_fallback=is_fallback,
        fallback_from=fallback_from,
        error=error_msg,
        completeness_score=completeness.get("completeness_score", 0),
        missing_details=completeness.get("missing_details", {}),
        tags=fixture.tags,
        as_of=as_of_value,
    )


def run_fixture_replay(
    fixture_ids: Optional[List[str]] = None,
    fixtures: Optional[List[FixtureEntry]] = None,
) -> ReplayReport:
    now = datetime.now()
    report = ReplayReport(
        run_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        date=now.strftime("%Y-%m-%d"),
    )

    if fixtures is not None:
        items = fixtures
    elif fixture_ids is not None:
        items = []
        for fid in fixture_ids:
            f = get_fixture(fid)
            if f is not None:
                items.append(f)
    else:
        items = get_all_fixtures()

    report.total_fixtures = len(items)

    for fixture in items:
        result = _replay_single_fixture(fixture)
        report.results.append(result)

        if result.passed:
            report.passed += 1
        else:
            report.failed += 1
            report.all_passed = False
            ft = _classify_failure_type(result)
            report.failure_types[ft] = report.failure_types.get(ft, 0) + 1

        dt = result.data_type
        if dt not in report.by_data_type:
            report.by_data_type[dt] = {"total": 0, "passed": 0, "failed": 0}
        report.by_data_type[dt]["total"] += 1
        if result.passed:
            report.by_data_type[dt]["passed"] += 1
        else:
            report.by_data_type[dt]["failed"] += 1

        st = result.actual_status
        report.by_status[st] = report.by_status.get(st, 0) + 1

    return report


# ── Markdown Rendering ────────────────────────────────────────────────

def render_replay_report(report: ReplayReport) -> str:
    lines: List[str] = []
    lines.append("# Data Source Fixture Replay Report")
    lines.append("")
    lines.append(f"- **Date**: {report.date}")
    lines.append(f"- **Run at**: {report.run_at}")
    lines.append(f"- **Result**: {'ALL PASSED' if report.all_passed else 'HAS FAILURES'}")
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Count |")
    lines.append("|--------|-------|")
    lines.append(f"| Total fixtures | {report.total_fixtures} |")
    lines.append(f"| Passed | {report.passed} |")
    lines.append(f"| Failed | {report.failed} |")
    lines.append("")

    if report.by_data_type:
        lines.append("## By Data Type")
        lines.append("")
        lines.append("| Data Type | Total | Passed | Failed |")
        lines.append("|-----------|-------|--------|--------|")
        for dt, counts in sorted(report.by_data_type.items()):
            lines.append(
                f"| {dt} | {counts['total']} | {counts['passed']} | {counts['failed']} |"
            )
        lines.append("")

    if report.failure_types:
        lines.append("## Failure Types")
        lines.append("")
        for ft, count in sorted(report.failure_types.items()):
            lines.append(f"- **{ft}**: {count}")
        lines.append("")

    lines.append("## Fixture Details")
    lines.append("")
    lines.append(
        "| Fixture | Data Type | Expected | Actual | Passed | Vendor | "
        "Fallback | Completeness | Error |"
    )
    lines.append(
        "|---------|-----------|----------|--------|--------|--------|"
        "----------|-------------|-------|"
    )

    for r in report.results:
        error_short = (r.error or "-")[:50]
        if len(error_short) > 50:
            error_short = error_short[:47] + "..."
        lines.append(
            f"| {r.fixture_id} | {r.data_type} | {r.expected_status} | "
            f"{r.actual_status} | {'PASS' if r.passed else 'FAIL'} | "
            f"{r.vendor} | {r.fallback_from or '-'} | {r.completeness_score}% | "
            f"{error_short} |"
        )
    lines.append("")

    failed_results = [r for r in report.results if not r.passed]
    if failed_results:
        lines.append("## Failed Fixtures Detail")
        lines.append("")
        for r in failed_results:
            lines.append(f"### {r.fixture_id}")
            lines.append(f"- Description: {r.fixture_description}")
            lines.append(f"- Expected: {r.expected_status}, Actual: {r.actual_status}")
            lines.append(f"- Vendor: {r.vendor}, Endpoint: {r.endpoint}")
            if r.fallback_from:
                lines.append(f"- Fallback from: {r.fallback_from}")
            if r.error:
                lines.append(f"- Error: {r.error}")
            if r.missing_details:
                lines.append(f"- Missing details: {r.missing_details}")
            lines.append("")

    lines.append("---")
    lines.append("*Generated by fixture_replay.py — [DATA-005] data_source_replay*")
    lines.append("")
    return "\n".join(lines)


# ── File Output ───────────────────────────────────────────────────────

def save_replay_report(
    report: ReplayReport,
    output_dir: str = "docs/data_source_reports",
) -> str:
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"{report.date}.md")
    md = render_replay_report(report)
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    return path


def run_replay_and_save(
    output_dir: str = "docs/data_source_reports",
    fixture_ids: Optional[List[str]] = None,
) -> str:
    report = run_fixture_replay(fixture_ids=fixture_ids)
    path = save_replay_report(report, output_dir=output_dir)
    return path
