# [KB-002] local_knowledge_contract
# [HY-001] half_year_contract
"""investment wiki 输出协议 lint：TA 可消费字段检查。

在 KB-001 只读**审计**（库存/健康概览）之上，本模块定义 Tree Work investment
wiki 的**稳定输出契约**，并对每篇页面做规则化 lint，产出可执行的修复建议，
避免后续消化研报后 TA 仍抓不到股票、主题、风险、来源和时效。

与 ``local_knowledge_audit``（KB-001）的关系：
  - KB-001 回答“知识库里有什么、覆盖到什么程度”（inventory + health）。
  - KB-002 回答“每篇页面是否满足 TA 可消费契约”（rule-based pass/fail + 修复建议）。
  - 本模块**复用** KB-001 的只读解析助手（``_split_frontmatter`` /
    ``_parse_frontmatter`` / ``_extract_section_headers`` /
    ``classify_page_type`` / ``_is_nonempty_field`` …），不重复读盘逻辑，
    也不改动 KB-001 的数据类，保持两条链路互相独立。

设计约束（对应任务 KB-002）：
  - **只读**：仅用 ``open(..., "r", encoding="utf-8")`` 与 ``Path.iterdir``，
    绝不向知识库写文件。
  - **不输出长篇原文**：只读取 frontmatter、``^## `` 章节标题与 markdown 表头行，
    不读取段落正文。
  - **不调用 LLM / 不访问外网**：纯标准库 + 复用 KB-001 解析。
  - **不阻塞 TA**：lint 是建议性工具，``machine_readiness=low`` 只降低本地知识源
    置信度，不抛异常、不阻止后续 KB-003 接入。

契约要点（详见 ``docs/local_knowledge_contract.md``）：
  1. 基础 frontmatter（必填）：``title / created / updated / sources / tags / related``。
  2. 推荐机器字段：``symbols / themes / industry_chain_roles / report_type /
     evidence_level / valid_until / source_quality / stale_risk``。
  3. 正文必含章节：一句话总结/核心观点、投资逻辑/核心观点、风险提示、原始资料/关联研报。
  4. 公司评分表必须保留表头：公司|代码|核心业务|板块|利好度|共识度|预计启动|期待周期。
  5. 未验证/扫描失败内容必须显式标记“待补充/低置信”。

HY-001 半年报/财报扩展（详见 ``docs/local_knowledge_contract.md`` §10）：
  - 对 ``report_type ∈ {财报分析, 半年报, 中报}`` 的页面追加 7 条 HYF lint 规则。
  - 新增字段：``financial_period / disclosure_date / source_type / financial_facts /
     segment_facts / management_commentary / forward_guidance / risk_factors /
     source_links``。
  - 区分财报事实 / 管理层表述 / 券商观点 / 媒体观点（``source_type`` 取值表）。
  - HYF-007 检测“券商/媒体观点冒充事实”：``source_type`` 全是 ``broker_report`` /
    ``media`` 时给出 warning，TA 命中后标 ``OPINION_AS_FACT``，不进入事实反证（HY-005）。

使用示例::

    from tradingagents.dataflows.local_knowledge_lint import (
        lint_local_knowledge,
        render_lint_report,
    )
    result = lint_local_knowledge("/Users/maybee/Documents/knowledge")
    print(render_lint_report(result))
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 复用 KB-001 只读解析助手（保持单一解析实现，避免行为分叉）。
from tradingagents.dataflows.local_knowledge_audit import (
    INDEX_MD,
    INVESTMENT_SUBDIR,
    RECOMMENDED_MACHINE_FIELDS,
    RISK_SECTION_PATTERNS,
    SOURCES_SECTION_PATTERNS,
    SUMMARY_SECTION_PATTERNS,
    TODO_MARKERS,
    _any_match,
    _audit_single_page,
    _extract_section_headers,
    _is_nonempty_field,
    _is_valid_until_expired,
    _iter_markdown_files,
    _parse_frontmatter,
    _read_text_safe,
    _safe_str,
    _split_frontmatter,
    classify_page_type,
)
from tradingagents.dataflows.citation_policy import (  # [KB-014] citation_policy
    TIER_BROKER_RESEARCH,
    TIER_MEDIA,
    TIER_ORIGINAL_FILING,
    TIER_UNKNOWN,
    TIER_USER_NOTE,
    CitationAssessment,
    classify_source_quality_tier,
)
# [HY-011] half_year_metadata_sanity — 跨页与组合校验逻辑放在独立模块，
# 这里只在 lint_single_page / lint_local_knowledge 调用其纯函数。
# import 放在 citation_policy 之后，避免循环导入（新模块本身依赖本模块的常量）。
from tradingagents.dataflows.half_year_metadata_sanity import (  # [HY-011]
    HYM_RULE_IDS,
    check_half_year_metadata_cross_page,
    check_half_year_metadata_sanity_single,
    detect_revision_marker,
)


# ── 契约常量 ─────────────────────────────────────────────────────────

# KB-002 契约在 KB-001 基础上把 ``related`` 也提为必填（任务“基础 frontmatter”清单
# 显式包含 related）。保持为独立常量，避免影响 KB-001 的 REQUIRED_FRONTMATTER_FIELDS。
REQUIRED_FRONTMATTER_FIELDS: Tuple[str, ...] = (
    "title",
    "created",
    "updated",
    "sources",
    "tags",
    "related",
)

# “投资逻辑”章节单独列为 warning（一句话总结/核心观点 才是 error 级 summary）。
INVESTMENT_LOGIC_SECTION_PATTERNS: Tuple[str, ...] = (
    "投资逻辑",
    "核心观点",
    "核心结论",
)

# 公司评分表标准表头（顺序无关，但 8 列都必须出现）。
SCORE_TABLE_REQUIRED_COLUMNS: Tuple[str, ...] = (
    "公司",
    "代码",
    "核心业务",
    "板块",
    "利好度",
    "共识度",
    "预计启动",
    "期待周期",
)

# 低置信证据等级（TA 命中后标 LOW_CONFIDENCE，不进入候选加分）。
LOW_CONFIDENCE_EVIDENCE_LEVELS: Tuple[str, ...] = ("C", "c", "低", "low", "LOW")
HIGH_STALE_RISK_VALUES: Tuple[str, ...] = ("高", "high", "HIGH", "高（需更新）")

# 严重程度。info 不计入 readiness 惩罚（待补充/低置信本身是“显式标记”，符合契约）。
SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"

# readiness 阈值（与 KB-001 保持语义一致，但此处基于 lint findings 显式计算）。
_READINESS_HIGH_MAX_WARNINGS = 2
_READINESS_MEDIUM_MAX_ERRORS = 2
_READINESS_MEDIUM_MAX_WARNINGS = 4

# markdown 表头行：``| a | b |`` 且下一行是 ``|---|---|`` 分隔符。
_TABLE_HEADER_RE = re.compile(r"^\s*\|(.+)\|\s*$")
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")


# ── HY-001 半年报/财报扩展常量 ───────────────────────────────────────

# [HY-001] half_year_contract
# 触发半年报扩展 lint 的 report_type 取值；任一命中即视为财报页。
# 任务原文：「为 ``report_type=财报分析/半年报/中报`` 的页面增加专门规则」。
HALF_YEAR_REPORT_TYPES: Tuple[str, ...] = (
    "财报分析",
    "半年报",
    "中报",
)

# 合法的 source_type 取值（半结构化白名单，lint 时只警告“全是观点”不警告“未识别”）。
# 事实类：交易所公告 / 数据表；公司口径：管理层表述；观点类：券商 / 媒体。
SOURCE_TYPE_FACT_VALUES: Tuple[str, ...] = (
    "exchange_filing",
    "fact_table",
    "management_commentary",  # 公司自述视作半事实：能进入事实反证，但需打公司口径标
    "official_notice",
    "regulatory_notice",
)
SOURCE_TYPE_OPINION_VALUES: Tuple[str, ...] = (
    "broker_report",
    "media",
)
SOURCE_TYPE_ALL_VALUES: Tuple[str, ...] = SOURCE_TYPE_FACT_VALUES + SOURCE_TYPE_OPINION_VALUES
FILING_REPORT_TYPES: Tuple[str, ...] = HALF_YEAR_REPORT_TYPES + (
    "年报",
    "一季报",
    "三季报",
    "公告",
)

# financial_period 合法格式：YYYYH1 / YYYYH2 / YYYY中报 / YYYY年报 / YYYY一季报 /
# YYYY三季报 / FYxxQ<n>。lint 接受任一即可，不强制大小写。
_PERIOD_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r"^\d{4}H[12]$"),
    re.compile(r"^\d{4}半年报$"),
    re.compile(r"^\d{4}中报$"),
    re.compile(r"^\d{4}年报$"),
    re.compile(r"^\d{4}一季报$"),
    re.compile(r"^\d{4}三季报$"),
    re.compile(r"^\d{4}Q[1-4]$"),
    re.compile(r"^FY\d{2,4}Q[1-4]$", re.IGNORECASE),
    re.compile(r"^FY\d{2,4}H[12]$", re.IGNORECASE),
)

# disclosure_date：接受 ``YYYY-MM-DD`` / ``YYYY/MM/DD`` / ``YYYYMMDD``。
_DATE_RE = re.compile(r"^\d{4}[-/]?\d{2}[-/]?\d{2}$")


# ── 数据类 ────────────────────────────────────────────────────────────


@dataclass
class LintFinding:
    """单条 lint 发现。"""

    rule_id: str
    severity: str
    field: Optional[str]
    message: str
    fix_suggestion: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "field": self.field,
            "message": self.message,
            "fix_suggestion": self.fix_suggestion,
        }


@dataclass
class PageLintResult:
    """单篇 investment wiki 的 lint 结果。"""

    rel_path: str
    title: Optional[str]
    page_type: str
    machine_readiness: str = "low"
    findings: List[LintFinding] = field(default_factory=list)
    # 便捷聚合
    error_count: int = 0
    warning_count: int = 0
    info_count: int = 0
    is_to_be_supplemented: bool = False
    is_stale: bool = False
    is_low_confidence: bool = False
    has_symbols: bool = False
    has_themes: bool = False
    # [HY-001] half_year_contract — 财报页扩展状态
    is_half_year_report: bool = False
    half_year_period: Optional[str] = None
    half_year_opinion_only: bool = False
    # [KB-014] citation_policy — 来源可信度分层
    source_quality_tier: str = TIER_UNKNOWN
    citation_assessment: Optional[CitationAssessment] = None
    # [HY-011] 跨页版本检查的内部缓存；不进入 to_dict/API 契约。
    _hym_symbol_codes: List[str] = field(default_factory=list, repr=False)
    _hym_is_revision: bool = field(default=False, repr=False)
    _hym_revision_fields: List[str] = field(default_factory=list, repr=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rel_path": self.rel_path,
            "title": self.title,
            "page_type": self.page_type,
            "machine_readiness": self.machine_readiness,
            "findings": [f.to_dict() for f in self.findings],
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "info_count": self.info_count,
            "is_to_be_supplemented": self.is_to_be_supplemented,
            "is_stale": self.is_stale,
            "is_low_confidence": self.is_low_confidence,
            "has_symbols": self.has_symbols,
            "has_themes": self.has_themes,
            # HY-001 扩展字段
            "is_half_year_report": self.is_half_year_report,
            "half_year_period": self.half_year_period,
            "half_year_opinion_only": self.half_year_opinion_only,
            # KB-014 来源可信度分层
            "source_quality_tier": self.source_quality_tier,
            "citation_assessment": (
                self.citation_assessment.to_dict()
                if self.citation_assessment is not None
                else None
            ),
        }


@dataclass
class KnowledgeLintResult:
    """整库 lint 结果聚合。"""

    knowledge_root: str
    scanned_at: str
    contract_version: str = "kb-002-v1"
    page_count: int = 0
    page_results: List[PageLintResult] = field(default_factory=list)
    readiness_counts: Dict[str, int] = field(default_factory=dict)
    findings_by_severity: Dict[str, int] = field(default_factory=dict)
    findings_by_rule: Dict[str, int] = field(default_factory=dict)
    pages_low_readiness: List[str] = field(default_factory=list)
    pages_missing_symbols: List[str] = field(default_factory=list)
    pages_missing_risk: List[str] = field(default_factory=list)
    pages_missing_summary: List[str] = field(default_factory=list)
    pages_missing_sources: List[str] = field(default_factory=list)
    pages_low_confidence: List[str] = field(default_factory=list)
    pages_stale: List[str] = field(default_factory=list)
    pages_to_be_supplemented: List[str] = field(default_factory=list)
    index_missing_pages: List[str] = field(default_factory=list)
    top_fix_priorities: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    # [HY-001] half_year_contract — 财报页扩展聚合
    pages_half_year: List[str] = field(default_factory=list)
    pages_half_year_period_missing: List[str] = field(default_factory=list)
    pages_half_year_opinion_only: List[str] = field(default_factory=list)
    pages_half_year_facts_missing: List[str] = field(default_factory=list)
    # [KB-014] citation_policy — 来源可信度分层聚合
    pages_weak_source: List[str] = field(default_factory=list)
    pages_broker_only: List[str] = field(default_factory=list)
    pages_opinion_as_fact: List[str] = field(default_factory=list)
    tier_counts: Dict[str, int] = field(default_factory=dict)
    # [HY-011] half_year_metadata_sanity — 半年报元数据 sanity 聚合
    pages_invalid_or_future_disclosure: List[str] = field(default_factory=list)
    pages_period_end_after_disclosure: List[str] = field(default_factory=list)
    pages_period_mismatch: List[str] = field(default_factory=list)
    pages_symbol_name_mismatch: List[str] = field(default_factory=list)
    pages_multi_version_conflict: List[str] = field(default_factory=list)
    pages_revision_detected: List[str] = field(default_factory=list)
    pages_period_end_mismatch: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "knowledge_root": self.knowledge_root,
            "scanned_at": self.scanned_at,
            "contract_version": self.contract_version,
            "page_count": self.page_count,
            "page_results": [p.to_dict() for p in self.page_results],
            "readiness_counts": dict(self.readiness_counts),
            "findings_by_severity": dict(self.findings_by_severity),
            "findings_by_rule": dict(self.findings_by_rule),
            "pages_low_readiness": list(self.pages_low_readiness),
            "pages_missing_symbols": list(self.pages_missing_symbols),
            "pages_missing_risk": list(self.pages_missing_risk),
            "pages_missing_summary": list(self.pages_missing_summary),
            "pages_missing_sources": list(self.pages_missing_sources),
            "pages_low_confidence": list(self.pages_low_confidence),
            "pages_stale": list(self.pages_stale),
            "pages_to_be_supplemented": list(self.pages_to_be_supplemented),
            "index_missing_pages": list(self.index_missing_pages),
            "top_fix_priorities": list(self.top_fix_priorities),
            "errors": list(self.errors),
            # HY-001 扩展字段
            "pages_half_year": list(self.pages_half_year),
            "pages_half_year_period_missing": list(self.pages_half_year_period_missing),
            "pages_half_year_opinion_only": list(self.pages_half_year_opinion_only),
            "pages_half_year_facts_missing": list(self.pages_half_year_facts_missing),
            # KB-014 来源可信度分层
            "pages_weak_source": list(self.pages_weak_source),
            "pages_broker_only": list(self.pages_broker_only),
            "pages_opinion_as_fact": list(self.pages_opinion_as_fact),
            "tier_counts": dict(self.tier_counts),
            # HY-011 半年报元数据 sanity
            "pages_invalid_or_future_disclosure": list(
                self.pages_invalid_or_future_disclosure
            ),
            "pages_period_end_after_disclosure": list(
                self.pages_period_end_after_disclosure
            ),
            "pages_period_mismatch": list(self.pages_period_mismatch),
            "pages_symbol_name_mismatch": list(self.pages_symbol_name_mismatch),
            "pages_multi_version_conflict": list(self.pages_multi_version_conflict),
            "pages_revision_detected": list(self.pages_revision_detected),
            "pages_period_end_mismatch": list(
                self.pages_period_end_mismatch
            ),
        }


# ── 规则实现 ─────────────────────────────────────────────────────────


def _add(
    findings: List[LintFinding],
    rule_id: str,
    severity: str,
    message: str,
    fix_suggestion: str,
    field_name: Optional[str] = None,
) -> None:
    findings.append(
        LintFinding(
            rule_id=rule_id,
            severity=severity,
            field=field_name,
            message=message,
            fix_suggestion=fix_suggestion,
        )
    )


def _extract_table_header_rows(body: str) -> List[str]:
    """提取 markdown 表头行（紧跟分隔符的 ``| a | b |`` 行）。

    只取表头文本用于列名匹配，不读取表格数据行，避免输出原文。
    """
    headers: List[str] = []
    lines = body.splitlines()
    for idx, line in enumerate(lines):
        if _TABLE_SEPARATOR_RE.match(line) and idx > 0:
            prev = lines[idx - 1]
            m = _TABLE_HEADER_RE.match(prev)
            if m:
                headers.append(m.group(1))
    return headers


def _check_required_frontmatter(
    frontmatter: Dict[str, Any], findings: List[LintFinding]
) -> None:
    for f in REQUIRED_FRONTMATTER_FIELDS:
        if not _is_nonempty_field(frontmatter.get(f)):
            if f == "sources":
                # sources 空：error，且给单独的修复建议
                _add(
                    findings,
                    "FMR-001",
                    SEVERITY_ERROR,
                    f"必填 frontmatter 字段 ``{f}`` 缺失或为空",
                    "frontmatter 必须提供非空 ``sources``（如 ``- \"[[../../raw/xxx.md|某研报]]\"``），"
                    "TA 据此溯源，不能仅凭正文结论。",
                    field_name=f,
                )
            elif f == "related":
                _add(
                    findings,
                    "FMR-001",
                    SEVERITY_WARNING,
                    f"基础 frontmatter 字段 ``{f}`` 缺失（KB-002 契约列为必填）",
                    "补 ``related: [[investment/xxx]]`` 或空数组 ``related: []``，"
                    "便于 TA 关联同主题页面。",
                    field_name=f,
                )
            else:
                _add(
                    findings,
                    "FMR-001",
                    SEVERITY_ERROR,
                    f"必填 frontmatter 字段 ``{f}`` 缺失或为空",
                    f"在 frontmatter 补 ``{f}: <值>``，这是 TA 索引/排序的最小元数据。",
                    field_name=f,
                )


def _check_recommended_fields(
    frontmatter: Dict[str, Any], findings: List[LintFinding]
) -> None:
    for f in RECOMMENDED_MACHINE_FIELDS:
        if not _is_nonempty_field(frontmatter.get(f)):
            _add(
                findings,
                "FMR-002",
                SEVERITY_WARNING,
                f"推荐机器字段 ``{f}`` 缺失",
                _RECOMMENDED_FIELD_HINTS.get(f, f"补 ``{f}`` 字段。"),
                field_name=f,
            )


_RECOMMENDED_FIELD_HINTS: Dict[str, str] = {
    "symbols": "补 ``symbols: [\"603296.SH 华勤技术\"]``（代码+空格+简称）；"
    "缺失则不进入 KB-003 symbol 命中索引。",
    "themes": "补 ``themes: [AI服务器]``；TA 按主题召回相关 wiki。",
    "industry_chain_roles": "补 ``industry_chain_roles: [AI服务器ODM]``；"
    "昊天左侧候选用它定位产业链角色。",
    "report_type": "补 ``report_type: 公司点评|行业|综述|数据表|财报分析`` 之一。",
    "evidence_level": "补 ``evidence_level: A|B|C``（C=低置信，TA 标 LOW_CONFIDENCE）。",
    "valid_until": "补 ``valid_until: 2026-12-31`` 或 ``长期``；过期页面 TA 标 STALE。",
    "source_quality": "补 ``source_quality: 高|中|低``。",
    "stale_risk": "补 ``stale_risk: 低|中|高``；``高`` 的页面 TA 不用于实时结论。",
}


def _check_sections(
    section_headers: List[str], findings: List[LintFinding]
) -> None:
    if not _any_match(section_headers, SUMMARY_SECTION_PATTERNS):
        _add(
            findings,
            "SEC-001",
            SEVERITY_ERROR,
            "缺“一句话总结/核心观点/核心结论/投资逻辑”章节",
            "补 ``## 一句话总结``（1-2 句），TA 仅取该段作为背景摘要。",
        )
    if not _any_match(section_headers, INVESTMENT_LOGIC_SECTION_PATTERNS):
        _add(
            findings,
            "SEC-004",
            SEVERITY_WARNING,
            "缺“投资逻辑/核心观点”章节",
            "补 ``## 投资逻辑``，列 3-5 条核心驱动，TA 作为观点源。",
        )
    if not _any_match(section_headers, RISK_SECTION_PATTERNS):
        _add(
            findings,
            "SEC-002",
            SEVERITY_ERROR,
            "缺“风险提示”章节",
            "补 ``## 风险提示``，TA 在 raw_evidence 中标记 risk 段，避免只看利好。",
        )
    if not _any_match(section_headers, SOURCES_SECTION_PATTERNS):
        _add(
            findings,
            "SEC-003",
            SEVERITY_WARNING,
            "缺“原始资料/关联研报”章节",
            "补 ``## 原始资料`` 或在 frontmatter ``sources`` 写非空列表，TA 据此溯源。",
        )


def _check_symbols_for_typed_page(
    page_type: str, has_symbols: bool, findings: List[LintFinding]
) -> None:
    # 公司页/评分表必须有 symbols，否则 KB-003 symbol 命中会漏。
    if page_type in ("company", "score_table") and not has_symbols:
        _add(
            findings,
            "SYM-001",
            SEVERITY_ERROR,
            f"``{page_type}`` 类型页面缺 ``symbols`` 字段",
            "补 ``symbols: [\"603296.SH 华勤技术\"]``，否则 TA 无法按 symbol 命中本页。",
            field_name="symbols",
        )


def _check_score_table_headers(
    page_type: str, body: str, findings: List[LintFinding]
) -> None:
    if page_type != "score_table":
        return
    header_rows = _extract_table_header_rows(body)
    joined = " | ".join(header_rows)
    missing_cols = [c for c in SCORE_TABLE_REQUIRED_COLUMNS if c not in joined]
    if missing_cols:
        _add(
            findings,
            "TBL-001",
            SEVERITY_ERROR,
            f"评分表缺标准表头列：{', '.join(missing_cols)}",
            "评分表必须保留表头：``| 公司 | 代码 | 核心业务 | 板块 | 利好度 | "
            "共识度 | 预计启动 | 期待周期 |``，TA 据此解析候选评分。",
        )


def _check_todo_marker(
    body: str, title: Optional[str], filename: str, findings: List[LintFinding]
) -> bool:
    """检查“待补充/低置信”显式标记。返回是否被标记。"""
    has_todo = any(m in body for m in TODO_MARKERS) or "待补充" in (title or "") or "待补充" in filename
    if has_todo:
        _add(
            findings,
            "TODO-001",
            SEVERITY_INFO,
            "页面显式标记“待补充/低置信”（符合契约第 5 条）",
            "TA 接入时降级为 LOW_CONFIDENCE，不进入候选加分；无需修复，仅需保持标记。",
        )
    return has_todo


def _check_stale_and_confidence(
    frontmatter: Dict[str, Any], findings: List[LintFinding]
) -> Tuple[bool, bool]:
    """返回 (is_stale, is_low_confidence)。"""
    stale_val = _safe_str(frontmatter.get("stale_risk")) or ""
    is_stale_high = stale_val in HIGH_STALE_RISK_VALUES
    valid_until = _safe_str(frontmatter.get("valid_until"))
    is_expired = _is_valid_until_expired(valid_until)
    is_stale = is_stale_high or is_expired
    if is_stale_high:
        _add(
            findings,
            "STALE-001",
            SEVERITY_WARNING,
            f"``stale_risk={stale_val}``，知识可能过时",
            "TA 命中后标 STALE 并提示 ``needs_tree_work_research``，不直接用于实时结论。",
            field_name="stale_risk",
        )
    if is_expired:
        _add(
            findings,
            "STALE-002",
            SEVERITY_WARNING,
            f"``valid_until={valid_until}`` 已过期",
            "更新 ``valid_until`` 或回 Tree Work 重新消化；过期页 TA 标 STALE。",
            field_name="valid_until",
        )

    evidence = _safe_str(frontmatter.get("evidence_level")) or ""
    is_low_conf = evidence in LOW_CONFIDENCE_EVIDENCE_LEVELS
    if is_low_conf:
        _add(
            findings,
            "EVID-001",
            SEVERITY_INFO,
            f"``evidence_level={evidence}`` 为低置信",
            "TA 标 LOW_CONFIDENCE，不提升候选层级；补证后升到 A/B。",
            field_name="evidence_level",
        )
    return is_stale, is_low_conf


# ── HY-001 半年报/财报扩展 lint ──────────────────────────────────────


def _is_half_year_report(frontmatter: Dict[str, Any]) -> bool:
    """[HY-001] 判断页面是否触发半年报/财报扩展 lint。

    触发条件：``report_type ∈ {财报分析, 半年报, 中报}``。
    其余 report_type（公司点评/行业/数据表/...）**不**触发 HYF 规则，
    避免普通公司页被误判为财报页。
    """
    report_type = _safe_str(frontmatter.get("report_type")) or ""
    return report_type in HALF_YEAR_REPORT_TYPES


def _is_valid_financial_period(value: Optional[str]) -> bool:
    """[HY-001] 校验 ``financial_period`` 格式。

    接受：``2025H1`` / ``2025H2`` / ``2025中报`` / ``2025年报`` /
    ``2025一季报`` / ``2025三季报`` / ``2025Q1`` / ``FY26Q1`` / ``FY26H1``。
    其他格式（如 ``2025`` / ``上半年`` / ``最新``）视为不合法。
    """
    if not value:
        return False
    text = value.strip()
    if not text:
        return False
    return any(p.match(text) for p in _PERIOD_PATTERNS)


def _is_valid_disclosure_date(value: Optional[str]) -> bool:
    """[HY-001] 校验 ``disclosure_date`` 是否为合法日期格式。

    接受 ``YYYY-MM-DD`` / ``YYYY/MM/DD`` / ``YYYYMMDD``；不校验真实日历日
    （避免 2/30 这种边界把整页 lint 打挂，TA 侧只用它判断“是否有披露日”）。
    """
    if not value:
        return False
    text = value.strip()
    if not text:
        return False
    return bool(_DATE_RE.match(text))


def _normalize_source_type_list(value: Any) -> List[str]:
    """[HY-001] 把 frontmatter ``source_type`` 字段归一为小写字符串列表。

    接受 list / 单字符串 / 逗号分隔字符串。``None`` 或空 → ``[]``。
    例：``[Exchange_Filing, broker_report]`` → ``["exchange_filing", "broker_report"]``。
    """
    if value is None:
        return []
    if isinstance(value, str):
        items = [s.strip() for s in value.split(",")]
    elif isinstance(value, (list, tuple)):
        items = [str(s).strip() for s in value]
    else:
        return []
    return [s.lower() for s in items if s]


def _check_half_year_report(
    frontmatter: Dict[str, Any],
    has_symbols: bool,
    findings: List[LintFinding],
) -> Tuple[bool, Optional[str], bool]:
    """[HY-001] 对财报/半年报/中报页跑 7 条 HYF 规则。

    返回 ``(is_half_year_report, half_year_period, half_year_opinion_only)``。
    非财报页直接返回 ``(False, None, False)`` 不报任何 finding。
    """
    if not _is_half_year_report(frontmatter):
        return False, None, False

    # HYF-001：financial_period 缺失/格式不合法（error）
    period_raw = _safe_str(frontmatter.get("financial_period"))
    period_text = period_raw or ""
    if not period_text or not _is_valid_financial_period(period_text):
        _add(
            findings,
            "HYF-001",
            SEVERITY_ERROR,
            f"财报/半年报页缺合法 ``financial_period``（当前: {period_text or '缺失'}）",
            "补 ``financial_period: 2025H1`` / ``FY26Q1`` / ``2025中报`` / "
            "``2025年报`` 之一；TA 据此判断是否最新、是否过期。",
            field_name="financial_period",
        )
        period_text = None  # 统一：缺失/非法都视为 None

    # HYF-002：symbols 缺失（error）。财报页必为单/多公司，缺代码无法建事实表。
    if not has_symbols:
        _add(
            findings,
            "HYF-002",
            SEVERITY_ERROR,
            "财报/半年报页缺 ``symbols``",
            "补 ``symbols: [\"603296.SH 华勤技术\"]``；HY-003 事实索引按 symbol 建表。",
            field_name="symbols",
        )

    # HYF-003：disclosure_date 缺失/格式不合法（warning）
    disclosure = _safe_str(frontmatter.get("disclosure_date"))
    if not disclosure or not _is_valid_disclosure_date(disclosure):
        _add(
            findings,
            "HYF-003",
            SEVERITY_WARNING,
            f"财报/半年报页缺合法 ``disclosure_date``（当前: {disclosure or '缺失'}）",
            "补 ``disclosure_date: 2026-08-30``（交易所披露日）；"
            "缺失则 TA 标 ``disclosure_unknown``。",
            field_name="disclosure_date",
        )

    # HYF-004：source_type 缺失（warning）
    source_types = _normalize_source_type_list(frontmatter.get("source_type"))
    if not source_types:
        _add(
            findings,
            "HYF-004",
            SEVERITY_WARNING,
            "财报/半年报页缺 ``source_type`` 字段",
            "补 ``source_type: [exchange_filing, fact_table, management_commentary]``，"
            "区分事实/公司口径/券商观点；详见 ``docs/local_knowledge_contract.md`` §10.1。",
            field_name="source_type",
        )

    # HYF-005：financial_facts 缺失（warning）
    if not _is_nonempty_field(frontmatter.get("financial_facts")):
        _add(
            findings,
            "HYF-005",
            SEVERITY_WARNING,
            "财报/半年报页缺 ``financial_facts``（无可机读事实）",
            "补 ``financial_facts: [营收 150亿 (+30%), 毛利率 25%]``，"
            "HY-003 据此建事实表、HY-005 据此反证旧研报观点。",
            field_name="financial_facts",
        )

    # HYF-006：risk_factors 缺失（warning，与 ## 风险提示 章节互补）
    if not _is_nonempty_field(frontmatter.get("risk_factors")):
        _add(
            findings,
            "HYF-006",
            SEVERITY_WARNING,
            "财报/半年报页缺 ``risk_factors`` 结构化风险清单",
            "补 ``risk_factors: [客户集中度, 汇率]``；与正文 ``## 风险提示`` 互补，"
            "TA 在 HY-005 反证时按字段抽取。",
            field_name="risk_factors",
        )

    # HYF-007：source_type 全是 broker_report/media（观点冒充事实）→ warning
    opinion_only = bool(source_types) and all(
        s in SOURCE_TYPE_OPINION_VALUES for s in source_types
    )
    if opinion_only:
        _add(
            findings,
            "HYF-007",
            SEVERITY_WARNING,
            f"财报/半年报页 ``source_type={source_types}`` 全为券商/媒体观点，"
            "存在观点冒充事实风险",
            "至少补一个事实类来源（``exchange_filing`` / ``fact_table`` / "
            "``management_commentary``）；或在 ``report_type`` 改回 ``公司点评``。"
            "TA 命中后标 ``OPINION_AS_FACT``，不进入 HY-005 事实反证。",
            field_name="source_type",
        )

    return True, period_text, opinion_only


# ── KB-014 来源可信度分层 lint ────────────────────────────────────────


def _check_citation_policy(
    frontmatter: Dict[str, Any],
    page_type: str,
    findings: List[LintFinding],
) -> CitationAssessment:
    """[KB-014] citation_policy — 评估单页来源可信度分层并产出 CIT- findings。

    规则：
      - CIT-001（warning）：``source_quality_tier=unknown``（来源字段全缺或无法识别）。
      - CIT-002（info）：tier 为 ``broker_research`` / ``media`` / ``user_note``
        且 report_type 偏向财报/公告披露类（观点冒充事实风险提示）。
      - CIT-003（info）：tier 为 ``media`` / ``user_note`` / ``unknown``
        （弱来源，TA 标 WEAK_SOURCE，不计入 readiness 惩罚）。

    不会把 tier 直接转成 error，避免弱来源页面被"过滤"——只降权，不阻塞。
    """
    assessment = classify_source_quality_tier(frontmatter)

    if assessment.tier == TIER_UNKNOWN:
        _add(
            findings,
            "CIT-001",
            SEVERITY_WARNING,
            "页面缺来源字段（``sources`` / ``source_type`` 均空且无公告关键词）",
            "在 frontmatter 补 ``source_type: [exchange_filing]`` 或 "
            "``sources: [巨潮资讯 <公告URL>]``；TA 据此评估可信度。",
        )

    report_type = _safe_str(frontmatter.get("report_type")) or ""
    if (
        assessment.tier in (TIER_BROKER_RESEARCH, TIER_MEDIA, TIER_USER_NOTE)
        and report_type in FILING_REPORT_TYPES
    ):
        _add(
            findings,
            "CIT-002",
            SEVERITY_INFO,
            f"report_type={report_type} 但 tier={assessment.tier}，"
            "观点冒充事实风险（不阻塞，但 TA 标 OPINION_AS_FACT）",
            "补 ``source_type: [exchange_filing, fact_table]`` 升级为 original_filing，"
            "或把 ``report_type`` 改回 ``公司点评``。",
            field_name="source_type",
        )

    if assessment.tier in (TIER_MEDIA, TIER_USER_NOTE, TIER_UNKNOWN):
        _add(
            findings,
            "CIT-003",
            SEVERITY_INFO,
            f"tier={assessment.tier}，弱来源（仅作背景/线索）",
            "TA 标 WEAK_SOURCE，不进入候选加分；建议补公告/研报链接后升级。",
        )

    return assessment


def _compute_readiness(
    error_count: int,
    warning_count: int,
    is_to_be_supplemented: bool,
    is_stale: bool,
) -> str:
    """基于 lint findings 计算 machine_readiness: high/medium/low。

    规则（与 KB-001 语义对齐，但此处由 findings 显式推导，便于解释）：
      - 待补充/低置信标记本身不算 error（契约允许显式标记）。
      - stale/过期不直接降到 low，但会阻止 high。
    """
    if is_to_be_supplemented:
        # 待补充页一律 low：TA 不应基于未完成页做结论。
        return "low"
    if error_count == 0 and warning_count <= _READINESS_HIGH_MAX_WARNINGS and not is_stale:
        return "high"
    if error_count <= _READINESS_MEDIUM_MAX_ERRORS and warning_count <= _READINESS_MEDIUM_MAX_WARNINGS:
        return "medium"
    return "low"


# ── 单页 lint ─────────────────────────────────────────────────────────


def lint_single_page(rel_path: str, abs_path: Path) -> PageLintResult:
    """对单篇 investment wiki 跑全套契约 lint（只读）。"""
    text = _read_text_safe(abs_path)
    fm_text, body = _split_frontmatter(text)
    frontmatter = _parse_frontmatter(fm_text)
    section_headers = _extract_section_headers(body)

    title = _safe_str(frontmatter.get("title")) or abs_path.stem
    # 复用 KB-001 的分类器，保持类型语义一致。
    body_has_todo = any(m in body for m in TODO_MARKERS)
    page_type = classify_page_type(
        abs_path.name, title, frontmatter, section_headers, body_has_todo
    )

    findings: List[LintFinding] = []
    _check_required_frontmatter(frontmatter, findings)
    _check_recommended_fields(frontmatter, findings)
    _check_sections(section_headers, findings)

    has_symbols = _is_nonempty_field(frontmatter.get("symbols"))
    has_themes = _is_nonempty_field(frontmatter.get("themes"))
    _check_symbols_for_typed_page(page_type, has_symbols, findings)
    _check_score_table_headers(page_type, body, findings)

    is_todo = _check_todo_marker(body, title, abs_path.name, findings)
    is_stale, is_low_conf = _check_stale_and_confidence(frontmatter, findings)

    # [HY-001] half_year_contract — 财报/半年报/中报页追加 7 条 HYF 规则。
    # 放在通用规则之后，便于报告里“通用规则 → HYF 扩展”顺序阅读。
    is_hy, hy_period, hy_opinion_only = _check_half_year_report(
        frontmatter, has_symbols, findings
    )

    # [KB-014] citation_policy — 来源可信度分层评估（追加在 HYF 之后）。
    # 不论是否为财报页都跑：所有 investment wiki 都需要标注 tier。
    citation_assessment = _check_citation_policy(
        frontmatter, page_type, findings
    )

    # [HY-011] half_year_metadata_sanity — 组合关系校验（追加在 KB-014 之后）。
    # 只对 half_year_report 页跑；非财报页返回空列表。单页规则 HYM-001~004/007。
    # 跨页规则 HYM-005/006 在 ``lint_local_knowledge`` 阶段二跑（需要全量页集合）。
    findings.extend(
        check_half_year_metadata_sanity_single(
            frontmatter, has_symbols=has_symbols
        )
    )

    error_count = sum(1 for f in findings if f.severity == SEVERITY_ERROR)
    warning_count = sum(1 for f in findings if f.severity == SEVERITY_WARNING)
    info_count = sum(1 for f in findings if f.severity == SEVERITY_INFO)

    readiness = _compute_readiness(error_count, warning_count, is_todo, is_stale)

    result = PageLintResult(
        rel_path=rel_path,
        title=title,
        page_type=page_type,
        machine_readiness=readiness,
        findings=findings,
        error_count=error_count,
        warning_count=warning_count,
        info_count=info_count,
        is_to_be_supplemented=is_todo,
        is_stale=is_stale,
        is_low_confidence=is_low_conf,
        has_symbols=has_symbols,
        has_themes=has_themes,
        # HY-001 扩展字段
        is_half_year_report=is_hy,
        half_year_period=hy_period,
        half_year_opinion_only=hy_opinion_only,
        # KB-014 来源可信度分层
        source_quality_tier=citation_assessment.tier,
        citation_assessment=citation_assessment,
    )

    # [HY-011] half_year_metadata_sanity — 缓存跨页检查所需的最小元信息。
    # 仅在 PageLintResult 上挂私有字段（前缀 _hym_），不进入 to_dict 序列化，
    # 避免污染 API schema。cross-page 阶段读取这些字段做分组。
    _cache_cross_page_meta(result, frontmatter, is_hy, hy_period)

    return result


def _cache_cross_page_meta(
    result: PageLintResult,
    frontmatter: Dict[str, Any],
    is_hy: bool,
    hy_period: Optional[str],
) -> None:
    """[HY-011] 把跨页 HYM-005/006 需要的元信息缓存到 PageLintResult 私有字段。

    - ``_hym_symbol_codes``：解析出的市场代码列表（如 ``["603296.SH"]``）。
    - ``_hym_is_revision``：是否带修订标记（``revision`` / ``is_revised`` /
      ``supersedes`` / ``amendment``）。
    - ``_hym_revision_fields``：实际命中的修订字段名。

    非财报页或缺 symbol/period 的页：缓存空值，跨页检查自动跳过。
    """
    symbol_codes: List[str] = []
    if is_hy and hy_period:
        symbols_raw = frontmatter.get("symbols")
        entries: List[Any] = []
        if isinstance(symbols_raw, str):
            entries = [symbols_raw]
        elif isinstance(symbols_raw, (list, tuple)):
            entries = [s for s in symbols_raw if s is not None]
        for entry in entries:
            text = entry.strip() if isinstance(entry, str) else ""
            if not text:
                continue
            # 仅取 CODE 部分（空格前）；NAME 不参与跨页分组。
            code = text.split(maxsplit=1)[0].strip() if " " in text else text.strip()
            if code:
                symbol_codes.append(code)

    is_rev, rev_fields = detect_revision_marker(frontmatter)
    result._hym_symbol_codes = symbol_codes
    result._hym_is_revision = is_rev
    result._hym_revision_fields = rev_fields


# ── 整库 lint ─────────────────────────────────────────────────────────


def _collect_index_set(index_path: Path) -> set:
    """复用 KB-001 的 index 解析思路，返回 index 引用的 stem 集合。"""
    if not index_path.exists():
        return set()
    text = _read_text_safe(index_path)
    # 与 KB-001 ``_INDEX_LINK_RE`` 保持一致的口径：``[[investment/<name>...]]``
    pattern = re.compile(r"\[\[investment[/.]([^\]\|]+?)(?:\|[^\]]*)?\]\]")
    out = set()
    for m in pattern.finditer(text):
        target = m.group(1).strip()
        name = target.split("/")[-1]
        if name.endswith(".md"):
            name = name[:-3]
        if name:
            out.add(name)
    return out


def _build_top_fix_priorities(result: KnowledgeLintResult) -> List[Dict[str, Any]]:
    """按影响面排序，给出 Top 修复优先级（给 Tree Work/HR Agent 直接执行）。"""
    priorities: List[Dict[str, Any]] = []
    rule_to_pages: Dict[str, List[str]] = {}
    for page in result.page_results:
        for f in page.findings:
            if f.severity == SEVERITY_INFO:
                continue
            rule_to_pages.setdefault(f.rule_id, []).append(page.rel_path)

    # error 规则优先，其次按命中页数倒序。
    def _rank(item):
        rule_id = item[0]
        is_error = any(
            f.rule_id == rule_id and f.severity == SEVERITY_ERROR
            for page in result.page_results
            for f in page.findings
        )
        return (0 if is_error else 1, -len(item[1]), rule_id)

    for rule_id, pages in sorted(rule_to_pages.items(), key=_rank):
        # 取该规则第一条的修复建议作为代表。
        suggestion = ""
        for page in result.page_results:
            for f in page.findings:
                if f.rule_id == rule_id:
                    suggestion = f.fix_suggestion
                    break
            if suggestion:
                break
        priorities.append(
            {
                "rule_id": rule_id,
                "affected_pages": len(pages),
                "samples": sorted(pages)[:5],
                "fix_suggestion": suggestion,
            }
        )
    return priorities


def lint_local_knowledge(knowledge_root: str) -> KnowledgeLintResult:
    """只读 lint 整个 investment 分区，产出契约符合度结果。

    参数:
        knowledge_root: 知识库根目录绝对路径（如 ``~/Documents/knowledge``）。

    返回:
        :class:`KnowledgeLintResult`。永远不会因单页解析失败而抛异常：
        单页失败记入 ``errors`` 并跳过，保证整库 lint 总能跑完
       （不阻塞 TA，符合 KB-002 验收要求）。
    """
    root = Path(knowledge_root).expanduser()
    result = KnowledgeLintResult(
        knowledge_root=str(root),
        scanned_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )

    if not root.exists():
        result.errors.append(f"knowledge_root 不存在: {root}")
        result.readiness_counts = {"high": 0, "medium": 0, "low": 0}
        result.findings_by_severity = {
            SEVERITY_ERROR: 0,
            SEVERITY_WARNING: 0,
            SEVERITY_INFO: 0,
        }
        return result

    investment_dir = root / INVESTMENT_SUBDIR
    index_path = root / INDEX_MD

    md_files = _iter_markdown_files(investment_dir)
    if not md_files and not investment_dir.exists():
        result.errors.append(f"investment 分区不存在: {investment_dir}")

    for md in md_files:
        rel = str(md.relative_to(root))
        try:
            page = lint_single_page(rel, md)
        except Exception as exc:  # pragma: no cover - 容错：单页失败不影响整库
            result.errors.append(f"{rel}: lint 失败 {exc!r}")
            continue
        result.page_results.append(page)

    result.page_count = len(result.page_results)

    # [HY-011] half_year_metadata_sanity — 跨页 HYM-005/006 检查。
    # 在单页 lint 完成后跑：需要全量页集合做 (symbol, period) 分组。结果会向
    # 受影响页追加 finding，并重算 readiness/error_count/warning_count/info_count。
    _apply_cross_page_hym_findings(result)

    _aggregate_lint_stats(result)
    _collect_lint_gaps(result)

    # index 对齐（只读）：investment 文件 stem 是否被 index.md 引用。
    index_set = _collect_index_set(index_path)
    if index_set:
        indexed_low = []
        for page in result.page_results:
            stem = Path(page.rel_path).stem
            if stem not in index_set:
                indexed_low.append(page.rel_path)
        result.index_missing_pages = sorted(indexed_low)

    result.top_fix_priorities = _build_top_fix_priorities(result)
    return result


def _apply_cross_page_hym_findings(result: KnowledgeLintResult) -> None:
    """[HY-011] 跑跨页 HYM-005/006，把 finding 追加到受影响页并重算计数。

    - 调用 ``check_half_year_metadata_cross_page`` 拿到 ``{rel_path: [finding]}``。
    - 把 finding 追加到对应 PageLintResult.findings。
    - 重算该页的 error_count / warning_count / info_count / machine_readiness。
    - 不会抛异常：跨页检查失败只记入 result.errors，不阻塞后续聚合。
    """
    try:
        cross = check_half_year_metadata_cross_page(result.page_results)
    except Exception as exc:  # pragma: no cover - 容错
        result.errors.append(f"cross-page HYM 检查失败: {exc!r}")
        return

    if not cross:
        return

    # 构建 rel_path → PageLintResult 索引（rel_path 在 investment 分区内唯一）。
    page_by_path = {p.rel_path: p for p in result.page_results}

    for rel_path, new_findings in cross.items():
        page = page_by_path.get(rel_path)
        if page is None:
            continue
        # 幂等：避免重复追加（理论上 lint_local_knowledge 只调一次，但保险起见）。
        existing_ids = {(f.rule_id, f.message) for f in page.findings}
        for f in new_findings:
            if (f.rule_id, f.message) not in existing_ids:
                page.findings.append(f)

        # 重算计数与 readiness。is_to_be_supplemented / is_stale 保持单页阶段
        # 的判断（跨页检查不改这两个状态）。
        page.error_count = sum(
            1 for f in page.findings if f.severity == SEVERITY_ERROR
        )
        page.warning_count = sum(
            1 for f in page.findings if f.severity == SEVERITY_WARNING
        )
        page.info_count = sum(
            1 for f in page.findings if f.severity == SEVERITY_INFO
        )
        page.machine_readiness = _compute_readiness(
            page.error_count,
            page.warning_count,
            page.is_to_be_supplemented,
            page.is_stale,
        )


def _aggregate_lint_stats(result: KnowledgeLintResult) -> None:
    readiness = {"high": 0, "medium": 0, "low": 0}
    by_sev = {SEVERITY_ERROR: 0, SEVERITY_WARNING: 0, SEVERITY_INFO: 0}
    by_rule: Dict[str, int] = {}
    for page in result.page_results:
        readiness[page.machine_readiness] = readiness.get(page.machine_readiness, 0) + 1
        for f in page.findings:
            by_sev[f.severity] = by_sev.get(f.severity, 0) + 1
            by_rule[f.rule_id] = by_rule.get(f.rule_id, 0) + 1
    result.readiness_counts = readiness
    result.findings_by_severity = by_sev
    result.findings_by_rule = by_rule


def _collect_lint_gaps(result: KnowledgeLintResult) -> None:
    for page in result.page_results:
        finding_rules = {f.rule_id for f in page.findings}
        if page.machine_readiness == "low":
            result.pages_low_readiness.append(page.rel_path)
        if not page.has_symbols:
            result.pages_missing_symbols.append(page.rel_path)
        # SEC-002 finding = 缺风险章节
        if "SEC-002" in finding_rules:
            result.pages_missing_risk.append(page.rel_path)
        # SEC-001 finding = 缺一句话总结/核心观点章节
        if "SEC-001" in finding_rules:
            result.pages_missing_summary.append(page.rel_path)
        # sources 缺失：frontmatter sources 空（FMR-001 sources）且 无原始资料章节（SEC-003）
        has_sources_field = not any(
            f.rule_id == "FMR-001" and f.field == "sources" for f in page.findings
        )
        has_sources_section = "SEC-003" not in finding_rules
        if not has_sources_field and not has_sources_section:
            result.pages_missing_sources.append(page.rel_path)
        if page.is_low_confidence:
            result.pages_low_confidence.append(page.rel_path)
        if page.is_stale:
            result.pages_stale.append(page.rel_path)
        if page.is_to_be_supplemented:
            result.pages_to_be_supplemented.append(page.rel_path)
        # [HY-001] half_year_contract — 财报页扩展聚合
        if page.is_half_year_report:
            result.pages_half_year.append(page.rel_path)
            if "HYF-001" in finding_rules:
                result.pages_half_year_period_missing.append(page.rel_path)
            if "HYF-005" in finding_rules:
                result.pages_half_year_facts_missing.append(page.rel_path)
            if page.half_year_opinion_only:
                result.pages_half_year_opinion_only.append(page.rel_path)
        # [KB-014] citation_policy — 来源可信度分层聚合
        tier = page.source_quality_tier or TIER_UNKNOWN
        result.tier_counts[tier] = result.tier_counts.get(tier, 0) + 1
        if "CIT-001" in finding_rules or "CIT-003" in finding_rules:
            result.pages_weak_source.append(page.rel_path)
        if "CIT-002" in finding_rules:
            result.pages_opinion_as_fact.append(page.rel_path)
        if tier in (TIER_BROKER_RESEARCH,) and "CIT-002" not in finding_rules:
            # broker_research 但未触发 CIT-002（非财报页）—— 仍标记为 broker_only，
            # 方便前端 / 报告展示"该页只引用了券商观点"。
            result.pages_broker_only.append(page.rel_path)
        # [HY-011] half_year_metadata_sanity — 元数据 sanity 聚合
        if "HYM-001" in finding_rules:
            result.pages_invalid_or_future_disclosure.append(page.rel_path)
        if "HYM-002" in finding_rules:
            result.pages_period_end_after_disclosure.append(page.rel_path)
        if "HYM-003" in finding_rules:
            result.pages_period_mismatch.append(page.rel_path)
        if "HYM-004" in finding_rules:
            result.pages_symbol_name_mismatch.append(page.rel_path)
        if "HYM-005" in finding_rules:
            result.pages_multi_version_conflict.append(page.rel_path)
        if "HYM-006" in finding_rules:
            result.pages_revision_detected.append(page.rel_path)
        if "HYM-007" in finding_rules:
            result.pages_period_end_mismatch.append(page.rel_path)


# ── 报告渲染 ─────────────────────────────────────────────────────────


def render_lint_report(result: KnowledgeLintResult) -> str:
    """渲染 Markdown lint 报告。只含文件名/字段/规则/修复建议，不含原文。"""
    lines: List[str] = []
    lines.append(
        f"# Tree Work investment wiki 契约 lint 报告 — {result.scanned_at.split()[0]}"
    )
    lines.append("")
    lines.append(
        "> [KB-002 / HY-001 / HY-011] local_knowledge_contract — 只读 lint，"
        "不修改知识库；低分页面只降低置信度，不阻塞 TA。契约见 "
        "``docs/local_knowledge_contract.md``。"
    )
    lines.append("")

    # 1. 概览
    lines.append("## 1. 概览")
    lines.append("")
    lines.append(f"- knowledge_root: `{result.knowledge_root}`")
    lines.append(f"- scanned_at: {result.scanned_at}")
    lines.append(f"- contract_version: `{result.contract_version}`")
    lines.append(f"- investment_md_pages: **{result.page_count}**")
    sev = result.findings_by_severity
    lines.append(
        f"- findings: **{sev.get(SEVERITY_ERROR, 0)}** error / "
        f"**{sev.get(SEVERITY_WARNING, 0)}** warning / "
        f"**{sev.get(SEVERITY_INFO, 0)}** info"
    )
    rd = result.readiness_counts
    total = sum(rd.values()) or 1
    lines.append(
        f"- machine_readiness: high **{rd.get('high', 0)}** "
        f"({rd.get('high', 0) / total:.0%}) / "
        f"medium **{rd.get('medium', 0)}** / low **{rd.get('low', 0)}**"
    )
    # [HY-001] half_year_contract — 财报页概览行
    if result.pages_half_year:
        lines.append(
            f"- half_year_pages: **{len(result.pages_half_year)}** "
            f"（缺报告期 {len(result.pages_half_year_period_missing)} / "
            f"缺事实 {len(result.pages_half_year_facts_missing)} / "
            f"观点冒充事实 {len(result.pages_half_year_opinion_only)}）"
        )
    # [KB-014] citation_policy — 来源可信度分层概览
    if result.tier_counts:
        tier_summary = " / ".join(
            f"{t} {result.tier_counts.get(t, 0)}"
            for t in (
                "original_filing",
                "official_notice",
                "broker_research",
                "media",
                "user_note",
                "unknown",
            )
            if result.tier_counts.get(t, 0) > 0
        )
        lines.append(
            f"- citation_tier: {tier_summary} "
            f"（弱来源 {len(result.pages_weak_source)} / "
            f"观点冒充事实 {len(result.pages_opinion_as_fact)}）"
        )
    # [HY-011] half_year_metadata_sanity — 元数据 sanity 概览
    total_hym_issues = (
        len(result.pages_invalid_or_future_disclosure)
        + len(result.pages_period_end_after_disclosure)
        + len(result.pages_period_mismatch)
        + len(result.pages_symbol_name_mismatch)
        + len(result.pages_multi_version_conflict)
        + len(result.pages_period_end_mismatch)
    )
    if total_hym_issues > 0 or result.pages_revision_detected:
        lines.append(
            f"- half_year_metadata_sanity: 冲突/错配 **{total_hym_issues}** "
            f"（非法/未来披露日 {len(result.pages_invalid_or_future_disclosure)} / "
            f"期末日晚于披露日 {len(result.pages_period_end_after_disclosure)} / "
            f"周期错配 {len(result.pages_period_mismatch)} / "
            f"symbol/name 错配 {len(result.pages_symbol_name_mismatch)} / "
            f"多版本冲突 {len(result.pages_multi_version_conflict)} / "
            f"period_end-period 不一致 {len(result.pages_period_end_mismatch)}），"
            f"修订稿 {len(result.pages_revision_detected)}（info，不阻塞）"
        )
    if result.errors:
        lines.append("")
        lines.append(f"- ⚠️ lint 错误 ({len(result.errors)}):")
        for err in result.errors[:10]:
            lines.append(f"  - {err}")
    lines.append("")

    # 2. readiness 分布
    lines.append("## 2. Machine Readiness 分布")
    lines.append("")
    lines.append("| readiness | 数量 | 占比 | 说明 |")
    lines.append("|-----------|------|------|------|")
    lines.append(
        f"| high | {rd.get('high', 0)} | {rd.get('high', 0) / total:.0%} | "
        "0 error / ≤2 warning / 非待补充 / 非过期，TA 可直接消费 |"
    )
    lines.append(
        f"| medium | {rd.get('medium', 0)} | {rd.get('medium', 0) / total:.0%} | "
        "≤2 error / ≤4 warning，TA 消费但需补字段 |"
    )
    lines.append(
        f"| low | {rd.get('low', 0)} | {rd.get('low', 0) / total:.0%} | "
        "待补充/低置信/缺口过多，TA 标 LOW_CONFIDENCE/STALE，不进入候选加分 |"
    )
    lines.append("")

    # 3. findings by rule
    lines.append("## 3. 规则命中分布")
    lines.append("")
    if result.findings_by_rule:
        lines.append("| rule_id | 命中次数 | 严重度 | 说明 |")
        lines.append("|---------|----------|--------|------|")
        for rule_id in sorted(result.findings_by_rule.keys()):
            count = result.findings_by_rule[rule_id]
            sev_for_rule, desc = _RULE_DESCRIPTIONS.get(rule_id, ("?", ""))
            lines.append(f"| `{rule_id}` | {count} | {sev_for_rule} | {desc} |")
    else:
        lines.append("_无规则命中（全部页面通过契约）_")
    lines.append("")

    # 4. 缺口页面清单
    lines.append("## 4. 缺口页面清单")
    lines.append("")
    _emit_gap_list(lines, "缺一句话总结/核心观点 (SEC-001)", result.pages_missing_summary)
    _emit_gap_list(lines, "缺风险提示 (SEC-002)", result.pages_missing_risk)
    _emit_gap_list(lines, "缺 sources/原始资料 (FMR-001/SEC-003)", result.pages_missing_sources)
    _emit_gap_list(lines, "缺 symbols 字段 (FMR-002/SYM-001)", result.pages_missing_symbols)
    _emit_gap_list(lines, "低置信 evidence_level=C (EVID-001)", result.pages_low_confidence)
    _emit_gap_list(lines, "过期/高 stale_risk (STALE-001/002)", result.pages_stale)
    _emit_gap_list(lines, "显式待补充 (TODO-001)", result.pages_to_be_supplemented)
    _emit_gap_list(lines, "low readiness（需回 Tree Work 补字段）", result.pages_low_readiness)
    _emit_gap_list(lines, "index.md 未引用", result.index_missing_pages)
    # [HY-001] half_year_contract — 财报页扩展缺口清单
    _emit_gap_list(
        lines,
        "财报/半年报页缺 financial_period (HYF-001)",
        result.pages_half_year_period_missing,
    )
    _emit_gap_list(
        lines,
        "财报/半年报页缺 financial_facts (HYF-005)",
        result.pages_half_year_facts_missing,
    )
    _emit_gap_list(
        lines,
        "财报/半年报页 source_type 全为券商/媒体观点 (HYF-007)",
        result.pages_half_year_opinion_only,
    )
    # [KB-014] citation_policy — 来源可信度分层缺口清单
    _emit_gap_list(
        lines,
        "弱来源页面 (CIT-001/CIT-003：缺来源或仅媒体/笔记/unknown)",
        result.pages_weak_source,
    )
    _emit_gap_list(
        lines,
        "财报页 tier 为券商/媒体/笔记 (CIT-002 / 观点冒充事实风险)",
        result.pages_opinion_as_fact,
    )
    # [HY-011] half_year_metadata_sanity — 元数据 sanity 缺口清单
    _emit_gap_list(
        lines,
        "财报页 disclosure_date 非法或是未来日期 (HYM-001)",
        result.pages_invalid_or_future_disclosure,
    )
    _emit_gap_list(
        lines,
        "财报页 period_end_date 晚于 disclosure_date (HYM-002 / error)",
        result.pages_period_end_after_disclosure,
    )
    _emit_gap_list(
        lines,
        "财报页 report_type=半年报/中报 但 period 非半年报周期 (HYM-003)",
        result.pages_period_mismatch,
    )
    _emit_gap_list(
        lines,
        "财报页 symbols CODE/NAME 错配 (HYM-004)",
        result.pages_symbol_name_mismatch,
    )
    _emit_gap_list(
        lines,
        "同 symbol+period 多版本无修订标记 (HYM-005 / 跨页冲突)",
        result.pages_multi_version_conflict,
    )
    _emit_gap_list(
        lines,
        "同 symbol+period 检测到修订稿 (HYM-006 / info)",
        result.pages_revision_detected,
    )
    _emit_gap_list(
        lines,
        "财报页 period_end_date 与 financial_period 不一致 (HYM-007)",
        result.pages_period_end_mismatch,
    )

    # 5. Top 修复优先级
    lines.append("## 5. Top 修复优先级（按影响面排序）")
    lines.append("")
    if result.top_fix_priorities:
        for idx, p in enumerate(result.top_fix_priorities, 1):
            lines.append(
                f"{idx}. **`{p['rule_id']}`** — 影响 {p['affected_pages']} 页 — "
                f"{p['fix_suggestion']}"
            )
            for s in p["samples"]:
                lines.append(f"   - `{s}`")
    else:
        lines.append("_无 error/warning，全部页面满足契约 ✓_")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "_本报告由 ``scripts/lint_local_knowledge.py`` 只读生成；"
        "对应模块 ``tradingagents.dataflows.local_knowledge_lint``；"
        "契约文档 ``docs/local_knowledge_contract.md``。_"
    )
    lines.append("")
    return "\n".join(lines)


def _emit_gap_list(lines: List[str], title: str, items: List[str]) -> None:
    if not items:
        return
    lines.append(f"### {title} ({len(items)})")
    lines.append("")
    for s in items[:20]:
        lines.append(f"- `{s}`")
    if len(items) > 20:
        lines.append(f"- ... 其余 {len(items) - 20} 项见 ``to_dict()`` 输出")
    lines.append("")


# 规则说明表（用于报告渲染）。
_RULE_DESCRIPTIONS: Dict[str, Tuple[str, str]] = {
    "FMR-001": (SEVERITY_ERROR, "必填 frontmatter 字段缺失/为空"),
    "FMR-002": (SEVERITY_WARNING, "推荐机器字段缺失"),
    "SEC-001": (SEVERITY_ERROR, "缺一句话总结/核心观点章节"),
    "SEC-002": (SEVERITY_ERROR, "缺风险提示章节"),
    "SEC-003": (SEVERITY_WARNING, "缺原始资料/关联研报章节"),
    "SEC-004": (SEVERITY_WARNING, "缺投资逻辑/核心观点章节"),
    "SYM-001": (SEVERITY_ERROR, "公司/评分表页缺 symbols 字段"),
    "TBL-001": (SEVERITY_ERROR, "评分表缺标准表头列"),
    "TODO-001": (SEVERITY_INFO, "显式标记待补充/低置信（符合契约）"),
    "STALE-001": (SEVERITY_WARNING, "stale_risk=高"),
    "STALE-002": (SEVERITY_WARNING, "valid_until 已过期"),
    "EVID-001": (SEVERITY_INFO, "evidence_level=C 低置信"),
    # [HY-001] half_year_contract — 财报页扩展规则
    "HYF-001": (SEVERITY_ERROR, "财报/半年报页缺合法 financial_period"),
    "HYF-002": (SEVERITY_ERROR, "财报/半年报页缺 symbols"),
    "HYF-003": (SEVERITY_WARNING, "财报/半年报页缺合法 disclosure_date"),
    "HYF-004": (SEVERITY_WARNING, "财报/半年报页缺 source_type"),
    "HYF-005": (SEVERITY_WARNING, "财报/半年报页缺 financial_facts"),
    "HYF-006": (SEVERITY_WARNING, "财报/半年报页缺 risk_factors"),
    "HYF-007": (SEVERITY_WARNING, "source_type 全为券商/媒体观点（观点冒充事实）"),
    # [KB-014] citation_policy — 来源可信度分层规则
    "CIT-001": (SEVERITY_WARNING, "页面缺来源字段（unknown tier）"),
    "CIT-002": (SEVERITY_INFO, "财报页 tier 为券商/媒体/笔记（观点冒充事实风险）"),
    "CIT-003": (SEVERITY_INFO, "tier 为 media/user_note/unknown（弱来源）"),
    # [HY-011] half_year_metadata_sanity — 半年报元数据 sanity 规则
    "HYM-001": (SEVERITY_WARNING, "disclosure_date 非法或是未来日期"),
    "HYM-002": (SEVERITY_ERROR, "period_end_date 晚于 disclosure_date"),
    "HYM-003": (SEVERITY_WARNING, "report_type=半年报/中报 但 period 非半年报周期"),
    "HYM-004": (SEVERITY_WARNING, "symbols CODE/NAME 格式或一致性错配"),
    "HYM-005": (SEVERITY_WARNING, "同 symbol+period 多版本无修订标记"),
    "HYM-006": (SEVERITY_INFO, "同 symbol+period 检测到修订稿"),
    "HYM-007": (SEVERITY_WARNING, "period_end_date 与 financial_period 不一致"),
}


# ── CLI 便利 ─────────────────────────────────────────────────────────


def default_knowledge_root() -> str:
    """返回默认知识库根目录（与 KB-001 一致的环境变量优先）。"""
    env = (
        os.environ.get("AUTO_DEV_KNOWLEDGE_ROOT")
        or os.environ.get("KNOWLEDGE_ROOT")
    )
    if env:
        return env
    return str(Path.home() / "Documents" / "knowledge")


def suggest_lint_output_path(docs_dir: str = "docs/knowledge_reports") -> str:
    """生成默认输出路径 ``docs/knowledge_reports/local_knowledge_lint-YYYY-MM-DD.md``。"""
    today = date.today().strftime("%Y-%m-%d")
    return os.path.join(docs_dir, f"local_knowledge_lint-{today}.md")
