# [KB-001] local_knowledge_audit
"""Tree Work 本地知识库只读索引与健康审计。

把 ``~/Documents/knowledge/`` 作为 TA 的本地投研知识源做**只读**审计，确认
Tree Work 产物是否能被机器稳定检索和追溯，输出可读的 Markdown 审计报告。

功能：
  1. :func:`audit_local_knowledge`: 扫描 ``wiki/investment``、``inbox``、
     ``raw``、``wiki/index.md``、``wiki/log.md`` 的覆盖情况，统计页面数、
     frontmatter 覆盖、章节缺失、index 缺失、inbox 未消化、待补充页等。
  2. :func:`classify_page_type`: 识别 investment 页面类型
     (company / industry / score_table / summary / to_be_supplemented / unclassified)。
  3. :func:`render_audit_report`: 渲染 Markdown 报告，只含文件名/字段/统计/缺口，
     不含大段原文。

设计约束（对应任务 KB-001）：
  - **只读**：仅用 ``open(..., "r", encoding="utf-8")`` 与 ``os.walk`` /
    ``Path.iterdir``，绝不向知识库写文件。
  - **不输出长篇原文**：只读取 frontmatter 与 ``^## `` 章节标题，不读取段落正文。
  - **不调用 LLM / 不访问外网**：纯标准库 + 可选 PyYAML frontmatter 解析。

使用示例::

    from tradingagents.dataflows.local_knowledge_audit import (
        audit_local_knowledge,
        render_audit_report,
    )
    result = audit_local_knowledge("/Users/maybee/Documents/knowledge")
    print(render_audit_report(result))
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 可选依赖：PyYAML 解析 frontmatter；不可用时回退到内置的极简解析器。
try:  # pragma: no cover - 环境探测
    import yaml as _yaml  # type: ignore

    _HAS_YAML = True
except Exception:  # pragma: no cover
    _yaml = None  # type: ignore
    _HAS_YAML = False


# ── 常量 ──────────────────────────────────────────────────────────────

# 任务约束：investment 分区固定在 wiki/investment/。
INVESTMENT_SUBDIR = Path("wiki") / "investment"
INDEX_MD = Path("wiki") / "index.md"
LOG_MD = Path("wiki") / "log.md"
INBOX_SUBDIR = Path("inbox")
RAW_SUBDIR = Path("raw")

# 期望的 machine-readable frontmatter 字段（KB-002 契约草案）。
REQUIRED_FRONTMATTER_FIELDS: Tuple[str, ...] = ("title", "created", "updated", "sources", "tags")
RECOMMENDED_MACHINE_FIELDS: Tuple[str, ...] = (
    "symbols",
    "themes",
    "industry_chain_roles",
    "report_type",
    "evidence_level",
    "valid_until",
    "source_quality",
    "stale_risk",
)

# 段落级章节期待：一句话总结 / 核心观点 / 投资逻辑 / 核心数据 / 风险提示 / 标的 / 原始资料。
SUMMARY_SECTION_PATTERNS: Tuple[str, ...] = (
    "一句话总结",
    "核心观点",
    "核心结论",
    "投资逻辑",
    "核心数据",
)
RISK_SECTION_PATTERNS: Tuple[str, ...] = (
    "风险提示",
    "风险",
    "风险与不确定性",
)
SOURCES_SECTION_PATTERNS: Tuple[str, ...] = (
    "原始资料",
    "关联研报",
    "资料来源",
    "参考资料",
)

# 待补充/低置信标记。
TODO_MARKERS: Tuple[str, ...] = (
    "此页面内容不完整",
    "待补充",
    "内容待补充",
    "占位",
)

# 页面类型关键词。
_SCORE_TABLE_KEYWORDS = ("评分表", "评分体系")
_SUMMARY_TABLE_KEYWORDS = ("汇总表", "汇总", "速查表", "核心看点汇总")
_INDUSTRY_REPORT_TYPES = ("行业", "周报", "策略", "深度", "综述", "产业链")
_COMPANY_REPORT_TYPES = ("公司点评", "公司分析", "财报分析", "公司深度")
_STOCK_CODE_RE = re.compile(r"\b(6|0|3|9|8)\d{5}\b")
_STOCK_CODE_FILE_RE = re.compile(r"(\d{6})")
_FRONTMATTER_DELIM_RE = re.compile(r"^---\s*$", re.MULTILINE)
_INDEX_LINK_RE = re.compile(r"\[\[investment[/.]([^\]\|]+?)(?:\|[^\]]*)?\]\]")


# ── 数据类 ────────────────────────────────────────────────────────────


@dataclass
class WikiPageAudit:
    """单篇 investment wiki 的只读审计结果。"""

    rel_path: str
    title: Optional[str]
    page_type: str
    frontmatter_present: bool
    frontmatter_fields: List[str] = field(default_factory=list)
    section_headers: List[str] = field(default_factory=list)
    has_summary_section: bool = False
    has_risk_section: bool = False
    has_sources_section: bool = False
    has_sources_field: bool = False
    has_symbols_field: bool = False
    has_themes_field: bool = False
    missing_recommended_fields: List[str] = field(default_factory=list)
    machine_readiness: str = "low"
    is_to_be_supplemented: bool = False
    stale_risk_high: bool = False
    valid_until_expired: bool = False
    valid_until: Optional[str] = None
    updated_at: Optional[str] = None
    size_bytes: int = 0
    line_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rel_path": self.rel_path,
            "title": self.title,
            "page_type": self.page_type,
            "frontmatter_present": self.frontmatter_present,
            "frontmatter_fields": list(self.frontmatter_fields),
            "section_headers": list(self.section_headers),
            "has_summary_section": self.has_summary_section,
            "has_risk_section": self.has_risk_section,
            "has_sources_section": self.has_sources_section,
            "has_sources_field": self.has_sources_field,
            "has_symbols_field": self.has_symbols_field,
            "has_themes_field": self.has_themes_field,
            "missing_recommended_fields": list(self.missing_recommended_fields),
            "machine_readiness": self.machine_readiness,
            "is_to_be_supplemented": self.is_to_be_supplemented,
            "stale_risk_high": self.stale_risk_high,
            "valid_until_expired": self.valid_until_expired,
            "valid_until": self.valid_until,
            "updated_at": self.updated_at,
            "size_bytes": self.size_bytes,
            "line_count": self.line_count,
        }


@dataclass
class LocalKnowledgeAuditResult:
    """整库只读审计结果聚合。"""

    knowledge_root: str
    scanned_at: str
    investment_page_count: int = 0
    inbox_item_count: int = 0
    raw_md_count: int = 0
    index_present: bool = False
    index_investment_entries: int = 0
    log_present: bool = False
    log_line_count: int = 0
    page_audits: List[WikiPageAudit] = field(default_factory=list)
    type_counts: Dict[str, int] = field(default_factory=dict)
    frontmatter_field_coverage: Dict[str, int] = field(default_factory=dict)
    machine_readiness_counts: Dict[str, int] = field(default_factory=dict)
    pages_missing_summary: List[str] = field(default_factory=list)
    pages_missing_risk: List[str] = field(default_factory=list)
    pages_missing_sources: List[str] = field(default_factory=list)
    pages_missing_symbols: List[str] = field(default_factory=list)
    index_missing_pages: List[str] = field(default_factory=list)
    index_orphan_entries: List[str] = field(default_factory=list)
    inbox_unprocessed: List[str] = field(default_factory=list)
    to_be_supplemented_pages: List[str] = field(default_factory=list)
    stale_or_high_risk_pages: List[str] = field(default_factory=list)
    structural_gaps: List[Dict[str, Any]] = field(default_factory=list)
    integration_suggestions: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "knowledge_root": self.knowledge_root,
            "scanned_at": self.scanned_at,
            "investment_page_count": self.investment_page_count,
            "inbox_item_count": self.inbox_item_count,
            "raw_md_count": self.raw_md_count,
            "index_present": self.index_present,
            "index_investment_entries": self.index_investment_entries,
            "log_present": self.log_present,
            "log_line_count": self.log_line_count,
            "page_audits": [p.to_dict() for p in self.page_audits],
            "type_counts": dict(self.type_counts),
            "frontmatter_field_coverage": dict(self.frontmatter_field_coverage),
            "machine_readiness_counts": dict(self.machine_readiness_counts),
            "pages_missing_summary": list(self.pages_missing_summary),
            "pages_missing_risk": list(self.pages_missing_risk),
            "pages_missing_sources": list(self.pages_missing_sources),
            "pages_missing_symbols": list(self.pages_missing_symbols),
            "index_missing_pages": list(self.index_missing_pages),
            "index_orphan_entries": list(self.index_orphan_entries),
            "inbox_unprocessed": list(self.inbox_unprocessed),
            "to_be_supplemented_pages": list(self.to_be_supplemented_pages),
            "stale_or_high_risk_pages": list(self.stale_or_high_risk_pages),
            "structural_gaps": list(self.structural_gaps),
            "integration_suggestions": list(self.integration_suggestions),
            "errors": list(self.errors),
        }


# ── 解析辅助 ──────────────────────────────────────────────────────────


def _split_frontmatter(text: str) -> Tuple[Optional[str], str]:
    """把开头的 ``---\\n...\\n---`` frontmatter 与正文分离。

    返回 ``(frontmatter_text_or_None, body_text)``。只匹配文件起始的 frontmatter，
    避免误把正文中出现的 ``---``（如表格分隔符）当作边界。
    """
    if not text.startswith("---"):
        return None, text
    # 跳过第一个 ---，寻找下一个独占一行的 ---
    lines = text.splitlines()
    # lines[0] 应为 "---"
    if not lines or lines[0].strip() != "---":
        return None, text
    end_idx = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end_idx = idx
            break
    if end_idx is None:
        return None, text
    fm_text = "\n".join(lines[1:end_idx])
    body = "\n".join(lines[end_idx + 1 :])
    return fm_text, body


def _parse_frontmatter(fm_text: Optional[str]) -> Dict[str, Any]:
    """解析 frontmatter 文本为 dict。缺失或失败时返回空 dict。"""
    if not fm_text:
        return {}
    if _HAS_YAML:
        try:
            loaded = _yaml.safe_load(fm_text)
            if isinstance(loaded, dict):
                return loaded
            return {}
        except Exception:
            # 回退到极简解析器；不能因为 yaml 报错就放弃整篇审计。
            pass
    return _parse_frontmatter_minimal(fm_text)


def _parse_frontmatter_minimal(fm_text: str) -> Dict[str, Any]:
    """极简 YAML 解析器：只处理 ``key: value`` 与 ``key: [a, b]`` / 多行列表。

    适用于本项目 frontmatter 形态；非通用 YAML 解析器。
    """
    result: Dict[str, Any] = {}
    current_key: Optional[str] = None
    for raw_line in fm_text.splitlines():
        if not raw_line.strip():
            continue
        line = raw_line.rstrip()
        # 多行列表项：  - xxx
        stripped = line.strip()
        if stripped.startswith("- ") and current_key is not None:
            val = stripped[2:].strip()
            existing = result.get(current_key)
            if isinstance(existing, list):
                existing.append(val)
            else:
                result[current_key] = [val]
            continue
        # key: value
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if not key:
                continue
            if value == "":
                # 可能是多行列表的开头
                result[key] = []
                current_key = key
            elif value.startswith("[") and value.endswith("]"):
                inner = value[1:-1].strip()
                if inner:
                    items = _split_list_items(inner)
                    result[key] = items
                else:
                    result[key] = []
                current_key = key
            else:
                result[key] = value.strip('"').strip("'")
                current_key = key
    return result


def _split_list_items(inner: str) -> List[str]:
    """切分 ``[a, b, "c, d"]`` 形式的列表；保留引号内逗号。"""
    items: List[str] = []
    buf: List[str] = []
    in_quote: Optional[str] = None
    for ch in inner:
        if ch in ('"', "'"):
            if in_quote == ch:
                in_quote = None
            elif in_quote is None:
                in_quote = ch
            buf.append(ch)
            continue
        if ch == "," and in_quote is None:
            items.append("".join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    if buf:
        items.append("".join(buf).strip())
    return [it for it in items if it]


def _read_text_safe(path: Path, limit_bytes: int = 200_000) -> str:
    """只读、容错地读取文本；超大文件截断。"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            data = fh.read(limit_bytes)
        return data
    except Exception:
        return ""


def _extract_section_headers(body: str) -> List[str]:
    """只提取 ``^## `` 与 ``^### `` 标题行，不读取段落正文。"""
    headers: List[str] = []
    for line in body.splitlines():
        if line.startswith("## ") or line.startswith("### "):
            headers.append(line.lstrip("#").strip())
    return headers


def _any_match(headers: List[str], patterns: Tuple[str, ...]) -> bool:
    for h in headers:
        for p in patterns:
            if p in h:
                return True
    return False


def _is_nonempty_field(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (list, tuple, dict)):
        return len(value) > 0
    if isinstance(value, str):
        return value.strip() not in ("", "[]", "{}", "无", "暂无")
    return True


def _safe_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        return None
    text = str(value).strip().strip('"').strip("'")
    return text or None


def _is_valid_until_expired(valid_until: Optional[str], today: Optional[date] = None) -> bool:
    """判断 ``valid_until`` 是否早于今天；无法解析（如 ``长期``）视为未过期。"""
    if not valid_until:
        return False
    today = today or date.today()
    text = valid_until.strip()
    # 接受 "2026-08-31" / "2026/08/31" / "20260831"；拒绝 "长期"/"无"/"未定"。
    cleaned = re.sub(r"[/-]", "", text)
    if not re.fullmatch(r"\d{8}", cleaned):
        return False
    try:
        vu_date = date(int(cleaned[:4]), int(cleaned[4:6]), int(cleaned[6:8]))
    except ValueError:
        return False
    return vu_date < today


# ── 页面类型识别 ─────────────────────────────────────────────────────


def classify_page_type(
    filename: str,
    title: Optional[str],
    frontmatter: Dict[str, Any],
    section_headers: List[str],
    body_hint_has_todo: bool,
) -> str:
    """识别 investment 页面类型。

    返回 ``score_table`` / ``summary`` / ``to_be_supplemented`` /
    ``company`` / ``industry`` / ``unclassified`` 之一。
    """
    name = filename
    title_text = (title or "").lower()
    name_text = name.lower()

    # 待补充优先（KB-002 显式要求）。
    if body_hint_has_todo or "待补充" in name or "待补充" in (title or ""):
        return "to_be_supplemented"

    # 评分表：标题/文件名命中关键词，或正文有 "标的列表" + 利好度/共识度。
    if any(k in name or k in (title or "") for k in _SCORE_TABLE_KEYWORDS):
        return "score_table"
    joined_headers = " | ".join(section_headers)
    if "标的列表" in joined_headers and ("利好度" in joined_headers or "共识度" in joined_headers):
        return "score_table"

    # 汇总表/速查表。
    if any(k in name or k in (title or "") for k in _SUMMARY_TABLE_KEYWORDS):
        return "summary"

    report_type = _safe_str(frontmatter.get("report_type")) or ""
    symbols = frontmatter.get("symbols")
    has_company_symbol = _is_nonempty_field(symbols) or bool(_STOCK_CODE_FILE_RE.search(name))

    # 公司页：报告类型为公司类 或 文件名带 6 位股票代码 + 单标的。
    if report_type in _COMPANY_REPORT_TYPES or any(k in report_type for k in ("公司", "财报")):
        return "company"
    if has_company_symbol and report_type and report_type not in _INDUSTRY_REPORT_TYPES:
        return "company"

    # 行业页：报告类型为行业/周报/策略/深度/综述/产业链。
    if report_type in _INDUSTRY_REPORT_TYPES or any(
        k in report_type for k in ("行业", "周报", "策略", "深度", "综述", "产业链")
    ):
        return "industry"
    if any(k in name or k in (title or "") for k in ("产业链", "行业", "周报", "策略", "综述")):
        return "industry"

    # 文件名带股票代码但无明确类型线索，倾向公司页。
    if _STOCK_CODE_FILE_RE.search(name):
        return "company"

    # title/name 含 "评分"/"速查" 兜底。
    if "评分" in name or "评分" in (title or ""):
        return "score_table"

    _ = (name_text, title_text)  # 保留以便后续扩展，避免未用告警
    return "unclassified"


def _compute_machine_readiness(page: WikiPageAudit) -> str:
    """根据缺失字段与章节计算 machine_readiness: high/medium/low。"""
    if page.is_to_be_supplemented:
        return "low"
    score = 0
    total = 0
    checks = [
        page.has_summary_section,
        page.has_risk_section,
        page.has_sources_field,
        page.has_symbols_field,
        page.has_themes_field,
    ]
    for c in checks:
        total += 1
        if c:
            score += 1
    # 缺推荐字段越少，readiness 越高。
    missing_penalty = len(page.missing_recommended_fields)
    ratio = score / total if total else 0.0
    if ratio >= 0.8 and missing_penalty <= 2:
        return "high"
    if ratio >= 0.4 and missing_penalty <= 5:
        return "medium"
    return "low"


# ── 主审计逻辑 ───────────────────────────────────────────────────────


def _audit_single_page(rel_path: str, abs_path: Path) -> WikiPageAudit:
    """审计单个 investment markdown 页面（只读）。"""
    text = _read_text_safe(abs_path)
    fm_text, body = _split_frontmatter(text)
    frontmatter = _parse_frontmatter(fm_text)
    section_headers = _extract_section_headers(body)
    body_has_todo = any(marker in body for marker in TODO_MARKERS)

    fm_fields: List[str] = list(frontmatter.keys()) if frontmatter else []

    title = _safe_str(frontmatter.get("title")) or abs_path.stem
    page_type = classify_page_type(
        abs_path.name,
        title,
        frontmatter,
        section_headers,
        body_has_todo,
    )

    has_summary = _any_match(section_headers, SUMMARY_SECTION_PATTERNS)
    has_risk = _any_match(section_headers, RISK_SECTION_PATTERNS)
    has_sources_section = _any_match(section_headers, SOURCES_SECTION_PATTERNS)

    has_sources_field = _is_nonempty_field(frontmatter.get("sources"))
    has_symbols_field = _is_nonempty_field(frontmatter.get("symbols"))
    has_themes_field = _is_nonempty_field(frontmatter.get("themes"))

    missing_rec = [
        f for f in RECOMMENDED_MACHINE_FIELDS if not _is_nonempty_field(frontmatter.get(f))
    ]

    stale_val = _safe_str(frontmatter.get("stale_risk")) or ""
    stale_high = stale_val in ("高", "high", "HIGH", "高（需更新）")

    valid_until = _safe_str(frontmatter.get("valid_until"))
    updated_at = _safe_str(frontmatter.get("updated"))

    page = WikiPageAudit(
        rel_path=rel_path,
        title=title,
        page_type=page_type,
        frontmatter_present=fm_text is not None,
        frontmatter_fields=fm_fields,
        section_headers=section_headers,
        has_summary_section=has_summary,
        has_risk_section=has_risk,
        has_sources_section=has_sources_section,
        has_sources_field=has_sources_field,
        has_symbols_field=has_symbols_field,
        has_themes_field=has_themes_field,
        missing_recommended_fields=missing_rec,
        is_to_be_supplemented=body_has_todo or page_type == "to_be_supplemented",
        stale_risk_high=stale_high,
        valid_until_expired=_is_valid_until_expired(valid_until),
        valid_until=valid_until,
        updated_at=updated_at,
        size_bytes=abs_path.stat().st_size if abs_path.exists() else 0,
        line_count=text.count("\n") + (1 if text and not text.endswith("\n") else 0),
    )
    page.machine_readiness = _compute_machine_readiness(page)
    return page


def _iter_markdown_files(root: Path) -> List[Path]:
    if not root.exists() or not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_file() and p.suffix.lower() == ".md")


def _list_inbox_items(inbox_dir: Path) -> List[str]:
    """列出 inbox/ 下所有文件（md + 图片 + 其他），用于未消化清单。"""
    if not inbox_dir.exists() or not inbox_dir.is_dir():
        return []
    items: List[str] = []
    for path in sorted(inbox_dir.rglob("*")):
        if path.is_file():
            items.append(str(path.relative_to(inbox_dir)))
    return items


def _parse_index_entries(index_path: Path) -> Tuple[List[str], List[str]]:
    """解析 wiki/index.md，返回 investment 引用条目名列表（去 .md 后缀）。

    返回 ``(investment_entries, all_investment_link_targets)``，本版两者一致。
    """
    if not index_path.exists():
        return [], []
    text = _read_text_safe(index_path)
    entries: List[str] = []
    for m in _INDEX_LINK_RE.finditer(text):
        target = m.group(1).strip()
        if target:
            entries.append(target)
    return entries, entries


def audit_local_knowledge(knowledge_root: str) -> LocalKnowledgeAuditResult:
    """只读扫描 Tree Work 本地知识库，产出审计结果。

    参数:
        knowledge_root: 知识库根目录绝对路径（如 ``~/Documents/knowledge``）。

    返回:
        :class:`LocalKnowledgeAuditResult`。永远不会抛文件不存在异常：
        缺失分区会记入 ``errors`` 并保持计数为 0，方便在 fixture / CI 下复用。
    """
    root = Path(knowledge_root).expanduser()
    result = LocalKnowledgeAuditResult(
        knowledge_root=str(root),
        scanned_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )

    if not root.exists():
        result.errors.append(f"knowledge_root 不存在: {root}")
        _build_structural_gaps_and_suggestions(result)
        return result

    investment_dir = root / INVESTMENT_SUBDIR
    inbox_dir = root / INBOX_SUBDIR
    raw_dir = root / RAW_SUBDIR
    index_path = root / INDEX_MD
    log_path = root / LOG_MD

    # ── investment 分区 ──
    md_files = _iter_markdown_files(investment_dir)
    if not md_files and not investment_dir.exists():
        result.errors.append(f"investment 分区不存在: {investment_dir}")

    for md in md_files:
        rel = str(md.relative_to(root))
        try:
            page = _audit_single_page(rel, md)
        except Exception as exc:  # pragma: no cover - 容错：单页失败不影响整库
            result.errors.append(f"{rel}: 审计失败 {exc!r}")
            continue
        result.page_audits.append(page)

    result.investment_page_count = len(result.page_audits)

    # ── inbox / raw 计数 ──
    inbox_items = _list_inbox_items(inbox_dir)
    result.inbox_item_count = len(inbox_items)
    result.inbox_unprocessed = inbox_items

    raw_mds = _iter_markdown_files(raw_dir)
    result.raw_md_count = len(raw_mds)

    # ── index.md ──
    result.index_present = index_path.exists()
    index_entries: List[str] = []
    if result.index_present:
        index_entries, _ = _parse_index_entries(index_path)
    result.index_investment_entries = len(index_entries)

    # ── log.md ──
    result.log_present = log_path.exists()
    if result.log_present:
        try:
            result.log_line_count = sum(1 for _ in open(log_path, "r", encoding="utf-8", errors="replace"))
        except Exception:
            result.log_line_count = 0

    # ── 类型/字段/readiness 聚合 ──
    _aggregate_page_stats(result)

    # ── 缺口统计 ──
    _collect_gaps(result)

    # ── index 对齐 ──
    _collect_index_misalignment(result, index_entries)

    # ── 结构缺口与接入建议 ──
    _build_structural_gaps_and_suggestions(result)

    return result


def _aggregate_page_stats(result: LocalKnowledgeAuditResult) -> None:
    type_counts: Dict[str, int] = {}
    fm_coverage: Dict[str, int] = {}
    readiness: Dict[str, int] = {"high": 0, "medium": 0, "low": 0}
    for page in result.page_audits:
        type_counts[page.page_type] = type_counts.get(page.page_type, 0) + 1
        readiness[page.machine_readiness] = readiness.get(page.machine_readiness, 0) + 1
        for f in page.frontmatter_fields:
            fm_coverage[f] = fm_coverage.get(f, 0) + 1
    result.type_counts = type_counts
    result.frontmatter_field_coverage = fm_coverage
    result.machine_readiness_counts = readiness


def _collect_gaps(result: LocalKnowledgeAuditResult) -> None:
    for page in result.page_audits:
        if not page.has_summary_section:
            result.pages_missing_summary.append(page.rel_path)
        if not page.has_risk_section:
            result.pages_missing_risk.append(page.rel_path)
        if not page.has_sources_field and not page.has_sources_section:
            result.pages_missing_sources.append(page.rel_path)
        if not page.has_symbols_field:
            result.pages_missing_symbols.append(page.rel_path)
        if page.is_to_be_supplemented:
            result.to_be_supplemented_pages.append(page.rel_path)
        if page.stale_risk_high or page.valid_until_expired:
            result.stale_or_high_risk_pages.append(page.rel_path)


def _collect_index_misalignment(result: LocalKnowledgeAuditResult, index_entries: List[str]) -> None:
    """对比 index.md 与 investment 文件，找出缺失引用与孤儿条目。

    ``index_missing_pages`` 返回完整 rel_path（与其它 gap 列表保持一致），
    ``index_orphan_entries`` 返回 index 引用名（无对应文件）。
    """
    if not index_entries:
        return
    # investment 文件名（去 .md 后缀）→ 完整 rel_path 映射
    stem_to_rel: Dict[str, str] = {}
    for page in result.page_audits:
        stem = Path(page.rel_path).stem
        # 同名 stem 取字典序最小的 rel_path，保证稳定。
        if stem not in stem_to_rel or page.rel_path < stem_to_rel[stem]:
            stem_to_rel[stem] = page.rel_path
    file_stems = set(stem_to_rel.keys())

    index_set = set()
    for entry in index_entries:
        # entry 可能是 "investment/foo" 或 "foo" 或 "foo.md"
        name = entry.split("/")[-1]
        if name.endswith(".md"):
            name = name[:-3]
        index_set.add(name)

    missing_stems = sorted(file_stems - index_set)
    result.index_missing_pages = [stem_to_rel[s] for s in missing_stems]
    result.index_orphan_entries = sorted(index_set - file_stems)


def _build_structural_gaps_and_suggestions(result: LocalKnowledgeAuditResult) -> None:
    """归纳 >=5 类结构缺口与对应接入建议（不修改知识库）。"""
    gaps: List[Dict[str, Any]] = []

    gaps.append(
        {
            "gap": "缺一句话总结/核心观点章节",
            "count": len(result.pages_missing_summary),
            "samples": result.pages_missing_summary[:5],
            "suggestion": "为每篇缺失摘要的 wiki 补 ``## 一句话总结``，TA 仅取该段作为背景摘要。",
        }
    )
    gaps.append(
        {
            "gap": "缺风险提示章节",
            "count": len(result.pages_missing_risk),
            "samples": result.pages_missing_risk[:5],
            "suggestion": "补 ``## 风险提示``，TA 在 raw_evidence 中标记 risk 段，避免只看利好。",
        }
    )
    gaps.append(
        {
            "gap": "缺 sources/原始资料字段或章节",
            "count": len(result.pages_missing_sources),
            "samples": result.pages_missing_sources[:5],
            "suggestion": "frontmatter ``sources`` 必填且非空，或在正文补 ``## 原始资料``；TA 据此溯源。",
        }
    )
    gaps.append(
        {
            "gap": "缺 machine symbols 字段",
            "count": len(result.pages_missing_symbols),
            "samples": result.pages_missing_symbols[:5],
            "suggestion": "为每篇补 ``symbols: [\"603296.SH 华勤技术\"]``，TA 按 symbol 命中 wiki。",
        }
    )
    gaps.append(
        {
            "gap": "待补充/低置信页面",
            "count": len(result.to_be_supplemented_pages),
            "samples": result.to_be_supplemented_pages[:5],
            "suggestion": "TA 接入时对这些页降级为 LOW_CONFIDENCE，不进入候选加分。",
        }
    )
    gaps.append(
        {
            "gap": "stale_risk=高 或 valid_until 已过期页面",
            "count": len(result.stale_or_high_risk_pages),
            "samples": result.stale_or_high_risk_pages[:5],
            "suggestion": "TA 命中后标记 STALE 并提示 ``needs_tree_work_research``，不直接用于实时结论。",
        }
    )
    gaps.append(
        {
            "gap": "index.md 未引用的 investment 页",
            "count": len(result.index_missing_pages),
            "samples": result.index_missing_pages[:5],
            "suggestion": "Tree Work 在 index.md 补 ``[[investment/<name>]]`` 引用，保证可发现性。",
        }
    )
    gaps.append(
        {
            "gap": "inbox 未消化材料",
            "count": result.inbox_item_count,
            "samples": result.inbox_unprocessed[:5],
            "suggestion": "Tree Work 优先消化 inbox；TA 只读 wiki/investment，不直接读 inbox。",
        }
    )

    result.structural_gaps = gaps

    suggestions: List[str] = [
        "TA 仅消费 ``wiki/investment`` 已消化页面，禁止把 raw/inbox 当结论源。",
        "对接字段建议（KB-002 契约草案）: symbols / themes / industry_chain_roles / "
        "report_type / evidence_level / valid_until / source_quality / stale_risk。",
        "命中 evidence_level=C 或 stale_risk=高 的页面，TA 标 LOW_CONFIDENCE/STALE，不提升候选层级。",
        "评分表类型必须保留表头: 公司|代码|核心业务|板块|利好度|共识度|预计启动|期待周期。",
        "无 symbols/themes 的页面不进入 KB-003 symbol 命中索引，避免误召回。",
        "machine_readiness=low 的页面优先回 Tree Work 补字段，TA 侧只列缺口不阻塞主链路。",
    ]
    result.integration_suggestions = suggestions


# ── 报告渲染 ─────────────────────────────────────────────────────────


def render_audit_report(result: LocalKnowledgeAuditResult) -> str:
    """渲染 Markdown 审计报告。只含文件名/字段/统计/缺口，不含原文段落。"""
    lines: List[str] = []
    lines.append(f"# Tree Work 本地知识库只读审计报告 — {result.scanned_at.split()[0]}")
    lines.append("")
    lines.append("> [KB-001] local_knowledge_audit — 只读扫描，不修改知识库；不含原文段落与敏感信息。")
    lines.append("")

    lines.append("## 1. 扫描概览")
    lines.append("")
    lines.append(f"- knowledge_root: `{result.knowledge_root}`")
    lines.append(f"- scanned_at: {result.scanned_at}")
    lines.append(f"- investment_md_pages: **{result.investment_page_count}**")
    lines.append(f"- inbox_items: **{result.inbox_item_count}**")
    lines.append(f"- raw_md_files: **{result.raw_md_count}**")
    lines.append(f"- wiki/index.md: {'存在' if result.index_present else '缺失'} "
                 f"(investment 引用 {result.index_investment_entries} 条)")
    lines.append(f"- wiki/log.md: {'存在' if result.log_present else '缺失'} "
                 f"({result.log_line_count} 行)")
    if result.errors:
        lines.append("")
        lines.append(f"- ⚠️ 扫描错误 ({len(result.errors)}):")
        for err in result.errors[:10]:
            lines.append(f"  - {err}")
    lines.append("")

    # 2. 页面类型分布
    lines.append("## 2. 页面类型分布")
    lines.append("")
    if result.type_counts:
        lines.append("| 类型 | 数量 |")
        lines.append("|------|------|")
        order = [
            "company",
            "industry",
            "score_table",
            "summary",
            "to_be_supplemented",
            "unclassified",
        ]
        keys = order + [k for k in result.type_counts if k not in order]
        for k in keys:
            if k in result.type_counts:
                lines.append(f"| {k} | {result.type_counts[k]} |")
    else:
        lines.append("_无 investment 页面（fixture 或空知识库）_")
    lines.append("")

    # 3. machine_readiness
    lines.append("## 3. Machine Readiness 分布")
    lines.append("")
    rd = result.machine_readiness_counts
    total = sum(rd.values()) or 1
    lines.append("| readiness | 数量 | 占比 |")
    lines.append("|-----------|------|------|")
    for k in ("high", "medium", "low"):
        v = rd.get(k, 0)
        lines.append(f"| {k} | {v} | {v / total:.0%} |")
    lines.append("")

    # 4. Frontmatter 字段覆盖
    lines.append("## 4. Frontmatter 字段覆盖 (investment)")
    lines.append("")
    cov = result.frontmatter_field_coverage
    total_pages = result.investment_page_count or 1
    if cov:
        lines.append("| 字段 | 出现页数 | 覆盖率 |")
        lines.append("|------|----------|--------|")
        # 必填字段优先列出
        priority = list(REQUIRED_FRONTMATTER_FIELDS) + list(RECOMMENDED_MACHINE_FIELDS)
        seen = set()
        ordered: List[str] = []
        for f in priority:
            if f in cov:
                ordered.append(f)
                seen.add(f)
        for f in sorted(cov):
            if f not in seen:
                ordered.append(f)
        for f in ordered:
            lines.append(f"| `{f}` | {cov[f]} | {cov[f] / total_pages:.0%} |")
    else:
        lines.append("_无 frontmatter 覆盖数据_")
    lines.append("")

    # 5. 结构缺口
    lines.append("## 5. 结构缺口（>=5 类）")
    lines.append("")
    lines.append("| 缺口类型 | 数量 | 接入建议 |")
    lines.append("|----------|------|----------|")
    for g in result.structural_gaps:
        suggestion = g["suggestion"]
        lines.append(f"| {g['gap']} | {g['count']} | {suggestion} |")
    lines.append("")
    for g in result.structural_gaps:
        if g["samples"]:
            lines.append(f"### 缺口样本 — {g['gap']}")
            lines.append("")
            for s in g["samples"]:
                lines.append(f"- `{s}`")
            lines.append("")

    # 6. index 对齐
    lines.append("## 6. index.md 对齐")
    lines.append("")
    if result.index_missing_pages:
        lines.append(f"### index 未引用的 investment 页 ({len(result.index_missing_pages)})")
        lines.append("")
        for p in result.index_missing_pages[:20]:
            lines.append(f"- `{p}`")
        if len(result.index_missing_pages) > 20:
            lines.append(f"- ... 其余 {len(result.index_missing_pages) - 20} 项见 ``to_dict()`` 输出")
        lines.append("")
    else:
        lines.append("- 所有 investment 页均被 index.md 引用 ✓")
        lines.append("")
    if result.index_orphan_entries:
        lines.append(f"### index 孤儿引用 (无对应文件) ({len(result.index_orphan_entries)})")
        lines.append("")
        for p in result.index_orphan_entries[:20]:
            lines.append(f"- `{p}`")
        lines.append("")

    # 7. inbox 未消化
    lines.append("## 7. inbox 未消化清单")
    lines.append("")
    if result.inbox_unprocessed:
        for item in result.inbox_unprocessed:
            lines.append(f"- `{item}`")
    else:
        lines.append("_inbox 为空_")
    lines.append("")

    # 8. 接入建议
    lines.append("## 8. TA 接入建议（KB-002/KB-003 前置）")
    lines.append("")
    for idx, s in enumerate(result.integration_suggestions, 1):
        lines.append(f"{idx}. {s}")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("_本报告由 ``scripts/audit_local_knowledge.py`` 只读生成；对应模块 "
                 "``tradingagents.dataflows.local_knowledge_audit``。_")
    lines.append("")
    return "\n".join(lines)


# ── CLI 便利 ─────────────────────────────────────────────────────────


def default_knowledge_root() -> str:
    """返回默认知识库根目录（环境变量优先，其次 ``~/Documents/knowledge``）。"""
    env = os.environ.get("AUTO_DEV_KNOWLEDGE_ROOT") or os.environ.get("KNOWLEDGE_ROOT")
    if env:
        return env
    return str(Path.home() / "Documents" / "knowledge")


def suggest_audit_output_path(docs_dir: str = "docs/knowledge_reports") -> str:
    """生成默认输出路径 ``docs/knowledge_reports/local_knowledge_audit-YYYY-MM-DD.md``。"""
    today = date.today().strftime("%Y-%m-%d")
    return os.path.join(docs_dir, f"local_knowledge_audit-{today}.md")
