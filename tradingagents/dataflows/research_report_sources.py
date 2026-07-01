# [DATA-025] free_research_report_sources
"""免费研报来源目录与 Eastmoney/AKShare 研报源 smoke.

梳理免费可用的券商研报元数据来源, 并对 AKShare ``stock_research_report_em``
(东方财富研报中心 / ``reportapi.eastmoney.com/report/list``) 做小样本 smoke.

核心边界 (与 TASKS.md DATA-025 执行约束一致):
  - 第一版只做**目录** + **小样本 smoke**, 不批量下载 PDF, 不提交版权正文.
  - 免费研报仅作为**观点 / 关注度 / 预期源**, 不能替代公告或财报事实源.
  - 默认 fixture dry-run; live-smoke 必须显式开关 + 环境变量双重门禁.
  - 不读取 / 打印 / 持久化任何 API Key / cookie / token.
  - 不写生产 tradingagents.db; 不调用 LLM; 不改 prompts; 不改强动作门禁.

与 DATA-011 的关系:
  DATA-011 的 ``research_report`` raw_evidence 使用 AKShare ``stock_institute_recommend``
  (券商评级/推荐接口, 字段精简). 本模块验证的 ``stock_research_report_em`` 是东财研报中心
  全量研报元数据 (含 PDF 链接 / 盈利预测 / 东财评级), 作为**外部研报元数据补充源**,
  复用同一组状态语义 (HAS_DATA / NORMAL_NO_DATA / FAILED), 不改变 readiness_score
  与动作门禁.

使用示例 (库):
    from tradingagents.dataflows.research_report_sources import (
        RESEARCH_REPORT_FREE_SOURCES,
        run_research_report_smoke,
        render_research_report_smoke_report,
    )
    report = run_research_report_smoke()           # fixture dry-run
    print(render_research_report_smoke_report(report))

使用示例 (CLI):
    python scripts/run_research_report_smoke.py    # fixture dry-run
    TA_LIVE_DATA_SMOKE=1 python scripts/run_research_report_smoke.py \\
        --live-smoke --symbols 600519.SH,000001.SZ
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from .source_catalog import DataType


# ── 常量 ───────────────────────────────────────────────────────────────

_LIVE_ENV = "TA_LIVE_DATA_SMOKE"
_MAX_SYMBOLS = 5
DEFAULT_SMOKE_SYMBOLS: List[str] = ["600519.SH", "000001.SZ", "603629.SH"]

# AKShare 研报接口方法名 (stock_research_report_em, 不同于 DATA-011 的
# stock_institute_recommend — 前者是东财研报中心全量研报元数据 + PDF 链接)
AKSHARE_REPORT_METHOD = "stock_research_report_em"
EASTMONEY_REPORT_ENDPOINT = "reportapi.eastmoney.com/report/list"


# ── 状态语义 (与 SourceFreshnessStatus / DATA-011 raw_evidence 对齐) ──


class ResearchReportStatus:
    """研报 smoke 状态语义.

    与 ``EvidenceStatus`` / ``SourceFreshnessStatus`` 对齐, 便于和 DATA-011
    raw_evidence / readiness_score 联动, 不引入新状态.
    """

    HAS_DATA = "HAS_DATA"
    NORMAL_NO_DATA = "NORMAL_NO_DATA"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"  # live-smoke env gated / 超过最大样本数
    NOT_RUN = "NOT_RUN"

    ALL = [HAS_DATA, NORMAL_NO_DATA, FAILED, SKIPPED, NOT_RUN]


# ── 免费研报来源目录 ──────────────────────────────────────────────────
#
# 任务 §实现要点 1: 更新数据源能力矩阵, 增加 ``research_report_free_sources``.
# 每条 entry 描述一个免费研报来源的能力边界, 区分:
#   - access_type:    default_smoke (默认 smoke) / supplemental_index (补充索引) /
#                     fact_cross_check (事实源交叉校验, 非研报) / manual_only (人工补充)
#   - content_role:   opinion (观点/预期源) / fact (公告/法披事实源)
#   - is_default_smoke: 是否在 smoke 中默认探测
#
# 任务 §免费来源初版 1-4:
#   1. 东方财富研报中心/AKShare stock_research_report_em — 默认 smoke
#   2. 新浪财经研究评级页 — 补充索引
#   3. 巨潮资讯/CNInfo — 事实源交叉校验 (不是券商研报源)
#   4. 券商官网/上市公司 IR 页面 — 人工补充, 不默认爬取

RESEARCH_REPORT_FREE_SOURCES: List[Dict[str, Any]] = [
    {
        "source_id": "eastmoney_research_report_em",
        "name_cn": "东方财富研报中心 / 个股研报",
        "vendor": "cn_akshare",
        "endpoint": EASTMONEY_REPORT_ENDPOINT,
        "akshare_method": AKSHARE_REPORT_METHOD,
        "data_type": DataType.REPORT.value,
        "fields": [
            "日期", "报告名称", "股票简称", "股票代码", "机构",
            "东财评级", "行业", "盈利预测-收益", "盈利预测-市盈率",
            "报告PDF链接", "近一月个股研报数",
        ],
        "unit": "条",
        "freshness": "daily",
        "rate_limit_risk": "medium",
        "access_type": "default_smoke",
        "content_role": "opinion",
        "is_default_smoke": True,
        "provides_pdf_link": True,
        "usage_note": (
            "通过 AKShare stock_research_report_em(symbol) 获取个股研报元数据、"
            "机构、评级、盈利预测、日期、PDF 链接。仅作观点/关注度/预期源, "
            "不替代公告或财报。第一版只做目录与小样本 smoke, 不批量下载 PDF。"
        ),
        "known_limits": [
            "字段覆盖依赖东财上书机构, 部分研报缺盈利预测/目标价",
            "PDF 正文受版权保护, 不得下载入库",
        ],
    },
    {
        "source_id": "sina_research_rating",
        "name_cn": "新浪财经研究/评级页",
        "vendor": "cn_akshare",
        "endpoint": "stock_institute_recommend_detail",
        "akshare_method": "stock_institute_recommend_detail",
        "data_type": DataType.RATING.value,
        "fields": ["股票代码", "股票名称", "目标价", "最新评级", "评级机构", "分析师", "行业", "评级日期"],
        "unit": "条",
        "freshness": "daily",
        "rate_limit_risk": "medium",
        "access_type": "supplemental_index",
        "content_role": "opinion",
        "is_default_smoke": False,
        "provides_pdf_link": False,
        "usage_note": (
            "AKShare 已接入 (DATA-012 rating). 字段以标题、日期、评级为主, "
            "可作为研报关注度交叉索引源, 不重复 smoke。"
        ),
        "known_limits": ["新浪财经评级数据覆盖不全"],
    },
    {
        "source_id": "cninfo_announcement",
        "name_cn": "巨潮资讯 / CNInfo",
        "vendor": "cn_astock",
        "endpoint": "cninfo.com.cn/hisAnnouncement",
        "akshare_method": "",
        "data_type": DataType.NOTICE.value,
        "fields": ["announcementTitle", "announcementTypeName", "announcementTime", "announcementId"],
        "unit": "条",
        "freshness": "daily",
        "rate_limit_risk": "low",
        "access_type": "fact_cross_check",
        "content_role": "fact",
        "is_default_smoke": False,
        "provides_pdf_link": True,
        "usage_note": (
            "公告/法披真源, 不是券商研报源; 只用于公告与原始披露交叉验证。"
            "研报观点不能凌驾于公告事实之上。"
        ),
        "known_limits": ["需 orgId 映射"],
    },
    {
        "source_id": "broker_ir_page",
        "name_cn": "券商官网 / 上市公司 IR 页面",
        "vendor": "manual",
        "endpoint": "manual",
        "akshare_method": "",
        "data_type": DataType.REPORT.value,
        "fields": [],
        "unit": "条",
        "freshness": "unknown",
        "rate_limit_risk": "unknown",
        "access_type": "manual_only",
        "content_role": "opinion",
        "is_default_smoke": False,
        "provides_pdf_link": True,
        "usage_note": (
            "券商官网/上市公司 IR 页面作为人工补充, 不做默认爬取。"
            "需要时由用户/投资控手动登记, 避免爬虫合规风险。"
        ),
        "known_limits": ["页面结构各异", "需人工核对版权"],
    },
]


def get_research_report_free_sources() -> List[Dict[str, Any]]:
    """返回免费研报来源目录的深拷贝."""
    return [dict(s) for s in RESEARCH_REPORT_FREE_SOURCES]


def get_default_smoke_source() -> Dict[str, Any]:
    """返回默认 smoke 的免费研报来源 (东方财富研报中心)."""
    for s in RESEARCH_REPORT_FREE_SOURCES:
        if s.get("is_default_smoke"):
            return dict(s)
    return dict(RESEARCH_REPORT_FREE_SOURCES[0])


# ── 数据模型 ──────────────────────────────────────────────────────────


@dataclass
class ResearchReportRecord:
    """单条研报元数据 (从 AKShare DataFrame 解析)."""

    date: str = ""
    title: str = ""
    stock_name: str = ""
    stock_code: str = ""
    org: str = ""
    rating: str = ""
    industry: str = ""
    predict_eps: str = ""
    predict_pe: str = ""
    pdf_url: str = ""
    recent_count: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "date": self.date,
            "title": self.title,
            "stock_name": self.stock_name,
            "stock_code": self.stock_code,
            "org": self.org,
            "rating": self.rating,
            "industry": self.industry,
            "predict_eps": self.predict_eps,
            "predict_pe": self.predict_pe,
            "pdf_url": self.pdf_url,
            "recent_count": self.recent_count,
        }


@dataclass
class ResearchReportSmokeResult:
    """单次研报 smoke 结果."""

    symbol: str = ""
    source_id: str = ""
    vendor: str = ""
    status: str = ResearchReportStatus.NOT_RUN
    record_count: int = 0
    latency_ms: float = 0.0
    error: str = ""
    sample_records: List[ResearchReportRecord] = field(default_factory=list)
    diagnosis: str = ""
    fixture_id: str = ""
    probe_mode: str = "fixture"  # fixture / live / skipped

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "source_id": self.source_id,
            "vendor": self.vendor,
            "status": self.status,
            "record_count": self.record_count,
            "latency_ms": round(self.latency_ms, 1),
            "error": (self.error or "")[:200],
            "sample_records": [r.to_dict() for r in self.sample_records],
            "diagnosis": self.diagnosis,
            "fixture_id": self.fixture_id,
            "probe_mode": self.probe_mode,
        }


@dataclass
class ResearchReportSmokeReport:
    """研报 smoke 顶层报告."""

    run_at: str = ""
    date: str = ""
    mode: str = "fixture"  # fixture / live / skipped
    env_gated: bool = True
    symbols: List[str] = field(default_factory=list)
    results: List[ResearchReportSmokeResult] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)
    source_id: str = "eastmoney_research_report_em"
    endpoint: str = EASTMONEY_REPORT_ENDPOINT

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_at": self.run_at,
            "date": self.date,
            "mode": self.mode,
            "env_gated": self.env_gated,
            "symbols": self.symbols,
            "results": [r.to_dict() for r in self.results],
            "summary": self.summary,
            "source_id": self.source_id,
            "endpoint": self.endpoint,
        }


# ── fixture: 有数据 / 无数据 / 接口失败 三类 (任务验收明确要求) ────────
#
# 每个 fixture 模拟 AKShare stock_research_report_em 返回的 DataFrame 形态,
# 解析器面对的是真实列名 (日期 / 报告名称 / 机构 / 东财评级 / 报告PDF链接 ...),
# 而不是测试专用 shortcut.

# 模拟 AKShare stock_research_report_em 返回的 DataFrame 行 (dict 列 = 中文列名)
SMOKE_FIXTURES: Dict[str, Dict[str, Any]] = {
    "HAS_DATA": {
        "description": "东方财富研报中心正常返回多份券商研报",
        "symbol": "600519.SH",
        "vendor": "cn_akshare",
        "rows": [
            {
                "序号": 1,
                "报告名称": "贵州茅台2025年深度研究",
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
                "报告名称": "贵州茅台三季报点评",
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
        "expected_status": ResearchReportStatus.HAS_DATA,
        "expected_record_count": 2,
    },
    "NORMAL_NO_DATA": {
        "description": "接口正常但标的近期无研报覆盖 (新股 / 冷门股 / 停牌)",
        "symbol": "603629.SH",
        "vendor": "cn_akshare",
        "rows": [],
        "expected_status": ResearchReportStatus.NORMAL_NO_DATA,
        "expected_record_count": 0,
    },
    "FAILED": {
        "description": "AKShare / 东财接口失败 (ConnectionError / 限流 / 字段变更)",
        "symbol": "000001.SZ",
        "vendor": "cn_akshare",
        "rows": None,
        "error": "ConnectionError: HTTPSConnectionPool(host=reportapi.eastmoney.com, port=443): Max retries exceeded",
        "expected_status": ResearchReportStatus.FAILED,
        "expected_record_count": 0,
    },
}


# ── 解析 AKShare DataFrame → 结构化 records ──────────────────────────


def _pick(row: Dict[str, Any], *keys: str, default: str = "") -> str:
    """从 DataFrame 行里按多个候选列名取值 (兼容字段覆盖不稳定)."""
    for k in keys:
        if k in row and row[k] not in (None, ""):
            val = row[k]
            # NaN / float nan 兜底
            if isinstance(val, float) and val != val:
                continue
            return str(val)
    return default


def parse_research_report_rows(
    rows: Optional[List[Dict[str, Any]]],
) -> List[ResearchReportRecord]:
    """把 AKShare stock_research_report_em 返回的行解析成结构化 records.

    兼容字段覆盖不稳定: 优先用中文名, 缺失时用英文名兜底.
    不下载 / 不保存 PDF 正文, 只保留 ``报告PDF链接`` 字段.
    """
    if not rows:
        return []
    records: List[ResearchReportRecord] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        record = ResearchReportRecord(
            date=_pick(row, "日期", "publishDate"),
            title=_pick(row, "报告名称", "title"),
            stock_name=_pick(row, "股票简称", "stockName"),
            stock_code=_pick(row, "股票代码", "stockCode"),
            org=_pick(row, "机构", "orgSName"),
            rating=_pick(row, "东财评级", "emRatingName"),
            industry=_pick(row, "行业", "indvInduName"),
            predict_eps=_pick(row, "2026-盈利预测-收益", "predictThisYearEps"),
            predict_pe=_pick(row, "2026-盈利预测-市盈率", "predictThisYearPe"),
            pdf_url=_pick(row, "报告PDF链接", "pdfUrl"),
            recent_count=_pick(row, "近一月个股研报数", "count"),
        )
        records.append(record)
    return records


# ── 单条探测 ──────────────────────────────────────────────────────────


def _probe_fixture(
    fixture_id: str,
    fixture: Dict[str, Any],
) -> ResearchReportSmokeResult:
    """对单个 fixture 跑解析 + 状态判定."""
    rows = fixture.get("rows")
    error = fixture.get("error", "")
    expected_status = fixture.get("expected_status", ResearchReportStatus.NOT_RUN)

    if rows is None:
        # 接口失败 fixture
        status = ResearchReportStatus.FAILED
        records: List[ResearchReportRecord] = []
        diagnosis = error or "接口失败"
    elif len(rows) == 0:
        status = ResearchReportStatus.NORMAL_NO_DATA
        records = []
        diagnosis = fixture.get("description", "正常无数据")
    else:
        records = parse_research_report_rows(rows)
        status = ResearchReportStatus.HAS_DATA if records else ResearchReportStatus.NORMAL_NO_DATA
        diagnosis = fixture.get("description", "有数据")

    return ResearchReportSmokeResult(
        symbol=fixture.get("symbol", ""),
        source_id="eastmoney_research_report_em",
        vendor=fixture.get("vendor", "cn_akshare"),
        status=status,
        record_count=len(records),
        latency_ms=0.0,
        error=error,
        sample_records=records,
        diagnosis=diagnosis,
        fixture_id=fixture_id,
        probe_mode="fixture",
    )


def _probe_live_symbol(
    symbol: str,
    *,
    fetch_fn: Optional[Callable[[str], Any]] = None,
) -> ResearchReportSmokeResult:
    """对一个 symbol 真实调用 AKShare stock_research_report_em 并判定状态.

    *fetch_fn* 用于测试注入; 默认走 ``akshare.stock_research_report_em``.
    """
    result = ResearchReportSmokeResult(
        symbol=symbol,
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
            result.status = ResearchReportStatus.NORMAL_NO_DATA
            result.diagnosis = "AKShare 返回 None"
            return result
        # DataFrame API 兼容
        try:
            empty = df.empty  # pandas DataFrame
        except AttributeError:
            empty = not bool(df)
        if empty:
            result.status = ResearchReportStatus.NORMAL_NO_DATA
            result.diagnosis = "该股近期无券商研报覆盖 (新股/冷门股/停牌属正常)"
            return result
        try:
            rows = df.to_dict(orient="records")
        except AttributeError:
            rows = list(df) if isinstance(df, list) else []
        records = parse_research_report_rows(rows)
        if records:
            result.status = ResearchReportStatus.HAS_DATA
            result.record_count = len(records)
            result.sample_records = records[:5]
            result.diagnosis = f"返回 {len(records)} 条研报元数据"
        else:
            result.status = ResearchReportStatus.NORMAL_NO_DATA
            result.diagnosis = "返回非空 DataFrame 但解析后无有效记录"
    except AttributeError as exc:
        result.status = ResearchReportStatus.FAILED
        result.error = f"AttributeError: {exc}"
        result.diagnosis = "AKShare stock_research_report_em 接口不可用"
    except Exception as exc:
        result.status = ResearchReportStatus.FAILED
        result.error = f"{type(exc).__name__}: {exc}"
        result.diagnosis = "接口调用失败 (网络/限流/字段变更)"
    return result


# ── 汇总 ──────────────────────────────────────────────────────────────


def _compute_summary(
    results: List[ResearchReportSmokeResult],
) -> Dict[str, Any]:
    total = len(results)
    by_status: Dict[str, int] = {}
    for r in results:
        by_status[r.status] = by_status.get(r.status, 0) + 1
    runnable = [r for r in results if r.status != ResearchReportStatus.SKIPPED]
    # live 模式: FAILED 才算真失败
    live_failures = [
        r for r in runnable
        if r.status == ResearchReportStatus.FAILED and r.probe_mode == "live"
    ]
    # fixture 模式: 每条 fixture 必须命中 expected_status (FAILED fixture 应得到 FAILED)
    fixture_runnable = [r for r in runnable if r.probe_mode == "fixture"]
    if fixture_runnable and all(r.probe_mode == "fixture" for r in runnable):
        all_passed = all(
            r.status == SMOKE_FIXTURES.get(r.fixture_id, {}).get(
                "expected_status", r.status
            )
            for r in runnable
        )
    else:
        all_passed = bool(runnable) and not live_failures
    return {
        "total": total,
        "by_status": by_status,
        "HAS_DATA": by_status.get(ResearchReportStatus.HAS_DATA, 0),
        "NORMAL_NO_DATA": by_status.get(ResearchReportStatus.NORMAL_NO_DATA, 0),
        "FAILED": by_status.get(ResearchReportStatus.FAILED, 0),
        "SKIPPED": by_status.get(ResearchReportStatus.SKIPPED, 0),
        "all_passed": all_passed,
        "has_failures": bool(live_failures),
        "total_records": sum(r.record_count for r in results),
    }


# ── 顶层入口 ──────────────────────────────────────────────────────────


def is_live_smoke_enabled() -> bool:
    """``TA_LIVE_DATA_SMOKE=1`` 才允许实盘调用."""
    return os.environ.get(_LIVE_ENV, "") == "1"


def run_research_report_smoke(
    symbols: Optional[List[str]] = None,
    *,
    live_smoke: bool = False,
    fetch_fn: Optional[Callable[[str], Any]] = None,
) -> ResearchReportSmokeReport:
    """运行研报 smoke.

    - 默认 fixture dry-run (回放 HAS_DATA / NORMAL_NO_DATA / FAILED 三类), 不发网络请求.
    - ``live_smoke=True`` 且 ``TA_LIVE_DATA_SMOKE=1`` 时, 真实调用
      AKShare ``stock_research_report_em`` 抽样验证; 否则标 SKIPPED.
    - 最多抽样 ``_MAX_SYMBOLS`` 只, 防止开发任务误触成本敏感的批量调用.
    """
    now = datetime.now()
    report = ResearchReportSmokeReport(
        run_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        date=now.strftime("%Y-%m-%d"),
    )

    if not live_smoke:
        # fixture dry-run
        report.mode = "fixture"
        report.env_gated = True
        results: List[ResearchReportSmokeResult] = []
        for fid, fixture in SMOKE_FIXTURES.items():
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
            ResearchReportSmokeResult(
                symbol=s,
                source_id="eastmoney_research_report_em",
                vendor="cn_akshare",
                status=ResearchReportStatus.SKIPPED,
                diagnosis="TA_LIVE_DATA_SMOKE!=1, 未发起网络调用",
                probe_mode="live",
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
        # 简单限流: 每次调用之间留一点间隔, 避免触发东财限流
        time.sleep(0.5)
    report.results = results
    report.summary = _compute_summary(results)
    return report


# ── 渲染 ──────────────────────────────────────────────────────────────


def render_research_report_smoke_report(
    report: ResearchReportSmokeReport,
) -> str:
    """渲染成 markdown 报告."""
    lines: List[str] = []
    lines.append("# 免费研报来源目录与研报源 Smoke 报告 — DATA-025")
    lines.append("")
    lines.append(
        "> [DATA-025] free_research_report_sources. "
        "梳理免费可用研报来源, 并对 AKShare ``stock_research_report_em`` "
        "(东方财富研报中心) 做小样本 smoke."
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
    lines.append(f"- **Default smoke source**: `{report.source_id}`")
    lines.append(f"- **Endpoint**: `{report.endpoint}`")
    lines.append(f"- **Symbols**: {', '.join(report.symbols) or 'fixture'}")
    lines.append("")

    # ── 观点源 vs 事实源 区分 (任务验收明确要求) ──
    lines.append("## 免费研报来源目录")
    lines.append("")
    lines.append(
        "> **观点源** (研报/评级/预期) 不能替代 **事实源** (公告/财报/法披). "
        "研报观点仅作关注度/预期参考, 公告事实才是动作依据."
    )
    lines.append("")
    lines.append(
        "| source_id | 名称 | vendor | endpoint | data_type | 角色 | 接入方式 | 提供 PDF | 默认 smoke |"
    )
    lines.append(
        "|-----------|------|--------|----------|-----------|------|----------|----------|-----------|"
    )
    for src in RESEARCH_REPORT_FREE_SOURCES:
        role_cn = "观点/预期源" if src["content_role"] == "opinion" else "公告/事实源"
        access_cn = {
            "default_smoke": "默认 smoke",
            "supplemental_index": "补充索引",
            "fact_cross_check": "事实交叉校验",
            "manual_only": "人工补充",
        }.get(src["access_type"], src["access_type"])
        lines.append(
            f"| `{src['source_id']}` | {src['name_cn']} | {src['vendor']} "
            f"| `{src['endpoint']}` | `{src['data_type']}` | {role_cn} "
            f"| {access_cn} | {'是' if src['provides_pdf_link'] else '-'} "
            f"| {'是' if src['is_default_smoke'] else '-'} |"
        )
    lines.append("")

    lines.append("### 各来源字段与边界")
    lines.append("")
    for src in RESEARCH_REPORT_FREE_SOURCES:
        lines.append(f"#### `{src['source_id']}` — {src['name_cn']}")
        lines.append("")
        lines.append(f"- **vendor / endpoint**: `{src['vendor']}` / `{src['endpoint']}`")
        if src.get("akshare_method"):
            lines.append(f"- **AKShare 方法**: `{src['akshare_method']}`")
        lines.append(f"- **data_type**: `{src['data_type']}`")
        lines.append(f"- **角色**: {'观点/预期源 (不能替代公告/财报)' if src['content_role'] == 'opinion' else '公告/事实源'}")
        lines.append(f"- **实时性**: `{src['freshness']}` | **限流风险**: `{src['rate_limit_risk']}`")
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
    lines.append(f"| All runnable passed | {'Yes' if s.get('all_passed') else 'No'} |")
    lines.append(f"| Has failures | {'Yes' if s.get('has_failures') else 'No'} |")
    lines.append("")

    # ── Probe details ──
    lines.append("## Probe Details")
    lines.append("")
    lines.append(
        "| Symbol | Source | Vendor | Mode | Status | Records | Latency (ms) | Fixture | Diagnosis |"
    )
    lines.append(
        "|--------|--------|--------|------|--------|---------|-------------|---------|-----------|"
    )
    for r in report.results:
        diag = (r.diagnosis or "")[:60]
        if len(r.diagnosis) > 60:
            diag += "..."
        lines.append(
            f"| {r.symbol or '-'} | `{r.source_id}` | {r.vendor} | {r.probe_mode} "
            f"| {r.status} | {r.record_count} | {r.latency_ms:.0f} "
            f"| {r.fixture_id or '-'} | {diag or '-'} |"
        )
    lines.append("")

    # ── 样本记录 (HAS_DATA) ──
    has_data_results = [r for r in report.results if r.sample_records]
    if has_data_results:
        lines.append("## Sample Records (HAS_DATA)")
        lines.append("")
        lines.append("> 仅展示研报**元数据** (标题/机构/评级/日期/PDF 链接); 不含 PDF 正文, 不含版权内容.")
        lines.append("")
        for r in has_data_results:
            lines.append(f"### {r.symbol} — {r.record_count} records")
            lines.append("")
            lines.append(
                "| 日期 | 机构 | 评级 | 行业 | 报告名称 | 盈利预测-收益 | PDF 链接 |"
            )
            lines.append(
                "|------|------|------|------|----------|--------------|----------|"
            )
            for rec in r.sample_records[:5]:
                title = rec.title
                if len(title) > 50:
                    title = title[:50] + "..."
                lines.append(
                    f"| {rec.date} | {rec.org} | {rec.rating} | {rec.industry} "
                    f"| {title} | {rec.predict_eps or '-'} | {rec.pdf_url or '-'} |"
                )
            lines.append("")

    # ── fixture coverage ──
    if report.mode == "fixture":
        lines.append("## Fixture Coverage")
        lines.append("")
        lines.append(
            "三类典型场景回放: 有数据 / 无数据 / 接口失败. "
            "证明解析器对每类都能给出正确状态."
        )
        lines.append("")
        lines.append("| fixture_id | expected → actual | description |")
        lines.append("|------------|-------------------|-------------|")
        for r in report.results:
            if not r.fixture_id:
                continue
            expected = SMOKE_FIXTURES.get(r.fixture_id, {}).get(
                "expected_status", "?"
            )
            ok_mark = "✓" if expected == r.status else "✗"
            lines.append(
                f"| `{r.fixture_id}` | `{expected}` → `{r.status}` {ok_mark} "
                f"| {r.diagnosis} |"
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
            "TA_LIVE_DATA_SMOKE=1 python scripts/run_research_report_smoke.py "
            "--live-smoke --symbols 600519.SH,000001.SZ"
        )
        lines.append("```")
        lines.append("")

    # ── 边界声明 ──
    lines.append("## 边界声明")
    lines.append("")
    lines.append("- 免费研报仅作为**观点 / 关注度 / 预期源**, 不能替代公告或财报事实源.")
    lines.append("- 不批量下载 PDF 正文; 不提交版权内容; 不做全市场扫描.")
    lines.append("- 与 DATA-011 `research_report` raw_evidence 共享状态语义 (HAS_DATA / NORMAL_NO_DATA / FAILED), 不改变 readiness_score 与强动作门禁.")
    lines.append("- live-smoke 默认关闭, 需 `TA_LIVE_DATA_SMOKE=1` + `--live-smoke` 双重门禁.")
    lines.append("")
    lines.append("---")
    lines.append("*Generated by research_report_sources.py — `[DATA-025] free_research_report_sources`*")
    lines.append("")
    return "\n".join(lines)


def save_research_report_smoke_report(
    report: ResearchReportSmokeReport,
    output_dir: str = "docs/data_source_reports",
) -> str:
    """把 markdown 报告写到 ``docs/data_source_reports/research_report_sources-YYYY-MM-DD.md``."""
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"research_report_sources-{report.date}.md")
    md = render_research_report_smoke_report(report)
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    return path


# ── DATA-023 能力矩阵 supplement (只读附加, 不改 matrix items) ─────────


def build_capability_matrix_supplement() -> Dict[str, Any]:
    """构造一个只读 supplement, 供 capability matrix API 附加 ``research_report_free_sources``.

    设计原则 (与 fund_flow_source_probe.build_capability_matrix_overlay 一致):
    - **不修改** ``source_capability_matrix.get_source_capability_matrix()`` 的 items 输出
      (避免破坏 DATA-023 已发布 docs/SOURCE_CAPABILITY_MATRIX.md).
    - supplement 只提供免费研报来源目录, 调用方 (API / UI) 把它作为 matrix 顶层
      ``research_report_free_sources`` 字段返回.
    - 不输出任何敏感字段.
    """
    return {
        "supplement_source": "[DATA-025] free_research_report_sources",
        "default_smoke_source_id": "eastmoney_research_report_em",
        "default_smoke_endpoint": EASTMONEY_REPORT_ENDPOINT,
        "default_smoke_akshare_method": AKSHARE_REPORT_METHOD,
        "opinion_sources": [
            s["source_id"] for s in RESEARCH_REPORT_FREE_SOURCES
            if s["content_role"] == "opinion"
        ],
        "fact_sources_for_cross_check": [
            s["source_id"] for s in RESEARCH_REPORT_FREE_SOURCES
            if s["content_role"] == "fact"
        ],
        "sources": get_research_report_free_sources(),
        "notes": (
            "免费研报仅作观点/关注度/预期源, 不能替代公告或财报; "
            "live-smoke 默认关闭, 需 TA_LIVE_DATA_SMOKE=1 + --live-smoke 双重门禁."
        ),
    }
