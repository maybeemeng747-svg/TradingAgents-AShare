# [KB-002] local_knowledge_contract
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

    error_count = sum(1 for f in findings if f.severity == SEVERITY_ERROR)
    warning_count = sum(1 for f in findings if f.severity == SEVERITY_WARNING)
    info_count = sum(1 for f in findings if f.severity == SEVERITY_INFO)

    readiness = _compute_readiness(error_count, warning_count, is_todo, is_stale)

    return PageLintResult(
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
    )


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


# ── 报告渲染 ─────────────────────────────────────────────────────────


def render_lint_report(result: KnowledgeLintResult) -> str:
    """渲染 Markdown lint 报告。只含文件名/字段/规则/修复建议，不含原文。"""
    lines: List[str] = []
    lines.append(
        f"# Tree Work investment wiki 契约 lint 报告 — {result.scanned_at.split()[0]}"
    )
    lines.append("")
    lines.append(
        "> [KB-002] local_knowledge_contract — 只读 lint，不修改知识库；"
        "低分页面只降低置信度，不阻塞 TA。契约见 ``docs/local_knowledge_contract.md``。"
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
