# [KB-007] research_attention_score
"""多研报重复提及因子 — Research Attention Score。

在 KB-001 只读**审计**、KB-002 契约 **lint**、KB-003 单 symbol 查询之上，本模块
对 Tree Work ``wiki/investment/`` 全量消化页面做**倒排索引**，统计每只标的被多少篇
研报/评分表/主题页重复提及，合成一个中性的"研究关注度"分数，用于候选发现与中线
研究优先级排序。

设计约束（对应任务 KB-007）：
  - **不是买入信号**：分数只表达"被多少研报反复提到"，**不构成**任何买卖建议或
    强动作词；输出文档刻意避免 ``买入/卖出/加仓/减仓/强烈推荐`` 等强动作词。
  - **只读消化后的 wiki**：只使用 ``wiki/investment/`` 已消化页面的 frontmatter 与
    短摘要，**不读取 raw PDF 全文**。
  - **不调用 LLM / 不访问外网**：纯标准库 + 复用 KB-001/KB-002 解析助手。
  - **降权规则**：deprecated（待补充 / ``evidence_level=C``）、过期
    （``valid_until`` 已过期）、``stale_risk=高`` 的页面**只作弱证据**，
    不会提升主候选层级。
  - **资产分类**：A 股 / 港股 / 美股 / 基金 / 未上市主体分开统计，不混成一类。
  - **来源去重**：同一原始资料别名在多页命中时只计一次主权重，避免同源刷分
    （KB-009 在此基础上进一步引入机构级去重，本版按 wiki-link alias 去重）。

合成分数字段（与任务要求对齐）：
  - ``mention_count``：命中页数（含 stale/deprecated）。
  - ``fresh_mention_count``：非 stale 且非 deprecated 的命中页数。
  - ``theme_count``：去重后的主题数。
  - ``source_count``：去重 alias 后的来源数。
  - ``high_quality_mention_count``：``source_quality=高`` 且 ``evidence_level=A`` 的页数。
  - ``stale_mention_count``：``stale_risk=高`` 或 ``valid_until`` 已过期的页数。
  - ``deprecated_mention_count``：待补充 / 低置信（``evidence_level=C``）的页数。
  - ``report_type_distribution``：按 ``report_type`` 分组的命中页数。

合成公式（解释可读、可复现）::

    mention_component      = fresh_mention_count                       # +1.0 / 页
    theme_component        = max(0, theme_count - 1) * 0.5             # 主题交叉奖励
    source_quality_component = high_quality_mention_count * 0.5        # 高质量来源奖励
    freshness_ratio        = fresh_mention_count / max(mention_count, 1)
    stale_penalty          = stale_mention_count * 0.3
    deprecated_penalty     = deprecated_mention_count * 0.5
    duplicate_source_penalty = max(0, raw_source_mentions - source_count) * 0.1
    raw  = (mention_component + theme_component + source_quality_component)
           * freshness_ratio
    score = max(0.0, raw - stale_penalty - deprecated_penalty
                    - duplicate_source_penalty)

使用示例::

    from tradingagents.dataflows.research_attention import (
        compute_research_attention,
        render_research_attention_report,
    )
    result = compute_research_attention("/Users/maybee/Documents/knowledge")
    print(render_research_attention_report(result))
"""

from __future__ import annotations

import os
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 复用 KB-001 / KB-002 / KB-003 的只读解析助手，保持单一解析实现，避免行为分叉。
from tradingagents.dataflows.local_knowledge_audit import (
    INVESTMENT_SUBDIR,
    TODO_MARKERS,
    _audit_single_page,
    _is_nonempty_field,
    _is_valid_until_expired,
    _iter_markdown_files,
    _parse_frontmatter,
    _read_text_safe,
    _safe_str,
    _split_frontmatter,
    default_knowledge_root,
)
from tradingagents.dataflows.local_knowledge_lint import (
    HIGH_STALE_RISK_VALUES,
    LOW_CONFIDENCE_EVIDENCE_LEVELS,
)


# ── 常量 ──────────────────────────────────────────────────────────────

CONTRACT_VERSION = "kb-007-v1"
TASK_CODE = "KB-007"

# 资产类别枚举（与任务"对基金代码、港股、美股、未上市主体与 A 股股票分类型处理"对齐）。
ASSET_CLASS_A_SHARE = "A_SHARE"
ASSET_CLASS_HK = "HK"
ASSET_CLASS_US = "US"
ASSET_CLASS_FUND = "FUND"
ASSET_CLASS_UNLISTED = "UNLISTED"
ASSET_CLASS_OTHER = "OTHER"
ALL_ASSET_CLASSES: Tuple[str, ...] = (
    ASSET_CLASS_A_SHARE,
    ASSET_CLASS_HK,
    ASSET_CLASS_US,
    ASSET_CLASS_FUND,
    ASSET_CLASS_UNLISTED,
    ASSET_CLASS_OTHER,
)

# A 股代码后缀（沪深北交所）。SS 是部分数据源对 SH 的别名，统一识别。
_A_SHARE_SUFFIXES: Tuple[str, ...] = (".SH", ".SZ", ".BJ", ".SS")
_HK_SUFFIXES: Tuple[str, ...] = (".HK",)
_US_SUFFIXES: Tuple[str, ...] = (".US",)

# 6 位 A 股 bare code（与 KB-001 ``_STOCK_CODE_RE`` 口径一致，但这里要求完整 6 位）。
_BARE_A_SHARE_CODE_RE = re.compile(r"^\d{6}$")
# 6 位 A 股代码（用于无后缀兜底识别，前缀 0/3/6/8/9 视作 A 股；4/5 视作基金候选）。
_A_SHARE_PREFIXES: Tuple[str, ...] = ("0", "3", "6", "8", "9")
_FUND_PREFIXES: Tuple[str, ...] = ("1", "4", "5")

# 基金关键词（出现在 tags / themes / report_type / name 时判定为基金）。
_FUND_KEYWORDS: Tuple[str, ...] = ("基金", "ETF", "LOF", "QDII", "指数基金", "联接基金")

# 高质量来源阈值。
HIGH_SOURCE_QUALITY_VALUES: Tuple[str, ...] = ("高", "high", "HIGH")
HIGH_EVIDENCE_LEVEL_VALUES: Tuple[str, ...] = ("A", "a")

# score 组件权重（暴露为模块常量，便于 KB-009 后续调参）。
_W_MENTION = 1.0
_W_THEME = 0.5
_W_HIGH_QUALITY = 0.5
_P_STALE = 0.3
_P_DEPRECATED = 0.5
_P_DUPLICATE_SOURCE = 0.1

# 输出榜单默认上限。
_DEFAULT_TOP = 30
_DEFAULT_DETAIL = 15

# wiki-link ``[[target|alias]]`` 提取 alias 作为来源去重 key。
_WIKI_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


# ── 数据类 ────────────────────────────────────────────────────────────


@dataclass
class PageMention:
    """单页命中记录（只携带元数据，绝不携带大段原文）。"""

    rel_path: str
    title: str
    page_type: str
    report_type: str = ""
    updated_at: Optional[str] = None
    source_quality: str = ""
    evidence_level: str = ""
    is_stale: bool = False
    is_deprecated: bool = False
    is_low_confidence: bool = False
    is_to_be_supplemented: bool = False
    source_aliases: List[str] = field(default_factory=list)
    themes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rel_path": self.rel_path,
            "title": self.title,
            "page_type": self.page_type,
            "report_type": self.report_type,
            "updated_at": self.updated_at,
            "source_quality": self.source_quality,
            "evidence_level": self.evidence_level,
            "is_stale": self.is_stale,
            "is_deprecated": self.is_deprecated,
            "is_low_confidence": self.is_low_confidence,
            "is_to_be_supplemented": self.is_to_be_supplemented,
            "source_aliases": list(self.source_aliases),
            "themes": list(self.themes),
        }


@dataclass
class SymbolAttention:
    """单只标的的研究关注度聚合。"""

    symbol_key: str
    bare_code: str
    name: str
    asset_class: str
    research_attention_score: float = 0.0
    mention_count: int = 0
    fresh_mention_count: int = 0
    theme_count: int = 0
    source_count: int = 0
    high_quality_mention_count: int = 0
    stale_mention_count: int = 0
    deprecated_mention_count: int = 0
    report_type_distribution: Dict[str, int] = field(default_factory=dict)
    themes: List[str] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)
    latest_updated: Optional[str] = None
    matched_pages: List[PageMention] = field(default_factory=list)
    score_explain: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol_key": self.symbol_key,
            "bare_code": self.bare_code,
            "name": self.name,
            "asset_class": self.asset_class,
            "research_attention_score": round(self.research_attention_score, 2),
            "mention_count": self.mention_count,
            "fresh_mention_count": self.fresh_mention_count,
            "theme_count": self.theme_count,
            "source_count": self.source_count,
            "high_quality_mention_count": self.high_quality_mention_count,
            "stale_mention_count": self.stale_mention_count,
            "deprecated_mention_count": self.deprecated_mention_count,
            "report_type_distribution": dict(self.report_type_distribution),
            "themes": list(self.themes),
            "sources": list(self.sources),
            "latest_updated": self.latest_updated,
            "matched_pages": [p.to_dict() for p in self.matched_pages],
            "score_explain": list(self.score_explain),
        }


@dataclass
class ResearchAttentionResult:
    """整库研究关注度聚合结果。"""

    knowledge_root: str
    scanned_at: str
    as_of_date: str
    contract_version: str = CONTRACT_VERSION
    task: str = TASK_CODE
    investment_page_count: int = 0
    page_with_symbols_count: int = 0
    symbol_count: int = 0
    asset_class_counts: Dict[str, int] = field(default_factory=dict)
    symbols: List[SymbolAttention] = field(default_factory=list)
    top_symbols: List[SymbolAttention] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "knowledge_root": self.knowledge_root,
            "scanned_at": self.scanned_at,
            "as_of_date": self.as_of_date,
            "contract_version": self.contract_version,
            "task": self.task,
            "investment_page_count": self.investment_page_count,
            "page_with_symbols_count": self.page_with_symbols_count,
            "symbol_count": self.symbol_count,
            "asset_class_counts": dict(self.asset_class_counts),
            "symbols": [s.to_dict() for s in self.symbols],
            "top_symbols": [s.to_dict() for s in self.top_symbols],
            "errors": list(self.errors),
        }


# ── 解析辅助 ──────────────────────────────────────────────────────────


def _normalize_str_list(raw: Any) -> List[str]:
    """把 frontmatter 任意字段归一为去空字符串列表。"""
    if raw is None:
        return []
    if isinstance(raw, str):
        text = raw.strip()
        return [text] if text else []
    if isinstance(raw, (list, tuple)):
        out: List[str] = []
        for x in raw:
            if x is None:
                continue
            text = str(x).strip().strip('"').strip("'")
            if text:
                out.append(text)
        return out
    return []


def _strip_wiki_link(raw: str) -> str:
    """``[[target|alias]]`` 取 alias；``[[target]]`` 取 target 末段；普通文本直通。"""
    text = str(raw).strip()
    if not text:
        return ""
    m = _WIKI_LINK_RE.search(text)
    if m:
        inner = m.group(1)
        if "|" in inner:
            return inner.split("|", 1)[1].strip()
        # 无 alias，取最后一段文件名（去 .md）。
        tail = inner.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        return tail.strip()
    return text


def _dedup_preserve_order(items: List[str]) -> List[str]:
    seen: List[str] = []
    for it in items:
        if it and it not in seen:
            seen.append(it)
    return seen


def _split_symbol_entry(entry: str) -> Tuple[str, str, str]:
    """``603296.SH 华勤技术`` → ``(code_with_suffix, bare_code, name)``。

    - code_with_suffix: ``603296.SH``（保留交易所后缀，大写）。
    - bare_code: ``603296``（去后缀）。
    - name: ``华勤技术``（去首尾空白）。
    """
    parts = str(entry).strip().split(maxsplit=1)
    code = parts[0] if parts else ""
    name = parts[1].strip() if len(parts) > 1 else ""
    bare = re.sub(r"\.(SH|SZ|BJ|HK|US|SS)$", "", code, flags=re.IGNORECASE).strip()
    code_norm = code.upper() if code else ""
    return code_norm, bare, name


def classify_asset_class(
    code_with_suffix: str,
    bare_code: str,
    name: str,
    frontmatter: Dict[str, Any],
) -> str:
    """按后缀 + 代码前缀 + 上下文关键词识别资产类别。

    优先级：HK / US 后缀 → 基金关键词（覆盖 .SH/.SZ/.BJ 后缀的 ETF/LOF）→
    A 股后缀 → A 股前缀 → 基金前缀 → 未上市。

    例：``510300.SH 沪深300ETF`` 因 tags/name 含 ``ETF`` 而归类为 FUND，
    而非 A_SHARE；``603296.SH 华勤技术`` 因无基金关键词归类为 A_SHARE。
    """
    code_upper = code_with_suffix.upper()
    if any(code_upper.endswith(s) for s in _HK_SUFFIXES):
        return ASSET_CLASS_HK
    if any(code_upper.endswith(s) for s in _US_SUFFIXES):
        return ASSET_CLASS_US

    # 上下文关键词：基金关键词命中优先于 A 股后缀，避免 ETF/LOF 被混入 A 股。
    tags = _normalize_str_list(frontmatter.get("tags"))
    themes = _normalize_str_list(frontmatter.get("themes"))
    report_type = _safe_str(frontmatter.get("report_type")) or ""
    context_blob = " ".join([name, report_type] + tags + themes).lower()
    is_fund_context = any(kw.lower() in context_blob for kw in _FUND_KEYWORDS)

    if is_fund_context:
        return ASSET_CLASS_FUND

    if any(code_upper.endswith(s) for s in _A_SHARE_SUFFIXES):
        return ASSET_CLASS_A_SHARE

    # 后缀缺失时按代码前缀兜底。
    if _BARE_A_SHARE_CODE_RE.match(bare_code):
        if bare_code[:1] in _FUND_PREFIXES:
            return ASSET_CLASS_FUND
        if bare_code[:1] in _A_SHARE_PREFIXES:
            return ASSET_CLASS_A_SHARE

    # 既无标准后缀，也无 6 位数字代码 —— 视为未上市主体（如私募/海外未代码化主体）。
    # 有 name 但无 code 也归 UNLISTED（其它资本市场的主体），只有完全无信息才 OTHER。
    if bare_code or name:
        return ASSET_CLASS_UNLISTED
    return ASSET_CLASS_OTHER


def _build_page_mention(
    rel_path: str,
    abs_path: Path,
    page_audit: Any,
    frontmatter: Dict[str, Any],
) -> PageMention:
    """从单页构造一条 PageMention（只读，不输出原文）。"""
    fm_sources = _normalize_str_list(frontmatter.get("sources"))
    source_aliases = _dedup_preserve_order(
        [_strip_wiki_link(s) for s in fm_sources]
    )
    themes = _dedup_preserve_order(_normalize_str_list(frontmatter.get("themes")))

    stale_val = _safe_str(frontmatter.get("stale_risk")) or ""
    is_stale = stale_val in HIGH_STALE_RISK_VALUES or page_audit.valid_until_expired
    evidence = _safe_str(frontmatter.get("evidence_level")) or ""
    is_low_conf = evidence in LOW_CONFIDENCE_EVIDENCE_LEVELS
    is_todo = page_audit.is_to_be_supplemented

    # deprecated：待补充 / 低置信，与 stale 分开统计（可能同时为真）。
    is_deprecated = is_todo or is_low_conf

    return PageMention(
        rel_path=rel_path,
        title=page_audit.title or abs_path.stem,
        page_type=page_audit.page_type,
        report_type=_safe_str(frontmatter.get("report_type")) or "",
        updated_at=_safe_str(frontmatter.get("updated")),
        source_quality=_safe_str(frontmatter.get("source_quality")) or "",
        evidence_level=evidence,
        is_stale=is_stale,
        is_deprecated=is_deprecated,
        is_low_confidence=is_low_conf,
        is_to_be_supplemented=is_todo,
        source_aliases=source_aliases,
        themes=themes,
    )


def _is_high_quality(mention: PageMention) -> bool:
    return (
        mention.source_quality in HIGH_SOURCE_QUALITY_VALUES
        and mention.evidence_level in HIGH_EVIDENCE_LEVEL_VALUES
    )


def _is_fresh(mention: PageMention) -> bool:
    return not (mention.is_stale or mention.is_deprecated)


# ── 分数合成 ──────────────────────────────────────────────────────────


def _compose_score(sym: SymbolAttention) -> Tuple[float, List[str]]:
    """合成 research_attention_score，返回 (score, explain 列表)。

    explain 字段对每个加减分项给出可读解释，便于 KB-008/KB-009 在前端展示。
    """
    explain: List[str] = []

    mention_component = sym.fresh_mention_count * _W_MENTION
    if mention_component:
        explain.append(
            f"fresh_mention={sym.fresh_mention_count} × {_W_MENTION} "
            f"= +{mention_component:.2f}"
        )

    extra_themes = max(0, sym.theme_count - 1)
    theme_component = extra_themes * _W_THEME
    if theme_component:
        explain.append(
            f"theme_cross={extra_themes} × {_W_THEME} = +{theme_component:.2f}"
        )

    high_q_component = sym.high_quality_mention_count * _W_HIGH_QUALITY
    if high_q_component:
        explain.append(
            f"high_quality={sym.high_quality_mention_count} × {_W_HIGH_QUALITY} "
            f"= +{high_q_component:.2f}"
        )

    base = mention_component + theme_component + high_q_component

    freshness_ratio = (
        sym.fresh_mention_count / sym.mention_count if sym.mention_count else 0.0
    )
    if sym.mention_count and freshness_ratio < 1.0:
        explain.append(
            f"freshness_ratio={sym.fresh_mention_count}/{sym.mention_count} "
            f"= {freshness_ratio:.2f} (整体按比例缩放)"
        )
    base *= freshness_ratio

    stale_penalty = sym.stale_mention_count * _P_STALE
    if stale_penalty:
        explain.append(
            f"stale={sym.stale_mention_count} × {_P_STALE} = -{stale_penalty:.2f}"
        )

    deprecated_penalty = sym.deprecated_mention_count * _P_DEPRECATED
    if deprecated_penalty:
        explain.append(
            f"deprecated={sym.deprecated_mention_count} × {_P_DEPRECATED} "
            f"= -{deprecated_penalty:.2f}"
        )

    # 同一 alias 在多页命中视作重复来源，每多一次出现扣 0.1。
    raw_source_mentions = sum(len(p.source_aliases) for p in sym.matched_pages)
    dup_count = max(0, raw_source_mentions - sym.source_count)
    duplicate_source_penalty = dup_count * _P_DUPLICATE_SOURCE
    if duplicate_source_penalty:
        explain.append(
            f"duplicate_source={dup_count} × {_P_DUPLICATE_SOURCE} "
            f"= -{duplicate_source_penalty:.2f}"
        )

    score = max(
        0.0,
        base - stale_penalty - deprecated_penalty - duplicate_source_penalty,
    )
    return score, explain


# ── 主索引逻辑 ────────────────────────────────────────────────────────


def compute_research_attention(knowledge_root: str) -> ResearchAttentionResult:
    """扫描 ``wiki/investment/`` 建立 symbol 倒排索引，合成 research_attention_score。

    参数:
        knowledge_root: 知识库根目录绝对路径。

    返回:
        :class:`ResearchAttentionResult`。永远不会因单页解析失败而抛异常：
        单页失败记入 ``errors`` 并跳过；知识库不存在时返回空结果。
    """
    today = date.today()
    root = Path(knowledge_root).expanduser()
    result = ResearchAttentionResult(
        knowledge_root=str(root),
        scanned_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        as_of_date=today.strftime("%Y-%m-%d"),
    )

    if not root.exists():
        result.errors.append(f"knowledge_root 不存在: {root}")
        return result

    investment_dir = root / INVESTMENT_SUBDIR
    md_files = _iter_markdown_files(investment_dir)
    if not md_files:
        if not investment_dir.exists():
            result.errors.append(f"investment 分区不存在: {investment_dir}")
        return result

    # ── 1. 逐页解析，建立倒排索引 symbol_key -> PageMention[] ──
    # 同一 (code, name) 组合作为 symbol_key；同一页面可能命中多个 symbol。
    inverted: Dict[str, SymbolAttention] = {}
    page_with_symbols = 0

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

        result.investment_page_count += 1

        symbols_raw = _normalize_str_list(frontmatter.get("symbols"))
        if not symbols_raw:
            # 无 symbols 字段的页面不进入倒排索引（与 KB-003 命中口径一致）。
            continue

        page_with_symbols += 1
        try:
            mention = _build_page_mention(rel, md, page_audit, frontmatter)
        except Exception as exc:  # pragma: no cover
            result.errors.append(f"{rel}: 构造命中失败 {exc!r}")
            continue

        for entry in symbols_raw:
            code_norm, bare, name = _split_symbol_entry(entry)
            if not code_norm and not name:
                continue
            asset_class = classify_asset_class(
                code_norm, bare, name, frontmatter
            )
            # symbol_key 优先用规范化的 code；缺 code 时用 name 兜底。
            symbol_key = code_norm or name
            sym = inverted.get(symbol_key)
            if sym is None:
                sym = SymbolAttention(
                    symbol_key=symbol_key,
                    bare_code=bare,
                    name=name,
                    asset_class=asset_class,
                )
                inverted[symbol_key] = sym
            else:
                # 同一 code 出现在多页时，name 取首个非空；asset_class 取首个识别值。
                if not sym.name and name:
                    sym.name = name
                if sym.asset_class == ASSET_CLASS_OTHER and asset_class != ASSET_CLASS_OTHER:
                    sym.asset_class = asset_class
            sym.matched_pages.append(mention)

    result.page_with_symbols_count = page_with_symbols

    # ── 2. 聚合每只标的的字段 ──
    for sym in inverted.values():
        _aggregate_symbol(sym)

    # ── 3. 排序：按分数降序，分数相同按 mention_count → theme_count → symbol_key ──
    all_symbols = list(inverted.values())
    all_symbols.sort(
        key=lambda s: (
            -s.research_attention_score,
            -s.mention_count,
            -s.theme_count,
            s.symbol_key,
        )
    )
    result.symbols = all_symbols
    result.top_symbols = all_symbols[:_DEFAULT_TOP]
    result.symbol_count = len(all_symbols)

    asset_counts: Dict[str, int] = {ac: 0 for ac in ALL_ASSET_CLASSES}
    for sym in all_symbols:
        asset_counts[sym.asset_class] = asset_counts.get(sym.asset_class, 0) + 1
    result.asset_class_counts = asset_counts

    return result


def _aggregate_symbol(sym: SymbolAttention) -> None:
    """聚合单标的的 mention / fresh / theme / source / stale / deprecated / score 字段。"""
    pages = sym.matched_pages
    if not pages:
        return

    sym.mention_count = len(pages)
    sym.fresh_mention_count = sum(1 for p in pages if _is_fresh(p))
    sym.high_quality_mention_count = sum(1 for p in pages if _is_high_quality(p))
    sym.stale_mention_count = sum(1 for p in pages if p.is_stale)
    sym.deprecated_mention_count = sum(1 for p in pages if p.is_deprecated)

    themes_all: List[str] = []
    sources_all: List[str] = []
    report_type_dist: Dict[str, int] = {}
    latest: Optional[str] = None
    for p in pages:
        themes_all.extend(p.themes)
        sources_all.extend(p.source_aliases)
        rtype = p.report_type or "未分类"
        report_type_dist[rtype] = report_type_dist.get(rtype, 0) + 1
        if p.updated_at:
            if latest is None or p.updated_at > latest:
                latest = p.updated_at

    sym.themes = _dedup_preserve_order(themes_all)[:20]
    sym.sources = _dedup_preserve_order(sources_all)[:20]
    sym.theme_count = len(sym.themes)
    sym.source_count = len(sym.sources)
    sym.report_type_distribution = report_type_dist
    sym.latest_updated = latest

    score, explain = _compose_score(sym)
    sym.research_attention_score = score
    sym.score_explain = explain


# ── 报告渲染 ──────────────────────────────────────────────────────────


def render_research_attention_report(
    result: ResearchAttentionResult,
    *,
    top: int = _DEFAULT_TOP,
    detail: int = _DEFAULT_DETAIL,
) -> str:
    """渲染 Markdown 报告（中性表述，不含买卖建议或强动作词）。"""
    lines: List[str] = []
    lines.append(
        f"# [{TASK_CODE}] 多研报重复提及因子 — Research Attention Score"
    )
    lines.append(f"_{result.as_of_date}_")
    lines.append("")
    lines.append(
        "> 仅表征“被多少篇研报/评分表/主题页重复提及”，**不构成买卖建议或强动作词**。"
        "deprecated / 过期 / 低置信页面已降权为弱证据。"
    )
    lines.append("")

    # 1. 扫描概览
    lines.append("## 1. 扫描概览")
    lines.append("")
    lines.append(f"- knowledge_root: `{result.knowledge_root}`")
    lines.append(f"- scanned_at: {result.scanned_at}")
    lines.append(f"- as_of_date: {result.as_of_date}")
    lines.append(f"- contract_version: `{result.contract_version}`")
    lines.append(
        f"- investment_md_pages: **{result.investment_page_count}** "
        f"(其中 frontmatter 含 symbols 字段的页面 **{result.page_with_symbols_count}**)"
    )
    lines.append(f"- 命中独立标的数: **{result.symbol_count}**")
    if result.errors:
        lines.append("")
        lines.append(f"- ⚠️ 扫描错误 ({len(result.errors)}):")
        for err in result.errors[:10]:
            lines.append(f"  - {err}")
    lines.append("")

    # 2. 资产类别分布
    lines.append("## 2. 资产类别分布")
    lines.append("")
    if any(result.asset_class_counts.get(ac, 0) for ac in ALL_ASSET_CLASSES):
        lines.append("| asset_class | 数量 | 说明 |")
        lines.append("|-------------|------|------|")
        desc = {
            ASSET_CLASS_A_SHARE: "A 股（.SH/.SZ/.BJ/.SS）",
            ASSET_CLASS_HK: "港股（.HK）",
            ASSET_CLASS_US: "美股（.US）",
            ASSET_CLASS_FUND: "基金 / ETF / LOF",
            ASSET_CLASS_UNLISTED: "未上市主体 / 私募 / 海外未代码化",
            ASSET_CLASS_OTHER: "其它（无法识别）",
        }
        for ac in ALL_ASSET_CLASSES:
            cnt = result.asset_class_counts.get(ac, 0)
            if cnt:
                lines.append(f"| {ac} | {cnt} | {desc.get(ac, '')} |")
    else:
        lines.append("_无命中标的（fixture 或空知识库）_")
    lines.append("")

    # 3. Top symbols 表
    lines.append(f"## 3. Top 研究关注度标的（按 research_attention_score 排序，前 {top}）")
    lines.append("")
    top_syms = result.symbols[:top]
    if top_syms:
        lines.append(
            "| 排名 | symbol_key | 名称 | asset_class | score | fresh | mention | "
            "theme | source | high_q | stale | deprec |"
        )
        lines.append("|------|------------|------|-------------|-------|-------|---------|"
            "-------|--------|--------|-------|--------|")
        for idx, sym in enumerate(top_syms, 1):
            lines.append(
                f"| {idx} | `{sym.symbol_key}` | {sym.name or '-'} | {sym.asset_class} | "
                f"{sym.research_attention_score:.2f} | {sym.fresh_mention_count} | "
                f"{sym.mention_count} | {sym.theme_count} | {sym.source_count} | "
                f"{sym.high_quality_mention_count} | {sym.stale_mention_count} | "
                f"{sym.deprecated_mention_count} |"
            )
    else:
        lines.append("_无命中标的_")
    lines.append("")

    # 4. 详细明细
    detail_syms = result.symbols[:detail]
    if detail_syms:
        lines.append(f"## 4. 重点标的明细（前 {detail}）")
        lines.append("")
        for sym in detail_syms:
            lines.append(f"### `{sym.symbol_key}` — {sym.name or '未命名'}")
            lines.append("")
            lines.append(
                f"- asset_class: **{sym.asset_class}** ｜ "
                f"score: **{sym.research_attention_score:.2f}** ｜ "
                f"latest_updated: `{sym.latest_updated or '-'}`"
            )
            lines.append(
                f"- mention={sym.mention_count} / fresh={sym.fresh_mention_count} / "
                f"stale={sym.stale_mention_count} / deprecated={sym.deprecated_mention_count} / "
                f"high_quality={sym.high_quality_mention_count}"
            )
            if sym.themes:
                lines.append(f"- themes ({sym.theme_count}): {', '.join(sym.themes[:10])}")
            if sym.sources:
                lines.append(
                    f"- sources ({sym.source_count}): {', '.join(sym.sources[:8])}"
                )
            if sym.report_type_distribution:
                rt = ", ".join(
                    f"{k}×{v}" for k, v in sorted(
                        sym.report_type_distribution.items(), key=lambda kv: (-kv[1], kv[0])
                    )
                )
                lines.append(f"- report_type_distribution: {rt}")
            if sym.score_explain:
                lines.append("- score_explain:")
                for line in sym.score_explain:
                    lines.append(f"  - {line}")
            if sym.matched_pages:
                lines.append(f"- matched_pages ({len(sym.matched_pages)}):")
                for p in sym.matched_pages[:10]:
                    flags: List[str] = []
                    if p.is_stale:
                        flags.append("STALE")
                    if p.is_deprecated:
                        flags.append("DEPRECATED")
                    if p.is_low_confidence:
                        flags.append("LOW_CONFIDENCE")
                    if p.is_to_be_supplemented:
                        flags.append("TODO")
                    flag_text = f" [{', '.join(flags)}]" if flags else ""
                    lines.append(
                        f"  - `{p.rel_path}` ({p.page_type}/{p.report_type or '-'})"
                        f"{flag_text}"
                    )
            lines.append("")

    # 5. 计分口径
    lines.append("## 5. 计分口径")
    lines.append("")
    lines.append("```")
    lines.append("mention_component        = fresh_mention_count × 1.0")
    lines.append("theme_component          = max(0, theme_count - 1) × 0.5")
    lines.append("source_quality_component = high_quality_mention_count × 0.5")
    lines.append("freshness_ratio          = fresh_mention_count / max(mention_count, 1)")
    lines.append("raw  = (mention + theme + source_quality) × freshness_ratio")
    lines.append("penalty = stale×0.3 + deprecated×0.5 + duplicate_source×0.1")
    lines.append("score = max(0, raw - penalty)")
    lines.append("```")
    lines.append("")
    lines.append(
        "- **fresh**：非 stale 且非 deprecated 的命中页。"
        "- **deprecated**：待补充标记 或 `evidence_level=C`。"
        "- **stale**：`stale_risk=高` 或 `valid_until` 已过期。"
        "- **duplicate_source**：同一 wiki-link alias 在多页重复出现。"
    )
    lines.append("")

    # 6. 免责
    lines.append("## 6. 免责声明")
    lines.append("")
    lines.append(
        "- 本报告仅作为**研究关注度**与**候选研究优先级**因子，不构成任何买卖建议；"
        "不与行情、公告、财务、资金流信号混用。"
    )
    lines.append(
        "- 过期 / 低置信 / 待补充页面已显式降权，不会提升主候选层级。"
    )
    lines.append(
        "- 港股 / 美股 / 基金 / 未上市主体与 A 股分类型展示，不混合排序加总。"
    )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        f"_由 `scripts/run_research_attention.py` 只读生成；"
        f"对应模块 `tradingagents.dataflows.research_attention`。_"
    )
    lines.append("")
    return "\n".join(lines)


# ── 单 symbol 查询（KB-008 接入 TA 报告 / TradeFlow 候选）──────────────


# 与 KB-003 local_knowledge_provider 命中口径一致：去后缀比较 bare code。
_SUFFIX_RE_KB008 = re.compile(r"\.(SH|SZ|BJ|HK|US|SS)$", re.IGNORECASE)


def _symbols_equivalent(query_symbol: str, sym_key: str, bare_code: str) -> bool:
    """判断查询 symbol 与倒排索引 symbol_key 是否指同一标的。

    - ``603296`` 命中 ``603296.SH``（bare code 相等）。
    - ``603296.SH`` 命中 ``603296.SH``（去后缀比较 + 全字符串比较）。
    - 大小写不敏感。
    """
    if not query_symbol or not sym_key:
        return False
    q = query_symbol.strip().upper()
    s = sym_key.strip().upper()
    if q == s:
        return True
    q_bare = _SUFFIX_RE_KB008.sub("", q).strip()
    s_bare = _SUFFIX_RE_KB008.sub("", s).strip()
    if q_bare and s_bare and q_bare == s_bare:
        return True
    # bare_code 字段兜底（sym_key 可能是 name 形态）。
    if bare_code and q_bare and q_bare == bare_code.upper():
        return True
    return False


def lookup_research_attention(
    knowledge_root: str,
    symbol: Optional[str],
) -> Optional[SymbolAttention]:
    """[KB-008] 单 symbol 查询研究关注度。

    复用 :func:`compute_research_attention` 的全库倒排索引，按 symbol 命中
    返回对应的 :class:`SymbolAttention`。无命中返回 ``None``。

    设计约束（与 KB-007 一致）：
      - **只读**：绝不向知识库写文件。
      - **不调用 LLM / 不访问外网**。
      - **非买卖信号**：返回值只表达"被多少研报反复提到"，调用方（TA 报告 /
        TradeFlow）必须把它当作**研究优先级/解释信息**，不得直接改变交易动作。

    参数:
        knowledge_root: 知识库根目录绝对路径。
        symbol: A 股代码或代码+后缀（如 ``603296`` / ``603296.SH``）。

    返回:
        :class:`SymbolAttention` 或 ``None``。永远不会因单页解析失败而抛异常：
        异常向上层传播由调用方决定是否容错。
    """
    symbol = (symbol or "").strip()
    if not symbol:
        return None
    result = compute_research_attention(knowledge_root)
    for sym in result.symbols:
        if _symbols_equivalent(symbol, sym.symbol_key, sym.bare_code):
            return sym
    return None


def attention_to_summary(sym: Optional[SymbolAttention]) -> Dict[str, Any]:
    """[KB-008] 把单 symbol 关注度聚合为前端/UI 可直接展示的扁平字典。

    无命中（``sym is None``）时返回空命中结构，调用方据此渲染 NORMAL_NO_DATA。

    返回字段（与任务要求对齐）：
      - ``research_attention_score``：综合关注度（保留 2 位小数）。
      - ``knowledge_theme_count``：去重主题数。
      - ``mention_count``：命中篇数（含 stale/deprecated）。
      - ``fresh_mention_count``：非 stale/deprecated 的命中篇数。
      - ``high_quality_mention_count``：``source_quality=高`` 且 ``evidence_level=A``
        的命中篇数。
      - ``stale_mention_count``：``stale_risk=高`` 或 ``valid_until`` 已过期的页数。
      - ``deprecated_mention_count``：待补充 / 低置信页数。
      - ``source_count``：去重 alias 后的来源数。
      - ``themes``：主题列表（前 10 个）。
      - ``sources``：来源列表（前 8 个）。
      - ``latest_updated``：最近更新时间。
      - ``matched_pages``：命中页相对路径与标题列表（前 5 条，便于观察仓展示）。
      - ``score_explain``：分数构成可读解释（前 6 条）。
      - ``research_attention_summary``：一句话可读摘要，含负面信息（过期 / 低置信）。
      - ``has_hit``：是否命中（False 时其余字段为空结构）。
    """
    if sym is None:
        return {
            "has_hit": False,
            "research_attention_score": 0.0,
            "knowledge_theme_count": 0,
            "mention_count": 0,
            "fresh_mention_count": 0,
            "high_quality_mention_count": 0,
            "stale_mention_count": 0,
            "deprecated_mention_count": 0,
            "source_count": 0,
            "themes": [],
            "sources": [],
            "latest_updated": None,
            "matched_pages": [],
            "score_explain": [],
            "research_attention_summary": "",
        }

    matched_pages = [
        {"rel_path": p.rel_path, "title": p.title}
        for p in sym.matched_pages[:5]
    ]
    summary = _render_attention_summary(sym)
    return {
        "has_hit": True,
        "research_attention_score": round(sym.research_attention_score, 2),
        "knowledge_theme_count": sym.theme_count,
        "mention_count": sym.mention_count,
        "fresh_mention_count": sym.fresh_mention_count,
        "high_quality_mention_count": sym.high_quality_mention_count,
        "stale_mention_count": sym.stale_mention_count,
        "deprecated_mention_count": sym.deprecated_mention_count,
        "source_count": sym.source_count,
        "themes": list(sym.themes[:10]),
        "sources": list(sym.sources[:8]),
        "latest_updated": sym.latest_updated,
        "matched_pages": matched_pages,
        "score_explain": list(sym.score_explain[:6]),
        "research_attention_summary": summary,
    }


def _render_attention_summary(sym: SymbolAttention) -> str:
    """渲染一句话摘要：综合关注度 + 命中篇数 + 主题交叉 + 负面信息。

    刻意同时展示 stale / deprecated 数量，满足任务约束"前端展示必须同时显示
    负面信息：过期数、低置信数、主题是否拥挤"。**不输出买卖建议或强动作词**。
    """
    parts: List[str] = []
    parts.append(f"综合关注度 {sym.research_attention_score:.2f}")
    parts.append(
        f"命中 {sym.mention_count} 篇（fresh {sym.fresh_mention_count}"
        f" / 高质量 {sym.high_quality_mention_count}）"
    )
    if sym.theme_count:
        parts.append(f"主题交叉 {sym.theme_count}")
    if sym.source_count:
        parts.append(f"来源 {sym.source_count}")
    # 负面信息（必须显示，不能省略）。
    negatives: List[str] = []
    if sym.stale_mention_count:
        negatives.append(f"过期 {sym.stale_mention_count}")
    if sym.deprecated_mention_count:
        negatives.append(f"低置信/待补充 {sym.deprecated_mention_count}")
    if sym.theme_count >= 6:
        negatives.append("主题较拥挤")
    if negatives:
        parts.append("负面：" + "、".join(negatives))
    return "；".join(parts) + "。"


def render_research_attention_inline(sym: Optional[SymbolAttention]) -> str:
    """[KB-008] 渲染嵌入"本地知识补充"区块的研究关注度段。

    - ``sym is None``：返回空串，上层可选择隐藏段落。
    - 有命中：渲染一段 Markdown，含综合关注度、命中篇数、主题交叉、来源质量、
      过期/低置信数、命中页相对路径（前 5 条）。**不输出买卖建议或强动作词**。

    与 :func:`render_research_attention_report`（整库 Markdown 报告）不同，
    本函数只渲染单 symbol 的简短摘要，便于嵌入 TA 报告 / TradeFlow 候选详情。
    """
    if sym is None:
        return ""

    lines: List[str] = []
    lines.append("**研报关注度（多研报重复提及因子）**")
    lines.append("")
    lines.append(
        '> 仅表征「被多少篇研报/评分表/主题页重复提及」，'
        '**不构成买卖建议**；过期 / 低置信页面已降权为弱证据。'
    )
    lines.append("")
    lines.append(
        f"- 综合关注度：**{sym.research_attention_score:.2f}** ｜ "
        f"asset_class: {sym.asset_class}"
    )
    lines.append(
        f"- 命中篇数：{sym.mention_count}（fresh {sym.fresh_mention_count} / "
        f"高质量 {sym.high_quality_mention_count} / "
        f"过期 {sym.stale_mention_count} / "
        f"低置信/待补充 {sym.deprecated_mention_count}）"
    )
    if sym.theme_count:
        lines.append(f"- 主题交叉（{sym.theme_count}）：{', '.join(sym.themes[:6])}")
    if sym.source_count:
        lines.append(f"- 来源（{sym.source_count}）：{', '.join(sym.sources[:6])}")
    if sym.latest_updated:
        lines.append(f"- 最近更新：`{sym.latest_updated}`")
    if sym.matched_pages:
        lines.append(f"- 命中页（前 5 / 共 {len(sym.matched_pages)}）：")
        for p in sym.matched_pages[:5]:
            flags: List[str] = []
            if p.is_stale:
                flags.append("STALE")
            if p.is_deprecated:
                flags.append("DEPRECATED")
            if p.is_low_confidence:
                flags.append("LOW_CONFIDENCE")
            if p.is_to_be_supplemented:
                flags.append("TODO")
            flag_text = f" [{', '.join(flags)}]" if flags else ""
            lines.append(f"  - `{p.rel_path}`{flag_text}")
    if sym.score_explain:
        lines.append("- 分数构成：")
        for line in sym.score_explain[:6]:
            lines.append(f"  - {line}")
    lines.append("")
    return "\n".join(lines)


# ── CLI 便利 ──────────────────────────────────────────────────────────


def suggest_report_output_path(
    docs_dir: str = "docs/knowledge_reports",
    ext: str = ".md",
) -> str:
    """生成默认输出路径 ``docs/knowledge_reports/research_attention-YYYY-MM-DD.<ext>``。"""
    today = date.today().strftime("%Y-%m-%d")
    return os.path.join(docs_dir, f"research_attention-{today}{ext}")


__all__ = [
    "ALL_ASSET_CLASSES",
    "ASSET_CLASS_A_SHARE",
    "ASSET_CLASS_HK",
    "ASSET_CLASS_US",
    "ASSET_CLASS_FUND",
    "ASSET_CLASS_UNLISTED",
    "ASSET_CLASS_OTHER",
    "CONTRACT_VERSION",
    "TASK_CODE",
    "PageMention",
    "SymbolAttention",
    "ResearchAttentionResult",
    "classify_asset_class",
    "compute_research_attention",
    "lookup_research_attention",
    "attention_to_summary",
    "render_research_attention_report",
    "suggest_report_output_path",
]
