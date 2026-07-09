# [KB-010] local_knowledge_cache
"""Tree Work 本地知识索引缓存与 freshness manifest。

在 KB-001 只读审计 / KB-003 本地知识查询 / KB-007 研究关注度之上，叠加一层
轻量索引缓存，避免每次 ``query_local_knowledge`` / ``compute_research_attention``
都对 ``~/Documents/knowledge/wiki/investment/`` 做全量扫描与重复解析。

设计约束（对应任务 KB-010）：
  - **只读知识库**：仅用 ``open(..., "r", encoding="utf-8")`` 与 ``Path.iterdir``，
    绝不向知识库写文件；缓存只写项目本地运行目录（默认 ``.cache/``）或调用方
    指定的路径，**不写生产 DB**。
  - **缓存命中不改变交易动作**：缓存只提高查询速度和解释稳定性；
    ``local_knowledge_score`` / ``research_attention_score`` / 强动作门禁均不受
    缓存影响——命中与未命中必须产出**语义等价**的结果。
  - **manifest 驱动失效**：基于文件 ``rel_path / size / mtime_ns`` 生成 manifest，
    任意文件新增 / 删除 / 修改都触发重建；sha1 前缀作为兜底校验，防止 mtime
    精度不足导致漏判。
  - **freshness_status 显式输出**：``fresh``（manifest 完全匹配）/ ``stale``
    （有变更）/ ``missing``（缓存不存在）/ ``error``（缓存损坏或知识库不可读）。
  - **缓存损坏自动回退**：JSON 解析失败 / 字段缺失 / contract_version 不匹配时
    丢弃缓存并重建，不影响 TA 主流程。
  - **不调用 LLM / 不访问外网**：纯标准库。

缓存结构（JSON）::

    {
      "contract_version": "kb-010-v1",
      "knowledge_root": "/Users/maybee/Documents/knowledge",
      "built_at": "2026-07-05T12:00:00",
      "manifest": {
        "wiki/investment/foo.md": {"size": 1234, "mtime_ns": 123, "sha1_prefix": "abc..."},
        ...
      },
      "pages": {
        "wiki/investment/foo.md": {
          "rel_path": "...",
          "title": "...",
          "page_type": "company",
          "machine_readiness": "high",
          "is_to_be_supplemented": false,
          "stale_risk_high": false,
          "valid_until_expired": false,
          "valid_until": "2099-12-31",
          "updated_at": "2026-06-29",
          "size_bytes": 1234,
          "line_count": 50,
          "frontmatter": {...},
          "symbols": [...],
          "themes": [...],
          "tags": [...],
          "summary": "...",
          "risks": [...],
          "sources": [...]
        },
        ...
      },
      "errors": ["..."]
    }

使用示例::

    from tradingagents.dataflows.local_knowledge_cache import (
        get_or_build_cache,
        query_local_knowledge_cached,
    )
    cache = get_or_build_cache("/Users/maybee/Documents/knowledge")
    print(cache.freshness_status)  # fresh / stale / missing / error
    result = query_local_knowledge_cached(cache, symbol="603296")
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tradingagents.dataflows.local_knowledge_audit import (
    INVESTMENT_SUBDIR,
    _audit_single_page,
    _iter_markdown_files,
    _parse_frontmatter,
    _read_text_safe,
    _safe_str,
    _split_frontmatter,
)
from tradingagents.dataflows.local_knowledge_lint import (
    HIGH_STALE_RISK_VALUES,
    LOW_CONFIDENCE_EVIDENCE_LEVELS,
)
from tradingagents.dataflows.local_knowledge_provider import (
    LocalKnowledgeMatch,
    LocalKnowledgeQueryResult,
    STATUS_FAILED,
    STATUS_NORMAL_NO_DATA,
    _aggregate_result,
    _build_match,
    _extract_risk_items,
    _extract_section_text,
    _extract_sources,
    _MAX_MATCHED_PAGES,
    _MAX_RISK_ENTRIES,
    _MAX_SOURCE_ENTRIES,
    _RISK_HEADERS,
    _RISK_MAX_CHARS,
    _SUMMARY_HEADERS,
    _SUMMARY_MAX_CHARS,
    _normalize_str_list,
    _normalize_symbol_list,
    _page_matches,
    _rank_key,
)


# ── 常量 ──────────────────────────────────────────────────────────────

CONTRACT_VERSION = "kb-010-v1"
TASK_CODE = "KB-010"

# freshness_status 枚举（与任务要求字段对齐）。
FRESHNESS_FRESH = "fresh"
FRESHNESS_STALE = "stale"
FRESHNESS_MISSING = "missing"
FRESHNESS_ERROR = "error"

# sha1 前缀长度（不存全量 sha1，省空间；mtime + size 已是主校验）。
_SHA1_PREFIX_LEN = 16

# 默认缓存文件路径（项目本地运行目录，已在 .gitignore 中）。
_DEFAULT_CACHE_DIR = ".cache"
_DEFAULT_CACHE_FILENAME = "knowledge_cache.json"


# ── 数据类 ────────────────────────────────────────────────────────────


@dataclass
class ManifestEntry:
    """单文件 manifest 条目。

    ``mtime_ns`` 是主校验（纳秒精度，文件系统支持时）；``sha1_prefix`` 是兜底，
    防止 mtime 精度不足或被人为 touch 但内容未变。
    """

    size: int
    mtime_ns: int
    sha1_prefix: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "size": self.size,
            "mtime_ns": self.mtime_ns,
            "sha1_prefix": self.sha1_prefix,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ManifestEntry":
        return cls(
            size=int(data.get("size") or 0),
            mtime_ns=int(data.get("mtime_ns") or 0),
            sha1_prefix=str(data.get("sha1_prefix") or "")[:_SHA1_PREFIX_LEN],
        )


@dataclass
class CachedPageData:
    """单页预解析数据。

    携带 KB-003 命中匹配与段落抽取所需的全部字段；缓存命中时无需重新读文件。
    """

    rel_path: str
    title: str
    page_type: str
    machine_readiness: str = "low"
    is_to_be_supplemented: bool = False
    stale_risk_high: bool = False
    valid_until_expired: bool = False
    valid_until: Optional[str] = None
    updated_at: Optional[str] = None
    size_bytes: int = 0
    line_count: int = 0
    # frontmatter 原始解析结果（只保留 JSON 可序列化的标量/list/dict）。
    frontmatter: Dict[str, Any] = field(default_factory=dict)
    # 归一化后的匹配字段（与 KB-003 口径一致）。
    symbols: List[str] = field(default_factory=list)
    themes: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    # 预抽取的段落（裁剪到上限，不输出原文）。
    summary: str = ""
    risks: List[str] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rel_path": self.rel_path,
            "title": self.title,
            "page_type": self.page_type,
            "machine_readiness": self.machine_readiness,
            "is_to_be_supplemented": self.is_to_be_supplemented,
            "stale_risk_high": self.stale_risk_high,
            "valid_until_expired": self.valid_until_expired,
            "valid_until": self.valid_until,
            "updated_at": self.updated_at,
            "size_bytes": self.size_bytes,
            "line_count": self.line_count,
            "frontmatter": _sanitize_jsonable(self.frontmatter),
            "symbols": list(self.symbols),
            "themes": list(self.themes),
            "tags": list(self.tags),
            "summary": self.summary,
            "risks": list(self.risks),
            "sources": list(self.sources),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CachedPageData":
        fm = data.get("frontmatter")
        frontmatter = dict(fm) if isinstance(fm, dict) else {}
        return cls(
            rel_path=str(data.get("rel_path") or ""),
            title=str(data.get("title") or ""),
            page_type=str(data.get("page_type") or "unclassified"),
            machine_readiness=str(data.get("machine_readiness") or "low"),
            is_to_be_supplemented=bool(data.get("is_to_be_supplemented") or False),
            stale_risk_high=bool(data.get("stale_risk_high") or False),
            valid_until_expired=bool(data.get("valid_until_expired") or False),
            valid_until=data.get("valid_until"),
            updated_at=data.get("updated_at"),
            size_bytes=int(data.get("size_bytes") or 0),
            line_count=int(data.get("line_count") or 0),
            frontmatter=frontmatter,
            symbols=list(data.get("symbols") or []),
            themes=list(data.get("themes") or []),
            tags=list(data.get("tags") or []),
            summary=str(data.get("summary") or ""),
            risks=list(data.get("risks") or []),
            sources=list(data.get("sources") or []),
        )


@dataclass
class KnowledgeCache:
    """整库缓存（manifest + 预解析页面 + freshness 状态）。

    ``freshness_status`` 反映 **本次 ``get_or_build_cache`` 调用结束时** 缓存相对
    于知识库当前状态的新鲜度：
      - ``fresh``：缓存命中且 manifest 完全匹配，``pages`` 可直接复用。
      - ``stale``：缓存命中但 manifest 有变更，已自动重建（``pages`` 已更新）。
      - ``missing``：缓存文件不存在，已首次构建。
      - ``error``：缓存损坏 / 知识库不可读 / 解析异常，``pages`` 可能为空。
    """

    contract_version: str = CONTRACT_VERSION
    knowledge_root: str = ""
    built_at: str = ""
    manifest: Dict[str, ManifestEntry] = field(default_factory=dict)
    pages: Dict[str, CachedPageData] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    freshness_status: str = FRESHNESS_MISSING
    # 缓存来源路径（便于调试与 CLI 透出）；为空表示内存构建未落盘。
    cache_path: Optional[str] = None
    # 是否复用了落盘缓存（True 表示未触发全量重建）。
    reused_disk_cache: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "knowledge_root": self.knowledge_root,
            "built_at": self.built_at,
            "manifest": {k: v.to_dict() for k, v in self.manifest.items()},
            "pages": {k: v.to_dict() for k, v in self.pages.items()},
            "errors": list(self.errors),
            "freshness_status": self.freshness_status,
        }


# ── JSON 序列化辅助 ──────────────────────────────────────────────────


def _sanitize_jsonable(value: Any) -> Any:
    """把 frontmatter 中不可 JSON 序列化的值转为字符串，保持缓存可落盘。

    PyYAML 解析出的 frontmatter 可能包含 date / set / 自定义对象；缓存只能存
    JSON 兼容类型。这里做最小兜底，不改变语义。
    """
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_sanitize_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _sanitize_jsonable(v) for k, v in value.items()}
    # date / datetime / set / 其他对象 → 字符串。
    return str(value)


# ── manifest 构建 ────────────────────────────────────────────────────


def _file_sha1_prefix(path: Path, limit_bytes: int = 1_000_000) -> str:
    """读取文件前 ``limit_bytes`` 字节计算 sha1，返回前 16 字符。

    只读首 1MB 是性能折中——manifest 的主校验是 (size, mtime_ns)，sha1 只作为
    兜底防止 mtime 被 touch 但内容未变时误判重建。
    """
    try:
        h = hashlib.sha1()
        with open(path, "rb") as fh:
            remaining = limit_bytes
            while remaining > 0:
                chunk = fh.read(min(65536, remaining))
                if not chunk:
                    break
                h.update(chunk)
                remaining -= len(chunk)
        return h.hexdigest()[:_SHA1_PREFIX_LEN]
    except OSError:
        return ""


def build_manifest(knowledge_root: str) -> Tuple[Dict[str, ManifestEntry], List[str]]:
    """扫描 ``wiki/investment/`` 构建 manifest。

    返回 ``(manifest, errors)``。``errors`` 记录知识库不可读等降级原因，不抛异常。
    """
    root = Path(knowledge_root).expanduser()
    manifest: Dict[str, ManifestEntry] = {}
    errors: List[str] = []

    if not root.exists():
        errors.append(f"knowledge_root 不存在: {root}")
        return manifest, errors

    investment_dir = root / INVESTMENT_SUBDIR
    if not investment_dir.exists():
        errors.append(f"investment 分区不存在: {investment_dir}")
        return manifest, errors

    md_files = _iter_markdown_files(investment_dir)
    for md in md_files:
        rel = str(md.relative_to(root))
        try:
            stat = md.stat()
            sha1 = _file_sha1_prefix(md)
            manifest[rel] = ManifestEntry(
                size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                sha1_prefix=sha1,
            )
        except OSError as exc:  # pragma: no cover - 容错
            errors.append(f"{rel}: 读取 stat 失败 {exc!r}")

    return manifest, errors


def _manifest_matches(
    cached: Dict[str, ManifestEntry],
    current: Dict[str, ManifestEntry],
) -> Tuple[bool, List[str]]:
    """比较两份 manifest 是否一致。

    返回 ``(is_match, diff_reasons)``。``diff_reasons`` 描述差异类型（新增/删除/修改），
    便于在 freshness explain 中透出。
    """
    reasons: List[str] = []
    if set(cached.keys()) != set(current.keys()):
        added = set(current.keys()) - set(cached.keys())
        removed = set(cached.keys()) - set(current.keys())
        if added:
            reasons.append(f"新增 {len(added)} 个文件")
        if removed:
            reasons.append(f"删除 {len(removed)} 个文件")
        return False, reasons

    changed: List[str] = []
    for rel, cur in current.items():
        old = cached.get(rel)
        if old is None:
            changed.append(rel)
            continue
        if old.size != cur.size or old.mtime_ns != cur.mtime_ns:
            changed.append(rel)
            continue
        # sha1 兜底：仅当两者都非空且不一致才判变（避免文件系统不支持 sha1 时误判）。
        if old.sha1_prefix and cur.sha1_prefix and old.sha1_prefix != cur.sha1_prefix:
            changed.append(rel)

    if changed:
        reasons.append(f"修改 {len(changed)} 个文件")
        return False, reasons
    return True, []


# ── 单页预解析（复用 KB-001/KB-003 解析逻辑，保持口径一致）────────────


def _build_cached_page(
    rel_path: str,
    abs_path: Path,
) -> Tuple[Optional[CachedPageData], Optional[str]]:
    """解析单页并填充 CachedPageData。

    返回 ``(page_data, error)``：成功时 ``error=None``；失败时 ``page_data=None``。
    复用 KB-001 的 ``_audit_single_page`` 与 KB-003 的段落抽取，确保缓存与全量
    扫描语义一致。
    """
    try:
        page_audit = _audit_single_page(rel_path, abs_path)
    except Exception as exc:  # pragma: no cover - 容错
        return None, f"{rel_path}: 审计失败 {exc!r}"

    try:
        text = _read_text_safe(abs_path)
        fm_text, body = _split_frontmatter(text)
        frontmatter = _parse_frontmatter(fm_text)
    except Exception as exc:  # pragma: no cover
        return None, f"{rel_path}: 解析失败 {exc!r}"

    symbols = _normalize_symbol_list(frontmatter.get("symbols"))
    themes = _normalize_str_list(frontmatter.get("themes"))
    tags = _normalize_str_list(frontmatter.get("tags"))
    summary = _extract_section_text(body, _SUMMARY_HEADERS, _SUMMARY_MAX_CHARS)
    risks = _extract_risk_items(body, max_items=_MAX_RISK_ENTRIES)
    fm_sources = _normalize_str_list(frontmatter.get("sources"))
    sources = _extract_sources(fm_sources, body, max_items=_MAX_SOURCE_ENTRIES)

    return (
        CachedPageData(
            rel_path=rel_path,
            title=page_audit.title or abs_path.stem,
            page_type=page_audit.page_type,
            machine_readiness=page_audit.machine_readiness,
            is_to_be_supplemented=page_audit.is_to_be_supplemented,
            stale_risk_high=page_audit.stale_risk_high,
            valid_until_expired=page_audit.valid_until_expired,
            valid_until=page_audit.valid_until,
            updated_at=page_audit.updated_at,
            size_bytes=page_audit.size_bytes,
            line_count=page_audit.line_count,
            frontmatter=_sanitize_jsonable(frontmatter),
            symbols=symbols,
            themes=themes,
            tags=tags,
            summary=summary,
            risks=risks,
            sources=sources,
        ),
        None,
    )


# ── 主入口：get_or_build_cache ───────────────────────────────────────


def default_cache_path(cache_dir: str = _DEFAULT_CACHE_DIR) -> str:
    """返回默认缓存文件路径 ``.cache/knowledge_cache.json``。"""
    return os.path.join(cache_dir, _DEFAULT_CACHE_FILENAME)


def build_cache_from_scan(knowledge_root: str) -> KnowledgeCache:
    """全量扫描知识库并构建缓存（不读 / 不写落盘缓存）。

    供 ``get_or_build_cache`` 在缓存缺失 / stale / 损坏时调用，也可单独用于
    首次预热。永远不会抛异常：单页失败记入 ``errors``。
    """
    root = Path(knowledge_root).expanduser()
    cache = KnowledgeCache(
        contract_version=CONTRACT_VERSION,
        knowledge_root=str(root),
        built_at=datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    )

    manifest, man_errors = build_manifest(knowledge_root)
    cache.manifest = manifest
    cache.errors.extend(man_errors)

    if not manifest:
        # 知识库不可读 / investment 分区不存在 / 无 md 文件。
        return cache

    for rel in manifest.keys():
        abs_path = root / rel
        if not abs_path.exists():
            cache.errors.append(f"{rel}: 文件不存在（跳过）")
            continue
        page_data, err = _build_cached_page(rel, abs_path)
        if page_data is not None:
            cache.pages[rel] = page_data
        elif err:
            cache.errors.append(err)

    return cache


def load_cache_from_disk(
    cache_path: str,
    expected_knowledge_root: Optional[str] = None,
) -> Tuple[Optional[KnowledgeCache], Optional[str]]:
    """从 JSON 文件加载缓存。

    返回 ``(cache, error)``：成功时 ``error=None``；失败时 ``cache=None`` 且
    ``error`` 描述原因（文件不存在 / JSON 损坏 / contract_version 不匹配 /
    knowledge_root 不匹配）。调用方应根据 error 自动回退到全量重建。
    """
    path = Path(cache_path).expanduser()
    if not path.exists():
        return None, f"缓存文件不存在: {path}"

    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"缓存文件损坏（将重建）: {exc!r}"

    if not isinstance(raw, dict):
        return None, "缓存结构非 dict（将重建）"

    version = str(raw.get("contract_version") or "")
    if version != CONTRACT_VERSION:
        return None, f"contract_version 不匹配（{version} != {CONTRACT_VERSION}）"

    cache = KnowledgeCache(
        contract_version=version,
        knowledge_root=str(raw.get("knowledge_root") or ""),
        built_at=str(raw.get("built_at") or ""),
        errors=list(raw.get("errors") or []),
        cache_path=str(path),
    )

    if (
        expected_knowledge_root
        and cache.knowledge_root
        and cache.knowledge_root != str(Path(expected_knowledge_root).expanduser())
    ):
        return None, (
            f"knowledge_root 不匹配（缓存={cache.knowledge_root}, "
            f"当前={Path(expected_knowledge_root).expanduser()}）"
        )

    manifest_raw = raw.get("manifest")
    if isinstance(manifest_raw, dict):
        cache.manifest = {
            str(k): ManifestEntry.from_dict(v)
            for k, v in manifest_raw.items()
            if isinstance(v, dict)
        }

    pages_raw = raw.get("pages")
    if not isinstance(pages_raw, dict):
        return None, "缓存缺少 pages 字段（将重建）"
    cache.pages = {
        str(k): CachedPageData.from_dict(v)
        for k, v in pages_raw.items()
        if isinstance(v, dict)
    }
    missing_pages = sorted(set(cache.manifest) - set(cache.pages))
    if missing_pages:
        sample = ", ".join(missing_pages[:3])
        return None, f"缓存 pages 不完整（缺少 {len(missing_pages)} 页：{sample}）"

    return cache, None


def save_cache_to_disk(cache: KnowledgeCache, cache_path: str) -> Optional[str]:
    """把缓存写入 JSON 文件。

    返回 ``error``：成功时为 ``None``。失败时不抛异常，调用方可在 errors 中记录。
    目录不存在时自动创建。**绝不写知识库目录**（调用方应传入项目运行目录）。
    """
    path = Path(cache_path).expanduser()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(cache.to_dict(), fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        return f"缓存写入失败: {exc!r}"
    cache.cache_path = str(path)
    return None


def get_or_build_cache(
    knowledge_root: str,
    *,
    cache_path: Optional[str] = None,
    rebuild: bool = False,
    no_cache: bool = False,
) -> KnowledgeCache:
    """主入口：获取或构建知识库缓存。

    参数:
        knowledge_root: 知识库根目录绝对路径。
        cache_path: 缓存 JSON 文件路径。``None`` 时使用
            :func:`default_cache_path`。仅在 ``no_cache=False`` 时生效。
        rebuild: 强制重建（忽略落盘缓存，重建后写回）。对应 CLI ``--rebuild-cache``。
        no_cache: 完全禁用缓存（内存构建，不读 / 不写落盘）。对应 CLI ``--no-cache``。

    返回:
        :class:`KnowledgeCache`。``freshness_status`` 字段反映本次调用的结果：
          - ``fresh``：复用落盘缓存且 manifest 完全匹配。
          - ``stale``：缓存有变更，已自动重建。
          - ``missing``：缓存文件不存在，已首次构建。
          - ``error``：知识库不可读或解析异常。

    永远不会抛异常；落盘失败 / 缓存损坏均回退到全量扫描。
    """
    root = Path(knowledge_root).expanduser()

    # no_cache：完全跳过落盘缓存，只在内存构建。
    if no_cache:
        cache = build_cache_from_scan(str(root))
        cache.freshness_status = (
            FRESHNESS_ERROR if not cache.manifest and cache.errors else FRESHNESS_STALE
        )
        # no_cache 不写盘，cache_path 保持 None。
        return cache

    cpath = cache_path or default_cache_path()

    # rebuild：强制忽略落盘缓存。
    if not rebuild:
        disk_cache, load_err = load_cache_from_disk(cpath, expected_knowledge_root=str(root))
        if disk_cache is not None:
            # 落盘缓存可读 → 比对 manifest。
            current_manifest, _ = build_manifest(str(root))
            is_match, _diff_reasons = _manifest_matches(
                disk_cache.manifest, current_manifest
            )
            if is_match:
                disk_cache.freshness_status = FRESHNESS_FRESH
                disk_cache.reused_disk_cache = True
                return disk_cache
            # manifest 不一致 → 重建（落盘缓存页面数据不再可信）。
            rebuilt = build_cache_from_scan(str(root))
            rebuilt.freshness_status = FRESHNESS_STALE
            write_err = save_cache_to_disk(rebuilt, cpath)
            if write_err:
                rebuilt.errors.append(write_err)
            return rebuilt
        # load_cache_from_disk 返回 None（文件缺失 / 损坏 / 版本不匹配）。
        # load_err 区分 "文件不存在"（freshness=missing）vs "损坏"（error）。
        rebuilt = build_cache_from_scan(str(root))
        if not rebuilt.manifest and rebuilt.errors:
            # 知识库本身不可读。
            rebuilt.freshness_status = FRESHNESS_ERROR
            return rebuilt
        if load_err and "不存在" in load_err:
            rebuilt.freshness_status = FRESHNESS_MISSING
        else:
            rebuilt.freshness_status = FRESHNESS_STALE
            rebuilt.errors.append(f"落盘缓存不可用：{load_err}")
        write_err = save_cache_to_disk(rebuilt, cpath)
        if write_err:
            rebuilt.errors.append(write_err)
        return rebuilt

    # rebuild=True：强制重建并写回。
    rebuilt = build_cache_from_scan(str(root))
    if not rebuilt.manifest and rebuilt.errors:
        rebuilt.freshness_status = FRESHNESS_ERROR
        return rebuilt
    rebuilt.freshness_status = FRESHNESS_STALE
    write_err = save_cache_to_disk(rebuilt, cpath)
    if write_err:
        rebuilt.errors.append(write_err)
    return rebuilt


# ── 缓存驱动的查询（KB-003 命中匹配，不重新读文件）────────────────────


def _cached_page_to_match(
    page: CachedPageData,
    matched_by: List[str],
) -> LocalKnowledgeMatch:
    """把缓存页面数据转为 :class:`LocalKnowledgeMatch`（口径与 KB-003 一致）。

    复用 KB-003 的 stale / low_confidence / confidence 计算，确保缓存命中与
    全量扫描产出**语义等价**的命中对象。
    """
    # 复用 KB-003 _build_match 的 stale/low 判定口径。
    frontmatter = page.frontmatter if isinstance(page.frontmatter, dict) else {}
    stale_val = _safe_str(frontmatter.get("stale_risk")) or ""
    is_stale = (
        page.stale_risk_high
        or stale_val in HIGH_STALE_RISK_VALUES
        or page.valid_until_expired
    )
    evidence = _safe_str(frontmatter.get("evidence_level")) or ""
    is_low_conf = evidence in LOW_CONFIDENCE_EVIDENCE_LEVELS
    is_todo = page.is_to_be_supplemented

    if is_todo or is_low_conf:
        confidence = "low"
    elif is_stale:
        confidence = "low"
    elif page.machine_readiness == "high":
        confidence = "high"
    elif page.machine_readiness == "medium":
        confidence = "medium"
    else:
        confidence = "low"

    # [KB-014] citation_policy — 缓存路径同样评估来源层级，与全量扫描语义等价。
    from tradingagents.dataflows.citation_policy import (
        apply_tier_to_confidence,
        classify_source_quality_tier,
        compute_tier_confidence_weight,
    )

    citation = classify_source_quality_tier(frontmatter)
    tier_weight = compute_tier_confidence_weight(
        citation.tier,
        page.machine_readiness,
        is_stale=is_stale,
        is_low_confidence=is_low_conf,
        is_to_be_supplemented=is_todo,
    )
    adjusted_confidence = apply_tier_to_confidence(confidence, citation)

    return LocalKnowledgeMatch(
        rel_path=page.rel_path,
        title=page.title,
        page_type=page.page_type,
        summary=page.summary,
        risks=list(page.risks),
        sources=list(page.sources),
        symbols=list(page.symbols),
        themes=list(page.themes),
        tags=list(page.tags),
        updated_at=page.updated_at,
        machine_readiness=page.machine_readiness,
        is_stale=is_stale,
        is_low_confidence=is_low_conf,
        is_to_be_supplemented=is_todo,
        matched_by=list(matched_by),
        confidence=adjusted_confidence,
        # KB-014 来源可信度分层
        source_quality_tier=citation.tier,
        citation_confidence_weight=tier_weight,
    )


def _page_matches_from_cache(
    page: CachedPageData,
    *,
    symbol: Optional[str],
    name: Optional[str],
    themes: Optional[List[str]],
    tags: Optional[List[str]],
) -> List[str]:
    """缓存驱动的命中判定（与 KB-003 ``_page_matches`` 口径一致）。

    复用 KB-003 的 ``_symbol_matches / _name_matches / _theme_matches /
    _tags_match``，确保缓存与全量扫描结果一致。
    """
    from tradingagents.dataflows.local_knowledge_provider import (
        _name_matches,
        _symbol_matches,
        _tags_match,
        _theme_matches,
    )

    matched: List[str] = []
    if symbol:
        if _symbol_matches(symbol, page.symbols):
            matched.append("symbol")
    if name:
        if _name_matches(name, page.title or "", page.rel_path):
            matched.append("name")
    if themes:
        if _theme_matches(themes, page.themes):
            matched.append("theme")
    if tags:
        if _tags_match(tags, page.tags):
            matched.append("tag")
    return matched


def query_local_knowledge_cached(
    cache: KnowledgeCache,
    *,
    symbol: Optional[str] = None,
    name: Optional[str] = None,
    themes: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
    max_pages: int = _MAX_MATCHED_PAGES,
) -> LocalKnowledgeQueryResult:
    """基于缓存执行 KB-003 命中查询（不重新读文件）。

    与 :func:`local_knowledge_provider.query_local_knowledge` 语义等价：
      - 同样的查询条件（symbol/name/themes/tags 任一）。
      - 同样的状态机（HAS_DATA / NORMAL_NO_DATA / FAILED / STALE / LOW_CONFIDENCE）。
      - 同样的排序与截断（page_type_rank → readiness → rel_path，前 N 条）。

    参数:
        cache: 已构建的 :class:`KnowledgeCache`（通常由 :func:`get_or_build_cache`
            返回）。
        symbol/name/themes/tags: 与 KB-003 一致。
        max_pages: 最多返回的命中页数。

    返回:
        :class:`LocalKnowledgeQueryResult`。缓存为空 / 知识库不可读时返回
        ``status=FAILED`` 并把 ``cache.errors`` 透传到 ``result.errors``。
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

    result = LocalKnowledgeQueryResult(
        knowledge_root=cache.knowledge_root,
        query=query_desc,
    )
    result.errors.extend(cache.errors or [])

    # 知识库不可读（manifest 为空且有错误）→ FAILED。
    if not cache.pages and cache.errors:
        result.status = STATUS_FAILED
        return result

    if not any([symbol, name, themes_norm, tags_norm]):
        result.status = STATUS_NORMAL_NO_DATA
        result.errors.append("未提供查询条件（symbol/name/themes/tags 任一）")
        return result

    for rel, page in cache.pages.items():
        matched_by = _page_matches_from_cache(
            page,
            symbol=symbol,
            name=name,
            themes=themes_norm,
            tags=tags_norm,
        )
        if not matched_by:
            continue
        result.matched_pages.append(_cached_page_to_match(page, matched_by))

    result.matched_pages.sort(key=_rank_key)
    if len(result.matched_pages) > max_pages:
        result.matched_pages = result.matched_pages[:max_pages]

    _aggregate_result(result)
    return result


# ── freshness manifest 摘要（供 CLI / 报告透出）──────────────────────


def freshness_summary(cache: KnowledgeCache) -> Dict[str, Any]:
    """生成缓存 freshness 摘要（用于 CLI 输出 / 任务运行档案）。

    返回字段：
      - ``freshness_status``：fresh / stale / missing / error。
      - ``knowledge_root``：缓存对应的知识库根目录。
      - ``built_at``：缓存构建时间。
      - ``page_count``：缓存中的页面数。
      - ``manifest_size``：manifest 条目数。
      - ``cache_path``：落盘路径（None 表示仅在内存）。
      - ``reused_disk_cache``：是否复用了落盘缓存（未触发全量重建）。
      - ``errors``：错误列表（前 5 条）。
    """
    return {
        "freshness_status": cache.freshness_status,
        "knowledge_root": cache.knowledge_root,
        "built_at": cache.built_at,
        "page_count": len(cache.pages),
        "manifest_size": len(cache.manifest),
        "cache_path": cache.cache_path,
        "reused_disk_cache": cache.reused_disk_cache,
        "errors": list(cache.errors or [])[:5],
    }


__all__ = [
    "CONTRACT_VERSION",
    "TASK_CODE",
    "FRESHNESS_FRESH",
    "FRESHNESS_STALE",
    "FRESHNESS_MISSING",
    "FRESHNESS_ERROR",
    "ManifestEntry",
    "CachedPageData",
    "KnowledgeCache",
    "build_manifest",
    "build_cache_from_scan",
    "load_cache_from_disk",
    "save_cache_to_disk",
    "get_or_build_cache",
    "query_local_knowledge_cached",
    "freshness_summary",
    "default_cache_path",
]
