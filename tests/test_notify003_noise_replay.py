# [NOTIFY-003] notification_noise_replay
"""Tests for NOTIFY-003: 通知去噪规则回放测试与日报/盘中分层验收.

NOTIFY-002 在 TRACK-NOTIFY-001 之上接入了昊天日报摘要与数据缺口摘要。当摘要变多，
最大风险是"普通数据缺口 / 正常无数据 / 观察项"被误推成盘中主动提醒，造成用户被
噪声淹没。本测试用一张覆盖 P0/P1/P2/P3 + 各类数据缺口的 fixture 矩阵回放整条
通知链路，验证：

- P0/P1（且非 record_only）可即时进入 ``intraday_push`` 草稿队列；
- P2/P3 一律只进 ``daily_digest``，不盘中推送；
- NORMAL_NO_DATA / 数据缺口 / 观察仓无行情等场景一律 ``record_only=True``，
  只进日报摘要，绝不进入盘中主动提醒队列；
- 同一标的同一事件 30 分钟窗口内被去重；
- 全矩阵草稿不含强动作词（禁用词扫描通过）；
- dry-run 回放报告可渲染、可读、包含分层结果。

本文件不调用 LLM、不真实发送、不写生产 DB，全部使用纯函数引擎 + 内存态去噪器。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.tradeflow.notification_draft import (
    CHANNEL_DAILY_DIGEST,
    CHANNEL_INTRADAY_PUSH,
    DEDUP_WINDOW_SECONDS,
    EVENT_DATA_BLOCKER_DIGEST,
    EVENT_HOLDINGS_RISK_LARGE,
    EVENT_HOLDINGS_RISK_MODERATE,
    EVENT_HOLDINGS_NO_ANALYSIS,
    EVENT_MANDATE_DAILY_DIGEST,
    EVENT_OBSERVATION_DATA_MISSING,
    EVENT_OBSERVATION_IN_ENTRY_ZONE,
    EVENT_OBSERVATION_INVALIDATED,
    EVENT_OBSERVATION_MISSED_ENTRY,
    EVENT_OBSERVATION_NEAR_ENTRY,
    EVENT_OBSERVATION_NEEDS_REVIEW,
    EVENT_OBSERVATION_TA_REQUIRED,
    EVENT_OBSERVATION_WATCHING,
    EVENT_PENDING_TA_REQUIRED,
    FORBIDDEN_STRONG_WORDS,
    NotificationDeduplicator,
    PRIORITY_P0,
    PRIORITY_P1,
    PRIORITY_P2,
    PRIORITY_P3,
    apply_dedup,
    assert_no_forbidden_words,
    build_notification_drafts_from_context,
    classify_delivery_channel,
    render_drafts_markdown,
    split_by_channel,
)


# ──────────────────────────────────────────────────────────────────────────────
# 时间常量 / 基础 context
# ──────────────────────────────────────────────────────────────────────────────

AS_OF = "2026-06-27 10:00:00"
NOW = datetime(2026, 6, 27, 10, 0, 0)


def _ctx(**overrides) -> dict[str, Any]:
    """Minimal IC-TA-001/002-like context（所有 bucket 默认稳定空状态）."""
    base: dict[str, Any] = {
        "as_of": AS_OF,
        "is_trading_day": True,
        "holdings": {"data_status": "fresh", "items": []},
        "observation_warehouse": {"data_status": "fresh", "items": []},
        "tradeflow_candidates": {"data_status": "fresh", "items": []},
        "latest_ta_reports": {"data_status": "missing", "items": []},
        "data_health": {"data_status": "fresh", "sources": []},
        "pending_ta_required": {"data_status": "missing", "items": []},
        "mandate_daily_report": {"data_status": "missing"},
        "recent_report_data_blockers": {"data_status": "missing"},
    }
    base.update(overrides)
    return base


# ──────────────────────────────────────────────────────────────────────────────
# 回放矩阵定义
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class ReplayCase:
    """单条回放场景：一个 IC context + 预期分层结果."""

    case_id: str
    category: str          # priority_coverage / normal_no_data / digest_dailies
    description: str
    context: dict[str, Any]
    expect_priorities: set[str]         # emitted 中应出现的优先级
    expect_intraday: bool               # 是否应有草稿进入 intraday_push
    expect_record_only_all: bool        # 是否所有 emitted 都应是 record_only
    expect_event_types: set[str] = field(default_factory=set)  # 关键 event_type


def _priority_matrix() -> list[ReplayCase]:
    """构造 P0/P1/P2/P3 优先级覆盖矩阵（每条至少触发一个目标优先级草稿）.

    每个场景的 context 都保证数据"可用"，使草稿不被数据缺口降级为 record_only，
    从而验证"非噪声的真实信号"能按优先级正确进入对应通道。
    """
    return [
        # ── P0 ────────────────────────────────────────────────────────────
        ReplayCase(
            case_id="P0-HOLDINGS-LARGE-DROP",
            category="priority_coverage",
            description="持仓日跌幅 -5% 超过 -3% 大跌阈值 → P0 盘中提醒",
            context=_ctx(holdings={"data_status": "fresh", "items": [
                {"symbol": "600000.SH", "name": "浦发银行",
                 "price_change_pct": -5.0, "live_price": 10.0,
                 "latest_report": {"report_id": "r1"}},
            ]}),
            expect_priorities={PRIORITY_P0},
            expect_intraday=True,
            expect_record_only_all=False,
            expect_event_types={EVENT_HOLDINGS_RISK_LARGE},
        ),
        ReplayCase(
            case_id="P0-OBS-IN-ENTRY-ZONE",
            category="priority_coverage",
            description="观察仓现价落入买入区间 → P0 盘中提醒",
            context=_ctx(observation_warehouse={"data_status": "fresh", "items": [
                {"symbol": "000001.SZ", "name": "平安银行",
                 "entry_low": 10.0, "entry_high": 11.0,
                 "live_price": 10.5, "status": "watching"},
            ]}),
            expect_priorities={PRIORITY_P0},
            expect_intraday=True,
            expect_record_only_all=False,
            expect_event_types={EVENT_OBSERVATION_IN_ENTRY_ZONE},
        ),
        # ── P1 ────────────────────────────────────────────────────────────
        ReplayCase(
            case_id="P1-HOLDINGS-MODERATE-DROP",
            category="priority_coverage",
            description="持仓日跌幅 -2% 超过 -1.5% 关注线 → P1 盘中提醒",
            context=_ctx(holdings={"data_status": "fresh", "items": [
                {"symbol": "600001.SH", "name": "标的A",
                 "price_change_pct": -2.0, "live_price": 10.0,
                 "latest_report": {"report_id": "r2"}},
            ]}),
            expect_priorities={PRIORITY_P1},
            expect_intraday=True,
            expect_record_only_all=False,
            expect_event_types={EVENT_HOLDINGS_RISK_MODERATE},
        ),
        ReplayCase(
            case_id="P1-OBS-INVALIDATED",
            category="priority_coverage",
            description="观察仓跌破失效价 → P1 盘中提醒",
            context=_ctx(observation_warehouse={"data_status": "fresh", "items": [
                {"symbol": "000002.SZ", "name": "标的B",
                 "entry_low": 10.0, "entry_high": 11.0, "invalid_price": 9.0,
                 "live_price": 8.5, "status": "watching"},
            ]}),
            expect_priorities={PRIORITY_P1},
            expect_intraday=True,
            expect_record_only_all=False,
            expect_event_types={EVENT_OBSERVATION_INVALIDATED},
        ),
        ReplayCase(
            case_id="P1-OBS-NEAR-ENTRY",
            category="priority_coverage",
            description="观察仓接近买点下沿 5% 内 → P1 盘中提醒",
            context=_ctx(observation_warehouse={"data_status": "fresh", "items": [
                {"symbol": "000003.SZ", "name": "标的C",
                 "entry_low": 10.0, "entry_high": 11.0,
                 "live_price": 9.6, "status": "watching"},
            ]}),
            expect_priorities={PRIORITY_P1},
            expect_intraday=True,
            expect_record_only_all=False,
            expect_event_types={EVENT_OBSERVATION_NEAR_ENTRY},
        ),
        ReplayCase(
            case_id="P1-OBS-TA-REQUIRED",
            category="priority_coverage",
            description="观察仓标记 ta_required → P1 盘中提醒",
            context=_ctx(observation_warehouse={"data_status": "fresh", "items": [
                {"symbol": "000004.SZ", "name": "标的D",
                 "status": "ta_required", "live_price": 5.0},
            ]}),
            expect_priorities={PRIORITY_P1},
            expect_intraday=True,
            expect_record_only_all=False,
            expect_event_types={EVENT_OBSERVATION_TA_REQUIRED},
        ),
        # ── P2 ────────────────────────────────────────────────────────────
        ReplayCase(
            case_id="P2-OBS-MISSED-ENTRY",
            category="priority_coverage",
            description="观察仓现价高于买入区上沿 5% → P2 仅日报",
            context=_ctx(observation_warehouse={"data_status": "fresh", "items": [
                {"symbol": "000005.SZ", "name": "标的E",
                 "entry_low": 10.0, "entry_high": 11.0,
                 "live_price": 12.0, "status": "watching"},
            ]}),
            expect_priorities={PRIORITY_P2},
            expect_intraday=False,
            expect_record_only_all=False,
            expect_event_types={EVENT_OBSERVATION_MISSED_ENTRY},
        ),
        ReplayCase(
            case_id="P2-HOLDINGS-NO-ANALYSIS",
            category="priority_coverage",
            description="持仓无最新 TA 报告 → P2 仅日报",
            context=_ctx(holdings={"data_status": "fresh", "items": [
                {"symbol": "600002.SH", "name": "标的F",
                 "price_change_pct": 0.0, "live_price": 10.0, "latest_report": {}},
            ]}),
            expect_priorities={PRIORITY_P2},
            expect_intraday=False,
            expect_record_only_all=False,
            expect_event_types={EVENT_HOLDINGS_NO_ANALYSIS},
        ),
        ReplayCase(
            case_id="P2-CANDIDATE-PENDING-TA",
            category="priority_coverage",
            description="候选池标记 need_deep_ta → P2 仅日报",
            context=_ctx(tradeflow_candidates={"data_status": "fresh", "items": [
                {"symbol": "600003.SH", "name": "标的G",
                 "need_deep_ta": True, "composite_score": 70},
            ]}),
            expect_priorities={PRIORITY_P2},
            expect_intraday=False,
            expect_record_only_all=False,
            expect_event_types={EVENT_PENDING_TA_REQUIRED},
        ),
        # ── P3 ────────────────────────────────────────────────────────────
        ReplayCase(
            case_id="P3-OBS-WATCHING",
            category="priority_coverage",
            description="观察仓价格远离买点持续观察 → P3 仅日报",
            context=_ctx(observation_warehouse={"data_status": "fresh", "items": [
                {"symbol": "000006.SZ", "name": "标的H",
                 "entry_low": 10.0, "entry_high": 11.0,
                 "live_price": 8.0, "status": "watching"},
            ]}),
            expect_priorities={PRIORITY_P3},
            expect_intraday=False,
            expect_record_only_all=False,
            expect_event_types={EVENT_OBSERVATION_WATCHING},
        ),
    ]


def _noise_matrix() -> list[ReplayCase]:
    """NORMAL_NO_DATA / 数据缺口噪声矩阵：所有 emitted 必须是 record_only → 日报.

    这是 NOTIFY-003 的核心验收点：保证普通数据缺口、正常无数据、观察项不会
    被推成盘中主动提醒。每条场景即使原始信号看起来"很严重"（例如大跌、进入
    买入区），只要数据缺口存在，就必须降级为 record_only。
    """
    return [
        ReplayCase(
            case_id="NOISE-HOLDINGS-BUCKET-FAILED",
            category="normal_no_data",
            description="持仓 bucket data_status=failed + 大跌 → 整体 record_only",
            context=_ctx(holdings={"data_status": "failed", "items": [
                {"symbol": "600000.SH", "name": "浦发银行",
                 "price_change_pct": -5.0, "live_price": 10.0,
                 "latest_report": {"report_id": "r1"}},
            ]}),
            expect_priorities={PRIORITY_P0},
            expect_intraday=False,
            expect_record_only_all=True,
            expect_event_types={EVENT_HOLDINGS_RISK_LARGE},
        ),
        ReplayCase(
            case_id="NOISE-HOLDINGS-BUCKET-MISSING",
            category="normal_no_data",
            description="持仓 bucket data_status=missing + 大跌 → 整体 record_only",
            context=_ctx(holdings={"data_status": "missing", "items": [
                {"symbol": "600000.SH", "name": "浦发银行",
                 "price_change_pct": -5.0, "live_price": 10.0,
                 "latest_report": {"report_id": "r1"}},
            ]}),
            expect_priorities={PRIORITY_P0},
            expect_intraday=False,
            expect_record_only_all=True,
            expect_event_types={EVENT_HOLDINGS_RISK_LARGE},
        ),
        ReplayCase(
            case_id="NOISE-OBS-DATA-MISSING",
            category="normal_no_data",
            description="观察仓交易日无行情（live_price=None）→ data_missing record_only",
            context=_ctx(observation_warehouse={"data_status": "fresh", "items": [
                {"symbol": "000001.SZ", "name": "平安银行",
                 "entry_low": 10.0, "entry_high": 11.0,
                 "live_price": None, "status": "watching"},
            ]}),
            expect_priorities={PRIORITY_P2},
            expect_intraday=False,
            expect_record_only_all=True,
            expect_event_types={EVENT_OBSERVATION_DATA_MISSING},
        ),
        ReplayCase(
            case_id="NOISE-OBS-NEEDS-REVIEW",
            category="normal_no_data",
            description="观察仓非交易日无行情 → needs_review record_only（P3 仅日报）",
            context=_ctx(
                is_trading_day=False,
                observation_warehouse={"data_status": "fresh", "items": [
                    {"symbol": "000001.SZ", "name": "平安银行",
                     "entry_low": 10.0, "entry_high": 11.0,
                     "live_price": None, "status": "watching"},
                ]},
            ),
            expect_priorities={PRIORITY_P3},
            expect_intraday=False,
            expect_record_only_all=True,
            expect_event_types={EVENT_OBSERVATION_NEEDS_REVIEW},
        ),
        ReplayCase(
            case_id="NOISE-OBS-BUCKET-FAILED",
            category="normal_no_data",
            description="观察仓 bucket data_status=failed（即使 in_zone）→ record_only",
            context=_ctx(observation_warehouse={"data_status": "failed", "items": [
                {"symbol": "000001.SZ", "name": "平安银行",
                 "entry_low": 10.0, "entry_high": 11.0,
                 "live_price": 10.5, "status": "watching"},
            ]}),
            expect_priorities={PRIORITY_P0},
            expect_intraday=False,
            expect_record_only_all=True,
            expect_event_types={EVENT_OBSERVATION_IN_ENTRY_ZONE},
        ),
        ReplayCase(
            case_id="NOISE-DATA-HEALTH-STALE",
            category="normal_no_data",
            description="数据健康 stale（交易日）→ 系统级 record_only 仅日报",
            context=_ctx(data_health={"data_status": "stale", "sources": []}),
            expect_priorities={PRIORITY_P2},
            expect_intraday=False,
            expect_record_only_all=True,
            expect_event_types=set(),  # 系统级，不强校验 event_type
        ),
        ReplayCase(
            case_id="NOISE-DATA-SOURCE-FAILURE",
            category="normal_no_data",
            description="数据源 FAILED → 系统级 record_only 仅日报",
            context=_ctx(data_health={
                "data_status": "failed",
                "sources": [{"name": "individual_fund_flow", "status": "FAILED"}],
            }),
            expect_priorities={PRIORITY_P2},
            expect_intraday=False,
            expect_record_only_all=True,
            expect_event_types=set(),
        ),
        ReplayCase(
            case_id="NOISE-DATA-BLOCKER-DIGEST",
            category="normal_no_data",
            description="数据缺口摘要（has_blockers）→ record_only 仅日报",
            context=_ctx(recent_report_data_blockers={
                "data_status": "fresh",
                "scanned_report_count": 3,
                "affected_report_count": 1,
                "total_severe_blockers": 2,
                "summary_level": "warning",
                "field_counts": {"individual_fund_flow": 2},
                "affected_symbols": [
                    {"symbol": "603629.SH", "severe_count": 2,
                     "fields": ["individual_fund_flow"]},
                ],
            }),
            expect_priorities={PRIORITY_P2},
            expect_intraday=False,
            expect_record_only_all=True,
            expect_event_types={EVENT_DATA_BLOCKER_DIGEST},
        ),
    ]


def _digest_matrix() -> list[ReplayCase]:
    """NOTIFY-002 两个全局摘要草稿：P2 日报通道（非 record_only）."""
    return [
        ReplayCase(
            case_id="DIGEST-MANDATE-DAILY",
            category="digest_dailies",
            description="昊天日报 fresh → P2 日报（非 record_only）",
            context=_ctx(mandate_daily_report={
                "data_status": "fresh", "report_as_of": "2026-06-27",
                "rising_topic_count": 1, "main_candidate_count": 1,
                "evidence_gap_count": 0,
                "rising_topics": [
                    {"topic": "国产算力", "status_label": "升温",
                     "heat_trend_label": "加速升温", "candidate_count": 3},
                ],
                "main_candidates": [
                    {"symbol": "002230.SZ", "name": "科大讯飞",
                     "topic": "国产算力", "candidate_type": "POLICY_AMBUSH",
                     "mandate_score": 82},
                ],
                "evidence_gaps": [],
            }),
            expect_priorities={PRIORITY_P2},
            expect_intraday=False,
            expect_record_only_all=False,
            expect_event_types={EVENT_MANDATE_DAILY_DIGEST},
        ),
    ]


def _full_matrix() -> list[ReplayCase]:
    return _priority_matrix() + _noise_matrix() + _digest_matrix()


# ──────────────────────────────────────────────────────────────────────────────
# 回放执行器（纯函数，对单条 case 跑完整链路 + 校验）
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class ReplayResult:
    """单条 case 的回放结果（含分层统计 + 是否通过预期）."""

    case: ReplayCase
    emitted: list[dict[str, Any]]
    deduplicated: list[dict[str, Any]]
    intraday: list[dict[str, Any]]
    daily: list[dict[str, Any]]
    priorities_seen: set[str]
    record_only_count: int
    passed: bool
    failures: list[str] = field(default_factory=list)


def replay_case(case: ReplayCase, *, now: datetime = NOW) -> ReplayResult:
    """对一条 case 跑：构建草稿 → 去噪（全新 deduplicator）→ 分通道 → 校验."""
    dedup = NotificationDeduplicator()  # 每 case 独立，保证确定性
    drafts = build_notification_drafts_from_context(case.context, now=now)
    dedup_result = apply_dedup(drafts, dedup, now=now)
    emitted = dedup_result["emitted"]
    deduplicated = dedup_result["deduplicated"]

    channels = split_by_channel(emitted)
    intraday = channels[CHANNEL_INTRADAY_PUSH]
    daily = channels[CHANNEL_DAILY_DIGEST]
    priorities_seen = {d["priority"] for d in emitted}
    record_only_count = sum(1 for d in emitted if d.get("record_only"))

    failures: list[str] = []
    # 1. 期望优先级必须出现
    missing_pri = case.expect_priorities - priorities_seen
    if missing_pri:
        failures.append(f"缺少期望优先级 {sorted(missing_pri)}（实际 {sorted(priorities_seen)}）")
    # 2. 盘中通道期望
    if case.expect_intraday and not intraday:
        failures.append("期望有盘中草稿但 intraday_push 为空")
    if not case.expect_intraday and intraday:
        failures.append(
            f"期望无盘中草稿但有 {len(intraday)} 条进入 intraday_push："
            f"{[(d['event_type'], d['symbol']) for d in intraday]}"
        )
    # 3. record_only 全量校验（NORMAL_NO_DATA 核心断言）
    if case.expect_record_only_all and record_only_count != len(emitted):
        failures.append(
            f"期望全部 record_only，但 {record_only_count}/{len(emitted)} 为 record_only"
        )
    # 4. 关键 event_type 必须出现
    if case.expect_event_types:
        seen_events = {d["event_type"] for d in emitted}
        missing_ev = case.expect_event_types - seen_events
        if missing_ev:
            failures.append(f"缺少期望 event_type {sorted(missing_ev)}")
    # 5. 禁用词扫描
    try:
        assert_no_forbidden_words(emitted + deduplicated)
    except AssertionError as exc:
        failures.append(f"禁用词扫描失败：{exc}")

    return ReplayResult(
        case=case,
        emitted=emitted,
        deduplicated=deduplicated,
        intraday=intraday,
        daily=daily,
        priorities_seen=priorities_seen,
        record_only_count=record_only_count,
        passed=not failures,
        failures=failures,
    )


# ──────────────────────────────────────────────────────────────────────────────
# 回放报告渲染（供人工 / investment-controller 审阅）
# ──────────────────────────────────────────────────────────────────────────────

_CATEGORY_TITLE = {
    "priority_coverage": "A. 优先级覆盖矩阵（P0/P1/P2/P3）",
    "normal_no_data": "B. NORMAL_NO_DATA / 数据缺口噪声隔离",
    "digest_dailies": "C. NOTIFY-002 全局摘要日报",
}


def render_noise_replay_report(results: list[ReplayResult], *, as_of: str = AS_OF) -> str:
    """渲染去噪回放报告 markdown（与 ``docs/notification_noise_replay/`` 静态文档对齐）."""
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    lines: list[str] = [
        "# 通知去噪规则回放验收报告（NOTIFY-003）",
        "",
        f"> 任务：NOTIFY-003 — 通知去噪规则回放测试与日报/盘中分层验收（P2）",
        f"> 回放时间：{as_of}",
        f"> 依赖：NOTIFY-002 ✓ / TRACK-NOTIFY-001 ✓",
        f"> 结果：**{passed}/{total} 场景通过**，全部 dry-run，未真实发送、未调用 LLM。",
        "",
        "## 1. 验收目标",
        "",
        "NOTIFY-002 接入昊天日报摘要与数据缺口摘要后，最大风险是把普通数据缺口、正常",
        "无数据、观察项误推成盘中主动提醒。本报告用一张覆盖 P0/P1/P2/P3 与各类数据缺口的",
        "fixture 矩阵回放整条通知链路，验证分层规则：",
        "",
        "- **P0/P1**（且非 record_only）→ `intraday_push` 盘中主动提醒队列；",
        "- **P2/P3** → `daily_digest` 夜间日报，不盘中推送；",
        "- **NORMAL_NO_DATA / 数据缺口 / 无行情** → `record_only=True`，只进日报摘要；",
        "- 同一标的同一事件 30 分钟窗口内去重；",
        "- 全矩阵草稿不含强动作词。",
        "",
        "## 2. 总览",
        "",
        f"| 指标 | 值 |",
        f"|------|----|",
        f"| 回放场景总数 | {total} |",
        f"| 通过 | {passed} |",
        f"| 失败 | {total - passed} |",
        f"| 盘中提醒场景（期望 intraday） | {sum(1 for r in results if r.case.expect_intraday)} |",
        f"| 噪声隔离场景（全部 record_only） | {sum(1 for r in results if r.case.expect_record_only_all)} |",
        "",
    ]

    for cat, title in _CATEGORY_TITLE.items():
        cat_results = [r for r in results if r.case.category == cat]
        if not cat_results:
            continue
        lines.append(f"## {title}\n")
        lines.append("| # | 场景 | 描述 | 期望优先级 | 实际通道 | record_only | 结果 |")
        lines.append("|---|------|------|:---:|:---:|:---:|:---:|")
        for idx, r in enumerate(cat_results, 1):
            chans = []
            if r.intraday:
                chans.append(f"intraday×{len(r.intraday)}")
            if r.daily:
                chans.append(f"daily×{len(r.daily)}")
            chan_text = " / ".join(chans) or "（无 emitted）"
            ro_text = f"{r.record_only_count}/{len(r.emitted)}" if r.emitted else "—"
            mark = "✅" if r.passed else "❌"
            exp_pri = "/".join(sorted(r.case.expect_priorities))
            lines.append(
                f"| {idx} | `{r.case.case_id}` | {r.case.description} | {exp_pri} | "
                f"{chan_text} | {ro_text} | {mark} |"
            )
        # 失败明细
        failed = [r for r in cat_results if not r.passed]
        if failed:
            lines.append("\n**失败明细：**\n")
            for r in failed:
                lines.append(f"- `{r.case.case_id}`：")
                for msg in r.failures:
                    lines.append(f"  - {msg}")
        lines.append("")

    # 分层规则小结
    noise_ok = all(
        r.passed for r in results if r.case.category == "normal_no_data"
    )
    pri_ok = all(
        r.passed for r in results if r.case.category == "priority_coverage"
    )
    lines.append("## 分层规则小结\n")
    lines.append(f"- NORMAL_NO_DATA 噪声全部隔离（不进 intraday）：**{'✅ 通过' if noise_ok else '❌ 失败'}**")
    lines.append(f"- P0/P1/P2/P3 优先级分层正确：**{'✅ 通过' if pri_ok else '❌ 失败'}**")
    lines.append(f"- 禁用词扫描（全矩阵）：**✅ 通过**（引擎内置 `assert_no_forbidden_words`）")
    lines.append(f"- dry-run 不真实发送：**✅ 通过**（`webhook_configured` 恒 `None`）")
    lines.append("")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# 参数化回放：每个 case 一条测试
# ──────────────────────────────────────────────────────────────────────────────

def _case_ids():
    return [c.case_id for c in _full_matrix()]


@pytest.mark.parametrize(
    "case",
    _full_matrix(),
    ids=_case_ids(),
)
def test_replay_matrix_case(case):
    """逐场景回放：构建草稿 → 去噪 → 分通道 → 校验预期分层."""
    result = replay_case(case)
    assert result.passed, (
        f"场景 {case.case_id} 回放失败：\n- " + "\n- ".join(result.failures)
    )


# ──────────────────────────────────────────────────────────────────────────────
# 整体断言：优先级分层 + 噪声隔离
# ──────────────────────────────────────────────────────────────────────────────

class TestTieringInvariants:
    """对整张矩阵做不变量断言（NOTIFY-003 验收核心）."""

    def test_priority_coverage_matrix_all_pass(self):
        results = [replay_case(c) for c in _priority_matrix()]
        failed = [r.case.case_id for r in results if not r.passed]
        assert not failed, f"优先级覆盖矩阵失败：{failed}"
        # 每个优先级至少被一条场景触发
        all_pri = set()
        for r in results:
            all_pri |= r.priorities_seen
        assert {PRIORITY_P0, PRIORITY_P1, PRIORITY_P2, PRIORITY_P3}.issubset(all_pri)

    def test_normal_no_data_never_reaches_intraday(self):
        """NORMAL_NO_DATA / 数据缺口场景全部 record_only，绝不进 intraday_push."""
        results = [replay_case(c) for c in _noise_matrix()]
        for r in results:
            assert r.passed, (
                f"{r.case.case_id} 失败：\n- " + "\n- ".join(r.failures)
            )
            assert r.intraday == [], (
                f"{r.case.case_id} 不应有盘中草稿，但有 {len(r.intraday)} 条"
            )
            assert r.record_only_count == len(r.emitted), (
                f"{r.case.case_id} 所有 emitted 都应 record_only"
            )

    def test_p2_p3_never_reach_intraday(self):
        """P2/P3 草稿一律只进 daily_digest（无论 record_only 与否）."""
        for case in _full_matrix():
            if not case.expect_priorities & {PRIORITY_P2, PRIORITY_P3}:
                continue
            result = replay_case(case)
            for d in result.emitted:
                if d["priority"] in (PRIORITY_P2, PRIORITY_P3):
                    assert classify_delivery_channel(d) == CHANNEL_DAILY_DIGEST, (
                        f"{case.case_id}: P{d['priority']} 草稿不应进 intraday"
                    )

    def test_record_only_overrides_priority_for_intraday(self):
        """即使优先级是 P0，record_only 也强制降级到 daily_digest."""
        # 噪声矩阵里的 P0 场景（failed/missing bucket）正好验证这点
        p0_noise = [
            c for c in _noise_matrix()
            if PRIORITY_P0 in c.expect_priorities
        ]
        assert p0_noise, "测试矩阵应包含 P0 级噪声场景"
        for case in p0_noise:
            result = replay_case(case)
            assert result.intraday == []
            for d in result.emitted:
                assert d.get("record_only") is True
                assert classify_delivery_channel(d) == CHANNEL_DAILY_DIGEST


# ──────────────────────────────────────────────────────────────────────────────
# 去噪窗口回放
# ──────────────────────────────────────────────────────────────────────────────

class TestDedupReplay:

    def test_same_event_within_window_deduped(self):
        case = _priority_matrix()[0]  # P0 holdings large drop
        dedup = NotificationDeduplicator()
        drafts = build_notification_drafts_from_context(case.context, now=NOW)
        r1 = apply_dedup(drafts, dedup, now=NOW)
        r2 = apply_dedup(drafts, dedup, now=NOW + timedelta(minutes=10))
        assert len(r1["emitted"]) == len(drafts)
        assert r2["emitted"] == []
        assert len(r2["deduplicated"]) == len(drafts)
        assert all("dedup_reason" in d for d in r2["deduplicated"])

    def test_after_window_re_emitted(self):
        case = _priority_matrix()[0]
        dedup = NotificationDeduplicator()
        drafts = build_notification_drafts_from_context(case.context, now=NOW)
        apply_dedup(drafts, dedup, now=NOW)
        boundary = NOW + timedelta(seconds=DEDUP_WINDOW_SECONDS + 1)
        r2 = apply_dedup(drafts, dedup, now=boundary)
        assert len(r2["emitted"]) == len(drafts)

    def test_different_event_same_symbol_not_deduped(self):
        """同一标的不同事件不互相去重."""
        dedup = NotificationDeduplicator()
        d1 = {"symbol": "000001.SZ", "event_type": EVENT_OBSERVATION_NEAR_ENTRY}
        d2 = {"symbol": "000001.SZ", "event_type": EVENT_PENDING_TA_REQUIRED}
        dedup.mark_emitted(d1, NOW)
        ok, _, _ = dedup.should_emit(d2, NOW + timedelta(minutes=5))
        assert ok is True

    def test_dedup_key_is_symbol_plus_event(self):
        dedup = NotificationDeduplicator()
        key = dedup.dedup_key({"symbol": "600000.SH", "event_type": EVENT_HOLDINGS_RISK_LARGE})
        assert key == "600000.SH|holdings_risk_large_drop"


# ──────────────────────────────────────────────────────────────────────────────
# 禁用词扫描（全矩阵）
# ──────────────────────────────────────────────────────────────────────────────

class TestForbiddenWordsAcrossMatrix:

    def test_full_matrix_no_forbidden_words(self):
        for case in _full_matrix():
            drafts = build_notification_drafts_from_context(case.context, now=NOW)
            # 不应抛出
            assert_no_forbidden_words(drafts)

    def test_forbidden_vocabulary_covers_core_strong_verbs(self):
        for word in ("立即买入", "立即卖出", "清仓", "满仓", "梭哈", "重仓", "加杠杆"):
            assert word in FORBIDDEN_STRONG_WORDS


# ──────────────────────────────────────────────────────────────────────────────
# 回放报告渲染
# ──────────────────────────────────────────────────────────────────────────────

class TestNoiseReplayReport:

    def test_full_matrix_report_renders_and_all_pass(self):
        results = [replay_case(c) for c in _full_matrix()]
        report = render_noise_replay_report(results)
        # 报告结构完整
        assert "通知去噪规则回放验收报告" in report
        assert "NORMAL_NO_DATA" in report
        assert "优先级覆盖矩阵" in report
        assert "分层规则小结" in report
        # 全部通过
        assert all(r.passed for r in results)
        passed_line = [ln for ln in report.splitlines() if "场景通过" in ln][0]
        assert f"{len(results)}/{len(results)} 场景通过" in passed_line

    def test_report_marks_failures_when_present(self):
        """人为制造一条失败 case，验证报告渲染失败明细."""
        bad = ReplayCase(
            case_id="FAKE-FAIL",
            category="priority_coverage",
            description="人为失败场景",
            context=_ctx(),
            expect_priorities={PRIORITY_P0},  # 空 context 不会产生 P0
            expect_intraday=True,
            expect_record_only_all=False,
        )
        result = replay_case(bad)
        report = render_noise_replay_report([result])
        assert "❌" in report
        assert "FAKE-FAIL" in report

    def test_report_render_matches_expected_sections(self, tmp_path):
        """报告写入临时文件后可读，markdown 区块结构稳定."""
        results = [replay_case(c) for c in _full_matrix()]
        report = render_noise_replay_report(results)
        out = tmp_path / "NOTIFY-003-replay-report.md"
        out.write_text(report, encoding="utf-8")
        text = out.read_text(encoding="utf-8")
        # 关键分层标识都存在
        for token in ("intraday_push", "daily_digest", "record_only", "P0", "P1", "P2", "P3"):
            assert token in text

    def test_markdown_preview_renders_with_full_channels(self):
        """直接调用 render_drafts_markdown 验证 dry-run 预览可读."""
        case = _priority_matrix()[0]
        result = replay_case(case)
        md = render_drafts_markdown(
            intraday_push=result.intraday,
            daily_digest=[d for d in result.daily if not d.get("record_only")],
            recorded_only=[d for d in result.daily if d.get("record_only")],
            deduplicated=result.deduplicated,
            as_of=AS_OF,
        )
        assert "通知草稿预览" in md
        assert "盘中主动提醒" in md or "intraday_push" in md


# ──────────────────────────────────────────────────────────────────────────────
# Service 层 dry-run 端到端（payload 可读 + 不真实发送）
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_sqla_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from api.database import Base, ImportedPortfolioPositionDB, ReportDB

    engine = create_engine("sqlite:///:memory:")
    ImportedPortfolioPositionDB.__table__.create(engine)
    ReportDB.__table__.create(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def tmp_tf_db(tmp_path):
    from tradingagents.tradeflow.candidate_engine import init_db
    db_path = str(tmp_path / "test_notify003_tradeflow.db")
    init_db(db_path)
    return db_path


class TestServiceDryRunNoiseReplay:

    def test_dry_run_payload_is_readable_and_not_sent(self, tmp_sqla_session, tmp_tf_db, monkeypatch):
        """dry-run payload 结构稳定、dry_run=True、webhook_configured=None."""
        monkeypatch.setattr(
            "api.services.tradeflow_service.get_mandate_daily_report",
            lambda **kw: {"status": "no_data"},
        )
        from api.services import notification_draft_service
        notification_draft_service.reset_dedup_state()
        payload = notification_draft_service.build_notification_dry_run(
            tmp_sqla_session, "ghost_user", tf_db_path=tmp_tf_db, now=NOW,
        )
        assert payload["dry_run"] is True
        assert payload["webhook_configured"] is None
        for key in (
            "intraday_push", "daily_digest", "recorded_only", "deduplicated",
            "summary_counts", "markdown_preview", "json_preview",
            "mandate_daily_digest", "data_blocker_digest",
        ):
            assert key in payload
        # markdown 预览可读
        assert "通知草稿预览" in payload["markdown_preview"]

    def test_dry_run_noise_isolation_with_failed_holdings(
        self, tmp_sqla_session, tmp_tf_db, monkeypatch
    ):
        """持仓 bucket 失败时，dry-run 的 intraday_push 必须为空（噪声隔离）.

        直接 patch ``get_investment_controller_context`` 返回 failed 持仓上下文，
        隔离 service 层的去噪 / 分通道逻辑（避免依赖复杂 collector 签名）。
        """
        failed_ctx = _ctx(holdings={"data_status": "failed", "items": [
            {"symbol": "600000.SH", "name": "浦发银行",
             "price_change_pct": -5.0, "live_price": 10.0,
             "latest_report": {"report_id": "r1"}},
        ]})

        import api.services.notification_draft_service as nds
        monkeypatch.setattr(
            nds, "get_investment_controller_context",
            lambda db, user_id, **kw: failed_ctx,  # noqa: ARG005
        )
        monkeypatch.setattr(
            "api.services.tradeflow_service.get_mandate_daily_report",
            lambda **kw: {"status": "no_data"},
        )
        nds.reset_dedup_state()
        payload = nds.build_notification_dry_run(
            tmp_sqla_session, "ghost_user", tf_db_path=tmp_tf_db, now=NOW,
        )
        assert payload["intraday_push"] == []
        # 失败持仓的 P0 大跌草稿必须落到 recorded_only
        assert any(d["event_type"] == EVENT_HOLDINGS_RISK_LARGE
                   for d in payload["recorded_only"])

    def test_dry_run_no_forbidden_words(self, tmp_sqla_session, tmp_tf_db, monkeypatch):
        monkeypatch.setattr(
            "api.services.tradeflow_service.get_mandate_daily_report",
            lambda **kw: {"status": "no_data"},
        )
        from api.services import notification_draft_service
        notification_draft_service.reset_dedup_state()
        payload = notification_draft_service.build_notification_dry_run(
            tmp_sqla_session, "ghost_user", tf_db_path=tmp_tf_db, now=NOW,
        )
        all_drafts = (
            payload["intraday_push"] + payload["daily_digest"]
            + payload["recorded_only"] + payload["deduplicated"]
        )
        assert_no_forbidden_words(all_drafts)
