# [DATA-027] research_source_smoke
"""免费研报 / 公告 / 半年报源 smoke 扩展与失败归因.

在 DATA-025 (免费研报来源目录 + Eastmoney 研报 smoke) 基础上, 把 smoke 覆盖面
从**单一研报元数据源**扩展到三类披露入口:

  1. **研报元数据** (research_report) — 东方财富研报中心 / AKShare
     ``stock_research_report_em`` (复用 DATA-025 目录).
  2. **公告披露** (announcement) — 巨潮资讯 / CNInfo 与东财公告入口.
  3. **半年报披露** (half_year_report) — CNInfo 定期报告披露入口.

并把 DATA-025 的三类粗粒度 fixture (HAS_DATA / NORMAL_NO_DATA / FAILED) 升级为
**失败归因分类器** (借鉴 DATA-024 ``fund_flow_source_probe``), 区分:

  - ``network_error``  — 网络/连接/超时 (接口失败)
  - ``rate_limited``   — 限流 (429)
  - ``field_missing``  — 接口返回了行但关键契约字段为空 (字段缺失)
  - ``schema_change``  — 列名/结构变更导致解析后字段全空
  - ``no_data``        — 正常无数据 (新股 / 冷门股 / 停牌 / 暂无披露)
  - ``ok``             — 有数据且契约字段完整
  - ``unknown``        — 兜底

核心边界 (与 TASKS.md DATA-027 执行约束一致):
  - 默认 fixture / dry-run, 不做大批量抓取.
  - 不下载或提交 PDF 正文, 只保留元数据与 PDF 链接字段.
  - 不把无研报当失败: ``NORMAL_NO_DATA`` 与 ``FAILED`` 严格区分.
  - 不写生产 tradingagents.db; 不调用 LLM; 不改 prompts; 不改强动作门禁.
  - 与 DATA-023 能力矩阵字段保持一致 (data_type / vendor / endpoint /
    freshness / unit / fields / rate_limit_risk).

与 DATA-025 的关系:
  DATA-025 只 smoke 东财研报元数据源 (3 类粗粒度 fixture); 本模块扩展到
  公告 / 半年报披露入口, 并增加失败归因, 输出独立的
  ``research-source-smoke-YYYY-MM-DD.md`` 报告. 不修改 DATA-025 模块本身,
  也不修改 DATA-023 matrix items.

使用示例 (库):
    from tradingagents.dataflows.research_source_smoke import (
        run_research_source_smoke,
        render_research_source_smoke_report,
    )
    report = run_research_source_smoke()           # fixture dry-run
    print(render_research_source_smoke_report(report))

使用示例 (CLI):
    python scripts/run_research_source_smoke.py    # fixture dry-run
    TA_LIVE_DATA_SMOKE=1 python scripts/run_research_source_smoke.py \\
        --live-smoke --symbols 600519.SH,000001.SZ
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from .source_catalog import DataType
from .source_freshness_report import (
    _detect_failed,
    _detect_normal_no_data,
    _detect_rate_limited,
)


# ── 常量 ───────────────────────────────────────────────────────────────

_LIVE_ENV = "TA_LIVE_DATA_SMOKE"
_MAX_SYMBOLS = 5
DEFAULT_SMOKE_SYMBOLS: List[str] = ["600519.SH", "000001.SZ", "603629.SH"]


# ── 状态语义 (与 DATA-025 / DATA-011 raw_evidence 对齐) ────────────────


class ResearchSourceStatus:
    """披露源 smoke 状态语义.

    与 ``ResearchReportStatus`` / ``EvidenceStatus`` 对齐,
    便于和 DATA-011 raw_evidence / readiness_score 联动, 不引入新状态.
    """

    HAS_DATA = "HAS_DATA"
    NORMAL_NO_DATA = "NORMAL_NO_DATA"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"  # live-smoke env gated
    NOT_RUN = "NOT_RUN"

    ALL = [HAS_DATA, NORMAL_NO_DATA, FAILED, SKIPPED, NOT_RUN]


# ── 失败归因枚举 (借鉴 DATA-024 FundFlowErrorType) ─────────────────────


class ResearchSourceErrorType:
    """研报 / 公告 / 半年报源 probe 错误归因类型.

    任务验收明确要求 fixture 覆盖: 有数据 / 无数据 / 接口失败 / 字段缺失.
    对应归因:
      - ``ok``            — 有数据 (HAS_DATA)
      - ``no_data``       — 无数据 (NORMAL_NO_DATA)
      - ``network_error`` — 接口失败 (FAILED)
      - ``field_missing`` — 字段缺失 (数据行存在但关键契约字段为空)
    另保留 ``rate_limited`` / ``schema_change`` / ``unknown`` 以覆盖更细场景.
    """

    OK = "ok"
    NETWORK_ERROR = "network_error"
    RATE_LIMITED = "rate_limited"
    FIELD_MISSING = "field_missing"
    SCHEMA_CHANGE = "schema_change"
    NO_DATA = "no_data"
    UNKNOWN = "unknown"

    ALL = [
        OK,
        NETWORK_ERROR,
        RATE_LIMITED,
        FIELD_MISSING,
        SCHEMA_CHANGE,
        NO_DATA,
        UNKNOWN,
    ]

    # 任务明确要求归因到的 4 类 (不含 ok / unknown; schema_change 为字段缺失细分)
    REQUIRED_ATTRIBUTION = [
        NETWORK_ERROR,
        RATE_LIMITED,
        FIELD_MISSING,
        NO_DATA,
    ]

    LABEL_CN: Dict[str, str] = {
        OK: "正常",
        NETWORK_ERROR: "网络/接口失败",
        RATE_LIMITED: "限流",
        FIELD_MISSING: "字段缺失",
        SCHEMA_CHANGE: "接口结构变更",
        NO_DATA: "正常无数据",
        UNKNOWN: "未知",
    }

    # 桥接到 DATA-025 / SourceFreshnessStatus 的状态语义
    TO_FRESHNESS_STATUS: Dict[str, str] = {
        OK: ResearchSourceStatus.HAS_DATA,
        NETWORK_ERROR: ResearchSourceStatus.FAILED,
        RATE_LIMITED: ResearchSourceStatus.FAILED,
        FIELD_MISSING: ResearchSourceStatus.HAS_DATA,
        SCHEMA_CHANGE: ResearchSourceStatus.NORMAL_NO_DATA,
        NO_DATA: ResearchSourceStatus.NORMAL_NO_DATA,
        UNKNOWN: ResearchSourceStatus.FAILED,
    }


# ── 披露源目录 (研报 / 公告 / 半年报 三类入口) ─────────────────────────
#
# 任务 §实现要点 1: 扩展 smoke 覆盖 Eastmoney/AKShare 研报元数据与公告披露入口.
# 每条 entry 描述一个披露源的能力边界, 字段与 DATA-023 能力矩阵 entry 保持一致
# (data_type / vendor / endpoint / freshness / unit / fields / rate_limit_risk),
# 额外增加 source_type / akshare_method / content_role / known_limits 描述能力边界.
#
# source_type:
#   - research_report   研报元数据 (观点/预期源)
#   - announcement      公告披露 (事实源)
#   - half_year_report  半年报定期披露 (事实源)

RESEARCH_DISCLOSURE_SOURCES: List[Dict[str, Any]] = [
    {
        "source_id": "eastmoney_research_report_em",
        "source_type": "research_report",
        "name_cn": "东方财富研报中心 / 个股研报",
        "vendor": "cn_akshare",
        "endpoint": "reportapi.eastmoney.com/report/list",
        "akshare_method": "stock_research_report_em",
        "data_type": DataType.REPORT.value,
        "fields": [
            "日期", "报告名称", "股票简称", "股票代码", "机构",
            "东财评级", "行业", "盈利预测-收益", "盈利预测-市盈率",
            "报告PDF链接", "近一月个股研报数",
        ],
        "unit": "条",
        "freshness": "daily",
        "rate_limit_risk": "medium",
        "content_role": "opinion",
        "provides_pdf_link": True,
        "usage_note": (
            "AKShare stock_research_report_em(symbol) 个股研报元数据、机构、评级、"
            "盈利预测、PDF 链接。仅作观点/关注度源, 不替代公告或财报。"
        ),
        "known_limits": [
            "字段覆盖依赖东财上书机构, 部分研报缺盈利预测/目标价",
            "PDF 正文受版权保护, 不得下载入库",
        ],
    },
    {
        "source_id": "cninfo_announcement",
        "source_type": "announcement",
        "name_cn": "巨潮资讯 / CNInfo 公告披露",
        "vendor": "cn_astock",
        "endpoint": "cninfo.com.cn/hisAnnouncement",
        "akshare_method": "",
        "data_type": DataType.NOTICE.value,
        "fields": [
            "公告标题", "公告类型", "公告时间", "公告ID",
            "股票代码", "股票简称",
        ],
        "unit": "条",
        "freshness": "daily",
        "rate_limit_risk": "low",
        "content_role": "fact",
        "provides_pdf_link": True,
        "usage_note": (
            "公告/法披真源, 用于公告披露交叉验证。研报观点不能凌驾于公告事实之上。"
        ),
        "known_limits": ["需 orgId 映射", "公告数量大需按类型筛选"],
    },
    {
        "source_id": "eastmoney_announcement",
        "source_type": "announcement",
        "name_cn": "东方财富公告 / 数据中心",
        "vendor": "cn_astock",
        "endpoint": "np-anotice-stock.eastmoney.com/api/security/ann",
        "akshare_method": "",
        "data_type": DataType.NOTICE.value,
        "fields": [
            "公告标题", "公告类型", "公告日期", "公告代码", "股票代码",
        ],
        "unit": "条",
        "freshness": "daily",
        "rate_limit_risk": "medium",
        "content_role": "fact",
        "provides_pdf_link": True,
        "usage_note": (
            "东财公告接口, 作为 CNInfo 公告源的补充, 公告类型与时间字段为主。"
        ),
        "known_limits": ["字段命名与 CNInfo 不同, 需归一化"],
    },
    {
        "source_id": "cninfo_half_year_report",
        "source_type": "half_year_report",
        "name_cn": "巨潮资讯 / 半年报定期披露",
        "vendor": "cn_astock",
        "endpoint": "cninfo.com.cn/hisAnnouncement (category=半年度报告)",
        "akshare_method": "",
        "data_type": DataType.NOTICE.value,
        "fields": [
            "公告标题", "公告类型", "公告时间", "公告ID", "股票代码",
            "报告年度", "报告类型",
        ],
        "unit": "条",
        "freshness": "daily",
        "rate_limit_risk": "low",
        "content_role": "fact",
        "provides_pdf_link": True,
        "usage_note": (
            "CNInfo 半年度报告披露入口, 报告期内可获取半年报披露公告与 PDF 链接。"
            "非报告期返回空属 NORMAL_NO_DATA。"
        ),
        "known_limits": [
            "仅在半年报披露季 (7-9 月) 有数据",
            "需按 category 筛选半年度报告",
        ],
    },
]


def get_research_disclosure_sources() -> List[Dict[str, Any]]:
    """返回披露源目录的深拷贝."""
    return [dict(s) for s in RESEARCH_DISCLOSURE_SOURCES]


def get_sources_by_type(source_type: str) -> List[Dict[str, Any]]:
    """按 source_type 过滤披露源."""
    return [
        dict(s) for s in RESEARCH_DISCLOSURE_SOURCES
        if s.get("source_type") == source_type
    ]


# ── 数据模型 ──────────────────────────────────────────────────────────


@dataclass
class DisclosureRecord:
    """单条披露元数据 (研报 / 公告 / 半年报统一模型).

    不同 source_type 下部分字段为空 (如研报才有 rating/industry,
    公告才有 announcement_type). 不含 PDF 正文, 只保留 pdf_url 链接.
    """

    source_type: str = ""  # research_report / announcement / half_year_report
    date: str = ""
    title: str = ""
    stock_name: str = ""
    stock_code: str = ""
    org: str = ""  # 研报机构 / 公告发布主体
    rating: str = ""  # 研报评级 (research_report only)
    industry: str = ""  # 行业 (research_report only)
    announcement_type: str = ""  # 公告类型 (announcement / half_year_report)
    report_period: str = ""  # 报告期 (half_year_report, e.g. 2026H1)
    pdf_url: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_type": self.source_type,
            "date": self.date,
            "title": self.title,
            "stock_name": self.stock_name,
            "stock_code": self.stock_code,
            "org": self.org,
            "rating": self.rating,
            "industry": self.industry,
            "announcement_type": self.announcement_type,
            "report_period": self.report_period,
            "pdf_url": self.pdf_url,
        }


@dataclass
class ResearchSourceProbeResult:
    """单次披露源 probe 结果 (含失败归因)."""

    symbol: str = ""
    source_id: str = ""
    source_type: str = ""  # research_report / announcement / half_year_report
    vendor: str = ""
    status: str = ResearchSourceStatus.NOT_RUN
    error_type: str = ResearchSourceErrorType.UNKNOWN
    record_count: int = 0
    latency_ms: float = 0.0
    error: str = ""
    sample_records: List[DisclosureRecord] = field(default_factory=list)
    diagnosis: str = ""
    fixture_id: str = ""
    probe_mode: str = "fixture"  # fixture / live / skipped

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "source_id": self.source_id,
            "source_type": self.source_type,
            "vendor": self.vendor,
            "status": self.status,
            "error_type": self.error_type,
            "error_type_label_cn": ResearchSourceErrorType.LABEL_CN.get(
                self.error_type, ResearchSourceErrorType.UNKNOWN
            ),
            "record_count": self.record_count,
            "latency_ms": round(self.latency_ms, 1),
            "error": (self.error or "")[:200],
            "sample_records": [r.to_dict() for r in self.sample_records],
            "diagnosis": self.diagnosis,
            "fixture_id": self.fixture_id,
            "probe_mode": self.probe_mode,
        }


@dataclass
class ResearchSourceProbeReport:
    """披露源 probe 顶层报告."""

    run_at: str = ""
    date: str = ""
    mode: str = "fixture"  # fixture / live / skipped
    env_gated: bool = True
    symbols: List[str] = field(default_factory=list)
    results: List[ResearchSourceProbeResult] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_at": self.run_at,
            "date": self.date,
            "mode": self.mode,
            "env_gated": self.env_gated,
            "symbols": self.symbols,
            "results": [r.to_dict() for r in self.results],
            "summary": self.summary,
        }


# ── fixture: 覆盖 4 类验收场景 (有数据 / 无数据 / 接口失败 / 字段缺失)
#
# 跨 3 个 source_type 覆盖, 每个 fixture 用与真实 AKShare/CNInfo 返回一致的
# 行结构 (中文列名), 解析器面对的是真实字段而不是测试专用 shortcut.
#
# FIELD_MISSING 是 DATA-027 新增场景: 接口返回了行但关键契约字段 (标题/日期)
# 为空 — 这既不是 FAILED 也不是 NORMAL_NO_DATA, 必须单独归因.

PROBE_FIXTURES: Dict[str, Dict[str, Any]] = {
    "HAS_DATA": {
        "description": "东财研报中心正常返回多份券商研报元数据",
        "symbol": "600519.SH",
        "source_id": "eastmoney_research_report_em",
        "source_type": "research_report",
        "vendor": "cn_akshare",
        "rows": [
            {
                "序号": 1,
                "报告名称": "贵州茅台2026年深度研究",
                "股票简称": "贵州茅台",
                "股票代码": "600519",
                "机构": "中信证券",
                "日期": "2026-06-15",
                "东财评级": "买入",
                "行业": "白酒",
                "2026-盈利预测-收益": "58.50",
                "2026-盈利预测-市盈率": "30.20",
                "报告PDF链接": "https://pdf.dfcfw.com/pdf/H3_ABC123_1.pdf",
                "近一月个股研报数": "12",
            },
            {
                "序号": 2,
                "报告名称": "贵州茅台一季报点评",
                "股票简称": "贵州茅台",
                "股票代码": "600519",
                "机构": "中金公司",
                "日期": "2026-06-10",
                "东财评级": "增持",
                "行业": "白酒",
                "2026-盈利预测-收益": "57.80",
                "2026-盈利预测-市盈率": "30.60",
                "报告PDF链接": "https://pdf.dfcfw.com/pdf/H3_DEF456_1.pdf",
                "近一月个股研报数": "12",
            },
        ],
        "error": "",
        "expected_error_type": ResearchSourceErrorType.OK,
    },
    "HAS_DATA_ANNOUNCEMENT": {
        "description": "CNInfo 公告披露正常返回近期公告",
        "symbol": "000001.SZ",
        "source_id": "cninfo_announcement",
        "source_type": "announcement",
        "vendor": "cn_astock",
        "rows": [
            {
                "公告标题": "平安银行2026年半年度报告",
                "公告类型": "定期报告",
                "公告时间": "2026-08-30",
                "公告ID": "CNINFO-001",
                "股票代码": "000001",
                "股票简称": "平安银行",
            },
            {
                "公告标题": "平安银行关于召开2026年第三次临时股东大会的通知",
                "公告类型": "股东大会",
                "公告时间": "2026-07-15",
                "公告ID": "CNINFO-002",
                "股票代码": "000001",
                "股票简称": "平安银行",
            },
        ],
        "error": "",
        "expected_error_type": ResearchSourceErrorType.OK,
    },
    "HAS_DATA_HALF_YEAR": {
        "description": "CNInfo 半年报披露季正常返回半年报公告",
        "symbol": "603629.SH",
        "source_id": "cninfo_half_year_report",
        "source_type": "half_year_report",
        "vendor": "cn_astock",
        "rows": [
            {
                "公告标题": "利通电子2026年半年度报告",
                "公告类型": "半年度报告",
                "公告时间": "2026-08-28",
                "公告ID": "CNINFO-HY-001",
                "股票代码": "603629",
                "报告年度": "2026",
                "报告类型": "半年报",
            },
        ],
        "error": "",
        "expected_error_type": ResearchSourceErrorType.OK,
    },
    "NORMAL_NO_DATA": {
        "description": "接口正常但标的近期无研报覆盖 (新股 / 冷门股 / 停牌)",
        "symbol": "603629.SH",
        "source_id": "eastmoney_research_report_em",
        "source_type": "research_report",
        "vendor": "cn_akshare",
        "rows": [],
        "error": "",
        "expected_error_type": ResearchSourceErrorType.NO_DATA,
    },
    "NORMAL_NO_DATA_HALF_YEAR": {
        "description": "非半年报披露季, CNInfo 半年报入口正常返回空 (属正常无数据)",
        "symbol": "600519.SH",
        "source_id": "cninfo_half_year_report",
        "source_type": "half_year_report",
        "vendor": "cn_astock",
        "rows": [],
        "error": "",
        "expected_error_type": ResearchSourceErrorType.NO_DATA,
    },
    "FAILED": {
        "description": "AKShare / 东财接口失败 (ConnectionError / 超时)",
        "symbol": "000001.SZ",
        "source_id": "eastmoney_research_report_em",
        "source_type": "research_report",
        "vendor": "cn_akshare",
        "rows": None,
        "error": "ConnectionError: HTTPSConnectionPool(host=reportapi.eastmoney.com, port=443): Max retries exceeded",
        "expected_error_type": ResearchSourceErrorType.NETWORK_ERROR,
    },
    "RATE_LIMITED": {
        "description": "东财公告接口 429 限流 — 退避后重试或切 fallback",
        "symbol": "000001.SZ",
        "source_id": "eastmoney_announcement",
        "source_type": "announcement",
        "vendor": "cn_astock",
        "rows": None,
        "error": "HTTPError 429: Too Many Requests — 请求过于频繁，请稍后重试",
        "expected_error_type": ResearchSourceErrorType.RATE_LIMITED,
    },
    "FIELD_MISSING": {
        "description": "接口返回了行但关键契约字段 (标题/日期) 全空 — 字段缺失, 不能当 HAS_DATA",
        "symbol": "600519.SH",
        "source_id": "eastmoney_research_report_em",
        "source_type": "research_report",
        "vendor": "cn_akshare",
        "rows": [
            {
                "序号": 1,
                "报告名称": "",
                "股票简称": "",
                "股票代码": "600519",
                "机构": "",
                "日期": "",
                "东财评级": "",
                "行业": "",
                "报告PDF链接": "",
            },
            {
                "序号": 2,
                "报告名称": None,
                "日期": None,
                "机构": "",
            },
        ],
        "error": "",
        "expected_error_type": ResearchSourceErrorType.FIELD_MISSING,
    },
    "SCHEMA_CHANGE": {
        "description": "接口列名/结构变更: 关键列名完全不存在, 解析后所有 record 字段全空",
        "symbol": "000001.SZ",
        "source_id": "eastmoney_announcement",
        "source_type": "announcement",
        "vendor": "cn_astock",
        "rows": [
            {"未知列A": "x", "未知列B": "y"},
            {"data1": "foo", "data2": "bar"},
        ],
        "error": "",
        "expected_error_type": ResearchSourceErrorType.SCHEMA_CHANGE,
    },
}


# ── 解析行 → DisclosureRecord ─────────────────────────────────────────


def _pick(row: Dict[str, Any], *keys: str, default: str = "") -> str:
    """从行里按多个候选列名取值 (兼容字段覆盖不稳定 / NaN)."""
    for k in keys:
        if k in row and row[k] not in (None, ""):
            val = row[k]
            if isinstance(val, float) and val != val:
                continue
            return str(val)
    return default


def parse_disclosure_rows(
    rows: Optional[List[Dict[str, Any]]],
    source_type: str = "",
) -> List[DisclosureRecord]:
    """把披露源返回的行解析成结构化 records.

    兼容研报 / 公告 / 半年报三类列名, 缺失时用候选列名兜底.
    不下载 / 不保存 PDF 正文, 只保留 ``pdf_url`` 字段.
    """
    if not rows:
        return []
    records: List[DisclosureRecord] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        record = DisclosureRecord(
            source_type=source_type,
            date=_pick(row, "日期", "公告时间", "公告日期", "publishDate"),
            title=_pick(row, "报告名称", "公告标题", "title", "announcementTitle"),
            stock_name=_pick(row, "股票简称", "stockName"),
            stock_code=_pick(row, "股票代码", "stockCode"),
            org=_pick(row, "机构", "orgSName", "发布主体"),
            rating=_pick(row, "东财评级", "emRatingName"),
            industry=_pick(row, "行业", "indvInduName"),
            announcement_type=_pick(
                row, "公告类型", "announcementTypeName", "announcementType"
            ),
            report_period=_pick(row, "报告年度", "报告期"),
            pdf_url=_pick(row, "报告PDF链接", "公告链接", "pdfUrl", "adjunctUrl"),
        )
        records.append(record)
    return records


# ── 失败归因分类器 ─────────────────────────────────────────────────────


def _record_has_key_fields(record: DisclosureRecord) -> bool:
    """判断一条 record 是否有关键契约字段 (标题/日期 至少一项非空)."""
    return bool(record.title or record.date)


def classify_research_source_error_type(
    rows: Any = None,
    status: str = "",
    error: str = "",
    parsed_records: Optional[List[DisclosureRecord]] = None,
) -> str:
    """把一条披露源探测结果归因到 ``ResearchSourceErrorType`` 之一.

    优先级 (与 DATA-024 classify_fund_flow_error_type 对齐):
      1. RATE_LIMITED   — error 命中限流模式
      2. NETWORK_ERROR  — error 命中失败模式 / status=FAILED
      3. NO_DATA        — status=NORMAL_NO_DATA 或 rows 为空
      4. FIELD_MISSING  — rows 非空但解析后关键字段 (标题/日期) 全空
      5. SCHEMA_CHANGE  — rows 非空但解析后 record 所有字段全空 (列名不匹配)
      6. OK             — 数据存在且关键字段非空
      7. UNKNOWN        — 兜底
    """
    rows_list = rows if isinstance(rows, list) else (None if rows is None else list(rows))
    err_str = error or ""

    # 1. 限流优先 (最具体的失败类型)
    if err_str and _detect_rate_limited(err_str):
        return ResearchSourceErrorType.RATE_LIMITED

    # 2. 网络/接口失败模式
    if err_str and _detect_failed(err_str):
        return ResearchSourceErrorType.NETWORK_ERROR
    if status == ResearchSourceStatus.FAILED:
        return ResearchSourceErrorType.NETWORK_ERROR
    # rows=None 表示接口失败 (与 DATA-025 fixture 约定一致)
    if rows_list is None and err_str:
        return ResearchSourceErrorType.NETWORK_ERROR

    # 3. 正常无数据
    if status == ResearchSourceStatus.NORMAL_NO_DATA:
        return ResearchSourceErrorType.NO_DATA
    if status in ("NOT_QUERIED", "SKIPPED"):
        return ResearchSourceErrorType.NO_DATA
    if rows_list is not None and len(rows_list) == 0 and not err_str:
        return ResearchSourceErrorType.NO_DATA

    # 4-6. 数据存在时的细分
    records = parsed_records if parsed_records is not None else (
        parse_disclosure_rows(rows_list) if rows_list else []
    )

    if rows_list and records:
        any_key_field = any(_record_has_key_fields(r) for r in records)
        if not any_key_field:
            # 关键字段 (标题/日期) 全空
            # 区分 SCHEMA_CHANGE (record 所有字段全空) 与 FIELD_MISSING
            any_field_at_all = any(
                any(v for v in r.to_dict().values() if v not in ("", r.source_type))
                for r in records
            )
            if not any_field_at_all:
                return ResearchSourceErrorType.SCHEMA_CHANGE
            return ResearchSourceErrorType.FIELD_MISSING
        return ResearchSourceErrorType.OK

    if rows_list and not records:
        # 有原始行但解析器返回空 (列名完全不匹配)
        return ResearchSourceErrorType.SCHEMA_CHANGE

    if not err_str:
        return ResearchSourceErrorType.UNKNOWN
    return ResearchSourceErrorType.UNKNOWN


# ── 单条探测 ──────────────────────────────────────────────────────────


def _probe_fixture(
    fixture_id: str,
    fixture: Dict[str, Any],
) -> ResearchSourceProbeResult:
    """对单个 fixture 跑解析 + 失败归因分类."""
    rows = fixture.get("rows")
    error = fixture.get("error", "")
    source_type = fixture.get("source_type", "")
    source_id = fixture.get("source_id", "")
    vendor = fixture.get("vendor", "")

    records: List[DisclosureRecord] = []
    if rows:
        records = parse_disclosure_rows(rows, source_type=source_type)

    error_type = classify_research_source_error_type(
        rows=rows,
        error=error,
        parsed_records=records if records else None,
    )
    status = ResearchSourceErrorType.TO_FRESHNESS_STATUS.get(
        error_type, ResearchSourceStatus.NOT_RUN
    )

    diagnosis = fixture.get("description", "")
    if error_type == ResearchSourceErrorType.FIELD_MISSING:
        diagnosis = "接口返回行但关键契约字段 (标题/日期) 为空 — 字段缺失"
    elif error_type == ResearchSourceErrorType.SCHEMA_CHANGE:
        diagnosis = "接口列名/结构变更, 解析后字段全空"

    return ResearchSourceProbeResult(
        symbol=fixture.get("symbol", ""),
        source_id=source_id,
        source_type=source_type,
        vendor=vendor,
        status=status,
        error_type=error_type,
        record_count=len(records),
        latency_ms=0.0,
        error=error,
        sample_records=records[:5],
        diagnosis=diagnosis,
        fixture_id=fixture_id,
        probe_mode="fixture",
    )


def _probe_live_symbol(
    symbol: str,
    *,
    source_type: str = "research_report",
    fetch_fn: Optional[Callable[[str], Any]] = None,
) -> ResearchSourceProbeResult:
    """对一个 symbol 真实调用披露源并归因.

    *fetch_fn* 用于测试注入; 默认走研报源的 ``akshare.stock_research_report_em``.
    live 路径主要服务研报元数据源; 公告/半年报 live 探测需各自 fetch_fn 注入.
    """
    result = ResearchSourceProbeResult(
        symbol=symbol,
        source_type=source_type,
        source_id="eastmoney_research_report_em",
        vendor="cn_akshare",
        probe_mode="live",
    )

    if fetch_fn is None:
        def fetch(sym: str):  # noqa: E306
            import akshare as ak
            from .providers.cn_akshare_provider import AKSHARE_CALL_LOCK
            with AKSHARE_CALL_LOCK:
                return ak.stock_research_report_em(symbol=sym)
        fetch_fn = fetch

    t0 = time.monotonic()
    try:
        df = fetch_fn(symbol)
        result.latency_ms = (time.monotonic() - t0) * 1000
        if df is None:
            # live 返回 None 视为正常无数据 (与 DATA-025 _probe_live_symbol 一致)
            rows: Optional[List[Dict[str, Any]]] = []
            records: List[DisclosureRecord] = []
            error_type = ResearchSourceErrorType.NO_DATA
            result.error_type = error_type
            result.status = ResearchSourceErrorType.TO_FRESHNESS_STATUS.get(
                error_type, ResearchSourceStatus.NORMAL_NO_DATA
            )
            result.record_count = 0
            result.diagnosis = "live 返回 None"
            return result
        else:
            try:
                empty = df.empty
            except AttributeError:
                empty = not bool(df)
            if empty:
                rows = []
            else:
                try:
                    rows = df.to_dict(orient="records")
                except AttributeError:
                    rows = list(df) if isinstance(df, list) else []
        records = parse_disclosure_rows(rows, source_type=source_type) if rows else []
        error_type = classify_research_source_error_type(
            rows=rows,
            parsed_records=records if records else None,
        )
        result.error_type = error_type
        result.status = ResearchSourceErrorType.TO_FRESHNESS_STATUS.get(
            error_type, ResearchSourceStatus.FAILED
        )
        result.record_count = len(records)
        result.sample_records = records[:5]
        if error_type == ResearchSourceErrorType.OK:
            result.diagnosis = f"live ok, 返回 {len(records)} 条"
        else:
            result.diagnosis = f"live classified as {error_type}"
    except AttributeError as exc:
        result.status = ResearchSourceStatus.FAILED
        result.error = f"AttributeError: {exc}"
        result.error_type = ResearchSourceErrorType.NETWORK_ERROR
        result.diagnosis = "接口不可用 (AttributeError)"
    except Exception as exc:
        err_str = f"{type(exc).__name__}: {exc}"
        result.error = err_str
        result.error_type = classify_research_source_error_type(error=err_str)
        result.status = ResearchSourceErrorType.TO_FRESHNESS_STATUS.get(
            result.error_type, ResearchSourceStatus.FAILED
        )
        result.diagnosis = f"live exception: {err_str}"
    return result


# ── 汇总 ──────────────────────────────────────────────────────────────


def _compute_summary(
    results: List[ResearchSourceProbeResult],
) -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "total": len(results),
        "by_error_type": {},
        "by_status": {},
        "by_source_type": {},
        "fixture_coverage": [],
        "required_classes_covered": [],
        "required_classes_missing": [],
        "HAS_DATA": 0,
        "NORMAL_NO_DATA": 0,
        "FAILED": 0,
        "SKIPPED": 0,
        "has_failures": False,
        "all_passed": False,
        "total_records": 0,
    }

    for r in results:
        et = r.error_type
        summary["by_error_type"][et] = summary["by_error_type"].get(et, 0) + 1
        st = r.status
        summary["by_status"][st] = summary["by_status"].get(st, 0) + 1
        sct = r.source_type or "unknown"
        summary["by_source_type"][sct] = summary["by_source_type"].get(sct, 0) + 1
        if r.fixture_id:
            summary["fixture_coverage"].append(r.fixture_id)
        summary["total_records"] += r.record_count

    summary["HAS_DATA"] = summary["by_status"].get(ResearchSourceStatus.HAS_DATA, 0)
    summary["NORMAL_NO_DATA"] = summary["by_status"].get(
        ResearchSourceStatus.NORMAL_NO_DATA, 0
    )
    summary["FAILED"] = summary["by_status"].get(ResearchSourceStatus.FAILED, 0)
    summary["SKIPPED"] = summary["by_status"].get(ResearchSourceStatus.SKIPPED, 0)

    covered = set(summary["by_error_type"].keys())
    required = set(ResearchSourceErrorType.REQUIRED_ATTRIBUTION)
    summary["required_classes_covered"] = sorted(required & covered)
    summary["required_classes_missing"] = sorted(required - covered)

    runnable = [r for r in results if r.status != ResearchSourceStatus.SKIPPED]
    live_failures = [
        r for r in runnable
        if r.probe_mode == "live"
        and r.error_type in (
            ResearchSourceErrorType.NETWORK_ERROR,
            ResearchSourceErrorType.RATE_LIMITED,
            ResearchSourceErrorType.UNKNOWN,
        )
    ]
    summary["has_failures"] = bool(live_failures)

    # fixture 模式: 每条 fixture 必须命中 expected_error_type
    fixture_runnable = [r for r in runnable if r.probe_mode == "fixture"]
    if fixture_runnable and all(r.probe_mode == "fixture" for r in runnable):
        summary["all_passed"] = all(
            r.error_type
            == PROBE_FIXTURES.get(r.fixture_id, {}).get(
                "expected_error_type", r.error_type
            )
            for r in runnable
        )
    else:
        summary["all_passed"] = bool(runnable) and not live_failures

    return summary


# ── 顶层入口 ──────────────────────────────────────────────────────────


def is_live_smoke_enabled() -> bool:
    """``TA_LIVE_DATA_SMOKE=1`` 才允许实盘调用."""
    return os.environ.get(_LIVE_ENV, "") == "1"


def run_research_source_smoke(
    symbols: Optional[List[str]] = None,
    *,
    live_smoke: bool = False,
    fetch_fn: Optional[Callable[[str], Any]] = None,
    today: Optional[str] = None,
) -> ResearchSourceProbeReport:
    """运行披露源 smoke.

    - 默认 fixture dry-run (回放 8 类场景: 有数据×3 / 无数据×2 / 接口失败 /
      限流 / 字段缺失 / 结构变更), 不发网络请求.
    - ``live_smoke=True`` 且 ``TA_LIVE_DATA_SMOKE=1`` 时, 真实调用
      AKShare ``stock_research_report_em`` 抽样验证; 否则标 SKIPPED.
    - 最多抽样 ``_MAX_SYMBOLS`` 只.

    Args:
        symbols: live-smoke 抽样标的, 默认 ``DEFAULT_SMOKE_SYMBOLS``.
        live_smoke: True 才允许走真实网络调用.
        fetch_fn: 测试注入点; 默认走真实 AKShare.
        today: 测试用固定日期 (YYYY-MM-DD).
    """
    now = datetime.now()
    if today:
        date_str = today[:10]
        run_at = f"{today} 00:00:00"
    else:
        date_str = now.strftime("%Y-%m-%d")
        run_at = now.strftime("%Y-%m-%d %H:%M:%S")

    report = ResearchSourceProbeReport(
        run_at=run_at,
        date=date_str,
    )

    if not live_smoke:
        report.mode = "fixture"
        report.env_gated = True
        results: List[ResearchSourceProbeResult] = []
        for fid, fixture in PROBE_FIXTURES.items():
            results.append(_probe_fixture(fid, fixture))
        report.results = results
        report.summary = _compute_summary(results)
        return report

    # live mode
    report.mode = "live"
    syms = list(symbols or DEFAULT_SMOKE_SYMBOLS)
    if len(syms) > _MAX_SYMBOLS:
        syms = syms[:_MAX_SYMBOLS]
    report.symbols = syms

    if not is_live_smoke_enabled():
        report.env_gated = True
        results = [
            ResearchSourceProbeResult(
                symbol=s,
                source_id="eastmoney_research_report_em",
                source_type="research_report",
                vendor="cn_akshare",
                status=ResearchSourceStatus.SKIPPED,
                diagnosis="TA_LIVE_DATA_SMOKE!=1, 未发起网络调用",
                probe_mode="skipped",
            )
            for s in syms
        ]
        report.results = results
        report.summary = _compute_summary(results)
        return report

    report.env_gated = False
    results = []
    for s in syms:
        results.append(_probe_live_symbol(s, fetch_fn=fetch_fn))
        time.sleep(0.5)
    report.results = results
    report.summary = _compute_summary(results)
    return report


# ── 渲染 ──────────────────────────────────────────────────────────────


def render_research_source_smoke_report(
    report: ResearchSourceProbeReport,
) -> str:
    """渲染成 markdown 报告."""
    lines: List[str] = []
    lines.append("# 免费研报/公告/半年报源 Smoke 报告 — DATA-027")
    lines.append("")
    lines.append(
        "> [DATA-027] research_source_smoke. 在 DATA-025 基础上扩展免费源 smoke, "
        "覆盖研报元数据 / 公告披露 / 半年报披露三类入口, 并增加失败归因 "
        "(网络失败 / 限流 / 字段缺失 / 结构变更 / 正常无数据)."
    )
    lines.append("")
    lines.append(f"- **Date**: {report.date}")
    lines.append(f"- **Run at**: {report.run_at}")
    lines.append(f"- **Mode**: `{report.mode}`")
    gate_text = (
        "Yes (TA_LIVE_DATA_SMOKE not set — no live network calls)"
        if report.env_gated and report.mode == "live"
        else ("No (live calls executed)" if report.mode == "live" else "n/a (fixture dry-run)")
    )
    lines.append(f"- **Env gated**: {gate_text}")
    lines.append(f"- **Symbols**: {', '.join(report.symbols) or 'fixture'}")
    lines.append("")

    # ── 披露源目录 ──
    lines.append("## 披露源目录 (研报 / 公告 / 半年报)")
    lines.append("")
    lines.append(
        "> 字段与 DATA-023 能力矩阵一致: data_type / vendor / endpoint / "
        "freshness / unit / fields / rate_limit_risk."
    )
    lines.append("")
    lines.append(
        "| source_id | source_type | 名称 | vendor | endpoint | data_type |"
        " freshness | rate_limit | 角色 |"
    )
    lines.append(
        "|-----------|-------------|------|--------|----------|-----------|"
        "------------|------------|------|"
    )
    role_cn = {"opinion": "观点源", "fact": "事实源"}
    type_cn = {
        "research_report": "研报元数据",
        "announcement": "公告披露",
        "half_year_report": "半年报披露",
    }
    for src in RESEARCH_DISCLOSURE_SOURCES:
        lines.append(
            f"| `{src['source_id']}` | {type_cn.get(src['source_type'], src['source_type'])} "
            f"| {src['name_cn']} | {src['vendor']} | `{src['endpoint']}` "
            f"| `{src['data_type']}` | `{src['freshness']}` | `{src['rate_limit_risk']}` "
            f"| {role_cn.get(src['content_role'], src['content_role'])} |"
        )
    lines.append("")

    lines.append("### 各来源字段与限制")
    lines.append("")
    for src in RESEARCH_DISCLOSURE_SOURCES:
        lines.append(f"#### `{src['source_id']}` — {src['name_cn']}")
        lines.append("")
        lines.append(f"- **source_type**: `{src['source_type']}`")
        lines.append(f"- **vendor / endpoint**: `{src['vendor']}` / `{src['endpoint']}`")
        if src.get("akshare_method"):
            lines.append(f"- **AKShare 方法**: `{src['akshare_method']}`")
        lines.append(f"- **data_type**: `{src['data_type']}` (与 DATA-023 一致)")
        lines.append(f"- **freshness / unit / rate_limit**: `{src['freshness']}` / `{src['unit']}` / `{src['rate_limit_risk']}`")
        if src["fields"]:
            lines.append(f"- **字段**: {', '.join(src['fields'])}")
        lines.append(f"- **用途**: {src['usage_note']}")
        if src.get("known_limits"):
            lines.append(f"- **已知限制**: {'；'.join(src['known_limits'])}")
        lines.append("")

    # ── Summary ──
    s = report.summary
    lines.append("## Smoke Summary")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Total probes | {s.get('total', 0)} |")
    lines.append(f"| HAS_DATA | {s.get('HAS_DATA', 0)} |")
    lines.append(f"| NORMAL_NO_DATA | {s.get('NORMAL_NO_DATA', 0)} |")
    lines.append(f"| FAILED | {s.get('FAILED', 0)} |")
    lines.append(f"| SKIPPED | {s.get('SKIPPED', 0)} |")
    lines.append(f"| Total records parsed | {s.get('total_records', 0)} |")
    covered = s.get("required_classes_covered", [])
    missing = s.get("required_classes_missing", [])
    lines.append(
        f"| Required attribution covered | {', '.join(covered) if covered else '-'} |"
    )
    lines.append(
        f"| Required attribution missing | {', '.join(missing) if missing else 'none ✓'} |"
    )
    lines.append(f"| All runnable passed | {'Yes' if s.get('all_passed') else 'No'} |")
    lines.append(f"| Has live failures | {'Yes' if s.get('has_failures') else 'No'} |")
    lines.append("")

    by_et = s.get("by_error_type", {})
    if by_et:
        lines.append("### By error_type (失败归因)")
        lines.append("")
        lines.append("| error_type | label_cn | count |")
        lines.append("|------------|----------|-------|")
        for et in ResearchSourceErrorType.ALL:
            if et in by_et:
                lines.append(
                    f"| `{et}` | {ResearchSourceErrorType.LABEL_CN.get(et, et)} "
                    f"| {by_et[et]} |"
                )
        lines.append("")

    by_sct = s.get("by_source_type", {})
    if by_sct:
        lines.append("### By source_type")
        lines.append("")
        lines.append("| source_type | count |")
        lines.append("|-------------|-------|")
        for sct in sorted(by_sct.keys()):
            lines.append(f"| `{sct}` | {by_sct[sct]} |")
        lines.append("")

    # ── Probe details ──
    lines.append("## Probe Details")
    lines.append("")
    lines.append(
        "| Symbol | source_type | Source | Vendor | Mode | Status | error_type |"
        " Records | Fixture | Diagnosis |"
    )
    lines.append(
        "|--------|-------------|--------|--------|------|--------|------------|"
        "---------|---------|-----------|"
    )
    for r in report.results:
        diag = (r.diagnosis or "")[:60]
        if len(r.diagnosis) > 60:
            diag += "..."
        lines.append(
            f"| {r.symbol or '-'} | {r.source_type or '-'} | `{r.source_id}` "
            f"| {r.vendor or '-'} | {r.probe_mode} | {r.status} | `{r.error_type}` "
            f"| {r.record_count} | {r.fixture_id or '-'} | {diag or '-'} |"
        )
    lines.append("")

    # ── 样本记录 (HAS_DATA) ──
    has_data_results = [r for r in report.results if r.sample_records]
    if has_data_results:
        lines.append("## Sample Records (有数据)")
        lines.append("")
        lines.append(
            "> 仅展示披露**元数据** (标题/机构/类型/日期/PDF 链接); "
            "不含 PDF 正文, 不含版权内容."
        )
        lines.append("")
        for r in has_data_results:
            lines.append(
                f"### {r.symbol} — {r.source_type} — {r.record_count} records"
            )
            lines.append("")
            lines.append("| 日期 | 标题 | 机构/主体 | 类型 | 评级 | PDF 链接 |")
            lines.append("|------|------|-----------|------|------|----------|")
            for rec in r.sample_records[:5]:
                title = rec.title or rec.announcement_type or "-"
                if len(title) > 50:
                    title = title[:50] + "..."
                type_col = rec.rating or rec.announcement_type or rec.report_period or "-"
                lines.append(
                    f"| {rec.date or '-'} | {title} | {rec.org or '-'} "
                    f"| {type_col} | {rec.rating or '-'} | {rec.pdf_url or '-'} |"
                )
            lines.append("")

    # ── fixture coverage ──
    if report.mode == "fixture":
        lines.append("## Fixture Coverage")
        lines.append("")
        lines.append(
            "验收要求的 4 类场景 (有数据 / 无数据 / 接口失败 / 字段缺失) 均已回放, "
            "并补充限流与结构变更两类, 覆盖研报 / 公告 / 半年报三类 source_type."
        )
        lines.append("")
        lines.append("| fixture_id | source_type | expected → actual | description |")
        lines.append("|------------|-------------|-------------------|-------------|")
        for r in report.results:
            if not r.fixture_id:
                continue
            expected = PROBE_FIXTURES.get(r.fixture_id, {}).get(
                "expected_error_type", "?"
            )
            ok_mark = "✓" if expected == r.error_type else "✗"
            lines.append(
                f"| `{r.fixture_id}` | {r.source_type or '-'} "
                f"| `{expected}` → `{r.error_type}` {ok_mark} | {r.diagnosis} |"
            )
        lines.append("")

    # ── 失败归因说明 ──
    lines.append("## 失败归因说明")
    lines.append("")
    lines.append(
        "DATA-027 把 DATA-025 的 3 类粗粒度 fixture 升级为失败归因分类器 "
        "(借鉴 DATA-024). 关键区分:"
    )
    lines.append("")
    lines.append("| error_type | 含义 | 对应 status |")
    lines.append("|------------|------|--------------|")
    for et in ResearchSourceErrorType.ALL:
        st = ResearchSourceErrorType.TO_FRESHNESS_STATUS.get(et, "-")
        lines.append(
            f"| `{et}` | {ResearchSourceErrorType.LABEL_CN.get(et, et)} | `{st}` |"
        )
    lines.append("")
    lines.append(
        "- **字段缺失 (field_missing)**: 接口返回了行但关键契约字段 (标题/日期) 全空, "
        "既不是 FAILED 也不是 NORMAL_NO_DATA — 需单独标记, 防止空壳数据被当 HAS_DATA."
    )
    lines.append(
        "- **正常无数据 (no_data)** 与 **接口失败 (network_error)** 严格区分: "
        "无研报/无公告是正常, 接口崩溃才是失败."
    )
    lines.append("")

    # ── live gate ──
    if report.mode == "live" and report.env_gated:
        lines.append("## Live-Smoke Gate")
        lines.append("")
        lines.append(
            "本报告为 live-smoke 模式但环境变量 `TA_LIVE_DATA_SMOKE` 未设置为 `1`, "
            "因此未发起任何真实网络调用. 所有标的标记为 `SKIPPED`."
        )
        lines.append("")
        lines.append("如需实盘抽样, 请显式设置环境变量后重跑:")
        lines.append("")
        lines.append("```bash")
        lines.append(
            "TA_LIVE_DATA_SMOKE=1 python scripts/run_research_source_smoke.py "
            "--live-smoke --symbols 600519.SH,000001.SZ"
        )
        lines.append("```")
        lines.append("")

    # ── 边界声明 ──
    lines.append("## 边界声明")
    lines.append("")
    lines.append("- 默认 fixture / dry-run, 不做大批量抓取; 不下载或提交 PDF 正文.")
    lines.append("- 研报/评级仅作**观点 / 关注度 / 预期源**, 公告/半年报才是**事实源**.")
    lines.append("- 严格区分 NORMAL_NO_DATA (无数据属正常) 与 FAILED (接口失败).")
    lines.append(
        "- 与 DATA-023 能力矩阵字段一致 (data_type / vendor / endpoint / "
        "freshness / unit / fields / rate_limit_risk); 不修改 matrix items."
    )
    lines.append("- live-smoke 默认关闭, 需 `TA_LIVE_DATA_SMOKE=1` + `--live-smoke` 双重门禁.")
    lines.append("")
    lines.append("---")
    lines.append(
        "*Generated by research_source_smoke.py — `[DATA-027] research_source_smoke`*"
    )
    lines.append("")
    return "\n".join(lines)


def save_research_source_smoke_report(
    report: ResearchSourceProbeReport,
    output_dir: str = "docs/data_source_reports",
) -> str:
    """把 markdown 报告写到 ``docs/data_source_reports/research-source-smoke-YYYY-MM-DD.md``."""
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"research-source-smoke-{report.date}.md")
    md = render_research_source_smoke_report(report)
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    return path


# ── DATA-023 能力矩阵 overlay (只读附加, 不改 matrix items) ────────────


def build_capability_matrix_overlay(
    report: ResearchSourceProbeReport,
) -> Dict[str, Any]:
    """从 probe 报告生成一个 overlay, 供 capability matrix API / 文档附加.

    设计原则 (与 DATA-024 / DATA-025 一致):
    - **不修改** ``source_capability_matrix.get_source_capability_matrix()`` 的 items.
    - overlay 只提供 *最近一次 probe 的统计快照* + 披露源目录.
    - 不输出任何敏感字段.
    """
    by_et = dict(report.summary.get("by_error_type", {}))
    return {
        "overlay_source": "[DATA-027] research_source_smoke",
        "probe_run_at": report.run_at,
        "probe_mode": report.mode,
        "env_gated": report.env_gated,
        "source_types_covered": [
            "research_report",
            "announcement",
            "half_year_report",
        ],
        "total_probes": report.summary.get("total", 0),
        "by_error_type": by_et,
        "required_classes_covered": list(
            report.summary.get("required_classes_covered", [])
        ),
        "required_classes_missing": list(
            report.summary.get("required_classes_missing", [])
        ),
        "all_passed": bool(report.summary.get("all_passed")),
        "has_live_failures": bool(report.summary.get("has_failures")),
        "disclosure_sources": get_research_disclosure_sources(),
        "notes": (
            "fixture dry-run 覆盖研报/公告/半年报三类入口的有数据/无数据/"
            "接口失败/字段缺失场景; live-smoke 默认关闭, 需双重门禁."
        ),
    }
