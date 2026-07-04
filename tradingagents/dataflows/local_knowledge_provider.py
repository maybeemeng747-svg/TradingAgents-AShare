# [KB-003] local_knowledge_raw_evidence
"""Tree Work 本地知识源 raw_evidence 接入。

在 KB-001 只读**审计**与 KB-002 契约 **lint** 之上，本模块按 symbol/name/theme/tags
只读查询 Tree Work 已消化的 ``wiki/investment/`` 页面，产出 raw_evidence 兼容的
结构化结果，供 TA 报告渲染"本地知识补充"区块。

设计约束（对应任务 KB-003）：
  - **只读**：仅用 ``open(..., "r", encoding="utf-8")`` 与 ``Path.iterdir``，
    绝不向知识库写文件。
  - **只接消化后的 wiki**：不直接读 raw PDF / inbox 做结论。
  - **背景/观点源**：本地知识不替代行情/公告/财务/资金流；只作为补充证据，
    不影响强动作门禁。
  - **低置信/过期页面标记 STALE/LOW_CONFIDENCE**：不进入候选加分。
  - **不输出长篇原文**：仅取"一句话总结/核心观点"段 + 风险段 + 原页面路径，
    每段摘要最多 200 字。
  - **不调用 LLM / 不访问外网**：纯标准库 + 复用 KB-001 解析助手。

状态机（与 raw_evidence 契约一致）：
  - ``HAS_DATA``：至少一个 high/medium readiness 命中页（且非纯 stale/low）。
  - ``NORMAL_NO_DATA``：无任何命中。
  - ``FAILED``：知识库不可读 / 路径不存在 / 解析异常。
  - ``STALE``：仅命中 stale_risk=高 或 valid_until 已过期 的页面。
  - ``LOW_CONFIDENCE``：仅命中 待补充/低置信（machine_readiness=low）页面。

返回结构（与任务要求字段对齐）：
  ``status / vendor=tree_work_wiki / matched_pages / symbols / themes /
  summary / risks / sources / updated_at / confidence``。

使用示例::

    from tradingagents.dataflows.local_knowledge_provider import (
        query_local_knowledge,
        render_local_knowledge_block,
    )
    result = query_local_knowledge(
        "/Users/maybee/Documents/knowledge", symbol="603296"
    )
    print(render_local_knowledge_block(result))
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 复用 KB-001 / KB-002 的只读解析助手，保持单一解析实现，避免行为分叉。
from tradingagents.dataflows.local_knowledge_audit import (
    INVESTMENT_SUBDIR,
    TODO_MARKERS,
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
    default_knowledge_root,
)
from tradingagents.dataflows.local_knowledge_lint import (
    HIGH_STALE_RISK_VALUES,
    LOW_CONFIDENCE_EVIDENCE_LEVELS,
)


# ── 常量 ──────────────────────────────────────────────────────────────

VENDOR = "tree_work_wiki"

# 状态枚举（与 evidence_contract 保持一致口径）。
STATUS_HAS_DATA = "HAS_DATA"
STATUS_NORMAL_NO_DATA = "NORMAL_NO_DATA"
STATUS_FAILED = "FAILED"
STATUS_STALE = "STALE"
STATUS_LOW_CONFIDENCE = "LOW_CONFIDENCE"

# 命中页面类型在排序中的优先级（公司页 > 评分表 > 行业 > 汇总 > 未分类）。
_PAGE_TYPE_RANK: Dict[str, int] = {
    "company": 0,
    "score_table": 1,
    "industry": 2,
    "summary": 3,
    "unclassified": 4,
    "to_be_supplemented": 5,
}

# 6 位股票代码（文件名/正文出现均算潜在命中线索）。
_STOCK_CODE_RE = re.compile(r"\b(6|0|3|9|8)\d{5}\b")
_BARE_CODE_RE = re.compile(r"^\d{6}$")
# A 股代码后缀（用于把 "603296" 与 "603296.SH" 视作同一标的）。
_SUFFIX_RE = re.compile(r"\.(SH|SZ|BJ|HK|US|SS)$", re.IGNORECASE)

# 单条摘要/风险的最大长度（避免输出长篇原文）。
_SUMMARY_MAX_CHARS = 200
_RISK_MAX_CHARS = 120
_MAX_SUMMARY_ENTRIES = 3
_MAX_RISK_ENTRIES = 5
_MAX_SOURCE_ENTRIES = 5
_MAX_MATCHED_PAGES = 5


# ── 数据类 ────────────────────────────────────────────────────────────


@dataclass
class LocalKnowledgeMatch:
    """单条 wiki 命中。

    只携带背景/观点级字段，**绝不**包含大段原文。
    """

    rel_path: str
    title: str
    page_type: str
    summary: str = ""
    risks: List[str] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)
    symbols: List[str] = field(default_factory=list)
    themes: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    updated_at: Optional[str] = None
    machine_readiness: str = "low"
    is_stale: bool = False
    is_low_confidence: bool = False
    is_to_be_supplemented: bool = False
    matched_by: List[str] = field(default_factory=list)
    confidence: str = "low"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rel_path": self.rel_path,
            "title": self.title,
            "page_type": self.page_type,
            "summary": self.summary,
            "risks": list(self.risks),
            "sources": list(self.sources),
            "symbols": list(self.symbols),
            "themes": list(self.themes),
            "tags": list(self.tags),
            "updated_at": self.updated_at,
            "machine_readiness": self.machine_readiness,
            "is_stale": self.is_stale,
            "is_low_confidence": self.is_low_confidence,
            "is_to_be_supplemented": self.is_to_be_supplemented,
            "matched_by": list(self.matched_by),
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LocalKnowledgeMatch":
        return cls(
            rel_path=str(data.get("rel_path") or ""),
            title=str(data.get("title") or ""),
            page_type=str(data.get("page_type") or "unclassified"),
            summary=str(data.get("summary") or ""),
            risks=list(data.get("risks") or []),
            sources=list(data.get("sources") or []),
            symbols=list(data.get("symbols") or []),
            themes=list(data.get("themes") or []),
            tags=list(data.get("tags") or []),
            updated_at=data.get("updated_at"),
            machine_readiness=str(data.get("machine_readiness") or "low"),
            is_stale=bool(data.get("is_stale") or False),
            is_low_confidence=bool(data.get("is_low_confidence") or False),
            is_to_be_supplemented=bool(data.get("is_to_be_supplemented") or False),
            matched_by=list(data.get("matched_by") or []),
            confidence=str(data.get("confidence") or "low"),
        )


@dataclass
class LocalKnowledgeQueryResult:
    """整次查询的聚合结果。"""

    status: str = STATUS_NORMAL_NO_DATA
    vendor: str = VENDOR
    matched_pages: List[LocalKnowledgeMatch] = field(default_factory=list)
    symbols: List[str] = field(default_factory=list)
    themes: List[str] = field(default_factory=list)
    summary: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)
    updated_at: Optional[str] = None
    confidence: str = "low"
    query: Dict[str, Any] = field(default_factory=dict)
    knowledge_root: str = ""
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "vendor": self.vendor,
            "matched_pages": [m.to_dict() for m in self.matched_pages],
            "symbols": list(self.symbols),
            "themes": list(self.themes),
            "summary": list(self.summary),
            "risks": list(self.risks),
            "sources": list(self.sources),
            "updated_at": self.updated_at,
            "confidence": self.confidence,
            "query": dict(self.query),
            "knowledge_root": self.knowledge_root,
            "errors": list(self.errors),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LocalKnowledgeQueryResult":
        matched = [
            LocalKnowledgeMatch.from_dict(d)
            for d in (data.get("matched_pages") or [])
            if isinstance(d, dict)
        ]
        return cls(
            status=str(data.get("status") or STATUS_NORMAL_NO_DATA),
            vendor=str(data.get("vendor") or VENDOR),
            matched_pages=matched,
            symbols=list(data.get("symbols") or []),
            themes=list(data.get("themes") or []),
            summary=list(data.get("summary") or []),
            risks=list(data.get("risks") or []),
            sources=list(data.get("sources") or []),
            updated_at=data.get("updated_at"),
            confidence=str(data.get("confidence") or "low"),
            query=dict(data.get("query") or {}),
            knowledge_root=str(data.get("knowledge_root") or ""),
            errors=list(data.get("errors") or []),
        )


# ── 匹配辅助 ──────────────────────────────────────────────────────────


def _normalize_symbol_list(raw: Any) -> List[str]:
    """把 frontmatter ``symbols`` 字段（list 或 str）归一为字符串列表。

    Tree Work 约定每项形如 ``603296.SH 华勤技术``（代码+空格+简称）。
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        items = [raw]
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        return []
    out: List[str] = []
    for item in items:
        if item is None:
            continue
        text = str(item).strip().strip('"').strip("'")
        if text:
            out.append(text)
    return out


def _normalize_str_list(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        # 兼容 ``themes: [AI服务器, 液冷]`` 内联写法被 KB-001 极简解析器处理过的情况。
        text = raw.strip()
        if not text:
            return []
        return [text]
    if isinstance(raw, (list, tuple)):
        return [str(x).strip() for x in raw if str(x).strip()]
    return []


def _split_symbol_entry(entry: str) -> Tuple[str, str]:
    """把 ``603296.SH 华勤技术`` 拆为 ``(代码, 简称)``。

    代码部分去掉 ``.SH/.SZ`` 后缀得到 6 位 bare code；简称部分原样返回（去首尾空白）。
    """
    parts = entry.split(maxsplit=1)
    code = parts[0] if parts else ""
    name = parts[1] if len(parts) > 1 else ""
    bare = _SUFFIX_RE.sub("", code).strip()
    return bare, name


def _symbol_matches(query_symbol: str, page_symbols: List[str]) -> bool:
    """判断查询 symbol 是否命中页面 symbols 列表。

    - ``603296`` 命中 ``603296.SH 华勤技术``（bare code 子串）。
    - ``603296.SH`` 命中 ``603296.SH 华勤技术``（去后缀比较）。
    - ``华勤技术`` 命中 ``603296.SH 华勤技术``（简称子串，大小写不敏感）。
    """
    if not query_symbol or not page_symbols:
        return False
    q = query_symbol.strip()
    q_bare = _SUFFIX_RE.sub("", q).strip()
    q_lower = q.lower()
    for entry in page_symbols:
        bare, name = _split_symbol_entry(entry)
        if q_bare and bare == q_bare:
            return True
        # 全代码比较（保留后缀，大小写不敏感）。
        entry_code = entry.split(maxsplit=1)[0] if entry else ""
        if entry_code and entry_code.lower() == q_lower:
            return True
        if name and q_lower and q_lower in name.lower():
            return True
    return False


def _theme_matches(query_themes: List[str], page_themes: List[str]) -> bool:
    """查询 themes 与页面 themes 任一子串命中即视为命中（大小写不敏感）。"""
    if not query_themes or not page_themes:
        return False
    q_norm = [t.lower().strip() for t in query_themes if t and t.strip()]
    p_norm = [t.lower().strip() for t in page_themes if t and t.strip()]
    for q in q_norm:
        for p in p_norm:
            if not q or not p:
                continue
            if q in p or p in q:
                return True
    return False


def _name_matches(query_name: str, title: str, filename: str) -> bool:
    """查询 name 与页面 title/filename 子串命中（大小写不敏感）。"""
    if not query_name:
        return False
    q = query_name.strip().lower()
    if not q:
        return False
    if q in (title or "").lower():
        return True
    if q in (filename or "").lower():
        return True
    return False


def _tags_match(query_tags: List[str], page_tags: List[str]) -> bool:
    if not query_tags or not page_tags:
        return False
    q_norm = {t.lower().strip() for t in query_tags if t and t.strip()}
    p_norm = {t.lower().strip() for t in page_tags if t and t.strip()}
    return bool(q_norm & p_norm)


# ── 段落抽取 ──────────────────────────────────────────────────────────


_SUMMARY_HEADERS = (
    "一句话总结",
    "核心观点",
    "核心结论",
    "投资逻辑",
    "核心数据",
)
_RISK_HEADERS = (
    "风险提示",
    "风险",
    "风险与不确定性",
)
_SOURCES_HEADERS = (
    "原始资料",
    "关联研报",
    "资料来源",
    "参考资料",
)


def _extract_section_text(body: str, headers: Tuple[str, ...], max_chars: int) -> str:
    """提取 ``## <header>`` 紧跟的段落文本（到下一个 ``##`` 之前），裁剪到 max_chars。

    不会输出整段原文——只取首段（首个非空段落），并截断到上限。
    """
    lines = body.splitlines()
    in_section = False
    collected: List[str] = []
    for line in lines:
        if line.startswith("## ") or line.startswith("### "):
            header_text = line.lstrip("#").strip()
            if in_section:
                # 已经在目标段中遇到下一个标题，结束。
                break
            if any(p in header_text for p in headers):
                in_section = True
            continue
        if in_section:
            # 只取首段非空行；遇到连续空行视为段落结束。
            stripped = line.strip()
            if not stripped:
                if collected:
                    break
                continue
            collected.append(stripped)
            if sum(len(c) for c in collected) >= max_chars:
                break
    text = " ".join(collected).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…"
    return text


def _extract_risk_items(body: str, max_items: int = 5) -> List[str]:
    """从风险段提取条目（``- xxx`` 或 ``1. xxx``），裁剪每条到 _RISK_MAX_CHARS。"""
    items_raw = _extract_list_items(body, _RISK_HEADERS, max_items)
    items: List[str] = []
    for text in items_raw:
        if len(text) > _RISK_MAX_CHARS:
            text = text[: _RISK_MAX_CHARS].rstrip() + "…"
        if text and text not in items:
            items.append(text)
    return items


def _extract_sources(
    frontmatter_sources: List[str],
    body: str,
    max_items: int = 5,
) -> List[str]:
    """提取原始资料/来源：优先 frontmatter ``sources``，其次正文 ``## 原始资料`` 段。

    输出每条裁剪到 _RISK_MAX_CHARS。``[[...|alias]]`` 形式取 alias。
    """
    items: List[str] = []
    for raw in frontmatter_sources:
        text = _strip_wiki_link(str(raw))
        if not text:
            continue
        if len(text) > _RISK_MAX_CHARS:
            text = text[: _RISK_MAX_CHARS].rstrip() + "…"
        if text and text not in items:
            items.append(text)
        if len(items) >= max_items:
            return items
    # 正文段：按列表项 ``- xxx`` / ``* xxx`` / ``1. xxx`` 解析（与风险段一致）。
    section_items = _extract_list_items(body, _SOURCES_HEADERS, max_items - len(items))
    for text in section_items:
        cleaned = _strip_wiki_link(text).strip()
        if not cleaned:
            continue
        if len(cleaned) > _RISK_MAX_CHARS:
            cleaned = cleaned[: _RISK_MAX_CHARS].rstrip() + "…"
        if cleaned and cleaned not in items:
            items.append(cleaned)
        if len(items) >= max_items:
            break
    return items


def _extract_list_items(
    body: str,
    headers: Tuple[str, ...],
    max_items: int,
) -> List[str]:
    """从指定章节提取列表项（``- xxx`` / ``* xxx`` / ``1. xxx``）。

    与 ``_extract_section_text`` 不同，本函数保留每个列表项为独立条目，
    适合 sources / risks 等结构化列表。
    """
    if max_items <= 0:
        return []
    lines = body.splitlines()
    in_section = False
    items: List[str] = []
    for line in lines:
        if line.startswith("## ") or line.startswith("### "):
            header_text = line.lstrip("#").strip()
            if in_section:
                break
            if any(p in header_text for p in headers):
                in_section = True
            continue
        if not in_section:
            continue
        stripped = line.strip()
        if not stripped:
            if items:
                # 列表项之间可能空行；遇到连续空行才结束。简单起见，遇到空行
                # 且已有 items 时停止当前段（与 risks 一致）。
                break
            continue
        m = re.match(r"^(?:[-*•]|\d+[.)、])\s*(.+)$", stripped)
        if m:
            items.append(m.group(1).strip())
            if len(items) >= max_items:
                break
    return items


def _strip_wiki_link(text: str) -> str:
    """把 ``[[target|alias]]`` 替换为 alias（无 alias 则 target）。"""
    if not text:
        return ""

    def _replace(m: re.Match) -> str:
        inner = m.group(1)
        if "|" in inner:
            return inner.split("|", 1)[1].strip()
        return inner.split("/")[-1].strip()

    return re.sub(r"\[\[([^\]]+)\]\]", _replace, text).strip()


# ── 单页处理 ──────────────────────────────────────────────────────────


def _compute_page_confidence(
    machine_readiness: str,
    is_stale: bool,
    is_low_confidence: bool,
    is_to_be_supplemented: bool,
) -> str:
    """计算单页 confidence：high/medium/low。"""
    if is_to_be_supplemented or is_low_confidence:
        return "low"
    if is_stale:
        return "low"
    if machine_readiness == "high":
        return "high"
    if machine_readiness == "medium":
        return "medium"
    return "low"


def _build_match(
    rel_path: str,
    abs_path: Path,
    page_audit: Any,
    frontmatter: Dict[str, Any],
    matched_by: List[str],
) -> LocalKnowledgeMatch:
    """从已审计的页面构造一条 LocalKnowledgeMatch（只读，不输出原文）。"""
    text = _read_text_safe(abs_path)
    _fm_text, body = _split_frontmatter(text)

    summary = _extract_section_text(body, _SUMMARY_HEADERS, _SUMMARY_MAX_CHARS)
    risks = _extract_risk_items(body, max_items=_MAX_RISK_ENTRIES)
    fm_sources = _normalize_str_list(frontmatter.get("sources"))
    sources = _extract_sources(fm_sources, body, max_items=_MAX_SOURCE_ENTRIES)

    symbols = _normalize_symbol_list(frontmatter.get("symbols"))
    themes = _normalize_str_list(frontmatter.get("themes"))
    tags = _normalize_str_list(frontmatter.get("tags"))

    updated_at = _safe_str(frontmatter.get("updated"))

    # 复用 KB-001/KB-002 的 stale/low 判断，保持口径一致。
    stale_val = _safe_str(frontmatter.get("stale_risk")) or ""
    is_stale = stale_val in HIGH_STALE_RISK_VALUES or page_audit.valid_until_expired
    evidence = _safe_str(frontmatter.get("evidence_level")) or ""
    is_low_conf = evidence in LOW_CONFIDENCE_EVIDENCE_LEVELS
    is_todo = page_audit.is_to_be_supplemented

    confidence = _compute_page_confidence(
        page_audit.machine_readiness, is_stale, is_low_conf, is_todo
    )

    return LocalKnowledgeMatch(
        rel_path=rel_path,
        title=page_audit.title or abs_path.stem,
        page_type=page_audit.page_type,
        summary=summary,
        risks=risks,
        sources=sources,
        symbols=symbols,
        themes=themes,
        tags=tags,
        updated_at=updated_at,
        machine_readiness=page_audit.machine_readiness,
        is_stale=is_stale,
        is_low_confidence=is_low_conf,
        is_to_be_supplemented=is_todo,
        matched_by=matched_by,
        confidence=confidence,
    )


def _page_matches(
    page_audit: Any,
    abs_path: Path,
    frontmatter: Dict[str, Any],
    *,
    symbol: Optional[str],
    name: Optional[str],
    themes: Optional[List[str]],
    tags: Optional[List[str]],
) -> List[str]:
    """返回命中的字段名列表（空表示未命中）。

    匹配维度：
      - symbol: frontmatter ``symbols`` 字段
      - name: title/filename 子串
      - theme: frontmatter ``themes`` 子串
      - tag: frontmatter ``tags`` 集合交集
    """
    matched: List[str] = []
    if symbol:
        page_symbols = _normalize_symbol_list(frontmatter.get("symbols"))
        if _symbol_matches(symbol, page_symbols):
            matched.append("symbol")
    if name:
        if _name_matches(name, page_audit.title or "", abs_path.name):
            matched.append("name")
    if themes:
        page_themes = _normalize_str_list(frontmatter.get("themes"))
        if _theme_matches(themes, page_themes):
            matched.append("theme")
    if tags:
        page_tags = _normalize_str_list(frontmatter.get("tags"))
        if _tags_match(tags, page_tags):
            matched.append("tag")
    return matched


def _rank_key(match: LocalKnowledgeMatch) -> Tuple[int, int, str]:
    """排序键：(page_type_rank, readiness_rank, rel_path)。升序，取前 N。"""
    type_rank = _PAGE_TYPE_RANK.get(match.page_type, 99)
    readiness_rank = {"high": 0, "medium": 1, "low": 2}.get(
        match.machine_readiness, 3
    )
    # stale/low 排到后面。
    if match.is_stale or match.is_low_confidence or match.is_to_be_supplemented:
        readiness_rank = max(readiness_rank, 2)
    return (type_rank, readiness_rank, match.rel_path)


# ── 主查询逻辑 ────────────────────────────────────────────────────────


def query_local_knowledge(
    knowledge_root: str,
    *,
    symbol: Optional[str] = None,
    name: Optional[str] = None,
    themes: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
    max_pages: int = _MAX_MATCHED_PAGES,
) -> LocalKnowledgeQueryResult:
    """按 symbol/name/themes/tags 只读查询 investment wiki。

    参数:
        knowledge_root: 知识库根目录绝对路径。
        symbol: A 股代码或代码+后缀（如 ``603296`` / ``603296.SH``）。
        name: 公司/主题简称（与 title/filename 子串匹配）。
        themes: 主题列表（与页面 themes 子串匹配）。
        tags: tag 列表（与页面 tags 集合交集匹配）。
        max_pages: 最多返回的命中页数。

    返回:
        :class:`LocalKnowledgeQueryResult`。永远不会因单页解析失败而抛异常：
        单页失败记入 ``errors`` 并跳过；知识库不存在时返回 status=FAILED。
    """
    symbol = (symbol or "").strip() or None
    name = (name or "").strip() or None
    themes_norm = [t.strip() for t in (themes or []) if t and t.strip()]
    tags_norm = [t.strip() for t in (tags or []) if t and t.strip()]

    query_desc: Dict[str, Any] = {}
    if symbol:
        query_desc["symbol"] = symbol
    if name:
        query_desc["name"] = name
    if themes_norm:
        query_desc["themes"] = list(themes_norm)
    if tags_norm:
        query_desc["tags"] = list(tags_norm)

    root = Path(knowledge_root).expanduser()
    result = LocalKnowledgeQueryResult(
        knowledge_root=str(root),
        query=query_desc,
    )

    if not root.exists():
        result.status = STATUS_FAILED
        result.errors.append(f"knowledge_root 不存在: {root}")
        return result

    if not any([symbol, name, themes_norm, tags_norm]):
        # 没给查询条件：不召回，避免误把整库 76 页都视为命中。
        result.status = STATUS_NORMAL_NO_DATA
        result.errors.append("未提供查询条件（symbol/name/themes/tags 任一）")
        return result

    investment_dir = root / INVESTMENT_SUBDIR
    md_files = _iter_markdown_files(investment_dir)
    if not md_files:
        if not investment_dir.exists():
            result.errors.append(f"investment 分区不存在: {investment_dir}")
        result.status = STATUS_NORMAL_NO_DATA
        return result

    for md in md_files:
        rel = str(md.relative_to(root))
        try:
            page_audit = _audit_single_page(rel, md)
            text = _read_text_safe(md)
            fm_text, _body = _split_frontmatter(text)
            frontmatter = _parse_frontmatter(fm_text)
        except Exception as exc:  # pragma: no cover - 容错：单页失败不影响整库
            result.errors.append(f"{rel}: 解析失败 {exc!r}")
            continue

        matched_by = _page_matches(
            page_audit,
            md,
            frontmatter,
            symbol=symbol,
            name=name,
            themes=themes_norm,
            tags=tags_norm,
        )
        if not matched_by:
            continue
        try:
            match = _build_match(rel, md, page_audit, frontmatter, matched_by)
        except Exception as exc:  # pragma: no cover
            result.errors.append(f"{rel}: 构造命中失败 {exc!r}")
            continue
        result.matched_pages.append(match)

    # 排序 + 截断。
    result.matched_pages.sort(key=_rank_key)
    if len(result.matched_pages) > max_pages:
        result.matched_pages = result.matched_pages[:max_pages]

    _aggregate_result(result)
    return result


def _aggregate_result(result: LocalKnowledgeQueryResult) -> None:
    """根据命中页聚合 symbols/themes/summary/risks/sources/updated_at/status/confidence。"""
    matches = result.matched_pages
    if not matches:
        result.status = STATUS_NORMAL_NO_DATA
        result.confidence = "low"
        return

    # 聚合字段（去重保序）。
    symbols_seen: List[str] = []
    themes_seen: List[str] = []
    summary_entries: List[str] = []
    risk_seen: List[str] = []
    sources_seen: List[str] = []
    for m in matches:
        for s in m.symbols:
            if s not in symbols_seen:
                symbols_seen.append(s)
        for t in m.themes:
            if t not in themes_seen:
                themes_seen.append(t)
        if m.summary and m.summary not in summary_entries:
            summary_entries.append(m.summary)
        for r in m.risks:
            if r not in risk_seen:
                risk_seen.append(r)
        for src in m.sources:
            if src not in sources_seen:
                sources_seen.append(src)

    result.symbols = symbols_seen[:10]
    result.themes = themes_seen[:10]
    result.summary = summary_entries[:_MAX_SUMMARY_ENTRIES]
    result.risks = risk_seen[:_MAX_RISK_ENTRIES]
    result.sources = sources_seen[:_MAX_SOURCE_ENTRIES]

    # updated_at：取最大（字符串 ISO 日期比较即可）。
    upd_list = [m.updated_at for m in matches if m.updated_at]
    result.updated_at = max(upd_list) if upd_list else None

    # 状态机：是否有非 stale/low 命中？
    has_fresh = any(
        not (m.is_stale or m.is_low_confidence or m.is_to_be_supplemented)
        for m in matches
    )
    has_only_stale = all(
        m.is_stale and not m.is_low_confidence and not m.is_to_be_supplemented
        for m in matches
    )
    has_only_low = all(
        (m.is_low_confidence or m.is_to_be_supplemented) and not m.is_stale
        for m in matches
    )

    if has_fresh:
        result.status = STATUS_HAS_DATA
        # confidence 取最高命中页。
        for m in matches:
            if m.confidence == "high":
                result.confidence = "high"
                break
            if m.confidence == "medium":
                result.confidence = "medium"
        else:
            result.confidence = "low"
    elif has_only_stale:
        result.status = STATUS_STALE
        result.confidence = "low"
    elif has_only_low:
        result.status = STATUS_LOW_CONFIDENCE
        result.confidence = "low"
    else:
        # 混合 stale + low，按低置信处理。
        result.status = STATUS_LOW_CONFIDENCE
        result.confidence = "low"


# ── 报告渲染 ──────────────────────────────────────────────────────────


_STATUS_LABELS = {
    STATUS_HAS_DATA: "已命中本地知识",
    STATUS_NORMAL_NO_DATA: "无本地知识命中",
    STATUS_FAILED: "本地知识查询失败",
    STATUS_STALE: "本地知识已过期（仅供参考）",
    STATUS_LOW_CONFIDENCE: "本地知识低置信（仅供参考）",
}


def render_local_knowledge_block(result: LocalKnowledgeQueryResult) -> str:
    """渲染 Markdown "本地知识补充" 区块。

    最多 3 条摘要 + 风险 + 原页面路径。不输出长篇原文。

    状态为 NORMAL_NO_DATA / FAILED 时返回空字符串，让上层可以选择隐藏区块。
    """
    if not isinstance(result, LocalKnowledgeQueryResult):
        return ""
    if result.status in (STATUS_NORMAL_NO_DATA, STATUS_FAILED):
        return ""

    lines: List[str] = []
    lines.append("### 本地知识补充")
    lines.append("")
    status_label = _STATUS_LABELS.get(result.status, result.status)
    lines.append(
        f"> 来源：Tree Work `wiki/investment`（vendor=tree_work_wiki）— "
        f"{status_label}；本地知识仅作背景/观点源，不替代行情、公告、财务、资金流。"
    )
    lines.append("")

    if result.matched_pages:
        # 摘要（最多 3 条）
        for idx, m in enumerate(result.matched_pages[:_MAX_SUMMARY_ENTRIES], 1):
            confidence_tag = _confidence_tag(m)
            stale_tag = " · STALE" if m.is_stale else ""
            low_tag = " · LOW_CONFIDENCE" if m.is_low_confidence else ""
            todo_tag = " · 待补充" if m.is_to_be_supplemented else ""
            tag_suffix = confidence_tag + stale_tag + low_tag + todo_tag
            lines.append(f"**{idx}. {m.title}**{tag_suffix}")
            lines.append("")
            if m.summary:
                lines.append(f"- 摘要：{m.summary}")
            else:
                lines.append("- 摘要：（页面未提供一句话总结）")
            if m.symbols:
                lines.append(f"- 关联标的：{', '.join(m.symbols[:5])}")
            if m.themes:
                lines.append(f"- 主题：{', '.join(m.themes[:5])}")
            lines.append(f"- 原页面：`{m.rel_path}`")
            lines.append("")

        # 风险聚合
        if result.risks:
            lines.append("**风险提示（本地知识源）：**")
            lines.append("")
            for r in result.risks[:_MAX_RISK_ENTRIES]:
                lines.append(f"- {r}")
            lines.append("")

        # 来源聚合
        if result.sources:
            lines.append("**原始资料/关联研报（本地知识源）：**")
            lines.append("")
            for s in result.sources[:_MAX_SOURCE_ENTRIES]:
                lines.append(f"- {s}")
            lines.append("")

    if result.updated_at:
        lines.append(f"_最近更新：{result.updated_at}；confidence={result.confidence}_")
        lines.append("")

    return "\n".join(lines)


def _confidence_tag(m: LocalKnowledgeMatch) -> str:
    if m.confidence == "high":
        return " · HIGH"
    if m.confidence == "medium":
        return " · MEDIUM"
    return " · LOW"


# ── DataCollector 接入辅助 ────────────────────────────────────────────


def build_raw_evidence_entry(
    result: LocalKnowledgeQueryResult,
    trade_date: str,
    fetched_at: str,
) -> Dict[str, Any]:
    """把查询结果转为 ``DataCollector.build_raw_evidence`` 兼容的 raw_evidence 条目。

    [KB-003] local_knowledge_raw_evidence
    """
    payload = result.to_dict()
    return {
        "raw": payload,
        "field": "local_knowledge",
        "status": result.status,
        "vendor": VENDOR,
        "endpoint": "wiki/investment",
        "as_of": trade_date,
        "fetched_at": fetched_at,
        "record_count": len(result.matched_pages),
        "unit": None,
        "error": "; ".join(result.errors) if result.errors else None,
        "fallback_from": None,
        "source_url": None,
        "is_realtime_patched": False,
        "source_type": "tree_work_wiki",
        "unit_verified": None,
        "query_mode": None,
        "adjustment": None,
        "force_reason": None,
    }


def query_failed_entry(
    trade_date: str,
    fetched_at: str,
    error: str,
) -> Dict[str, Any]:
    """构造 FAILED 状态的 raw_evidence 条目（知识库不可读/异常时使用）。"""
    return {
        "raw": None,
        "field": "local_knowledge",
        "status": STATUS_FAILED,
        "vendor": VENDOR,
        "endpoint": "wiki/investment",
        "as_of": trade_date,
        "fetched_at": fetched_at,
        "record_count": 0,
        "unit": None,
        "error": (error or "")[:200] or None,
        "fallback_from": None,
        "source_url": None,
        "is_realtime_patched": False,
        "source_type": "tree_work_wiki",
        "unit_verified": None,
        "query_mode": None,
        "adjustment": None,
        "force_reason": None,
    }


# ── CLI 便利（复用 default_knowledge_root）────────────────────────────


def suggest_query_output_path(docs_dir: str = "docs/knowledge_reports") -> str:
    today = date.today().strftime("%Y-%m-%d")
    return os.path.join(docs_dir, f"local_knowledge_query-{today}.md")


# ── TradeFlow 候选命中分（KB-004）─────────────────────────────────────

# [KB-004] tradeflow_knowledge_score
"""命中分计算约束（对应任务 KB-004）：

- **过期知识不得加分**：``is_stale=True`` 的命中页对 ``local_knowledge_score``
  贡献为 0，但仍计入 ``stale_hit_count`` 与 ``matched_pages`` 列表，便于前端
  提示用户更新。
- **低置信/待补充同理**：``is_low_confidence`` 或 ``is_to_be_supplemented``
  的命中页贡献为 0，但仍计入 ``low_confidence_hit_count``。
- **只加解释力，不改变强动作门禁**：``local_knowledge_score`` 仅作为研究优先级
  /解释因子；调用方不得据此把候选 tier / action 提升或降级。
- **不输出长篇原文**：``matched_pages_brief`` 每条只携带 ``rel_path / title /
  updated_at / confidence / is_stale / is_low_confidence / summary_snippet``，
  摘要片段裁剪到 ``_SUMMARY_MAX_CHARS``。
- **不调用 LLM / 不访问外网**。
"""

# 单页命中分上限与权重（fresh 命中页才计入分数）。
_PAGE_SCORE_WEIGHTS = {
    "high": 1.0,
    "medium": 0.6,
    "low": 0.3,
}
_LOCAL_KNOWLEDGE_SCORE_MAX = 3.0


def _page_score(match: LocalKnowledgeMatch) -> float:
    """单页命中分：fresh + confidence 决定，过期/低置信/待补充一律 0。"""
    if match.is_stale or match.is_low_confidence or match.is_to_be_supplemented:
        return 0.0
    return _PAGE_SCORE_WEIGHTS.get(match.confidence, 0.0)


def compute_local_knowledge_score(
    result: Optional[LocalKnowledgeQueryResult],
) -> Dict[str, Any]:
    """[KB-004] 把 :class:`LocalKnowledgeQueryResult` 聚合为候选命中分字典。

    返回字段：
      - ``local_knowledge_score``：float，上限 ``_LOCAL_KNOWLEDGE_SCORE_MAX``。
      - ``knowledge_hit_count``：命中页总数（含 stale / low）。
      - ``fresh_hit_count``：fresh 命中页数。
      - ``stale_hit_count``：仅 stale 命中页数。
      - ``low_confidence_hit_count``：低置信/待补充命中页数。
      - ``has_hit``：是否有任何 fresh 命中。
      - ``status``：透传查询状态（``HAS_DATA`` / ``NORMAL_NO_DATA`` / …）。
      - ``confidence``：透传 ``result.confidence``。
      - ``updated_at``：透传 ``result.updated_at``。
      - ``themes``：去重主题列表（前 10 条）。
      - ``risks``：风险提示列表（前 5 条）。
      - ``matched_pages_brief``：命中页精简列表，便于前端展示。
      - ``errors``：查询失败 / 解析失败原因（前 5 条），便于前端排障。
      - ``local_knowledge_summary``：一句话可读摘要，含负面信息。

    参数:
        result: 已计算的 :class:`LocalKnowledgeQueryResult`，或 ``None``。
            传 ``None`` 时返回空命中结构（score=0、hit_count=0），调用方据此
            渲染 NORMAL_NO_DATA 语义。

    返回:
        上述字段的扁平字典。永远不会抛异常。
    """
    empty: Dict[str, Any] = {
        "local_knowledge_score": 0.0,
        "knowledge_hit_count": 0,
        "fresh_hit_count": 0,
        "stale_hit_count": 0,
        "low_confidence_hit_count": 0,
        "has_hit": False,
        "status": STATUS_NORMAL_NO_DATA,
        "confidence": "low",
        "updated_at": None,
        "themes": [],
        "risks": [],
        "matched_pages_brief": [],
        "errors": [],
        "local_knowledge_summary": "",
    }
    if result is None or not isinstance(result, LocalKnowledgeQueryResult):
        return empty

    matched = result.matched_pages or []
    if not matched:
        # 显式保留 status / confidence，方便调用方区分 NORMAL_NO_DATA / FAILED。
        empty["status"] = result.status
        empty["confidence"] = result.confidence
        empty["errors"] = list(result.errors or [])[:5]
        if result.status == STATUS_HAS_DATA:
            # 防御：状态为 HAS_DATA 但 matched 为空时仍视为无命中。
            empty["status"] = STATUS_NORMAL_NO_DATA
        elif result.status == STATUS_FAILED:
            empty["local_knowledge_summary"] = "本地知识查询失败，不参与命中分计算。"
        return empty

    fresh_count = 0
    stale_count = 0
    low_count = 0
    total_score = 0.0
    briefs: List[Dict[str, Any]] = []
    for m in matched:
        if m.is_stale and not (m.is_low_confidence or m.is_to_be_supplemented):
            stale_count += 1
        elif m.is_low_confidence or m.is_to_be_supplemented:
            low_count += 1
        else:
            fresh_count += 1
        total_score += _page_score(m)
        briefs.append({
            "rel_path": m.rel_path,
            "title": m.title,
            "page_type": m.page_type,
            "updated_at": m.updated_at,
            "confidence": m.confidence,
            "is_stale": m.is_stale,
            "is_low_confidence": m.is_low_confidence,
            "is_to_be_supplemented": m.is_to_be_supplemented,
            "summary_snippet": (m.summary or "")[:_SUMMARY_MAX_CHARS],
            "matched_by": list(m.matched_by),
        })

    score = min(total_score, _LOCAL_KNOWLEDGE_SCORE_MAX)
    score = round(score, 2)
    has_hit = fresh_count > 0

    summary = _render_local_knowledge_summary(
        score=score,
        hit_count=len(matched),
        fresh_count=fresh_count,
        stale_count=stale_count,
        low_count=low_count,
        has_hit=has_hit,
        status=result.status,
        themes=result.themes,
        risks=result.risks,
    )

    return {
        "local_knowledge_score": score,
        "knowledge_hit_count": len(matched),
        "fresh_hit_count": fresh_count,
        "stale_hit_count": stale_count,
        "low_confidence_hit_count": low_count,
        "has_hit": has_hit,
        "status": result.status,
        "confidence": result.confidence,
        "updated_at": result.updated_at,
        "themes": list(result.themes)[:10],
        "risks": list(result.risks)[:_MAX_RISK_ENTRIES],
        "matched_pages_brief": briefs[:_MAX_MATCHED_PAGES],
        "errors": list(result.errors or [])[:5],
        "local_knowledge_summary": summary,
    }


def _render_local_knowledge_summary(
    *,
    score: float,
    hit_count: int,
    fresh_count: int,
    stale_count: int,
    low_count: int,
    has_hit: bool,
    status: str,
    themes: List[str],
    risks: List[str],
) -> str:
    """[KB-004] 渲染候选本地知识命中的一句话摘要。

    必须包含负面信息（过期/低置信命中数）；不得包含买卖建议词。
    无命中时返回空字符串（与 KB-008 NORMAL_NO_DATA 语义一致）。
    """
    if not has_hit:
        if status == STATUS_FAILED:
            return "本地知识查询失败，不参与命中分计算。"
        if stale_count and not low_count:
            return (
                f"本地知识仅命中 {stale_count} 条过期页，需更新；"
                f"命中分 0.00。"
            )
        if low_count and not stale_count:
            return (
                f"本地知识仅命中 {low_count} 条低置信/待补充页，仅供参考；"
                f"命中分 0.00。"
            )
        if stale_count and low_count:
            return (
                f"本地知识命中 {stale_count + low_count} 条过期/低置信页，"
                f"不参与命中分计算。"
            )
        return ""

    parts: List[str] = [f"本地知识命中 {hit_count} 条（fresh {fresh_count}）"]
    parts.append(f"命中分 {score:.2f}")
    if themes:
        parts.append("主题：" + "、".join(themes[:3]))
    if stale_count:
        parts.append(f"过期 {stale_count} 条需更新")
    if low_count:
        parts.append(f"低置信 {low_count} 条仅供参考")
    text = "；".join(parts) + "。"
    if risks:
        text += " 风险提示：" + "、".join(risks[:2])
    return text


def needs_tree_work_research(
    *,
    candidate_type: str = "",
    mandate_topic: str = "",
    topic_status: str = "",
    mandate_score: float = 0.0,
    knowledge_summary: Optional[Dict[str, Any]] = None,
) -> bool:
    """[KB-004] 判断候选是否需要标记 ``needs_tree_work_research``。

    触发条件（全部满足）：
      1. 候选属于昊天左侧池（``POLICY_AMBUSH`` / ``POLICY_CONFIRM`` /
         ``EVENT_WATCH``）或携带非空 ``mandate_topic``。
      2. 主题“较热”：``mandate_score > 0``，或 ``topic_status`` 处于升温/
         已确认/加速等正面状态。
      3. 本地知识命中分结构 ``has_hit=False``（无 fresh 命中）。

    任意条件不满足返回 ``False``；该函数**只**生成提示标记，不改变候选 tier。
    """
    mandate_types = {"POLICY_AMBUSH", "POLICY_CONFIRM", "EVENT_WATCH"}
    is_mandate_candidate = (
        (candidate_type or "").strip() in mandate_types
        or bool((mandate_topic or "").strip())
    )
    if not is_mandate_candidate:
        return False

    hot_topic_statuses = {
        "RISING", "CONFIRMED", "ACCELERATING", "HEATING_UP",
        "LEFT_SIDE", "ACTIVE",
    }
    is_hot = (
        (mandate_score or 0.0) > 0.0
        or (topic_status or "").strip().upper() in hot_topic_statuses
    )
    if not is_hot:
        return False

    if not isinstance(knowledge_summary, dict):
        return True
    return not knowledge_summary.get("has_hit", False)
