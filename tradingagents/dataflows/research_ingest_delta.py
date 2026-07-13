# [KB-019] research_ingest_delta
"""Tree Work 研报增量摄取清单与重复导入预检。

面向半年报集中披露期，把 ``inbox`` / ``raw`` / ``wiki`` 的新增、已消化、
重复、缺字段、待更新资料整理为**可回查的增量摄取清单**，避免同一研报被重复
消化或遗漏。

设计约束（对应任务 KB-019）：
  - **只读**：仅用 ``open(..., "r", encoding="utf-8")`` / ``Path.iterdir`` /
    ``Path.stat``，绝不向知识库写文件、不移动 / 不删除任何 knowledge 文件。
  - **不复制长篇研报正文**：清单只含路径 / 分类 / 状态 / 简短原因 / 元数据。
  - **不调用 live LLM / 不访问外网**：纯标准库 + 复用 KB-001 / KB-005 / KB-010
    只读解析能力。
  - **不写生产 DB**：不持久化任何状态到 SQLite。
  - **优先复用**：``tree_work_backlog.py`` (KB-005)、``local_knowledge_cache.py``
    (KB-010)、``half_year_task_pack.py`` (HY-002) 的解析口径，**禁止另造第二套
    索引**。
  - **重复预检不自动删除**：只标注 ``duplicate_of``，由 Tree Work 人工决定。

``ingest_key`` 合成（稳定 16 字符 sha1 前缀）::

    raw = "|".join([
        normalized(rel_path),
        fingerprint_prefix,
        normalized(symbol),
        normalized(report_date),
        normalized(institution),
        normalized(source_url),
    ])
    ingest_key = sha1(raw)[:16]

其中 ``fingerprint`` = sha1(文件前 1 MB 内容)[:16]；对二进制 PDF 等同样适用。
``ingest_key`` 既区分"同内容不同路径"（相同 fingerprint，不同 path），也区分
"同路径不同版本"（相同 path，不同 fingerprint）。

六类 ``status``：

  - ``new``：未被任何 wiki sources 引用，且非重复（待 Tree Work 消化）。
  - ``digested``：已被至少一个 wiki/investment 页 ``sources`` 引用。
  - ``duplicate``：与另一文件 fingerprint 完全相同（同一篇研报多文件），
    或同 institution + 同 report_date + 同 symbol 且标题相似度 ≥ 阈值。
    保留首次出现的为"原始"，其余标 ``duplicate_of``。
  - ``needs_metadata``：缺关键元数据（``report_date`` / ``institution`` /
    ``symbol`` 任一），无法生成稳定 ingest_key 的"业务字段"部分。
  - ``stale``：已被 wiki 引用但引用页 ``stale_risk=高`` 或 ``valid_until`` 过期。
  - ``conflict``：与另一文件 ``ingest_key`` 完全相同（同 path + 同元数据）但
    ``fingerprint`` 不同，通常意味着文件被重新 OCR / 替换 / 覆盖，需要人工复核。

使用示例::

    from tradingagents.dataflows.research_ingest_delta import (
        build_research_ingest_delta,
        render_ingest_delta_report,
    )
    delta = build_research_ingest_delta("/Users/maybee/Documents/knowledge")
    print(render_ingest_delta_report(delta))
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 复用 KB-001 / KB-005 / KB-010 / HY-002 的只读解析，保持单一实现。
from tradingagents.dataflows.local_knowledge_audit import (
    INDEX_MD,
    INBOX_SUBDIR,
    INVESTMENT_SUBDIR,
    LOG_MD,
    RAW_SUBDIR,
    _iter_markdown_files,
    _parse_frontmatter,
    _read_text_safe,
    _safe_str,
    _split_frontmatter,
    default_knowledge_root,
)
from tradingagents.dataflows.tree_work_backlog import (
    TreeWorkBacklog,
    build_tree_work_backlog,
    extract_raw_references,
)


# ── 常量 ──────────────────────────────────────────────────────────────

TASK_CODE = "KB-019"
CONTRACT_VERSION = "kb-019-v1"

# status 枚举（与任务原文严格一致）。
STATUS_NEW = "new"
STATUS_DIGESTED = "digested"
STATUS_DUPLICATE = "duplicate"
STATUS_NEEDS_METADATA = "needs_metadata"
STATUS_STALE = "stale"
STATUS_CONFLICT = "conflict"

STATUS_ORDER: Tuple[str, ...] = (
    STATUS_CONFLICT,  # 冲突最优先暴露
    STATUS_DUPLICATE,
    STATUS_NEEDS_METADATA,
    STATUS_STALE,
    STATUS_NEW,
    STATUS_DIGESTED,
)
ALL_STATUSES: Tuple[str, ...] = (
    STATUS_NEW,
    STATUS_DIGESTED,
    STATUS_DUPLICATE,
    STATUS_NEEDS_METADATA,
    STATUS_STALE,
    STATUS_CONFLICT,
)

STATUS_TITLES: Dict[str, str] = {
    STATUS_NEW: "新增（待消化）",
    STATUS_DIGESTED: "已消化（已被 wiki 引用）",
    STATUS_DUPLICATE: "重复（同一研报多文件）",
    STATUS_NEEDS_METADATA: "缺元数据（无法稳定索引）",
    STATUS_STALE: "待更新（引用页过期）",
    STATUS_CONFLICT: "冲突（同 key 不同指纹）",
}

# 来源分区。
AREA_INBOX = "inbox"
AREA_RAW = "raw"
AREA_WIKI = "wiki"

# fingerprint 限制（只读前 1MB，加速）。
_FINGERPRINT_LIMIT_BYTES = 1_000_000
_FINGERPRINT_PREFIX_LEN = 16
_INGEST_KEY_LEN = 16

# 标题相似度阈值（用于"同机构同日同股近似标题"判定）。
_TITLE_SIMILARITY_THRESHOLD = 0.6

# 报告日期正则：支持 YYYY-MM-DD / YYYYMMDD / YYYY/MM/DD。
_DATE_PATTERNS = (
    re.compile(r"(20\d{2})-(\d{1,2})-(\d{1,2})"),
    re.compile(r"(20\d{2})/(\d{1,2})/(\d{1,2})"),
    re.compile(r"\b(20\d{2})(\d{2})(\d{2})\b"),
)

# 文件名 → institution / subject 的常见解析模式：
#   YYYY-MM-DD-<机构>-<标题>.ext
#   YYYYMMDD-<机构>-<标题>.ext
_FILENAME_DATED_RE = re.compile(
    r"^(?P<date>\d{4}[-/]?\d{1,2}[-/]?\d{1,2})-(?P<rest>.+)$"
)

# 常见券商/研究机构关键词（用于 institution 识别兜底）。
_INSTITUTION_KEYWORDS: Tuple[str, ...] = (
    "证券", "证券研究", "研究所", "投顾", "投资咨询",
    "基金", "资管", "信托", "期货",
    "中金", "中信", "中邮", "中银", "华泰", "华源", "国信", "国盛",
    "开源", "东吴", "东莞", "万联", "山西", "华鑫", "民生", "招商",
    "光大", "平安", "兴业", "长江", "方正", "安信", "财通", "浙商",
)

# A 股代码正则（6 位数字，可带 .SH/.SZ/.BJ 后缀）。
_STOCK_CODE_RE = re.compile(r"\b(6\d{5}|0\d{5}|3\d{5}|[48]\d{5})\b")
_STOCK_CODE_WITH_SUFFIX_RE = re.compile(
    r"\b(\d{6})\.(?:SH|SZ|BJ|sh|sz|bj)\b"
)


# ── 数据类 ────────────────────────────────────────────────────────────


@dataclass
class IngestItem:
    """一条增量摄取清单项。

    每条记录携带任务要求的溯源字段：
    ``ingest_key / status / source_area / location / fingerprint /
    symbol / report_date / institution / source_url``。
    """

    ingest_key: str
    status: str
    location: str  # 相对 knowledge_root 的路径
    source_area: str  # inbox / raw / wiki
    fingerprint: str = ""
    title: str = ""
    symbols: List[str] = field(default_factory=list)
    report_date: Optional[str] = None
    institution: Optional[str] = None
    source_url: Optional[str] = None
    reason: str = ""
    referenced_by: List[str] = field(default_factory=list)
    duplicate_of: Optional[str] = None  # 原始 ingest_key
    conflict_with: List[str] = field(default_factory=list)  # 冲突 ingest_keys
    extra: Dict[str, Any] = field(default_factory=dict)
    # 内部字段：标记本 item 是否被 stale wiki 引用（不进 to_dict）。
    _linked_wiki_stale: bool = field(default=False, repr=False, compare=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ingest_key": self.ingest_key,
            "status": self.status,
            "location": self.location,
            "source_area": self.source_area,
            "fingerprint": self.fingerprint,
            "title": self.title,
            "symbols": list(self.symbols),
            "report_date": self.report_date,
            "institution": self.institution,
            "source_url": self.source_url,
            "reason": self.reason,
            "referenced_by": list(self.referenced_by),
            "duplicate_of": self.duplicate_of,
            "conflict_with": list(self.conflict_with),
            "extra": dict(self.extra),
        }


@dataclass
class ResearchIngestDelta:
    """整库增量摄取清单聚合。"""

    knowledge_root: str
    generated_at: str
    as_of_date: str
    contract_version: str = CONTRACT_VERSION
    task: str = TASK_CODE
    items: List[IngestItem] = field(default_factory=list)
    # 上游统计。
    upstream_summary: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)

    def status_counts(self) -> Dict[str, int]:
        return {status: sum(1 for it in self.items if it.status == status) for status in ALL_STATUSES}

    def total(self) -> int:
        return len(self.items)

    def items_by_status(self, status: str) -> List[IngestItem]:
        return [it for it in self.items if it.status == status]

    def all_items_sorted(self) -> List[IngestItem]:
        """按 status_order → source_area → location 稳定排序。"""
        order = {s: i for i, s in enumerate(STATUS_ORDER)}
        area_order = {AREA_WIKI: 0, AREA_RAW: 1, AREA_INBOX: 2}
        return sorted(
            self.items,
            key=lambda it: (
                order.get(it.status, 99),
                area_order.get(it.source_area, 9),
                it.location,
            ),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "knowledge_root": self.knowledge_root,
            "generated_at": self.generated_at,
            "as_of_date": self.as_of_date,
            "contract_version": self.contract_version,
            "task": self.task,
            "items": [it.to_dict() for it in self.items],
            "status_counts": self.status_counts(),
            "total": self.total(),
            "upstream_summary": dict(self.upstream_summary),
            "errors": list(self.errors),
        }


# ── 元数据抽取 ────────────────────────────────────────────────────────


def compute_fingerprint(path: Path, limit_bytes: int = _FINGERPRINT_LIMIT_BYTES) -> str:
    """读取文件前 ``limit_bytes`` 字节计算 sha1，返回 16 字符前缀。

    二进制（PDF / 图片）同样适用；读取失败时返回空串（容错）。
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
        return h.hexdigest()[:_FINGERPRINT_PREFIX_LEN]
    except OSError:
        return ""


def normalize_rel_path(rel_path: str) -> str:
    """归一化相对路径：``\\`` → ``/``，去首尾空白。"""
    if not rel_path:
        return ""
    return rel_path.replace("\\", "/").strip().lstrip("./")


def normalize_symbol(symbol: Optional[str]) -> str:
    """``603296.SH`` / ``603296.SH 华勤技术`` → ``603296``。"""
    if not symbol:
        return ""
    text = str(symbol).strip()
    if not text:
        return ""
    # 取第一个 token（去掉公司简称部分）。
    first = text.split()[0]
    # 去 .SH/.SZ 等后缀。
    m = re.match(r"^(\d{6})(?:\.(?:SH|SZ|BJ|sh|sz|bj))?$", first)
    return m.group(1) if m else first.lower()


def normalize_date(value: Optional[str]) -> Optional[str]:
    """把多种日期格式归一为 ``YYYY-MM-DD``；无法识别返回 None。"""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    for pat in _DATE_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        y, mo, d = m.group(1), m.group(2), m.group(3)
        try:
            return date(int(y), int(mo), int(d)).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def normalize_str(value: Optional[str]) -> str:
    if not value:
        return ""
    return str(value).strip()


def _extract_symbols_from_text(text: str) -> List[str]:
    """从文本中提取 A 股代码（去重，保持顺序）。"""
    if not text:
        return []
    found: List[str] = []
    seen: set = set()
    # 优先匹配带后缀的。
    for m in _STOCK_CODE_WITH_SUFFIX_RE.finditer(text):
        code = m.group(1)
        if code not in seen:
            seen.add(code)
            found.append(code)
    # 再匹配裸 6 位。
    for m in _STOCK_CODE_RE.finditer(text):
        code = m.group(1)
        if code not in seen:
            seen.add(code)
            found.append(code)
    return found


def _parse_filename_metadata(filename: str) -> Tuple[Optional[str], Optional[str], str]:
    """从文件名解析 ``(report_date, institution, title)``。

    约定：``YYYY-MM-DD-<机构>-<标题>.ext`` 或 ``YYYYMMDD-<机构>-<标题>.ext``。
    无法解析时返回 ``(None, None, filename_stem)``。
    """
    stem = Path(filename).stem
    m = _FILENAME_DATED_RE.match(stem)
    if not m:
        return None, None, stem
    date_raw = m.group("date")
    rest = m.group("rest").strip()
    report_date = normalize_date(date_raw)
    if not rest:
        return report_date, None, stem
    # 尝试拆分机构：第一个 ``-`` 之前的部分若含"证券/研究所"等关键词，视为机构。
    parts = rest.split("-", 1)
    if len(parts) == 2:
        head, tail = parts[0].strip(), parts[1].strip()
        if any(kw in head for kw in _INSTITUTION_KEYWORDS):
            return report_date, head, tail or stem
    # 兜底：检查整个 rest 是否含机构关键词。
    for kw in _INSTITUTION_KEYWORDS:
        idx = rest.find(kw)
        if idx >= 0:
            # 取关键词所在 token。
            head = rest.split("-")[0].strip()
            return report_date, head, rest
    return report_date, None, rest


def extract_research_metadata(
    abs_path: Path,
    rel_path: str,
) -> Dict[str, Any]:
    """抽取单条资料的元数据。

    合并优先级：frontmatter > filename；symbol 同时从 frontmatter / 正文兜底。

    返回 dict，键：
      ``title / symbols / report_date / institution / source_url / frontmatter``
    """
    text = _read_text_safe(abs_path)
    fm_text, body = _split_frontmatter(text)
    frontmatter = _parse_frontmatter(fm_text) if fm_text else {}

    title = _safe_str(frontmatter.get("title"))
    report_date_fm = normalize_date(_safe_str(frontmatter.get("report_date"))
                                    or _safe_str(frontmatter.get("date")))
    institution_fm = _safe_str(frontmatter.get("institution"))
    source_url_fm = _safe_str(frontmatter.get("source_url")) or _safe_str(
        frontmatter.get("source_link")
    )

    # symbols：frontmatter 优先，再从正文兜底。
    symbols: List[str] = []
    fm_symbols = frontmatter.get("symbols")
    if isinstance(fm_symbols, list):
        for entry in fm_symbols:
            for code in _extract_symbols_from_text(str(entry)):
                if code not in symbols:
                    symbols.append(code)
    elif isinstance(fm_symbols, str):
        symbols = _extract_symbols_from_text(fm_symbols)
    if not symbols:
        # 文件名 + 正文兜底。
        for code in _extract_symbols_from_text(Path(rel_path).name):
            if code not in symbols:
                symbols.append(code)
        if body:
            for code in _extract_symbols_from_text(body[:8000]):
                if code not in symbols:
                    symbols.append(code)

    # 文件名兜底 date / institution / title。
    fn_date, fn_inst, fn_title = _parse_filename_metadata(Path(rel_path).name)

    return {
        "title": title or fn_title or Path(rel_path).stem,
        "symbols": symbols,
        "report_date": report_date_fm or fn_date,
        "institution": institution_fm or fn_inst,
        "source_url": source_url_fm,
        "frontmatter": frontmatter,
    }


# ── ingest_key 合成 ──────────────────────────────────────────────────


def compute_ingest_key(
    rel_path: str,
    fingerprint: str,
    symbol: str,
    report_date: Optional[str],
    institution: Optional[str],
    source_url: Optional[str],
) -> str:
    """生成稳定的 16 字符 ingest_key。

    相对路径、文件指纹、symbol、report_date、institution、source_url 都参与；
    任一组件变化都会产生不同的 key，便于区分"同内容不同路径"与"同路径不同版本"。
    """
    parts = [
        normalize_rel_path(rel_path),
        (fingerprint or "")[:_FINGERPRINT_PREFIX_LEN],
        normalize_symbol(symbol),
        normalize_date(report_date) or "",
        normalize_str(institution).lower(),
        normalize_str(source_url).lower(),
    ]
    raw = "|".join(parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:_INGEST_KEY_LEN]


def compute_business_key(
    symbol: str,
    report_date: Optional[str],
    institution: Optional[str],
    source_url: Optional[str],
) -> str:
    """业务字段 key：``symbol|date|institution|source_url``。

    用于"同机构同日同股"重复检测；不含 path / fingerprint，
    所以能识别"同一篇研报被存到不同文件名"的情形。
    """
    parts = [
        normalize_symbol(symbol),
        normalize_date(report_date) or "",
        normalize_str(institution).lower(),
        normalize_str(source_url).lower(),
    ]
    return "|".join(parts)


# ── 标题相似度（用于近似重复检测）──────────────────────────────────────


def _tokenize_title(title: str) -> set:
    """简易分词：词级 token（≥2 字符）+ 字符 bigram（捕捉中文子串重叠）。"""
    if not title:
        return set()
    sep_re = r"[\s\-_/\\|：:.,，。()（）\[\]【】]+"
    tokens: set = set()
    # 词级 token。
    for t in re.split(sep_re, title):
        if len(t) >= 2:
            tokens.add(t.lower())
    # 字符 bigram（去除分隔符后取相邻 2 字符）。
    cleaned = re.sub(sep_re, "", title)
    for i in range(len(cleaned) - 1):
        bg = cleaned[i : i + 2].lower()
        if bg:
            tokens.add(bg)
    return tokens


def title_similarity(a: str, b: str) -> float:
    """Sørensen–Dice 相似度（基于字符 bigram + 词 token）。

    相比 Jaccard，Dice 对"一个标题是另一个的子串"更敏感，适合检测
    "同机构同日同股 + 近似标题"的重复。
    """
    ta = _tokenize_title(a)
    tb = _tokenize_title(b)
    if not ta or not tb:
        return 0.0
    inter = ta & tb
    return (2.0 * len(inter)) / (len(ta) + len(tb))


# ── 上游 KB-005 backlog 复用 ─────────────────────────────────────────


def _collect_referenced_raw_names(backlog: TreeWorkBacklog) -> set:
    """从 KB-005 backlog 收集已被 wiki sources 引用的 raw 文件名集合。

    backlog 本身已经遍历过 wiki 页并提取了 raw_references（KB-005 内部），
    但 ``TreeWorkBacklog`` 公开字段只有 ``raw_referenced`` 计数。为了拿到具体
    名称集合，这里重新跑一次 KB-005 的 wiki 扫描（同样的只读口径）。
    """
    referenced: set = set()
    root = Path(backlog.knowledge_root)
    investment_dir = root / INVESTMENT_SUBDIR
    if not investment_dir.exists():
        return referenced
    for md in _iter_markdown_files(investment_dir):
        try:
            text = _read_text_safe(md)
            fm_text, body = _split_frontmatter(text)
            frontmatter = _parse_frontmatter(fm_text) if fm_text else {}
            for name in extract_raw_references(frontmatter, body):
                referenced.add(name)
        except Exception:  # pragma: no cover - 容错
            continue
    return referenced


# ── 单页 stale 判定 ─────────────────────────────────────────────────


def _is_wiki_page_stale(frontmatter: Dict[str, Any]) -> bool:
    """根据 KB-001 口径判断 wiki frontmatter 是否过期。"""
    stale_val = _safe_str(frontmatter.get("stale_risk")) or ""
    if stale_val in ("高", "high", "HIGH", "高（需更新）"):
        return True
    valid_until = _safe_str(frontmatter.get("valid_until"))
    if valid_until:
        cleaned = re.sub(r"[/-]", "", valid_until)
        if re.fullmatch(r"\d{8}", cleaned):
            try:
                vu_date = date(int(cleaned[:4]), int(cleaned[4:6]), int(cleaned[6:8]))
                if vu_date < date.today():
                    return True
            except ValueError:
                pass
    return False


# ── 主构建逻辑 ────────────────────────────────────────────────────────


def _scan_raw_like_files(
    knowledge_root: Path,
    *,
    include_inbox: bool = True,
    include_raw: bool = True,
) -> List[Tuple[Path, str, str]]:
    """扫描 inbox / raw 下所有文件（含子目录），返回 ``(abs_path, rel_path, area)``。

    wiki/investment 的 markdown 页由 :func:`_scan_wiki_pages` 单独处理。
    """
    out: List[Tuple[Path, str, str]] = []
    if include_inbox:
        inbox_dir = knowledge_root / INBOX_SUBDIR
        if inbox_dir.exists():
            for path in sorted(inbox_dir.rglob("*")):
                if path.is_file():
                    rel = str(path.relative_to(knowledge_root))
                    out.append((path, rel, AREA_INBOX))
    if include_raw:
        raw_dir = knowledge_root / RAW_SUBDIR
        if raw_dir.exists():
            for path in sorted(raw_dir.rglob("*")):
                if path.is_file():
                    rel = str(path.relative_to(knowledge_root))
                    out.append((path, rel, AREA_RAW))
    return out


def _scan_wiki_pages(knowledge_root: Path) -> List[Tuple[Path, str, Dict[str, Any]]]:
    """扫描 wiki/investment markdown 页，返回 ``(abs_path, rel_path, frontmatter)``。"""
    out: List[Tuple[Path, str, Dict[str, Any]]] = []
    investment_dir = knowledge_root / INVESTMENT_SUBDIR
    if not investment_dir.exists():
        return out
    for md in _iter_markdown_files(investment_dir):
        try:
            text = _read_text_safe(md)
            fm_text, _body = _split_frontmatter(text)
            frontmatter = _parse_frontmatter(fm_text) if fm_text else {}
        except Exception:  # pragma: no cover - 容错
            frontmatter = {}
        rel = str(md.relative_to(knowledge_root))
        out.append((md, rel, frontmatter))
    return out


def _build_items_initial(
    knowledge_root: Path,
    *,
    include_inbox: bool,
    include_raw: bool,
    include_wiki: bool,
) -> Tuple[List[IngestItem], List[str]]:
    """初始扫描：构建 IngestItem 列表（不含 status / duplicate / conflict）。

    返回 ``(items, errors)``。``status`` 字段先填空，后续阶段统一判定。
    """
    items: List[IngestItem] = []
    errors: List[str] = []

    # inbox / raw
    for abs_path, rel_path, area in _scan_raw_like_files(
        knowledge_root,
        include_inbox=include_inbox,
        include_raw=include_raw,
    ):
        try:
            meta = extract_research_metadata(abs_path, rel_path)
        except Exception as exc:  # pragma: no cover - 容错
            errors.append(f"{rel_path}: 元数据抽取失败 {exc!r}")
            meta = {"title": Path(rel_path).stem, "symbols": [], "report_date": None,
                    "institution": None, "source_url": None, "frontmatter": {}}
        fingerprint = compute_fingerprint(abs_path)
        try:
            size_bytes = abs_path.stat().st_size
        except OSError:
            size_bytes = 0
        primary_symbol = meta["symbols"][0] if meta["symbols"] else ""
        ingest_key = compute_ingest_key(
            rel_path,
            fingerprint,
            primary_symbol,
            meta["report_date"],
            meta["institution"],
            meta["source_url"],
        )
        items.append(
            IngestItem(
                ingest_key=ingest_key,
                status="",  # 后续阶段统一判定
                location=rel_path,
                source_area=area,
                fingerprint=fingerprint,
                title=meta["title"],
                symbols=list(meta["symbols"]),
                report_date=meta["report_date"],
                institution=meta["institution"],
                source_url=meta["source_url"],
                extra={
                    "business_key": compute_business_key(
                        primary_symbol,
                        meta["report_date"],
                        meta["institution"],
                        meta["source_url"],
                    ),
                    "size_bytes": size_bytes,
                },
            )
        )

    # wiki/investment：只采集 stale / needs_metadata 信号（不参与 dup 检测）。
    if include_wiki:
        for abs_path, rel_path, frontmatter in _scan_wiki_pages(knowledge_root):
            try:
                size_bytes = abs_path.stat().st_size
            except OSError:
                size_bytes = 0
            fingerprint = compute_fingerprint(abs_path)
            symbols: List[str] = []
            fm_symbols = frontmatter.get("symbols")
            if isinstance(fm_symbols, list):
                for entry in fm_symbols:
                    for code in _extract_symbols_from_text(str(entry)):
                        if code not in symbols:
                            symbols.append(code)
            elif isinstance(fm_symbols, str):
                symbols = _extract_symbols_from_text(fm_symbols)
            title = _safe_str(frontmatter.get("title")) or Path(rel_path).stem
            primary_symbol = symbols[0] if symbols else ""
            items.append(
                IngestItem(
                    ingest_key=compute_ingest_key(
                        rel_path,
                        fingerprint,
                        primary_symbol,
                        normalize_date(_safe_str(frontmatter.get("updated"))
                                       or _safe_str(frontmatter.get("created"))),
                        None,
                        None,
                    ),
                    status="",
                    location=rel_path,
                    source_area=AREA_WIKI,
                    fingerprint=fingerprint,
                    title=title,
                    symbols=symbols,
                    report_date=normalize_date(_safe_str(frontmatter.get("updated"))),
                    institution=None,
                    source_url=None,
                    extra={
                        "size_bytes": size_bytes,
                        "frontmatter": _jsonable(frontmatter),
                    },
                )
            )

    return items, errors


def _jsonable(value: Any) -> Any:
    """把 frontmatter 中不可 JSON 序列化的值转为字符串（与 KB-010 同口径）。"""
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return str(value)


def _detect_duplicates_and_conflicts(items: List[IngestItem]) -> None:
    """两阶段重复 / 冲突检测。

    Phase 1 — 全局 fingerprint 重复：相同内容指纹 → ``duplicate``（按 location
    字典序第一个为"原始"，其余 ``duplicate_of`` 指向它）。

    Phase 2 — 同 business_key 重复 / 冲突：未被 Phase 1 标记、但
    ``symbol + date + institution`` 三要素齐全且相同的 items 中：
      - 标题 token Jaccard 相似度 ≥ 阈值 → ``duplicate``（任务原文：同机构同日
        同股近似标题做重复预检）。
      - 标题相似度 < 阈值 → ``conflict``（同业务身份但内容显著不同）。

    **只标注，不删除**。``duplicate`` 优先于 ``conflict``。
    """
    # ── Phase 1: 全局 fingerprint 重复 ──
    by_fp: Dict[str, List[IngestItem]] = {}
    for it in items:
        if it.source_area == AREA_WIKI or not it.fingerprint:
            continue
        by_fp.setdefault(it.fingerprint, []).append(it)
    for _fp, group in by_fp.items():
        if len(group) <= 1:
            continue
        sorted_items = sorted(group, key=lambda x: x.location)
        original = sorted_items[0]
        for it in sorted_items[1:]:
            if not it.duplicate_of:
                it.duplicate_of = original.ingest_key

    # ── Phase 2: 同 business_key 重复 / 冲突 ──
    by_bk: Dict[str, List[IngestItem]] = {}
    for it in items:
        if it.source_area == AREA_WIKI or not it.fingerprint:
            continue
        if it.duplicate_of:
            continue  # Phase 1 已标
        bk = it.extra.get("business_key") or ""
        parts = bk.split("|")
        if len(parts) < 4:
            continue
        symbol_part, date_part, inst_part, _url_part = parts
        # 三要素齐全才参与（symbol + date + institution）。
        if not (symbol_part and date_part and inst_part):
            continue
        by_bk.setdefault(bk, []).append(it)

    for _bk, group in by_bk.items():
        if len(group) <= 1:
            continue
        # 选择"原始"：按 fingerprint 分组，组内成员最多的 FP 集群为原始（更可能
        # 是真实原始文件而非变体）；平手时取文件较大者（更完整），再按 location
        # 字典序最小者，保证稳定。
        fp_groups: Dict[str, List[IngestItem]] = {}
        for it in group:
            fp_groups.setdefault(it.fingerprint, []).append(it)
        sorted_fps = sorted(
            fp_groups.keys(),
            key=lambda fp: (
                -len(fp_groups[fp]),
                -max(it.extra.get("size_bytes", 0) for it in fp_groups[fp]),
                min(it.location for it in fp_groups[fp]),
                fp,
            ),
        )
        original_fp = sorted_fps[0]
        original = min(
            fp_groups[original_fp],
            key=lambda x: (
                -x.extra.get("size_bytes", 0),
                x.location,
            ),
        )
        # 评估其他成员与原始的标题相似度。
        for it in sorted(group, key=lambda x: x.location):
            if it is original:
                continue
            sim = title_similarity(original.title, it.title)
            if sim >= _TITLE_SIMILARITY_THRESHOLD:
                if not it.duplicate_of:
                    it.duplicate_of = original.ingest_key
                    it.extra["title_similarity"] = round(sim, 3)
            else:
                if not it.conflict_with:
                    it.conflict_with.append(original.ingest_key)


def _classify_status(
    items: List[IngestItem],
    referenced_raw_names: set,
    stale_wiki_paths: set,
) -> None:
    """统一判定每条 item 的 status。优先级见 STATUS_ORDER。"""
    # 先建立 fingerprint → wiki stale 关联（raw 被 stale wiki 引用 → raw 也标 stale）。
    # 通过 wiki sources 反查（已在 KB-005 backlog 中算过）；这里用 referenced_raw_names。
    for it in items:
        status = _decide_status(
            it,
            referenced_raw_names=referenced_raw_names,
            stale_wiki_paths=stale_wiki_paths,
        )
        it.status = status
        it.reason = _explain_status(it)


def _decide_status(
    it: IngestItem,
    *,
    referenced_raw_names: set,
    stale_wiki_paths: set,
) -> str:
    """按 STATUS_ORDER 优先级判定单个 item 的 status。"""
    # wiki 页只走 needs_metadata / stale 两条路。
    if it.source_area == AREA_WIKI:
        fm = it.extra.get("frontmatter") or {}
        # 缺关键元数据：symbols / report_type / sources 任一为空 → needs_metadata。
        has_symbols = bool(it.symbols)
        has_report_type = bool(_safe_str(fm.get("report_type")))
        has_sources_field = bool(_is_nonempty_field(fm.get("sources")))
        if not (has_symbols and has_report_type and has_sources_field):
            return STATUS_NEEDS_METADATA
        if _is_wiki_page_stale(fm):
            return STATUS_STALE
        # wiki 页既不缺字段也不过期 → 不进入清单（在 build 时过滤）。
        return STATUS_DIGESTED

    # inbox / raw：按优先级。
    # 1. conflict 最高（同 path 不同 fingerprint）。
    if it.conflict_with:
        return STATUS_CONFLICT

    # 2. duplicate（同内容多文件 / 同机构同日同股近似标题）。
    if it.duplicate_of:
        return STATUS_DUPLICATE

    # 3. needs_metadata：缺关键元数据（symbol/date/institution 任一）。
    if not it.report_date or not it.institution or not it.symbols:
        return STATUS_NEEDS_METADATA

    # 4. stale：raw 被某 wiki 引用，但引用页 stale。
    raw_name = Path(it.location).name
    if raw_name in referenced_raw_names:
        # 找到引用它的 wiki 页是否过期。
        # 注意：stale_wiki_paths 是 wiki rel_path 集合；这里需要 name 级别的关联。
        # 简化：若 raw 被引用，且任何 stale wiki 存在 → 标 stale（保守）。
        # 更精确的关联在 _link_raw_to_stale_wiki 中完成。
        if it._linked_wiki_stale:
            return STATUS_STALE
        return STATUS_DIGESTED

    # 5. new：未被任何 wiki 引用。
    return STATUS_NEW


def _is_nonempty_field(value: Any) -> bool:
    """与 KB-001 同口径。"""
    if value is None:
        return False
    if isinstance(value, (list, tuple, dict)):
        return len(value) > 0
    if isinstance(value, str):
        return value.strip() not in ("", "[]", "{}", "无", "暂无")
    return True


def _explain_status(it: IngestItem) -> str:
    """生成简短的 status 原因（≤ 2 句）。"""
    if it.status == STATUS_NEW:
        return "未被任何 wiki sources 引用，待 Tree Work 消化为 wiki 页"
    if it.status == STATUS_DIGESTED:
        pages = ", ".join(it.referenced_by[:3])
        return f"已被 wiki 页引用：{pages}" if pages else "已被 wiki sources 引用"
    if it.status == STATUS_DUPLICATE:
        sim = it.extra.get("title_similarity")
        suffix = f"（标题相似度 {sim}）" if sim else ""
        return f"与 ingest_key={it.duplicate_of} 内容/元数据重复{suffix}；保留首次出现的为原始"
    if it.status == STATUS_NEEDS_METADATA:
        missing = []
        if not it.symbols:
            missing.append("symbol")
        if not it.report_date:
            missing.append("report_date")
        if not it.institution:
            missing.append("institution")
        if it.source_area == AREA_WIKI:
            fm = it.extra.get("frontmatter") or {}
            if not _safe_str(fm.get("report_type")):
                missing.append("report_type")
            if not _is_nonempty_field(fm.get("sources")):
                missing.append("sources")
        return f"缺关键元数据：{'、'.join(missing) or 'unknown'}；无法稳定索引"
    if it.status == STATUS_STALE:
        return "引用页 stale_risk=高 或 valid_until 已过期，需复核或补字段"
    if it.status == STATUS_CONFLICT:
        keys = ",".join(it.conflict_with[:3])
        return (
            f"同 symbol/date/institution 但 fingerprint 不同（冲突 keys: {keys}）；"
            "疑似版本差异 / OCR 差异 / 文件被覆盖，需人工复核"
        )
    return ""


def _link_raw_to_wiki(
    items: List[IngestItem],
    knowledge_root: Path,
) -> Tuple[set, set]:
    """建立 raw → 引用它的 wiki 页映射，并返回 (referenced_raw_names, stale_wiki_paths)。

    会逐页扫 wiki/investment，提取 raw_references + stale 判定。
    """
    referenced_raw_names: set = set()
    stale_wiki_paths: set = set()
    raw_name_to_items: Dict[str, List[IngestItem]] = {}
    for it in items:
        if it.source_area in (AREA_INBOX, AREA_RAW):
            raw_name_to_items.setdefault(Path(it.location).name, []).append(it)

    investment_dir = knowledge_root / INVESTMENT_SUBDIR
    if not investment_dir.exists():
        return referenced_raw_names, stale_wiki_paths

    for md in _iter_markdown_files(investment_dir):
        try:
            text = _read_text_safe(md)
            fm_text, body = _split_frontmatter(text)
            frontmatter = _parse_frontmatter(fm_text) if fm_text else {}
        except Exception:  # pragma: no cover
            continue
        wiki_rel = str(md.relative_to(knowledge_root))
        refs = extract_raw_references(frontmatter, body)
        is_stale = _is_wiki_page_stale(frontmatter)
        if is_stale:
            stale_wiki_paths.add(wiki_rel)
        for ref_name in refs:
            referenced_raw_names.add(ref_name)
            if is_stale:
                # 把 stale 信号传回被引用的 raw item。
                for it in raw_name_to_items.get(ref_name, []):
                    it._linked_wiki_stale = True
                    if wiki_rel not in it.referenced_by:
                        it.referenced_by.append(wiki_rel)
            else:
                for it in raw_name_to_items.get(ref_name, []):
                    if wiki_rel not in it.referenced_by:
                        it.referenced_by.append(wiki_rel)
    return referenced_raw_names, stale_wiki_paths


def build_research_ingest_delta(
    knowledge_root: str,
    *,
    include_inbox: bool = True,
    include_raw: bool = True,
    include_wiki: bool = True,
    use_backlog: bool = True,
) -> ResearchIngestDelta:
    """只读扫描知识库，产出研报增量摄取清单。

    参数:
        knowledge_root: 知识库根目录绝对路径。
        include_inbox / include_raw / include_wiki: 是否纳入对应分区。
        include_wiki=False 时只产出 inbox+raw 的增量清单（默认 True，因为
        wiki 也会贡献 stale / needs_metadata 信号）。
        use_backlog: 是否调用 KB-005 ``build_tree_work_backlog`` 收集上游统计。
            True 时会在 ``upstream_summary`` 里塞 ``kb005`` 子节。

    返回:
        :class:`ResearchIngestDelta`。任何上游异常都被吞掉并记入 ``errors``，
        清单仍能产出（基于直接扫描结果）。
    """
    root = Path(knowledge_root).expanduser()
    today = date.today()
    delta = ResearchIngestDelta(
        knowledge_root=str(root),
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        as_of_date=today.strftime("%Y-%m-%d"),
    )

    if not root.exists():
        delta.errors.append(f"knowledge_root 不存在: {root}")
        return delta

    # ── 1. 初始扫描 ──
    items, scan_errors = _build_items_initial(
        root,
        include_inbox=include_inbox,
        include_raw=include_raw,
        include_wiki=include_wiki,
    )
    delta.errors.extend(scan_errors)

    # ── 2. 建立 raw ↔ wiki 引用关系（同时收集 referenced_raw_names / stale wiki）──
    referenced_raw_names, stale_wiki_paths = _link_raw_to_wiki(items, root)

    # ── 3. 重复 / 冲突预检（两阶段） ──
    _detect_duplicates_and_conflicts(items)

    # ── 4. 状态判定 ──
    _classify_status(items, referenced_raw_names, stale_wiki_paths)

    # ── 5. 过滤：wiki 页既不缺字段也不过期的不进清单（避免噪声）──
    filtered: List[IngestItem] = []
    for it in items:
        if it.source_area == AREA_WIKI and it.status == STATUS_DIGESTED:
            continue
        filtered.append(it)
    delta.items = filtered

    # ── 6. 上游统计 ──
    delta.upstream_summary = {
        "raw_files_scanned": sum(1 for it in items if it.source_area == AREA_RAW),
        "inbox_files_scanned": sum(1 for it in items if it.source_area == AREA_INBOX),
        "wiki_pages_scanned": sum(1 for it in items if it.source_area == AREA_WIKI),
        "referenced_raw_names": len(referenced_raw_names),
        "stale_wiki_paths": len(stale_wiki_paths),
    }
    if use_backlog:
        try:
            backlog = build_tree_work_backlog(str(root))
            delta.upstream_summary["kb005"] = {
                "raw_total": backlog.raw_total,
                "raw_referenced": backlog.raw_referenced,
                "raw_undigested": len(backlog.raw_undigested),
                "inbox_total": backlog.inbox_total,
                "investment_page_count": backlog.investment_page_count,
                "errors": len(backlog.errors),
            }
            delta.errors.extend(backlog.errors[:5])  # 只透传前 5 条上游错误
        except Exception as exc:  # pragma: no cover - 容错
            delta.errors.append(f"KB-005 backlog 失败: {exc!r}")

    return delta


# ── 报告渲染 ─────────────────────────────────────────────────────────


_FORBIDDEN_ACTION_WORDS: Tuple[str, ...] = (
    "买入", "卖出", "加仓", "减仓", "强烈推荐", "立即买入", "全仓", "止损",
    "BUY", "SELL",
)


def has_forbidden_action_words(text: str) -> bool:
    """检查文本是否含强动作词（用于报告/JSON 输出防泄漏）。"""
    if not text:
        return False
    return any(w in text for w in _FORBIDDEN_ACTION_WORDS)


def _render_status_table(items: List[IngestItem]) -> List[str]:
    lines: List[str] = []
    if not items:
        lines.append("_（无）_")
        lines.append("")
        return lines
    lines.append("| ingest_key | 来源 | 路径 | 标题 | symbol | 日期 | 机构 | 原因 |")
    lines.append("|------------|------|------|------|--------|------|------|------|")
    for it in sorted(
        items,
        key=lambda x: (x.source_area, x.location),
    ):
        symbol = it.symbols[0] if it.symbols else "-"
        date_str = it.report_date or "-"
        inst = it.institution or "-"
        title = (it.title or "-").replace("|", "/")[:40]
        reason = (it.reason or "-").replace("|", "/")[:80]
        lines.append(
            f"| `{it.ingest_key}` | `{it.source_area}` | `{it.location}` | "
            f"{title} | `{symbol}` | {date_str} | {inst} | {reason} |"
        )
    lines.append("")
    return lines


def render_ingest_delta_report(delta: ResearchIngestDelta) -> str:
    """渲染 Markdown 增量摄取清单报告。

    报告结构：
      1. 概览（总数 / 分类统计 / 上游摘要）。
      2. 按状态分组列出条目。
      3. 重复预检说明（不自动删除）。
      4. 免责声明（不含原文、不含强动作词）。
    """
    counts = delta.status_counts()
    total = delta.total()
    lines: List[str] = []
    lines.append(
        f"# Tree Work 研报增量摄取清单 — {delta.as_of_date}"
    )
    lines.append("")
    lines.append(
        f"> [{TASK_CODE}] research_ingest_delta — 只读扫描 inbox/raw/wiki，"
        "标注 ``new / digested / duplicate / needs_metadata / stale / conflict`` "
        "六类状态；**不修改知识库、不复制研报原文、不输出交易动作、不自动删除重复**。"
    )
    lines.append("")

    # 1. 概览
    lines.append("## 1. 概览")
    lines.append("")
    lines.append(f"- knowledge_root: `{delta.knowledge_root}`")
    lines.append(f"- generated_at: {delta.generated_at}")
    lines.append(f"- as_of_date: {delta.as_of_date}")
    lines.append(f"- contract_version: `{delta.contract_version}`")
    lines.append(f"- 清单总数: **{total}**")
    if delta.errors:
        lines.append(f"- ⚠️ 错误 ({len(delta.errors)}):")
        for err in delta.errors[:10]:
            lines.append(f"  - {err}")
    lines.append("")

    # 2. 上游摘要
    lines.append("## 2. 上游扫描摘要")
    lines.append("")
    up = delta.upstream_summary
    lines.append("| 来源 | 数量 |")
    lines.append("|------|------|")
    lines.append(f"| inbox 文件 | {up.get('inbox_files_scanned', 0)} |")
    lines.append(f"| raw 文件 | {up.get('raw_files_scanned', 0)} |")
    lines.append(f"| wiki investment 页 | {up.get('wiki_pages_scanned', 0)} |")
    lines.append(f"| 被 wiki 引用的 raw | {up.get('referenced_raw_names', 0)} |")
    lines.append(f"| 过期 wiki 页 | {up.get('stale_wiki_paths', 0)} |")
    if "kb005" in up:
        kb = up["kb005"]
        lines.append(
            f"| KB-005 raw_total | {kb.get('raw_total', 0)}（referenced {kb.get('raw_referenced', 0)} / undigested {kb.get('raw_undigested', 0)}） |"
        )
    lines.append("")

    # 3. 分类统计
    lines.append("## 3. 分类统计")
    lines.append("")
    lines.append("| 状态 | 数量 | 说明 |")
    lines.append("|------|------|------|")
    for status in ALL_STATUSES:
        lines.append(
            f"| `{status}` | {counts.get(status, 0)} | {STATUS_TITLES.get(status, '')} |"
        )
    lines.append("")

    # 4. 按状态分组
    lines.append("## 4. 按状态分组")
    lines.append("")
    for status in STATUS_ORDER:
        items = delta.items_by_status(status)
        title = STATUS_TITLES.get(status, status)
        lines.append(f"### {title}（{len(items)}）")
        lines.append("")
        lines.extend(_render_status_table(items))

    # 5. 重复预检说明
    lines.append("## 5. 重复预检说明")
    lines.append("")
    lines.append("- 同 fingerprint（同内容多文件）→ ``duplicate``；保留首次出现的为原始。")
    lines.append(
        "- 同 institution + 同 report_date + 同 symbol 且标题 token Jaccard 相似度 ≥ "
        f"{_TITLE_SIMILARITY_THRESHOLD} → ``duplicate``。"
    )
    lines.append("- 同 path + 不同 fingerprint → ``conflict``（疑似文件被覆盖）。")
    lines.append(
        "- **本工具只标注重复 / 冲突，绝不自动删除文件**；由 Tree Work 人工确认后处理。"
    )
    lines.append("")

    # 6. 建议执行顺序
    lines.append("## 6. 建议执行顺序")
    lines.append("")
    lines.append("1. **先处理 `conflict`**：文件被覆盖可能丢失关键信息，需对比版本。")
    lines.append("2. **再处理 `needs_metadata`**：补 symbol/date/institution 才能稳定索引。")
    lines.append("3. **核对 `duplicate`**：确认是否真重复，合并或归档多余副本。")
    lines.append("4. **消化 `new`**：把新增 raw 提炼为 wiki/investment 页并补 sources 反向链接。")
    lines.append("5. **更新 `stale`**：复核过期 wiki 页，补最新半年报/季度事实。")
    lines.append("6. **`digested`**：仅作回查记录，无需动作。")
    lines.append("")

    # 7. 免责声明
    lines.append("## 7. 免责声明")
    lines.append("")
    lines.append(
        "- 本清单只含元数据、摘要和路径，**不含研报原文段落**；"
        "**不输出买卖建议或强动作词**。"
    )
    lines.append(
        "- 重复 / 冲突标注仅为预检结果，最终归属由 Tree Work 人工确认。"
    )
    lines.append(
        "- 重复执行幂等：知识库文件不会被修改、移动或删除。"
    )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        f"_由 `scripts/research_ingest_delta.py` 只读生成；"
        f"对应模块 `tradingagents.dataflows.research_ingest_delta`。_"
    )
    lines.append("")
    return "\n".join(lines)


# ── CLI 便利 ─────────────────────────────────────────────────────────


def suggest_ingest_delta_output_path(
    docs_dir: str = "docs/knowledge_reports",
) -> str:
    """生成默认输出路径 ``docs/knowledge_reports/research-ingest-delta-YYYY-MM-DD.md``。"""
    today = date.today().strftime("%Y-%m-%d")
    return os.path.join(docs_dir, f"research-ingest-delta-{today}.md")


__all__ = [
    "TASK_CODE",
    "CONTRACT_VERSION",
    "STATUS_NEW",
    "STATUS_DIGESTED",
    "STATUS_DUPLICATE",
    "STATUS_NEEDS_METADATA",
    "STATUS_STALE",
    "STATUS_CONFLICT",
    "STATUS_ORDER",
    "ALL_STATUSES",
    "STATUS_TITLES",
    "AREA_INBOX",
    "AREA_RAW",
    "AREA_WIKI",
    "IngestItem",
    "ResearchIngestDelta",
    "compute_fingerprint",
    "compute_ingest_key",
    "compute_business_key",
    "normalize_rel_path",
    "normalize_symbol",
    "normalize_date",
    "normalize_str",
    "extract_research_metadata",
    "title_similarity",
    "build_research_ingest_delta",
    "render_ingest_delta_report",
    "has_forbidden_action_words",
    "suggest_ingest_delta_output_path",
    "default_knowledge_root",
]
