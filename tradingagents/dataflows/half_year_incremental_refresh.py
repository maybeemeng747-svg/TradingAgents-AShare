# [HY-009] half_year_incremental_refresh
"""半年报增量刷新、缓存失效与事实冲突审计。

在 HY-003 半年报事实索引、HY-005 事实反证、HY-008 端到端验收之上，叠加
增量感知能力：识别新披露、修订稿、删除页和知识库页面更新，安全刷新缓存
并标记跨版本事实冲突。

设计约束（对应任务 HY-009）：
  - **只读本地知识库**：绝不向知识库写文件；不写生产 DB。
  - **增量判断基于 mtime / content hash / report_period / disclosure_date**：
    新增、修订、过期和删除四类变更均可识别。
  - **缓存键含 schema / version**：损坏或协议升级时可安全重建。
  - **同报告期关键指标冲突输出 fact_conflict_flags 和来源路径**：
    营收、利润、现金流、毛利率等。
  - **增量审计摘要**：说明刷新数量、冲突数量、失效缓存和待 Tree Work 复核项。
  - **重复执行幂等**：无变化不重建全量索引。
  - **冲突事实不进入 HAS_DATA 强结论路径**。
  - **不调用 LLM / 不访问外网**：纯标准库 + 复用已有模块。

使用示例::

    from tradingagents.dataflows.half_year_incremental_refresh import (
        incremental_refresh_half_year_facts,
    )
    result = incremental_refresh_half_year_facts(
        "/Users/maybee/Documents/knowledge", symbol="000977"
    )
    print(result.refresh_summary)
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from tradingagents.dataflows.half_year_facts_provider import (
    DATA_CONFLICT,
    DATA_FRESH,
    DATA_STALE,
    HalfYearFactsPage,
    HalfYearFactsQueryResult,
    ParsedMetric,
    _build_facts_page,
    _build_facts_page_from_cache,
    _detect_conflicts,
    _is_valid_date,
    _page_matches_query,
    _parse_single_fact,
    query_half_year_facts,
)
from tradingagents.dataflows.local_knowledge_audit import (
    INVESTMENT_SUBDIR,
    _iter_markdown_files,
    _parse_frontmatter,
    _read_text_safe,
    _safe_str,
    _split_frontmatter,
)
from tradingagents.dataflows.local_knowledge_cache import (
    CONTRACT_VERSION,
    CachedPageData,
    FRESHNESS_ERROR,
    FRESHNESS_FRESH,
    FRESHNESS_MISSING,
    FRESHNESS_STALE,
    KnowledgeCache,
    ManifestEntry,
    _build_cached_page,
    _file_sha1_prefix,
    _manifest_matches,
    build_cache_from_scan,
    build_manifest,
    get_or_build_cache,
    save_cache_to_disk,
)
from tradingagents.dataflows.local_knowledge_lint import (
    _is_half_year_report,
    _is_valid_financial_period,
    _normalize_source_type_list,
)
from tradingagents.dataflows.local_knowledge_provider import (
    STATUS_HAS_DATA,
    STATUS_NORMAL_NO_DATA,
    _normalize_symbol_list,
)


# ── 常量 ──────────────────────────────────────────────────────────────

TASK_CODE = "HY-009"

# content hash 前缀长度（与 KB-010 sha1 前缀一致）。
_HASH_PREFIX_LEN = 16

# 冲突检测的关键指标（与 HY-003 _CONFLICT_METRIC_KEYS 一致）。
_CONFLICT_METRIC_KEYS = ("revenue", "net_profit", "gross_margin", "operating_cash_flow")

# 变更类型枚举。
CHANGE_NEW = "new"
CHANGE_REVISED = "revised"
CHANGE_DELETED = "deleted"
CHANGE_EXPIRED = "expired"
CHANGE_UNCHANGED = "unchanged"

# [HY-009-R1] 冲突扫描专用上限：冲突检测必须覆盖全部命中页，
# 不受展示用 max_pages 截断影响。
_CONFLICT_SCAN_MAX_PAGES = 10 ** 9


# ── 数据类 ────────────────────────────────────────────────────────────


@dataclass
class FactConflictFlag:
    """单条事实冲突标记。

    同报告期、同指标 key 出现多个不同 value 时生成。
    """

    period: str
    metric_key: str
    values: List[Dict[str, str]]  # [{"value": "150.2亿", "source": "rel_path"}, ...]
    detail: str  # 人读冲突描述

    def to_dict(self) -> Dict[str, Any]:
        return {
            "period": self.period,
            "metric_key": self.metric_key,
            "values": list(self.values),
            "detail": self.detail,
        }


@dataclass
class PageChange:
    """单页变更记录。"""

    rel_path: str
    change_type: str  # new / revised / deleted / expired / unchanged
    old_hash: Optional[str] = None  # 变更前 content hash（仅 revised/deleted）
    new_hash: Optional[str] = None  # 变更后 content hash（仅 new/revised）
    old_period: Optional[str] = None  # 变更前 financial_period（仅 revised）
    new_period: Optional[str] = None  # 变更后 financial_period（仅 new/revised）
    old_disclosure_date: Optional[str] = None
    new_disclosure_date: Optional[str] = None
    reason: str = ""  # 变更原因描述

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rel_path": self.rel_path,
            "change_type": self.change_type,
            "old_hash": self.old_hash,
            "new_hash": self.new_hash,
            "old_period": self.old_period,
            "new_period": self.new_period,
            "old_disclosure_date": self.old_disclosure_date,
            "new_disclosure_date": self.new_disclosure_date,
            "reason": self.reason,
        }


@dataclass
class IncrementalRefreshResult:
    """增量刷新结果。"""

    status: str = "no_changes"  # no_changes / refreshed / conflict / failed
    task: str = TASK_CODE
    knowledge_root: str = ""
    symbol: str = ""
    name: str = ""
    # 变更列表
    page_changes: List[PageChange] = field(default_factory=list)
    # 冲突标记
    fact_conflict_flags: List[FactConflictFlag] = field(default_factory=list)
    # 刷新后的事实查询结果
    facts_result: Optional[HalfYearFactsQueryResult] = None
    # 增量审计摘要
    refresh_summary: str = ""
    # 统计
    added_count: int = 0
    revised_count: int = 0
    deleted_count: int = 0
    expired_count: int = 0
    conflict_count: int = 0
    invalidated_cache_count: int = 0
    pending_review_count: int = 0
    # 缓存状态
    cache_freshness: str = ""
    reused_cache: bool = False
    errors: List[str] = field(default_factory=list)
    query: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "task": self.task,
            "knowledge_root": self.knowledge_root,
            "symbol": self.symbol,
            "name": self.name,
            "page_changes": [c.to_dict() for c in self.page_changes],
            "fact_conflict_flags": [f.to_dict() for f in self.fact_conflict_flags],
            "facts_result": self.facts_result.to_dict() if self.facts_result else None,
            "refresh_summary": self.refresh_summary,
            "added_count": self.added_count,
            "revised_count": self.revised_count,
            "deleted_count": self.deleted_count,
            "expired_count": self.expired_count,
            "conflict_count": self.conflict_count,
            "invalidated_cache_count": self.invalidated_cache_count,
            "pending_review_count": self.pending_review_count,
            "cache_freshness": self.cache_freshness,
            "reused_cache": self.reused_cache,
            "errors": list(self.errors),
            "query": dict(self.query),
        }


# ── 页面指纹 ──────────────────────────────────────────────────────────


def _compute_page_fingerprint(abs_path: Path) -> Optional[str]:
    """计算单页 content hash 前缀（与 KB-010 sha1 口径一致）。"""
    try:
        return _file_sha1_prefix(abs_path)
    except Exception:
        return None


def _extract_page_metadata(
    frontmatter: Dict[str, Any],
) -> Tuple[Optional[str], Optional[str], List[str]]:
    """从 frontmatter 提取 financial_period / disclosure_date / source_type。"""
    period_raw = _safe_str(frontmatter.get("financial_period"))
    period = period_raw if period_raw and _is_valid_financial_period(period_raw) else None

    disclosure_raw = _safe_str(frontmatter.get("disclosure_date"))
    disclosure = disclosure_raw if disclosure_raw and _is_valid_date(disclosure_raw) else None

    source_type = _normalize_source_type_list(frontmatter.get("source_type"))
    return period, disclosure, source_type


def _is_expired(frontmatter: Dict[str, Any], today: Optional[date] = None) -> bool:
    """检查页面是否过期（valid_until 已过期或 stale_risk=高）。"""
    from tradingagents.dataflows.local_knowledge_audit import _is_valid_until_expired
    from tradingagents.dataflows.local_knowledge_lint import HIGH_STALE_RISK_VALUES

    stale_val = _safe_str(frontmatter.get("stale_risk")) or ""
    if stale_val in HIGH_STALE_RISK_VALUES:
        return True
    valid_until = _safe_str(frontmatter.get("valid_until"))
    if valid_until:
        if _is_valid_until_expired(valid_until, today=today):
            return True
    return False


# ── 变更检测 ──────────────────────────────────────────────────────────


def _detect_page_changes(
    knowledge_root: str,
    cached_manifest: Optional[Dict[str, ManifestEntry]] = None,
    cached_pages: Optional[Dict[str, CachedPageData]] = None,
    *,
    today: Optional[date] = None,
) -> Tuple[List[PageChange], Dict[str, ManifestEntry], List[str]]:
    """检测页面级变更。

    返回 ``(changes, current_manifest, errors)``。
    变更类型：new / revised / deleted / expired / unchanged。
    """
    root = Path(knowledge_root).expanduser()
    errors: List[str] = []

    current_manifest, man_errors = build_manifest(knowledge_root)
    errors.extend(man_errors)

    changes: List[PageChange] = []

    if cached_manifest is None:
        cached_manifest = {}

    cached_paths = set(cached_manifest.keys())
    current_paths = set(current_manifest.keys())

    # 新增页面
    for rel in sorted(current_paths - cached_paths):
        abs_path = root / rel
        new_hash = _compute_page_fingerprint(abs_path)

        # 检查是否为半年报
        try:
            text = _read_text_safe(abs_path)
            fm_text, _ = _split_frontmatter(text)
            frontmatter = _parse_frontmatter(fm_text)
            is_hy = _is_half_year_report(frontmatter)
            period, disclosure, _ = _extract_page_metadata(frontmatter)
            expired = _is_expired(frontmatter, today=today)
        except Exception:
            is_hy = False
            period = None
            disclosure = None
            expired = False

        if not is_hy:
            continue

        change_type = CHANGE_EXPIRED if expired else CHANGE_NEW
        changes.append(PageChange(
            rel_path=rel,
            change_type=change_type,
            new_hash=new_hash,
            new_period=period,
            new_disclosure_date=disclosure,
            reason="新增半年报页面" if change_type == CHANGE_NEW else "新增过期半年报页面",
        ))

    # 删除页面
    for rel in sorted(cached_paths - current_paths):
        # 只关注半年报页面
        cached_page = (cached_pages or {}).get(rel)
        if cached_page is not None:
            frontmatter = cached_page.frontmatter if isinstance(cached_page.frontmatter, dict) else {}
            if not _is_half_year_report(frontmatter):
                continue
            old_period = _safe_str(frontmatter.get("financial_period"))
        else:
            # 没有缓存页面数据，检查 manifest 中是否有记录
            continue

        old_hash = cached_manifest.get(rel)
        changes.append(PageChange(
            rel_path=rel,
            change_type=CHANGE_DELETED,
            old_hash=old_hash.sha1_prefix if old_hash else None,
            old_period=old_period,
            reason="半年报页面已删除",
        ))

    # 修改页面（mtime / size / sha1 变化）
    for rel in sorted(cached_paths & current_paths):
        old_entry = cached_manifest.get(rel)
        new_entry = current_manifest.get(rel)
        if old_entry is None or new_entry is None:
            continue

        # 快速路径：mtime + size 都没变 → 检查 sha1
        if (old_entry.size == new_entry.size
                and old_entry.mtime_ns == new_entry.mtime_ns):
            if (old_entry.sha1_prefix and new_entry.sha1_prefix
                    and old_entry.sha1_prefix == new_entry.sha1_prefix):
                # [HY-009-R1] 内容完全一致也要检测 expiry-only 变化：
                # 缓存构建时未过期、按注入基准日期已过期的页面，必须标记
                # 过期变更使缓存失效并进入审计，不得当作无变化静默复用。
                # stale_risk 无需复查：内容一致则 frontmatter 一致，
                # 构建时已把 stale_risk=高 计入快照。
                cached_page = (cached_pages or {}).get(rel)
                fm = (
                    cached_page.frontmatter
                    if cached_page is not None and isinstance(cached_page.frontmatter, dict)
                    else {}
                )
                cached_snapshot_expired = bool(
                    cached_page is not None
                    and (cached_page.valid_until_expired or cached_page.stale_risk_high)
                )
                if (
                    fm
                    and _is_half_year_report(fm)
                    and not cached_snapshot_expired
                    and _is_expired(fm, today=today)
                ):
                    changes.append(PageChange(
                        rel_path=rel,
                        change_type=CHANGE_EXPIRED,
                        old_hash=old_entry.sha1_prefix,
                        new_hash=new_entry.sha1_prefix,
                        old_period=_safe_str(fm.get("financial_period")) or None,
                        new_period=_safe_str(fm.get("financial_period")) or None,
                        reason="仅有效期状态变化（valid_until 过期或 stale_risk=高）",
                    ))
                continue  # 完全一致，跳过
            # mtime 相同但 sha1 不同（罕见：touch 后内容变化）
            # 继续检查

        # 有变化 → 读取新 frontmatter
        abs_path = root / rel
        try:
            text = _read_text_safe(abs_path)
            fm_text, _ = _split_frontmatter(text)
            frontmatter = _parse_frontmatter(fm_text)
            is_hy = _is_half_year_report(frontmatter)
            new_period, new_disclosure, _ = _extract_page_metadata(frontmatter)
            expired = _is_expired(frontmatter, today=today)
        except Exception:
            continue

        if not is_hy:
            # 页面不再是半年报（report_type 被改了）→ 视为删除
            cached_page = (cached_pages or {}).get(rel)
            if cached_page is not None:
                cached_fm = cached_page.frontmatter if isinstance(cached_page.frontmatter, dict) else {}
                if _is_half_year_report(cached_fm):
                    old_period = _safe_str(cached_fm.get("financial_period"))
                    changes.append(PageChange(
                        rel_path=rel,
                        change_type=CHANGE_DELETED,
                        old_hash=old_entry.sha1_prefix,
                        old_period=old_period,
                        reason="report_type 不再是半年报",
                    ))
            continue

        # 获取旧信息
        cached_page = (cached_pages or {}).get(rel)
        old_period = None
        old_disclosure = None
        if cached_page is not None:
            cached_fm = cached_page.frontmatter if isinstance(cached_page.frontmatter, dict) else {}
            old_period = _safe_str(cached_fm.get("financial_period"))
            old_disclosure = _safe_str(cached_fm.get("disclosure_date"))

        # 判断是否只是 metadata 变化还是内容实质变化
        content_changed = (
            old_entry.size != new_entry.size
            or old_entry.sha1_prefix != new_entry.sha1_prefix
        )
        metadata_changed = (
            old_period != new_period
            or old_disclosure != new_disclosure
        )

        if not content_changed and not metadata_changed:
            # [HY-009-R1] 仅有效期状态变化（valid_until 随时间推移过期 /
            # stale_risk=高，内容与其他元数据未动）也必须识别为过期变更，
            # 使缓存失效并进入审计；否则过期页在增量层被当成无变化
            if expired:
                changes.append(PageChange(
                    rel_path=rel,
                    change_type=CHANGE_EXPIRED,
                    old_hash=old_entry.sha1_prefix,
                    new_hash=new_entry.sha1_prefix,
                    old_period=old_period,
                    new_period=new_period,
                    old_disclosure_date=old_disclosure,
                    new_disclosure_date=new_disclosure,
                    reason="仅有效期状态变化（valid_until 过期或 stale_risk=高）",
                ))
            continue  # 无实质变化

        change_type = CHANGE_EXPIRED if expired else CHANGE_REVISED
        reason_parts = []
        if content_changed:
            reason_parts.append("内容变更")
        if metadata_changed:
            if old_period != new_period:
                reason_parts.append(f"报告期 {old_period}→{new_period}")
            if old_disclosure != new_disclosure:
                reason_parts.append(f"披露日 {old_disclosure}→{new_disclosure}")

        changes.append(PageChange(
            rel_path=rel,
            change_type=change_type,
            old_hash=old_entry.sha1_prefix,
            new_hash=new_entry.sha1_prefix,
            old_period=old_period,
            new_period=new_period,
            old_disclosure_date=old_disclosure,
            new_disclosure_date=new_disclosure,
            reason="；".join(reason_parts) if reason_parts else "页面变更",
        ))

    return changes, current_manifest, errors


# ── 跨版本事实冲突检测 ────────────────────────────────────────────────


def _detect_cross_version_conflicts(
    pages: List[HalfYearFactsPage],
) -> List[FactConflictFlag]:
    """检测同报告期跨版本事实冲突。

    与 HY-003 的 ``_detect_conflicts`` 不同：本函数不仅标记 data_status=conflict，
    还输出结构化的 ``FactConflictFlag`` 列表，包含来源路径和冲突值。

    扩展检测范围：除 revenue / net_profit / gross_margin 外，还检测
    operating_cash_flow。
    """
    by_period: Dict[str, List[HalfYearFactsPage]] = {}
    for p in pages:
        if not p.financial_period:
            continue
        by_period.setdefault(p.financial_period, []).append(p)

    flags: List[FactConflictFlag] = []

    for period, group in by_period.items():
        if len(group) < 2:
            continue

        metric_values: Dict[str, List[Tuple[str, str]]] = {}
        for p in group:
            for m in p.financial_facts:
                if m.metric_key not in _CONFLICT_METRIC_KEYS:
                    continue
                if not m.value:
                    continue
                metric_values.setdefault(m.metric_key, []).append(
                    (m.value, p.rel_path)
                )

        for metric_key, pairs in metric_values.items():
            distinct = {v for v, _ in pairs}
            if len(distinct) <= 1:
                continue

            values_list = [
                {"value": v, "source": rp} for v, rp in pairs
            ]
            detail_parts = [f"{v}({rp})" for v, rp in pairs]
            detail = f"{period} {metric_key} 冲突：" + " vs ".join(detail_parts)

            flags.append(FactConflictFlag(
                period=period,
                metric_key=metric_key,
                values=values_list,
                detail=detail,
            ))

    return flags


# ── 增量缓存刷新 ──────────────────────────────────────────────────────


def _build_incremental_cache(
    knowledge_root: str,
    old_cache: Optional[KnowledgeCache],
    changes: List[PageChange],
    current_manifest: Dict[str, ManifestEntry],
    *,
    today: Optional[date] = None,
) -> Tuple[KnowledgeCache, int]:
    """增量刷新缓存：只重建变更页面，复用未变更页面。

    返回 ``(new_cache, invalidated_count)``。
    """
    root = Path(knowledge_root).expanduser()

    if old_cache is None or not old_cache.pages:
        # 无旧缓存 → 全量重建
        new_cache = build_cache_from_scan(knowledge_root)
        return new_cache, 0

    changed_paths: Set[str] = {c.rel_path for c in changes}
    invalidated = 0

    # 复用未变更页面
    new_pages: Dict[str, CachedPageData] = {}
    for rel, page in old_cache.pages.items():
        if rel not in changed_paths:
            new_pages[rel] = page

    # 重建变更页面（新增 + 修订）
    for change in changes:
        if change.change_type == CHANGE_DELETED:
            # 已在上面的过滤中排除
            invalidated += 1
            continue

        abs_path = root / change.rel_path
        if not abs_path.exists():
            continue

        page_data, err = _build_cached_page(change.rel_path, abs_path)
        if page_data is not None:
            new_pages[change.rel_path] = page_data
            invalidated += 1
        # 错误不阻塞，记入 cache.errors

    # 构建新缓存
    new_cache = KnowledgeCache(
        contract_version=CONTRACT_VERSION,
        knowledge_root=str(root),
        built_at=datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        manifest=current_manifest,
        pages=new_pages,
    )

    return new_cache, invalidated


# ── 主入口 ────────────────────────────────────────────────────────────


def incremental_refresh_half_year_facts(
    knowledge_root: str,
    *,
    symbol: Optional[str] = None,
    name: Optional[str] = None,
    old_cache: Optional[KnowledgeCache] = None,
    today: Optional[date] = None,
    max_pages: int = 5,
) -> IncrementalRefreshResult:
    """增量刷新半年报事实索引。

    参数:
        knowledge_root: 知识库根目录绝对路径。
        symbol: A 股代码。
        name: 公司简称。
        old_cache: 上次缓存状态（KB-010 KnowledgeCache）。``None`` 时视为首次构建。
        today: 基准日期（测试注入）。
        max_pages: 最多返回的命中页数。

    返回:
        :class:`IncrementalRefreshResult`。
    """
    symbol = (symbol or "").strip() or None
    name = (name or "").strip() or None
    if today is None:
        today = date.today()

    query_desc: Dict[str, Any] = {}
    if symbol:
        query_desc["symbol"] = symbol
    if name:
        query_desc["name"] = name

    result = IncrementalRefreshResult(
        knowledge_root=str(Path(knowledge_root).expanduser()),
        symbol=symbol or "",
        name=name or "",
        query=query_desc,
    )

    root = Path(knowledge_root).expanduser()
    if not root.exists():
        result.status = "failed"
        result.errors.append(f"knowledge_root 不存在: {root}")
        result.refresh_summary = f"刷新失败：知识库不存在 {root}"
        return result

    if not any([symbol, name]):
        result.status = "failed"
        result.errors.append("未提供查询条件（symbol/name 任一）")
        result.refresh_summary = "刷新失败：未提供查询条件"
        return result

    # 1. 检测变更
    cached_manifest = old_cache.manifest if old_cache else None
    cached_pages = old_cache.pages if old_cache else None

    changes, current_manifest, detect_errors = _detect_page_changes(
        knowledge_root,
        cached_manifest=cached_manifest,
        cached_pages=cached_pages,
        today=today,
    )
    result.errors.extend(detect_errors)

    # 统计变更
    for change in changes:
        if change.change_type == CHANGE_NEW:
            result.added_count += 1
        elif change.change_type == CHANGE_REVISED:
            result.revised_count += 1
        elif change.change_type == CHANGE_DELETED:
            result.deleted_count += 1
        elif change.change_type == CHANGE_EXPIRED:
            result.expired_count += 1

    result.page_changes = changes

    # 2. 判断是否需要刷新
    has_changes = bool(changes)

    if has_changes:
        # 增量刷新缓存
        new_cache, invalidated = _build_incremental_cache(
            knowledge_root, old_cache, changes, current_manifest, today=today
        )
        result.invalidated_cache_count = invalidated
        result.cache_freshness = new_cache.freshness_status
        result.reused_cache = False
    elif old_cache is not None:
        # 无变更 → 复用旧缓存（幂等：不重建全量索引）
        new_cache = old_cache
        result.cache_freshness = FRESHNESS_FRESH
        result.reused_cache = True
    else:
        # 首次调用且无旧缓存 → 全量构建
        new_cache = build_cache_from_scan(knowledge_root)
        result.cache_freshness = new_cache.freshness_status
        result.reused_cache = False

    # 3. 查询半年报事实（使用新缓存）
    facts = query_half_year_facts(
        knowledge_root,
        symbol=symbol,
        name=name,
        cache=new_cache,
        today=today,
        max_pages=max_pages,
    )
    result.facts_result = facts

    # 4. 跨版本冲突检测
    # [HY-009-R1] 冲突检查不得受 max_pages 截断：冲突证据落在截断之外时
    # 不得伪装成无冲突。用不截断的全量查询扫描冲突，展示用结果仍按
    # max_pages 截断。
    full_facts = query_half_year_facts(
        knowledge_root,
        symbol=symbol,
        name=name,
        cache=new_cache,
        today=today,
        max_pages=_CONFLICT_SCAN_MAX_PAGES,
    )
    conflict_flags = _detect_cross_version_conflicts(full_facts.pages)
    result.fact_conflict_flags = conflict_flags
    result.conflict_count = len(conflict_flags)

    # 冲突页标记 data_status=conflict（与 HY-003 口径一致）
    _detect_conflicts(full_facts.pages)

    # [HY-009-R1] 只要存在冲突（含现金流冲突），附加到结果的查询级
    # data_status 必须进入 conflict（不可用），不得保持 fresh 伪装 HAS_DATA
    if conflict_flags and result.facts_result is not None:
        result.facts_result.data_status = DATA_CONFLICT

    # 5. 待 Tree Work 复核项
    pending_review = 0
    for flag in conflict_flags:
        pending_review += len(flag.values)
    # 删除的页面也需要复核
    pending_review += result.deleted_count
    result.pending_review_count = pending_review

    # 6. 状态判定
    if conflict_flags:
        result.status = "conflict"
    elif has_changes:
        result.status = "refreshed"
    else:
        result.status = "no_changes"

    # 7. 生成审计摘要
    result.refresh_summary = _build_refresh_summary(result)

    return result


def _build_refresh_summary(result: IncrementalRefreshResult) -> str:
    """生成增量审计摘要。"""
    lines: List[str] = []
    lines.append(f"## 半年报增量刷新审计 — {TASK_CODE}")
    lines.append("")
    lines.append(f"- 知识库：`{result.knowledge_root}`")
    lines.append(f"- 查询：`{result.query}`")
    lines.append(f"- 刷新状态：{result.status}")
    lines.append(f"- 缓存状态：{result.cache_freshness}（复用旧缓存：{'是' if result.reused_cache else '否'}）")
    lines.append("")

    # 变更统计
    lines.append("### 变更统计")
    lines.append("")
    lines.append(f"- 新增：{result.added_count}")
    lines.append(f"- 修订：{result.revised_count}")
    lines.append(f"- 删除：{result.deleted_count}")
    lines.append(f"- 过期：{result.expired_count}")
    lines.append(f"- 冲突：{result.conflict_count}")
    lines.append(f"- 失效缓存条目：{result.invalidated_cache_count}")
    lines.append(f"- 待 Tree Work 复核：{result.pending_review_count}")
    lines.append("")

    # 变更明细
    if result.page_changes:
        lines.append("### 变更明细")
        lines.append("")
        for change in result.page_changes:
            type_label = {
                CHANGE_NEW: "新增",
                CHANGE_REVISED: "修订",
                CHANGE_DELETED: "删除",
                CHANGE_EXPIRED: "过期",
            }.get(change.change_type, change.change_type)
            lines.append(f"- [{type_label}] `{change.rel_path}`：{change.reason}")
        lines.append("")

    # 冲突明细
    if result.fact_conflict_flags:
        lines.append("### 事实冲突明细")
        lines.append("")
        for flag in result.fact_conflict_flags:
            lines.append(f"- {flag.detail}")
        lines.append("")
        lines.append("> ⚠️ 冲突事实不进入 HAS_DATA 强结论路径，需 Tree Work 复核后方可使用。")
        lines.append("")

    # 无变更
    if not result.page_changes and not result.fact_conflict_flags:
        lines.append("### 无变更")
        lines.append("")
        lines.append("半年报页面无新增/修订/删除/过期变更，复用旧缓存，未重建全量索引。")
        lines.append("")

    # 错误
    if result.errors:
        lines.append("### 错误")
        lines.append("")
        for err in result.errors[:5]:
            lines.append(f"- {err}")
        lines.append("")

    return "\n".join(lines)


# ── 便利函数 ──────────────────────────────────────────────────────────


def incremental_refresh_with_disk_cache(
    knowledge_root: str,
    *,
    symbol: Optional[str] = None,
    name: Optional[str] = None,
    cache_path: Optional[str] = None,
    today: Optional[date] = None,
    save: bool = True,
) -> IncrementalRefreshResult:
    """带落盘缓存的增量刷新入口。

    自动加载旧缓存、检测变更、增量刷新，可选保存新缓存。
    """
    from tradingagents.dataflows.local_knowledge_cache import (
        default_cache_path,
        load_cache_from_disk,
    )

    cpath = cache_path or default_cache_path()
    root = Path(knowledge_root).expanduser()

    # 尝试加载旧缓存
    old_cache, load_err = load_cache_from_disk(cpath, expected_knowledge_root=str(root))
    if load_err:
        # 缓存不可用 → 首次构建模式
        old_cache = None

    result = incremental_refresh_half_year_facts(
        knowledge_root,
        symbol=symbol,
        name=name,
        old_cache=old_cache,
        today=today,
    )

    # 保存新缓存
    if save and result.facts_result is not None:
        # 构建新缓存并保存
        changes = result.page_changes
        current_manifest, _ = build_manifest(knowledge_root)
        new_cache, _ = _build_incremental_cache(
            knowledge_root, old_cache, changes, current_manifest, today=today
        )
        write_err = save_cache_to_disk(new_cache, cpath)
        if write_err:
            result.errors.append(f"缓存保存失败：{write_err}")

    return result


__all__ = [
    "TASK_CODE",
    "CHANGE_NEW",
    "CHANGE_REVISED",
    "CHANGE_DELETED",
    "CHANGE_EXPIRED",
    "CHANGE_UNCHANGED",
    "FactConflictFlag",
    "PageChange",
    "IncrementalRefreshResult",
    "incremental_refresh_half_year_facts",
    "incremental_refresh_with_disk_cache",
]
