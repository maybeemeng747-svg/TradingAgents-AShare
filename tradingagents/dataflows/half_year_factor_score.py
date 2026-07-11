# [HY-006] tradeflow_half_year_factor
"""TradeFlow/昊天候选接入半年报因子与降权规则。

把 HY-003 半年报事实表 (:class:`HalfYearFactsQueryResult`) 与 HY-005
观点-事实反证检测结果 (:class:`ThesisFactCheckResult`) 聚合成一个扁平字典，
供 TradeFlow 候选详情 / 昊天证据包 / 前端 drawer 渲染。

输出结构与 KB-004 ``compute_local_knowledge_score`` 风格一致（扁平 dict +
summary + errors），便于前端复用同一套渲染逻辑。

核心约束（对应任务 HY-006）：
  - **半年报知识不能单独把弱候选提升为主候选**：正向分上限
    ``_HALF_YEAR_SCORE_POSITIVE_CAP``（1.0），远低于本地知识命中分上限
    （3.0），即使事实强力支持也只能"提高研究优先级"，不改变 action_tier。
  - **只允许降权/解释/研究优先级调整**：``action_tier_scorer`` 的 7 个评分
    因子没有一个读取本模块字段；``half_year_fact_score`` 仅作为解释信息，
    ``downgrade_reasons`` 只追加到候选 ``downgrade_reasons`` 列表。
  - **数据缺失不得惩罚过度**：无半年报数据时 score=0，只标
    ``needs_research_review=True`` 提示研究缺口，不施加负分。
  - **失败不变成研究缺口**：FAILED 是基础设施故障（知识库缺失/解析异常），
    ``needs_research_review=False``，与 KB-004 ``needs_tree_work_research``
    的 FAILED 语义保持一致。

不调用 LLM / 不访问外网 / 不写 DB / 不抛异常。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# [HY-006] tradeflow_half_year_factor — 分数区间常量。
# 正向上限：事实支持只能"提高研究优先级"，不能把弱技术候选抬成主候选。
# 取值远低于本地知识命中分上限（3.0），体现"半年报是佐证不是发动机"。
_HALF_YEAR_SCORE_POSITIVE_CAP = 1.0
# 负向下限：强反证时的最大降权幅度。
_HALF_YEAR_SCORE_NEGATIVE_FLOOR = -3.0

# 降权分档（与 HY-005 evidence_level 对齐）。
_SCORE_CONTRADICTED_STRONG = -3.0
_SCORE_CONTRADICTED_MODERATE = -2.0
_SCORE_WEAKENED_MULTI = -1.0
_SCORE_WEAKENED_SINGLE = -0.5
_SCORE_SUPPORTED = 1.0
_SCORE_INSUFFICIENT = 0.0

# 风险/摘要文本裁剪上限。
_SUMMARY_MAX_CHARS = 200
_RISK_FLAG_MAX = 6
_REASON_MAX_CHARS = 160
_SUMMARY_ENTRIES = 3

# 任务编码。
TASK_CODE = "HY-006"

# 候选状态枚举（本模块产出）。
STATUS_HAS_FACTS = "HAS_FACTS"
STATUS_NO_FACTS = "NO_FACTS"
STATUS_FAILED = "FAILED"

# thesis_check_status 占位（无 HY-005 结果时）。
THESIS_STATUS_NONE = "no_thesis"

# 不得出现在 summary / risk_flags 中的强动作词（防回归）。
_FORBIDDEN_ACTION_WORDS = (
    "买入", "卖出", "加仓", "减仓", "止损", "建仓", "满仓", "清仓",
    "buy", "sell", "strong buy", "strong sell",
)


# [HY-006] tradeflow_half_year_factor
def compute_half_year_factor_score(
    facts_result: Optional[Any],
    thesis_result: Optional[Any] = None,
    *,
    candidate_type: str = "",
    mandate_topic: str = "",
) -> Dict[str, Any]:
    """把半年报事实 + 观点反证结果聚合为候选半年报因子字典。

    参数:
        facts_result: HY-003 :class:`HalfYearFactsQueryResult`，或 ``None``。
            支持 duck-typing：只要有 ``status`` / ``pages`` / ``risks`` /
            ``summary`` / ``latest_period`` / ``data_status`` / ``errors``
            字段即可（``to_dict`` 产出的 dict 也可）。
        thesis_result: HY-005 :class:`ThesisFactCheckResult`，或 ``None``。
            支持 duck-typing：``thesis_check_status`` / ``contradiction_flags``
            / ``weakened_flags`` / ``supported_flags`` / ``evidence_level`` /
            ``needs_tree_work_review`` / ``summary`` / ``errors``。``None`` 时
            只基于事实做评分（无反证维度）。
        candidate_type: 候选类型（POLICY_AMBUSH / POLICY_CONFIRM / EVENT_WATCH
            / TECH_TRADE …）。用于判断是否属于昊天左侧池，影响
            ``needs_research_review``。
        mandate_topic: 昊天主题。非空时视作左侧候选。

    返回:
        扁平字典，字段见模块文档。永远不会抛异常。
    """
    empty: Dict[str, Any] = _empty_structure()
    if facts_result is None and thesis_result is None:
        return empty

    # ── 解析事实结果 ──
    facts_dict = _coerce_facts(facts_result)
    facts_status = facts_dict.get("status", "")
    facts_pages = facts_dict.get("pages") or []
    facts_risks = facts_dict.get("risks") or []
    facts_summary_list = facts_dict.get("summary") or []
    latest_period = facts_dict.get("latest_period")
    data_status = facts_dict.get("data_status", "") or ""
    facts_errors = facts_dict.get("errors") or []

    # 基础设施失败：不变成研究缺口，返回空结构并透传错误。
    if facts_status == "FAILED" and thesis_result is None:
        empty["status"] = STATUS_FAILED
        empty["errors"] = list(facts_errors)[:5]
        empty["half_year_fact_summary"] = "半年报事实查询失败，不参与因子评分。"
        return empty

    # ── 解析反证结果 ──
    thesis_dict = _coerce_thesis(thesis_result)
    thesis_status = thesis_dict.get("thesis_check_status", THESIS_STATUS_NONE)
    contradiction_flags = thesis_dict.get("contradiction_flags") or []
    weakened_flags = thesis_dict.get("weakened_flags") or []
    supported_flags = thesis_dict.get("supported_flags") or []
    thesis_evidence_level = thesis_dict.get("evidence_level", "") or ""
    thesis_needs_review = bool(thesis_dict.get("needs_tree_work_review", False))
    thesis_summary_list = thesis_dict.get("summary") or []
    thesis_errors = thesis_dict.get("errors") or []

    # ── 是否有可用事实 ──
    has_fresh_facts = _has_usable_facts(facts_status, facts_pages, data_status)
    overall_status = STATUS_HAS_FACTS if has_fresh_facts else (
        STATUS_FAILED if facts_status == "FAILED" else STATUS_NO_FACTS
    )

    # ── 计算因子分（反证优先）──
    score, score_reason = _compute_score(
        thesis_status=thesis_status,
        thesis_evidence_level=thesis_evidence_level,
        contradiction_count=len(contradiction_flags),
        weakened_count=len(weakened_flags),
        supported_count=len(supported_flags),
        has_fresh_facts=has_fresh_facts,
    )

    # ── 降权原因（contradicted / weakened）──
    downgrade_reasons: List[str] = _build_downgrade_reasons(
        thesis_status=thesis_status,
        contradiction_flags=contradiction_flags,
        weakened_flags=weakened_flags,
    )

    # ── 风险标记（半年报风险 + 反证风险）──
    risk_flags = _build_risk_flags(facts_risks, contradiction_flags, weakened_flags)

    # ── needs_research_review ──
    # 触发条件：
    #   1. HY-005 反证需要 Tree Work 复核（contradicted 或 weakened>=2），或
    #   2. 昊天左侧候选但没有可用半年报事实（研究缺口）。
    # FAILED 不触发（基础设施故障不是研究缺口）。
    is_mandate_candidate = _is_mandate_candidate(candidate_type, mandate_topic)
    needs_review = False
    if overall_status != STATUS_FAILED:
        if thesis_needs_review:
            needs_review = True
        elif is_mandate_candidate and not has_fresh_facts:
            needs_review = True

    # ── 研究优先级提示（只解释，不改 action tier）──
    research_priority_hint = _research_priority_hint(
        has_fresh_facts=has_fresh_facts,
        thesis_status=thesis_status,
        is_mandate_candidate=is_mandate_candidate,
    )

    # ── 摘要 ──
    summary = _render_summary(
        score=score,
        score_reason=score_reason,
        has_fresh_facts=has_fresh_facts,
        latest_period=latest_period,
        data_status=data_status,
        thesis_status=thesis_status,
        contradiction_count=len(contradiction_flags),
        weakened_count=len(weakened_flags),
        supported_count=len(supported_flags),
        facts_summary_list=facts_summary_list,
        thesis_summary_list=thesis_summary_list,
        overall_status=overall_status,
    )

    errors: List[str] = []
    errors.extend(str(e) for e in facts_errors if e)
    errors.extend(str(e) for e in thesis_errors if e)
    errors = errors[:5]

    detail: Dict[str, Any] = {
        "facts_status": facts_status,
        "facts_latest_period": latest_period,
        "facts_data_status": data_status,
        "facts_page_count": len(facts_pages),
        "thesis_check_status": thesis_status,
        "thesis_evidence_level": thesis_evidence_level,
        "contradiction_count": len(contradiction_flags),
        "weakened_count": len(weakened_flags),
        "supported_count": len(supported_flags),
        "thesis_needs_tree_work_review": thesis_needs_review,
        "score_reason": score_reason,
        "candidate_type": candidate_type,
        "mandate_topic": mandate_topic,
    }

    return {
        "half_year_fact_score": round(score, 2),
        "half_year_fact_summary": summary,
        "half_year_risk_flags": risk_flags,
        "half_year_fact_detail": detail,
        "needs_research_review": bool(needs_review),
        "has_fresh_facts": bool(has_fresh_facts),
        "fact_period": latest_period,
        "fact_data_status": data_status,
        "thesis_check_status": thesis_status if thesis_status != THESIS_STATUS_NONE else "",
        "status": overall_status,
        "downgrade_reasons": downgrade_reasons,
        "research_priority_hint": research_priority_hint,
        "errors": errors,
    }


# ── 内部工具 ──────────────────────────────────────────────────────────


def _empty_structure() -> Dict[str, Any]:
    return {
        "half_year_fact_score": 0.0,
        "half_year_fact_summary": "",
        "half_year_risk_flags": [],
        "half_year_fact_detail": {},
        "needs_research_review": False,
        "has_fresh_facts": False,
        "fact_period": None,
        "fact_data_status": "",
        "thesis_check_status": "",
        "status": STATUS_NO_FACTS,
        "downgrade_reasons": [],
        "research_priority_hint": "",
        "errors": [],
    }


def _coerce_facts(facts_result: Optional[Any]) -> Dict[str, Any]:
    """把 HY-003 结果对象/dict/None 归一为 dict。"""
    if facts_result is None:
        return {}
    if isinstance(facts_result, dict):
        return dict(facts_result)
    # duck-typing：dataclass-like 对象。
    out: Dict[str, Any] = {}
    for key in (
        "status", "pages", "risks", "summary", "latest_period",
        "data_status", "errors", "symbol", "name",
    ):
        val = getattr(facts_result, key, None)
        if val is not None:
            out[key] = val
    return out


def _coerce_thesis(thesis_result: Optional[Any]) -> Dict[str, Any]:
    """把 HY-005 结果对象/dict/None 归一为 dict。"""
    if thesis_result is None:
        return {}
    if isinstance(thesis_result, dict):
        return dict(thesis_result)
    out: Dict[str, Any] = {}
    for key in (
        "thesis_check_status", "contradiction_flags", "weakened_flags",
        "supported_flags", "evidence_level", "needs_tree_work_review",
        "summary", "errors", "priority_reminder",
    ):
        val = getattr(thesis_result, key, None)
        if val is not None:
            out[key] = val
    return out


# data_status 值视作"可用事实"的集合（与 HY-005 _RELIABLE_FACT_STATUSES 对齐）。
_USABLE_DATA_STATUSES = frozenset({"fresh", "conflict"})


def _has_usable_facts(
    facts_status: str,
    facts_pages: Any,
    result_data_status: str = "",
) -> bool:
    """判断是否有可用（fresh / conflict）的半年报事实页。

    结果级 status 必须为 HAS_DATA，且聚合 data_status 或至少一页的
    data_status 属于 fresh / conflict。NORMAL_NO_DATA / FAILED / 仅 stale /
    仅 opinion_only 均视作无可用事实。当页面缺少 data_status 字段（dict
    duck-typing）时，回退到结果级 data_status。
    """
    if facts_status not in ("HAS_DATA", "has_data"):
        return False
    pages = facts_pages or []
    if not pages:
        return False
    for page in pages:
        ds = _page_attr(page, "data_status")
        if ds in _USABLE_DATA_STATUSES:
            return True
    # 回退：页面没有 data_status 字段时，看结果级聚合 data_status。
    if result_data_status:
        return result_data_status in _USABLE_DATA_STATUSES
    # 页面与结果级都没有 data_status：HAS_DATA 已足够，视作有事实（兼容
    # 简化 dict 输入）。
    return True


def _page_attr(page: Any, key: str) -> str:
    """从事实页对象/dict 安全取字段。"""
    if page is None:
        return ""
    if isinstance(page, dict):
        return str(page.get(key, "") or "")
    return str(getattr(page, key, "") or "")


def _compute_score(
    *,
    thesis_status: str,
    thesis_evidence_level: str,
    contradiction_count: int,
    weakened_count: int,
    supported_count: int,
    has_fresh_facts: bool,
) -> tuple:
    """计算因子分与分档原因。

    优先级：contradicted > weakened > supported > fresh_facts_only > none。
    正向分上限 ``_HALF_YEAR_SCORE_POSITIVE_CAP``，负向下限
    ``_HALF_YEAR_SCORE_NEGATIVE_FLOOR``。
    """
    if thesis_status == "contradicted":
        if thesis_evidence_level == "strong":
            return _SCORE_CONTRADICTED_STRONG, "contradicted_strong"
        return _SCORE_CONTRADICTED_MODERATE, "contradicted_moderate"
    if thesis_status == "weakened":
        if weakened_count >= 2:
            return _SCORE_WEAKENED_MULTI, "weakened_multi"
        return _SCORE_WEAKENED_SINGLE, "weakened_single"
    if thesis_status == "supported" and supported_count > 0:
        return _SCORE_SUPPORTED, "supported_capped"
    if thesis_status == "insufficient_data":
        # 事实存在但观点无法可靠反证（事实页 stale/opinion_only 或缺观点）
        # → 中性，不奖励也不惩罚。
        return _SCORE_INSUFFICIENT, "insufficient_data"
    # 有事实但无反证维度：只给极小正分提示"有新鲜事实可参考"，
    # 绝不提升为强正面。
    if has_fresh_facts:
        return _HALF_YEAR_SCORE_POSITIVE_CAP, "fresh_facts_no_thesis"
    return _SCORE_INSUFFICIENT, "no_facts"


def _build_downgrade_reasons(
    *,
    thesis_status: str,
    contradiction_flags: List[Any],
    weakened_flags: List[Any],
) -> List[str]:
    """对 contradicted / weakened 输出降权原因（不含买卖词）。"""
    reasons: List[str] = []
    if thesis_status == "contradicted" and contradiction_flags:
        sample = _extract_flag_metric(contradiction_flags[0])
        reasons.append(
            f"半年报事实打脸投资逻辑（{sample}）" if sample
            else "半年报事实打脸投资逻辑"
        )
    if thesis_status == "weakened" and weakened_flags:
        sample = _extract_flag_metric(weakened_flags[0])
        cnt = len(weakened_flags)
        reasons.append(
            f"半年报事实削弱投资逻辑（{cnt} 项{('，如 ' + sample) if sample else ''}）"
        )
    return [_clip(r, _REASON_MAX_CHARS) for r in reasons]


def _build_risk_flags(
    facts_risks: List[Any],
    contradiction_flags: List[Any],
    weakened_flags: List[Any],
) -> List[str]:
    """聚合风险标记：半年报风险 + 反证 flag metric。"""
    flags: List[str] = []
    seen: set = set()

    def _add(text: str) -> None:
        text = _clip((text or "").strip(), _REASON_MAX_CHARS)
        if text and text not in seen:
            seen.add(text)
            flags.append(text)

    for r in facts_risks:
        if isinstance(r, str):
            _add(r)
    for f in contradiction_flags:
        _add(_flag_risk_text(f, prefix="事实打脸"))
    for f in weakened_flags:
        _add(_flag_risk_text(f, prefix="事实削弱"))
    return flags[:_RISK_FLAG_MAX]


def _flag_risk_text(flag: Any, *, prefix: str) -> str:
    """从反证 flag 提取一句风险标记文本。"""
    metric = _extract_flag_metric(flag)
    reason = _flag_attr(flag, "reason") or ""
    if reason:
        return f"{prefix}：{reason}"
    if metric:
        return f"{prefix}：{metric}"
    return prefix


def _extract_flag_metric(flag: Any) -> str:
    """从反证 flag 提取指标标签（如 revenue / net_profit 的中文 label）。"""
    metric_key = _flag_attr(flag, "metric_key")
    label_map = {
        "revenue": "营收",
        "net_profit": "净利润",
        "gross_margin": "毛利率",
        "operating_cash_flow": "经营现金流",
        "segment": "分业务",
        "risk": "风险",
        "other": "其它",
    }
    return label_map.get(metric_key or "", metric_key or "")


def _flag_attr(flag: Any, key: str) -> str:
    if isinstance(flag, dict):
        return str(flag.get(key, "") or "")
    return str(getattr(flag, key, "") or "")


def _is_mandate_candidate(candidate_type: str, mandate_topic: str) -> bool:
    """判断是否属于昊天左侧池（与 KB-004 needs_tree_work_research 对齐）。"""
    mandate_types = {"POLICY_AMBUSH", "POLICY_CONFIRM", "EVENT_WATCH"}
    if (candidate_type or "").strip() in mandate_types:
        return True
    return bool((mandate_topic or "").strip())


def _research_priority_hint(
    *,
    has_fresh_facts: bool,
    thesis_status: str,
    is_mandate_candidate: bool,
) -> str:
    """生成研究优先级提示（只解释，不改 action tier）。"""
    if thesis_status == "supported":
        return "半年报事实支持投资逻辑，可提高研究优先级（不改动作档位）"
    if has_fresh_facts and thesis_status in ("insufficient_data", THESIS_STATUS_NONE, ""):
        if is_mandate_candidate:
            return "有新鲜半年报事实可供研报反证，建议优先复核"
        return "有新鲜半年报事实可供参考"
    return ""


def _render_summary(
    *,
    score: float,
    score_reason: str,
    has_fresh_facts: bool,
    latest_period: Any,
    data_status: str,
    thesis_status: str,
    contradiction_count: int,
    weakened_count: int,
    supported_count: int,
    facts_summary_list: List[Any],
    thesis_summary_list: List[Any],
    overall_status: str,
) -> str:
    """渲染一句话可读摘要（含负面信息，不含买卖词）。"""
    if overall_status == STATUS_FAILED:
        return "半年报事实查询失败，不参与因子评分。"

    if not has_fresh_facts and thesis_status in (
        "", THESIS_STATUS_NONE, "insufficient_data",
    ):
        if overall_status == STATUS_NO_FACTS:
            return "暂无可用半年报事实，仅标记研究缺口，不施加降权。"
        return ""

    parts: List[str] = []
    if latest_period:
        parts.append(f"报告期 {latest_period}")
    if thesis_status == "contradicted":
        parts.append(f"事实打脸 {contradiction_count} 项")
    elif thesis_status == "weakened":
        parts.append(f"事实削弱 {weakened_count} 项")
    elif thesis_status == "supported":
        parts.append(f"事实支持 {supported_count} 项")
    elif has_fresh_facts:
        parts.append("有新鲜事实但未做观点反证")

    parts.append(f"因子分 {score:.2f}")

    # 追加事实/反证摘要的第一条（如有）。
    extra = ""
    for src in (thesis_summary_list, facts_summary_list):
        for item in src:
            text = _clip(str(item or "").strip(), _SUMMARY_MAX_CHARS)
            if text:
                extra = text
                break
        if extra:
            break

    line = "；".join(parts) + "。"
    if extra:
        line += " " + extra
    return _clip(line, _SUMMARY_MAX_CHARS)


def _clip(text: str, max_chars: int) -> str:
    text = (text or "").strip()
    if len(text) > max_chars:
        return text[:max_chars].rstrip() + "…"
    return text


def has_forbidden_action_words(payload: Dict[str, Any]) -> List[str]:
    """[HY-006] 检查产出字典中是否包含强动作词（防回归辅助）。"""
    hits: List[str] = []
    for field_name in ("half_year_fact_summary", "research_priority_hint"):
        text = str(payload.get(field_name, "") or "").lower()
        for word in _FORBIDDEN_ACTION_WORDS:
            if word.lower() in text:
                hits.append(f"{field_name}:{word}")
    for flag in payload.get("half_year_risk_flags", []) or []:
        text = str(flag).lower()
        for word in _FORBIDDEN_ACTION_WORDS:
            if word.lower() in text:
                hits.append(f"half_year_risk_flags:{word}")
    for reason in payload.get("downgrade_reasons", []) or []:
        text = str(reason).lower()
        for word in _FORBIDDEN_ACTION_WORDS:
            if word.lower() in text:
                hits.append(f"downgrade_reasons:{word}")
    return hits
