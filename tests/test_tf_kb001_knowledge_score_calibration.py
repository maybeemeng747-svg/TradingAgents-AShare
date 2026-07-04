# [TF-KB-001] knowledge_score_calibration
"""Tests for TradeFlow 本地知识分校准回放与弱候选防提升 (TF-KB-001).

覆盖：
  - ``build_knowledge_influence_explain``：三类场景（强知识+技术弱 /
    强知识+技术确认 / 无知识+技术强）的 explain 文案与标记。
  - ``calibrate_candidate_knowledge_influence``：写回
    ``knowledge_influence_explain`` / ``knowledge_influence_detail``，
    不改 tier / action / 强动作门禁。
  - ``verify_no_knowledge_promotion``：对抗性回归——scorer 签名干净、
    知识分拉到极值 tier/score 不变、explain 无买卖建议词。
  - ``run_calibration_replay``：三套 fixture 的端到端校验。
  - TradeFlow service ``_apply_knowledge_calibration_explain``：失败安全。
  - explain 不含买卖建议词（防回归）。
  - Pydantic schema 包含新增字段。
"""

from __future__ import annotations

import copy
from typing import Any, Dict

import pytest

from tradingagents.tradeflow.knowledge_score_calibration import (
    LOCAL_KNOWLEDGE_SCORE_CAP,
    RESEARCH_ATTENTION_EFFECTIVE_SCORE_SORT_REF,
    WEAK_CATEGORY_COUNT,
    WEAK_DATA_COMPLETENESS,
    build_calibration_fixtures,
    build_knowledge_influence_explain,
    calibrate_candidate_knowledge_influence,
    calibrate_candidates_knowledge_influence,
    render_calibration_replay_markdown,
    run_calibration_replay,
    verify_no_knowledge_promotion,
)
from tradingagents.tradeflow.action_tier_scorer import run_action_tier_scorer


# ── 强约束：常量口径 ──────────────────────────────────────────────


def test_caps_match_kb004_and_are_documented():
    """local_knowledge_score 上限必须与 KB-004 的 _LOCAL_KNOWLEDGE_SCORE_MAX 一致。"""
    from tradingagents.dataflows.local_knowledge_provider import (
        _LOCAL_KNOWLEDGE_SCORE_MAX,
    )
    assert LOCAL_KNOWLEDGE_SCORE_CAP == _LOCAL_KNOWLEDGE_SCORE_MAX == 3.0
    assert RESEARCH_ATTENTION_EFFECTIVE_SCORE_SORT_REF > 0.0
    assert WEAK_DATA_COMPLETENESS == 0.3
    assert WEAK_CATEGORY_COUNT == 2


# ── build_knowledge_influence_explain ─────────────────────────────


def test_explain_strong_knowledge_weak_technical_blocks_promotion():
    """S1：强知识命中但技术弱 → 提升阻断、弱候选保持、explain 点名降级原因。"""
    item = {
        "symbol": "603296",
        "local_knowledge_score": 3.0,
        "research_attention_effective_score": 5.0,
        "knowledge_hit_count": 3,
        "data_completeness": 0.1,
        "composite_score": 8.0,
        "positive_category_count": 1,
        "overheat_flags": ["短期过热"],
        "action_tier": "scan",
    }
    result = build_knowledge_influence_explain(item)
    assert result.knowledge_present is True
    assert result.knowledge_promotion_blocked is True
    assert result.weak_candidate_kept is True
    # explain 必须同时回答"不进入 scorer"和"弱候选保持降级"。
    joined = " ".join(result.knowledge_influence_explain)
    assert "不进入 action_tier_scorer" in joined
    assert "弱/降级候选保持 scan" in joined
    assert "数据完整度低" in joined
    assert "过热" in joined


def test_explain_strong_knowledge_technical_confirmed_still_blocks_strong_action():
    """S2：强知识 + 技术确认 → 提研究优先级，但 tier=watch 时仍标记提升阻断。"""
    item = {
        "symbol": "300888",
        "local_knowledge_score": 3.0,
        "research_attention_effective_score": 4.0,
        "knowledge_hit_count": 2,
        "data_completeness": 0.55,
        "composite_score": 45.0,
        "positive_category_count": 3,
        "action_tier": "watch",
    }
    result = build_knowledge_influence_explain(item)
    assert result.knowledge_present is True
    # watch（非 actionable）→ 知识没有把它推成强动作 → 仍记阻断。
    assert result.knowledge_promotion_blocked is True
    # 技术确认 + 无弱信号 → 不应标记弱候选。
    assert result.weak_candidate_kept is False


def test_explain_no_knowledge_strong_technical_not_blocked():
    """S3：无知识命中 + 技术强 → 不走知识路径，knowledge_present=False。"""
    item = {
        "symbol": "600519",
        "local_knowledge_score": 0.0,
        "research_attention_effective_score": 0.0,
        "knowledge_hit_count": 0,
        "data_completeness": 0.9,
        "composite_score": 85.0,
        "positive_category_count": 4,
        "action_tier": "actionable",
    }
    result = build_knowledge_influence_explain(item)
    assert result.knowledge_present is False
    assert result.knowledge_promotion_blocked is False
    joined = " ".join(result.knowledge_influence_explain)
    assert "NORMAL_NO_DATA" in joined
    assert "候选排序完全由技术/数据门禁决定" in joined


def test_explain_invalidated_candidate_kept_downgraded():
    """已失效候选必须保持降级，知识分不得覆盖。"""
    item = {
        "symbol": "000001",
        "local_knowledge_score": 3.0,
        "research_attention_effective_score": 4.0,
        "knowledge_hit_count": 2,
        "observe_state": "INVALIDATED",
        "data_completeness": 0.9,
        "composite_score": 80.0,
        "positive_category_count": 4,
        "action_tier": "scan",
    }
    result = build_knowledge_influence_explain(item)
    assert result.weak_candidate_kept is True
    assert "已失效" in result.weak_reasons


def test_explain_never_contains_buy_sell_words():
    """explain 不得包含任何买卖建议词（防回归）。"""
    fixtures = build_calibration_fixtures()
    for item in fixtures:
        calibrate_candidate_knowledge_influence(item)
        result = build_knowledge_influence_explain(item)
        joined = " ".join(result.knowledge_influence_explain).lower()
        forbidden = (
            "买入", "卖出", "加仓", "减仓", "止损", "建仓", "满仓", "清仓",
            "buy", "sell", "strong buy", "strong sell",
        )
        for word in forbidden:
            assert word not in joined, f"explain 含禁词 {word}: {joined}"


def test_explain_handles_non_dict_and_empty():
    """非法输入不得抛异常。"""
    assert build_knowledge_influence_explain(None).knowledge_present is False  # type: ignore[arg-type]
    assert build_knowledge_influence_explain({}).knowledge_present is False
    assert build_knowledge_influence_explain({"symbol": "X"}).knowledge_present is False


# ── calibrate_candidate_knowledge_influence ───────────────────────


def test_calibrate_writes_back_fields_without_changing_tier():
    """写回 explain/detail，但 tier / action 字段保持不变。"""
    item = {
        "symbol": "603296",
        "local_knowledge_score": 3.0,
        "data_completeness": 0.1,
        "positive_category_count": 1,
        "action_tier": "scan",
        "action": "OBSERVE",
    }
    before = copy.deepcopy(item)
    calibrate_candidate_knowledge_influence(item)
    # 新增字段。
    assert "knowledge_influence_explain" in item
    assert "knowledge_influence_detail" in item
    assert isinstance(item["knowledge_influence_explain"], list)
    # tier / action 未被修改。
    assert item["action_tier"] == before["action_tier"]
    assert item["action"] == before["action"]
    # KB-004/KB-009 字段未被篡改。
    assert item["local_knowledge_score"] == before["local_knowledge_score"]


def test_calibrate_batch_failure_safe():
    """批量校准遇到非法项不得阻塞。"""
    items: list[Dict[str, Any]] = [
        {"symbol": "A", "local_knowledge_score": 2.0, "action_tier": "watch"},
        "not-a-dict",  # type: ignore[list-item]
        {"symbol": "B", "local_knowledge_score": 0.0, "action_tier": "scan"},
    ]
    out = calibrate_candidates_knowledge_influence(items)
    # item 0 有知识命中 → explain 非空。
    assert out[0]["knowledge_influence_explain"]  # type: ignore[index]
    # item 1 非法 → 保持原样，未被处理。
    assert out[1] == "not-a-dict"  # type: ignore[index]
    # item 2 无知识命中 → explain 走 NORMAL_NO_DATA 分支，detail 标记 knowledge_present=False。
    assert out[2]["knowledge_influence_detail"]["knowledge_present"] is False  # type: ignore[index]


# ── verify_no_knowledge_promotion（对抗性回归）──────────────────


def test_verify_scorer_signature_has_no_knowledge_inputs():
    """action_tier_scorer 签名不得含任何 knowledge/attention 参数。"""
    fixtures = build_calibration_fixtures()
    for it in fixtures:
        calibrate_candidate_knowledge_influence(it)
    report = verify_no_knowledge_promotion(fixtures)
    assert report["scorer_signature_clean"] is True
    assert report["leaky_params"] == []


def test_verify_tier_invariant_to_extreme_knowledge():
    """把知识分拉到极值，scorer 的 tier/score 必须完全不变。"""
    fixtures = build_calibration_fixtures()
    for it in fixtures:
        calibrate_candidate_knowledge_influence(it)
    report = verify_no_knowledge_promotion(fixtures)
    assert report["all_passed"] is True
    for per in report["per_item"]:
        assert per["tier_invariant"] is True
        assert per["explain_clean"] is True
    assert report["violations"] == []


def test_verify_catches_synthetic_leak(monkeypatch):
    """若有人给 scorer 加了 knowledge 参数，回归必须能抓到（守门测试）。"""
    import inspect

    def fake_scorer(
        trigger_price=None, current_price=None, invalid_price=None,
        observe_state="WAITING", data_completeness=0.0, composite_score=0.0,
        positive_category_count=0, fund_flow_anomaly_score=0.0,
        fund_flow_unit_verified=False, risk_penalty=0.0, game_balance="",
        ambush_score=0.0, local_knowledge_score=0.0,  # 故意泄漏
    ):
        from tradingagents.tradeflow.action_tier_scorer import ActionTierResult
        return ActionTierResult()

    import tradingagents.tradeflow.knowledge_score_calibration as mod
    import tradingagents.tradeflow.action_tier_scorer as scorer_mod
    monkeypatch.setattr(scorer_mod, "run_action_tier_scorer", fake_scorer)
    # inspect 在被测函数内 import 的是同名符号；直接 patch 模块属性即可生效。
    report = verify_no_knowledge_promotion([{"symbol": "X"}])
    assert report["scorer_signature_clean"] is False
    assert "local_knowledge_score" in report["leaky_params"]


# ── run_calibration_replay ───────────────────────────────────────


def test_replay_three_fixtures_all_pass():
    """三套 fixture 端到端回放：tier 符合预期、回归通过、校准通过。"""
    report = run_calibration_replay()
    assert report["task"] == "TF-KB-001"
    assert report["calibration_passed"] is True
    assert report["regression"]["all_passed"] is True
    scenarios = report["scenarios"]
    assert len(scenarios) == 3
    by_scn = {s["scenario"]: s for s in scenarios}
    # S1 弱候选保持 scan。
    s1 = by_scn["S1_strong_knowledge_weak_technical"]
    assert s1["scorer_tier"] == "scan"
    assert s1["weak_candidate_kept"] is True
    assert s1["knowledge_promotion_blocked"] is True
    assert s1["tier_ok"] and s1["weak_ok"] and s1["blocked_ok"]
    # S2 技术确认 → watch（非 actionable），知识只提研究优先级。
    s2 = by_scn["S2_strong_knowledge_technical_confirmed"]
    assert s2["scorer_tier"] == "watch"
    assert s2["weak_candidate_kept"] is False
    assert s2["knowledge_promotion_blocked"] is True
    # S3 技术强 → actionable，无知识命中。
    s3 = by_scn["S3_no_knowledge_strong_technical"]
    assert s3["scorer_tier"] == "actionable"
    assert s3["knowledge_promotion_blocked"] is False


def test_replay_does_not_mutate_input_fixtures():
    """回放深拷贝 fixture，不得污染调用方。"""
    fixtures = build_calibration_fixtures()
    snapshot = copy.deepcopy(fixtures)
    run_calibration_replay(fixtures)
    assert fixtures == snapshot


def test_render_markdown_has_no_buy_sell_words_and_includes_invariant():
    """渲染出的 Markdown 不得含买卖建议词，且包含核心不变量说明。"""
    report = run_calibration_replay()
    md = render_calibration_replay_markdown(report)
    assert "TF-KB-001" in md
    assert "不得单独触发候选入池" in md or "不得改变 action_tier" in md
    forbidden = ("买入", "卖出", "strong buy", "strong sell")
    for word in forbidden:
        assert word not in md.lower()


# ── TradeFlow service 集成（失败安全 + 写回）────────────────────


def test_service_apply_calibration_explain_failure_safe():
    """_apply_knowledge_calibration_explain 空列表/异常均不阻塞。"""
    from api.services.tradeflow_service import _apply_knowledge_calibration_explain
    assert _apply_knowledge_calibration_explain([]) == []
    items = [{"symbol": "A", "local_knowledge_score": 1.0, "action_tier": "watch"}]
    out = _apply_knowledge_calibration_explain(items)
    assert out[0]["knowledge_influence_explain"]


def test_service_apply_calibration_explain_handles_missing_module(monkeypatch):
    """模块 import 失败时静默降级为空 explain。"""
    from api.services import tradeflow_service as svc

    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def boom(name, *args, **kwargs):
        if "knowledge_score_calibration" in name:
            raise ImportError("simulated")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", boom)
    items = [{"symbol": "A", "action_tier": "scan"}]
    out = svc._apply_knowledge_calibration_explain(items)
    assert out[0].get("knowledge_influence_explain", []) == []
    assert out[0].get("knowledge_influence_detail", {}) == {}


# ── Pydantic schema ──────────────────────────────────────────────


def test_schema_candidate_item_has_knowledge_influence_fields():
    """TradeFlowCandidateItem 必须暴露新增字段，默认值与 NORMAL_NO_DATA 一致。"""
    from api.tradeflow_schemas import TradeFlowCandidateItem
    item = TradeFlowCandidateItem(symbol="603296")
    assert item.knowledge_influence_explain == []
    assert item.knowledge_influence_detail == {}


def test_schema_observation_item_has_knowledge_influence_fields():
    """观察仓 schema 也应暴露新增字段。"""
    from api.tradeflow_schemas import ObservationItemResponse
    item = ObservationItemResponse(symbol="603296")
    assert item.knowledge_influence_explain == []
    assert item.knowledge_influence_detail == {}


# ── 数据不足路径仍走 observation/filtered ────────────────────────


def test_data_insufficient_candidate_stays_scan_with_full_knowledge():
    """数据完整度极低 + 知识满命中：scorer 必须仍输出 scan，action 不变。"""
    item = {
        "symbol": "999999",
        "local_knowledge_score": LOCAL_KNOWLEDGE_SCORE_CAP,
        "research_attention_effective_score": RESEARCH_ATTENTION_EFFECTIVE_SCORE_SORT_REF,
        "knowledge_hit_count": 5,
        "data_completeness": 0.0,
        "composite_score": 0.0,
        "positive_category_count": 0,
        "observe_state": "WAITING",
    }
    from tradingagents.tradeflow.knowledge_score_calibration import _scorer_kwargs_from_item
    result = run_action_tier_scorer(**_scorer_kwargs_from_item(item))
    assert result.action_tier == "scan"
    calibrate_candidate_knowledge_influence(item)
    explain = build_knowledge_influence_explain(item)
    assert explain.knowledge_promotion_blocked is True
    assert explain.weak_candidate_kept is True
