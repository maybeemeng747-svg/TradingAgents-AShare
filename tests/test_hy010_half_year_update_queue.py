# [HY-010] half_year_update_queue
"""Tests for HY-010 持仓/观察仓半年报待更新清单.

覆盖（对应 docs/TASKS.md HY-010 验收方式）：
  - **三类来源合并**：holdings / observation / mandate_candidate 都能进 universe，
    同 symbol 多来源时合并 origins / observation_state / candidate_type。
  - **重复 symbol**：跨 bucket 重复 symbol 只保留一条 entry，origins 去重保序。
  - **无知识根 / half_year_facts skipped/failed/missing**：所有 symbol 降级为
    ``missing`` 状态，但 universe 仍输出。
  - **冲突**：``has_conflict=True`` 或 ``facts_status=CONFLICT`` → status=conflict，
    持仓+冲突排在最前（priority_rank=0）。
  - **未知日期**：fresh facts 但 ``latest_disclosure_date`` 缺失 → status=unknown
    （不猜日期）。
  - **needs_digest**：thesis contradicted/weakened 或 needs_tree_work_review →
    status=needs_digest。
  - **stale**：data_status=stale 或 has_stale 或 facts_status=STALE/LOW_CONFIDENCE →
    status=stale。
  - **priority 排序**：持仓冲突 > 持仓缺失 > 持仓 needs_digest > 持仓其他 >
    观察仓接近触发 > 昊天主候选 > 观察仓其他 > 昊天观察候选。
  - **稳定空结构**：context 不是 dict / universe 全空时返回稳定空结构，不抛异常。
  - **不输出强买卖词**：reason / fact_summary 全量扫描禁词表，绝不漏出。
  - **不写 DB / 不调 LLM**：纯函数，只读 IC context dict。
  - **Markdown / JSON 输出**：to_dict / render_half_year_update_markdown 稳定可序列化。
  - **重复执行稳定**：同一 context 多次调用结果一致。
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from tradingagents.tradeflow.half_year_update_queue import (
    ALL_STATUSES,
    ORIGIN_HOLDING,
    ORIGIN_MANDATE_CANDIDATE,
    ORIGIN_OBSERVATION,
    STATUS_CONFLICT,
    STATUS_MISSING,
    STATUS_NEEDS_DIGEST,
    STATUS_STALE,
    STATUS_UNKNOWN,
    STATUS_UP_TO_DATE,
    TASK_CODE,
    HalfYearUpdateEntry,
    HalfYearUpdateQueue,
    build_half_year_update_queue,
    render_half_year_update_markdown,
    suggest_report_path,
)

# [HY-010] half_year_update_queue — task code tag.

_AS_OF = "2026-07-13 09:30:00"

_FORBIDDEN_ACTION_WORDS = (
    "买入", "卖出", "加仓", "减仓", "立即买入", "立即卖出",
    "全仓", "满仓", "清仓", "止损", "建仓", "强烈推荐",
    "BUY", "SELL", "strong buy", "strong sell",
)


def _assert_no_forbidden_words(text: str) -> None:
    for word in _FORBIDDEN_ACTION_WORDS:
        assert word not in text, (
            f"text contains forbidden word {word!r}: {text!r}"
        )


# ── helpers / fixtures ────────────────────────────────────────────────


def _hy_item(
    *,
    symbol: str,
    facts_status: str = "HAS_FACTS",
    data_status: str = "fresh",
    has_conflict: bool = False,
    has_stale: bool = False,
    latest_period: str = "2025H1",
    latest_disclosure_date: str = "2026-08-29",
    half_year_score: float = 1.0,
    fact_summary_text: str = "2025H1 营收 +30%",
    thesis_check_status: str = "",
    thesis_inline: str = "",
    needs_tree_work_review: bool = False,
    needs_research_review: bool = False,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "name": symbol,  # 测试中由 universe bucket 覆盖真实 name
        "origin": "half_year_facts",
        "facts_status": facts_status,
        "data_status": data_status,
        "has_conflict": has_conflict,
        "has_stale": has_stale,
        "latest_period": latest_period,
        "latest_disclosure_date": latest_disclosure_date,
        "half_year_score": half_year_score,
        "fact_summary_text": fact_summary_text,
        "thesis_check_status": thesis_check_status,
        "thesis_inline": thesis_inline,
        "needs_tree_work_review": needs_tree_work_review,
        "needs_research_review": needs_research_review,
        "source": "half_year_facts_provider",
        "as_of": _AS_OF,
    }


def _hy_bucket(items: list[dict[str, Any]], *, data_status: str = "fresh") -> dict[str, Any]:
    return {
        "source": "half_year_facts_context",
        "as_of": _AS_OF,
        "data_status": data_status,
        "knowledge_root": "",
        "symbol_count": len(items),
        "scanned_symbol_count": max(len(items), 1),
        "fresh_fact_count": sum(1 for i in items if i.get("data_status") == "fresh"),
        "contradicted_count": sum(
            1 for i in items if i.get("thesis_check_status") == "contradicted"
        ),
        "weakened_count": sum(
            1 for i in items if i.get("thesis_check_status") == "weakened"
        ),
        "needs_review_count": sum(
            1 for i in items
            if i.get("needs_tree_work_review") or i.get("needs_research_review")
        ),
        "items": items,
        "errors": [],
        "read_only": True,
    }


def _holding(symbol: str, name: str = "") -> dict[str, Any]:
    return {
        "symbol": symbol,
        "name": name or symbol,
        "current_position": 100.0,
        "average_cost": 10.0,
        "live_price": None,
        "floating_pnl": None,
        "floating_pnl_pct": None,
        "latest_report": None,
        "source": "imported_portfolio",
        "as_of": _AS_OF,
    }


def _observation(
    symbol: str,
    *,
    name: str = "",
    status: str = "watching",
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "name": name or symbol,
        "status": status,
        "horizon": "mid",
        "entry_low": 10.0,
        "entry_high": 11.0,
        "trigger_price": 10.5,
        "invalid_price": 9.5,
        "reason": "test observation",
        "source": "observation_warehouse",
        "as_of": _AS_OF,
    }


def _candidate(
    symbol: str,
    *,
    name: str = "",
    candidate_type: str = "POLICY_AMBUSH",
    composite_score: float = 60.0,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "name": name or symbol,
        "candidate_type": candidate_type,
        "tier": "A",
        "composite_score": composite_score,
        "primary_strategy": candidate_type,
        "strategy_tags": [],
        "trigger_price": 100.0,
        "support_price": 95.0,
        "invalid_price": 90.0,
        "need_deep_ta": True,
        "deep_ta_status": "pending",
        "action_tier": "P2",
        "reason": "test candidate",
        "source": "tradeflow_candidates",
        "as_of": _AS_OF,
    }


def _build_context(
    *,
    holdings: list[dict[str, Any]] | None = None,
    observation: list[dict[str, Any]] | None = None,
    candidates: list[dict[str, Any]] | None = None,
    mandate_main: list[dict[str, Any]] | None = None,
    mandate_observation: list[dict[str, Any]] | None = None,
    mandate_data_status: str = "fresh",
    half_year_items: list[dict[str, Any]] | None = None,
    half_year_data_status: str = "fresh",
) -> dict[str, Any]:
    """Build a minimal IC context covering the 5 buckets HY-010 reads."""
    return {
        "schema_version": "1.0",
        "as_of": _AS_OF,
        "previous_trade_date": "2026-07-11",
        "is_trading_day": True,
        "generated_by": "investment_controller_context",
        "read_only": True,
        "holdings": {
            "source": "imported_portfolio",
            "as_of": _AS_OF,
            "data_status": "fresh" if holdings else "missing",
            "count": len(holdings or []),
            "items": holdings or [],
        },
        "observation_warehouse": {
            "source": "observation_warehouse",
            "as_of": _AS_OF,
            "data_status": "fresh" if observation else "missing",
            "count": len(observation or []),
            "items": observation or [],
        },
        "tradeflow_candidates": {
            "source": "tradeflow_candidates",
            "as_of": _AS_OF,
            "data_status": "fresh" if candidates else "missing",
            "count": len(candidates or []),
            "items": candidates or [],
        },
        "mandate_daily_report": {
            "source": "mandate_daily_report",
            "as_of": _AS_OF,
            "data_status": mandate_data_status,
            "main_candidates": mandate_main or [],
            "observation_candidates": mandate_observation or [],
        },
        "half_year_facts": _hy_bucket(
            half_year_items or [], data_status=half_year_data_status
        ) if half_year_items is not None or half_year_data_status != "fresh" else {
            "source": "half_year_facts_context",
            "as_of": _AS_OF,
            "data_status": half_year_data_status,
            "items": half_year_items or [],
        },
    }


# ── 1. universe 合并与去重 ─────────────────────────────────────────


class TestUniverseMerging:
    """三类来源合并、重复 symbol 去重、来源标签保留。"""

    def test_three_origins_each_present(self):
        ctx = _build_context(
            holdings=[_holding("600584.SH", name="华天科技")],
            observation=[_observation("601689.SH", status="near_entry")],
            candidates=[_candidate("688256.SH", name="寒武纪")],
        )
        q = build_half_year_update_queue(ctx)
        assert q.universe_size == 3
        by_sym = {e.symbol: e for e in q.items}
        assert ORIGIN_HOLDING in by_sym["600584.SH"].origins
        assert ORIGIN_OBSERVATION in by_sym["601689.SH"].origins
        assert ORIGIN_MANDATE_CANDIDATE in by_sym["688256.SH"].origins

    def test_duplicate_symbol_merges_origins(self):
        # 同一 symbol 出现在 holdings + observation + mandate main。
        ctx = _build_context(
            holdings=[_holding("600584.SH", name="华天科技")],
            observation=[_observation("600584.SH", status="ta_required")],
            mandate_main=[{"symbol": "600584.SH", "name": "华天科技", "topic": "半导体"}],
        )
        q = build_half_year_update_queue(ctx)
        assert q.universe_size == 1
        entry = q.items[0]
        assert entry.symbol == "600584.SH"
        assert set(entry.origins) == {
            ORIGIN_HOLDING, ORIGIN_OBSERVATION, ORIGIN_MANDATE_CANDIDATE
        }
        # holding 来源优先填 name。
        assert entry.name == "华天科技"
        # observation_state 保留。
        assert entry.observation_state == "ta_required"
        # mandate main_candidates 把 is_main_candidate 置真。
        assert entry.is_main_candidate is True

    def test_duplicate_symbol_within_same_bucket_deduped(self):
        ctx = _build_context(
            holdings=[
                _holding("600584.SH", name="华天科技"),
                _holding("600584.SH", name="重复"),
            ]
        )
        q = build_half_year_update_queue(ctx)
        assert q.universe_size == 1
        # 首条 name 被保留（不覆盖）。
        assert q.items[0].name == "华天科技"

    def test_empty_universe_returns_stable_structure(self):
        ctx = _build_context()
        q = build_half_year_update_queue(ctx)
        assert q.universe_size == 0
        assert q.items == []
        # 状态分布仍列出所有状态，全 0。
        for s in ALL_STATUSES:
            assert q.summary_by_status[s] == 0
        # 来源分布也全 0。
        assert q.summary_by_origin == {
            ORIGIN_HOLDING: 0,
            ORIGIN_OBSERVATION: 0,
            ORIGIN_MANDATE_CANDIDATE: 0,
        }

    def test_context_not_dict_returns_empty(self):
        q = build_half_year_update_queue("not a dict")  # type: ignore[arg-type]
        assert q.universe_size == 0
        assert q.items == []
        assert q.knowledge_root_available is False
        assert any("not a dict" in n for n in q.notes)


# ── 2. 状态分类（六种）─────────────────────────────────────────────


class TestStatusClassification:
    """六种状态：up_to_date / missing / stale / conflict / needs_digest / unknown。"""

    def test_up_to_date_with_disclosure_date(self):
        ctx = _build_context(
            holdings=[_holding("600584.SH")],
            half_year_items=[_hy_item(symbol="600584.SH")],
        )
        q = build_half_year_update_queue(ctx)
        entry = q.items[0]
        assert entry.status == STATUS_UP_TO_DATE
        assert entry.latest_disclosure_date == "2026-08-29"

    def test_unknown_when_disclosure_date_missing(self):
        ctx = _build_context(
            holdings=[_holding("600584.SH")],
            half_year_items=[
                _hy_item(symbol="600584.SH", latest_disclosure_date="")
            ],
        )
        q = build_half_year_update_queue(ctx)
        entry = q.items[0]
        assert entry.status == STATUS_UNKNOWN
        assert entry.latest_disclosure_date == ""

    def test_missing_when_symbol_not_in_half_year_facts(self):
        ctx = _build_context(
            holdings=[_holding("600584.SH")],
            half_year_items=[],  # 空 half_year_facts
        )
        q = build_half_year_update_queue(ctx)
        entry = q.items[0]
        assert entry.status == STATUS_MISSING

    def test_conflict_when_has_conflict_true(self):
        ctx = _build_context(
            holdings=[_holding("600584.SH")],
            half_year_items=[
                _hy_item(symbol="600584.SH", has_conflict=True)
            ],
        )
        q = build_half_year_update_queue(ctx)
        assert q.items[0].status == STATUS_CONFLICT

    def test_conflict_when_facts_status_CONFLICT(self):
        ctx = _build_context(
            holdings=[_holding("600584.SH")],
            half_year_items=[
                _hy_item(symbol="600584.SH", facts_status="CONFLICT", has_conflict=False)
            ],
        )
        q = build_half_year_update_queue(ctx)
        assert q.items[0].status == STATUS_CONFLICT

    def test_needs_digest_when_thesis_contradicted(self):
        ctx = _build_context(
            holdings=[_holding("600584.SH")],
            half_year_items=[
                _hy_item(
                    symbol="600584.SH",
                    thesis_check_status="contradicted",
                    thesis_inline="半年报营收打脸既有多头观点",
                )
            ],
        )
        q = build_half_year_update_queue(ctx)
        entry = q.items[0]
        assert entry.status == STATUS_NEEDS_DIGEST
        assert entry.thesis_check_status == "contradicted"

    def test_needs_digest_when_needs_tree_work_review(self):
        ctx = _build_context(
            holdings=[_holding("600584.SH")],
            half_year_items=[
                _hy_item(
                    symbol="600584.SH",
                    needs_tree_work_review=True,
                )
            ],
        )
        q = build_half_year_update_queue(ctx)
        assert q.items[0].status == STATUS_NEEDS_DIGEST

    def test_stale_when_data_status_stale(self):
        ctx = _build_context(
            holdings=[_holding("600584.SH")],
            half_year_items=[
                _hy_item(
                    symbol="600584.SH",
                    data_status="stale",
                    has_stale=True,
                )
            ],
        )
        q = build_half_year_update_queue(ctx)
        assert q.items[0].status == STATUS_STALE

    def test_conflict_beats_needs_digest(self):
        # 同时 has_conflict + thesis_check_status=contradicted → conflict 胜出。
        ctx = _build_context(
            holdings=[_holding("600584.SH")],
            half_year_items=[
                _hy_item(
                    symbol="600584.SH",
                    has_conflict=True,
                    thesis_check_status="contradicted",
                )
            ],
        )
        q = build_half_year_update_queue(ctx)
        assert q.items[0].status == STATUS_CONFLICT


# ── 3. 无知识根 / half_year_facts skipped/failed/missing ─────────────


class TestHalfYearFactsDegradation:
    """half_year_facts bucket 降级路径覆盖。"""

    def test_half_year_bucket_missing_all_symbols_missing(self):
        ctx = _build_context(
            holdings=[_holding("600584.SH")],
            observation=[_observation("601689.SH")],
        )
        del ctx["half_year_facts"]
        q = build_half_year_update_queue(ctx)
        assert q.universe_size == 2
        assert all(e.status == STATUS_MISSING for e in q.items)
        assert q.knowledge_root_available is False
        assert any("half_year_facts bucket missing" in n for n in q.notes)

    def test_half_year_data_status_skipped(self):
        ctx = _build_context(
            holdings=[_holding("600584.SH")],
            half_year_data_status="skipped",
            half_year_items=[],
        )
        q = build_half_year_update_queue(ctx)
        assert q.items[0].status == STATUS_MISSING
        assert q.knowledge_root_available is False

    def test_half_year_data_status_failed(self):
        ctx = _build_context(
            holdings=[_holding("600584.SH")],
            half_year_data_status="failed",
            half_year_items=[],
        )
        q = build_half_year_update_queue(ctx)
        assert q.items[0].status == STATUS_MISSING
        assert q.knowledge_root_available is False

    def test_half_year_data_status_missing_with_empty_items(self):
        ctx = _build_context(
            holdings=[_holding("600584.SH")],
            half_year_data_status="missing",
            half_year_items=[],
        )
        q = build_half_year_update_queue(ctx)
        # knowledge_root_available 仍 True（只是扫描了没结果），但状态 missing。
        assert q.items[0].status == STATUS_MISSING


# ── 4. 优先级排序 ───────────────────────────────────────────────────


class TestPrioritySorting:
    """排序：持仓冲突 > 持仓缺失 > 持仓 needs_digest > 持仓其他 >
    观察仓接近触发 > 昊天主候选 > 观察仓其他 > 昊天观察候选。"""

    def test_holding_conflict_rank_0(self):
        ctx = _build_context(
            holdings=[_holding("600584.SH")],
            half_year_items=[_hy_item(symbol="600584.SH", has_conflict=True)],
        )
        q = build_half_year_update_queue(ctx)
        assert q.items[0].priority_rank == 0
        assert q.items[0].priority_tier == "P0_HOLDING_CONFLICT"

    def test_holding_missing_rank_1(self):
        ctx = _build_context(
            holdings=[_holding("600584.SH")],
            half_year_items=[],
        )
        q = build_half_year_update_queue(ctx)
        assert q.items[0].priority_rank == 1
        assert q.items[0].priority_tier == "P1_HOLDING_MISSING"

    def test_holding_needs_digest_rank_2(self):
        ctx = _build_context(
            holdings=[_holding("600584.SH")],
            half_year_items=[
                _hy_item(symbol="600584.SH", thesis_check_status="contradicted")
            ],
        )
        q = build_half_year_update_queue(ctx)
        assert q.items[0].priority_rank == 2
        assert q.items[0].priority_tier == "P2_HOLDING_NEEDS_DIGEST"

    def test_holding_up_to_date_rank_3(self):
        ctx = _build_context(
            holdings=[_holding("600584.SH")],
            half_year_items=[_hy_item(symbol="600584.SH")],
        )
        q = build_half_year_update_queue(ctx)
        assert q.items[0].priority_rank == 3
        assert q.items[0].priority_tier == "P3_HOLDING_OTHER"

    def test_observation_near_entry_rank_4(self):
        ctx = _build_context(
            observation=[_observation("601689.SH", status="near_entry")],
        )
        q = build_half_year_update_queue(ctx)
        assert q.items[0].priority_rank == 4
        assert q.items[0].priority_tier == "P4_OBSERVATION_NEAR_TRIGGER"

    def test_observation_in_entry_zone_rank_4(self):
        ctx = _build_context(
            observation=[_observation("601689.SH", status="in_entry_zone")],
        )
        q = build_half_year_update_queue(ctx)
        assert q.items[0].priority_rank == 4

    def test_observation_ta_required_rank_4(self):
        ctx = _build_context(
            observation=[_observation("601689.SH", status="ta_required")],
        )
        q = build_half_year_update_queue(ctx)
        assert q.items[0].priority_rank == 4

    def test_mandate_main_candidate_rank_5(self):
        ctx = _build_context(
            mandate_main=[{"symbol": "688256.SH", "name": "寒武纪", "topic": "半导体"}],
        )
        q = build_half_year_update_queue(ctx)
        assert q.items[0].priority_rank == 5
        assert q.items[0].priority_tier == "P5_MANDATE_MAIN"
        assert q.items[0].is_main_candidate is True

    def test_observation_watching_rank_6(self):
        ctx = _build_context(
            observation=[_observation("002353.SZ", status="watching")],
        )
        q = build_half_year_update_queue(ctx)
        assert q.items[0].priority_rank == 6
        assert q.items[0].priority_tier == "P6_OBSERVATION_OTHER"

    def test_mandate_observation_candidate_rank_7(self):
        ctx = _build_context(
            mandate_observation=[
                {"symbol": "002353.SZ", "name": "杰瑞股份", "topic": "能源"}
            ],
        )
        q = build_half_year_update_queue(ctx)
        assert q.items[0].priority_rank == 7
        assert q.items[0].priority_tier == "P7_MANDATE_OBSERVATION"
        assert q.items[0].is_main_candidate is False

    def test_full_priority_ordering(self):
        """验证完整排序：8 个 symbol，每个 tier 一条。"""
        ctx = _build_context(
            holdings=[
                _holding("H_CONFLICT", name="持仓冲突"),
                _holding("H_MISSING", name="持仓缺失"),
                _holding("H_DIGEST", name="持仓反证"),
                _holding("H_OK", name="持仓已更新"),
            ],
            observation=[
                _observation("O_NEAR", status="near_entry"),
                _observation("O_WATCH", status="watching"),
            ],
            mandate_main=[{"symbol": "M_MAIN", "name": "昊天主候选", "topic": "T"}],
            mandate_observation=[{"symbol": "M_OBS", "name": "昊天观察候选", "topic": "T"}],
            half_year_items=[
                _hy_item(symbol="H_CONFLICT", has_conflict=True),
                # H_MISSING 不放 half_year_item → 自动 missing
                _hy_item(symbol="H_DIGEST", thesis_check_status="contradicted"),
                _hy_item(symbol="H_OK"),
            ],
        )
        q = build_half_year_update_queue(ctx)
        ranks = [e.priority_rank for e in q.items]
        # 期望顺序：0, 1, 2, 3, 4, 5, 6, 7
        assert ranks == [0, 1, 2, 3, 4, 5, 6, 7]
        symbols_at_top = [e.symbol for e in q.items[:3]]
        assert symbols_at_top == ["H_CONFLICT", "H_MISSING", "H_DIGEST"]

    def test_secondary_sort_by_half_year_score(self):
        """同 priority_rank 内，分数低（更负面）的靠前。"""
        ctx = _build_context(
            holdings=[_holding("A"), _holding("B")],
            half_year_items=[
                _hy_item(symbol="A", half_year_score=1.5),
                _hy_item(symbol="B", half_year_score=-3.0),
            ],
        )
        q = build_half_year_update_queue(ctx)
        # 两个都是 holding+up_to_date（rank 3），分数低的 B 排前。
        assert q.items[0].symbol == "B"
        assert q.items[1].symbol == "A"


# ── 5. 稳定空结构与异常容错 ─────────────────────────────────────────


class TestStableEmptyStructure:
    """各种异常 / 缺数据路径都返回稳定结构，不抛异常。"""

    def test_context_not_dict(self):
        q = build_half_year_update_queue(None)  # type: ignore[arg-type]
        assert q.universe_size == 0

    def test_buckets_not_dict(self):
        # holdings / observation 等是错误类型时不抛异常。
        ctx: dict[str, Any] = {
            "as_of": _AS_OF,
            "holdings": "not a dict",
            "observation_warehouse": None,
            "tradeflow_candidates": [],
            "mandate_daily_report": 42,
            "half_year_facts": True,
        }
        q = build_half_year_update_queue(ctx)
        assert q.universe_size == 0
        # knowledge_root_available 由 half_year_facts data_status 推断，
        # bucket 不是 dict 时 data_status 取空 → 不在 skipped/failed 集合 → True。
        # 但 universe 为空，items 也为空。

    def test_items_not_list(self):
        ctx: dict[str, Any] = {
            "as_of": _AS_OF,
            "holdings": {"items": "not a list"},
            "observation_warehouse": {"items": [{"symbol": "X"}]},
        }
        q = build_half_year_update_queue(ctx)
        # holdings items 不是 list 被过滤；observation 仍能进。
        assert q.universe_size == 1
        assert q.items[0].symbol == "X"

    def test_item_not_dict_skipped(self):
        ctx = _build_context(
            holdings=["string_item", _holding("600584.SH"), 42, None],
        )
        q = build_half_year_update_queue(ctx)
        assert q.universe_size == 1
        assert q.items[0].symbol == "600584.SH"

    def test_mandate_data_status_not_fresh_skipped(self):
        # mandate_daily_report data_status != fresh 时，main_candidates / observation
        # 不进 universe。
        ctx = _build_context(
            mandate_data_status="missing",
            mandate_main=[{"symbol": "M_MAIN", "name": "X"}],
        )
        q = build_half_year_update_queue(ctx)
        assert q.universe_size == 0


# ── 6. 强动作词防线 ─────────────────────────────────────────────────


class TestNoStrongActionWords:
    """reason / fact_summary 全量扫描禁词。"""

    def test_reason_clean_for_all_statuses(self):
        ctx = _build_context(
            holdings=[_holding("H1"), _holding("H2"), _holding("H3"), _holding("H4")],
            observation=[_observation("O1"), _observation("O2", status="near_entry")],
            half_year_items=[
                _hy_item(symbol="H1", has_conflict=True),
                _hy_item(symbol="H2", thesis_check_status="contradicted"),
                _hy_item(symbol="H3", data_status="stale", has_stale=True),
                _hy_item(symbol="H4", latest_disclosure_date=""),
            ],
        )
        q = build_half_year_update_queue(ctx)
        for entry in q.items:
            _assert_no_forbidden_words(entry.reason)
            _assert_no_forbidden_words(entry.fact_summary)

    def test_fact_summary_inherited_from_hy007(self):
        ctx = _build_context(
            holdings=[_holding("H1")],
            half_year_items=[
                _hy_item(
                    symbol="H1",
                    fact_summary_text="2025H1 储能营收同比 -20.0%",
                )
            ],
        )
        q = build_half_year_update_queue(ctx)
        assert q.items[0].fact_summary == "2025H1 储能营收同比 -20.0%"

    def test_reason_clipped_to_max_chars(self):
        long_inline = "半年报营收大幅下滑" + "x" * 300
        ctx = _build_context(
            holdings=[_holding("H1")],
            half_year_items=[
                _hy_item(
                    symbol="H1",
                    thesis_check_status="contradicted",
                    thesis_inline=long_inline,
                )
            ],
        )
        q = build_half_year_update_queue(ctx)
        assert len(q.items[0].reason) <= 200

    def test_forbidden_word_in_upstream_raises(self):
        # 防御性测试：如果上游 fact_summary 含禁词，build 阶段就要 raise。
        ctx = _build_context(
            holdings=[_holding("H1")],
            half_year_items=[
                _hy_item(
                    symbol="H1",
                    fact_summary_text="建议立即买入并加仓",
                )
            ],
        )
        with pytest.raises(AssertionError, match="forbidden action word"):
            build_half_year_update_queue(ctx)


# ── 7. Markdown / JSON 输出 ────────────────────────────────────────


class TestOutputSerialization:
    """to_dict / render_half_year_update_markdown / JSON 序列化稳定。"""

    def test_to_dict_json_serializable(self):
        ctx = _build_context(
            holdings=[_holding("H1")],
            observation=[_observation("O1", status="near_entry")],
            half_year_items=[_hy_item(symbol="H1")],
        )
        q = build_half_year_update_queue(ctx)
        d = q.to_dict()
        # 必须能 round-trip JSON（无 dataclass / datetime 残留）。
        s = json.dumps(d, ensure_ascii=False)
        d2 = json.loads(s)
        assert d2["task"] == TASK_CODE
        assert d2["universe_size"] == 2
        assert len(d2["items"]) == 2
        # 每条 item 必含 source 标签。
        for item in d2["items"]:
            assert item["source"] == "half_year_update_queue"

    def test_entry_to_dict(self):
        entry = HalfYearUpdateEntry(
            symbol="600584.SH", name="华天科技",
            origins=["holding"], status=STATUS_UP_TO_DATE,
        )
        d = entry.to_dict()
        assert d["symbol"] == "600584.SH"
        assert d["source"] == "half_year_update_queue"

    def test_markdown_contains_header_and_table(self):
        ctx = _build_context(
            holdings=[_holding("H1")],
            half_year_items=[_hy_item(symbol="H1")],
        )
        q = build_half_year_update_queue(ctx)
        md = render_half_year_update_markdown(q)
        assert "HY-010" in md
        assert "universe 大小" in md
        assert "| 优先级 |" in md
        assert "不输出交易动作" in md

    def test_markdown_empty_universe(self):
        ctx = _build_context()
        q = build_half_year_update_queue(ctx)
        md = render_half_year_update_markdown(q)
        assert "universe 为空" in md
        # 空 universe 不输出表格行。
        assert "| 优先级 |" not in md

    def test_markdown_reason_pipe_escaped(self):
        # reason 含 "|" 时不破坏表格。
        ctx = _build_context(
            holdings=[_holding("H1")],
            half_year_items=[
                _hy_item(
                    symbol="H1",
                    fact_summary_text="a | b",
                    thesis_check_status="contradicted",
                )
            ],
        )
        q = build_half_year_update_queue(ctx)
        md = render_half_year_update_markdown(q)
        # reason cell 不应出现裸 |（已被替换为 /）。
        # 找到 H1 那一行。
        h1_line = next(line for line in md.splitlines() if "H1" in line)
        # 10 列表格行应有 11 个 |（leading + 9 separators + trailing）。
        # 如果 reason 含裸 |，count 会 > 11。
        assert h1_line.count("|") == 11

    def test_markdown_clean_of_forbidden_words(self):
        ctx = _build_context(
            holdings=[_holding("H1"), _holding("H2")],
            observation=[_observation("O1"), _observation("O2", status="near_entry")],
            half_year_items=[
                _hy_item(symbol="H1", has_conflict=True),
                _hy_item(symbol="H2", thesis_check_status="weakened"),
            ],
        )
        q = build_half_year_update_queue(ctx)
        md = render_half_year_update_markdown(q)
        _assert_no_forbidden_words(md)

    def test_render_not_queue_returns_empty(self):
        assert render_half_year_update_markdown("not a queue") == ""  # type: ignore[arg-type]

    def test_suggest_report_path_format(self):
        path = suggest_report_path()
        assert path.startswith("docs/knowledge_reports/half_year_update_queue-")
        assert path.endswith(".md")


# ── 8. 重复执行稳定性 ──────────────────────────────────────────────


class TestIdempotent:
    """同一 context 多次调用结果一致（不依赖时间副作用）。"""

    def test_repeatable_with_fixed_as_of(self):
        ctx = _build_context(
            holdings=[_holding("H1")],
            observation=[_observation("O1", status="near_entry")],
            half_year_items=[_hy_item(symbol="H1")],
        )
        q1 = build_half_year_update_queue(ctx, as_of=_AS_OF)
        q2 = build_half_year_update_queue(ctx, as_of=_AS_OF)
        assert q1.universe_size == q2.universe_size
        assert [e.symbol for e in q1.items] == [e.symbol for e in q2.items]
        assert q1.summary_by_status == q2.summary_by_status

    def test_deep_copy_does_not_affect_subsequent_run(self):
        ctx = _build_context(
            holdings=[_holding("H1")],
            half_year_items=[_hy_item(symbol="H1")],
        )
        q1 = build_half_year_update_queue(ctx, as_of=_AS_OF)
        ctx_copy = copy.deepcopy(ctx)
        q2 = build_half_year_update_queue(ctx_copy, as_of=_AS_OF)
        assert q1.to_dict() == q2.to_dict()


# ── 9. summary 统计 ─────────────────────────────────────────────────


class TestSummaryCounts:
    """summary_by_status / summary_by_origin 准确。"""

    def test_summary_by_status_counts_all_statuses(self):
        ctx = _build_context(
            holdings=[_holding("H1"), _holding("H2"), _holding("H3")],
            observation=[_observation("O1"), _observation("O2")],
            half_year_items=[
                _hy_item(symbol="H1", has_conflict=True),
                _hy_item(symbol="H2", thesis_check_status="contradicted"),
                _hy_item(symbol="H3"),
                # O1 / O2 不在 half_year_facts → missing
            ],
        )
        q = build_half_year_update_queue(ctx)
        s = q.summary_by_status
        assert s[STATUS_CONFLICT] == 1
        assert s[STATUS_NEEDS_DIGEST] == 1
        assert s[STATUS_UP_TO_DATE] == 1
        assert s[STATUS_MISSING] == 2
        assert s[STATUS_STALE] == 0
        assert s[STATUS_UNKNOWN] == 0

    def test_summary_by_origin_counts_multi_origin(self):
        # H1 同时是 holding + observation + mandate_main。
        ctx = _build_context(
            holdings=[_holding("H1")],
            observation=[_observation("H1", status="ta_required")],
            mandate_main=[{"symbol": "H1", "name": "X", "topic": "T"}],
        )
        q = build_half_year_update_queue(ctx)
        o = q.summary_by_origin
        # universe_size 是 1（去重），但来源计数三个 +1。
        assert q.universe_size == 1
        assert o[ORIGIN_HOLDING] == 1
        assert o[ORIGIN_OBSERVATION] == 1
        assert o[ORIGIN_MANDATE_CANDIDATE] == 1

    def test_summary_by_status_includes_all_keys(self):
        ctx = _build_context()
        q = build_half_year_update_queue(ctx)
        # 即便 universe 为空，所有状态 key 都在。
        assert set(q.summary_by_status.keys()) == set(ALL_STATUSES)
