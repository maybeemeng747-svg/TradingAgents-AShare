# [HY-008] half_year_e2e_acceptance
"""HY-008 — 半年报知识链路端到端回放验收（P2）.

验收 Tree Work 半年报 wiki → TA 报告（HY-004）→ TradeFlow 候选（HY-006）→
investment-controller briefing（HY-007）的整条链路，确保事实/来源/状态/去噪
在四层之间一致，且半年报知识**只作背景证据**——不覆盖数据不足原因、不改变
最终动作语义、不输出强买卖词。

四个回放场景（对应 docs/TASKS.md HY-008 实现要点 1）：

| 场景              | HY-003 命中   | HY-005 反证    | HY-006 分数 | HY-007 提醒        |
|-------------------|---------------|----------------|-------------|--------------------|
| S1 事实支持        | HAS_FACTS     | supported      | +1.0        | fact_update (P2)   |
| S2 事实削弱        | HAS_FACTS     | weakened       | -0.5        | rebuttal_alert (P2)|
| S3 事实打脸        | HAS_FACTS     | contradicted   | -3.0        | rebuttal_alert (P2)|
| S4 无半年报        | NO_DATA       | insufficient   | 0.0         | （无提醒，不刷屏）  |

执行约束（对应 HY-008 任务约束）：
  - 全程 fixture/dry-run，**禁止 live LLM**。
  - 不写生产 ``tradingagents.db``（TA 报告回放使用内存 SQLite）。
  - 不修改 ``tradingagents/prompts/``。
  - 重点检查字段、来源、状态、去噪和不越权。

链路覆盖（每个场景都跑完 5 段）::

    [A] HY-003 query_half_year_facts        → HalfYearFactsQueryResult
    [B] KB-015 build_research_fact_opinion_index + HY-005 check_thesis_against_facts
                                            → ThesisFactCheckResult
    [C] HY-006 compute_half_year_factor_score
                            + tradeflow _enrich_candidate_with_half_year
                                            → 候选 half_year_fact_score / 降权原因
    [D] HY-004 report_service.attach_report_half_year_facts
                                            → half_year_facts_block / status
    [E] HY-007 _collect_half_year_facts + build_half_year_reminders
                                            → IC briefing reminder_type / priority
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, List

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.database import Base
from api.services import report_service, tradeflow_service
from tradingagents.dataflows.half_year_factor_score import (
    STATUS_FAILED as HY6_FAILED,
)
from tradingagents.dataflows.half_year_factor_score import (
    STATUS_HAS_FACTS as HY6_HAS_FACTS,
)
from tradingagents.dataflows.half_year_factor_score import (
    STATUS_NO_FACTS as HY6_NO_FACTS,
)
from tradingagents.dataflows.half_year_factor_score import (
    compute_half_year_factor_score,
    has_forbidden_action_words,
)
from tradingagents.dataflows.half_year_facts_provider import (
    query_half_year_facts,
)
from tradingagents.dataflows.research_fact_opinion_index import (
    build_research_fact_opinion_index,
)
from tradingagents.dataflows.thesis_fact_check import (
    STATUS_CONTRADICTED,
    STATUS_INSUFFICIENT_DATA,
    STATUS_SUPPORTED,
    STATUS_WEAKENED,
    check_thesis_against_facts,
)
from tradingagents.graph.signal_processing import WAIT_REASON_DATA_MISSING

# [HY-008] half_year_e2e_acceptance — task code tag.


# ── 强动作词防线（与 HY-005/006/007 一致）─────────────────────────────

_FORBIDDEN_ACTION_WORDS = (
    "买入", "卖出", "加仓", "减仓", "立即买入", "立即卖出",
    "全仓", "满仓", "清仓", "止损", "建仓", "强烈推荐",
    "BUY", "SELL", "strong buy", "strong sell",
)


def _assert_no_strong_action_words(text: str) -> None:
    for forbidden in _FORBIDDEN_ACTION_WORDS:
        assert forbidden not in text, (
            f"text contains forbidden word {forbidden!r}: {text!r}"
        )


# ── 内联 fixture 页面（4 场景，完全自洽，不依赖 ~/Documents/knowledge）──

_INVESTMENT = "wiki/investment"


def _write_page(root: Path, filename: str, content: str) -> None:
    inv = root / _INVESTMENT
    inv.mkdir(parents=True, exist_ok=True)
    (inv / filename).write_text(content, encoding="utf-8")


# S1 — 事实支持：浪潮信息 AI 服务器放量，公告营收 +60%，研报观点同向。
_S1_HALF_YEAR = """---
title: 浪潮信息000977-2025H1半年报
symbols: ["000977.SZ 浪潮信息"]
report_type: 半年报
evidence_level: A
valid_until: 2099-12-31
stale_risk: 低
financial_period: 2025H1
disclosure_date: 2026-08-29
source_type: [exchange_filing, fact_table]
financial_facts:
  - 营收 420.4亿 (+60.0% YoY)
  - 归母净利 12.5亿 (+45.2% YoY)
segment_facts:
  - AI服务器营收占比提升
risk_factors: [上游GPU供应, 客户集中度]
---

# 浪潮信息（000977）— 2025H1 财报

## 投资逻辑

- AI服务器整机放量，营收高增。

## 风险提示

- 上游GPU供应紧张。
- 客户集中度风险。
"""

_S1_OPINION = """---
title: 浪潮信息000977-研究观点
symbols: ["000977.SZ 浪潮信息"]
report_type: 深度研究
evidence_level: B
valid_until: 2099-12-31
stale_risk: 低
source_type: [broker_report]
---

# 浪潮信息深度

## 投资逻辑

- AI服务器业务将放量增长，营收高增
"""

# S2 — 事实削弱：海康威视 研报预期营收 +60%，公告实际 +20%（20 < 60×0.5）。
_S2_HALF_YEAR = """---
title: 海康威视002415-2025H1半年报
symbols: ["002415.SZ 海康威视"]
report_type: 半年报
evidence_level: A
valid_until: 2099-12-31
stale_risk: 低
financial_period: 2025H1
disclosure_date: 2026-08-29
source_type: [exchange_filing, fact_table]
financial_facts:
  - 营收 430.0亿 (+20.0% YoY)
risk_factors: [海外政策, 汇率]
---

# 海康威视（002415）— 2025H1 财报

## 投资逻辑

- 公告口径营收增长。

## 风险提示

- 海外政策风险。
"""

_S2_OPINION = """---
title: 海康威视002415-研究观点
symbols: ["002415.SZ 海康威视"]
report_type: 深度研究
evidence_level: B
valid_until: 2099-12-31
stale_risk: 低
source_type: [broker_report]
---

# 海康威视深度

## 投资逻辑

- 营收将爆发式增长 +60%
"""

# S3 — 事实打脸：宁德时代 研报看多储能爆发，公告储能营收 -20%、出货下滑。
_S3_HALF_YEAR = """---
title: 宁德时代300750-2025H1半年报
symbols: ["300750.SZ 宁德时代"]
report_type: 半年报
evidence_level: A
valid_until: 2099-12-31
stale_risk: 低
financial_period: 2025H1
disclosure_date: 2026-08-28
source_type: [exchange_filing, fact_table]
financial_facts:
  - 储能系统营收 120亿 (-20.0% YoY)
segment_facts:
  - 储能出货同比下滑
risk_factors: [储能增速不及预期, 原材料价格]
---

# 宁德时代（300750）— 2025H1 财报

## 投资逻辑

- 储能营收同比下滑（公告口径）。

## 风险提示

- 储能增速不及预期。
"""

_S3_OPINION = """---
title: 宁德时代300750-研究观点
symbols: ["300750.SZ 宁德时代"]
report_type: 深度研究
evidence_level: B
valid_until: 2099-12-31
stale_risk: 低
source_type: [broker_report]
---

# 宁德时代深度

## 投资逻辑

- 储能业务下半年将迎爆发式增长
"""

# S4 — 无半年报：平安银行只有公司点评页（非财报），不触发任何 HY 规则。
_S4_NON_FINANCIAL = """---
title: 平安银行000001-公司点评
symbols: ["000001.SZ 平安银行"]
report_type: 公司点评
evidence_level: A
valid_until: 2099-12-31
stale_risk: 低
---

# 平安银行（000001）

## 投资逻辑

- 零售业务转型推进。

## 风险提示

- 资产质量波动。
"""


# ── 场景定义 ──────────────────────────────────────────────────────────


def _build_s1_kb(tmp_path: Path) -> Path:
    root = tmp_path / "s1_supported"
    _write_page(root, "浪潮信息000977-2025H1半年报.md", _S1_HALF_YEAR)
    _write_page(root, "浪潮信息000977-研究观点.md", _S1_OPINION)
    return root


def _build_s2_kb(tmp_path: Path) -> Path:
    root = tmp_path / "s2_weakened"
    _write_page(root, "海康威视002415-2025H1半年报.md", _S2_HALF_YEAR)
    _write_page(root, "海康威视002415-研究观点.md", _S2_OPINION)
    return root


def _build_s3_kb(tmp_path: Path) -> Path:
    root = tmp_path / "s3_contradicted"
    _write_page(root, "宁德时代300750-2025H1半年报.md", _S3_HALF_YEAR)
    _write_page(root, "宁德时代300750-研究观点.md", _S3_OPINION)
    return root


def _build_s4_kb(tmp_path: Path) -> Path:
    root = tmp_path / "s4_no_half_year"
    _write_page(root, "平安银行000001-公司点评.md", _S4_NON_FINANCIAL)
    return root


#: 四场景的期望值（黄金基线，来源：probe 脚本实证）。
SCENARIOS: List[Dict[str, Any]] = [
    {
        "id": "s1_supported",
        "builder": _build_s1_kb,
        "symbol": "000977.SZ",
        "name": "浪潮信息",
        "description": "事实支持：研报看多 AI 服务器放量，公告营收 +60% 同向。",
        "expected_facts_status": "HAS_DATA",
        "expected_thesis_status": STATUS_SUPPORTED,
        "expected_hy6_status": HY6_HAS_FACTS,
        "expected_score_min": 0.5,        # supported → +1.0
        "expected_score_max": 1.5,
        "expected_has_downgrade": False,
        "expected_hy4_status": "HAS_FACTS",
        "expected_hy4_block_nonempty": True,
        "expected_reminder_type": "half_year_fact_update",
        "expected_reminder_present": True,
    },
    {
        "id": "s2_weakened",
        "builder": _build_s2_kb,
        "symbol": "002415.SZ",
        "name": "海康威视",
        "description": "事实削弱：研报预期营收 +60%，公告实际 +20%（不及预期一半）。",
        "expected_facts_status": "HAS_DATA",
        "expected_thesis_status": STATUS_WEAKENED,
        "expected_hy6_status": HY6_HAS_FACTS,
        "expected_score_min": -1.5,       # weakened_single → -0.5
        "expected_score_max": -0.1,
        "expected_has_downgrade": True,
        "expected_hy4_status": "HAS_FACTS",
        "expected_hy4_block_nonempty": True,
        "expected_reminder_type": "half_year_rebuttal_alert",
        "expected_reminder_present": True,
    },
    {
        "id": "s3_contradicted",
        "builder": _build_s3_kb,
        "symbol": "300750.SZ",
        "name": "宁德时代",
        "description": "事实打脸：研报看多储能爆发，公告储能营收 -20%、出货下滑。",
        "expected_facts_status": "HAS_DATA",
        "expected_thesis_status": STATUS_CONTRADICTED,
        "expected_hy6_status": HY6_HAS_FACTS,
        "expected_score_min": -4.0,       # contradicted (non-strong) → -2.0 ~ -3.0
        "expected_score_max": -1.5,
        "expected_has_downgrade": True,
        "expected_hy4_status": "HAS_FACTS",
        "expected_hy4_block_nonempty": True,
        "expected_reminder_type": "half_year_rebuttal_alert",
        "expected_reminder_present": True,
    },
    {
        "id": "s4_no_half_year",
        "builder": _build_s4_kb,
        "symbol": "000001.SZ",
        "name": "平安银行",
        "description": "无半年报：仅有公司点评页，不触发任何 HY 规则。",
        "expected_facts_status": "NORMAL_NO_DATA",
        "expected_thesis_status": STATUS_INSUFFICIENT_DATA,
        "expected_hy6_status": HY6_NO_FACTS,
        "expected_score_min": -0.01,      # no facts → 0.0
        "expected_score_max": 0.01,
        "expected_has_downgrade": False,
        "expected_hy4_status": "NO_DATA",
        "expected_hy4_block_nonempty": False,
        "expected_reminder_type": None,
        "expected_reminder_present": False,
    },
]


# ── TA 报告回放夹具（数据不足观察 + 半年报事实并存）──────────────────

_OHLCV_HAS_DATA = (
    "date,open,high,low,close,volume,amount\n"
    "2026-06-18,10.00,10.42,9.91,10.28,128000,1315840\n"
    "2026-06-19,10.28,10.35,10.02,10.11,153000,1546830\n"
    "2026-06-20,10.11,10.50,10.05,10.44,201000,2102240\n"
    "2026-06-21,10.44,10.60,10.30,10.52,176000,1847520\n"
    "2026-06-24,10.52,10.71,10.45,10.66,142000,1513720\n"
)

_DATA_MISSING_RAW_EVIDENCE = {
    "stock_data": _OHLCV_HAS_DATA,
    "fund_flow_individual": "主力资金获取失败：AKShare timeout，Eastmoney push2 返回 502",
    "lhb": "龙虎榜：未上榜，非异动日无数据",
}

_DATA_MISSING_FINAL_DECISION = (
    "结论：主力资金证据缺失，叠加龙虎榜未上榜，本轮不给出强方向。\n"
    "## 交易建议\n"
    "- Buy Level: 1（证据不足，不入场）\n"
    "- Risk Level: 2（关注关键支撑 9.80）\n"
)


def _make_db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    SessionLocal = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    return db, engine


def _build_data_missing_result_data(symbol: str) -> Dict[str, Any]:
    """构造一份"数据不足观察"的 result_data（fund_flow 失败）。"""
    return {
        "market_report": "行情报告占位",
        "smart_money_report": "资金面报告占位",
        "news_report": "新闻/公告报告占位",
        "final_trade_decision": _DATA_MISSING_FINAL_DECISION,
        "research_direction": "中性",
        "execution_action": "WAIT",
        "action_label": "数据不足观察",
        "metadata": {"raw_evidence": copy.deepcopy(_DATA_MISSING_RAW_EVIDENCE)},
    }


# ════════════════════════════════════════════════════════════════════
# 1. 端到端链路一致性：HY-003 → KB-015/HY-005 → HY-006 → HY-004 → HY-007
# ════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "scenario", SCENARIOS, ids=[s["id"] for s in SCENARIOS]
)
def test_e2e_half_year_knowledge_chain_consistency(
    scenario, tmp_path: Path, monkeypatch
):
    """[HY-008] 四场景全链路一致性：每层状态/分数/降权原因与黄金基线吻合。

    链路：HY-003 facts → HY-005 thesis → HY-006 score → HY-004 report block →
    HY-007 IC briefing。每一层都不得抛异常，且字段与场景预期一致。
    """
    kb_root = scenario["builder"](tmp_path)
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(kb_root))
    symbol = scenario["symbol"]

    # [A] HY-003 半年报事实查询。
    facts = query_half_year_facts(str(kb_root), symbol=symbol)
    assert facts.status == scenario["expected_facts_status"], (
        f"{scenario['id']}: HY-003 status={facts.status}, "
        f"expected={scenario['expected_facts_status']}"
    )

    # [B] KB-015 研报观点索引 + HY-005 观点-事实反证。
    opinions = build_research_fact_opinion_index(str(kb_root), symbol=symbol)
    thesis = check_thesis_against_facts(opinions, facts, symbol=symbol)
    assert thesis.thesis_check_status == scenario["expected_thesis_status"], (
        f"{scenario['id']}: HY-005 thesis={thesis.thesis_check_status}, "
        f"expected={scenario['expected_thesis_status']}; "
        f"flags={thesis.to_dict()['flags'][:2]}"
    )

    # [C] HY-006 半年报因子分（直接调用 + TradeFlow 候选注入两条路径）。
    score = compute_half_year_factor_score(facts, thesis)
    assert score["status"] == scenario["expected_hy6_status"]
    assert scenario["expected_score_min"] <= score["half_year_fact_score"] <= scenario["expected_score_max"], (
        f"{scenario['id']}: HY-006 score={score['half_year_fact_score']}, "
        f"expected range [{scenario['expected_score_min']}, {scenario['expected_score_max']}]"
    )
    has_downgrade = bool(score.get("downgrade_reasons"))
    assert has_downgrade == scenario["expected_has_downgrade"], (
        f"{scenario['id']}: downgrade_reasons={score.get('downgrade_reasons')}, "
        f"expected has_downgrade={scenario['expected_has_downgrade']}"
    )

    # [C'] TradeFlow 候选注入（_enrich_candidate_with_half_year 读同一 KB）。
    candidate_item = {
        "symbol": symbol,
        "name": scenario["name"],
        "candidate_type": "",
        "mandate_topic": "",
    }
    enriched = tradeflow_service._enrich_candidate_with_half_year(
        dict(candidate_item)
    )
    assert enriched["half_year_fact_score"] == pytest.approx(
        score["half_year_fact_score"], abs=1e-6
    ), (
        f"{scenario['id']}: TradeFlow enriched score "
        f"{enriched['half_year_fact_score']} != direct {score['half_year_fact_score']}"
    )
    # TradeFlow 候选字段必须齐全（前端渲染依赖）。
    for field in (
        "half_year_fact_score", "half_year_fact_summary",
        "half_year_risk_flags", "half_year_fact_detail",
        "needs_research_review",
    ):
        assert field in enriched, f"{scenario['id']}: TradeFlow 缺字段 {field}"

    # [D] HY-004 TA 报告区块（attach_report_half_year_facts）。
    result_data = _build_data_missing_result_data(symbol)
    attached = report_service.attach_report_half_year_facts(
        copy.deepcopy(result_data), symbol=symbol
    )
    assert attached["half_year_facts_status"] == scenario["expected_hy4_status"], (
        f"{scenario['id']}: HY-004 status={attached['half_year_facts_status']}, "
        f"expected={scenario['expected_hy4_status']}"
    )
    block = attached.get("half_year_facts_block") or ""
    if scenario["expected_hy4_block_nonempty"]:
        assert block, f"{scenario['id']}: expected non-empty HY-004 block"
    else:
        assert block == "", f"{scenario['id']}: NO_DATA 应返回空 block"

    # [E] HY-007 IC briefing（_collect_half_year_facts + build_half_year_reminders）。
    reminders = _build_reminders_for_symbol(scenario, kb_root, monkeypatch)
    if scenario["expected_reminder_present"]:
        assert reminders, f"{scenario['id']}: expected at least one reminder"
        types = {r["reminder_type"] for r in reminders}
        assert scenario["expected_reminder_type"] in types, (
            f"{scenario['id']}: reminder types={types}, "
            f"expected to contain {scenario['expected_reminder_type']!r}"
        )
    else:
        # 无半年报 → 不刷屏（无 fresh facts / rebuttal / needs_review）。
        assert reminders == [], (
            f"{scenario['id']}: 无半年报场景不应产生提醒，got {reminders}"
        )


@pytest.mark.parametrize(
    "scenario", SCENARIOS, ids=[s["id"] for s in SCENARIOS]
)
def test_e2e_no_strong_action_words_across_chain(
    scenario, tmp_path: Path, monkeypatch
):
    """[HY-008] 全链路输出文本不含强买卖词（防回归辅助）。

    覆盖：HY-005 summary / HY-006 summary+downgrade+risk_flags+hint /
    HY-004 block / HY-007 reminder reason + thesis_inline + markdown preview。
    """
    kb_root = scenario["builder"](tmp_path)
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(kb_root))
    symbol = scenario["symbol"]

    facts = query_half_year_facts(str(kb_root), symbol=symbol)
    opinions = build_research_fact_opinion_index(str(kb_root), symbol=symbol)
    thesis = check_thesis_against_facts(opinions, facts, symbol=symbol)
    score = compute_half_year_factor_score(facts, thesis)

    # HY-006 自检 + 逐字段扫描。
    assert has_forbidden_action_words(score) == [], (
        f"{scenario['id']}: HY-006 forbidden words: "
        f"{has_forbidden_action_words(score)}"
    )
    for s in thesis.summary:
        _assert_no_strong_action_words(s)

    # HY-004 报告区块。
    result_data = _build_data_missing_result_data(symbol)
    attached = report_service.attach_report_half_year_facts(
        copy.deepcopy(result_data), symbol=symbol
    )
    _assert_no_strong_action_words(attached.get("half_year_facts_block") or "")

    # HY-007 briefing reminders + markdown preview。
    reminders = _build_reminders_for_symbol(scenario, kb_root, monkeypatch)
    for r in reminders:
        _assert_no_strong_action_words(r.get("reason", ""))
        _assert_no_strong_action_words(r.get("thesis_inline", ""))

    # HY-007 pre_market markdown preview（含半年报区块）。
    from tradingagents.tradeflow.controller_briefing_payload import (
        build_pre_market_payload,
    )
    ctx = _build_minimal_ctx(symbol, scenario["name"], kb_root, monkeypatch)
    if ctx is not None:
        payload = build_pre_market_payload(ctx, as_of="2026-07-13 09:30:00")
        _assert_no_strong_action_words(payload.get("markdown_preview", ""))


# ════════════════════════════════════════════════════════════════════
# 2. 数据不足原因不被半年报覆盖（HY-008 实现要点 4 回归）
# ════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "scenario",
    [s for s in SCENARIOS if s["expected_hy4_status"] == "HAS_FACTS"],
    ids=[s["id"] for s in SCENARIOS if s["expected_hy4_status"] == "HAS_FACTS"],
)
def test_half_year_facts_do_not_mask_data_missing_reasons(
    scenario, tmp_path: Path, monkeypatch
):
    """[HY-008] 半年报事实命中时，数据不足原因（wait_reason_codes /
    data_blockers）与最终动作语义（action_label=数据不足观察）必须逐字保留。

    这是半年报知识链路的**核心隔离契约**：半年报事实只作背景证据，绝不替代
    缺失的行情/资金/公告数据，也不得把"数据不足观察"改成方向性动作。
    """
    kb_root = scenario["builder"](tmp_path)
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(kb_root))
    db, engine = _make_db()
    try:
        report = report_service.create_report(
            db=db,
            symbol=scenario["symbol"],
            trade_date="2026-07-13",
            decision="HOLD",
            result_data=_build_data_missing_result_data(scenario["symbol"]),
        )

        # 1. 半年报区块确实渲染了（让测试有意义）。
        assert report.result_data.get("half_year_facts_status") == "HAS_FACTS"
        assert report.result_data.get("half_year_facts_block"), (
            f"{scenario['id']}: expected HY block to render alongside data gap"
        )

        # 2. 数据不足原因被保留——这是半年报不得掩盖的核心。
        codes = report.result_data.get("wait_reason_codes") or []
        assert WAIT_REASON_DATA_MISSING in codes, (
            f"{scenario['id']}: DATA_MISSING 被 half_year_facts 命中掩盖"
        )

        # 3. data_blockers 仍报资金面失败。
        blockers = {
            b["key"]: b
            for b in (report.result_data.get("data_blockers") or [])
        }
        assert blockers["individual_fund_flow"]["status"] == "query_failed"

        # 4. 最终动作语义不变——半年报命中不得改成方向性动作。
        assert report.action_label == "数据不足观察"
        assert report.execution_action == "WAIT"

        # 5. Buy Level / Risk Level 文本逐字保留。
        assert "Buy Level:" in report.final_trade_decision
        assert "Risk Level:" in report.final_trade_decision
    finally:
        db.close()
        engine.dispose()


@pytest.mark.parametrize(
    "scenario", SCENARIOS, ids=[s["id"] for s in SCENARIOS]
)
def test_half_year_knowledge_never_overrides_action_gate(
    scenario, tmp_path: Path, monkeypatch
):
    """[HY-008] 半年报因子分（无论正负）不得改变 TradeFlow 候选的强动作门禁。

    覆盖三种情形：
      - S1 supported (+1.0)：不得把候选抬成主候选 / 强动作。
      - S3 contradicted (-3.0)：不得把候选改成"立即退出"等强动作。
      - S4 no_facts (0.0)：不得凭空生成动作。
    """
    kb_root = scenario["builder"](tmp_path)
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(kb_root))

    # 构造一个带原有 tier/action 的候选，半年报注入后这些字段必须不变。
    base_item = {
        "symbol": scenario["symbol"],
        "name": scenario["name"],
        "candidate_type": "",
        "mandate_topic": "",
        "tier": "C",
        "action": "watch",
        "action_tier": "scan",
    }
    enriched = tradeflow_service._enrich_candidate_with_half_year(dict(base_item))

    # 强动作门禁字段逐字保留。
    assert enriched["tier"] == "C"
    assert enriched["action"] == "watch"
    assert enriched["action_tier"] == "scan"

    # 半年报字段是**新增**字段，不覆盖原有键。
    assert "half_year_fact_score" in enriched
    assert "half_year_fact_summary" in enriched


# ════════════════════════════════════════════════════════════════════
# 3. 来源可追溯 + 字段完整（HY-008 验收方式：事实是什么、来源在哪里）
# ════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "scenario",
    [s for s in SCENARIOS if s["expected_hy4_status"] == "HAS_FACTS"],
    ids=[s["id"] for s in SCENARIOS if s["expected_hy4_status"] == "HAS_FACTS"],
)
def test_source_traceability_and_field_completeness(
    scenario, tmp_path: Path, monkeypatch
):
    """[HY-008] 每层都携带可追溯来源（vendor / rel_path / period），且字段完整。

    验收问题：事实是什么？来源在哪里？旧逻辑是否被支持/削弱？
    本测试用 HAS_FACTS 场景（S1/S2/S3）回答。
    """
    kb_root = scenario["builder"](tmp_path)
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(kb_root))
    symbol = scenario["symbol"]

    facts = query_half_year_facts(str(kb_root), symbol=symbol)
    # HY-003：每页都有 rel_path / period / source_type。
    for page in facts.pages:
        assert page.rel_path, "HY-003 page 缺 rel_path"
        assert page.financial_period, "HY-003 page 缺 financial_period"
        assert page.source_type, "HY-003 page 缺 source_type"
        assert "investment" in page.rel_path or "wiki" in page.rel_path

    opinions = build_research_fact_opinion_index(str(kb_root), symbol=symbol)
    thesis = check_thesis_against_facts(opinions, facts, symbol=symbol)

    # HY-005：反证 flag 带 opinion_text + fact_text + metric_key（可追溯）。
    checked_flags = [
        f for f in thesis.flags
        if f.check_status != STATUS_INSUFFICIENT_DATA
    ]
    assert checked_flags, f"{scenario['id']}: expected at least one checked flag"
    for flag in checked_flags:
        assert flag.opinion_text, "HY-005 flag 缺 opinion_text"
        assert flag.metric_key, "HY-005 flag 缺 metric_key"
        assert flag.reason, "HY-005 flag 缺 reason"

    # HY-005 事实基准报告期可追溯。
    assert thesis.fact_period == "2025H1"
    assert thesis.fact_data_status == "fresh"

    # HY-006：detail 携带事实/反证计数与来源状态。
    score = compute_half_year_factor_score(facts, thesis)
    detail = score["half_year_fact_detail"]
    assert detail["facts_status"] == "HAS_DATA"
    assert detail["facts_latest_period"] == "2025H1"
    assert detail["thesis_check_status"] == scenario["expected_thesis_status"]
    # 反证计数与 flag 分流一致。
    assert detail["contradiction_count"] == len(thesis.contradiction_flags)
    assert detail["weakened_count"] == len(thesis.weakened_flags)
    assert detail["supported_count"] == len(thesis.supported_flags)

    # HY-004：summary 结构化字段完整（前端可独立渲染）。
    result_data = _build_data_missing_result_data(symbol)
    attached = report_service.attach_report_half_year_facts(
        copy.deepcopy(result_data), symbol=symbol
    )
    summary = attached["half_year_facts_summary"]
    assert isinstance(summary, dict)
    assert summary["status"] == "HAS_DATA"
    assert summary["matched_count"] >= 1
    assert summary["latest_period"] == "2025H1"
    # 四个子区都在（事实 / 管理层表述 / 待验证事项 / 风险）。
    sections = summary.get("sections", {})
    for sec in ("facts", "management_commentary", "needs_verification", "risks"):
        assert sec in sections, f"HY-004 summary 缺子区 {sec}"


# ════════════════════════════════════════════════════════════════════
# 4. 去噪与提醒优先级（HY-007 联动）
# ════════════════════════════════════════════════════════════════════


def test_rebuttal_alerts_route_to_daily_digest_without_priority_reminder(
    tmp_path: Path, monkeypatch
):
    """[HY-008] contradicted / weakened 但无 priority_reminder → P2 daily_digest。

    只有 contradicted + priority_reminder 才进 P1 intraday_push；本链路 fixture
    不注入 KB-007 research_attention_score，故 priority_reminder=False，所有
    反证提醒都只进日报，不盘中推送（去噪规则）。
    """
    from tradingagents.tradeflow.controller_briefing_payload import (
        ALLOWED_HALF_YEAR_REMINDER_TYPES,
        HALF_YEAR_REMINDER_FACT_UPDATE,
        HALF_YEAR_REMINDER_REBUTTAL_ALERT,
        NOTIFY_LEVEL_DAILY_DIGEST,
        NOTIFY_LEVEL_INTRADAY_PUSH,
    )

    # 用 S2 (weakened) + S3 (contradicted) 两个反证场景。
    for scenario in (SCENARIOS[1], SCENARIOS[2]):
        kb_root = scenario["builder"](tmp_path)
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(kb_root))
        reminders = _build_reminders_for_symbol(scenario, kb_root, monkeypatch)
        assert reminders, f"{scenario['id']}: expected rebuttal reminder"
        for r in reminders:
            assert r["reminder_type"] in ALLOWED_HALF_YEAR_REMINDER_TYPES
            assert r["reminder_type"] == HALF_YEAR_REMINDER_REBUTTAL_ALERT
            # 无 priority_reminder → 只进 daily_digest，不盘中推送。
            assert r["notify_level"] == NOTIFY_LEVEL_DAILY_DIGEST, (
                f"{scenario['id']}: 反证提醒无 priority 却进了 "
                f"{r['notify_level']}"
            )
            assert r["notify_level"] != NOTIFY_LEVEL_INTRADAY_PUSH


def test_supported_facts_route_to_fact_update_daily_digest(
    tmp_path: Path, monkeypatch
):
    """[HY-008] supported → fact_update / P2 daily_digest（提高研究优先级，不推送）。"""
    from tradingagents.tradeflow.controller_briefing_payload import (
        HALF_YEAR_REMINDER_FACT_UPDATE,
        NOTIFY_LEVEL_DAILY_DIGEST,
    )

    scenario = SCENARIOS[0]  # S1 supported
    kb_root = scenario["builder"](tmp_path)
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(kb_root))
    reminders = _build_reminders_for_symbol(scenario, kb_root, monkeypatch)
    assert reminders, "S1 supported 应产生 fact_update 提醒"
    r = reminders[0]
    assert r["reminder_type"] == HALF_YEAR_REMINDER_FACT_UPDATE
    assert r["notify_level"] == NOTIFY_LEVEL_DAILY_DIGEST
    assert r["priority"] == "P2"


def test_no_half_year_symbol_produces_no_reminder_noise(
    tmp_path: Path, monkeypatch
):
    """[HY-008] 无半年报命中的标的不得产生"没有半年报"噪音提醒。"""
    scenario = SCENARIOS[3]  # S4 no half-year
    kb_root = scenario["builder"](tmp_path)
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(kb_root))
    reminders = _build_reminders_for_symbol(scenario, kb_root, monkeypatch)
    assert reminders == [], (
        f"无半年报标的产生了噪音提醒：{reminders}"
    )


# ════════════════════════════════════════════════════════════════════
# 5. 失败路径与边界（墨菲定律）
# ════════════════════════════════════════════════════════════════════


def test_knowledge_root_not_mutated(tmp_path: Path, monkeypatch):
    """[HY-008] 整条链路都是只读——KB 文件在 5 段调用后 SHA 不变。"""
    import hashlib

    kb_root = SCENARIOS[0]["builder"](tmp_path)
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(kb_root))

    def _snapshot() -> Dict[str, str]:
        out = {}
        for p in sorted(kb_root.rglob("*.md")):
            out[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
        return out

    before = _snapshot()

    # 跑完五段链路。
    facts = query_half_year_facts(str(kb_root), symbol="000977.SZ")
    opinions = build_research_fact_opinion_index(str(kb_root), symbol="000977.SZ")
    thesis = check_thesis_against_facts(opinions, facts, symbol="000977.SZ")
    compute_half_year_factor_score(facts, thesis)
    tradeflow_service._enrich_candidate_with_half_year(
        {"symbol": "000977.SZ", "name": "浪潮信息"}
    )
    report_service.attach_report_half_year_facts(
        _build_data_missing_result_data("000977.SZ"), symbol="000977.SZ"
    )

    after = _snapshot()
    assert before == after, "半年报知识链路写入了知识库（违反只读契约）"


def test_chain_does_not_call_llm_or_write_prod_db(tmp_path: Path, monkeypatch):
    """[HY-008] 全链路不调 live LLM、不写生产 DB。

    通过禁用 LLM 客户端构造函数 + 使用内存 SQLite 验证：链路在无 LLM 环境
    下仍完整产出，且报告写入内存库（非 tradingagents.db）。
    """
    kb_root = SCENARIOS[0]["builder"](tmp_path)
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(kb_root))

    # 禁用所有已知 LLM 客户端构造（若链路偷调 LLM 会抛错）。
    for mod_path, cls_names in (
        ("tradingagents.llm_clients.factory", ["create_llm"]),
        ("langchain_openai", ["ChatOpenAI"]),
        ("langchain_anthropic", ["ChatAnthropic"]),
    ):
        try:
            mod = __import__(mod_path, fromlist=cls_names)
        except Exception:
            continue
        for cls_name in cls_names:
            if hasattr(mod, cls_name):
                monkeypatch.setattr(
                    mod, cls_name,
                    lambda *a, **k: (_ for _ in ()).throw(
                        AssertionError(f"LLM 构造被调用: {mod_path}.{cls_name}")
                    ),
                )

    db, engine = _make_db()
    try:
        # 链路在无 LLM 环境下完整跑通。
        report = report_service.create_report(
            db=db,
            symbol="000977.SZ",
            trade_date="2026-07-13",
            decision="HOLD",
            result_data=_build_data_missing_result_data("000977.SZ"),
        )
        assert report.result_data.get("half_year_facts_status") == "HAS_FACTS"

        # 验证写入的是内存库，不是生产 tradingagents.db。
        from api.database import ReportDB
        rows = db.query(ReportDB).all()
        assert len(rows) == 1
        assert rows[0].symbol == "000977.SZ"
    finally:
        db.close()
        engine.dispose()


def test_corrupted_opinion_page_does_not_break_chain(tmp_path: Path, monkeypatch):
    """[HY-008] 单页研报解析异常不阻塞链路——HY-005 容错跳过坏页。

    墨菲定律：研报页可能格式损坏。链路必须降级为 insufficient_data 而非崩溃。
    """
    root = tmp_path / "corrupted"
    inv = root / _INVESTMENT
    inv.mkdir(parents=True)
    # 合格半年报页。
    (inv / "good.md").write_text(_S1_HALF_YEAR, encoding="utf-8")
    # 损坏的研报页（frontmatter 不闭合 + 乱码二进制样内容）。
    (inv / "bad.md").write_text(
        "---\nsymbols: [\"000977.SZ 浪潮信息\"]\nthis is not valid yaml: [unclosed\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(root))

    facts = query_half_year_facts(str(root), symbol="000977.SZ")
    # 事实查询仍命中合格页。
    assert facts.status == "HAS_DATA"
    # 反证不抛异常（坏页被容错跳过）。
    opinions = build_research_fact_opinion_index(str(root), symbol="000977.SZ")
    thesis = check_thesis_against_facts(opinions, facts, symbol="000977.SZ")
    # 链路产出有效（supported 或 insufficient 都可接受，关键是没崩）。
    assert thesis.thesis_check_status in (
        STATUS_SUPPORTED, STATUS_INSUFFICIENT_DATA,
    )


# ════════════════════════════════════════════════════════════════════
# helpers — HY-007 IC bucket / briefing 构造
# ════════════════════════════════════════════════════════════════════


def _build_reminders_for_symbol(
    scenario: Dict[str, Any],
    kb_root: Path,
    monkeypatch,
) -> List[Dict[str, Any]]:
    """用真实 HY-007 IC bucket 收集链路生成提醒（不 mock item）。"""
    from api.services import investment_controller_context as icc
    from tradingagents.tradeflow.controller_briefing_payload import (
        build_half_year_reminders,
    )

    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(kb_root))
    bucket = icc._collect_half_year_facts(
        "2026-07-13 09:30:00",
        holdings={
            "items": [{"symbol": scenario["symbol"], "name": scenario["name"]}]
        },
        observation={"items": []},
        candidates={"items": []},
        mandate_report={"data_status": "missing"},
        notes=[],
    )
    if not bucket.get("items"):
        return []
    ctx = _ctx_with_half_year_bucket(bucket)
    return build_half_year_reminders(ctx, "2026-07-13 09:30:00")


def _ctx_with_half_year_bucket(bucket: Dict[str, Any]) -> Dict[str, Any]:
    """构造最小 IC context，把 bucket 10 嵌进去（复用 HY-007 测试口径）。"""
    from api.services.investment_controller_context import (
        _build_half_year_alert_lane,
    )
    return {
        "schema_version": "1.0",
        "as_of": "2026-07-13 09:30:00",
        "previous_trade_date": "2026-07-11",
        "is_trading_day": True,
        "generated_by": "investment_controller_context",
        "read_only": True,
        "holdings": {"items": [], "data_status": "fresh", "count": 0},
        "observation_warehouse": {"items": [], "data_status": "fresh", "count": 0},
        "tradeflow_candidates": {"items": [], "data_status": "fresh", "count": 0},
        "latest_ta_reports": {"items": [], "data_status": "fresh", "count": 0},
        "data_health": {"data_status": "fresh", "sources": []},
        "pending_ta_required": {"items": [], "data_status": "fresh", "count": 0},
        "mandate_daily_report": {"data_status": "missing"},
        "recent_report_data_blockers": {
            "data_status": "fresh", "scanned_report_count": 0,
            "affected_symbols": [], "summary_level": "ok",
            "field_counts": {}, "total_severe_blockers": 0,
        },
        "local_knowledge_hits": {"items": [], "data_status": "missing"},
        "half_year_facts": bucket,
        "controller_hints": {
            "data_status": "fresh",
            "needs_ta": [],
            "daily_report_only": [],
            "suppress_push_data_insufficient": [],
            "research_review": [],
            "half_year_alerts": _build_half_year_alert_lane(
                bucket, "2026-07-13 09:30:00"
            ),
        },
        "notes": [],
        "runtime_tier_meta": {
            "runtime_tier": "FAST_RADAR",
            "expected_latency": "5-30s",
            "llm_allowed": False,
            "requires_confirmation": False,
            "cost_risk": "none",
        },
    }


def _build_minimal_ctx(
    symbol: str, name: str, kb_root: Path, monkeypatch
):
    """[HY-008] helper：用真实 IC bucket 构造 context，供 pre_market payload 用。"""
    from api.services import investment_controller_context as icc
    monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(kb_root))
    bucket = icc._collect_half_year_facts(
        "2026-07-13 09:30:00",
        holdings={"items": [{"symbol": symbol, "name": name}]},
        observation={"items": []},
        candidates={"items": []},
        mandate_report={"data_status": "missing"},
        notes=[],
    )
    if not bucket.get("items"):
        return None
    return _ctx_with_half_year_bucket(bucket)
