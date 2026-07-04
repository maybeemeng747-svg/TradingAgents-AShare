# [KB-012] tree_work_task_pack
"""Tree Work 研报补录任务包导出。

在 KB-001 只读审计、KB-002 契约 lint、KB-005 inbox/raw/wiki backlog、KB-007/009
研究关注度与时效衰减之上，把分散在各模块的"需要 Tree Work 回补"的信号**合并为一份
可执行的补录任务包**，导出为 Markdown/JSON，方便用户/HR Agent 直接逐项消化研报。

设计约束（对应任务 KB-012）：
  - **只生成任务包**：不修改 knowledge，不写任何知识库文件。
  - **不复制研报长文本**：每条任务只携带路径 / 类别 / 建议动作 / 简短原因，绝不
    携带原文段落。
  - **不输出交易建议**：任务包面向 Tree Work ingest 流程，按"缺什么字段"分组，
    给出 ingest 模板与字段要求，不给买卖动作或强动作词。
  - **不调用 LLM / 不访问外网**：纯标准库 + 复用 KB-002/KB-005/KB-007/KB-009
    的只读解析能力。
  - **可空库运行**：知识库缺失或空库时返回稳定空结构，绝不抛异常。

任务分组（``TaskGroup``，按任务要求"缺 symbol/name / 缺 thesis / 缺 risks /
过期需复核 / 热门但证据薄"对齐）：
  - ``missing_symbol``：缺 symbols/name（KB-002 FMR-002/SYM-001 + KB-005 fill_fields）。
  - ``missing_thesis``：缺一句话总结/核心观点/投资逻辑章节（KB-002 SEC-001/SEC-004
    + KB-005 fill_summary）。
  - ``missing_risks``：缺风险提示章节（KB-002 SEC-002 + KB-005 fill_risks）。
  - ``missing_sources``：缺 sources/原始资料章节（KB-002 FMR-001/SEC-003 +
    KB-005 fill_source_links + raw 未消化）。
  - ``needs_review_stale``：过期/高 stale_risk/低置信页面需回 Tree Work 复核
    （KB-002 STALE-001/STALE-002/EVID-001 + KB-007 stale_mention +
    KB-009 expired_fresh）。
  - ``hot_but_thin``：研究关注度高但证据薄/主题拥挤的页面（KB-007 高分 +
    KB-009 high_stale / theme_crowding）。
  - ``ingest_new``：inbox 未消化 + raw 未消化，需要新建 wiki 页面（KB-005 ingest）。

每条 ``TaskPackItem`` 必含字段：
  - ``group``：上述分组枚举。
  - ``location``：相对知识库根目录的路径（或 raw/inbox 文件名）。
  - ``sources``：来自哪些上游信号（如 ``["KB-002:SEC-001", "KB-005:fill_summary"]``）。
  - ``suggested_action``：建议 Tree Work 采取的动作（与 KB-005 动作语义一致）。
  - ``detail``：简短原因（不超过 2 句）。
  - ``priority``：high/medium/low，由 source 模块的规则继承而来。
  - ``extra``：可选结构化字段（如 rule_id / theme / matched_symbol）。

使用示例::

    from tradingagents.dataflows.tree_work_task_pack import (
        build_tree_work_task_pack,
        render_task_pack_report,
    )
    pack = build_tree_work_task_pack("/Users/maybee/Documents/knowledge")
    print(render_task_pack_report(pack))
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 复用 KB-002 / KB-005 / KB-007 / KB-009 只读解析能力，保持单一解析实现。
from tradingagents.dataflows.local_knowledge_audit import default_knowledge_root
from tradingagents.dataflows.local_knowledge_lint import (
    KnowledgeLintResult,
    PageLintResult,
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    lint_local_knowledge,
)
from tradingagents.dataflows.research_attention import (
    ResearchAttentionResult,
    SymbolAttention,
    compute_research_attention,
)
from tradingagents.dataflows.tree_work_backlog import (
    BacklogItem,
    TreeWorkBacklog,
    build_tree_work_backlog,
)


# ── 常量 ──────────────────────────────────────────────────────────────

CONTRACT_VERSION = "kb-012-v1"
TASK_CODE = "KB-012"

# 任务分组枚举（与任务描述对齐）。
GROUP_MISSING_SYMBOL = "missing_symbol"
GROUP_MISSING_THESIS = "missing_thesis"
GROUP_MISSING_RISKS = "missing_risks"
GROUP_MISSING_SOURCES = "missing_sources"
GROUP_NEEDS_REVIEW_STALE = "needs_review_stale"
GROUP_HOT_BUT_THIN = "hot_but_thin"
GROUP_INGEST_NEW = "ingest_new"

# 分组顺序（按"必填 → 章节 → 时效 → ingest 新页面"递进；high → low 优先级）。
GROUP_ORDER: Tuple[str, ...] = (
    GROUP_MISSING_SYMBOL,
    GROUP_MISSING_THESIS,
    GROUP_MISSING_RISKS,
    GROUP_MISSING_SOURCES,
    GROUP_NEEDS_REVIEW_STALE,
    GROUP_HOT_BUT_THIN,
    GROUP_INGEST_NEW,
)

# 分组中文说明。
GROUP_TITLES: Dict[str, str] = {
    GROUP_MISSING_SYMBOL: "缺 symbol/name（TA 无法按 symbol 命中）",
    GROUP_MISSING_THESIS: "缺一句话总结/投资逻辑（TA 无法取摘要）",
    GROUP_MISSING_RISKS: "缺风险提示（TA 无法标 risk）",
    GROUP_MISSING_SOURCES: "缺 sources/原始资料链接（TA 无法溯源）",
    GROUP_NEEDS_REVIEW_STALE: "过期/低置信需回 Tree Work 复核",
    GROUP_HOT_BUT_THIN: "研究关注度高但证据薄（过热 / 主题拥挤）",
    GROUP_INGEST_NEW: "inbox/raw 未消化（需新建 wiki 页）",
}

# 分组默认优先级（hot_but_thin 与 ingest_new 略低，避免淹没字段补录）。
_GROUP_DEFAULT_PRIORITY: Dict[str, str] = {
    GROUP_MISSING_SYMBOL: "high",
    GROUP_MISSING_THESIS: "high",
    GROUP_MISSING_RISKS: "high",
    GROUP_MISSING_SOURCES: "medium",
    GROUP_NEEDS_REVIEW_STALE: "medium",
    GROUP_HOT_BUT_THIN: "medium",
    GROUP_INGEST_NEW: "medium",
}

# KB-005 category 到 KB-012 group 的映射（用于 ingest_new + 字段缺口）。
_BACKLOG_CATEGORY_TO_GROUP: Dict[str, str] = {
    # inbox / raw 未消化 → ingest_new
    "inbox_unprocessed": GROUP_INGEST_NEW,
    "raw_undigested": GROUP_INGEST_NEW,
    # wiki 待补充 / 废弃 / 字段缺口 → 由 _resolve_field_gap_group 进一步分流
    "wiki_to_be_supplemented": GROUP_MISSING_SOURCES,  # 默认归到 sources/字段缺口
    "wiki_deprecated": GROUP_NEEDS_REVIEW_STALE,
    "wiki_field_gap": GROUP_MISSING_SOURCES,  # 由 _resolve_field_gap_group 细分
    # index 未同步：不进入任务包（与 ingest 补录无关）。
}

# KB-005 action 到分组细分的映射（当 category=wiki_field_gap/supplement 时使用）。
_ACTION_TO_GROUP: Dict[str, str] = {
    "fill_fields": GROUP_MISSING_SYMBOL,  # 通常补 symbols/机器字段
    "fill_summary": GROUP_MISSING_THESIS,
    "fill_risks": GROUP_MISSING_RISKS,
    "fill_source_links": GROUP_MISSING_SOURCES,
    "ingest": GROUP_INGEST_NEW,
    "archive": GROUP_NEEDS_REVIEW_STALE,
    "review": GROUP_NEEDS_REVIEW_STALE,
    "add_index_link": GROUP_INGEST_NEW,  # 新页未在 index 中也算补录信号
    "remove_orphan_link": GROUP_NEEDS_REVIEW_STALE,
}

# KB-002 rule_id 到分组的映射。
_LINT_RULE_TO_GROUP: Dict[str, str] = {
    "FMR-001": GROUP_MISSING_SOURCES,  # 必填字段缺失（多为 sources）
    "FMR-002": GROUP_MISSING_SYMBOL,   # 推荐机器字段（含 symbols）
    "SEC-001": GROUP_MISSING_THESIS,
    "SEC-002": GROUP_MISSING_RISKS,
    "SEC-003": GROUP_MISSING_SOURCES,
    "SEC-004": GROUP_MISSING_THESIS,
    "SYM-001": GROUP_MISSING_SYMBOL,
    "TBL-001": GROUP_MISSING_THESIS,   # 评分表表头缺失归到 thesis
    "STALE-001": GROUP_NEEDS_REVIEW_STALE,
    "STALE-002": GROUP_NEEDS_REVIEW_STALE,
    "EVID-001": GROUP_NEEDS_REVIEW_STALE,
    "TODO-001": GROUP_NEEDS_REVIEW_STALE,
}

# KB-002 rule_id 中带 field=symbols 时也归 missing_symbol。
_SYMBOL_FIELD_RULES = {"FMR-001", "FMR-002"}

# 研究关注度 hot_but_thin 阈值（与任务"热门但证据薄"对齐）。
_HOT_BUT_THIN_ATTENTION_THRESHOLD = 1.5  # research_attention_score >= 1.5 视为"热门"
_HOT_BUT_THIN_STALE_RATIO = 0.34          # stale/mention >= 34% 视为"证据薄"
_HOT_BUT_THIN_THEME_CROWDING = 6          # theme_count >= 6 视为"主题拥挤"

# Tree Work ingest 模板（Markdown）：补 frontmatter + 章节骨架。
INGEST_TEMPLATE_MD = """```markdown
---
title: <公司名/主题> — <一句话标题>
created: {today}
updated: {today}
sources:
  - "[[../../raw/<原始资料文件名>.md|<来源别名>]]"
tags: [<主题标签>]
related: []
symbols: ["<6位代码>.SH <简称>"]   # 例：603296.SH 华勤技术
themes: [<主题>]                   # 例：AI服务器
industry_chain_roles: [<产业链角色>]
report_type: 公司点评|行业|综述|数据表|财报分析
evidence_level: A|B|C              # C=低置信
valid_until: YYYY-MM-DD 或 长期
source_quality: 高|中|低
stale_risk: 低|中|高
---

# <标题>

## 一句话总结

<1-2 句话点出核心驱动 / 结论，TA 仅取该段作为背景摘要。>

## 投资逻辑

- <核心驱动 1>
- <核心驱动 2>
- <核心驱动 3>

## 风险提示

- <风险 1>
- <风险 2>

## 原始资料

- [[../../raw/<原始资料>.md|<研报别名>]]
```
"""


# ── 数据类 ────────────────────────────────────────────────────────────


@dataclass
class TaskPackItem:
    """一条 Tree Work 补录任务。"""

    group: str
    location: str
    suggested_action: str
    detail: str = ""
    priority: str = "medium"
    sources: List[str] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "group": self.group,
            "location": self.location,
            "suggested_action": self.suggested_action,
            "detail": self.detail,
            "priority": self.priority,
            "sources": list(self.sources),
            "extra": dict(self.extra),
        }


@dataclass
class TreeWorkTaskPack:
    """整库 Tree Work 补录任务包聚合。"""

    knowledge_root: str
    generated_at: str
    as_of_date: str
    contract_version: str = CONTRACT_VERSION
    task: str = TASK_CODE
    items_by_group: Dict[str, List[TaskPackItem]] = field(default_factory=dict)
    # 上游信号来源摘要（便于解释）。
    upstream_summary: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)

    def all_items(self) -> List[TaskPackItem]:
        """按 group_order → priority → location 排序返回所有任务项。

        - 先按 ``GROUP_ORDER`` 分组（保持任务描述的分组优先级）。
        - 组内按 priority（high → medium → low）→ location 排序。
        """
        prio = {"high": 0, "medium": 1, "low": 2}
        group_idx = {g: i for i, g in enumerate(GROUP_ORDER)}
        items: List[TaskPackItem] = []
        for group in GROUP_ORDER:
            for item in self.items_by_group.get(group, []):
                items.append(item)
        items.sort(
            key=lambda it: (
                group_idx.get(it.group, len(GROUP_ORDER)),
                prio.get(it.priority, 3),
                it.location,
            )
        )
        return items

    def group_counts(self) -> Dict[str, int]:
        return {group: len(self.items_by_group.get(group, [])) for group in GROUP_ORDER}

    def total(self) -> int:
        return sum(len(items) for items in self.items_by_group.values())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "knowledge_root": self.knowledge_root,
            "generated_at": self.generated_at,
            "as_of_date": self.as_of_date,
            "contract_version": self.contract_version,
            "task": self.task,
            "items_by_group": {
                group: [i.to_dict() for i in self.items_by_group.get(group, [])]
                for group in GROUP_ORDER
            },
            "group_counts": self.group_counts(),
            "total": self.total(),
            "upstream_summary": dict(self.upstream_summary),
            "errors": list(self.errors),
        }


# ── 上游信号到任务的转换器 ────────────────────────────────────────────


def _priority_max(*priorities: str) -> str:
    """取一组优先级中最高（high > medium > low）。"""
    order = {"high": 0, "medium": 1, "low": 2}
    if not priorities:
        return "medium"
    return min(priorities, key=lambda p: order.get(p, 3))


def _resolve_field_gap_group(action: str) -> str:
    """对 KB-005 wiki_field_gap / supplement 类目按 action 细分到 KB-012 分组。"""
    return _ACTION_TO_GROUP.get(action, GROUP_MISSING_SOURCES)


def _lint_finding_group(rule_id: str, field_name: Optional[str]) -> str:
    """对 KB-002 finding 决定其归属分组（field=symbols 时覆盖默认）。"""
    if field_name == "symbols" and rule_id in _SYMBOL_FIELD_RULES:
        return GROUP_MISSING_SYMBOL
    if rule_id == "FMR-001" and field_name == "sources":
        return GROUP_MISSING_SOURCES
    return _LINT_RULE_TO_GROUP.get(rule_id, GROUP_NEEDS_REVIEW_STALE)


def _lint_finding_action(rule_id: str, group: str) -> str:
    """根据 KB-002 finding 给出建议动作（与 KB-005 动作语义一致）。"""
    if group == GROUP_MISSING_SYMBOL:
        return "fill_fields"
    if group == GROUP_MISSING_THESIS:
        return "fill_summary"
    if group == GROUP_MISSING_RISKS:
        return "fill_risks"
    if group == GROUP_MISSING_SOURCES:
        return "fill_source_links"
    if group in (GROUP_NEEDS_REVIEW_STALE, GROUP_HOT_BUT_THIN):
        return "review"
    return "fill_fields"


def _collect_from_lint(
    lint_result: KnowledgeLintResult, pack: TreeWorkTaskPack
) -> None:
    """把 KB-002 的 finding（error/warning）转换为 KB-012 任务项。

    info 级 finding（TODO-001/EVID-001 标记）只进入 needs_review_stale，不进入
    缺口分组，避免"已显式标记"的页面被误判为待补字段。
    """
    for page in lint_result.page_results:
        for finding in page.findings:
            if finding.severity == SEVERITY_INFO:
                # info 级（TODO/EVID）归 needs_review_stale，提示回 Tree Work 复核。
                if finding.rule_id in ("TODO-001", "EVID-001"):
                    pack.items_by_group.setdefault(GROUP_NEEDS_REVIEW_STALE, []).append(
                        TaskPackItem(
                            group=GROUP_NEEDS_REVIEW_STALE,
                            location=page.rel_path,
                            suggested_action="review",
                            detail=finding.message,
                            priority="low",
                            sources=[f"KB-002:{finding.rule_id}"],
                            extra={"rule_id": finding.rule_id},
                        )
                    )
                continue

            group = _lint_finding_group(finding.rule_id, finding.field)
            action = _lint_finding_action(finding.rule_id, group)
            priority = "high" if finding.severity == SEVERITY_ERROR else "medium"
            pack.items_by_group.setdefault(group, []).append(
                TaskPackItem(
                    group=group,
                    location=page.rel_path,
                    suggested_action=action,
                    detail=finding.message,
                    priority=priority,
                    sources=[f"KB-002:{finding.rule_id}"],
                    extra={
                        "rule_id": finding.rule_id,
                        "field": finding.field,
                        "fix_suggestion": finding.fix_suggestion,
                    },
                )
            )


def _collect_from_backlog(
    backlog: TreeWorkBacklog, pack: TreeWorkTaskPack
) -> None:
    """把 KB-005 的清单项映射到 KB-012 分组（去重由 _dedup_items 后续统一处理）。"""
    for item in backlog.all_items():
        if item.category == "index_not_synced":
            # index 未同步不进入任务包（不属于 ingest 补录范畴）。
            continue
        if item.category in ("wiki_field_gap", "wiki_to_be_supplemented"):
            group = _resolve_field_gap_group(item.suggested_action)
        elif item.category == "wiki_deprecated":
            group = GROUP_NEEDS_REVIEW_STALE
        else:
            group = _BACKLOG_CATEGORY_TO_GROUP.get(
                item.category, GROUP_INGEST_NEW
            )
        pack.items_by_group.setdefault(group, []).append(
            TaskPackItem(
                group=group,
                location=item.location,
                suggested_action=item.suggested_action,
                detail=item.detail,
                priority=item.priority,
                sources=[f"KB-005:{item.category}"],
                extra={"backlog_category": item.category},
            )
        )


def _collect_from_attention(
    attention: ResearchAttentionResult, pack: TreeWorkTaskPack
) -> None:
    """把 KB-007 / KB-009 的关注度与衰减结果映射为 hot_but_thin / needs_review 任务。

    - 任一 stale_mention > 0 的 symbol → needs_review_stale（每页一条 review）。
    - 满足 hot_but_thin 阈值（score≥阈值 且 (stale_ratio≥阈值 或 theme_crowding)）
      → hot_but_thin。
    """
    # 延迟导入 KB-009 衰减模块，缺依赖时降级为只用 KB-007 字段。
    try:
        from tradingagents.dataflows.research_attention_decay import (
            compute_symbol_decay as _kb009_compute_decay,
        )
        decay_available = True
    except Exception:
        decay_available = False

    for sym in attention.symbols:
        if sym.asset_class != "A_SHARE":
            # 非 A 股不进入任务包（与任务约束"不混资产类别"对齐）。
            continue

        # 1) needs_review_stale：stale_mention > 0 的命中页逐条提示 review。
        stale_pages = [p for p in sym.matched_pages if p.is_stale]
        for page in stale_pages:
            pack.items_by_group.setdefault(GROUP_NEEDS_REVIEW_STALE, []).append(
                TaskPackItem(
                    group=GROUP_NEEDS_REVIEW_STALE,
                    location=page.rel_path,
                    suggested_action="review",
                    detail=(
                        f"`{sym.symbol_key}` {sym.name or ''} 研报关注度页面过期/"
                        "高 stale_risk，需回 Tree Work 复核或补证"
                    ).strip(),
                    priority="medium",
                    sources=["KB-007:stale_mention"],
                    extra={
                        "symbol": sym.symbol_key,
                        "name": sym.name,
                        "page_type": page.page_type,
                    },
                )
            )

        # 2) hot_but_thin：高关注度 + 证据薄 / 主题拥挤。
        score = sym.research_attention_score
        mention = sym.mention_count or 0
        stale_ratio = (
            sym.stale_mention_count / sym.mention_count if sym.mention_count else 0.0
        )
        theme_crowding = sym.theme_count >= _HOT_BUT_THIN_THEME_CROWDING

        # KB-009 衰减补充信号（如可用）。
        overheat_flags: List[str] = []
        high_stale_fresh = 0
        if decay_available:
            try:
                decay = _kb009_compute_decay(sym)
                overheat_flags = list(decay.overheat_flags)
                high_stale_fresh = decay.high_stale_fresh_count
            except Exception:
                pass

        is_hot = score >= _HOT_BUT_THIN_ATTENTION_THRESHOLD
        is_thin = (
            stale_ratio >= _HOT_BUT_THIN_STALE_RATIO
            or theme_crowding
            or high_stale_fresh > 0
            or bool(overheat_flags)
        )
        if is_hot and is_thin and mention > 0:
            detail_parts: List[str] = [
                f"score={score:.2f}",
                f"mention={mention}",
                f"stale_ratio={stale_ratio:.2f}",
            ]
            if theme_crowding:
                detail_parts.append(f"theme_crowding={sym.theme_count}")
            if overheat_flags:
                detail_parts.append("overheat=" + ",".join(overheat_flags))
            pack.items_by_group.setdefault(GROUP_HOT_BUT_THIN, []).append(
                TaskPackItem(
                    group=GROUP_HOT_BUT_THIN,
                    location=f"symbol:{sym.symbol_key}",
                    suggested_action="review",
                    detail=(
                        f"`{sym.symbol_key}` {sym.name or ''} 研究关注度高但证据薄，"
                        + "；".join(detail_parts)
                    ).strip(),
                    priority="medium",
                    sources=["KB-007:hot_but_thin"],
                    extra={
                        "symbol": sym.symbol_key,
                        "name": sym.name,
                        "score": round(score, 2),
                        "mention_count": mention,
                        "theme_count": sym.theme_count,
                        "stale_ratio": round(stale_ratio, 2),
                        "overheat_flags": overheat_flags,
                    },
                )
            )


# ── 去重与聚合 ────────────────────────────────────────────────────────


def _item_dedup_key(item: TaskPackItem) -> Tuple[str, str]:
    """同一 (group, location) 视为同一条任务（多来源信号合并）。"""
    return (item.group, item.location)


def _merge_items(items: List[TaskPackItem]) -> TaskPackItem:
    """合并同一 (group, location) 的多条任务，sources/priority 取最严，detail 取最长。"""
    if len(items) == 1:
        return items[0]
    locations = {it.location for it in items}
    if len(locations) != 1:
        # 不应发生（调用前已按 dedup key 分组）；防御性返回首条。
        return items[0]
    location = items[0].location
    group = items[0].group
    all_sources: List[str] = []
    all_extras: Dict[str, Any] = {}
    details: List[str] = []
    priorities: List[str] = []
    actions: List[str] = []
    for it in items:
        for s in it.sources:
            if s not in all_sources:
                all_sources.append(s)
        for k, v in it.extra.items():
            if k not in all_extras:
                all_extras[k] = v
            elif all_extras[k] != v:
                # 同字段不同值时合并为列表。
                existing = all_extras[k]
                if not isinstance(existing, list):
                    existing = [existing]
                if v not in existing:
                    existing.append(v)
                all_extras[k] = existing
        if it.detail and it.detail not in details:
            details.append(it.detail)
        priorities.append(it.priority)
        actions.append(it.suggested_action)
    return TaskPackItem(
        group=group,
        location=location,
        suggested_action=actions[0] if actions else "review",
        detail="；".join(details)[:300],
        priority=_priority_max(*priorities),
        sources=all_sources,
        extra=all_extras,
    )


def _dedup_items(pack: TreeWorkTaskPack) -> None:
    """对每个 group 内按 (group, location) 去重合并。"""
    for group, items in list(pack.items_by_group.items()):
        if not items:
            continue
        bucket: Dict[Tuple[str, str], List[TaskPackItem]] = {}
        for it in items:
            bucket.setdefault(_item_dedup_key(it), []).append(it)
        merged = [_merge_items(group_items) for group_items in bucket.values()]
        # 组内排序：priority → location。
        order = {"high": 0, "medium": 1, "low": 2}
        merged.sort(key=lambda it: (order.get(it.priority, 3), it.location))
        pack.items_by_group[group] = merged


# ── 上游摘要 ──────────────────────────────────────────────────────────


def _build_upstream_summary(
    lint_result: Optional[KnowledgeLintResult],
    backlog: Optional[TreeWorkBacklog],
    attention: Optional[ResearchAttentionResult],
) -> Dict[str, Any]:
    summary: Dict[str, Any] = {}
    if lint_result is not None:
        summary["kb002"] = {
            "page_count": lint_result.page_count,
            "readiness_counts": dict(lint_result.readiness_counts),
            "findings_by_severity": dict(lint_result.findings_by_severity),
            "top_rules": sorted(
                lint_result.findings_by_rule.items(),
                key=lambda kv: (-kv[1], kv[0]),
            )[:5],
        }
    if backlog is not None:
        summary["kb005"] = {
            "category_counts": backlog.category_counts(),
            "total_backlog": len(backlog.all_items()),
            "raw_total": backlog.raw_total,
            "raw_referenced": backlog.raw_referenced,
            "investment_page_count": backlog.investment_page_count,
        }
    if attention is not None:
        summary["kb007"] = {
            "symbol_count": attention.symbol_count,
            "asset_class_counts": dict(attention.asset_class_counts),
            "top_symbols": [
                {
                    "symbol": s.symbol_key,
                    "name": s.name,
                    "score": round(s.research_attention_score, 2),
                    "mention": s.mention_count,
                }
                for s in attention.top_symbols[:5]
            ],
        }
    return summary


# ── 主构建逻辑 ────────────────────────────────────────────────────────


def build_tree_work_task_pack(
    knowledge_root: str,
    *,
    collect_lint: bool = True,
    collect_backlog: bool = True,
    collect_attention: bool = True,
) -> TreeWorkTaskPack:
    """合并 KB-002 / KB-005 / KB-007/009 信号，产出 Tree Work 补录任务包。

    参数:
        knowledge_root: 知识库根目录绝对路径。
        collect_lint: 是否收集 KB-002 lint findings（默认 True）。
        collect_backlog: 是否收集 KB-005 backlog items（默认 True）。
        collect_attention: 是否收集 KB-007/009 关注度信号（默认 True）。

    返回:
        :class:`TreeWorkTaskPack`。任何上游模块抛异常都会被吞掉并记入
        ``errors``，任务包仍能产出（部分信号缺失时返回部分结果）。
    """
    root = Path(knowledge_root).expanduser()
    today = date.today()
    pack = TreeWorkTaskPack(
        knowledge_root=str(root),
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        as_of_date=today.strftime("%Y-%m-%d"),
    )

    if not root.exists():
        pack.errors.append(f"knowledge_root 不存在: {root}")
        return pack

    lint_result: Optional[KnowledgeLintResult] = None
    backlog: Optional[TreeWorkBacklog] = None
    attention: Optional[ResearchAttentionResult] = None

    if collect_lint:
        try:
            lint_result = lint_local_knowledge(str(root))
        except Exception as exc:  # pragma: no cover - 容错：上游失败不阻塞
            pack.errors.append(f"KB-002 lint 失败: {exc!r}")
            lint_result = None
        if lint_result is not None:
            pack.errors.extend(lint_result.errors)
            _collect_from_lint(lint_result, pack)

    if collect_backlog:
        try:
            backlog = build_tree_work_backlog(str(root))
        except Exception as exc:  # pragma: no cover
            pack.errors.append(f"KB-005 backlog 失败: {exc!r}")
            backlog = None
        if backlog is not None:
            pack.errors.extend(backlog.errors)
            _collect_from_backlog(backlog, pack)

    if collect_attention:
        try:
            attention = compute_research_attention(str(root))
        except Exception as exc:  # pragma: no cover
            pack.errors.append(f"KB-007 attention 失败: {exc!r}")
            attention = None
        if attention is not None:
            pack.errors.extend(attention.errors)
            _collect_from_attention(attention, pack)

    # 上游摘要（解释性元数据）。
    pack.upstream_summary = _build_upstream_summary(lint_result, backlog, attention)

    # 去重合并。
    _dedup_items(pack)

    return pack


# ── 报告渲染 ──────────────────────────────────────────────────────────


def _render_group_section(
    pack: TreeWorkTaskPack, group: str, lines: List[str]
) -> None:
    items = pack.items_by_group.get(group, [])
    title = GROUP_TITLES.get(group, group)
    lines.append(f"### {title}（{len(items)}）")
    lines.append("")
    if not items:
        lines.append("_（无）_")
        lines.append("")
        return
    lines.append("| 优先级 | 路径 | 建议动作 | 原因 | 来源信号 |")
    lines.append("|--------|------|----------|------|----------|")
    for it in items:
        sources_text = ", ".join(f"`{s}`" for s in it.sources)
        detail = it.detail.replace("|", "/") if it.detail else "-"
        lines.append(
            f"| {it.priority} | `{it.location}` | `{it.suggested_action}` | "
            f"{detail} | {sources_text} |"
        )
    lines.append("")


def render_task_pack_report(pack: TreeWorkTaskPack) -> str:
    """渲染 Markdown 任务包报告。

    报告结构：
      1. 概览（总任务数 / 分组统计 / 上游摘要）。
      2. 按分组列出任务（每条任务含路径/动作/原因/来源信号）。
      3. Tree Work ingest 模板（frontmatter + 章节）。
      4. 建议执行顺序。
      5. 免责声明。
    """
    lines: List[str] = []
    lines.append(
        f"# Tree Work 研报补录任务包 — {pack.as_of_date}"
    )
    lines.append("")
    lines.append(
        f"> [{TASK_CODE}] tree_work_task_pack — 只读合并 KB-002 lint / KB-005 backlog / "
        "KB-007/009 关注度信号，输出 Tree Work 补录任务包；"
        "**不修改知识库、不复制研报原文、不输出交易建议**。"
    )
    lines.append("")

    # 1. 概览
    lines.append("## 1. 概览")
    lines.append("")
    lines.append(f"- knowledge_root: `{pack.knowledge_root}`")
    lines.append(f"- generated_at: {pack.generated_at}")
    lines.append(f"- as_of_date: {pack.as_of_date}")
    lines.append(f"- contract_version: `{pack.contract_version}`")
    lines.append(f"- 任务总数: **{pack.total()}**")
    if pack.errors:
        lines.append(f"- ⚠️ 上游错误 ({len(pack.errors)}):")
        for err in pack.errors[:10]:
            lines.append(f"  - {err}")
    lines.append("")

    # 2. 分组统计
    lines.append("## 2. 分组统计")
    lines.append("")
    lines.append("| 分组 | 数量 | 说明 |")
    lines.append("|------|------|------|")
    counts = pack.group_counts()
    for group in GROUP_ORDER:
        cnt = counts.get(group, 0)
        lines.append(f"| `{group}` | {cnt} | {GROUP_TITLES.get(group, '')} |")
    lines.append("")

    # 3. 上游信号摘要
    lines.append("## 3. 上游信号摘要")
    lines.append("")
    upstream = pack.upstream_summary
    if not upstream:
        lines.append("_（无上游信号）_")
    else:
        if "kb002" in upstream:
            kb002 = upstream["kb002"]
            sev = kb002.get("findings_by_severity", {})
            rc = kb002.get("readiness_counts", {})
            lines.append(
                f"- **KB-002 lint**：{kb002.get('page_count', 0)} 页 / "
                f"error={sev.get('error', 0)} warning={sev.get('warning', 0)} "
                f"info={sev.get('info', 0)} / "
                f"high={rc.get('high', 0)} medium={rc.get('medium', 0)} "
                f"low={rc.get('low', 0)}"
            )
        if "kb005" in upstream:
            kb005 = upstream["kb005"]
            lines.append(
                f"- **KB-005 backlog**：raw={kb005.get('raw_total', 0)} "
                f"(referenced {kb005.get('raw_referenced', 0)}) / "
                f"investment_pages={kb005.get('investment_page_count', 0)} / "
                f"total_backlog={kb005.get('total_backlog', 0)}"
            )
        if "kb007" in upstream:
            kb007 = upstream["kb007"]
            ac = kb007.get("asset_class_counts", {})
            a_share = ac.get("A_SHARE", 0)
            lines.append(
                f"- **KB-007 attention**：symbols={kb007.get('symbol_count', 0)} "
                f"(A_SHARE {a_share})"
            )
            top = kb007.get("top_symbols", [])
            if top:
                top_text = ", ".join(
                    f"`{s['symbol']}`({s['score']})" for s in top[:5]
                )
                lines.append(f"  - Top 5：{top_text}")
    lines.append("")

    # 4. 按分组列出任务
    lines.append("## 4. 补录任务（按分组）")
    lines.append("")
    for group in GROUP_ORDER:
        _render_group_section(pack, group, lines)

    # 5. Tree Work ingest 模板
    lines.append("## 5. Tree Work ingest 模板")
    lines.append("")
    lines.append(
        "新建 / 重写 wiki/investment 页面时，按以下 frontmatter + 章节骨架填写；"
        "对应契约见 `docs/local_knowledge_contract.md`。"
    )
    lines.append("")
    lines.append(INGEST_TEMPLATE_MD.format(today=pack.as_of_date))

    # 6. 建议执行顺序
    lines.append("## 6. 建议执行顺序")
    lines.append("")
    lines.append("1. **先补字段（missing_symbol / thesis / risks / sources）**：")
    lines.append("   - 这些是 TA 已能命中的页面，补字段后立刻能被 KB-003 / KB-008 高置信引用。")
    lines.append("2. **复核过期/低置信页（needs_review_stale）**：")
    lines.append("   - 更新 `valid_until` / 重写 stale_risk；无法补证的标记 `evidence_level=C`。")
    lines.append("3. **处理热门但证据薄（hot_but_thin）**：")
    lines.append("   - 补高质量来源（`source_quality=高` + `evidence_level=A`）或降权。")
    lines.append("4. **新建 ingest 页面（ingest_new）**：")
    lines.append("   - 优先消化 raw/ 下高优先级 .md 研报；inbox 笔记按主题归类后合并。")
    lines.append("")
    lines.append(
        "> 每条任务完成后，在对应 wiki 页 frontmatter 更新 `updated`；"
        "后续运行 `scripts/tree_work_task_pack.py` 会自动剔除已完成项。"
    )
    lines.append("")

    # 7. 免责声明
    lines.append("## 7. 免责声明")
    lines.append("")
    lines.append(
        "- 本任务包只提供 Tree Work ingest 字段要求与优先级，**不构成任何买卖建议或强动作词**。"
    )
    lines.append(
        "- 所有任务来源可追溯到 KB-002/KB-005/KB-007/KB-009 的规则 ID；"
        "执行后可重跑对应 CLI 验证。"
    )
    lines.append("- 任务包不含研报原文段落，仅引用相对路径与简短原因。")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        f"_由 `scripts/tree_work_task_pack.py` 只读生成；"
        f"对应模块 `tradingagents.dataflows.tree_work_task_pack`。_"
    )
    lines.append("")
    return "\n".join(lines)


# ── CLI 便利 ─────────────────────────────────────────────────────────


def suggest_task_pack_output_path(
    docs_dir: str = "docs/knowledge_reports",
) -> str:
    """生成默认输出路径 ``docs/knowledge_reports/tree_work_task_pack-YYYY-MM-DD.md``。"""
    today = date.today().strftime("%Y-%m-%d")
    return os.path.join(docs_dir, f"tree_work_task_pack-{today}.md")


__all__ = [
    "CONTRACT_VERSION",
    "TASK_CODE",
    "GROUP_MISSING_SYMBOL",
    "GROUP_MISSING_THESIS",
    "GROUP_MISSING_RISKS",
    "GROUP_MISSING_SOURCES",
    "GROUP_NEEDS_REVIEW_STALE",
    "GROUP_HOT_BUT_THIN",
    "GROUP_INGEST_NEW",
    "GROUP_ORDER",
    "GROUP_TITLES",
    "INGEST_TEMPLATE_MD",
    "TaskPackItem",
    "TreeWorkTaskPack",
    "build_tree_work_task_pack",
    "render_task_pack_report",
    "suggest_task_pack_output_path",
    "default_knowledge_root",
]
