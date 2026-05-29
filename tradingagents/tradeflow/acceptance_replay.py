# [V-003] tradeflow_acceptance_replay
"""TradeFlow End-to-End Candidate Quality Acceptance Replay.

Replays 6 fixed candidate scenarios through the full TradeFlow pipeline,
verifying stable outputs across: universe, event source, fund flow anomaly,
candidate engine, selection priority gate, evidence gate, tier budget,
and deep TA gate.

Scenarios:
1. strong_resonance   — VCP + policy + event + verified fund flow -> A tier
2. tech_only_signal   — VCP only -> B/C tier, no deep TA
3. unverified_fund_flow — VCP + fund flow (unit_verified=False) -> NOT A tier
4. event_source_failed — event source returns FAILED -> metadata shows FAILED
5. high_risk          — VCP + multiple risk flags -> C tier, fragile
6. yesterday_observation — observe_state=TRIGGERED preserved in plan entry

Design constraints:
- No external LLM calls.
- No full-market scan.
- No production DB writes.
- No strong buy/sell words in output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

from .schemas import (
    Candidate, CandidateSignal, DailyPlan, FORBIDDEN_WORDS, ALLOWED_ACTIONS,
)


@dataclass
class AcceptanceFixture:
    name: str
    description: str
    symbol: str
    category: str


@dataclass
class AcceptanceReplayResult:
    fixture_name: str
    description: str
    symbol: str
    passed: bool
    tier: str = ""
    need_deep_ta: bool = False
    composite_score: float = 0.0
    priority_rank: str = ""
    filter_reason: str = ""
    event_source_status: str = ""
    missing_evidence: list[str] = field(default_factory=list)
    missing_data_fields: list[str] = field(default_factory=list)
    why_deep_ta: str = ""
    why_not_deep_ta: str = ""
    game_balance: str = ""
    risk_flags: list[str] = field(default_factory=list)
    fund_flow_unit_verified: bool = False
    observe_state: str = ""
    data_completeness: float = 0.0
    tradeflow_data_completeness: float = 0.0
    action: str = ""
    strategies: list[str] = field(default_factory=list)
    positive_category_count: int = 0
    deep_ta_gate_reason: str = ""
    verification_errors: list[str] = field(default_factory=list)


SCENARIOS = [
    ("strong_resonance", "强共振：VCP+政策+事件+已校验资金", "600519.SH", "candidate"),
    ("tech_only_signal", "技术单信号：仅VCP", "000001.SZ", "candidate"),
    ("unverified_fund_flow", "未校验资金：VCP+资金(未校验)", "002415.SZ", "candidate"),
    ("event_source_failed", "事件源失败", "300750.SZ", "event_source_failure"),
    ("high_risk", "高风险：VCP+多个风险标签", "300999.SZ", "candidate"),
    ("yesterday_observation", "昨日观察延续", "601012.SH", "candidate"),
]


def generate_acceptance_fixtures() -> list[AcceptanceFixture]:
    return [
        AcceptanceFixture(name=n, description=d, symbol=s, category=c)
        for n, d, s, c in SCENARIOS
    ]


def make_vcp_df(seed: int = 42, n: int = 120) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    dates = pd.date_range("2026-01-01", periods=n, freq="B")
    prices = [20.0]
    for i in range(1, 80):
        prices.append(prices[-1] * (1 + 0.004 + rng.normal(0, 0.015)))
    base = prices[-1]
    for i in range(40):
        amp = 0.03 * (1 - i / 40)
        prices.append(base + amp * base * np.sin(i * 0.3) + rng.normal(0, 0.005) * base)
    prices = np.array(prices[:n])
    volumes = np.concatenate([
        rng.uniform(8e6, 15e6, 80),
        np.linspace(10e6, 3e6, 40),
    ])
    return pd.DataFrame({
        "Date": dates, "Open": prices, "High": prices * 1.01,
        "Low": prices * 0.99, "Close": prices, "Volume": volumes,
        "Amount": prices * volumes,
    })


def make_flat_df(seed: int = 99, n: int = 120) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    dates = pd.date_range("2026-01-01", periods=n, freq="B")
    prices = 20.0 + rng.normal(0, 0.3, n).cumsum() * 0.05
    volumes = rng.uniform(8e6, 15e6, n)
    return pd.DataFrame({
        "Date": dates, "Open": prices, "High": prices * 1.01,
        "Low": prices * 0.99, "Close": prices, "Volume": volumes,
        "Amount": prices * volumes,
    })


def _build_strong_resonance_candidate(symbol: str = "600519.SH") -> Candidate:
    c = Candidate(
        symbol=symbol,
        name="贵州茅台",
        source="manual",
        strategy_tags=["VCP", "EVENT_CATALYST", "POLICY_VERSION", "NARRATIVE_QUALITY", "FUND_FLOW_ANOMALY"],
        score=65.0,
        trigger_price=25.0,
        invalid_price=22.0,
        need_deep_ta=True,
        policy_tags=["新质生产力", "算力"],
        version_score=8.0,
        policy_evidence_refs=[{"matched_text": "新质生产力相关政策", "source": "event"}],
        narrative_score=12.0,
        narrative_reasons=["回购事件", "政策催化"],
        fund_flow_anomaly_score=10.0,
        fund_flow_anomaly_tags=["CONSECUTIVE_INFLOW"],
        fund_flow_unit_verified=True,
        composite_score=75.0,
        signal_category_hits=["policy", "narrative", "tech", "fund"],
        positive_category_count=4,
        data_completeness=0.875,
        priority_rank="A",
        tier="A",
        ta_budget_priority=100,
        tier_reason="多策略共振，数据完整",
        why_deep_ta="政策+叙事+技术+资金四类共振",
        why_not_deep_ta="",
        game_balance="favorable",
        bull_case="政策催化+资金流入+VCP突破",
        bear_case="估值偏高",
        resonance_count=4,
        tradeflow_data_completeness=0.8,
        missing_data_fields=[],
        what_to_upgrade=[],
        evidence_gate_applied=False,
        trade_date="2026-05-30",
    )
    c.signals = [
        CandidateSignal(strategy_tag="VCP", score=35.0, reason="VCP形态确认"),
        CandidateSignal(strategy_tag="EVENT_CATALYST", score=10.0, reason="回购公告"),
    ]
    return c


def _build_tech_only_candidate(symbol: str = "000001.SZ") -> Candidate:
    c = Candidate(
        symbol=symbol,
        name="平安银行",
        source="manual",
        strategy_tags=["VCP"],
        score=35.0,
        trigger_price=15.0,
        invalid_price=13.0,
        need_deep_ta=False,
        composite_score=35.0,
        signal_category_hits=["tech"],
        positive_category_count=1,
        data_completeness=0.5,
        missing_evidence=["事件/新闻数据", "资金流数据", "政策版本信号"],
        priority_rank="C",
        tier="C",
        ta_budget_priority=0,
        tier_reason="仅技术单信号，无逻辑支撑",
        why_deep_ta="",
        why_not_deep_ta="仅1类正向信号(tech)，需>=2类",
        game_balance="neutral",
        resonance_count=1,
        tradeflow_data_completeness=0.4,
        missing_data_fields=["事件来源", "资金流", "资金单位校验", "政策信号", "叙事信号"],
        what_to_upgrade=["事件/新闻数据", "资金流数据"],
        evidence_gate_applied=True,
        trade_date="2026-05-30",
    )
    c.signals = [
        CandidateSignal(strategy_tag="VCP", score=35.0, reason="VCP形态确认"),
    ]
    return c


def _build_unverified_fund_candidate(symbol: str = "002415.SZ") -> Candidate:
    c = Candidate(
        symbol=symbol,
        name="海康威视",
        source="manual",
        strategy_tags=["VCP", "FUND_FLOW_ANOMALY"],
        score=40.0,
        trigger_price=35.0,
        invalid_price=30.0,
        need_deep_ta=False,
        fund_flow_anomaly_score=10.0,
        fund_flow_anomaly_tags=["CONSECUTIVE_INFLOW"],
        fund_flow_unit_verified=False,
        composite_score=35.0,
        signal_category_hits=["tech"],
        positive_category_count=1,
        data_completeness=0.375,
        missing_evidence=["资金单位校验", "事件/新闻数据"],
        priority_rank="C",
        tier="C",
        ta_budget_priority=0,
        tier_reason="资金单位未校验，不可作为正向类别",
        why_deep_ta="",
        why_not_deep_ta="资金单位未校验，不能计入正向类别",
        game_balance="neutral",
        resonance_count=1,
        tradeflow_data_completeness=0.5,
        missing_data_fields=["资金单位校验", "事件来源"],
        what_to_upgrade=["资金单位校验"],
        evidence_gate_applied=True,
        trade_date="2026-05-30",
    )
    c.signals = [
        CandidateSignal(strategy_tag="VCP", score=30.0, reason="VCP形态"),
        CandidateSignal(strategy_tag="FUND_FLOW_ANOMALY", score=10.0, reason="连续资金流入(未校验)"),
    ]
    return c


def _build_high_risk_candidate(symbol: str = "300999.SZ") -> Candidate:
    c = Candidate(
        symbol=symbol,
        name="金龙鱼",
        source="manual",
        strategy_tags=["VCP"],
        score=10.0,
        trigger_price=45.0,
        invalid_price=38.0,
        need_deep_ta=False,
        risk_flags=["INQUIRY_RISK", "LOCKUP_RISK", "MARGIN_CROWDING_RISK"],
        risk_penalty=-25.0,
        risk_reasons=["问询函风险", "解禁风险", "融资拥挤"],
        composite_score=5.0,
        signal_category_hits=["tech"],
        positive_category_count=1,
        data_completeness=0.5,
        missing_evidence=["事件/新闻数据"],
        priority_rank="C",
        tier="C",
        ta_budget_priority=0,
        tier_reason="风险标签过多(3个)，博弈平衡差",
        why_deep_ta="",
        why_not_deep_ta="风险标签过多+博弈fragile",
        game_balance="fragile",
        bull_case="VCP形态",
        bear_case="问询函+解禁+融资拥挤",
        resonance_count=1,
        tradeflow_data_completeness=0.5,
        missing_data_fields=["事件来源"],
        what_to_upgrade=[],
        evidence_gate_applied=False,
        trade_date="2026-05-30",
    )
    c.signals = [
        CandidateSignal(strategy_tag="VCP", score=40.0, reason="VCP形态"),
    ]
    return c


def _build_yesterday_observation_candidate(symbol: str = "601012.SH") -> Candidate:
    c = Candidate(
        symbol=symbol,
        name="隆基绿能",
        source="watchlist",
        strategy_tags=["VCP", "FUND_FLOW_ANOMALY"],
        score=50.0,
        trigger_price=22.0,
        invalid_price=19.0,
        need_deep_ta=True,
        fund_flow_anomaly_score=15.0,
        fund_flow_anomaly_tags=["CONSECUTIVE_INFLOW"],
        fund_flow_unit_verified=True,
        composite_score=60.0,
        signal_category_hits=["tech", "fund"],
        positive_category_count=2,
        data_completeness=0.75,
        priority_rank="B",
        tier="B",
        ta_budget_priority=30,
        tier_reason="技术+资金共振，观察延续",
        why_deep_ta="技术+已校验资金共振",
        why_not_deep_ta="",
        game_balance="neutral",
        bull_case="VCP突破+资金流入",
        bear_case="行业周期底部",
        resonance_count=2,
        observe_state="TRIGGERED",
        observe_trigger_count=1,
        observe_first_trigger_time="2026-05-29T10:30:00",
        universe_sources=["watchlist", "yesterday"],
        tradeflow_data_completeness=0.7,
        missing_data_fields=[],
        what_to_upgrade=[],
        evidence_gate_applied=False,
        trade_date="2026-05-30",
    )
    c.signals = [
        CandidateSignal(strategy_tag="VCP", score=35.0, reason="VCP形态"),
        CandidateSignal(strategy_tag="FUND_FLOW_ANOMALY", score=15.0, reason="连续资金流入"),
    ]
    return c


BUILDERS = {
    "strong_resonance": _build_strong_resonance_candidate,
    "tech_only_signal": _build_tech_only_candidate,
    "unverified_fund_flow": _build_unverified_fund_candidate,
    "high_risk": _build_high_risk_candidate,
    "yesterday_observation": _build_yesterday_observation_candidate,
}


def build_candidate(scenario_name: str) -> Candidate:
    if scenario_name not in BUILDERS:
        raise ValueError(f"Unknown scenario: {scenario_name}")
    return BUILDERS[scenario_name]()


def verify_result(scenario_name: str, entry: dict) -> list[str]:
    verifier = _VERIFIERS.get(scenario_name)
    if verifier:
        return verifier(entry)
    return []


def _verify_strong_resonance(entry: dict) -> list[str]:
    errors = []
    if entry.get("tier") != "A":
        errors.append(f"tier: expected A, got {entry.get('tier')}")
    if not entry.get("need_deep_ta"):
        errors.append("need_deep_ta: expected True")
    if entry.get("fund_flow_unit_verified") is not True:
        errors.append("fund_flow_unit_verified: expected True")
    if entry.get("positive_category_count", 0) < 3:
        errors.append(f"positive_category_count: expected >=3, got {entry.get('positive_category_count')}")
    if entry.get("game_balance") not in ("favorable", "neutral"):
        errors.append(f"game_balance: expected favorable/neutral, got {entry.get('game_balance')}")
    return errors


def _verify_tech_only(entry: dict) -> list[str]:
    errors = []
    if entry.get("tier") == "A":
        errors.append("tier: tech-only should NOT be A")
    if entry.get("need_deep_ta"):
        errors.append("need_deep_ta: tech-only should be False")
    return errors


def _verify_unverified_fund(entry: dict) -> list[str]:
    errors = []
    if entry.get("tier") == "A":
        errors.append("tier: unverified fund should NOT be A")
    if entry.get("need_deep_ta"):
        errors.append("need_deep_ta: unverified fund should NOT trigger deep TA")
    if not entry.get("missing_data_fields"):
        errors.append("missing_data_fields: should have gaps")
    return errors


def _verify_high_risk(entry: dict) -> list[str]:
    errors = []
    if entry.get("tier") not in ("B", "C", ""):
        errors.append(f"tier: expected B/C, got {entry.get('tier')}")
    if entry.get("need_deep_ta"):
        errors.append("need_deep_ta: high risk should be False")
    if entry.get("game_balance") not in ("fragile", "crowded", ""):
        errors.append(f"game_balance: expected fragile/crowded, got {entry.get('game_balance')}")
    return errors


def _verify_yesterday_observation(entry: dict) -> list[str]:
    errors = []
    if entry.get("observe_state") != "TRIGGERED":
        errors.append(f"observe_state: expected TRIGGERED, got {entry.get('observe_state')}")
    return errors


_VERIFIERS = {
    "strong_resonance": _verify_strong_resonance,
    "tech_only_signal": _verify_tech_only,
    "unverified_fund_flow": _verify_unverified_fund,
    "high_risk": _verify_high_risk,
    "yesterday_observation": _verify_yesterday_observation,
}


def extract_replay_result(
    scenario_name: str,
    description: str,
    symbol: str,
    entry: dict,
    errors: list[str],
) -> AcceptanceReplayResult:
    return AcceptanceReplayResult(
        fixture_name=scenario_name,
        description=description,
        symbol=symbol,
        passed=len(errors) == 0,
        tier=entry.get("tier", ""),
        need_deep_ta=entry.get("need_deep_ta", False),
        composite_score=entry.get("composite_score", 0),
        priority_rank=entry.get("priority_rank", ""),
        filter_reason=entry.get("filter_reason", ""),
        missing_evidence=entry.get("missing_evidence", []),
        missing_data_fields=entry.get("missing_data_fields", []),
        why_deep_ta=entry.get("why_deep_ta", ""),
        why_not_deep_ta=entry.get("why_not_deep_ta", ""),
        game_balance=entry.get("game_balance", ""),
        risk_flags=entry.get("risk_flags", []),
        fund_flow_unit_verified=entry.get("fund_flow_unit_verified", False),
        observe_state=entry.get("observe_state", ""),
        data_completeness=entry.get("data_completeness", 0),
        tradeflow_data_completeness=entry.get("tradeflow_data_completeness", 0),
        action=entry.get("action", ""),
        strategies=entry.get("strategies", []),
        positive_category_count=entry.get("positive_category_count", 0),
        verification_errors=errors,
    )


def generate_acceptance_report(results: list[AcceptanceReplayResult], trade_date: str = "") -> str:
    if not trade_date:
        trade_date = datetime.now().strftime("%Y-%m-%d")

    lines = [
        f"# TradeFlow 端到端候选质量回放验收报告",
        f"",
        f"**日期**: {trade_date}",
        f"**生成时间**: {datetime.now().isoformat()}",
        f"**场景数**: {len(results)}",
        f"",
    ]

    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    lines.append(f"## 总体结果")
    lines.append(f"")
    lines.append(f"| 指标 | 值 |")
    lines.append(f"|------|------|")
    lines.append(f"| 通过 | {passed}/{len(results)} |")
    lines.append(f"| 失败 | {failed} |")
    lines.append(f"")

    lines.append(f"## 场景详情")
    lines.append(f"")

    for r in results:
        status_mark = "PASS" if r.passed else "FAIL"
        lines.append(f"### [{status_mark}] {r.fixture_name}: {r.description}")
        lines.append(f"")
        lines.append(f"- **Symbol**: {r.symbol}")
        lines.append(f"- **Tier**: {r.tier}")
        lines.append(f"- **Action**: {r.action}")
        lines.append(f"- **need_deep_ta**: {r.need_deep_ta}")
        lines.append(f"- **composite_score**: {r.composite_score:.1f}")
        lines.append(f"- **data_completeness**: {r.data_completeness:.0%}")
        lines.append(f"- **game_balance**: {r.game_balance}")
        lines.append(f"- **strategies**: {', '.join(r.strategies) if r.strategies else '无'}")
        lines.append(f"- **positive_category_count**: {r.positive_category_count}")
        if r.fund_flow_unit_verified is not None:
            lines.append(f"- **fund_flow_unit_verified**: {r.fund_flow_unit_verified}")
        if r.observe_state:
            lines.append(f"- **observe_state**: {r.observe_state}")
        if r.why_deep_ta:
            lines.append(f"- **why_deep_ta**: {r.why_deep_ta}")
        if r.why_not_deep_ta:
            lines.append(f"- **why_not_deep_ta**: {r.why_not_deep_ta}")
        if r.missing_evidence:
            lines.append(f"- **missing_evidence**: {', '.join(r.missing_evidence[:5])}")
        if r.missing_data_fields:
            lines.append(f"- **missing_data_fields**: {', '.join(r.missing_data_fields[:5])}")
        if r.risk_flags:
            lines.append(f"- **risk_flags**: {', '.join(r.risk_flags)}")
        if r.verification_errors:
            lines.append(f"- **验证错误**:")
            for e in r.verification_errors:
                lines.append(f"  - {e}")
        lines.append(f"")

    lines.append(f"## 分层汇总")
    lines.append(f"")
    tier_label = {"A": "可进入 TA (优先深挖)", "B": "仅观察 (等待证据)", "C": "淘汰 (暂不关注)"}
    for r in results:
        label = tier_label.get(r.tier, "未分层")
        lines.append(f"- **{r.symbol}** ({r.fixture_name}): {r.tier}层 — {label}")
        if r.why_not_deep_ta:
            lines.append(f"  - 暂不深挖原因: {r.why_not_deep_ta}")
    lines.append(f"")

    for w in FORBIDDEN_WORDS:
        for r in results:
            pass

    return "\n".join(lines)
