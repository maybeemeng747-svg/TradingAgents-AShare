# [H-006] mandate_replay_eval
"""昊天候选池回放评估与反证机制。

建立昊天候选池的回放验证机制，避免系统越做越玄。每个高分政策候选都要能被
后续走势、公告兑现、政策延续或反证记录检验。

功能：
  1. 定义回放 fixture：覆盖 POLICY_AMBUSH / POLICY_CONFIRM / TECH_TRADE /
     EVENT_WATCH / PSEUDO_POLICY / OVERHEATED_AVOID 六类候选的已知走势结果
  2. replay runner 对每个 fixture 执行评估，输出 5/10/20/60 日指标
  3. 反证类型：policy_faded / false_path / overheat_reversal / fundamental_risk /
     capital_not_recognizing
  4. 报告渲染为 Markdown，不写 eval_results/，不调用 LLM

Usage:
    from tradingagents.tradeflow.mandate_replay_eval import (
        run_replay_evaluation,
        render_replay_report,
        get_all_replay_fixtures,
    )
    report = run_replay_evaluation()
    print(render_replay_report(report))
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from .ambush_score import CandidateType


# ── Counter-Evidence (反证) Types ──────────────────────────────────

COUNTER_POLICY_FADED = "policy_faded"
COUNTER_FALSE_PATH = "false_path"
COUNTER_OVERHEAT_REVERSAL = "overheat_reversal"
COUNTER_FUNDAMENTAL_RISK = "fundamental_risk"
COUNTER_CAPITAL_NOT_RECOGNIZING = "capital_not_recognizing"

ALL_COUNTER_TYPES = [
    COUNTER_POLICY_FADED,
    COUNTER_FALSE_PATH,
    COUNTER_OVERHEAT_REVERSAL,
    COUNTER_FUNDAMENTAL_RISK,
    COUNTER_CAPITAL_NOT_RECOGNIZING,
]

COUNTER_TYPE_LABELS = {
    COUNTER_POLICY_FADED: "政策消退",
    COUNTER_FALSE_PATH: "公司路径伪",
    COUNTER_OVERHEAT_REVERSAL: "过热回撤",
    COUNTER_FUNDAMENTAL_RISK: "基本面雷",
    COUNTER_CAPITAL_NOT_RECOGNIZING: "资金不认",
}

# ── Time Horizons ──────────────────────────────────────────────────

HORIZON_5D = 5
HORIZON_10D = 10
HORIZON_20D = 20
HORIZON_60D = 60

ALL_HORIZONS = [HORIZON_5D, HORIZON_10D, HORIZON_20D, HORIZON_60D]


# ── Data Models ────────────────────────────────────────────────────

@dataclass
class PriceSnapshot:
    entry_price: float = 0.0
    prices: Dict[int, float] = field(default_factory=dict)
    index_prices: Dict[int, float] = field(default_factory=dict)
    industry_prices: Dict[int, float] = field(default_factory=dict)

    def get_price(self, horizon: int) -> Optional[float]:
        return self.prices.get(horizon)

    def get_return_pct(self, horizon: int) -> Optional[float]:
        p = self.prices.get(horizon)
        if p is None or self.entry_price <= 0:
            return None
        return round((p - self.entry_price) / self.entry_price * 100, 2)

    def get_index_return_pct(self, horizon: int) -> Optional[float]:
        p = self.index_prices.get(horizon)
        idx_entry = self.index_prices.get(0)
        if p is None or idx_entry is None or idx_entry <= 0:
            return None
        return round((p - idx_entry) / idx_entry * 100, 2)

    def get_excess_return_pct(self, horizon: int) -> Optional[float]:
        r = self.get_return_pct(horizon)
        ir = self.get_index_return_pct(horizon)
        if r is None or ir is None:
            return None
        return round(r - ir, 2)

    def get_max_gain_pct(self) -> Optional[float]:
        if self.entry_price <= 0 or not self.prices:
            return None
        max_g = None
        for p in self.prices.values():
            if p > self.entry_price:
                g = (p - self.entry_price) / self.entry_price * 100
                if max_g is None or g > max_g:
                    max_g = g
        return round(max_g, 2) if max_g is not None else 0.0

    def get_max_drawdown_pct(self) -> Optional[float]:
        if self.entry_price <= 0 or not self.prices:
            return None
        max_dd = 0.0
        for p in self.prices.values():
            if p < self.entry_price:
                dd = (self.entry_price - p) / self.entry_price * 100
                if dd > max_dd:
                    max_dd = dd
        return round(max_dd, 2)

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "entry_price": self.entry_price,
            "prices": self.prices,
            "index_prices": self.index_prices,
            "industry_prices": self.industry_prices,
            "returns": {},
            "max_gain_pct": self.get_max_gain_pct(),
            "max_drawdown_pct": self.get_max_drawdown_pct(),
        }
        for h in ALL_HORIZONS:
            ret = self.get_return_pct(h)
            excess = self.get_excess_return_pct(h)
            result["returns"][str(h)] = {
                "return_pct": ret,
                "index_return_pct": self.get_index_return_pct(h),
                "excess_return_pct": excess,
            }
        return result


@dataclass
class PostEventCheck:
    policy_reconfirmed: bool = False
    announcement_fulfilled: bool = False
    trend_confirmed: bool = False
    risk_counter_evidence: bool = False
    policy_persistence_score: float = 0.0
    benefit_realization_score: float = 0.0
    details: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "policy_reconfirmed": self.policy_reconfirmed,
            "announcement_fulfilled": self.announcement_fulfilled,
            "trend_confirmed": self.trend_confirmed,
            "risk_counter_evidence": self.risk_counter_evidence,
            "policy_persistence_score": round(self.policy_persistence_score, 2),
            "benefit_realization_score": round(self.benefit_realization_score, 2),
            "details": self.details,
        }


@dataclass
class CounterEvidence:
    counter_type: str = ""
    description: str = ""
    horizon: int = 0
    severity: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "counter_type": self.counter_type,
            "description": self.description,
            "horizon": self.horizon,
            "severity": round(self.severity, 2),
        }


@dataclass
class ReplayFixture:
    fixture_id: str = ""
    description: str = ""
    symbol: str = ""
    candidate_type: str = ""
    mandate_score_component: float = 0.0
    beneficiary_score_component: float = 0.0
    ambush_score: float = 0.0
    mandate_topic: str = ""
    company_role: str = ""
    price_snapshot: PriceSnapshot = field(default_factory=PriceSnapshot)
    post_event: PostEventCheck = field(default_factory=PostEventCheck)
    counter_evidences: List[CounterEvidence] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fixture_id": self.fixture_id,
            "description": self.description,
            "symbol": self.symbol,
            "candidate_type": self.candidate_type,
            "mandate_score_component": self.mandate_score_component,
            "beneficiary_score_component": self.beneficiary_score_component,
            "ambush_score": self.ambush_score,
            "mandate_topic": self.mandate_topic,
            "company_role": self.company_role,
            "price_snapshot": self.price_snapshot.to_dict(),
            "post_event": self.post_event.to_dict(),
            "counter_evidences": [c.to_dict() for c in self.counter_evidences],
            "tags": self.tags,
        }


@dataclass
class ReplayEvaluation:
    fixture_id: str = ""
    symbol: str = ""
    candidate_type: str = ""
    passed: bool = True
    verdict: str = ""
    verdict_score: float = 0.0
    returns_by_horizon: Dict[str, Optional[float]] = field(default_factory=dict)
    max_gain_pct: Optional[float] = None
    max_drawdown_pct: Optional[float] = None
    beat_index: Dict[str, Optional[bool]] = field(default_factory=dict)
    policy_persisted: bool = False
    benefit_realized: bool = False
    counter_evidences: List[CounterEvidence] = field(default_factory=list)
    calibration_hints: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fixture_id": self.fixture_id,
            "symbol": self.symbol,
            "candidate_type": self.candidate_type,
            "passed": self.passed,
            "verdict": self.verdict,
            "verdict_score": round(self.verdict_score, 2),
            "returns_by_horizon": self.returns_by_horizon,
            "max_gain_pct": _fmt_pct(self.max_gain_pct),
            "max_drawdown_pct": _fmt_pct(self.max_drawdown_pct),
            "beat_index": {k: v for k, v in self.beat_index.items()},
            "policy_persisted": self.policy_persisted,
            "benefit_realized": self.benefit_realized,
            "counter_evidences": [c.to_dict() for c in self.counter_evidences],
            "calibration_hints": self.calibration_hints,
            "tags": self.tags,
        }


@dataclass
class ReplayReport:
    run_at: str = ""
    date: str = ""
    total_fixtures: int = 0
    passed: int = 0
    failed: int = 0
    all_passed: bool = True
    results: List[ReplayEvaluation] = field(default_factory=list)
    by_candidate_type: Dict[str, Dict[str, int]] = field(default_factory=dict)
    by_counter_type: Dict[str, int] = field(default_factory=dict)
    avg_verdict_score: float = 0.0
    calibration_summary: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_at": self.run_at,
            "date": self.date,
            "total_fixtures": self.total_fixtures,
            "passed": self.passed,
            "failed": self.failed,
            "all_passed": self.all_passed,
            "results": [r.to_dict() for r in self.results],
            "by_candidate_type": self.by_candidate_type,
            "by_counter_type": self.by_counter_type,
            "avg_verdict_score": round(self.avg_verdict_score, 2),
            "calibration_summary": self.calibration_summary,
        }


def _fmt_pct(val: Optional[float]) -> str:
    if val is None:
        return "N/A"
    return f"{val:.1f}%"


# ── Fixture Builders ───────────────────────────────────────────────

FIXTURE_POLICY_AMBUSH_SUCCESS = "policy_ambush_success"
FIXTURE_POLICY_AMBUSH_COUNTER = "policy_ambush_counter"
FIXTURE_POLICY_CONFIRM_SUCCESS = "policy_confirm_success"
FIXTURE_TECH_TRADE_SHORT = "tech_trade_short"
FIXTURE_EVENT_WATCH_NO_FOLLOW = "event_watch_no_follow"
FIXTURE_PSEUDO_POLICY_FAKE = "pseudo_policy_fake"
FIXTURE_OVERHEATED_CRASH = "overheated_crash"
FIXTURE_POLICY_FADE = "policy_fade"
FIXTURE_FALSE_PATH = "false_path"
FIXTURE_CAPITAL_IGNORE = "capital_ignore"
FIXTURE_DATA_GAP_UNCLASSIFIED = "data_gap_unclassified"
FIXTURE_OVERHEATED_WEAK_PATH = "overheated_weak_path"  # [H-009] mandate_counter_evidence_calibration
FIXTURE_STRONG_NOT_OVERKILL = "strong_policy_not_overkilled"  # [H-009]

ALL_REPLAY_FIXTURE_IDS = [
    FIXTURE_POLICY_AMBUSH_SUCCESS,
    FIXTURE_POLICY_AMBUSH_COUNTER,
    FIXTURE_POLICY_CONFIRM_SUCCESS,
    FIXTURE_TECH_TRADE_SHORT,
    FIXTURE_EVENT_WATCH_NO_FOLLOW,
    FIXTURE_PSEUDO_POLICY_FAKE,
    FIXTURE_OVERHEATED_CRASH,
    FIXTURE_POLICY_FADE,
    FIXTURE_FALSE_PATH,
    FIXTURE_CAPITAL_IGNORE,
    FIXTURE_DATA_GAP_UNCLASSIFIED,
    FIXTURE_OVERHEATED_WEAK_PATH,
    FIXTURE_STRONG_NOT_OVERKILL,
]


def _build_policy_ambush_success() -> ReplayFixture:
    return ReplayFixture(
        fixture_id=FIXTURE_POLICY_AMBUSH_SUCCESS,
        description="政策左侧埋伏成功——低空经济核心供应商，政策连续3日确认，公司公告订单，20日上涨18%",
        symbol="002097.SZ",
        candidate_type=CandidateType.POLICY_AMBUSH.value,
        mandate_score_component=65.0,
        beneficiary_score_component=72.0,
        ambush_score=58.5,
        mandate_topic="低空经济",
        company_role="CORE_SUPPLIER",
        price_snapshot=PriceSnapshot(
            entry_price=25.0,
            prices={5: 26.5, 10: 28.0, 20: 29.5, 60: 32.0},
            index_prices={0: 3100.0, 5: 3120.0, 10: 3130.0, 20: 3150.0, 60: 3200.0},
            industry_prices={0: 5000.0, 5: 5100.0, 10: 5150.0, 20: 5200.0, 60: 5350.0},
        ),
        post_event=PostEventCheck(
            policy_reconfirmed=True,
            announcement_fulfilled=True,
            trend_confirmed=True,
            risk_counter_evidence=False,
            policy_persistence_score=85.0,
            benefit_realization_score=78.0,
            details="政策二次确认(工信部会议)+公司中标公告+突破前高",
        ),
        counter_evidences=[],
        tags=["happy_path", "policy_ambush"],
    )


def _build_policy_ambush_counter() -> ReplayFixture:
    return ReplayFixture(
        fixture_id=FIXTURE_POLICY_AMBUSH_COUNTER,
        description="政策左侧埋伏反证——半导体概念股，政策信号消退，公司无实质订单，10日回撤12%",
        symbol="688xxx.SH",
        candidate_type=CandidateType.POLICY_AMBUSH.value,
        mandate_score_component=52.0,
        beneficiary_score_component=38.0,
        ambush_score=42.0,
        mandate_topic="半导体",
        company_role="CONCEPT_ONLY",
        price_snapshot=PriceSnapshot(
            entry_price=45.0,
            prices={5: 43.5, 10: 39.6, 20: 38.0, 60: 36.5},
            index_prices={0: 3100.0, 5: 3110.0, 10: 3090.0, 20: 3120.0, 60: 3150.0},
            industry_prices={0: 8000.0, 5: 7950.0, 10: 7900.0, 20: 8050.0, 60: 8100.0},
        ),
        post_event=PostEventCheck(
            policy_reconfirmed=False,
            announcement_fulfilled=False,
            trend_confirmed=False,
            risk_counter_evidence=True,
            policy_persistence_score=15.0,
            benefit_realization_score=10.0,
            details="政策信号未延续，公司公告仅'探索性研究'，资金持续流出",
        ),
        counter_evidences=[
            CounterEvidence(
                counter_type=COUNTER_POLICY_FADED,
                description="半导体政策信号5日后未再出现",
                horizon=5,
                severity=0.7,
            ),
            CounterEvidence(
                counter_type=COUNTER_FALSE_PATH,
                description="公司角色CONCEPT_ONLY，无实质受益路径",
                horizon=10,
                severity=0.8,
            ),
        ],
        tags=["counter_evidence", "policy_ambush"],
    )


def _build_policy_confirm_success() -> ReplayFixture:
    return ReplayFixture(
        fixture_id=FIXTURE_POLICY_CONFIRM_SUCCESS,
        description="政策右侧确认成功——算力龙头，政策+技术突破+资金确认，5日上涨8%",
        symbol="002230.SZ",
        candidate_type=CandidateType.POLICY_CONFIRM.value,
        mandate_score_component=70.0,
        beneficiary_score_component=80.0,
        ambush_score=62.0,
        mandate_topic="算力",
        company_role="LEADER",
        price_snapshot=PriceSnapshot(
            entry_price=50.0,
            prices={5: 54.0, 10: 56.0, 20: 55.0, 60: 60.0},
            index_prices={0: 3100.0, 5: 3115.0, 10: 3125.0, 20: 3140.0, 60: 3180.0},
            industry_prices={0: 6000.0, 5: 6150.0, 10: 6200.0, 20: 6250.0, 60: 6400.0},
        ),
        post_event=PostEventCheck(
            policy_reconfirmed=True,
            announcement_fulfilled=True,
            trend_confirmed=True,
            risk_counter_evidence=False,
            policy_persistence_score=90.0,
            benefit_realization_score=85.0,
            details="国务院会议再提算力+公司获大单+放量突破",
        ),
        counter_evidences=[],
        tags=["happy_path", "policy_confirm"],
    )


def _build_tech_trade_short() -> ReplayFixture:
    return ReplayFixture(
        fixture_id=FIXTURE_TECH_TRADE_SHORT,
        description="纯技术交易——VCP突破无政策证据，5日快涨后回落，不适合中线",
        symbol="300xxx.SZ",
        candidate_type=CandidateType.TECH_TRADE.value,
        mandate_score_component=5.0,
        beneficiary_score_component=0.0,
        ambush_score=15.0,
        mandate_topic="",
        company_role="UNKNOWN",
        price_snapshot=PriceSnapshot(
            entry_price=18.0,
            prices={5: 19.8, 10: 18.5, 20: 17.2, 60: 16.0},
            index_prices={0: 3100.0, 5: 3110.0, 10: 3120.0, 20: 3130.0, 60: 3150.0},
            industry_prices={0: 4500.0, 5: 4520.0, 10: 4530.0, 20: 4550.0, 60: 4580.0},
        ),
        post_event=PostEventCheck(
            policy_reconfirmed=False,
            announcement_fulfilled=False,
            trend_confirmed=False,
            risk_counter_evidence=False,
            policy_persistence_score=0.0,
            benefit_realization_score=0.0,
            details="纯VCP突破，无政策/事件支撑，5日脉冲后回落",
        ),
        counter_evidences=[],
        tags=["tech_only", "short_term"],
    )


def _build_event_watch_no_follow() -> ReplayFixture:
    return ReplayFixture(
        fixture_id=FIXTURE_EVENT_WATCH_NO_FOLLOW,
        description="事件观察无后续——单日新闻脉冲，无政策确认无受益路径，平盘",
        symbol="601xxx.SH",
        candidate_type=CandidateType.EVENT_WATCH.value,
        mandate_score_component=18.0,
        beneficiary_score_component=0.0,
        ambush_score=10.0,
        mandate_topic="",
        company_role="",
        price_snapshot=PriceSnapshot(
            entry_price=12.0,
            prices={5: 12.1, 10: 11.9, 20: 11.8, 60: 12.0},
            index_prices={0: 3100.0, 5: 3105.0, 10: 3110.0, 20: 3120.0, 60: 3140.0},
            industry_prices={0: 3000.0, 5: 3010.0, 10: 3015.0, 20: 3025.0, 60: 3040.0},
        ),
        post_event=PostEventCheck(
            policy_reconfirmed=False,
            announcement_fulfilled=False,
            trend_confirmed=False,
            risk_counter_evidence=False,
            policy_persistence_score=5.0,
            benefit_realization_score=0.0,
            details="单日新闻脉冲后无后续，市场不认可",
        ),
        counter_evidences=[],
        tags=["no_follow", "event_watch"],
    )


def _build_pseudo_policy_fake() -> ReplayFixture:
    return ReplayFixture(
        fixture_id=FIXTURE_PSEUDO_POLICY_FAKE,
        description="伪政策题材——媒体吹风无政策原文，CONCEPT_ONLY角色，10日回撤8%",
        symbol="000xxx.SZ",
        candidate_type=CandidateType.PSEUDO_POLICY.value,
        mandate_score_component=25.0,
        beneficiary_score_component=12.0,
        ambush_score=8.0,
        mandate_topic="中特估",
        company_role="CONCEPT_ONLY",
        price_snapshot=PriceSnapshot(
            entry_price=8.0,
            prices={5: 7.8, 10: 7.36, 20: 7.5, 60: 7.2},
            index_prices={0: 3100.0, 5: 3105.0, 10: 3095.0, 20: 3100.0, 60: 3110.0},
            industry_prices={0: 2000.0, 5: 2010.0, 10: 2005.0, 20: 2015.0, 60: 2025.0},
        ),
        post_event=PostEventCheck(
            policy_reconfirmed=False,
            announcement_fulfilled=False,
            trend_confirmed=False,
            risk_counter_evidence=True,
            policy_persistence_score=10.0,
            benefit_realization_score=5.0,
            details="仅媒体吹风，无政策原文/会议/公告，资金不认可",
        ),
        counter_evidences=[
            CounterEvidence(
                counter_type=COUNTER_FALSE_PATH,
                description="CONCEPT_ONLY角色，无实质受益",
                horizon=5,
                severity=0.6,
            ),
        ],
        tags=["pseudo", "counter_evidence"],
    )


def _build_overheated_crash() -> ReplayFixture:
    return ReplayFixture(
        fixture_id=FIXTURE_OVERHEATED_CRASH,
        description="过热规避——龙虎榜过热+融资拥挤+博弈fragile，5日暴跌15%",
        symbol="300yyy.SZ",
        candidate_type=CandidateType.OVERHEATED_AVOID.value,
        mandate_score_component=40.0,
        beneficiary_score_component=30.0,
        ambush_score=5.0,
        mandate_topic="机器人",
        company_role="PERIPHERAL",
        price_snapshot=PriceSnapshot(
            entry_price=30.0,
            prices={5: 25.5, 10: 24.0, 20: 23.5, 60: 22.0},
            index_prices={0: 3100.0, 5: 3090.0, 10: 3100.0, 20: 3110.0, 60: 3130.0},
            industry_prices={0: 5500.0, 5: 5450.0, 10: 5500.0, 20: 5550.0, 60: 5600.0},
        ),
        post_event=PostEventCheck(
            policy_reconfirmed=True,
            announcement_fulfilled=False,
            trend_confirmed=False,
            risk_counter_evidence=True,
            policy_persistence_score=50.0,
            benefit_realization_score=20.0,
            details="政策延续但过热回撤：龙虎榜游资撤退+融资盘强平",
        ),
        counter_evidences=[
            CounterEvidence(
                counter_type=COUNTER_OVERHEAT_REVERSAL,
                description="龙虎榜游资撤退+融资拥挤引发踩踏",
                horizon=5,
                severity=0.9,
            ),
            CounterEvidence(
                counter_type=COUNTER_FUNDAMENTAL_RISK,
                description="公司业绩预告低于预期",
                horizon=10,
                severity=0.5,
            ),
        ],
        tags=["overheated", "crash", "counter_evidence"],
    )


def _build_policy_fade() -> ReplayFixture:
    return ReplayFixture(
        fixture_id=FIXTURE_POLICY_FADE,
        description="政策消退——国企改革题材，首次有政策信号但后续无确认，20日横盘后下跌",
        symbol="600xxx.SH",
        candidate_type=CandidateType.POLICY_AMBUSH.value,
        mandate_score_component=45.0,
        beneficiary_score_component=35.0,
        ambush_score=38.0,
        mandate_topic="国企改革",
        company_role="CORE_SUPPLIER",
        price_snapshot=PriceSnapshot(
            entry_price=15.0,
            prices={5: 15.2, 10: 14.8, 20: 14.0, 60: 13.0},
            index_prices={0: 3100.0, 5: 3105.0, 10: 3110.0, 20: 3120.0, 60: 3140.0},
            industry_prices={0: 4000.0, 5: 4010.0, 10: 4020.0, 20: 4030.0, 60: 4050.0},
        ),
        post_event=PostEventCheck(
            policy_reconfirmed=False,
            announcement_fulfilled=False,
            trend_confirmed=False,
            risk_counter_evidence=True,
            policy_persistence_score=20.0,
            benefit_realization_score=15.0,
            details="首次政策信号后无二次确认，公司无相关公告",
        ),
        counter_evidences=[
            CounterEvidence(
                counter_type=COUNTER_POLICY_FADED,
                description="国企改革政策信号未延续，无后续文件/会议",
                horizon=10,
                severity=0.7,
            ),
        ],
        tags=["counter_evidence", "policy_fade"],
    )


def _build_false_path() -> ReplayFixture:
    return ReplayFixture(
        fixture_id=FIXTURE_FALSE_PATH,
        description="公司路径伪——AI应用题材，号称龙头但公告仅'探索合作'，60日跌20%",
        symbol="002yyy.SZ",
        candidate_type=CandidateType.POLICY_CONFIRM.value,
        mandate_score_component=55.0,
        beneficiary_score_component=50.0,
        ambush_score=48.0,
        mandate_topic="AI应用",
        company_role="LEADER",
        price_snapshot=PriceSnapshot(
            entry_price=40.0,
            prices={5: 42.0, 10: 38.0, 20: 35.0, 60: 32.0},
            index_prices={0: 3100.0, 5: 3110.0, 10: 3105.0, 20: 3120.0, 60: 3150.0},
            industry_prices={0: 7000.0, 5: 7100.0, 10: 7050.0, 20: 7150.0, 60: 7200.0},
        ),
        post_event=PostEventCheck(
            policy_reconfirmed=True,
            announcement_fulfilled=False,
            trend_confirmed=False,
            risk_counter_evidence=True,
            policy_persistence_score=70.0,
            benefit_realization_score=10.0,
            details="政策持续但公司受益路径为伪：公告'探索合作'无实质订单",
        ),
        counter_evidences=[
            CounterEvidence(
                counter_type=COUNTER_FALSE_PATH,
                description="LEADER角色但公告仅'探索性合作'，无实质订单/营收",
                horizon=10,
                severity=0.85,
            ),
        ],
        tags=["counter_evidence", "false_path"],
    )


def _build_capital_ignore() -> ReplayFixture:
    return ReplayFixture(
        fixture_id=FIXTURE_CAPITAL_IGNORE,
        description="资金不认——数据要素题材，政策连续但资金不认可，20日跑输指数",
        symbol="300zzz.SZ",
        candidate_type=CandidateType.POLICY_AMBUSH.value,
        mandate_score_component=60.0,
        beneficiary_score_component=55.0,
        ambush_score=50.0,
        mandate_topic="数据要素",
        company_role="INFRA_PROVIDER",
        price_snapshot=PriceSnapshot(
            entry_price=20.0,
            prices={5: 19.8, 10: 19.5, 20: 19.0, 60: 19.2},
            index_prices={0: 3100.0, 5: 3130.0, 10: 3160.0, 20: 3200.0, 60: 3250.0},
            industry_prices={0: 3500.0, 5: 3550.0, 10: 3580.0, 20: 3620.0, 60: 3680.0},
        ),
        post_event=PostEventCheck(
            policy_reconfirmed=True,
            announcement_fulfilled=True,
            trend_confirmed=False,
            risk_counter_evidence=False,
            policy_persistence_score=75.0,
            benefit_realization_score=60.0,
            details="政策持续+公司有订单但主力资金持续净流出，市场不买账",
        ),
        counter_evidences=[
            CounterEvidence(
                counter_type=COUNTER_CAPITAL_NOT_RECOGNIZING,
                description="主力资金20日持续净流出，市场不认可该受益路径",
                horizon=20,
                severity=0.6,
            ),
        ],
        tags=["counter_evidence", "capital_ignore"],
    )


def _build_data_gap_unclassified() -> ReplayFixture:
    return ReplayFixture(
        fixture_id=FIXTURE_DATA_GAP_UNCLASSIFIED,
        description="证据缺口未分类——关键数据源缺失导致无法确认候选类型，走势不明",
        symbol="600xxx.SH",
        candidate_type=CandidateType.UNCLASSIFIED_DATA_GAP.value,
        mandate_score_component=0.0,
        beneficiary_score_component=0.0,
        ambush_score=0.0,
        mandate_topic="",
        company_role="UNKNOWN",
        price_snapshot=PriceSnapshot(
            entry_price=15.0,
            prices={5: 15.2, 10: 14.8, 20: 15.0, 60: 14.5},
            index_prices={0: 3100.0, 5: 3105.0, 10: 3115.0, 20: 3125.0, 60: 3150.0},
            industry_prices={0: 4000.0, 5: 4010.0, 10: 4020.0, 20: 4035.0, 60: 4060.0},
        ),
        post_event=PostEventCheck(
            policy_reconfirmed=False,
            announcement_fulfilled=False,
            trend_confirmed=False,
            risk_counter_evidence=False,
            policy_persistence_score=0.0,
            benefit_realization_score=0.0,
            details="关键数据源缺失(fund_flow/news)，无法分类候选类型",
        ),
        counter_evidences=[],
        tags=["data_gap", "unclassified"],
    )


def _build_overheated_weak_path() -> ReplayFixture:
    return ReplayFixture(
        fixture_id=FIXTURE_OVERHEATED_WEAK_PATH,
        description="过热+路径弱——机器人概念股，CONCEPT_ONLY+博弈crowded+无政策原文，5日回撤10%",
        symbol="300www.SZ",
        candidate_type=CandidateType.POLICY_AMBUSH.value,
        mandate_score_component=30.0,
        beneficiary_score_component=12.0,
        ambush_score=15.0,
        mandate_topic="机器人",
        company_role="CONCEPT_ONLY",
        price_snapshot=PriceSnapshot(
            entry_price=22.0,
            prices={5: 19.8, 10: 19.0, 20: 18.5, 60: 17.0},
            index_prices={0: 3100.0, 5: 3110.0, 10: 3120.0, 20: 3130.0, 60: 3150.0},
            industry_prices={0: 5500.0, 5: 5480.0, 10: 5500.0, 20: 5520.0, 60: 5550.0},
        ),
        post_event=PostEventCheck(
            policy_reconfirmed=False,
            announcement_fulfilled=False,
            trend_confirmed=False,
            risk_counter_evidence=True,
            policy_persistence_score=15.0,
            benefit_realization_score=5.0,
            details="CONCEPT_ONLY+无政策原文+博弈crowded，H-009反证降权校准触发",
        ),
        counter_evidences=[
            CounterEvidence(
                counter_type=COUNTER_OVERHEAT_REVERSAL,
                description="过热+路径弱，H-009校准应降级",
                horizon=5,
                severity=0.8,
            ),
            CounterEvidence(
                counter_type=COUNTER_FALSE_PATH,
                description="CONCEPT_ONLY角色，无实质受益",
                horizon=5,
                severity=0.7,
            ),
        ],
        tags=["counter_evidence", "h009_overheated_weak"],
    )


def _build_strong_not_overkilled() -> ReplayFixture:
    return ReplayFixture(
        fixture_id=FIXTURE_STRONG_NOT_OVERKILL,
        description="强政策+强路径+不过热——低空经济LEADER，政策连续3日+实质订单，不应被误杀",
        symbol="002xxx.SZ",
        candidate_type=CandidateType.POLICY_AMBUSH.value,
        mandate_score_component=70.0,
        beneficiary_score_component=80.0,
        ambush_score=62.0,
        mandate_topic="低空经济",
        company_role="LEADER",
        price_snapshot=PriceSnapshot(
            entry_price=35.0,
            prices={5: 36.5, 10: 38.0, 20: 40.0, 60: 45.0},
            index_prices={0: 3100.0, 5: 3120.0, 10: 3130.0, 20: 3150.0, 60: 3200.0},
            industry_prices={0: 5000.0, 5: 5100.0, 10: 5200.0, 20: 5300.0, 60: 5500.0},
        ),
        post_event=PostEventCheck(
            policy_reconfirmed=True,
            announcement_fulfilled=True,
            trend_confirmed=True,
            risk_counter_evidence=False,
            policy_persistence_score=90.0,
            benefit_realization_score=85.0,
            details="LEADER+政策3日连续+实质订单+不过热，H-009不应降级",
        ),
        counter_evidences=[],
        tags=["happy_path", "h009_strong_not_overkilled"],
    )


_FIXTURE_BUILDERS = {
    FIXTURE_POLICY_AMBUSH_SUCCESS: _build_policy_ambush_success,
    FIXTURE_POLICY_AMBUSH_COUNTER: _build_policy_ambush_counter,
    FIXTURE_POLICY_CONFIRM_SUCCESS: _build_policy_confirm_success,
    FIXTURE_TECH_TRADE_SHORT: _build_tech_trade_short,
    FIXTURE_EVENT_WATCH_NO_FOLLOW: _build_event_watch_no_follow,
    FIXTURE_PSEUDO_POLICY_FAKE: _build_pseudo_policy_fake,
    FIXTURE_OVERHEATED_CRASH: _build_overheated_crash,
    FIXTURE_POLICY_FADE: _build_policy_fade,
    FIXTURE_FALSE_PATH: _build_false_path,
    FIXTURE_CAPITAL_IGNORE: _build_capital_ignore,
    FIXTURE_DATA_GAP_UNCLASSIFIED: _build_data_gap_unclassified,
    FIXTURE_OVERHEATED_WEAK_PATH: _build_overheated_weak_path,
    FIXTURE_STRONG_NOT_OVERKILL: _build_strong_not_overkilled,
}


def get_replay_fixture(fixture_id: str) -> Optional[ReplayFixture]:
    builder = _FIXTURE_BUILDERS.get(fixture_id)
    if builder is None:
        return None
    return builder()


def get_all_replay_fixtures() -> List[ReplayFixture]:
    return [builder() for builder in (_FIXTURE_BUILDERS[fid] for fid in ALL_REPLAY_FIXTURE_IDS)]


# ── Evaluation Logic ───────────────────────────────────────────────

def _evaluate_returns(fixture: ReplayFixture) -> Dict[str, Optional[float]]:
    returns: Dict[str, Optional[float]] = {}
    for h in ALL_HORIZONS:
        returns[str(h)] = fixture.price_snapshot.get_return_pct(h)
    return returns


def _evaluate_beat_index(fixture: ReplayFixture) -> Dict[str, Optional[bool]]:
    result: Dict[str, Optional[bool]] = {}
    for h in ALL_HORIZONS:
        r = fixture.price_snapshot.get_return_pct(h)
        ir = fixture.price_snapshot.get_index_return_pct(h)
        if r is not None and ir is not None:
            result[str(h)] = r > ir
        else:
            result[str(h)] = None
    return result


def _compute_verdict(fixture: ReplayFixture) -> tuple[str, float]:
    ct = fixture.candidate_type
    snap = fixture.price_snapshot
    post = fixture.post_event
    counters = fixture.counter_evidences

    max_gain = snap.get_max_gain_pct() or 0.0
    max_dd = snap.get_max_drawdown_pct() or 0.0
    ret_20 = snap.get_return_pct(20)
    ret_5 = snap.get_return_pct(5)

    score = 0.0
    verdict = ""

    if ct == CandidateType.POLICY_AMBUSH.value:
        if post.policy_reconfirmed and post.announcement_fulfilled and (ret_20 or 0) > 5:
            verdict = "confirmed:政策左侧埋伏验证成功"
            score = 90.0
        elif post.policy_reconfirmed and not counters:
            verdict = "partial:政策延续但兑现不足"
            score = 60.0
        elif counters:
            sev = max((c.severity for c in counters), default=0)
            verdict = f"counter_evidence:反证{counters[0].counter_type}"
            score = max(30.0 - sev * 30, 0.0)
        else:
            verdict = "neutral:无明确验证"
            score = 50.0

    elif ct == CandidateType.POLICY_CONFIRM.value:
        if post.trend_confirmed and (ret_5 or 0) > 3:
            verdict = "confirmed:政策右侧确认成功"
            score = 85.0
        elif counters:
            verdict = f"counter_evidence:反证{counters[0].counter_type}"
            score = 25.0
        elif (ret_20 or 0) < -5:
            verdict = "underperformed:右侧确认失败"
            score = 30.0
        else:
            verdict = "neutral:右侧信号不明"
            score = 50.0

    elif ct == CandidateType.TECH_TRADE.value:
        if (ret_5 or 0) > 5 and (ret_20 or 0) < 0:
            verdict = "confirmed:短线技术兑现后回落(符合预期)"
            score = 75.0
        elif max_gain > 8 and max_dd > 10:
            verdict = "warning:技术波动大但无政策锚定"
            score = 40.0
        else:
            verdict = "neutral:技术交易无特殊验证"
            score = 55.0

    elif ct == CandidateType.EVENT_WATCH.value:
        if not post.policy_reconfirmed and not post.announcement_fulfilled:
            verdict = "confirmed:事件观察无后续(符合预期)"
            score = 70.0
        else:
            verdict = "surprise:事件观察出现后续信号"
            score = 60.0

    elif ct == CandidateType.PSEUDO_POLICY.value:
        if counters or (ret_20 or 0) < -5:
            verdict = "confirmed:伪政策题材反证(符合预期)"
            score = 80.0
        else:
            verdict = "warning:伪政策题材未明显失败"
            score = 45.0

    elif ct == CandidateType.OVERHEATED_AVOID.value:
        if max_dd > 10:
            verdict = "confirmed:过热规避正确(暴跌验证)"
            score = 90.0
        elif counters:
            verdict = "confirmed:过热规避正确(反证触发)"
            score = 85.0
        else:
            verdict = "neutral:过热规避但未暴跌"
            score = 60.0

    elif ct == CandidateType.UNCLASSIFIED_DATA_GAP.value:
        verdict = "confirmed:证据缺口无法分类(符合预期)"
        score = 70.0

    else:
        verdict = "unknown:未知候选类型"
        score = 50.0

    return verdict, round(score, 2)


def _generate_calibration_hints(fixture: ReplayFixture, evaluation: ReplayEvaluation) -> List[str]:
    hints: List[str] = []
    ct = fixture.candidate_type
    counters = fixture.counter_evidences
    post = fixture.post_event

    if ct in (CandidateType.POLICY_AMBUSH.value, CandidateType.POLICY_CONFIRM.value):
        if counters:
            for c in counters:
                if c.counter_type == COUNTER_POLICY_FADED:
                    hints.append("H-002: policy_continuity_score 权重可能需上调，单日信号风险低估")
                if c.counter_type == COUNTER_FALSE_PATH:
                    hints.append("H-003: CONCEPT_ONLY/PERIPHERAL 角色的 beneficiary_score 应更激进降权")
                    hints.append("H-004: ambush_score 中 beneficiary 权重(30%)可能不足")
                if c.counter_type == COUNTER_CAPITAL_NOT_RECOGNIZING:
                    hints.append("H-004: 增加 fund_flow_confirmation 因子到 ambush_score")

    if ct == CandidateType.PSEUDO_POLICY.value:
        if not counters and (fixture.price_snapshot.get_return_pct(20) or 0) > 0:
            hints.append("H-004: PSEUDO_POLICY 分类可能过于保守，需检查 mandate 阈值")

    if ct == CandidateType.OVERHEATED_AVOID.value:
        max_dd = fixture.price_snapshot.get_max_drawdown_pct() or 0
        if max_dd > 15:
            hints.append("H-004: overheat_penalty 权重(15%)验证有效")
        if max_dd < 5:
            hints.append("H-004: overheat_penalty 可能过重，需检查 LHB/MARGIN 标签误报")

    if post.policy_persistence_score > 70 and not post.announcement_fulfilled:
        hints.append("H-003: 政策高分但无公司层兑现，需加强 company_evidence 门禁")

    if fixture.company_role in ("LEADER", "CORE_SUPPLIER") and evaluation.verdict_score < 40:
        hints.append("H-003: LEADER/CORE_SUPPLIER 角色评分与实际表现不符，检查弱词降级逻辑")

    return hints


def evaluate_fixture(fixture: ReplayFixture) -> ReplayEvaluation:
    returns = _evaluate_returns(fixture)
    beat_index = _evaluate_beat_index(fixture)
    verdict, verdict_score = _compute_verdict(fixture)

    snap = fixture.price_snapshot
    max_gain = snap.get_max_gain_pct()
    max_dd = snap.get_max_drawdown_pct()

    passed = verdict_score >= 50.0

    if fixture.candidate_type in (CandidateType.OVERHEATED_AVOID.value, CandidateType.PSEUDO_POLICY.value):
        passed = verdict_score >= 60.0

    evaluation = ReplayEvaluation(
        fixture_id=fixture.fixture_id,
        symbol=fixture.symbol,
        candidate_type=fixture.candidate_type,
        passed=passed,
        verdict=verdict,
        verdict_score=verdict_score,
        returns_by_horizon=returns,
        max_gain_pct=max_gain,
        max_drawdown_pct=max_dd,
        beat_index=beat_index,
        policy_persisted=fixture.post_event.policy_reconfirmed,
        benefit_realized=fixture.post_event.announcement_fulfilled,
        counter_evidences=list(fixture.counter_evidences),
        calibration_hints=[],
        tags=fixture.tags,
    )

    evaluation.calibration_hints = _generate_calibration_hints(fixture, evaluation)
    return evaluation


def run_replay_evaluation(
    fixture_ids: Optional[List[str]] = None,
    fixtures: Optional[List[ReplayFixture]] = None,
) -> ReplayReport:
    now = datetime.now()
    report = ReplayReport(
        run_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        date=now.strftime("%Y-%m-%d"),
    )

    if fixtures is not None:
        items = fixtures
    elif fixture_ids is not None:
        items = []
        for fid in fixture_ids:
            f = get_replay_fixture(fid)
            if f is not None:
                items.append(f)
    else:
        items = get_all_replay_fixtures()

    report.total_fixtures = len(items)
    total_score = 0.0
    all_hints: List[str] = []

    for fixture in items:
        ev = evaluate_fixture(fixture)
        report.results.append(ev)
        total_score += ev.verdict_score

        if ev.passed:
            report.passed += 1
        else:
            report.failed += 1
            report.all_passed = False

        ct = ev.candidate_type
        if ct not in report.by_candidate_type:
            report.by_candidate_type[ct] = {"total": 0, "passed": 0, "failed": 0}
        report.by_candidate_type[ct]["total"] += 1
        if ev.passed:
            report.by_candidate_type[ct]["passed"] += 1
        else:
            report.by_candidate_type[ct]["failed"] += 1

        for ce in ev.counter_evidences:
            ct_key = ce.counter_type
            report.by_counter_type[ct_key] = report.by_counter_type.get(ct_key, 0) + 1

        all_hints.extend(ev.calibration_hints)

    if report.total_fixtures > 0:
        report.avg_verdict_score = round(total_score / report.total_fixtures, 2)

    seen = set()
    for h in all_hints:
        if h not in seen:
            report.calibration_summary.append(h)
            seen.add(h)

    return report


# ── Markdown Rendering ─────────────────────────────────────────────

def render_replay_report(report: ReplayReport) -> str:
    lines: List[str] = []
    lines.append("# 昊天候选池回放评估报告")
    lines.append("")
    lines.append(f"- **日期**: {report.date}")
    lines.append(f"- **运行时间**: {report.run_at}")
    lines.append(f"- **结果**: {'全部通过' if report.all_passed else '存在失败'}")
    lines.append(f"- **平均评分**: {report.avg_verdict_score:.1f}")
    lines.append("")

    lines.append("## 汇总")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|------|------|")
    lines.append(f"| 总 fixture | {report.total_fixtures} |")
    lines.append(f"| 通过 | {report.passed} |")
    lines.append(f"| 失败 | {report.failed} |")
    lines.append("")

    if report.by_candidate_type:
        lines.append("## 按候选类型")
        lines.append("")
        lines.append("| 候选类型 | 总数 | 通过 | 失败 |")
        lines.append("|----------|------|------|------|")
        for ct, counts in sorted(report.by_candidate_type.items()):
            lines.append(f"| {ct} | {counts['total']} | {counts['passed']} | {counts['failed']} |")
        lines.append("")

    if report.by_counter_type:
        lines.append("## 反证类型统计")
        lines.append("")
        for ct_key, count in sorted(report.by_counter_type.items()):
            label = COUNTER_TYPE_LABELS.get(ct_key, ct_key)
            lines.append(f"- **{label}** ({ct_key}): {count}")
        lines.append("")

    lines.append("## 各 Fixture 详情")
    lines.append("")
    for r in report.results:
        ret_strs = []
        for h in ALL_HORIZONS:
            v = r.returns_by_horizon.get(str(h))
            ret_strs.append(f"{h}d={_fmt_pct(v)}")
        lines.append(f"### {r.fixture_id}")
        lines.append(f"- **标的**: {r.symbol}")
        lines.append(f"- **候选类型**: {r.candidate_type}")
        lines.append(f"- **判定**: {r.verdict}")
        lines.append(f"- **评分**: {r.verdict_score:.1f}")
        lines.append(f"- **通过**: {'是' if r.passed else '否'}")
        lines.append(f"- **收益率**: {' | '.join(ret_strs)}")
        lines.append(f"- **最大涨幅**: {_fmt_pct(r.max_gain_pct)}")
        lines.append(f"- **最大回撤**: {_fmt_pct(r.max_drawdown_pct)}")
        beat_strs = []
        for h in ALL_HORIZONS:
            b = r.beat_index.get(str(h))
            if b is None:
                beat_strs.append(f"{h}d=N/A")
            elif b:
                beat_strs.append(f"{h}d=跑赢")
            else:
                beat_strs.append(f"{h}d=跑输")
        lines.append(f"- **vs 指数**: {' | '.join(beat_strs)}")
        lines.append(f"- **政策延续**: {'是' if r.policy_persisted else '否'}")
        lines.append(f"- **受益兑现**: {'是' if r.benefit_realized else '否'}")
        if r.counter_evidences:
            lines.append("- **反证**:")
            for ce in r.counter_evidences:
                label = COUNTER_TYPE_LABELS.get(ce.counter_type, ce.counter_type)
                lines.append(f"  - {label}: {ce.description} (horizon={ce.horizon}d, severity={ce.severity:.2f})")
        if r.calibration_hints:
            lines.append("- **校准建议**:")
            for hint in r.calibration_hints:
                lines.append(f"  - {hint}")
        lines.append("")

    if report.calibration_summary:
        lines.append("## 校准建议汇总")
        lines.append("")
        for hint in report.calibration_summary:
            lines.append(f"- {hint}")
        lines.append("")

    lines.append("---")
    lines.append("*Generated by mandate_replay_eval.py — [H-006] mandate_replay_eval*")
    lines.append("")
    return "\n".join(lines)


# ── File Output ────────────────────────────────────────────────────

def save_replay_report(
    report: ReplayReport,
    output_dir: str = "docs/mandate_replay_reports",
) -> str:
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"{report.date}.md")
    md = render_replay_report(report)
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    return path


def run_replay_and_save(
    output_dir: str = "docs/mandate_replay_reports",
    fixture_ids: Optional[List[str]] = None,
) -> str:
    report = run_replay_evaluation(fixture_ids=fixture_ids)
    path = save_replay_report(report, output_dir=output_dir)
    return path
