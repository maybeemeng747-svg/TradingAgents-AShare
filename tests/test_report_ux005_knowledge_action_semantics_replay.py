# [REPORT-UX-005] knowledge_action_semantics_replay
"""REPORT-UX-005 — 本地知识补充不覆盖动作语义的扩展回放.

扩展 REPORT-UX-004 的回放范围，验证本地知识 / 研报关注度 / 半年报事实三组
解释性叠加字段不会把最终动作重新压成笼统的「观察」。

REPORT-UX-004 已覆盖 WAIT（数据不足观察）+ ENTER（条件入场）两类，重点证明
本地知识命中不会掩盖 DATA_MISSING wait_reason_codes。

REPORT-UX-005 在此基础上补齐 5 类 action semantics 全覆盖：

  | # | Scenario        | Action  | action_label  | KB hit          | half_year_facts   |
  |---|-----------------|---------|---------------|-----------------|-------------------|
  | W1| 数据不足 + KB+HY | WAIT    | 数据不足观察   | HAS_DATA 603296 | HAS_FACTS 603296  |
  | E1| 看多入场 + KB+HY | ENTER   | 条件入场      | HAS_DATA 603296 | HAS_FACTS 603296  |
  | H1| 持有 + KB+HY    | HOLD    | 持有          | HAS_DATA 603296 | HAS_FACTS 603296  |
  | R1| 减仓 + KB+HY    | REDUCE  | 条件减仓      | HAS_DATA 603296 | HAS_FACTS 603296  |
  | X1| 清仓 + KB+HY    | EXIT    | 条件清仓      | HAS_DATA 603296 | HAS_FACTS 603296  |

头条验收（任务 docs/TASKS.md REPORT-UX-005）：
  - 本地知识强命中不会把 ENTER/HOLD/REDUCE 覆写成 WAIT。
  - 数据不足时有明确 wait_reason_codes，不允许只剩笼统 action_label。

叠加字段（local_knowledge + research_attention + half_year_facts）全部以
``metadata.raw_evidence`` 条目形式注入，模拟 HY-004 接入后的报告管线。所有
叠加字段必须遵守 additive-only 契约：只增解释性 key，不动 decision /
execution_action / action_label / wait_reason_codes / data_blockers。

执行约束：
  - 不调用 live LLM。
  - 不写生产 DB（使用 in-memory SQLite）。
  - 不改 prompts。
  - fixture 全部 inline，知识库在 tmp_path 下构建。
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, List

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.database import Base
from api.services import report_service
from tests.test_kb007_research_attention import (
    _COMPANY_PAGE_A,
    _COMPANY_PAGE_B,
    _EXPIRED_PAGE,
    _FUND_PAGE,
    _HK_PAGE,
    _NO_SYMBOLS_PAGE,
    _SCORE_TABLE_PAGE,
    _UNLISTED_PAGE,
    _US_TODO_PAGE,
)
from tradingagents.dataflows.half_year_facts_provider import (
    STATUS_HAS_DATA as HY_STATUS_HAS_FACTS,
    build_half_year_facts_raw_evidence_entry,
    query_half_year_facts,
)
from tradingagents.dataflows.local_knowledge_audit import INVESTMENT_SUBDIR
from tradingagents.dataflows.local_knowledge_provider import (
    STATUS_HAS_DATA as LK_STATUS_HAS_DATA,
    build_raw_evidence_entry,
    query_local_knowledge,
)
from tradingagents.graph.signal_processing import (
    WAIT_REASON_DATA_MISSING,
)


# ── Inline helpers ────────────────────────────────────────────────────────


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# 华勤技术 603296 半年报事实页（qualified），用于 half_year_facts overlay。
# report_type=半年报 → 进入 HY-003 provider 命中范围；financial_period + facts
# 齐全 → data_status=fresh → HAS_FACTS。
_HUAQIN_HALF_YEAR_PAGE = """---
title: 华勤技术603296-2025H1半年报
created: 2026-08-29
updated: 2026-08-29
sources:
  - "[[../../raw/2026-08-29-华勤技术-2025H1.md|公司公告-2025H1]]"
tags: [华勤技术, 半年报, 2025H1]
symbols: ["603296.SH 华勤技术"]
themes: [AI服务器, ODM]
industry_chain_roles: [AI服务器ODM]
report_type: 半年报
evidence_level: A
valid_until: 2099-12-31
source_quality: 高
stale_risk: 低
financial_period: 2025H1
disclosure_date: 2026-08-29
source_type: [exchange_filing, fact_table, management_commentary]
financial_facts:
  - 营收 150.2亿 (+30.1% YoY)
  - 归母净利 8.5亿 (+45.0% YoY)
segment_facts:
  - AI服务器ODM营收占比提升
management_commentary:
  - 上调全年AI服务器出货指引
forward_guidance:
  - 下半年毛利率企稳
risk_factors: [客户集中度, 上游GPU供应]
source_links:
  - 巨潮资讯 <公告URL>
---

# 华勤技术（603296）— 2025H1 财报

## 一句话总结

2025H1 营收 150.2亿，同比 +30.1%，AI服务器放量。

## 投资逻辑

- AI服务器ODM放量，营收高增。

## 风险提示

- 客户集中度风险。

## 原始资料

- 公司公告。
"""


@pytest.fixture()
def fixture_kb(tmp_path: Path) -> Path:
    """扩展版知识库：KB-007 同款页面 + 华勤技术半年报事实页。

    命中映射：
      - 603296 / 华勤技术 → local_knowledge HAS_DATA（3 页命中）
                          + half_year_facts HAS_FACTS（qualified 半年报页）
      - 600000 / 浦发银行 → STALE（local_knowledge）
      - DELL.US → LOW_CONFIDENCE（local_knowledge）
      - 999999 → NORMAL_NO_DATA
    """
    inv = tmp_path / INVESTMENT_SUBDIR
    _write(inv / "华勤技术603296-超节点.md", _COMPANY_PAGE_A)
    _write(inv / "AI算力基础设施-公司评分表.md", _SCORE_TABLE_PAGE)
    _write(inv / "华勤技术-深度研究.md", _COMPANY_PAGE_B)
    _write(inv / "华勤技术603296-2025H1半年报.md", _HUAQIN_HALF_YEAR_PAGE)
    _write(inv / "腾讯控股-游戏复苏.md", _HK_PAGE)
    _write(inv / "Dell-FQ127-待补充.md", _US_TODO_PAGE)
    _write(inv / "沪深300ETF-指数跟踪.md", _FUND_PAGE)
    _write(inv / "某私募主体-调研纪要.md", _UNLISTED_PAGE)
    _write(inv / "某周期股-已过期.md", _EXPIRED_PAGE)
    _write(inv / "行业综述-无标的.md", _NO_SYMBOLS_PAGE)
    return tmp_path


def _make_db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    SessionLocal = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    return db, engine


# A realistic OHLCV CSV (>50 chars) so ``ohlcv_5d`` is HAS_DATA — 用于方向性
# 场景（ENTER/HOLD/REDUCE/EXIT），避免误触发 DATA_MISSING。
_OHLCV_HAS_DATA = (
    "date,open,high,low,close,volume,amount\n"
    "2026-06-18,10.00,10.42,9.91,10.28,128000,1315840\n"
    "2026-06-19,10.28,10.35,10.02,10.11,153000,1546830\n"
    "2026-06-20,10.11,10.50,10.05,10.44,201000,2102240\n"
    "2026-06-21,10.44,10.60,10.30,10.52,176000,1847520\n"
    "2026-06-24,10.52,10.71,10.45,10.66,142000,1513720\n"
)

# ── raw_evidence 模板 ─────────────────────────────────────────────────────

# 数据不足场景：ohlcv 有数据，但主力资金查询失败。
_DATA_MISSING_RAW_EVIDENCE = {
    "stock_data": _OHLCV_HAS_DATA,
    "fund_flow_individual": "主力资金获取失败：AKShare timeout，Eastmoney push2 返回 502",
    "lhb": "龙虎榜：未上榜，非异动日无数据",
}

# 数据齐全场景：所有关键字段都有数据，不触发 DATA_MISSING。
_DATA_OK_RAW_EVIDENCE = {
    "stock_data": _OHLCV_HAS_DATA,
    "fund_flow_individual": "主力资金净流入 1.2 亿",
    "lhb": "龙虎榜：机构净买入 5000 万",
}


# ── 三组叠加字段注入 ─────────────────────────────────────────────────────


def _inject_knowledge_overlays(
    raw_evidence: Dict[str, Any],
    *,
    kb_root: str,
    symbol: str,
    trade_date: str = "2026-07-11",
) -> Dict[str, Any]:
    """把 local_knowledge + half_year_facts 注入到 raw_evidence 中。

    模拟 HY-004 接入后的报告管线：data_collector 把本地知识与半年报事实
    作为 raw_evidence 条目写入 metadata，report_service 只做渲染/透传，
    不改动作语义。
    """
    enriched = copy.deepcopy(raw_evidence)

    # local_knowledge（KB-003）
    lk_result = query_local_knowledge(kb_root, symbol=symbol)
    enriched["local_knowledge"] = build_raw_evidence_entry(
        lk_result, trade_date=trade_date, fetched_at=trade_date + "T09:30:00Z"
    )

    # half_year_facts（HY-003）
    hy_result = query_half_year_facts(kb_root, symbol=symbol)
    enriched["half_year_facts"] = build_half_year_facts_raw_evidence_entry(
        hy_result, trade_date=trade_date, fetched_at=trade_date + "T09:30:00Z"
    )

    return enriched


# ── Buy/Risk Level 文本块 ─────────────────────────────────────────────────

_DATA_MISSING_FINAL_DECISION = (
    "结论：主力资金证据缺失，叠加龙虎榜未上榜，本轮不给出强方向。\n"
    "## 交易建议\n"
    "- Buy Level: 1（证据不足，不入场）\n"
    "- Risk Level: 2（关注关键支撑 9.80）\n"
)

_ENTER_FINAL_DECISION = (
    "结论：基本面与资金面共振，等待突破 10.80 后入场。\n"
    "## 交易建议\n"
    "- Buy Level: 3（条件入场）\n"
    "- Risk Level: 2（止损 9.80）\n"
)

_HOLD_FINAL_DECISION = (
    "结论：已持仓，趋势未破，继续持有。\n"
    "## 交易建议\n"
    "- Buy Level: 2（持有不加仓）\n"
    "- Risk Level: 2（止损 9.50）\n"
)

_REDUCE_FINAL_DECISION = (
    "结论：板块退潮信号增多，跌破 10.20 减仓至核心仓。\n"
    "## 交易建议\n"
    "- Buy Level: 1（减仓优先）\n"
    "- Risk Level: 3（止损 10.20）\n"
)

_EXIT_FINAL_DECISION = (
    "结论：逻辑证伪，放量破位，清仓退出。\n"
    "## 交易建议\n"
    "- Buy Level: 0（清仓退出）\n"
    "- Risk Level: 4（止损 9.90）\n"
)


def _build_result_data(scenario: Dict[str, Any], kb_root: str) -> Dict[str, Any]:
    """Build a result_data payload with knowledge overlays injected."""
    base_raw_evidence = _inject_knowledge_overlays(
        copy.deepcopy(scenario["raw_evidence"]),
        kb_root=kb_root,
        symbol=scenario["symbol"],
    )
    return {
        "market_report": "行情报告占位",
        "smart_money_report": "资金面报告占位",
        "news_report": "新闻/公告报告占位",
        "final_trade_decision": scenario["final_trade_decision"],
        "research_direction": scenario.get("research_direction"),
        "execution_action": scenario.get("execution_action"),
        "action_label": scenario.get("action_label"),
        "metadata": {"raw_evidence": base_raw_evidence},
    }


# ── Replay scenarios (5 action semantics) ─────────────────────────────────

# W1: 数据不足观察 + 本地知识命中 + 半年报事实命中。
# 必须保留 wait_reason_codes=[DATA_MISSING]，KB/HY 命中不得掩盖数据缺口。
SCENARIO_WAIT = {
    "id": "w1_wait__kb_hy",
    "symbol": "603296.SH",
    "name": "华勤技术",
    "description": "数据不足观察 + 本地知识命中 + 半年报事实命中",
    "decision": "HOLD",
    "action_label": "数据不足观察",
    "research_direction": "中性",
    "execution_action": "WAIT",
    "final_trade_decision": _DATA_MISSING_FINAL_DECISION,
    "raw_evidence": _DATA_MISSING_RAW_EVIDENCE,
    "expected_wait_codes_contains": [WAIT_REASON_DATA_MISSING],
    "expects_wait_codes_nonempty": True,
}

# E1: 看多入场 + 本地知识命中 + 半年报事实命中。
# 强 KB/HY 命中不得把 ENTER 覆写成 WAIT。
SCENARIO_ENTER = {
    "id": "e1_enter__kb_hy",
    "symbol": "603296.SH",
    "name": "华勤技术",
    "description": "看多入场 + 本地知识命中 + 半年报事实命中",
    "decision": "BUY",
    "action_label": "条件入场",
    "research_direction": "看多",
    "execution_action": "ENTER",
    "final_trade_decision": _ENTER_FINAL_DECISION,
    "raw_evidence": _DATA_OK_RAW_EVIDENCE,
    "expected_wait_codes_contains": [],
    "expects_wait_codes_nonempty": False,
}

# H1: 持有 + 本地知识命中 + 半年报事实命中。
# 强 KB/HY 命中不得把 HOLD 覆写成 WAIT。
SCENARIO_HOLD = {
    "id": "h1_hold__kb_hy",
    "symbol": "603296.SH",
    "name": "华勤技术",
    "description": "持有 + 本地知识命中 + 半年报事实命中",
    "decision": "HOLD",
    "action_label": "持有",
    "research_direction": "看多",
    "execution_action": "HOLD",
    "final_trade_decision": _HOLD_FINAL_DECISION,
    "raw_evidence": _DATA_OK_RAW_EVIDENCE,
    "expected_wait_codes_contains": [],
    "expects_wait_codes_nonempty": False,
}

# R1: 减仓 + 本地知识命中 + 半年报事实命中。
# 强 KB/HY 命中不得把 REDUCE 覆写成 WAIT。
SCENARIO_REDUCE = {
    "id": "r1_reduce__kb_hy",
    "symbol": "603296.SH",
    "name": "华勤技术",
    "description": "减仓 + 本地知识命中 + 半年报事实命中",
    "decision": "SELL",
    "action_label": "条件减仓",
    "research_direction": "偏空",
    "execution_action": "REDUCE",
    "final_trade_decision": _REDUCE_FINAL_DECISION,
    "raw_evidence": _DATA_OK_RAW_EVIDENCE,
    "expected_wait_codes_contains": [],
    "expects_wait_codes_nonempty": False,
}

# X1: 清仓 + 本地知识命中 + 半年报事实命中。
# 强 KB/HY 命中不得把 EXIT 覆写成 WAIT。
SCENARIO_EXIT = {
    "id": "x1_exit__kb_hy",
    "symbol": "603296.SH",
    "name": "华勤技术",
    "description": "清仓 + 本地知识命中 + 半年报事实命中",
    "decision": "SELL",
    "action_label": "条件清仓",
    "research_direction": "看空",
    "execution_action": "EXIT",
    "final_trade_decision": _EXIT_FINAL_DECISION,
    "raw_evidence": _DATA_OK_RAW_EVIDENCE,
    "expected_wait_codes_contains": [],
    "expects_wait_codes_nonempty": False,
}

SCENARIOS = [
    SCENARIO_WAIT,
    SCENARIO_ENTER,
    SCENARIO_HOLD,
    SCENARIO_REDUCE,
    SCENARIO_EXIT,
]

# 非 WAIT 场景（用于头条验收：强 KB/HY 命中不得覆写动作语义）。
DIRECTIONAL_SCENARIOS = [
    SCENARIO_ENTER,
    SCENARIO_HOLD,
    SCENARIO_REDUCE,
    SCENARIO_EXIT,
]


# ── 1. Core replay: action gate preserved for all 5 semantics ────────────


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
def test_replay_action_gate_preserved(scenario, fixture_kb: Path, monkeypatch):
    """[REPORT-UX-005] 三组叠加字段（local_knowledge + research_attention +
    half_year_facts）注入后，5 类 action semantics 的 decision /
    action_label / research_direction / execution_action 必须逐字保留。"""
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
    db, engine = _make_db()
    try:
        report = report_service.create_report(
            db=db,
            symbol=scenario["symbol"],
            trade_date="2026-07-11",
            decision=scenario["decision"],
            result_data=_build_result_data(scenario, str(fixture_kb)),
        )

        assert report.decision == scenario["decision"]
        assert report.action_label == scenario["action_label"]
        assert report.research_direction == scenario["research_direction"]
        assert report.execution_action == scenario["execution_action"]

        # result_data copies also unchanged.
        assert report.result_data["action_label"] == scenario["action_label"]
        assert report.result_data["research_direction"] == scenario["research_direction"]
        assert report.result_data["execution_action"] == scenario["execution_action"]

        # Buy Level / Risk Level text survives intact.
        assert "Buy Level:" in report.final_trade_decision
        assert "Risk Level:" in report.final_trade_decision
    finally:
        db.close()
        engine.dispose()


# ── 2. wait_reason_codes: WAIT has codes, non-WAIT empty ──────────────────


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
def test_replay_wait_reason_codes_contract(scenario, fixture_kb: Path, monkeypatch):
    """[REPORT-UX-005] wait_reason_codes 契约：

    - WAIT + 数据不足观察 → wait_reason_codes 非空（必须含 DATA_MISSING）。
      不允许只剩笼统 action_label 而无原因码。
    - 非 WAIT（ENTER/HOLD/REDUCE/EXIT）→ wait_reason_codes 为空。
      叠加字段不得为非 WAIT 动作凭空捏造 WAIT 原因。
    """
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
    db, engine = _make_db()
    try:
        report = report_service.create_report(
            db=db,
            symbol=scenario["symbol"],
            trade_date="2026-07-11",
            decision=scenario["decision"],
            result_data=_build_result_data(scenario, str(fixture_kb)),
        )

        codes = report.result_data.get("wait_reason_codes") or []

        if scenario["expects_wait_codes_nonempty"]:
            assert codes, (
                f"WAIT scenario {scenario['id']} produced empty wait_reason_codes "
                f"— 数据不足观察 must carry explicit reason codes, not a vague label"
            )
            for expected_code in scenario["expected_wait_codes_contains"]:
                assert expected_code in codes, (
                    f"expected {expected_code} in wait_reason_codes, got {codes}"
                )
        else:
            assert codes == [], (
                f"non-WAIT action {scenario['execution_action']} "
                f"({scenario['id']}) must have empty wait_reason_codes, "
                f"got {codes} — KB/HY overlay invented a WAIT reason"
            )
    finally:
        db.close()
        engine.dispose()


# ── 3. Headline: KB+HY strong hit must NOT overwrite directional actions ─


@pytest.mark.parametrize(
    "scenario", DIRECTIONAL_SCENARIOS, ids=[s["id"] for s in DIRECTIONAL_SCENARIOS]
)
def test_headline_kb_hy_hit_does_not_overwrite_directional_action(
    scenario, fixture_kb: Path, monkeypatch
):
    """[REPORT-UX-005] 头条验收：本地知识强命中 + 半年报事实命中不会把
    ENTER/HOLD/REDUCE 覆写成 WAIT。

    这是最重要的回归保护：当 603296（华勤技术）同时有 3 页本地知识命中 +
    qualified 半年报事实页时，方向性结论（看多入场 / 持有 / 减仓 / 清仓）
    必须原样保留，叠加字段只能作为背景信息渲染，不得改变动作门禁。
    """
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
    db, engine = _make_db()
    try:
        report = report_service.create_report(
            db=db,
            symbol=scenario["symbol"],
            trade_date="2026-07-11",
            decision=scenario["decision"],
            result_data=_build_result_data(scenario, str(fixture_kb)),
        )

        # 1. The action gate is intact — not collapsed to WAIT/观察.
        assert report.execution_action == scenario["execution_action"]
        assert report.execution_action != "WAIT"
        assert report.action_label == scenario["action_label"]
        assert report.action_label != "数据不足观察"
        assert report.action_label != "观望"

        # 2. The KB section DID render — so the test is meaningful (proves
        # the strong hit was present and still did not override the action).
        block = report.result_data.get("local_knowledge_block") or ""
        assert block, (
            "expected 华勤技术 KB hit to populate local_knowledge_block "
            "for the directional overlay test to be meaningful"
        )

        # 3. half_year_facts survived in raw_evidence (HY-004 forward-compat).
        raw_ev = (
            report.result_data.get("metadata", {}).get("raw_evidence", {})
            if isinstance(report.result_data.get("metadata"), dict)
            else {}
        )
        hy_entry = raw_ev.get("half_year_facts")
        assert isinstance(hy_entry, dict), (
            "half_year_facts raw_evidence entry must survive the report pipeline"
        )
        assert hy_entry.get("status") == HY_STATUS_HAS_FACTS, (
            f"half_year_facts status expected HAS_DATA/HAS_FACTS, "
            f"got {hy_entry.get('status')}"
        )

        # 4. wait_reason_codes empty for directional actions.
        codes = report.result_data.get("wait_reason_codes") or []
        assert codes == [], (
            f"directional action {scenario['execution_action']} must not "
            f"carry wait_reason_codes, got {codes}"
        )
    finally:
        db.close()
        engine.dispose()


# ── 4. half_year_facts overlay is additive-only ──────────────────────────


def test_half_year_facts_overlay_is_additive_only(
    fixture_kb: Path, monkeypatch
):
    """[REPORT-UX-005] half_year_facts 叠加字段必须遵守 additive-only 契约：

    与 local_knowledge（REPORT-UX-004 test_attach_report_local_knowledge_is_additive_only）
    保持一致——half_year_facts 以 raw_evidence 条目形式存在，不进入
    create_report 的改写路径，所有 pre-existing key 逐字保留。
    """
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))

    base = _build_result_data(SCENARIO_ENTER, str(fixture_kb))
    base = report_service.attach_report_data_blockers(base)
    resolved = report_service.resolve_report_fields(result_data=base)
    base = report_service.attach_report_wait_reason_codes(
        base, wait_reason_codes=resolved.get("wait_reason_codes")
    )
    pre_attach_keys = set(base.keys())
    pre_attach_snapshot = {k: copy.deepcopy(v) for k, v in base.items()}

    enriched = report_service.attach_report_local_knowledge(
        copy.deepcopy(base), symbol="603296"
    )

    # Original keys all preserved.
    for key in pre_attach_keys:
        assert key in enriched, f"attach dropped pre-existing key: {key}"

    # Original values are byte-for-byte identical (additive only).
    for key, value in pre_attach_snapshot.items():
        assert enriched[key] == value, (
            f"attach mutated pre-existing value for key: {key}"
        )

    # half_year_facts survives in raw_evidence (not wiped by KB attach).
    raw_ev = enriched.get("metadata", {}).get("raw_evidence", {})
    assert "half_year_facts" in raw_ev, (
        "half_year_facts raw_evidence entry must survive attach_report_local_knowledge"
    )

    # The strong action gate survived.
    assert enriched["action_label"] == "条件入场"
    assert enriched["execution_action"] == "ENTER"
    assert (enriched.get("wait_reason_codes") or []) == []


# ── 5. KB + HY overlay renders for all 5 semantics ───────────────────────


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
def test_replay_local_knowledge_block_renders_for_all_semantics(
    scenario, fixture_kb: Path, monkeypatch
):
    """[REPORT-UX-005] local_knowledge_block 对全部 5 类 action semantics
    均渲染命中（华勤技术 HAS_DATA），但渲染结果不随动作变化——叠加字段是
    背景信息，不是动作决定因子。"""
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
    db, engine = _make_db()
    try:
        report = report_service.create_report(
            db=db,
            symbol=scenario["symbol"],
            trade_date="2026-07-11",
            decision=scenario["decision"],
            result_data=_build_result_data(scenario, str(fixture_kb)),
        )

        block = report.result_data.get("local_knowledge_block") or ""
        summary = report.result_data.get("local_knowledge_summary")

        assert block, (
            f"expected non-empty local_knowledge_block for {scenario['id']}"
        )
        assert isinstance(summary, dict)
        assert summary.get("status") == LK_STATUS_HAS_DATA

        # The KB block presence must NOT change the action_label.
        assert report.action_label == scenario["action_label"]

        # half_year_facts present in raw_evidence for all semantics.
        raw_ev = (
            report.result_data.get("metadata", {}).get("raw_evidence", {})
            if isinstance(report.result_data.get("metadata"), dict)
            else {}
        )
        hy_entry = raw_ev.get("half_year_facts")
        assert isinstance(hy_entry, dict)
        assert hy_entry.get("status") == HY_STATUS_HAS_FACTS
    finally:
        db.close()
        engine.dispose()


# ── 6. ReportResponse schema round-trip with all overlays ────────────────


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
def test_replay_response_schema_with_overlays(scenario, fixture_kb: Path, monkeypatch):
    """[REPORT-UX-005] ReportResponse.model_validate + model_dump 在三组叠加
    字段同时存在时仍能 round-trip，不丢 KB / research_attention 字段，
    不破坏动作门禁。"""
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
    db, engine = _make_db()
    try:
        report = report_service.create_report(
            db=db,
            symbol=scenario["symbol"],
            trade_date="2026-07-11",
            decision=scenario["decision"],
            result_data=_build_result_data(scenario, str(fixture_kb)),
        )

        from api.main import (
            ReportResponse,
            _attach_report_data_blockers_for_response,
        )

        _attach_report_data_blockers_for_response(report)
        resp = ReportResponse.model_validate(report)
        dumped = resp.model_dump()

        # Strong action gate.
        assert dumped["action_label"] == scenario["action_label"]
        assert dumped["execution_action"] == scenario["execution_action"]

        # KB / research_attention fields declared on ReportResponse.
        for key in (
            "local_knowledge_block",
            "local_knowledge_summary",
            "research_attention_score",
            "knowledge_theme_count",
            "research_attention_summary",
            "research_attention_block",
        ):
            assert key in dumped, f"ReportResponse dropped KB field: {key}"

        # KB block non-empty for HAS_DATA scenario.
        assert dumped["local_knowledge_block"]
        assert isinstance(dumped["local_knowledge_summary"], dict)

        # wait_reason_codes contract.
        codes = dumped.get("wait_reason_codes") or []
        if scenario["expects_wait_codes_nonempty"]:
            assert codes, (
                f"WAIT scenario {scenario['id']} must surface wait_reason_codes "
                f"at top level, not just a vague action_label"
            )
        else:
            assert codes == []
    finally:
        db.close()
        engine.dispose()


# ── 7. Replay report generator ───────────────────────────────────────────


def test_generate_replay_report(fixture_kb: Path, monkeypatch, tmp_path: Path):
    """[REPORT-UX-005] 生成简短回放报告，覆盖 5 类 action semantics 的
    动作门禁 / wait_reason_codes / KB 命中 / half_year_facts 状态。

    报告必须能回答任务验收要求的三个问题：
      1. 本地知识强命中是否把 ENTER/HOLD/REDUCE 覆写成 WAIT？（答：否）
      2. 数据不足时是否有明确 wait_reason_codes？（答：是，DATA_MISSING）
      3. 叠加字段是否破坏报告 schema round-trip？（答：否）
    """
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))

    rows: List[str] = []
    rows.append("# REPORT-UX-005 回放报告")
    rows.append("")
    rows.append("> 本地知识 / 研报关注度 / 半年报事实叠加字段不覆盖动作语义的扩展回放。")
    rows.append("")
    rows.append("| # | Scenario | Action | action_label | KB | half_year_facts | wait_reason_codes |")
    rows.append("|---|----------|--------|--------------|----|-----------------|-------------------|")

    all_pass = True
    for idx, scenario in enumerate(SCENARIOS, 1):
        db, engine = _make_db()
        try:
            monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
            report = report_service.create_report(
                db=db,
                symbol=scenario["symbol"],
                trade_date="2026-07-11",
                decision=scenario["decision"],
                result_data=_build_result_data(scenario, str(fixture_kb)),
            )

            action_ok = report.execution_action == scenario["execution_action"]
            label_ok = report.action_label == scenario["action_label"]
            codes = report.result_data.get("wait_reason_codes") or []
            codes_ok = bool(codes) == scenario["expects_wait_codes_nonempty"]
            block = report.result_data.get("local_knowledge_block") or ""
            kb_ok = bool(block)

            raw_ev = (
                report.result_data.get("metadata", {}).get("raw_evidence", {})
                if isinstance(report.result_data.get("metadata"), dict)
                else {}
            )
            hy_status = (raw_ev.get("half_year_facts") or {}).get("status", "?")

            row_ok = action_ok and label_ok and codes_ok and kb_ok
            all_pass = all_pass and row_ok

            mark = "PASS" if row_ok else "FAIL"
            codes_str = ",".join(codes) if codes else "(empty)"
            rows.append(
                f"| {idx} | {mark} {scenario['id']} | {scenario['execution_action']} "
                f"| {scenario['action_label']} | {LK_STATUS_HAS_DATA if kb_ok else 'MISS'} "
                f"| {hy_status} | {codes_str} |"
            )
        finally:
            db.close()
            engine.dispose()

    rows.append("")
    rows.append(f"**总体结论：{'全部通过' if all_pass else '存在失败'}**")
    report_text = "\n".join(rows)

    # Write to tmp_path (the artifact is also persisted separately by the task
    # run, but this proves the generator works end-to-end).
    out = tmp_path / "report-ux-005-replay-report.md"
    out.write_text(report_text, encoding="utf-8")
    assert out.exists()
    assert "REPORT-UX-005 回放报告" in report_text
    assert all_pass, "replay report found at least one failing scenario"
