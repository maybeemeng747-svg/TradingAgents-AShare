# [HY-010] half_year_update_queue
"""持仓/观察仓半年报待更新清单（HY-010）.

把 investment-controller context 中的持仓 / 观察仓 / 昊天候选合并到一个统一的
symbol universe，并对每个 symbol 标注它当前的**半年报知识覆盖状态**，输出
"哪些票已有新事实、哪些仍缺半年报、哪些存在冲突或待 Tree Work 消化"的优先
清单，供 investment-controller / OpenClaw 只读消费。

设计约束（对应 docs/TASKS.md HY-010）：
  - **纯只读**：只读 IC context 的既有 bucket（``holdings`` /
    ``observation_warehouse`` / ``tradeflow_candidates`` / ``mandate_daily_report``
    / ``half_year_facts``），不写 DB、不调用 LLM、不访问外网、不重新查知识库。
  - **优先级即资料补全顺序**：状态 / 排序只回答"今晚优先消化哪几份资料以及
    为什么"，**绝不输出交易动作或强买卖词**。
  - **缺披露日 → unknown**：``latest_disclosure_date`` 缺失时标 ``unknown``，
    不得猜日期。
  - **稳定空结构**：缺 bucket / 缺 half_year_facts / 重复 symbol / 知识库
    不可用时返回稳定空结构，**永不抛异常**。
  - **来源保留**：每条 entry 保留 ``origins`` 列表（``holding`` /
    ``observation`` / ``mandate_candidate``），同一 symbol 多来源时合并去重。

状态机（HY-010 任务描述）：
  - ``up_to_date``：半年报事实可用且有披露日，未触发反证 / 复核标记。
  - ``missing``：symbol 在 universe 中但 ``half_year_facts.items`` 没有对应条目
                  （既可能真的没知识页，也可能只有 stale 数据未被 HY-007 surfacing）。
  - ``stale``：``half_year_facts`` item 命中但 ``data_status=stale`` 或
              ``facts_status ∈ {STALE, LOW_CONFIDENCE}``（背景数据已过期）。
  - ``conflict``：``has_conflict=True`` 或 ``facts_status=CONFLICT``（同 period 多页
                  数值打架，必须 Tree Work 复核）。
  - ``needs_digest``：``thesis_check_status ∈ {contradicted, weakened}`` 或
                      ``needs_tree_work_review / needs_research_review`` 为真
                      （事实与既有观点冲突，需要消化）。
  - ``unknown``：事实可用但 ``latest_disclosure_date`` 缺失（无法判定披露窗口）。

优先级排序（HY-010 任务描述，从小到大）：
  0. 持仓 + 冲突            — 持仓股事实打架，最高优先复核
  1. 持仓 + 缺失            — 持仓股缺半年报事实
  2. 持仓 + needs_digest    — 持仓股事实打脸既有观点
  3. 持仓 + 其他             — 持仓股 stale/unknown/up_to_date
  4. 观察仓 + 接近触发       — observation_state ∈ {near_entry, in_entry_zone, ta_required}
  5. 昊天主候选             — mandate main_candidate
  6. 观察仓 + 其他状态       — observation watching/invalidated/data_missing
  7. 昊天观察候选           — mandate observation_candidate
  9. 兜底

输出契约（每条 :class:`HalfYearUpdateEntry`）::

    {
      "symbol": str, "name": str,
      "origins": ["holding" | "observation" | "mandate_candidate", ...],
      "status": "up_to_date" | "missing" | "stale" | "conflict" |
                "needs_digest" | "unknown",
      "priority_rank": int,            # 0..9, 越小越优先
      "priority_tier": str,            # 人类可读 tier 名
      "latest_period": str | "",
      "latest_disclosure_date": str | "",
      "half_year_score": float,
      "thesis_check_status": str,
      "observation_state": str,        # "" 表示无观察仓来源
      "candidate_type": str,           # "" 表示无候选来源
      "mandate_score": float,          # 0.0 表示无昊天分
      "is_main_candidate": bool,
      "reason": str,                   # ≤200 字符，无强买卖词
      "fact_summary": str,             # ≤200 字符，复用 HY-007 pre-clip 文本
      "source": "half_year_update_queue"
    }

使用示例::

    from tradingagents.tradeflow.half_year_update_queue import (
        build_half_year_update_queue,
        render_half_year_update_markdown,
    )
    queue = build_half_year_update_queue(ic_context)
    print(render_half_year_update_markdown(queue))
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

# ── 任务常量 ──────────────────────────────────────────────────────────

TASK_CODE = "HY-010"
VENDOR = "half_year_update_queue"

# Symbol 来源标签（与任务描述对齐）。
ORIGIN_HOLDING = "holding"
ORIGIN_OBSERVATION = "observation"
ORIGIN_MANDATE_CANDIDATE = "mandate_candidate"

# 状态枚举（与任务描述对齐）。
STATUS_UP_TO_DATE = "up_to_date"
STATUS_MISSING = "missing"
STATUS_STALE = "stale"
STATUS_CONFLICT = "conflict"
STATUS_NEEDS_DIGEST = "needs_digest"
STATUS_UNKNOWN = "unknown"

ALL_STATUSES: Tuple[str, ...] = (
    STATUS_UP_TO_DATE,
    STATUS_MISSING,
    STATUS_STALE,
    STATUS_CONFLICT,
    STATUS_NEEDS_DIGEST,
    STATUS_UNKNOWN,
)

# 观察仓"接近触发"状态集合（来自 observation_state_engine）。
_OBS_NEAR_TRIGGER_STATES = frozenset({
    "near_entry",
    "in_entry_zone",
    "ta_required",
})

# 优先级 tier 名（人类可读）。
_TIER_HOLDING_CONFLICT = "P0_HOLDING_CONFLICT"
_TIER_HOLDING_MISSING = "P1_HOLDING_MISSING"
_TIER_HOLDING_NEEDS_DIGEST = "P2_HOLDING_NEEDS_DIGEST"
_TIER_HOLDING_OTHER = "P3_HOLDING_OTHER"
_TIER_OBSERVATION_NEAR_TRIGGER = "P4_OBSERVATION_NEAR_TRIGGER"
_TIER_MANDATE_MAIN = "P5_MANDATE_MAIN"
_TIER_OBSERVATION_OTHER = "P6_OBSERVATION_OTHER"
_TIER_MANDATE_OBSERVATION = "P7_MANDATE_OBSERVATION"
_TIER_FALLBACK = "P9_FALLBACK"

# 文本裁剪上限（与 HY-007 briefing 口径一致）。
_REASON_MAX_CHARS = 200
_FACT_SUMMARY_MAX_CHARS = 200

# 强动作禁用词（与 HY-005/006/007 一致）——reason / fact_summary 不得出现。
_FORBIDDEN_ACTION_WORDS: Tuple[str, ...] = (
    "买入", "卖出", "加仓", "减仓", "立即买入", "立即卖出",
    "全仓", "满仓", "清仓", "止损", "建仓", "强烈推荐",
    "BUY", "SELL", "strong buy", "strong sell",
)


# ── 数据类 ────────────────────────────────────────────────────────────


@dataclass
class HalfYearUpdateEntry:
    """单只 symbol 的半年报覆盖状态 entry。

    所有字段都是只读事实/软提醒，绝不携带交易动作。
    """

    symbol: str
    name: str = ""
    origins: List[str] = field(default_factory=list)
    status: str = STATUS_MISSING
    priority_rank: int = 9
    priority_tier: str = _TIER_FALLBACK
    latest_period: str = ""
    latest_disclosure_date: str = ""
    half_year_score: float = 0.0
    thesis_check_status: str = ""
    observation_state: str = ""
    candidate_type: str = ""
    mandate_score: float = 0.0
    is_main_candidate: bool = False
    reason: str = ""
    fact_summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "origins": list(self.origins),
            "status": self.status,
            "priority_rank": self.priority_rank,
            "priority_tier": self.priority_tier,
            "latest_period": self.latest_period,
            "latest_disclosure_date": self.latest_disclosure_date,
            "half_year_score": self.half_year_score,
            "thesis_check_status": self.thesis_check_status,
            "observation_state": self.observation_state,
            "candidate_type": self.candidate_type,
            "mandate_score": self.mandate_score,
            "is_main_candidate": self.is_main_candidate,
            "reason": self.reason,
            "fact_summary": self.fact_summary,
            "source": VENDOR,
        }


@dataclass
class HalfYearUpdateQueue:
    """整次半年报待更新清单的聚合结果。"""

    as_of: str = ""
    items: List[HalfYearUpdateEntry] = field(default_factory=list)
    summary_by_status: Dict[str, int] = field(default_factory=dict)
    summary_by_origin: Dict[str, int] = field(default_factory=dict)
    universe_size: int = 0
    knowledge_root_available: bool = True
    notes: List[str] = field(default_factory=list)
    task: str = TASK_CODE
    vendor: str = VENDOR

    def to_dict(self) -> Dict[str, Any]:
        return {
            "as_of": self.as_of,
            "task": self.task,
            "vendor": self.vendor,
            "universe_size": self.universe_size,
            "knowledge_root_available": self.knowledge_root_available,
            "summary_by_status": dict(self.summary_by_status),
            "summary_by_origin": dict(self.summary_by_origin),
            "items": [it.to_dict() for it in self.items],
            "notes": list(self.notes),
        }


# ── 内部辅助 ──────────────────────────────────────────────────────────


def _bucket(context: Dict[str, Any], key: str) -> Dict[str, Any]:
    """Safe bucket lookup — missing / wrong type → empty dict."""
    raw = context.get(key)
    return raw if isinstance(raw, dict) else {}


def _items(bucket: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Safe items[] lookup — non-list / non-dict entries filtered out."""
    raw = bucket.get("items")
    if not isinstance(raw, list):
        return []
    return [it for it in raw if isinstance(it, dict)]


def _str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _to_float(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _clip_text(text: str, max_chars: int) -> str:
    raw = (text or "").strip()
    if len(raw) <= max_chars:
        return raw
    # 留 1 字符给省略号，保证裁剪后总长度 ≤ max_chars。
    return raw[: max(0, max_chars - 1)].rstrip() + "…"


def _assert_no_forbidden_words(text: str, field_name: str) -> None:
    """Defensive guard — never let trade-verb leak into reason / summary."""
    for word in _FORBIDDEN_ACTION_WORDS:
        if word in text:
            raise AssertionError(
                f"[HY-010] forbidden action word {word!r} leaked into "
                f"{field_name}: {text!r}"
            )


# ── universe 构建 ────────────────────────────────────────────────────


def _build_symbol_universe(
    *,
    holdings: Dict[str, Any],
    observation: Dict[str, Any],
    candidates: Dict[str, Any],
    mandate_report: Dict[str, Any],
) -> Tuple[Dict[str, HalfYearUpdateEntry], List[str]]:
    """Merge holdings / observation / candidates / mandate into one universe.

    Returns ``(entries_by_symbol, notes)``。同一 symbol 多来源时合并 origins
    与各 bucket 携带的属性（observation_state / candidate_type / mandate_score /
    is_main_candidate）。来源优先级：holding > observation > mandate_candidate
    （用于决定 name 兜底与主来源）。
    """
    entries: Dict[str, HalfYearUpdateEntry] = {}
    notes: List[str] = []

    def _ensure(symbol: str) -> Optional[HalfYearUpdateEntry]:
        sym = _str(symbol)
        if not sym:
            return None
        entry = entries.get(sym)
        if entry is None:
            entry = HalfYearUpdateEntry(symbol=sym)
            entries[sym] = entry
        return entry

    # 1) holdings
    holding_items = _items(holdings)
    for item in holding_items:
        entry = _ensure(item.get("symbol"))
        if entry is None:
            continue
        if ORIGIN_HOLDING not in entry.origins:
            entry.origins.append(ORIGIN_HOLDING)
        name = _str(item.get("name"))
        if name and not entry.name:
            entry.name = name

    # 2) observation_warehouse
    observation_items = _items(observation)
    for item in observation_items:
        entry = _ensure(item.get("symbol"))
        if entry is None:
            continue
        if ORIGIN_OBSERVATION not in entry.origins:
            entry.origins.append(ORIGIN_OBSERVATION)
        name = _str(item.get("name"))
        if name and not entry.name:
            entry.name = name
        # 观察仓 status → observation_state（保留首个非空值，后续不覆盖）。
        obs_status = _str(item.get("status"))
        if obs_status and not entry.observation_state:
            entry.observation_state = obs_status

    # 3) tradeflow_candidates
    candidate_items = _items(candidates)
    for item in candidate_items:
        entry = _ensure(item.get("symbol"))
        if entry is None:
            continue
        if ORIGIN_MANDATE_CANDIDATE not in entry.origins:
            entry.origins.append(ORIGIN_MANDATE_CANDIDATE)
        name = _str(item.get("name"))
        if name and not entry.name:
            entry.name = name
        cand_type = _str(item.get("candidate_type"))
        if cand_type and not entry.candidate_type:
            entry.candidate_type = cand_type

    # 4) mandate_daily_report.main_candidates / observation_candidates
    mandate_ok = _str(mandate_report.get("data_status")) == "fresh"
    if mandate_ok:
        for cand in mandate_report.get("main_candidates", []) or []:
            if not isinstance(cand, dict):
                continue
            entry = _ensure(cand.get("symbol"))
            if entry is None:
                continue
            if ORIGIN_MANDATE_CANDIDATE not in entry.origins:
                entry.origins.append(ORIGIN_MANDATE_CANDIDATE)
            name = _str(cand.get("name"))
            if name and not entry.name:
                entry.name = name
            entry.is_main_candidate = True
            score = _to_float(cand.get("mandate_score"))
            if score > entry.mandate_score:
                entry.mandate_score = score
            topic = _str(cand.get("topic"))
            if topic and not entry.candidate_type:
                entry.candidate_type = topic
        for cand in mandate_report.get("observation_candidates", []) or []:
            if not isinstance(cand, dict):
                continue
            entry = _ensure(cand.get("symbol"))
            if entry is None:
                continue
            if ORIGIN_MANDATE_CANDIDATE not in entry.origins:
                entry.origins.append(ORIGIN_MANDATE_CANDIDATE)
            name = _str(cand.get("name"))
            if name and not entry.name:
                entry.name = name
            # observation_candidate 不置 is_main_candidate=True
            score = _to_float(cand.get("mandate_score"))
            if score > entry.mandate_score:
                entry.mandate_score = score

    if not entries:
        notes.append("universe empty: holdings/observation/candidates/mandate all empty")
    return entries, notes


# ── half_year_facts 查表 ─────────────────────────────────────────────


def _index_half_year_facts(
    half_year_bucket: Dict[str, Any],
) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """Build a {symbol: item} index from half_year_facts bucket items.

    Returns ``(index, notes)``。bucket 缺失 / data_status=skipped/failed/missing
    时返回空 index 与对应 note，绝不抛异常。
    """
    if not half_year_bucket:
        return {}, ["half_year_facts bucket missing — all symbols -> missing"]

    data_status = _str(half_year_bucket.get("data_status"))
    if data_status in ("skipped", "failed"):
        return {}, [f"half_year_facts data_status={data_status} — all symbols -> missing"]

    items = _items(half_year_bucket)
    if not items and data_status == "missing":
        return {}, ["half_year_facts data_status=missing — all symbols -> missing"]

    index: Dict[str, Dict[str, Any]] = {}
    for item in items:
        symbol = _str(item.get("symbol"))
        if not symbol:
            continue
        # 首条命中保留（HY-007 已按 symbol 去重，这里防御性处理）。
        if symbol not in index:
            index[symbol] = item
    return index, []


# ── 状态分类 ──────────────────────────────────────────────────────────


def _classify_status(item: Optional[Dict[str, Any]]) -> str:
    """把 half_year_facts item 映射到 HY-010 状态。

    ``item is None`` 表示 symbol 在 universe 中但 half_year_facts bucket 没有条目
    （HY-007 只 surfacing fresh/rebuttal/needs-review 的标的，其余被滤掉）。
    """
    if item is None:
        return STATUS_MISSING

    facts_status = _str(item.get("facts_status"))
    has_conflict = bool(item.get("has_conflict"))
    if has_conflict or facts_status == "CONFLICT":
        return STATUS_CONFLICT

    thesis = _str(item.get("thesis_check_status"))
    if thesis in ("contradicted", "weakened"):
        return STATUS_NEEDS_DIGEST
    if bool(item.get("needs_tree_work_review")) or bool(
        item.get("needs_research_review")
    ):
        return STATUS_NEEDS_DIGEST

    data_status = _str(item.get("data_status"))
    has_stale = bool(item.get("has_stale"))
    if data_status == "stale" or has_stale or facts_status in ("STALE", "LOW_CONFIDENCE"):
        return STATUS_STALE

    # 下面是 HAS_FACTS / fresh 分支。
    # 缺披露日 → unknown（任务约束：不得猜日期）。
    disclosure = _str(item.get("latest_disclosure_date"))
    if not disclosure:
        return STATUS_UNKNOWN

    return STATUS_UP_TO_DATE


_STATUS_RANK: Dict[str, int] = {
    STATUS_CONFLICT: 0,
    STATUS_NEEDS_DIGEST: 1,
    STATUS_MISSING: 2,
    STATUS_STALE: 3,
    STATUS_UNKNOWN: 4,
    STATUS_UP_TO_DATE: 5,
}


def _compute_priority(entry: HalfYearUpdateEntry) -> Tuple[int, str]:
    """计算 (priority_rank, priority_tier)。

    任务描述排序：持仓冲突 > 持仓缺失 > 接近触发观察仓 > 昊天主候选 > 其他。
    持仓 needs_digest / stale / unknown 填补在持仓缺失与观察仓之间，保持持仓
    bucket 内部优先于观察仓/候选池。
    """
    origins = entry.origins
    is_holding = ORIGIN_HOLDING in origins
    is_observation = ORIGIN_OBSERVATION in origins
    is_mandate = ORIGIN_MANDATE_CANDIDATE in origins

    if is_holding:
        if entry.status == STATUS_CONFLICT:
            return 0, _TIER_HOLDING_CONFLICT
        if entry.status == STATUS_MISSING:
            return 1, _TIER_HOLDING_MISSING
        if entry.status == STATUS_NEEDS_DIGEST:
            return 2, _TIER_HOLDING_NEEDS_DIGEST
        return 3, _TIER_HOLDING_OTHER

    if is_observation and entry.observation_state in _OBS_NEAR_TRIGGER_STATES:
        return 4, _TIER_OBSERVATION_NEAR_TRIGGER

    if is_mandate and entry.is_main_candidate:
        return 5, _TIER_MANDATE_MAIN

    if is_observation:
        return 6, _TIER_OBSERVATION_OTHER

    if is_mandate:
        return 7, _TIER_MANDATE_OBSERVATION

    return 9, _TIER_FALLBACK


def _build_reason(
    entry: HalfYearUpdateEntry,
    fact_item: Optional[Dict[str, Any]],
) -> str:
    """构造 ≤200 字符的 reason（无强买卖词）。

    优先复用 HY-007 已 pre-clip 的 fact_summary_text / thesis_inline，
    缺失时退化到模板；最后兜底裁剪 + 防御性禁词检查。
    """
    name = entry.name or entry.symbol
    origins_label = "/".join(entry.origins) or "unknown"
    period = entry.latest_period or ""
    period_tail = f"（报告期 {period}）" if period else ""

    status = entry.status
    if status == STATUS_CONFLICT:
        base = f"{name}{period_tail}：半年报事实冲突，需 Tree Work 复核（来源 {origins_label}）"
    elif status == STATUS_NEEDS_DIGEST:
        thesis = entry.thesis_check_status or "weakened"
        inline = _str(fact_item.get("thesis_inline") if fact_item else "")
        if inline:
            base = f"{name}{period_tail}：{inline}（来源 {origins_label}）"
        else:
            base = (
                f"{name}{period_tail}：半年报反证（{thesis}），需安排 TA 复核"
                f"（来源 {origins_label}）"
            )
    elif status == STATUS_MISSING:
        base = f"{name}：半年报事实未 surfacing（来源 {origins_label}），建议确认是否已 ingest"
    elif status == STATUS_STALE:
        base = f"{name}{period_tail}：半年报事实已过期（来源 {origins_label}），建议刷新"
    elif status == STATUS_UNKNOWN:
        base = (
            f"{name}{period_tail}：半年报事实可用但缺披露日（来源 {origins_label}），"
            f"标记 unknown，不得猜日期"
        )
    else:  # up_to_date
        base = f"{name}{period_tail}：半年报事实已更新（来源 {origins_label}）"

    return _clip_text(base, _REASON_MAX_CHARS)


def _build_fact_summary(fact_item: Optional[Dict[str, Any]]) -> str:
    """从 half_year_facts item 提取 ≤200 字符的 fact_summary（无强买卖词）。"""
    if not fact_item:
        return ""
    text = _str(fact_item.get("fact_summary_text"))
    return _clip_text(text, _FACT_SUMMARY_MAX_CHARS)


# ── 主入口 ────────────────────────────────────────────────────────────


def build_half_year_update_queue(
    context: Dict[str, Any],
    *,
    as_of: Optional[str] = None,
) -> HalfYearUpdateQueue:
    """从 IC context 构建"半年报待更新清单"。

    参数:
        context: investment_controller_context 完整 dict（至少包含
            ``holdings`` / ``observation_warehouse`` / ``tradeflow_candidates``
            / ``mandate_daily_report`` / ``half_year_facts`` bucket；缺失时
            降级为空 universe）。
        as_of: 可选时间戳；缺省用 ``context['as_of']`` 或 ``datetime.now()``。

    返回:
        :class:`HalfYearUpdateQueue`，永不抛异常。空 universe 时返回稳定空结构。
    """
    if not isinstance(context, dict):
        return HalfYearUpdateQueue(
            as_of=_str(as_of) or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            notes=["context is not a dict — returning empty queue"],
            knowledge_root_available=False,
        )

    ts = _str(as_of) or _str(context.get("as_of")) or datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    holdings = _bucket(context, "holdings")
    observation = _bucket(context, "observation_warehouse")
    candidates = _bucket(context, "tradeflow_candidates")
    mandate_report = _bucket(context, "mandate_daily_report")
    half_year_bucket = _bucket(context, "half_year_facts")

    queue = HalfYearUpdateQueue(as_of=ts)
    # knowledge_root_available：half_year_facts bucket 缺失或 data_status
    # 为 skipped/failed 时视为知识根不可用；missing 仅代表"扫了但没结果"，
    # 知识根本身仍可访问。
    hy_data_status = _str(half_year_bucket.get("data_status"))
    queue.knowledge_root_available = bool(half_year_bucket) and hy_data_status not in (
        "skipped", "failed"
    )

    entries, universe_notes = _build_symbol_universe(
        holdings=holdings,
        observation=observation,
        candidates=candidates,
        mandate_report=mandate_report,
    )
    queue.notes.extend(universe_notes)

    fact_index, fact_notes = _index_half_year_facts(half_year_bucket)
    queue.notes.extend(fact_notes)
    if not queue.knowledge_root_available:
        # 已经由 fact_notes 解释；不重复 append。
        pass

    # 给每个 entry 标注 status / half_year attributes / priority / reason。
    for symbol, entry in entries.items():
        fact_item = fact_index.get(symbol)
        entry.status = _classify_status(fact_item)

        if fact_item is not None:
            entry.latest_period = _str(fact_item.get("latest_period"))
            entry.latest_disclosure_date = _str(
                fact_item.get("latest_disclosure_date")
            )
            entry.half_year_score = _to_float(fact_item.get("half_year_score"))
            entry.thesis_check_status = _str(
                fact_item.get("thesis_check_status")
            )
            entry.fact_summary = _build_fact_summary(fact_item)
        else:
            # half_year_facts bucket 缺该 symbol → 保持默认 missing/空字段。
            # 把 has_conflict 等 boolean 显式置 False（防御）。
            entry.fact_summary = ""

        rank, tier = _compute_priority(entry)
        entry.priority_rank = rank
        entry.priority_tier = tier
        entry.reason = _build_reason(entry, fact_item)

        # 防御性自检（开发期 catch 上游 pre-clip 不严的回归）。
        _assert_no_forbidden_words(entry.reason, "reason")
        _assert_no_forbidden_words(entry.fact_summary, "fact_summary")

    # 排序：priority_rank → status_rank → half_year_score（升序，分数越低越靠前）
    #       → symbol（稳定）。
    sorted_entries = sorted(
        entries.values(),
        key=lambda e: (
            e.priority_rank,
            _STATUS_RANK.get(e.status, 9),
            e.half_year_score,
            e.symbol,
        ),
    )

    queue.items = sorted_entries
    queue.universe_size = len(sorted_entries)

    # 状态计数（包含所有状态，0 也要列出，便于下游稳定消费）。
    queue.summary_by_status = {s: 0 for s in ALL_STATUSES}
    for entry in sorted_entries:
        queue.summary_by_status[entry.status] = (
            queue.summary_by_status.get(entry.status, 0) + 1
        )

    # 来源计数（一个 symbol 多来源时每个来源都计一次）。
    queue.summary_by_origin = {
        ORIGIN_HOLDING: 0,
        ORIGIN_OBSERVATION: 0,
        ORIGIN_MANDATE_CANDIDATE: 0,
    }
    for entry in sorted_entries:
        for origin in entry.origins:
            queue.summary_by_origin[origin] = (
                queue.summary_by_origin.get(origin, 0) + 1
            )

    return queue


# ── Markdown 渲染 ────────────────────────────────────────────────────


_STATUS_LABELS: Dict[str, str] = {
    STATUS_UP_TO_DATE: "已更新",
    STATUS_MISSING: "缺失",
    STATUS_STALE: "已过期",
    STATUS_CONFLICT: "事实冲突",
    STATUS_NEEDS_DIGEST: "待消化反证",
    STATUS_UNKNOWN: "缺披露日",
}


def render_half_year_update_markdown(queue: HalfYearUpdateQueue) -> str:
    """渲染"半年报待更新清单" Markdown 报告。

    顶部摘要 + 表格（priority_rank / symbol / origins / status / 报告期 /
    披露日 / reason）。不输出交易动作。
    """
    if not isinstance(queue, HalfYearUpdateQueue):
        return ""

    lines: List[str] = []
    lines.append(f"# 半年报待更新清单 — {TASK_CODE}")
    lines.append("")
    lines.append(f"- 时间戳：`{queue.as_of}`")
    lines.append(f"- universe 大小：**{queue.universe_size}**")
    lines.append(
        f"- 知识库可用：{'是' if queue.knowledge_root_available else '否（half_year_facts skipped/failed）'}"
    )
    status_line = " / ".join(
        f"{_STATUS_LABELS.get(s, s)} {queue.summary_by_status.get(s, 0)}"
        for s in ALL_STATUSES
    )
    lines.append(f"- 状态分布：{status_line}")
    origin_line = " / ".join(
        f"{origin} {queue.summary_by_origin.get(origin, 0)}"
        for origin in (ORIGIN_HOLDING, ORIGIN_OBSERVATION, ORIGIN_MANDATE_CANDIDATE)
    )
    lines.append(f"- 来源分布：{origin_line}")
    if queue.notes:
        lines.append("- 备注：")
        for note in queue.notes[:6]:
            lines.append(f"  - {note}")
    lines.append("")
    lines.append(
        "> 本清单只表示资料补全 / 消化优先级，**不输出交易动作**；缺披露日标 unknown，"
        "不得猜日期。"
    )
    lines.append("")

    if not queue.items:
        lines.append("（universe 为空，无可排队的半年报待更新项）")
        return "\n".join(lines)

    lines.append(
        "| 优先级 | tier | symbol | 名称 | 来源 | 状态 | 报告期 | 披露日 | 半年报分 | reason |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for entry in queue.items:
        status_label = _STATUS_LABELS.get(entry.status, entry.status)
        origins_label = "/".join(entry.origins) or "-"
        disclosure = entry.latest_disclosure_date or "unknown"
        period = entry.latest_period or "-"
        score = f"{entry.half_year_score:.1f}" if entry.half_year_score else "-"
        # 表格 reason 里换行替换为空格，避免破坏表格。
        reason_cell = entry.reason.replace("|", "/").replace("\n", " ")
        lines.append(
            f"| {entry.priority_rank} | {entry.priority_tier} | "
            f"{entry.symbol} | {entry.name or '-'} | {origins_label} | "
            f"{status_label} | {period} | {disclosure} | {score} | {reason_cell} |"
        )
    lines.append("")
    return "\n".join(lines)


def suggest_report_path(docs_dir: str = "docs/knowledge_reports") -> str:
    """建议的报告输出路径（与 HY-002/HY-003 一致）。"""
    today = datetime.now().strftime("%Y-%m-%d")
    return f"{docs_dir}/half_year_update_queue-{today}.md"


__all__ = [
    "TASK_CODE",
    "VENDOR",
    "ORIGIN_HOLDING",
    "ORIGIN_OBSERVATION",
    "ORIGIN_MANDATE_CANDIDATE",
    "STATUS_UP_TO_DATE",
    "STATUS_MISSING",
    "STATUS_STALE",
    "STATUS_CONFLICT",
    "STATUS_NEEDS_DIGEST",
    "STATUS_UNKNOWN",
    "ALL_STATUSES",
    "HalfYearUpdateEntry",
    "HalfYearUpdateQueue",
    "build_half_year_update_queue",
    "render_half_year_update_markdown",
    "suggest_report_path",
]
