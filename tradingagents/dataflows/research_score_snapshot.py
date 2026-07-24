# [SCORE-001] research_score_snapshot_contract
"""只读 ``research_score_snapshot`` v1.1.0 loader / provider（SCORE-001）。

在 KB-001 / KB-014 之上，本模块为 TA 侧提供对 ZCode 发布的
``research_score_snapshot`` 的**只读**读取与校验能力。本模块**不**重新计算
知识库分数，**不**接受知识库给出的交易动作，**不**新增 API / 数据库列 /
TradeFlow 字段；这些接线由后续 SCORE-001B / SCORE-002~005 承担。

设计约束（对应任务 SCORE-001 执行约束）：
  - **只读知识库**：仅用 ``open(..., "r", encoding="utf-8")`` 与 ``Path.iterdir``，
    绝不向知识库写文件；不写生产 DB；不调用 live LLM。
  - **生产读取范围只允许正式快照**：``research_score_snapshots/<symbol>/`` 下的
    正式 JSON 快照；``research_score_snapshots/drafts/`` 一律排除，违反时 fail closed。
  - **快照只允许研究字段**：输入含 ``action/execution_action/playbook_stage/
    planned_position/buy_level/risk_level/entry_timing/portfolio_fit`` 时 fail closed
    并记录 schema warning，禁止静默消费。
  - **不用 0 代替"缺证据/不可评分"**：分数缺失为 ``None``；必须区分
    ``HAS_DATA / STALE / LOW_CONFIDENCE / NORMAL_NO_DATA / FAILED`` 五类状态。
  - **不建立第二套来源等级**：复用 KB-014 ``SOURCE_QUALITY_TIERS`` 与 citation 权重。
  - **不复制 ZCode 评分公式**：TA 只校验消费者所需的 v1.1.0 契约和跨字段安全条件。
  - **路径安全**：解析后路径必须在知识根目录内；符号链接逃逸 / 路径逃逸 fail closed。

降级决策表（fail closed）：
  - 正式目录无快照 → ``NORMAL_NO_DATA``
  - 快照结构损坏 / 非法 JSON / 未知 schema_version / 标的不匹配 / 禁止字段 /
    非法分数 / 悬空 evidence ref / 未来快照 / 路径逃逸 → ``FAILED``
  - 快照 ``as_of`` 相对 ``analysis_time`` 过期（超过 ``stale_after_days``）→ ``STALE``
  - 快照自身声明 ``STALE`` / ``LOW_CONFIDENCE`` / ``NORMAL_NO_DATA`` → 原样透传

历史回放契约：按 ``as_of <= analysis_time`` 选择最新合法正式快照，禁止使用未来快照。

使用示例::

    from tradingagents.dataflows.research_score_snapshot import (
        query_research_score_snapshot,
    )
    result = query_research_score_snapshot(
        "/Users/maybee/Documents/knowledge",
        symbol="605589.SH",
        analysis_time=datetime(2026, 7, 14, 15, 0, tzinfo=timezone(timedelta(hours=8))),
    )
    if result.status == "HAS_DATA" and result.snapshot:
        print(result.snapshot.scores)
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 复用 KB-014 来源可信度分层，不建立第二套 A-E 等级。
from tradingagents.dataflows.citation_policy import SOURCE_QUALITY_TIERS
# 复用 KB-001 默认知识根目录解析。
from tradingagents.dataflows.local_knowledge_lint import default_knowledge_root


# ── 常量 ──────────────────────────────────────────────────────────────

VENDOR = "zcode_research_scorer"
TASK_CODE = "SCORE-001"

# [SCORE-001] research_score_snapshot_contract
SCHEMA_VERSION = "1.1.0"
#: 本 loader 支持消费的 schema_version 白名单（未知版本 fail closed）。
SUPPORTED_SCHEMA_VERSIONS: Tuple[str, ...] = ("1.1.0",)

#: 知识库内快照根目录名（ZCode 发布产物所在）。
SNAPSHOTS_DIR_NAME = "research_score_snapshots"
#: 草案子目录名——生产 loader **一律排除**。
DRAFTS_DIR_NAME = "drafts"

# 五类合法状态（与契约一致；不用 0 冒充缺数据）。
STATUS_HAS_DATA = "HAS_DATA"
STATUS_STALE = "STALE"
STATUS_LOW_CONFIDENCE = "LOW_CONFIDENCE"
STATUS_NORMAL_NO_DATA = "NORMAL_NO_DATA"
STATUS_FAILED = "FAILED"
VALID_SNAPSHOT_STATUSES: Tuple[str, ...] = (
    STATUS_HAS_DATA,
    STATUS_STALE,
    STATUS_LOW_CONFIDENCE,
    STATUS_NORMAL_NO_DATA,
    STATUS_FAILED,
)

# 投资假设合法状态。
THESIS_STATUS_ACTIVE = "active"
THESIS_STATUS_WEAKENING = "weakening"
THESIS_STATUS_INVALIDATED = "invalidated"
THESIS_STATUS_INSUFFICIENT = "insufficient_evidence"
VALID_THESIS_STATUSES: Tuple[str, ...] = (
    THESIS_STATUS_ACTIVE,
    THESIS_STATUS_WEAKENING,
    THESIS_STATUS_INVALIDATED,
    THESIS_STATUS_INSUFFICIENT,
)

#: 快照禁止携带的动作/交易字段——命中即 fail closed（SCORE-001 安全契约）。
FORBIDDEN_ACTION_FIELDS: Tuple[str, ...] = (
    "action",
    "execution_action",
    "playbook_stage",
    "planned_position",
    "buy_level",
    "risk_level",
    "entry_timing",
    "portfolio_fit",
)

# 分数合法区间（闭区间；缺失为 None，不是 0）。
_SCORE_MIN = 0
_SCORE_MAX = 100

# loader 侧过期阈值（慢变量安全网）：as_of 距 analysis_time 超过该天数 → STALE。
# 研究证据/逻辑质量是慢变量，默认覆盖一个财报季；可由调用方覆盖。
DEFAULT_STALE_AFTER_DAYS = 120

# 数值/日期裁剪上限，避免输出长篇正文。
_REASON_MAX_CHARS = 160
_HYPOTHESIS_MAX_CHARS = 200
_CLAIM_MAX_CHARS = 160
_MAX_THESES = 8
_MAX_EVIDENCE_REFS = 30
_MAX_REASONS = 10
_MAX_WARNINGS = 10

# 日期/时间正则。
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# ISO 8601 带可选时区：``2026-07-13T20:00:00+08:00`` / ``...Z`` / 无时区。
_ISO_DT_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?"
    r"(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?$"
)


# ── 数据类 ────────────────────────────────────────────────────────────


@dataclass
class ScoreSummary:
    """两项核心分数（缺失为 ``None``，绝不用 0 冒充）。"""

    research_evidence_confidence: Optional[int] = None
    thesis_quality: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "research_evidence_confidence": self.research_evidence_confidence,
            "thesis_quality": self.thesis_quality,
        }


@dataclass
class InvestmentThesis:
    """单条投资假设（只读摘要，不含正文）。"""

    thesis_id: str = ""
    symbol: str = ""
    as_of: str = ""
    topic: str = ""
    direction: str = ""
    status: str = THESIS_STATUS_ACTIVE
    core_hypothesis: str = ""
    supporting_evidence_ids: List[str] = field(default_factory=list)
    counter_evidence_ids: List[str] = field(default_factory=list)
    missing_evidence: List[str] = field(default_factory=list)
    upgrade_conditions: List[str] = field(default_factory=list)
    downgrade_conditions: List[str] = field(default_factory=list)
    invalidation_conditions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "thesis_id": self.thesis_id,
            "symbol": self.symbol,
            "as_of": self.as_of,
            "topic": self.topic,
            "direction": self.direction,
            "status": self.status,
            "core_hypothesis": self.core_hypothesis,
            "supporting_evidence_ids": list(self.supporting_evidence_ids),
            "counter_evidence_ids": list(self.counter_evidence_ids),
            "missing_evidence": list(self.missing_evidence),
            "upgrade_conditions": list(self.upgrade_conditions),
            "downgrade_conditions": list(self.downgrade_conditions),
            "invalidation_conditions": list(self.invalidation_conditions),
        }


@dataclass
class EvidenceRef:
    """单条证据引用（来源路径只允许知识根目录相对路径）。"""

    evidence_id: str = ""
    claim: str = ""
    claim_type: str = ""
    source_path: str = ""
    source_type: str = ""
    source_quality_tier: str = "unknown"
    report_date: Optional[str] = None
    financial_period: Optional[str] = None
    locator: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "claim": self.claim,
            "claim_type": self.claim_type,
            "source_path": self.source_path,
            "source_type": self.source_type,
            "source_quality_tier": self.source_quality_tier,
            "report_date": self.report_date,
            "financial_period": self.financial_period,
            "locator": self.locator,
        }


@dataclass
class ScoreChange:
    """分数变化（previous/current/delta/reasons）。"""

    previous_snapshot_id: Optional[str] = None
    previous: Dict[str, Any] = field(default_factory=dict)
    current: Dict[str, Any] = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "previous_snapshot_id": self.previous_snapshot_id,
            "previous": dict(self.previous),
            "current": dict(self.current),
            "reasons": list(self.reasons),
        }


@dataclass
class ResearchScoreSnapshot:
    """一份完整的研究评分快照（只读，已通过契约校验）。"""

    schema_version: str = SCHEMA_VERSION
    snapshot_id: str = ""
    symbol: str = ""
    name: str = ""
    as_of: str = ""
    created_at: str = ""
    source_cutoff_at: str = ""
    rubric_id: str = ""
    rubric_version: str = ""
    status: str = STATUS_HAS_DATA
    scores: ScoreSummary = field(default_factory=ScoreSummary)
    thesis_breakdown: Dict[str, Any] = field(default_factory=dict)
    theses: List[InvestmentThesis] = field(default_factory=list)
    score_change: ScoreChange = field(default_factory=ScoreChange)
    evidence_refs: List[EvidenceRef] = field(default_factory=list)
    missing_evidence: List[str] = field(default_factory=list)
    upgrade_conditions: List[str] = field(default_factory=list)
    downgrade_conditions: List[str] = field(default_factory=list)
    invalidation_conditions: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    rel_path: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "snapshot_id": self.snapshot_id,
            "symbol": self.symbol,
            "name": self.name,
            "as_of": self.as_of,
            "created_at": self.created_at,
            "source_cutoff_at": self.source_cutoff_at,
            "rubric_id": self.rubric_id,
            "rubric_version": self.rubric_version,
            "status": self.status,
            "scores": self.scores.to_dict(),
            "thesis_breakdown": dict(self.thesis_breakdown),
            "theses": [t.to_dict() for t in self.theses],
            "score_change": self.score_change.to_dict(),
            "evidence_refs": [e.to_dict() for e in self.evidence_refs],
            "missing_evidence": list(self.missing_evidence),
            "upgrade_conditions": list(self.upgrade_conditions),
            "downgrade_conditions": list(self.downgrade_conditions),
            "invalidation_conditions": list(self.invalidation_conditions),
            "warnings": list(self.warnings),
            "rel_path": self.rel_path,
        }


@dataclass
class ResearchScoreQueryResult:
    """整次研究快照只读查询的聚合结果。

    ``status`` 反映**消费者侧**最终可用状态（可能因 fail-closed 降级）；
    ``snapshot`` 在 ``status=HAS_DATA/STALE/LOW_CONFIDENCE`` 时携带快照摘要，
    ``NORMAL_NO_DATA`` / ``FAILED`` 时为 ``None``。
    """

    status: str = STATUS_NORMAL_NO_DATA
    vendor: str = VENDOR
    task: str = TASK_CODE
    symbol: str = ""
    analysis_time: str = ""
    snapshot: Optional[ResearchScoreSnapshot] = None
    snapshot_id: Optional[str] = None
    schema_version: Optional[str] = None
    rubric_id: Optional[str] = None
    rubric_version: Optional[str] = None
    degradation_reasons: List[str] = field(default_factory=list)
    validation_warnings: List[str] = field(default_factory=list)
    knowledge_root: str = ""
    query: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        snap = self.snapshot.to_dict() if self.snapshot else None
        return {
            "status": self.status,
            "vendor": self.vendor,
            "task": self.task,
            "symbol": self.symbol,
            "analysis_time": self.analysis_time,
            "snapshot": snap,
            "snapshot_id": self.snapshot_id,
            "schema_version": self.schema_version,
            "rubric_id": self.rubric_id,
            "rubric_version": self.rubric_version,
            "degradation_reasons": list(self.degradation_reasons),
            "validation_warnings": list(self.validation_warnings),
            "knowledge_root": self.knowledge_root,
            "query": dict(self.query),
            "errors": list(self.errors),
        }


# ── 校验异常 ──────────────────────────────────────────────────────────


class SnapshotValidationError(Exception):
    """快照契约校验失败。

    携带 ``reason_code`` 供 loader 记录降级原因；不向外抛——loader 捕获后 fail closed。
    """

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.message = message


# ── 归一化助手 ────────────────────────────────────────────────────────


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _clip(text: str, max_chars: int) -> str:
    text = (text or "").strip()
    if len(text) > max_chars:
        return text[: max_chars].rstrip() + "…"
    return text


def _clip_list(items: List[Any], max_chars: int, max_entries: int) -> List[str]:
    out: List[str] = []
    for item in items:
        text = _safe_str(item)
        if not text:
            continue
        out.append(_clip(text, max_chars))
        if len(out) >= max_entries:
            break
    return out


def _normalize_symbol(symbol: str) -> str:
    """归一 symbol：去空白、转大写后缀。``605589.sh`` → ``605589.SH``。"""
    return (symbol or "").strip().upper()


def _parse_iso_datetime(value: str) -> Optional[datetime]:
    """解析 ISO 8601 字符串为 aware datetime；失败返回 None。"""
    if not value:
        return None
    text = value.strip().replace(" ", "T")
    # ``...Z`` → ``...+00:00`` 让 fromisoformat 可解析。
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_date_str(value: str) -> Optional[date]:
    """解析 ``YYYY-MM-DD`` 为 date；失败返回 None。"""
    if not value or not _DATE_RE.match(value.strip()):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


# ── 路径安全 ──────────────────────────────────────────────────────────


def _is_within_directory(root: Path, target: Path) -> bool:
    """判断 ``target`` 解析后是否在 ``root`` 解析后目录内（含根本身）。

    使用 ``resolve()`` 解析符号链接；逃逸即返回 False。
    """
    try:
        root_resolved = root.resolve(strict=False)
        target_resolved = target.resolve(strict=False)
    except (OSError, RuntimeError):
        return False
    try:
        target_resolved.relative_to(root_resolved)
        return True
    except ValueError:
        return False


def _contains_drafts_component(path: Path) -> bool:
    """路径任一组件为 ``drafts`` → True（生产 loader 排除草案）。"""
    return any(part == DRAFTS_DIR_NAME for part in path.parts)


# ── 契约校验 ──────────────────────────────────────────────────────────


def _check_forbidden_fields(data: Dict[str, Any]) -> None:
    """检查快照是否携带禁止的动作/交易字段。

    检查范围：顶层键 + ``scores`` / ``thesis_breakdown`` / 单条 thesis 的键。
    不递归到自由文本（claim/core_hypothesis），避免误报。
    """
    # 顶层。
    top_keys = set(data.keys())
    hit = top_keys.intersection(FORBIDDEN_ACTION_FIELDS)
    if hit:
        raise SnapshotValidationError(
            "FORBIDDEN_ACTION_FIELD",
            f"快照顶层含禁止动作字段: {sorted(hit)}",
        )
    # scores 子表（动作字段最可能的注入点）。
    scores = data.get("scores")
    if isinstance(scores, dict):
        hit = set(scores.keys()).intersection(FORBIDDEN_ACTION_FIELDS)
        if hit:
            raise SnapshotValidationError(
                "FORBIDDEN_ACTION_FIELD",
                f"scores 子表含禁止动作字段: {sorted(hit)}",
            )
    # thesis_breakdown 子表。
    tb = data.get("thesis_breakdown")
    if isinstance(tb, dict):
        hit = set(tb.keys()).intersection(FORBIDDEN_ACTION_FIELDS)
        if hit:
            raise SnapshotValidationError(
                "FORBIDDEN_ACTION_FIELD",
                f"thesis_breakdown 子表含禁止动作字段: {sorted(hit)}",
            )
    # 单条 thesis。
    theses = data.get("theses")
    if isinstance(theses, list):
        for idx, th in enumerate(theses):
            if isinstance(th, dict):
                hit = set(th.keys()).intersection(FORBIDDEN_ACTION_FIELDS)
                if hit:
                    raise SnapshotValidationError(
                        "FORBIDDEN_ACTION_FIELD",
                        f"thesis[{idx}] 含禁止动作字段: {sorted(hit)}",
                    )


def _validate_score_value(name: str, value: Any) -> Optional[int]:
    """校验单项分数：缺失返回 None；非整数/越界 raise。"""
    if value is None:
        return None
    # 显式拒绝字符串 "0" 冒充——必须是数值。
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SnapshotValidationError(
            "ILLEGAL_SCORE",
            f"scores.{name} 非数值: {value!r}",
        )
    if isinstance(value, float):
        if not value.is_integer():
            raise SnapshotValidationError(
                "ILLEGAL_SCORE",
                f"scores.{name} 非整数: {value!r}",
            )
        value = int(value)
    else:
        value = int(value)
    if value < _SCORE_MIN or value > _SCORE_MAX:
        raise SnapshotValidationError(
            "ILLEGAL_SCORE",
            f"scores.{name} 越界 ({value} 不在 [{_SCORE_MIN},{_SCORE_MAX}])",
        )
    return value


def _validate_thesis_evidence_closure(
    theses: List[InvestmentThesis],
    evidence_ids: set,
) -> None:
    """校验 thesis 引用的 evidence_id 必须在 evidence_refs 中存在（证据闭包）。

    悬空 ref 视为结构完整性失败 → raise（fail closed）。
    """
    for th in theses:
        for ref_id in th.supporting_evidence_ids:
            if ref_id and ref_id not in evidence_ids:
                raise SnapshotValidationError(
                    "DANGLING_EVIDENCE_REF",
                    f"thesis {th.thesis_id}: supporting_evidence_id 悬空: {ref_id}",
                )
        for ref_id in th.counter_evidence_ids:
            if ref_id and ref_id not in evidence_ids:
                raise SnapshotValidationError(
                    "DANGLING_EVIDENCE_REF",
                    f"thesis {th.thesis_id}: counter_evidence_id 悬空: {ref_id}",
                )


def _validate_snapshot_dict(
    data: Any,
    *,
    expected_symbol: str,
    analysis_time: datetime,
    knowledge_root: Path,
    snapshot_path: Path,
) -> ResearchScoreSnapshot:
    """对原始 dict 做完整契约校验，返回 :class:`ResearchScoreSnapshot`。

    任一校验失败 raise :class:`SnapshotValidationError`（reason_code + message）。
    """
    if not isinstance(data, dict):
        raise SnapshotValidationError("CORRUPTED", "快照根不是 JSON 对象")

    # 禁止动作字段（最早检查，避免消费被污染输入）。
    _check_forbidden_fields(data)

    # schema_version。
    schema_version = _safe_str(data.get("schema_version"))
    if not schema_version:
        raise SnapshotValidationError("UNKNOWN_SCHEMA_VERSION", "缺 schema_version")
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise SnapshotValidationError(
            "UNKNOWN_SCHEMA_VERSION",
            f"未知 schema_version={schema_version}（支持: {SUPPORTED_SCHEMA_VERSIONS}）",
        )

    # 必填顶层字段。
    snapshot_id = _safe_str(data.get("snapshot_id"))
    if not snapshot_id:
        raise SnapshotValidationError("CORRUPTED", "缺 snapshot_id")
    symbol = _safe_str(data.get("symbol"))
    if not symbol:
        raise SnapshotValidationError("CORRUPTED", "缺 symbol")
    name = _safe_str(data.get("name"))
    as_of = _safe_str(data.get("as_of"))
    as_of_date = _parse_date_str(as_of)
    if not as_of_date:
        raise SnapshotValidationError("CORRUPTED", f"as_of 非法日期: {as_of!r}")

    created_at = _safe_str(data.get("created_at"))
    if not created_at or not _ISO_DT_RE.match(created_at):
        raise SnapshotValidationError(
            "CORRUPTED", f"created_at 非法 ISO 时间: {created_at!r}"
        )
    source_cutoff_at = _safe_str(data.get("source_cutoff_at"))
    if not source_cutoff_at or not _ISO_DT_RE.match(source_cutoff_at):
        raise SnapshotValidationError(
            "CORRUPTED", f"source_cutoff_at 非法 ISO 时间: {source_cutoff_at!r}"
        )

    rubric_id = _safe_str(data.get("rubric_id"))
    rubric_version = _safe_str(data.get("rubric_version"))
    if not rubric_id or not rubric_version:
        raise SnapshotValidationError(
            "CORRUPTED", "缺 rubric_id/rubric_version（rubric 版本不可空）"
        )

    # 标的匹配（目录、查询与快照内部三者一致）。
    if _normalize_symbol(symbol) != _normalize_symbol(expected_symbol):
        raise SnapshotValidationError(
            "SYMBOL_MISMATCH",
            f"快照 symbol={symbol!r} 与查询 {expected_symbol!r} 不一致",
        )

    # 未来快照：as_of 严格 > analysis_time.date → fail closed（禁止穿越）。
    if as_of_date > analysis_time.date():
        raise SnapshotValidationError(
            "FUTURE_SNAPSHOT",
            f"as_of={as_of} 晚于 analysis_time={analysis_time.date()}",
        )

    # 状态字段。
    status = _safe_str(data.get("status")) or STATUS_HAS_DATA
    if status not in VALID_SNAPSHOT_STATUSES:
        raise SnapshotValidationError(
            "CORRUPTED", f"status 非法: {status!r}"
        )

    # 分数。
    raw_scores = data.get("scores")
    if raw_scores is None or not isinstance(raw_scores, dict):
        raw_scores = {}
    rec = _validate_score_value(
        "research_evidence_confidence",
        raw_scores.get("research_evidence_confidence"),
    )
    tqual = _validate_score_value(
        "thesis_quality", raw_scores.get("thesis_quality")
    )
    scores = ScoreSummary(
        research_evidence_confidence=rec, thesis_quality=tqual
    )

    # thesis_breakdown（自由 dict，只拒绝禁止字段，已在上面检查）。
    tb_raw = data.get("thesis_breakdown")
    thesis_breakdown = dict(tb_raw) if isinstance(tb_raw, dict) else {}

    # theses。
    theses_raw = data.get("theses")
    if not isinstance(theses_raw, list):
        theses_raw = []
    if len(theses_raw) > _MAX_THESES:
        theses_raw = theses_raw[:_MAX_THESES]
    theses: List[InvestmentThesis] = []
    for item in theses_raw:
        if not isinstance(item, dict):
            continue
        th_status = _safe_str(item.get("status")) or THESIS_STATUS_ACTIVE
        if th_status not in VALID_THESIS_STATUSES:
            raise SnapshotValidationError(
                "CORRUPTED", f"thesis status 非法: {th_status!r}"
            )
        theses.append(
            InvestmentThesis(
                thesis_id=_safe_str(item.get("thesis_id")),
                symbol=_safe_str(item.get("symbol")) or symbol,
                as_of=_safe_str(item.get("as_of")) or as_of,
                topic=_safe_str(item.get("topic")),
                direction=_safe_str(item.get("direction")),
                status=th_status,
                core_hypothesis=_clip(
                    _safe_str(item.get("core_hypothesis")), _HYPOTHESIS_MAX_CHARS
                ),
                supporting_evidence_ids=[
                    _safe_str(x)
                    for x in (item.get("supporting_evidence_ids") or [])
                    if _safe_str(x)
                ],
                counter_evidence_ids=[
                    _safe_str(x)
                    for x in (item.get("counter_evidence_ids") or [])
                    if _safe_str(x)
                ],
                missing_evidence=_clip_list(
                    item.get("missing_evidence") or [], _REASON_MAX_CHARS, _MAX_REASONS
                ),
                upgrade_conditions=_clip_list(
                    item.get("upgrade_conditions") or [], _REASON_MAX_CHARS, _MAX_REASONS
                ),
                downgrade_conditions=_clip_list(
                    item.get("downgrade_conditions") or [], _REASON_MAX_CHARS, _MAX_REASONS
                ),
                invalidation_conditions=_clip_list(
                    item.get("invalidation_conditions") or [],
                    _REASON_MAX_CHARS,
                    _MAX_REASONS,
                ),
            )
        )

    # evidence_refs + 来源等级（复用 KB-014，不建第二套）。
    refs_raw = data.get("evidence_refs")
    if not isinstance(refs_raw, list):
        refs_raw = []
    if len(refs_raw) > _MAX_EVIDENCE_REFS:
        refs_raw = refs_raw[:_MAX_EVIDENCE_REFS]
    evidence_refs: List[EvidenceRef] = []
    for item in refs_raw:
        if not isinstance(item, dict):
            continue
        tier = _safe_str(item.get("source_quality_tier")) or "unknown"
        if tier not in SOURCE_QUALITY_TIERS:
            raise SnapshotValidationError(
                "CORRUPTED",
                f"evidence_ref source_quality_tier 非法: {tier!r}"
                f"（合法: {SOURCE_QUALITY_TIERS}）",
            )
        source_path = _safe_str(item.get("source_path"))
        # 来源路径只允许相对路径；绝对路径/路径逃逸拒绝。
        if source_path and (os.path.isabs(source_path) or ".." in Path(source_path).parts):
            raise SnapshotValidationError(
                "PATH_ESCAPE",
                f"evidence_ref source_path 非法绝对/逃逸路径: {source_path!r}",
            )
        evidence_refs.append(
            EvidenceRef(
                evidence_id=_safe_str(item.get("evidence_id")),
                claim=_clip(_safe_str(item.get("claim")), _CLAIM_MAX_CHARS),
                claim_type=_safe_str(item.get("claim_type")),
                source_path=source_path,
                source_type=_safe_str(item.get("source_type")),
                source_quality_tier=tier,
                report_date=_safe_str(item.get("report_date")) or None,
                financial_period=_safe_str(item.get("financial_period")) or None,
                locator=_safe_str(item.get("locator")) or None,
            )
        )

    # 证据闭包：thesis 引用的 evidence_id 必须存在于 evidence_refs。
    evidence_ids = {e.evidence_id for e in evidence_refs if e.evidence_id}
    _validate_thesis_evidence_closure(theses, evidence_ids)

    # score_change。
    sc_raw = data.get("score_change")
    if not isinstance(sc_raw, dict):
        sc_raw = {}
    score_change = ScoreChange(
        previous_snapshot_id=_safe_str(sc_raw.get("previous_snapshot_id")) or None,
        previous=dict(sc_raw.get("previous")) if isinstance(sc_raw.get("previous"), dict) else {},
        current=dict(sc_raw.get("current")) if isinstance(sc_raw.get("current"), dict) else {},
        reasons=_clip_list(sc_raw.get("reasons") or [], _REASON_MAX_CHARS, _MAX_REASONS),
    )

    warnings_raw = data.get("warnings")
    if not isinstance(warnings_raw, list):
        warnings_raw = []
    warnings = _clip_list(warnings_raw, _REASON_MAX_CHARS, _MAX_WARNINGS)

    rel = ""
    try:
        rel = str(snapshot_path.relative_to(knowledge_root))
    except ValueError:
        rel = str(snapshot_path)

    return ResearchScoreSnapshot(
        schema_version=schema_version,
        snapshot_id=snapshot_id,
        symbol=symbol,
        name=name,
        as_of=as_of,
        created_at=created_at,
        source_cutoff_at=source_cutoff_at,
        rubric_id=rubric_id,
        rubric_version=rubric_version,
        status=status,
        scores=scores,
        thesis_breakdown=thesis_breakdown,
        theses=theses,
        score_change=score_change,
        evidence_refs=evidence_refs,
        missing_evidence=_clip_list(
            data.get("missing_evidence") or [], _REASON_MAX_CHARS, _MAX_REASONS
        ),
        upgrade_conditions=_clip_list(
            data.get("upgrade_conditions") or [], _REASON_MAX_CHARS, _MAX_REASONS
        ),
        downgrade_conditions=_clip_list(
            data.get("downgrade_conditions") or [], _REASON_MAX_CHARS, _MAX_REASONS
        ),
        invalidation_conditions=_clip_list(
            data.get("invalidation_conditions") or [], _REASON_MAX_CHARS, _MAX_REASONS
        ),
        warnings=warnings,
        rel_path=rel,
    )


# ── 候选发现 ──────────────────────────────────────────────────────────


def _candidate_snapshot_files(
    snapshots_root: Path,
    symbol: str,
) -> List[Path]:
    """枚举某 symbol 的正式快照候选（排除 drafts，排除路径逃逸）。

    返回所有位于 ``snapshots_root/<symbol>/`` 内、扩展名 ``.json``、不含 drafts
    组件、解析后仍在 snapshots_root 内的文件路径。
    """
    candidates: List[Path] = []
    sym = _normalize_symbol(symbol)
    if not sym:
        return candidates
    sym_dir = snapshots_root / sym
    if not sym_dir.exists() or not sym_dir.is_dir():
        return candidates
    for path in sym_dir.rglob("*.json"):
        if not path.is_file():
            continue
        if _contains_drafts_component(path):
            continue
        if not _is_within_directory(snapshots_root, path):
            continue
        candidates.append(path)
    return candidates


def _read_json_safe(path: Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """只读解析 JSON；失败返回 (None, error)。"""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        return None, f"JSON 解析失败: {exc}"
    except OSError as exc:
        return None, f"读取失败: {exc}"
    if not isinstance(data, dict):
        return None, "根节点非 JSON 对象"
    return data, None


def _extract_as_of(data: Dict[str, Any]) -> Optional[date]:
    """从原始 dict 抽取 as_of date（不触发完整校验）。"""
    return _parse_date_str(_safe_str(data.get("as_of")))


# ── 主查询逻辑 ────────────────────────────────────────────────────────


def query_research_score_snapshot(
    knowledge_root: str,
    *,
    symbol: str,
    analysis_time: Optional[datetime] = None,
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
) -> ResearchScoreQueryResult:
    """按 symbol + analysis_time 只读查询最新合法正式研究快照。

    参数:
        knowledge_root: 知识库根目录绝对路径。
        symbol: 标的代码（与 ZCode 快照 dir 名一致，如 ``605589.SH``）。
        analysis_time: 分析基准时间（aware datetime）。``None`` 用当前时间。
            历史回放必须满足 ``as_of <= analysis_time.date``，未来快照不穿越。
        stale_after_days: loader 侧过期安全网阈值（天）。``as_of`` 距
            ``analysis_time`` 超过该天数 → STALE。默认 :data:`DEFAULT_STALE_AFTER_DAYS`。

    返回:
        :class:`ResearchScoreQueryResult`。永不因单个快照损坏抛异常：损坏记入
        ``errors`` 并 fail closed；正式目录无快照返回 ``NORMAL_NO_DATA``。

    安全保证:
        - 只读，不写知识库 / DB，不调用 LLM。
        - 排除 ``research_score_snapshots/drafts/`` 草案。
        - 路径/符号链接逃逸 fail closed。
        - 禁止动作字段 fail closed。
        - 重复读取幂等。
    """
    sym = _normalize_symbol(symbol)
    if analysis_time is None:
        analysis_time = datetime.now(timezone.utc)
    elif analysis_time.tzinfo is None:
        analysis_time = analysis_time.replace(tzinfo=timezone.utc)

    root = Path(knowledge_root).expanduser()
    result = ResearchScoreQueryResult(
        knowledge_root=str(root),
        symbol=sym,
        analysis_time=analysis_time.isoformat(),
        query={"symbol": sym, "analysis_time": analysis_time.isoformat()},
    )

    if not sym:
        result.status = STATUS_NORMAL_NO_DATA
        result.errors.append("未提供查询 symbol")
        return result

    if not root.exists():
        result.status = STATUS_FAILED
        result.errors.append(f"knowledge_root 不存在: {root}")
        return result

    snapshots_root = root / SNAPSHOTS_DIR_NAME
    if not snapshots_root.exists():
        result.status = STATUS_NORMAL_NO_DATA
        result.errors.append(f"快照根目录不存在: {snapshots_root}")
        return result

    # 草案目录存在性记录（不读取，只标注可见性，便于审计）。
    drafts_dir = snapshots_root / DRAFTS_DIR_NAME
    if drafts_dir.exists():
        result.validation_warnings.append(
            "drafts 目录存在，生产 loader 已排除（SCORE-001 安全契约）"
        )

    candidates = _candidate_snapshot_files(snapshots_root, sym)
    if not candidates:
        result.status = STATUS_NORMAL_NO_DATA
        result.errors.append(f"无正式快照: {snapshots_root / sym}")
        return result

    # 第一遍：读取每个候选的 as_of，过滤未来快照，按 as_of 倒序。
    # 未来快照不进入"最新合法"候选（fail closed），但记录到 errors 供审计。
    # [SCORE-001B-R1] 区分"无候选文件"与"有候选文件但全部读取失败"。
    readable: List[Tuple[date, Path, Dict[str, Any]]] = []
    had_candidates_on_disk = bool(candidates)
    had_read_errors = False
    for path in candidates:
        data, err = _read_json_safe(path)
        if err:
            # 损坏文件不阻断其它候选，但记录。
            result.errors.append(f"{path.name}: {err}")
            had_read_errors = True
            continue
        as_of_date = _extract_as_of(data)
        if as_of_date is None:
            result.errors.append(f"{path.name}: as_of 非法/缺失")
            had_read_errors = True
            continue
        if as_of_date > analysis_time.date():
            result.errors.append(
                f"{path.name}: 未来快照 as_of={as_of_date} > analysis_time"
            )
            continue
        readable.append((as_of_date, path, data))

    if not readable:
        # [SCORE-001B-R1] 有候选文件但全部读取/解析失败 → FAILED（非 NORMAL_NO_DATA）。
        if had_candidates_on_disk and had_read_errors:
            result.status = STATUS_FAILED
            result.errors.append(
                "所有候选快照读取或解析失败（fail closed）"
            )
        else:
            result.status = STATUS_NORMAL_NO_DATA
        return result

    readable.sort(key=lambda triple: triple[0], reverse=True)

    # 第二遍：对最新候选做完整契约校验；失败则逐个降级尝试更旧版本。
    last_error: Optional[str] = None
    chosen: Optional[ResearchScoreSnapshot] = None
    for as_of_date, path, data in readable:
        try:
            chosen = _validate_snapshot_dict(
                data,
                expected_symbol=sym,
                analysis_time=analysis_time,
                knowledge_root=root,
                snapshot_path=path,
            )
            break
        except SnapshotValidationError as exc:
            last_error = f"{path.name} [{exc.reason_code}]: {exc.message}"
            result.errors.append(last_error)
            result.degradation_reasons.append(exc.reason_code)
            continue

    if chosen is None:
        # 所有候选都校验失败 → fail closed。
        result.status = STATUS_FAILED
        return result

    # 应用 loader 侧过期安全网（仅对 HAS_DATA/LOW_CONFIDENCE 降级到 STALE；
    # 不覆盖快照自身已声明的 FAILED/NORMAL_NO_DATA）。
    as_of_date = _parse_date_str(chosen.as_of)
    age_days = (analysis_time.date() - as_of_date).days if as_of_date else 0
    loader_stale = age_days > stale_after_days

    final_status = chosen.status
    if loader_stale and final_status in (STATUS_HAS_DATA, STATUS_LOW_CONFIDENCE):
        result.degradation_reasons.append(
            f"LOADER_STALE: as_of={chosen.as_of} 距 analysis_time={age_days}d"
            f" > {stale_after_days}d"
        )
        final_status = STATUS_STALE

    result.snapshot = chosen
    result.snapshot_id = chosen.snapshot_id
    result.schema_version = chosen.schema_version
    result.rubric_id = chosen.rubric_id
    result.rubric_version = chosen.rubric_version
    result.status = final_status
    return result


# ── 报告渲染 ──────────────────────────────────────────────────────────


_STATUS_LABELS: Dict[str, str] = {
    STATUS_HAS_DATA: "研究快照可用",
    STATUS_STALE: "研究快照已过期",
    STATUS_LOW_CONFIDENCE: "研究快照低置信",
    STATUS_NORMAL_NO_DATA: "无正式研究快照",
    STATUS_FAILED: "研究快照读取失败（fail closed）",
}


def render_research_score_block(result: ResearchScoreQueryResult) -> str:
    """渲染 Markdown "研究评分快照" 区块（只读摘要，不含正文）。

    状态为 ``NORMAL_NO_DATA`` / ``FAILED`` 时返回空串，让上层隐藏区块。
    本区块只作为慢变量背景，**不改变 decision/execution_action/action_tier**。
    """
    if not isinstance(result, ResearchScoreQueryResult):
        return ""
    if result.status in (STATUS_NORMAL_NO_DATA, STATUS_FAILED):
        return ""
    snap = result.snapshot
    if snap is None:
        return ""

    lines: List[str] = []
    lines.append("### 研究评分快照（背景慢变量）")
    lines.append("")
    status_label = _STATUS_LABELS.get(result.status, result.status)
    lines.append(
        f"> 来源：ZCode ``research_score_snapshot`` v{snap.schema_version}"
        f"（vendor=zcode_research_scorer）— {status_label}；"
        f"研究分只作背景证据，不覆盖 entry_timing/portfolio_fit，"
        f"不改变 decision/execution_action/action_tier。"
    )
    lines.append("")

    rec = (
        "—" if snap.scores.research_evidence_confidence is None
        else str(snap.scores.research_evidence_confidence)
    )
    tq = (
        "—" if snap.scores.thesis_quality is None
        else str(snap.scores.thesis_quality)
    )
    lines.append(
        f"- 研究证据可信度：**{rec}**　投资逻辑质量：**{tq}**"
        f"（缺失为 —，不用 0 冒充）"
    )
    lines.append(
        f"- 标的：``{snap.symbol}`` {snap.name}　as_of：``{snap.as_of}``"
        f"　rubric：``{snap.rubric_id}@{snap.rubric_version}``"
    )
    lines.append(f"- 快照：``{snap.snapshot_id}``　来源：``{snap.rel_path}``")
    if result.degradation_reasons:
        lines.append(
            "- 降级原因：" + "；".join(result.degradation_reasons[:3])
        )
    if snap.warnings:
        lines.append("- 警告：" + "；".join(snap.warnings[:3]))
    lines.append("")

    if snap.theses:
        lines.append("**投资假设摘要：**")
        lines.append("")
        for th in snap.theses[:5]:
            status_tag = th.status
            lines.append(
                f"- ``{th.thesis_id}`` {th.topic}（{th.direction}/{status_tag}）"
                f"— {_clip(th.core_hypothesis, 100)}"
            )
        lines.append("")

    if snap.missing_evidence:
        lines.append("**证据缺口：** " + "；".join(snap.missing_evidence[:4]))
        lines.append("")

    return "\n".join(lines)


def render_research_score_report(result: ResearchScoreQueryResult) -> str:
    """渲染完整查询报告（含元信息），用于 CLI 输出。"""
    lines: List[str] = []
    lines.append(f"# 研究评分快照查询报告 — {TASK_CODE}")
    lines.append("")
    lines.append(f"- 知识库：`{result.knowledge_root}`")
    lines.append(f"- symbol：`{result.symbol}`")
    lines.append(f"- analysis_time：`{result.analysis_time}`")
    lines.append(f"- 状态：{result.status}")
    if result.snapshot_id:
        lines.append(f"- snapshot_id：`{result.snapshot_id}`")
    if result.schema_version:
        lines.append(f"- schema_version：`{result.schema_version}`")
    if result.errors:
        lines.append("- errors：" + "；".join(result.errors[:5]))
    lines.append("")
    block = render_research_score_block(result)
    if block:
        lines.append(block)
    else:
        lines.append("（无可用正式研究快照）")
    return "\n".join(lines)


def suggest_query_output_path(docs_dir: str = "docs/knowledge_reports") -> str:
    today = date.today().strftime("%Y-%m-%d")
    return os.path.join(docs_dir, f"research_score_snapshot-{today}.md")


# ── API 序列化 ─────────────────────────────────────────────────────────
# [SCORE-001B] research_score_snapshot_api_adapter

#: 敏感信息正则——命中即过滤（不输出到 API 响应）。
_SENSITIVE_KEY_RE = re.compile(
    r"(token|cookie|key|secret|password|credential|api_key|apikey|auth)",
    re.IGNORECASE,
)

#: 绝对路径前缀（POSIX + Windows）。
_ABS_PATH_PREFIXES = ("/", "\\\\", "C:\\", "D:\\", "E:\\")

# [SCORE-001B-R1] 强动作词——快照文本不得泄漏知识库交易动作。
# 只覆盖"建议立即执行"级别的强动词，不覆盖方向性描述词（看多/看空/偏多/偏空）。
_FORBIDDEN_ACTION_VERBS: Tuple[str, ...] = (
    "强烈推荐买入", "强烈推荐卖出",
    "立即买入", "立即卖出", "满仓", "清仓", "全仓", "梭哈",
    "强烈推荐", "重仓买入", "重仓卖出", "强制清仓", "追涨买入",
    "建议买入", "建议卖出", "建议加仓", "建议减仓",
    "建议建仓", "建议清仓",
)


def _is_abs_path_like(value: str) -> bool:
    """判断字符串是否像绝对路径（POSIX 或 Windows）。"""
    if not value:
        return False
    stripped = value.strip()
    return (
        os.path.isabs(stripped)
        or any(stripped.startswith(p) for p in _ABS_PATH_PREFIXES)
        or (".." in Path(stripped).parts)
    )


def _filter_sensitive(obj: Any, depth: int = 0) -> Any:
    """递归过滤 dict/list 中的敏感字段和绝对路径。

    - 键名匹配 ``_SENSITIVE_KEY_RE`` → 移除
    - 值为绝对路径字符串 → 替换为 ``"[redacted_path]"``
    - 最大递归深度 6，防止深结构爆炸
    """
    if depth > 6:
        return obj
    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        for k, v in obj.items():
            if _SENSITIVE_KEY_RE.search(k):
                continue
            out[k] = _filter_sensitive(v, depth + 1)
        return out
    if isinstance(obj, list):
        return [_filter_sensitive(item, depth + 1) for item in obj]
    if isinstance(obj, str) and _is_abs_path_like(obj):
        return "[redacted_path]"
    return obj


# [SCORE-001B-R1] 强动作词清洗
def _strip_strong_action_verbs(text: str) -> str:
    """从文本中移除强动作词，替换为 ``[已过滤]``。

    只清洗"建议立即执行"级别的强动词（如"立即买入"、"满仓"），不清洗
    方向性描述（如"看多"、"偏空"）或分析性措辞。
    """
    if not text:
        return text
    result = text
    # Longest-first prevents a generic prefix such as ``强烈推荐`` from
    # leaving the actionable suffix ``买入``/``卖出`` behind.
    for verb in sorted(_FORBIDDEN_ACTION_VERBS, key=len, reverse=True):
        result = result.replace(verb, "[已过滤]")
    return result


def _sanitize_api_action_text(obj: Any, depth: int = 0) -> Any:
    """Recursively scrub strong-action phrases from the final API payload."""
    if depth > 6:
        return obj
    if isinstance(obj, dict):
        return {
            key: _sanitize_api_action_text(value, depth + 1)
            for key, value in obj.items()
        }
    if isinstance(obj, list):
        return [_sanitize_api_action_text(value, depth + 1) for value in obj]
    if isinstance(obj, str):
        return _strip_strong_action_verbs(obj)
    return obj


def snapshot_to_api_dict(result: ResearchScoreQueryResult) -> Dict[str, Any]:
    """将查询结果序列化为 API 安全的 slim dict（SCORE-001B）。

    设计约束：
    - 只输出摘要、证据引用和缺口，不返回整篇研报正文或本机绝对路径。
    - 新字段全部可选；无正式快照时返回 ``snapshot=None``，保持旧 API 兼容。
    - 过滤绝对路径、token、cookie、key 等敏感信息。
    - 不输出 ``decision`` / ``execution_action`` / ``playbook_stage`` 等动作字段。

    参数:
        result: :class:`ResearchScoreQueryResult` 实例。

    返回:
        API 安全的 dict，包含 ``status / snapshot_id / scores / theses_summary /
        evidence_refs_summary / missing_evidence / score_change_summary /
        warnings / degradation_reasons`` 等可选字段。
    """
    if not isinstance(result, ResearchScoreQueryResult):
        return {"status": STATUS_NORMAL_NO_DATA, "snapshot": None}

    base: Dict[str, Any] = {
        "status": result.status,
        "snapshot_id": result.snapshot_id,
        "schema_version": result.schema_version,
        "rubric_id": result.rubric_id,
        "rubric_version": result.rubric_version,
        "degradation_reasons": list(result.degradation_reasons or []),
        "validation_warnings": list(result.validation_warnings or []),
    }

    if result.snapshot is None:
        base["snapshot"] = None
        return _sanitize_api_action_text(_filter_sensitive(base))

    snap = result.snapshot

    # 分数摘要。
    scores = snap.scores.to_dict() if snap.scores else {}

    # 投资假设摘要（只保留 topic / direction / status / core_hypothesis 摘要）。
    # [SCORE-001B-R1] core_hypothesis 清洗强动作词。
    theses_summary: List[Dict[str, Any]] = []
    for th in snap.theses[:_MAX_THESES]:
        theses_summary.append({
            "thesis_id": th.thesis_id,
            "topic": _strip_strong_action_verbs(th.topic),
            "direction": _strip_strong_action_verbs(th.direction),
            "status": th.status,
            "core_hypothesis": _strip_strong_action_verbs(
                _clip(th.core_hypothesis, _HYPOTHESIS_MAX_CHARS)
            ),
        })

    # 证据引用摘要（只保留 claim / claim_type / source_quality_tier / report_date）。
    # [SCORE-001B-R1] claim 清洗强动作词。
    evidence_summary: List[Dict[str, Any]] = []
    for ref in snap.evidence_refs[:_MAX_EVIDENCE_REFS]:
        evidence_summary.append({
            "evidence_id": ref.evidence_id,
            "claim": _strip_strong_action_verbs(
                _clip(ref.claim, _CLAIM_MAX_CHARS)
            ),
            "claim_type": ref.claim_type,
            "source_quality_tier": ref.source_quality_tier,
            "report_date": ref.report_date,
            "financial_period": ref.financial_period,
        })

    # 分数变化摘要。
    sc = snap.score_change
    score_change_summary: Optional[Dict[str, Any]] = None
    if sc and (sc.previous or sc.current or sc.reasons):
        score_change_summary = {
            "previous_snapshot_id": sc.previous_snapshot_id,
            "reasons": [_strip_strong_action_verbs(r) for r in (sc.reasons or [])][:5],
        }

    # [SCORE-001B-R1] 所有文本字段清洗强动作词。
    base["snapshot"] = {
        "snapshot_id": snap.snapshot_id,
        "symbol": snap.symbol,
        "name": snap.name,
        "as_of": snap.as_of,
        "status": snap.status,
        "scores": scores,
        "theses_summary": theses_summary,
        "evidence_refs_summary": evidence_summary,
        "missing_evidence": [
            _strip_strong_action_verbs(s)
            for s in (snap.missing_evidence or [])
        ],
        "upgrade_conditions": [
            _strip_strong_action_verbs(s)
            for s in (snap.upgrade_conditions or [])
        ][:5],
        "downgrade_conditions": [
            _strip_strong_action_verbs(s)
            for s in (snap.downgrade_conditions or [])
        ][:5],
        "invalidation_conditions": [
            _strip_strong_action_verbs(s)
            for s in (snap.invalidation_conditions or [])
        ][:5],
        "score_change_summary": score_change_summary,
        "warnings": [
            _strip_strong_action_verbs(s)
            for s in (snap.warnings or [])
        ][:5],
    }

    return _sanitize_api_action_text(_filter_sensitive(base))


__all__ = [
    "VENDOR",
    "TASK_CODE",
    "SCHEMA_VERSION",
    "SUPPORTED_SCHEMA_VERSIONS",
    "SNAPSHOTS_DIR_NAME",
    "DRAFTS_DIR_NAME",
    "STATUS_HAS_DATA",
    "STATUS_STALE",
    "STATUS_LOW_CONFIDENCE",
    "STATUS_NORMAL_NO_DATA",
    "STATUS_FAILED",
    "VALID_SNAPSHOT_STATUSES",
    "VALID_THESIS_STATUSES",
    "FORBIDDEN_ACTION_FIELDS",
    "DEFAULT_STALE_AFTER_DAYS",
    "ScoreSummary",
    "InvestmentThesis",
    "EvidenceRef",
    "ScoreChange",
    "ResearchScoreSnapshot",
    "ResearchScoreQueryResult",
    "SnapshotValidationError",
    "query_research_score_snapshot",
    "render_research_score_block",
    "render_research_score_report",
    "suggest_query_output_path",
    "snapshot_to_api_dict",
    "_strip_strong_action_verbs",
    "_FORBIDDEN_ACTION_VERBS",
]
