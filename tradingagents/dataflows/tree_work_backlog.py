# [KB-005] tree_work_backlog
"""Tree Work inbox/raw/wiki 对齐与未消化研报清单。

在 KB-001 只读审计基础上，把 inbox 积压、raw 已存但 wiki 未消化、wiki 占位
（待补充/低置信/废弃）、index 未同步等缺口，转换为**可执行动作清单**，方便
Tree Work 后续逐项处理并复制执行。

设计约束（对应任务 KB-005）：
  - **只读**：仅用 ``open(..., "r", ...)`` 与 ``Path.iterdir`` / ``os.walk``，
    绝不向知识库写文件，不移动 / 不删除任何 knowledge 文件。
  - **不批量读取 PDF 正文**：仅按文件名 / 扩展名 / frontmatter 分类，不解析 PDF。
  - **不调用 LLM / 不访问外网**：纯标准库 + 复用 KB-001 审计助手。
  - **不输出原文段落**：清单只含路径 / 分类 / 建议动作 / 简短原因。

清单分类（``BacklogCategory``）：
  - ``inbox_unprocessed``：inbox 下未消化的笔记 / 截图 / 想法。
  - ``raw_undigested``：raw 下存在但没有任何 wiki 页 ``sources`` 引用的原始资料。
  - ``wiki_to_be_supplemented``：wiki/investment 下待补充 / 低置信占位页。
  - ``wiki_deprecated``：frontmatter 标注 ``deprecated`` 的废弃页（建议归档）。
  - ``wiki_field_gap``：缺核心章节/字段（总结 / 风险 / sources / symbols）。
  - ``index_not_synced``：wiki 页未被 index.md 引用，或 index 引用了不存在的页。

建议动作（``BacklogAction``）：
  - ``ingest``：消化为 wiki 页面（inbox / raw）。
  - ``fill_fields``：补 machine-readable frontmatter 字段。
  - ``fill_summary``：补 ``## 一句话总结`` / ``## 核心观点``。
  - ``fill_risks``：补 ``## 风险提示``。
  - ``fill_source_links``：补 ``sources`` / ``## 原始资料`` 与 raw 链接。
  - ``archive``：归档（废弃 / 重复占位页）。
  - ``add_index_link``：在 index.md 补 ``[[investment/<name>]]``。
  - ``remove_orphan_link``：移除 index.md 中无对应文件的引用。
  - ``review``：人工确认归类（如 meta prompt / 模板）。

使用示例::

    from tradingagents.dataflows.tree_work_backlog import (
        build_tree_work_backlog,
        render_backlog_report,
    )
    backlog = build_tree_work_backlog("/Users/maybee/Documents/knowledge")
    print(render_backlog_report(backlog))
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tradingagents.dataflows.local_knowledge_audit import (
    INBOX_SUBDIR,
    INDEX_MD,
    INVESTMENT_SUBDIR,
    LOG_MD,
    RAW_SUBDIR,
    LocalKnowledgeAuditResult,
    _is_nonempty_field,
    _iter_markdown_files,
    _parse_frontmatter,
    _read_text_safe,
    _safe_str,
    _split_frontmatter,
    audit_local_knowledge,
)

# ── 常量 ──────────────────────────────────────────────────────────────

# 清单分类。
CATEGORY_INBOX = "inbox_unprocessed"
CATEGORY_RAW = "raw_undigested"
CATEGORY_SUPPLEMENT = "wiki_to_be_supplemented"
CATEGORY_DEPRECATED = "wiki_deprecated"
CATEGORY_FIELD_GAP = "wiki_field_gap"
CATEGORY_INDEX = "index_not_synced"

# 建议动作。
ACTION_INGEST = "ingest"
ACTION_FILL_FIELDS = "fill_fields"
ACTION_FILL_SUMMARY = "fill_summary"
ACTION_FILL_RISKS = "fill_risks"
ACTION_FILL_SOURCES = "fill_source_links"
ACTION_ARCHIVE = "archive"
ACTION_ADD_INDEX = "add_index_link"
ACTION_REMOVE_ORPHAN = "remove_orphan_link"
ACTION_REVIEW = "review"

# inbox 扩展名分类。
_INBOX_NOTE_EXTS = {".md"}
_INBOX_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_INBOX_DOC_EXTS = {".pdf"}

# raw 中视为"已存在原始资料"的扩展名（与 inbox 一致，但 raw 主要是研报）。
_RAW_NOTE_EXTS = {".md"}
_RAW_PDF_EXTS = {".pdf"}

# wiki sources 字段里指向 raw 的 wikilink：[[../../raw/<name>|alias]] 或 [[../../raw/<name>]]
_RAW_LINK_RE = re.compile(r"\[\[(?:\.\./)+raw/([^\]|]+?)(?:\|[^\]]*)?\]\]")
# 也兼容绝对路径风格的 sources 字符串："raw/2026-05-14-xxx.md"
_RAW_PATH_RE = re.compile(r"(?:^|[/\s])raw/([^\s\]|'\"]+?\.md)")


# ── 数据类 ────────────────────────────────────────────────────────────


@dataclass
class BacklogItem:
    """一条 Tree Work 待处理清单项。"""

    category: str
    location: str  # 相对 knowledge_root 的路径或条目名
    suggested_action: str
    detail: str = ""
    priority: str = "medium"  # high / medium / low
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category,
            "location": self.location,
            "suggested_action": self.suggested_action,
            "detail": self.detail,
            "priority": self.priority,
            "extra": dict(self.extra),
        }


@dataclass
class TreeWorkBacklog:
    """整库 Tree Work 待处理清单聚合。"""

    knowledge_root: str
    generated_at: str
    inbox_unprocessed: List[BacklogItem] = field(default_factory=list)
    raw_undigested: List[BacklogItem] = field(default_factory=list)
    wiki_to_be_supplemented: List[BacklogItem] = field(default_factory=list)
    wiki_deprecated: List[BacklogItem] = field(default_factory=list)
    wiki_field_gaps: List[BacklogItem] = field(default_factory=list)
    index_items: List[BacklogItem] = field(default_factory=list)
    # 统计。
    raw_total: int = 0
    raw_referenced: int = 0
    inbox_total: int = 0
    investment_page_count: int = 0
    errors: List[str] = field(default_factory=list)

    def all_items(self) -> List[BacklogItem]:
        """按优先级排序返回所有清单项。"""
        order = {"high": 0, "medium": 1, "low": 2}
        items: List[BacklogItem] = []
        items.extend(self.inbox_unprocessed)
        items.extend(self.raw_undigested)
        items.extend(self.wiki_to_be_supplemented)
        items.extend(self.wiki_deprecated)
        items.extend(self.wiki_field_gaps)
        items.extend(self.index_items)
        items.sort(key=lambda it: (order.get(it.priority, 3), it.category, it.location))
        return items

    def category_counts(self) -> Dict[str, int]:
        return {
            CATEGORY_INBOX: len(self.inbox_unprocessed),
            CATEGORY_RAW: len(self.raw_undigested),
            CATEGORY_SUPPLEMENT: len(self.wiki_to_be_supplemented),
            CATEGORY_DEPRECATED: len(self.wiki_deprecated),
            CATEGORY_FIELD_GAP: len(self.wiki_field_gaps),
            CATEGORY_INDEX: len(self.index_items),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "knowledge_root": self.knowledge_root,
            "generated_at": self.generated_at,
            "inbox_unprocessed": [i.to_dict() for i in self.inbox_unprocessed],
            "raw_undigested": [i.to_dict() for i in self.raw_undigested],
            "wiki_to_be_supplemented": [i.to_dict() for i in self.wiki_to_be_supplemented],
            "wiki_deprecated": [i.to_dict() for i in self.wiki_deprecated],
            "wiki_field_gaps": [i.to_dict() for i in self.wiki_field_gaps],
            "index_items": [i.to_dict() for i in self.index_items],
            "stats": {
                "raw_total": self.raw_total,
                "raw_referenced": self.raw_referenced,
                "raw_undigested": len(self.raw_undigested),
                "inbox_total": self.inbox_total,
                "investment_page_count": self.investment_page_count,
                "category_counts": self.category_counts(),
                "total_backlog": len(self.all_items()),
            },
            "errors": list(self.errors),
        }


# ── inbox 分类 ───────────────────────────────────────────────────────


def classify_inbox_item(rel_path: str) -> Tuple[str, str, str]:
    """按扩展名对 inbox 文件给出 (action, detail, priority)。

    - 笔记/想法 (.md)：``ingest`` → 消化为对应分区 wiki 页。
    - 截图/图片 (.png/.jpg)：``ingest`` → OCR/转写后归档或转写到 wiki。
    - 文档 (.pdf)：``ingest`` → 精读后提炼 wiki 页。
    - 其它（如 prompt 模板）：``review`` → 人工确认归属分区。
    """
    suffix = Path(rel_path).suffix.lower()
    name = Path(rel_path).name
    if suffix in _INBOX_NOTE_EXTS:
        if "prompt" in name.lower() or "模板" in name or "template" in name.lower():
            return ACTION_REVIEW, "meta prompt/模板，确认归属分区后归档", "low"
        return ACTION_INGEST, "inbox 笔记/想法，提炼为 wiki 页面", "medium"
    if suffix in _INBOX_IMAGE_EXTS:
        return ACTION_INGEST, "inbox 截图/图片，需 OCR/转写后转写到 wiki", "medium"
    if suffix in _INBOX_DOC_EXTS:
        return ACTION_INGEST, "inbox PDF，精读后提炼 wiki 页（不批量读取正文）", "medium"
    return ACTION_REVIEW, "未知类型，人工确认归属", "low"


# ── raw 引用提取 ──────────────────────────────────────────────────────


def extract_raw_references(frontmatter: Dict[str, Any], body: str) -> List[str]:
    """从 wiki 页的 sources 字段 + 正文 wikilink 中提取被引用的 raw 文件名。

    返回去重后的 raw 文件名列表（仅文件名，不含路径），如 ``2026-05-14-中邮证券.md``。
    """
    referenced: List[str] = []

    sources = frontmatter.get("sources")
    if isinstance(sources, list):
        for src in sources:
            text = str(src)
            for m in _RAW_LINK_RE.finditer(text):
                referenced.append(m.group(1).strip())
            for m in _RAW_PATH_RE.finditer(text):
                referenced.append(m.group(1).strip())
    elif isinstance(sources, str):
        for m in _RAW_LINK_RE.finditer(sources):
            referenced.append(m.group(1).strip())

    # 正文中的 raw wikilink 也算引用（兜底）。
    for m in _RAW_LINK_RE.finditer(body):
        referenced.append(m.group(1).strip())

    # 归一化：只取文件名，去重，保留非空。
    normalized: List[str] = []
    seen = set()
    for ref in referenced:
        name = ref.split("/")[-1].strip()
        if name and name not in seen:
            seen.add(name)
            normalized.append(name)
    return normalized


def list_raw_files(raw_dir: Path) -> List[Path]:
    """列出 raw/ 下所有文件（含 md / pdf / 图片 / 其它），递归子目录。"""
    if not raw_dir.exists() or not raw_dir.is_dir():
        return []
    files: List[Path] = []
    for path in sorted(raw_dir.rglob("*")):
        if path.is_file():
            files.append(path)
    return files


def classify_raw_item(rel_path: str) -> Tuple[str, str, str]:
    """对未消化的 raw 文件给出 (action, detail, priority)。

    - .md 研报：``ingest`` → 提炼 wiki/investment 页面。
    - .pdf：``ingest`` → 需精读/O CR，优先级 medium（不批量读取正文）。
    - 图片：``ingest`` → OCR 后转写。
    - 其它：``review``。
    """
    suffix = Path(rel_path).suffix.lower()
    if suffix in _RAW_NOTE_EXTS:
        return ACTION_INGEST, "raw 笔记/研报 md，提炼为 wiki/investment 页面，补 sources 反向链接", "high"
    if suffix in _RAW_PDF_EXTS:
        return ACTION_INGEST, "raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文）", "medium"
    if suffix in _INBOX_IMAGE_EXTS:
        return ACTION_INGEST, "raw 图片素材，OCR/转写后归档或转写", "low"
    return ACTION_REVIEW, "raw 未知类型文件，人工确认是否需要消化", "low"


# ── wiki 页分析 ───────────────────────────────────────────────────────


@dataclass
class _WikiPageSummary:
    """wiki 页的 backlog 视角摘要（只读）。"""

    rel_path: str
    frontmatter: Dict[str, Any]
    is_to_be_supplemented: bool
    is_deprecated: bool
    has_summary_section: bool
    has_risk_section: bool
    has_sources_field: bool
    has_symbols_field: bool
    raw_references: List[str]


def _scan_wiki_page(abs_path: Path, rel_path: str) -> _WikiPageSummary:
    text = _read_text_safe(abs_path)
    fm_text, body = _split_frontmatter(text)
    frontmatter = _parse_frontmatter(fm_text)
    raw_refs = extract_raw_references(frontmatter, body)

    # 章节标题（只取 ## / ###）。
    summary_patterns = ("一句话总结", "核心观点", "核心结论", "投资逻辑", "核心数据")
    risk_patterns = ("风险提示", "风险", "风险与不确定性")
    has_summary = any(
        p in h
        for h in [ln.lstrip("#").strip() for ln in body.splitlines() if ln.startswith("## ") or ln.startswith("### ")]
        for p in summary_patterns
    )
    has_risk = any(
        p in h
        for h in [ln.lstrip("#").strip() for ln in body.splitlines() if ln.startswith("## ") or ln.startswith("### ")]
        for p in risk_patterns
    )

    # 待补充标记：frontmatter evidence_level=C / source_quality=低 / stale_risk=高 且正文含占位词，
    # 或正文/标题含 TODO_MARKERS。
    todo_markers = ("此页面内容不完整", "待补充", "内容待补充", "占位", "已废弃")
    body_has_todo = any(m in body or m in abs_path.name for m in todo_markers)

    deprecated_val = _safe_str(frontmatter.get("deprecated")) or ""
    is_deprecated = deprecated_val.lower() in ("true", "yes", "1") or "已废弃" in body

    return _WikiPageSummary(
        rel_path=rel_path,
        frontmatter=frontmatter,
        is_to_be_supplemented=body_has_todo,
        is_deprecated=is_deprecated,
        has_summary_section=has_summary,
        has_risk_section=has_risk,
        has_sources_field=_is_nonempty_field(frontmatter.get("sources")),
        has_symbols_field=_is_nonempty_field(frontmatter.get("symbols")),
        raw_references=raw_refs,
    )


def _build_wiki_backlog_items(page: _WikiPageSummary) -> List[BacklogItem]:
    """为单个 wiki 页生成 backlog 项（可能是 0~多项）。"""
    items: List[BacklogItem] = []

    # 废弃页优先归档（覆盖待补充判定）。
    if page.is_deprecated:
        reason = _safe_str(page.frontmatter.get("deprecated_reason")) or "frontmatter/正文标记为已废弃"
        items.append(
            BacklogItem(
                category=CATEGORY_DEPRECATED,
                location=page.rel_path,
                suggested_action=ACTION_ARCHIVE,
                detail=f"废弃页：{reason}；建议归档或合并到替代页",
                priority="medium",
            )
        )
        return items

    # 待补充 / 低置信占位页。
    if page.is_to_be_supplemented:
        gaps: List[str] = []
        if not page.has_summary_section:
            gaps.append("总结")
        if not page.has_risk_section:
            gaps.append("风险")
        if not page.has_sources_field:
            gaps.append("sources")
        detail = "占位/待补充页，补内容后转正" + (f"；缺：{'/'.join(gaps)}" if gaps else "")
        items.append(
            BacklogItem(
                category=CATEGORY_SUPPLEMENT,
                location=page.rel_path,
                suggested_action=ACTION_FILL_FIELDS,
                detail=detail,
                priority="high",
                extra={"missing": gaps},
            )
        )
        # 待补充页如果同时缺 sources 链接，单独给出 fill_source_links 建议。
        if not page.has_sources_field:
            items.append(
                BacklogItem(
                    category=CATEGORY_SUPPLEMENT,
                    location=page.rel_path,
                    suggested_action=ACTION_FILL_SOURCES,
                    detail="补 sources 字段指向 raw 原始资料",
                    priority="medium",
                )
            )
        return items

    # 正常页：逐项检查字段/章节缺口（每页最多产出几条，避免清单爆炸）。
    if not page.has_summary_section:
        items.append(
            BacklogItem(
                category=CATEGORY_FIELD_GAP,
                location=page.rel_path,
                suggested_action=ACTION_FILL_SUMMARY,
                detail="缺 ## 一句话总结 / 核心观点 章节",
                priority="medium",
            )
        )
    if not page.has_risk_section:
        items.append(
            BacklogItem(
                category=CATEGORY_FIELD_GAP,
                location=page.rel_path,
                suggested_action=ACTION_FILL_RISKS,
                detail="缺 ## 风险提示 章节",
                priority="medium",
            )
        )
    if not page.has_sources_field:
        items.append(
            BacklogItem(
                category=CATEGORY_FIELD_GAP,
                location=page.rel_path,
                suggested_action=ACTION_FILL_SOURCES,
                detail="frontmatter sources 字段为空，补 raw 反向链接",
                priority="medium",
            )
        )
    if not page.has_symbols_field:
        items.append(
            BacklogItem(
                category=CATEGORY_FIELD_GAP,
                location=page.rel_path,
                suggested_action=ACTION_FILL_FIELDS,
                detail="缺 machine symbols 字段，TA 无法按 symbol 命中",
                priority="medium",
            )
        )
    return items


# ── 主构建逻辑 ───────────────────────────────────────────────────────


def build_tree_work_backlog(knowledge_root: str) -> TreeWorkBacklog:
    """只读扫描 Tree Work 本地知识库，产出待处理清单。

    参数:
        knowledge_root: 知识库根目录绝对路径。

    返回:
        :class:`TreeWorkBacklog`。永远不会抛文件不存在异常：缺失分区会记入
        ``errors`` 并保持对应清单为空，便于 fixture / CI 复用。
    """
    root = Path(knowledge_root).expanduser()
    backlog = TreeWorkBacklog(
        knowledge_root=str(root),
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )

    if not root.exists():
        backlog.errors.append(f"knowledge_root 不存在: {root}")
        return backlog

    # 复用 KB-001 审计拿到 index 对齐 / 基础统计（只读）。
    audit: LocalKnowledgeAuditResult = audit_local_knowledge(str(root))
    backlog.errors.extend(audit.errors)
    backlog.investment_page_count = audit.investment_page_count
    backlog.inbox_total = audit.inbox_item_count

    inbox_dir = root / INBOX_SUBDIR
    raw_dir = root / RAW_SUBDIR
    investment_dir = root / INVESTMENT_SUBDIR

    # ── 1. inbox 未消化 ──
    if inbox_dir.exists() and inbox_dir.is_dir():
        for path in sorted(inbox_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = str(path.relative_to(root))
            action, detail, priority = classify_inbox_item(rel)
            backlog.inbox_unprocessed.append(
                BacklogItem(
                    category=CATEGORY_INBOX,
                    location=rel,
                    suggested_action=action,
                    detail=detail,
                    priority=priority,
                )
            )

    # ── 2. wiki 页分析：待补充 / 废弃 / 字段缺口 + 收集 raw 引用 ──
    referenced_raw_names: set = set()
    if investment_dir.exists() and investment_dir.is_dir():
        for md in _iter_markdown_files(investment_dir):
            rel = str(md.relative_to(root))
            try:
                page = _scan_wiki_page(md, rel)
            except Exception as exc:  # pragma: no cover - 容错：单页失败不影响整库
                backlog.errors.append(f"{rel}: 扫描失败 {exc!r}")
                continue
            referenced_raw_names.update(page.raw_references)
            backlog.wiki_to_be_supplemented.extend(
                it for it in _build_wiki_backlog_items(page) if it.category == CATEGORY_SUPPLEMENT
            )
            backlog.wiki_deprecated.extend(
                it for it in _build_wiki_backlog_items(page) if it.category == CATEGORY_DEPRECATED
            )
            backlog.wiki_field_gaps.extend(
                it for it in _build_wiki_backlog_items(page) if it.category == CATEGORY_FIELD_GAP
            )

    # ── 3. raw 未消化：raw 文件未被任何 wiki sources 引用 ──
    raw_files = list_raw_files(raw_dir)
    backlog.raw_total = len(raw_files)
    backlog.raw_referenced = len(
        {f.name for f in raw_files if f.name in referenced_raw_names}
    )
    for path in raw_files:
        rel = str(path.relative_to(root))
        if path.name in referenced_raw_names:
            continue
        action, detail, priority = classify_raw_item(rel)
        backlog.raw_undigested.append(
            BacklogItem(
                category=CATEGORY_RAW,
                location=rel,
                suggested_action=action,
                detail=detail,
                priority=priority,
            )
        )

    # ── 4. index 未同步 ──
    for missing in audit.index_missing_pages:
        backlog.index_items.append(
            BacklogItem(
                category=CATEGORY_INDEX,
                location=missing,
                suggested_action=ACTION_ADD_INDEX,
                detail="investment 页未被 wiki/index.md 引用，补 [[investment/<name>]]",
                priority="low",
            )
        )
    for orphan in audit.index_orphan_entries:
        backlog.index_items.append(
            BacklogItem(
                category=CATEGORY_INDEX,
                location=f"index.md -> {orphan}",
                suggested_action=ACTION_REMOVE_ORPHAN,
                detail="index.md 引用了不存在的 investment 页，移除引用或补建页面",
                priority="low",
            )
        )

    return backlog


# ── 报告渲染 ─────────────────────────────────────────────────────────


def _render_item_table(items: List[BacklogItem]) -> List[str]:
    lines: List[str] = []
    if not items:
        lines.append("_（无）_")
        lines.append("")
        return lines
    lines.append("| 优先级 | 路径 | 建议动作 | 原因 |")
    lines.append("|--------|------|----------|------|")
    order = {"high": 0, "medium": 1, "low": 2}
    for it in sorted(items, key=lambda i: (order.get(i.priority, 3), i.location)):
        lines.append(f"| {it.priority} | `{it.location}` | `{it.suggested_action}` | {it.detail} |")
    lines.append("")
    return lines


def render_backlog_report(backlog: TreeWorkBacklog) -> str:
    """渲染 Markdown 待处理清单报告。只含路径/分类/建议，不含原文段落。"""
    counts = backlog.category_counts()
    total = sum(counts.values())
    lines: List[str] = []
    lines.append(f"# Tree Work inbox/raw/wiki 待消化清单 — {backlog.generated_at.split()[0]}")
    lines.append("")
    lines.append("> [KB-005] tree_work_backlog — 只读扫描，不修改 knowledge 目录；不含原文段落。")
    lines.append("")

    # 1. 概览
    lines.append("## 1. 概览")
    lines.append("")
    lines.append(f"- knowledge_root: `{backlog.knowledge_root}`")
    lines.append(f"- generated_at: {backlog.generated_at}")
    lines.append(f"- investment_md_pages: **{backlog.investment_page_count}**")
    lines.append(f"- inbox_items: **{backlog.inbox_total}**")
    lines.append(
        f"- raw_files: **{backlog.raw_total}** (已被 wiki 引用 {backlog.raw_referenced}，"
        f"未消化 {len(backlog.raw_undigested)})"
    )
    lines.append(f"- backlog 总项: **{total}**")
    lines.append("")
    if backlog.errors:
        lines.append(f"- ⚠️ 扫描错误 ({len(backlog.errors)}):")
        for err in backlog.errors[:10]:
            lines.append(f"  - {err}")
        lines.append("")

    # 2. 分类统计
    lines.append("## 2. 分类统计")
    lines.append("")
    lines.append("| 分类 | 数量 | 说明 |")
    lines.append("|------|------|------|")
    lines.append(
        f"| `inbox_unprocessed` | {counts[CATEGORY_INBOX]} | inbox 积压：未消化的笔记/截图/想法 |"
    )
    lines.append(
        f"| `raw_undigested` | {counts[CATEGORY_RAW]} | raw 已存但无 wiki sources 引用的原始资料 |"
    )
    lines.append(
        f"| `wiki_to_be_supplemented` | {counts[CATEGORY_SUPPLEMENT]} | wiki 占位/待补充/低置信页 |"
    )
    lines.append(
        f"| `wiki_deprecated` | {counts[CATEGORY_DEPRECATED]} | frontmatter/正文标记废弃的页 |"
    )
    lines.append(
        f"| `wiki_field_gap` | {counts[CATEGORY_FIELD_GAP]} | 缺总结/风险/sources/symbols 章节 |"
    )
    lines.append(
        f"| `index_not_synced` | {counts[CATEGORY_INDEX]} | wiki 页未被 index 引用 / index 孤儿 |"
    )
    lines.append("")

    # 3. inbox 未消化
    lines.append("## 3. inbox 未消化（建议 ingest / review）")
    lines.append("")
    lines.extend(_render_item_table(backlog.inbox_unprocessed))

    # 4. raw 未消化
    lines.append("## 4. raw 未消化研报/资料（建议 ingest）")
    lines.append("")
    lines.extend(_render_item_table(backlog.raw_undigested))

    # 5. wiki 待补充
    lines.append("## 5. wiki 待补充/低置信占位页（建议 fill_fields）")
    lines.append("")
    lines.extend(_render_item_table(backlog.wiki_to_be_supplemented))

    # 6. wiki 废弃
    lines.append("## 6. wiki 废弃页（建议 archive）")
    lines.append("")
    lines.extend(_render_item_table(backlog.wiki_deprecated))

    # 7. wiki 字段/章节缺口
    lines.append("## 7. wiki 字段/章节缺口（建议 fill_*）")
    lines.append("")
    lines.extend(_render_item_table(backlog.wiki_field_gaps))

    # 8. index 未同步
    lines.append("## 8. index.md 未同步（建议 add_index_link / remove_orphan_link）")
    lines.append("")
    lines.extend(_render_item_table(backlog.index_items))

    # 9. 建议执行顺序
    lines.append("## 9. 建议执行顺序（Tree Work）")
    lines.append("")
    lines.append("1. **inbox 优先消化**：避免新素材积压，TA 不读 inbox。")
    lines.append("2. **raw 研报 ingest**：把高优先级 .md 研报提炼为 wiki/investment 页，并补 sources 反向链接。")
    lines.append("3. **wiki 待补充页 fill_fields**：补内容/总结/风险/sources，转正后 TA 才能高置信引用。")
    lines.append("4. **wiki 废弃页 archive**：合并到替代页或归档，避免 TA 误用低置信内容。")
    lines.append("5. **wiki 字段缺口 fill_***：补 symbols/sources/风险，提升 machine_readiness。")
    lines.append("6. **index.md 同步**：补引用 / 删孤儿，保证可发现性。")
    lines.append("")
    lines.append("> 复制本清单逐项执行；每项完成后回到对应 wiki 页补 frontmatter ``updated``。")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "_本报告由 ``scripts/tree_work_backlog.py`` 只读生成；对应模块 "
        "``tradingagents.dataflows.tree_work_backlog``。_"
    )
    lines.append("")
    return "\n".join(lines)


# ── CLI 便利 ─────────────────────────────────────────────────────────


def default_knowledge_root() -> str:
    """返回默认知识库根目录（环境变量优先，其次 ``~/Documents/knowledge``）。"""
    env = os.environ.get("AUTO_DEV_KNOWLEDGE_ROOT") or os.environ.get("KNOWLEDGE_ROOT")
    if env:
        return env
    return str(Path.home() / "Documents" / "knowledge")


def suggest_backlog_output_path(docs_dir: str = "docs/knowledge_reports") -> str:
    """生成默认输出路径 ``docs/knowledge_reports/tree_work_ingest_backlog-YYYY-MM-DD.md``。"""
    today = date.today().strftime("%Y-%m-%d")
    return os.path.join(docs_dir, f"tree_work_ingest_backlog-{today}.md")
