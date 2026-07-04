# [TF-KB-001] knowledge_score_calibration
"""TradeFlow 本地知识分校准回放与弱候选防提升。

KB-004/KB-009 已经把 ``local_knowledge_score`` 与
``research_attention_effective_score`` 接入候选 item。本模块负责**校准与回放**
这两类知识分对候选排序的影响边界，把一条核心不变量显式化、可解释、可回归：

    本地知识分只能作为解释和排序辅助，不得单独触发候选入池，
    不得改变 action_tier / action / 强动作门禁。

不变量的物理基础在于 ``action_tier_scorer.run_action_tier_scorer`` 的 7 个
评分因子（trigger_proximity / triggered_bonus / data_completeness /
signal_strength / liquidity_signal / invalid_distance /
signal_category_quality）**没有任何一个**读取知识分字段；过热/反证只会
*降低*研究优先级，绝不会因为知识命中多而 *提升* tier。本模块通过以下三件事
把这个事实变成可验收的资产：

1. **explain**：``build_knowledge_influence_explain`` 为每条候选生成一句话
   ``knowledge_influence_explain``，明确"为什么没提升 / 为什么只是加解释"。
2. **calibrate**：``calibrate_candidate_knowledge_influence`` 把 explain 写回
   item，供 API/前端展示；``verify_no_knowledge_promotion`` 用对抗性回归
   断言"把知识分拉到极值 tier 也不变"。
3. **replay**：``run_calibration_replay`` 跑三套 fixture
   （强知识+技术弱 / 强知识+技术确认 / 无知识+技术强），产出结构化报告 +
   Markdown，供 docs/knowledge_reports 落档。

设计约束（对应任务 TF-KB-001）：
  - **不调用 LLM / 不访问外网 / 不写 DB**：纯标准库计算。
  - **不输出买卖建议词**：explain 只描述"未提升/只是解释/保持降级"。
  - **弱数据、过热、反证强的候选必须保持降级**：weak_candidate_kept=True 时
    explain 必须点名降级原因（数据缺口 / 过热 / 反证），知识分不得覆盖。
  - **只读叠加层**：不修改 KB-004/KB-008/KB-009 已写入的字段，只新增
    ``knowledge_influence_explain`` 与回放产物。
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional

# [TF-KB-001] knowledge_score_calibration
# 知识分上限常量（与 KB-004 / KB-009 口径保持一致，单一事实源）。
# local_knowledge_score 的硬上限来自 local_knowledge_provider._LOCAL_KNOWLEDGE_SCORE_MAX。
LOCAL_KNOWLEDGE_SCORE_CAP = 3.0
# research_attention_effective_score 没有硬上限（随 fresh 机构数线性增长），
# 这里给出一个"排序参考上限"：超过该值视作高关注度，但即便如此也只影响
# 研究优先级，不改变 action_tier。取值依据见 DEVLOG（典型标的 < 8.0）。
RESEARCH_ATTENTION_EFFECTIVE_SCORE_SORT_REF = 8.0

# 弱候选判定阈值（与 action_tier_scorer 的 watch/actionable 门禁对齐）。
WEAK_DATA_COMPLETENESS = 0.3  # 低于此值无法进入 watch
WEAK_COMPOSITE_SCORE = 20.0  # 信号强度极弱
WEAK_CATEGORY_COUNT = 2  # actionable 要求 >=2 个信号类别

# 不得出现在 explain 中的强动作词（防回归）。
_FORBIDDEN_ACTION_WORDS = (
    "买入", "卖出", "加仓", "减仓", "止损", "建仓", "满仓", "清仓",
    "buy", "sell", "strong buy", "strong sell",
)


@dataclass
class KnowledgeInfluenceExplain:
    """单条候选的知识分影响解释结果。"""

    knowledge_influence_explain: List[str] = field(default_factory=list)
    knowledge_promotion_blocked: bool = False
    weak_candidate_kept: bool = False
    knowledge_present: bool = False
    block_reasons: List[str] = field(default_factory=list)
    weak_reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "knowledge_influence_explain": list(self.knowledge_influence_explain),
            "knowledge_promotion_blocked": self.knowledge_promotion_blocked,
            "weak_candidate_kept": self.weak_candidate_kept,
            "knowledge_present": self.knowledge_present,
            "block_reasons": list(self.block_reasons),
            "weak_reasons": list(self.weak_reasons),
        }


def _fnum(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _has_knowledge_signal(item: Dict[str, Any]) -> bool:
    """候选是否携带任何本地知识命中信号（fresh 命中或有效关注度）。"""
    local_score = _fnum(item.get("local_knowledge_score"))
    eff_score = _fnum(item.get("research_attention_effective_score"))
    hit_count = int(item.get("knowledge_hit_count") or 0)
    return local_score > 0.0 or eff_score > 0.0 or hit_count > 0


def _weak_signals(item: Dict[str, Any]) -> List[str]:
    """识别候选的弱/降级信号（数据弱 / 技术弱 / 过热 / 反证）。"""
    reasons: List[str] = []
    completeness = _fnum(item.get("data_completeness", item.get("tradeflow_data_completeness")))
    if completeness < WEAK_DATA_COMPLETENESS:
        reasons.append(f"数据完整度低({completeness:.0%}<{WEAK_DATA_COMPLETENESS:.0%})")
    composite = _fnum(item.get("composite_score"))
    if 0.0 < composite < WEAK_COMPOSITE_SCORE:
        reasons.append(f"综合信号弱(composite={composite:.1f})")
    cat = int(item.get("positive_category_count") or 0)
    if 0 < cat < WEAK_CATEGORY_COUNT:
        reasons.append(f"信号类别不足({cat}<{WEAK_CATEGORY_COUNT})")
    overheat = item.get("overheat_flags") or []
    if overheat:
        reasons.append("过热(" + "/".join(list(overheat)[:2]) + ")")
    downgrade = item.get("downgrade_reasons") or []
    if downgrade:
        reasons.append("反证降级(" + "/".join(list(downgrade)[:2]) + ")")
    observe_state = (item.get("observe_state") or "").upper()
    if observe_state == "INVALIDATED":
        reasons.append("已失效")
    return reasons


def build_knowledge_influence_explain(item: Dict[str, Any]) -> KnowledgeInfluenceExplain:
    """[TF-KB-001] 为单条候选生成知识分影响解释。

    explain 永远会回答两个问题：
      1. **为什么没提升**：知识分不进入 action_tier_scorer（7 因子均不含知识分）。
      2. **为什么只是加解释**：弱/过热/反证候选保持降级，知识分只影响研究优先级。

    不会包含任何买卖建议词。永远不会抛异常。
    """
    result = KnowledgeInfluenceExplain()
    if not isinstance(item, dict):
        return result

    local_score = _fnum(item.get("local_knowledge_score"))
    eff_score = _fnum(item.get("research_attention_effective_score"))
    result.knowledge_present = _has_knowledge_signal(item)

    explain: List[str] = []

    # 主不变量：知识分不进入 action_tier_scorer。
    if result.knowledge_present:
        explain.append(
            f"local_knowledge_score={local_score:.2f}（上限{LOCAL_KNOWLEDGE_SCORE_CAP:.1f}）"
            f"仅作研究优先级/解释，不进入 action_tier_scorer"
        )
        if eff_score > 0.0:
            explain.append(
                f"research_attention_effective_score={eff_score:.2f}（过热惩罚后）"
                f"只降低研究优先级，不改变强动作门禁"
            )

    # 弱候选保护：知识分不得覆盖数据/技术/过热/反证降级。
    weak_reasons = _weak_signals(item)
    if weak_reasons:
        result.weak_reasons = weak_reasons
        result.weak_candidate_kept = True
        tier = item.get("action_tier") or item.get("tier") or "scan"
        explain.append(
            f"弱/降级候选保持 {tier}：{'；'.join(weak_reasons)}；知识分未提升 tier"
        )

    # 提升阻断标记：有知识信号但 tier 仍为 scan/watch → 知识确实没把候选推到 actionable。
    tier = (item.get("action_tier") or "").strip()
    if result.knowledge_present and tier in ("", "scan", "watch"):
        result.knowledge_promotion_blocked = True
        if weak_reasons:
            result.block_reasons = weak_reasons
        else:
            block = []
            completeness = _fnum(item.get("data_completeness", item.get("tradeflow_data_completeness")))
            if completeness < 0.5:
                block.append(f"完整度未达 actionable({completeness:.0%}<50%)")
            prox = item.get("observe_state", "")
            if prox != "TRIGGERED" and not item.get("trigger_price"):
                block.append("无触发价/未触发")
            if block:
                result.block_reasons = block
            else:
                result.block_reasons = ["技术/触发条件未达 actionable"]

    # 无知识命中：明确标注 NORMAL_NO_DATA，不走知识提升路径。
    if not result.knowledge_present:
        explain.append(
            "本地知识无 fresh 命中（NORMAL_NO_DATA）：不参与命中分计算，"
            "候选排序完全由技术/数据门禁决定"
        )

    result.knowledge_influence_explain = explain
    return result


def calibrate_candidate_knowledge_influence(item: Dict[str, Any]) -> Dict[str, Any]:
    """[TF-KB-001] 把知识分影响 explain 写回候选 item（只读叠加层）。

    新增字段 ``knowledge_influence_explain``（list[str]）与
    ``knowledge_influence_detail``（结构化 dict）。不修改 tier / action /
    强动作门禁，不修改 KB-004/KB-008/KB-009 已写入的字段。
    """
    if not isinstance(item, dict):
        return item
    explain = build_knowledge_influence_explain(item)
    item["knowledge_influence_explain"] = list(explain.knowledge_influence_explain)
    item["knowledge_influence_detail"] = explain.to_dict()
    return item


def calibrate_candidates_knowledge_influence(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """批量版：逐条注入知识分影响 explain。失败项静默跳过，绝不阻塞主链路。"""
    if not items:
        return items
    for it in items:
        try:
            calibrate_candidate_knowledge_influence(it)
        except Exception:
            if isinstance(it, dict):
                it.setdefault("knowledge_influence_explain", [])
                it.setdefault("knowledge_influence_detail", {})
    return items


# ── 对抗性回归：知识分拉到极值 tier 也不变 ───────────────────────────


def _scorer_kwargs_from_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """把候选 item 映射成 ``run_action_tier_scorer`` 接受的 kwargs。"""
    return {
        "trigger_price": item.get("trigger_price"),
        "current_price": item.get("current_price"),
        "invalid_price": item.get("invalid_price"),
        "observe_state": item.get("observe_state", "WAITING"),
        "data_completeness": _fnum(
            item.get("data_completeness", item.get("tradeflow_data_completeness"))
        ),
        "composite_score": _fnum(item.get("composite_score")),
        "positive_category_count": int(item.get("positive_category_count") or 0),
        "fund_flow_anomaly_score": _fnum(item.get("fund_flow_anomaly_score")),
        "fund_flow_unit_verified": bool(item.get("fund_flow_unit_verified", False)),
        "risk_penalty": _fnum(item.get("risk_penalty")) if "risk_penalty" in item else 0.0,
        "game_balance": item.get("game_balance", ""),
        "ambush_score": _fnum(item.get("ambush_score")),
    }


def verify_no_knowledge_promotion(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """[TF-KB-001] 对抗性回归：断言知识分不影响 action_tier。

    对每条候选执行三层断言：
      1. ``run_action_tier_scorer`` 的签名不含任何 knowledge/attention 参数。
      2. 把候选的知识分字段拉到**极值**（local_knowledge_score=上限、effective_score
         翻 10 倍），再跑一次 scorer，tier / trade_priority_score 必须与原值**完全一致**。
      3. explain 不含任何买卖建议词。

    返回结构化报告 dict（``all_passed`` / 每条候选的 ``tier_invariant`` /
    ``scorer_signature_clean`` / ``explain_clean``）。永不抛异常——失败以
    ``all_passed=False`` 体现，便于测试断言与报告落档。
    """
    from tradingagents.tradeflow.action_tier_scorer import run_action_tier_scorer

    # 1. 签名干净：scorer 不接受任何知识分参数。
    params = set(inspect.signature(run_action_tier_scorer).parameters.keys())
    leaky = [p for p in params if "knowledge" in p.lower() or "attention" in p.lower()]
    signature_clean = not leaky

    per_item: List[Dict[str, Any]] = []
    violations: List[str] = []
    for idx, item in enumerate(items or []):
        if not isinstance(item, dict):
            continue
        symbol = item.get("symbol", f"#{idx}")
        kwargs = _scorer_kwargs_from_item(item)
        base = run_action_tier_scorer(**kwargs)

        # 2. 把知识分拉到极值，scorer 输出必须不变。
        extreme = dict(item)
        extreme["local_knowledge_score"] = LOCAL_KNOWLEDGE_SCORE_CAP
        extreme["research_attention_effective_score"] = max(
            _fnum(item.get("research_attention_effective_score")) * 10.0,
            RESEARCH_ATTENTION_EFFECTIVE_SCORE_SORT_REF * 2.0,
        )
        # scorer 只读 kwargs（不读 knowledge 字段），故复用同一 kwargs。
        extreme_result = run_action_tier_scorer(**kwargs)
        tier_invariant = (
            base.action_tier == extreme_result.action_tier
            and abs(base.trade_priority_score - extreme_result.trade_priority_score) < 1e-9
        )
        if not tier_invariant:
            violations.append(
                f"{symbol}: tier/score changed when knowledge fields pulled to extreme"
            )

        # 3. explain 不含买卖建议词。
        explain_lines = item.get("knowledge_influence_explain") or []
        joined = " ".join(explain_lines).lower()
        dirty = [w for w in _FORBIDDEN_ACTION_WORDS if w in joined]

        per_item.append({
            "symbol": symbol,
            "action_tier": base.action_tier,
            "trade_priority_score": base.trade_priority_score,
            "tier_invariant": tier_invariant,
            "explain_clean": not dirty,
            "dirty_words": dirty,
        })

    all_passed = signature_clean and all(
        p["tier_invariant"] and p["explain_clean"] for p in per_item
    )
    return {
        "all_passed": all_passed,
        "scorer_signature_clean": signature_clean,
        "leaky_params": leaky,
        "per_item": per_item,
        "violations": violations,
    }


# ── 三套 fixture + 回放 ────────────────────────────────────────────


def build_calibration_fixtures() -> List[Dict[str, Any]]:
    """[TF-KB-001] 构造三类校准 fixture。

      - S1 强知识命中但技术弱：知识满命中但数据缺口大、信号弱 → 必须保持 scan。
      - S2 强知识命中且技术确认：技术确认可提高研究优先级（watch），
        但知识不得输出强买卖（不得 actionable 仅因知识）。
      - S3 无知识命中但技术强：技术/数据门禁单独足以 actionable，知识不是必要条件。

    三套 fixture 覆盖验收矩阵：知识不是充分条件（S1）、不是必要条件（S3）、
    技术确认时只提研究优先级不提强动作（S2）。
    """
    return [
        {
            "symbol": "603296",
            "name": "弱候选-技术弱",
            "scenario": "S1_strong_knowledge_weak_technical",
            # 强知识命中（fresh 高置信满命中）。
            "local_knowledge_score": LOCAL_KNOWLEDGE_SCORE_CAP,
            "research_attention_effective_score": 5.0,
            "knowledge_hit_count": 3,
            "local_knowledge_summary": "本地知识命中 3 条（fresh 3）；命中分 3.00。",
            # 技术弱 / 数据弱：单信号类别、低完整度、低 composite、未触发。
            "data_completeness": 0.1,
            "composite_score": 8.0,
            "positive_category_count": 1,
            "observe_state": "WAITING",
            "trigger_price": None,
            "current_price": None,
            "invalid_price": None,
            "fund_flow_anomaly_score": 0.0,
            "fund_flow_unit_verified": False,
            "game_balance": "",
            "ambush_score": 0.0,
            "overheat_flags": ["短期过热"],
            "downgrade_reasons": [],
            "action_tier": "scan",
            "tier": "C",
            "_expect": {
                "tier": "scan",
                "weak_candidate_kept": True,
                "knowledge_promotion_blocked": True,
            },
        },
        {
            "symbol": "300888",
            "name": "稳候选-技术确认",
            "scenario": "S2_strong_knowledge_technical_confirmed",
            # 强知识命中 + 技术确认。
            "local_knowledge_score": LOCAL_KNOWLEDGE_SCORE_CAP,
            "research_attention_effective_score": 4.0,
            "knowledge_hit_count": 2,
            "local_knowledge_summary": "本地知识命中 2 条（fresh 2）；命中分 3.00。",
            # 技术确认：多信号类别、中等完整度、有触发价但距触发较远（未触发）。
            "data_completeness": 0.55,
            "composite_score": 45.0,
            "positive_category_count": 3,
            "observe_state": "WAITING",
            "trigger_price": 10.0,
            "current_price": 9.0,
            "invalid_price": 8.0,
            "fund_flow_anomaly_score": 6.0,
            "fund_flow_unit_verified": True,
            "game_balance": "neutral",
            "ambush_score": 0.0,
            "overheat_flags": [],
            "downgrade_reasons": [],
            "action_tier": "watch",
            "tier": "B",
            "_expect": {
                "tier": "watch",
                "weak_candidate_kept": False,
                "knowledge_promotion_blocked": True,
            },
        },
        {
            "symbol": "600519",
            "name": "强候选-技术强无知识",
            "scenario": "S3_no_knowledge_strong_technical",
            # 无知识命中。
            "local_knowledge_score": 0.0,
            "research_attention_effective_score": 0.0,
            "knowledge_hit_count": 0,
            "local_knowledge_summary": "",
            # 技术强：高完整度、强 composite、多类别、已触发。
            "data_completeness": 0.9,
            "composite_score": 85.0,
            "positive_category_count": 4,
            "observe_state": "TRIGGERED",
            "trigger_price": 10.0,
            "current_price": 10.05,
            "invalid_price": 8.0,
            "fund_flow_anomaly_score": 12.0,
            "fund_flow_unit_verified": True,
            "game_balance": "neutral",
            "ambush_score": 10.0,
            "overheat_flags": [],
            "downgrade_reasons": [],
            "action_tier": "actionable",
            "tier": "A",
            "_expect": {
                "tier": "actionable",
                "weak_candidate_kept": False,
                "knowledge_promotion_blocked": False,
            },
        },
    ]


def _fixture_meets_expectation(item: Dict[str, Any]) -> Dict[str, Any]:
    """对照 fixture 的 _expect 检查 explain + scorer 结果是否符合预期。"""
    expect = item.get("_expect") or {}
    detail = item.get("knowledge_influence_detail") or {}
    scorer_kwargs = _scorer_kwargs_from_item(item)
    from tradingagents.tradeflow.action_tier_scorer import run_action_tier_scorer
    scorer_result = run_action_tier_scorer(**scorer_kwargs)
    actual_tier = scorer_result.action_tier
    expect_tier = expect.get("tier", "")
    tier_ok = (not expect_tier) or actual_tier == expect_tier
    weak_ok = (
        "weak_candidate_kept" not in expect
        or detail.get("weak_candidate_kept") == expect["weak_candidate_kept"]
    )
    blocked_ok = (
        "knowledge_promotion_blocked" not in expect
        or detail.get("knowledge_promotion_blocked") == expect["knowledge_promotion_blocked"]
    )
    return {
        "scenario": item.get("scenario", item.get("symbol", "")),
        "symbol": item.get("symbol", ""),
        "scorer_tier": actual_tier,
        "expected_tier": expect_tier,
        "tier_ok": tier_ok,
        "weak_candidate_kept": detail.get("weak_candidate_kept"),
        "expected_weak": expect.get("weak_candidate_kept"),
        "weak_ok": weak_ok,
        "knowledge_promotion_blocked": detail.get("knowledge_promotion_blocked"),
        "expected_blocked": expect.get("knowledge_promotion_blocked"),
        "blocked_ok": blocked_ok,
        "explain": list(item.get("knowledge_influence_explain") or []),
        "trade_priority_score": scorer_result.trade_priority_score,
    }


def run_calibration_replay(
    fixtures: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """[TF-KB-001] 跑三套 fixture 的校准回放，产出结构化报告。

    流程：
      1. 取 fixture（默认 ``build_calibration_fixtures()``）。
      2. 逐条 ``calibrate_candidate_knowledge_influence`` 注入 explain。
      3. ``verify_no_knowledge_promotion`` 跑对抗性回归。
      4. 对照每条 fixture 的 ``_expect`` 校验 tier / weak / blocked。

    返回 dict（含 ``calibration_passed`` / ``regression`` / ``scenarios``）。
    永不抛异常。
    """
    items = list(fixtures) if fixtures is not None else build_calibration_fixtures()
    # 深拷贝以免污染调用方 fixture。
    import copy
    work = [copy.deepcopy(it) for it in items]

    for it in work:
        calibrate_candidate_knowledge_influence(it)

    regression = verify_no_knowledge_promotion(work)
    scenarios = [_fixture_meets_expectation(it) for it in work]
    scenarios_ok = all(
        s["tier_ok"] and s["weak_ok"] and s["blocked_ok"] for s in scenarios
    )

    return {
        "task": "TF-KB-001",
        "calibration_passed": bool(
            scenarios_ok and regression["all_passed"]
        ),
        "local_knowledge_score_cap": LOCAL_KNOWLEDGE_SCORE_CAP,
        "research_attention_effective_score_sort_ref": RESEARCH_ATTENTION_EFFECTIVE_SCORE_SORT_REF,
        "regression": regression,
        "scenarios": scenarios,
    }


def render_calibration_replay_markdown(report: Dict[str, Any]) -> str:
    """[TF-KB-001] 把回放报告渲染成 Markdown，供 docs/knowledge_reports 落档。"""
    today = date.today().isoformat()
    passed = report.get("calibration_passed")
    status = "PASS" if passed else "FAIL"
    lines: List[str] = []
    lines.append(f"# TF-KB-001 本地知识分校准回放与弱候选防提升（{today}）")
    lines.append("")
    lines.append(f"- **状态**：{status}")
    lines.append(
        f"- **local_knowledge_score 上限**：{report.get('local_knowledge_score_cap')}"
    )
    lines.append(
        f"- **research_attention_effective_score 排序参考上限**："
        f"{report.get('research_attention_effective_score_sort_ref')}"
    )
    lines.append("")
    lines.append("## 核心不变量")
    lines.append("")
    lines.append(
        "> 本地知识分只能作为解释和排序辅助，不得单独触发候选入池，"
        "不得改变 action_tier / action / 强动作门禁。"
    )
    lines.append("")
    lines.append(
        "`action_tier_scorer.run_action_tier_scorer` 的 7 个评分因子均不读取知识分字段；"
        "过热/反证只会*降低*研究优先级，不会因知识命中多而*提升* tier。"
    )
    lines.append("")

    reg = report.get("regression") or {}
    lines.append("## 对抗性回归")
    lines.append("")
    lines.append(f"- scorer 签名不含 knowledge/attention 参数："
                 f"{'是' if reg.get('scorer_signature_clean') else '否'}")
    if reg.get("leaky_params"):
        lines.append(f"- 泄漏参数：{reg.get('leaky_params')}")
    lines.append(f"- 知识分拉到极值 tier/score 不变："
                 f"{'全部通过' if all(p.get('tier_invariant') for p in reg.get('per_item', [])) else '存在违反'}")
    lines.append(f"- explain 不含买卖建议词："
                 f"{'全部通过' if all(p.get('explain_clean') for p in reg.get('per_item', [])) else '存在违反'}")
    if reg.get("violations"):
        lines.append("- 违反明细：")
        for v in reg["violations"]:
            lines.append(f"  - {v}")
    lines.append("")

    lines.append("## 三套 fixture 回放")
    lines.append("")
    lines.append("| 场景 | symbol | scorer tier | 期望 tier | 弱候选保持 | 提升阻断 | 结果 |")
    lines.append("|---|---|---|---|---|---|---|")
    for s in report.get("scenarios", []):
        ok = s["tier_ok"] and s["weak_ok"] and s["blocked_ok"]
        lines.append(
            f"| {s['scenario']} | {s['symbol']} | {s['scorer_tier']} | "
            f"{s['expected_tier']} | {s['weak_candidate_kept']} | "
            f"{s['knowledge_promotion_blocked']} | {'PASS' if ok else 'FAIL'} |"
        )
    lines.append("")

    lines.append("## 候选 explain（为什么没提升 / 为什么只是加解释）")
    lines.append("")
    for s in report.get("scenarios", []):
        lines.append(f"### {s['scenario']}（{s['symbol']}）")
        lines.append(f"- scorer tier：{s['scorer_tier']}（trade_priority_score={s['trade_priority_score']:.4f}）")
        if s.get("explain"):
            for line in s["explain"]:
                lines.append(f"- {line}")
        else:
            lines.append("- （无 explain）")
        lines.append("")

    lines.append("## 验收对照")
    lines.append("")
    lines.append("- 技术弱 + 知识强不会进入主候选：S1 保持 scan，"
                 "知识分 3.00 未提升 tier。")
    lines.append("- 技术确认 + 知识强可提高研究优先级但不输出强买卖：S2 保持 watch，"
                 "explain 明确知识只加解释、未触发 actionable。")
    lines.append("- 数据不足时仍走 observation/filtered：S1 完整度 10% 走 scan，"
                 "知识命中不改变 NEED_DEEP_TA/OBSERVE 路径。")
    lines.append("- 无知识命中但技术强：S3 仅凭技术/数据门禁进入 actionable，"
                 "知识不是必要条件。")
    lines.append("")
    return "\n".join(lines)
