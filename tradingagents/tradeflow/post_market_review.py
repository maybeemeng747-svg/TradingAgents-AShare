# [M-007] post_market_review
"""Post-Market Review & Strategy Hit Rate Analysis for TradeFlow.

Responsibilities:
1. Track candidate performance: next-day / 3-day / 5-day price change vs trigger.
2. Compute per-strategy hit rate, miss rate, and false positive rate.
3. Aggregate removal reasons and evidence gaps.
4. Generate review report to docs/tradeflow_reviews/YYYY-MM-DD.md.

Design constraints:
- Does NOT auto-adjust strategy weights — only outputs suggestions.
- Does NOT generate investment advice — only signal quality review.
- Uses fixture data for testing, does NOT write to production DB.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

from .schemas import Candidate, ALL_STRATEGIES, FORBIDDEN_WORDS
from .strategy_config import StrategyConfig, DEFAULT_STRATEGY_CONFIG


# [TF-REVIEW-003] strategy_attribution_review
class HitAttribution(str, Enum):
    """Explains WHAT dimension drove a candidate's outcome.

    Used for strategy hit attribution in post-market review.
    """
    TECHNICAL_HIT = "technical_hit"
    POLICY_HIT = "policy_hit"
    FUND_FLOW_HIT = "fund_flow_hit"
    DATA_ISSUE = "data_issue"
    RISK_HIT = "risk_hit"

    @property
    def label_cn(self) -> str:
        _MAP = {
            HitAttribution.TECHNICAL_HIT: "技术命中",
            HitAttribution.POLICY_HIT: "政策命中",
            HitAttribution.FUND_FLOW_HIT: "资金流命中",
            HitAttribution.DATA_ISSUE: "数据不足",
            HitAttribution.RISK_HIT: "风险触发",
        }
        return _MAP.get(self, "未知")

    @classmethod
    def all_values(cls) -> list[str]:
        return [a.value for a in cls]


# [TF-REVIEW-002] review_date_mapping
class ReviewDataStatus(str, Enum):
    """Explains WHY review data is missing, instead of showing a blank page."""
    OK = "OK"
    NO_MARKET_DATA = "NO_MARKET_DATA"
    NON_TRADING_DAY = "NON_TRADING_DAY"
    SOURCE_FAILED = "SOURCE_FAILED"
    NOT_ENOUGH_DAYS = "NOT_ENOUGH_DAYS"
    NO_CANDIDATES = "NO_CANDIDATES"

    @property
    def message_cn(self) -> str:
        _MAP = {
            ReviewDataStatus.OK: "数据正常",
            ReviewDataStatus.NO_MARKET_DATA: "无行情数据，等待收盘后补齐",
            ReviewDataStatus.NON_TRADING_DAY: "非交易日，无行情更新",
            ReviewDataStatus.SOURCE_FAILED: "行情数据源获取失败",
            ReviewDataStatus.NOT_ENOUGH_DAYS: "交易日不足，多日收益暂不可用",
            ReviewDataStatus.NO_CANDIDATES: "无候选记录",
        }
        return _MAP.get(self, "未知状态")


# [TF-REVIEW-004] review_empty_diagnostics
class ReviewEmptyReason(str, Enum):
    """Explains the SPECIFIC reason why a post-market Review appears empty.

    Distinguishes the common "没有数据" cases so the frontend can show an
    actionable message instead of a blank page.
    """

    NO_CANDIDATES = "no_candidates"
    NO_OBSERVE = "no_observe"
    NON_TRADING_DAY_MAPPED = "non_trading_day_mapped"
    MARKET_DATA_MISSING = "market_data_missing"
    NOT_GENERATED = "not_generated"

    @property
    def message_cn(self) -> str:
        _MAP = {
            ReviewEmptyReason.NO_CANDIDATES: "尚未生成该日期的候选池，无法复盘",
            ReviewEmptyReason.NO_OBSERVE: "候选池已生成，但盘中观察尚未执行，暂无触发/失效数据",
            ReviewEmptyReason.NON_TRADING_DAY_MAPPED: "查询日为非交易日或属于跨日计划，候选池将在生效交易日复盘",
            ReviewEmptyReason.MARKET_DATA_MISSING: "行情数据缺失，等待收盘后补齐再复盘",
            ReviewEmptyReason.NOT_GENERATED: "候选池存在，但尚未生成盘后复盘报告",
        }
        return _MAP.get(self, "未知原因")

    @property
    def suggested_action_cn(self) -> str:
        _MAP = {
            ReviewEmptyReason.NO_CANDIDATES: "前往候选池 Tab 生成今日候选，或选择已有候选池的日期",
            ReviewEmptyReason.NO_OBSERVE: "运行盘中观察后再复盘",
            ReviewEmptyReason.NON_TRADING_DAY_MAPPED: "切换到生效交易日查看复盘",
            ReviewEmptyReason.MARKET_DATA_MISSING: "收盘后重新生成复盘",
            ReviewEmptyReason.NOT_GENERATED: "点击下方按钮一键生成今日复盘",
        }
        return _MAP.get(self, "")

    @classmethod
    def all_values(cls) -> list[str]:
        return [r.value for r in cls]


@dataclass
class CandidatePerformance:
    symbol: str
    trade_date: str
    entry_price: float
    trigger_price: Optional[float]
    invalid_price: Optional[float]
    strategy_tags: list[str] = field(default_factory=list)
    tier: str = ""
    need_deep_ta: bool = False
    observe_state: str = "WAITING"
    composite_score: float = 0.0
    game_balance: str = ""
    risk_flags: list[str] = field(default_factory=list)

    next_day_close: Optional[float] = None
    day3_close: Optional[float] = None
    day5_close: Optional[float] = None
    next_day_return_pct: Optional[float] = None
    day3_return_pct: Optional[float] = None
    day5_return_pct: Optional[float] = None
    hit: Optional[bool] = None
    invalidated: Optional[bool] = None
    data_status: str = "OK"  # [TF-REVIEW-002] review_date_mapping
    # [TF-REVIEW-003] strategy_attribution_review
    candidate_type: str = ""
    split_scores: dict = field(default_factory=dict)
    hit_type: str = ""  # primary attribution: technical_hit/policy_hit/fund_flow_hit/data_issue/risk_hit
    tomorrow_focus: str = ""  # 明日关注
    downgrade_reason: str = ""  # 降级原因
    evidence_needed: list[str] = field(default_factory=list)  # 需要补证据

    def compute_returns(self) -> None:
        if self.entry_price and self.entry_price > 0:
            if self.next_day_close is not None:
                self.next_day_return_pct = round(
                    (self.next_day_close - self.entry_price) / self.entry_price * 100, 2
                )
            if self.day3_close is not None:
                self.day3_return_pct = round(
                    (self.day3_close - self.entry_price) / self.entry_price * 100, 2
                )
            if self.day5_close is not None:
                self.day5_return_pct = round(
                    (self.day5_close - self.entry_price) / self.entry_price * 100, 2
                )

        if self.next_day_return_pct is not None:
            if self.trigger_price is not None and self.entry_price > 0:
                trigger_distance = (self.trigger_price - self.entry_price) / self.entry_price
                self.hit = self.next_day_return_pct >= trigger_distance * 100
            else:
                self.hit = self.next_day_return_pct > 0

        if self.invalid_price is not None and self.next_day_close is not None:
            self.invalidated = self.next_day_close <= self.invalid_price

    # [TF-REVIEW-003] strategy_attribution_review
    def compute_attribution(self) -> None:
        """Compute hit_type (attribution) and next-day feedback fields."""
        self.hit_type = classify_hit_attribution(self)
        self.tomorrow_focus, self.downgrade_reason, self.evidence_needed = (
            compute_next_day_feedback(self)
        )


@dataclass
class StrategyStats:
    strategy_tag: str
    total_candidates: int = 0
    hit_count: int = 0
    miss_count: int = 0
    no_data_count: int = 0
    invalidated_count: int = 0
    avg_next_day_return: Optional[float] = None
    avg_day3_return: Optional[float] = None
    avg_day5_return: Optional[float] = None
    hit_rate: Optional[float] = None
    false_positive_rate: Optional[float] = None
    # [TF-REVIEW-003] strategy_attribution_review
    attributions: dict = field(default_factory=dict)

    def compute_rates(self) -> None:
        scored = self.hit_count + self.miss_count
        if scored > 0:
            self.hit_rate = round(self.hit_count / scored * 100, 1)
            self.false_positive_rate = round(self.miss_count / scored * 100, 1)


@dataclass
class ReviewSummary:
    review_date: str
    candidate_date: str
    total_candidates: int = 0
    scored_candidates: int = 0
    no_data_candidates: int = 0
    overall_hit_count: int = 0
    overall_miss_count: int = 0
    overall_invalidated_count: int = 0
    overall_hit_rate: Optional[float] = None
    overall_false_positive_rate: Optional[float] = None
    strategy_stats: dict[str, StrategyStats] = field(default_factory=dict)
    tier_stats: dict[str, dict] = field(default_factory=dict)
    common_removal_reasons: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    avg_next_day_return: Optional[float] = None
    avg_day3_return: Optional[float] = None
    avg_day5_return: Optional[float] = None
    data_status: str = "OK"  # [TF-REVIEW-002] review_date_mapping
    data_status_message: str = ""  # [TF-REVIEW-002] review_date_mapping
    plan_date: str = ""  # [TF-REVIEW-002] review_date_mapping — original candidate pool generation date
    effective_trade_date: str = ""  # [TF-REVIEW-002] review_date_mapping
    # [TF-REVIEW-003] strategy_attribution_review
    candidate_type_stats: dict[str, dict] = field(default_factory=dict)
    attribution_stats: dict[str, dict] = field(default_factory=dict)
    next_day_feedback: list[dict] = field(default_factory=list)

    def compute_overall(self) -> None:
        self.scored_candidates = self.overall_hit_count + self.overall_miss_count
        if self.scored_candidates > 0:
            self.overall_hit_rate = round(
                self.overall_hit_count / self.scored_candidates * 100, 1
            )
            self.overall_false_positive_rate = round(
                self.overall_miss_count / self.scored_candidates * 100, 1
            )


# [TF-REVIEW-003] strategy_attribution_review
_POLICY_CANDIDATE_TYPES = {"POLICY_AMBUSH", "POLICY_CONFIRM"}


def classify_hit_attribution(perf: "CandidatePerformance") -> str:
    """Classify the primary attribution of a candidate's outcome.

    Explains WHAT dimension drove the result, returning one of:
    technical_hit / policy_hit / fund_flow_hit / data_issue / risk_hit.
    """
    # data_issue: no market data to evaluate AND not triggered/inactivated
    if perf.hit is None and perf.observe_state not in ("TRIGGERED", "INVALIDATED"):
        return HitAttribution.DATA_ISSUE.value

    # risk_hit: invalidated AND has risk flags (covers both return-based and observe_state)
    if (perf.invalidated or perf.observe_state == "INVALIDATED") and perf.risk_flags:
        return HitAttribution.RISK_HIT.value

    scores = perf.split_scores or {}
    tech = float(scores.get("technical_score", 0.0) or 0.0)
    policy = float(scores.get("policy_score", 0.0) or 0.0)
    fund = float(scores.get("fund_flow_score", 0.0) or 0.0)

    # candidate_type drives policy attribution for 昊天 left-side candidates
    if perf.candidate_type in _POLICY_CANDIDATE_TYPES:
        return HitAttribution.POLICY_HIT.value

    # If split scores are all zero, fall back to strategy tags / candidate_type
    if tech == 0 and policy == 0 and fund == 0:
        tags = set(perf.strategy_tags or [])
        if perf.candidate_type in _POLICY_CANDIDATE_TYPES or "POLICY_VERSION" in tags:
            return HitAttribution.POLICY_HIT.value
        if "FUND_FLOW_ANOMALY" in tags:
            return HitAttribution.FUND_FLOW_HIT.value
        return HitAttribution.TECHNICAL_HIT.value

    # Dominant dimension wins
    if policy > 0 and policy >= tech and policy >= fund:
        return HitAttribution.POLICY_HIT.value
    if fund > 0 and fund > tech and fund > policy:
        return HitAttribution.FUND_FLOW_HIT.value
    return HitAttribution.TECHNICAL_HIT.value


def compute_next_day_feedback(
    perf: "CandidatePerformance",
) -> tuple[str, str, list[str]]:
    """Compute structured next-day feedback for a candidate.

    Returns (tomorrow_focus, downgrade_reason, evidence_needed):
    - tomorrow_focus: 明日关注 — actionable next-step guidance.
    - downgrade_reason: 降级原因 — why the candidate should lose weight.
    - evidence_needed: 需要补证据 — evidence/data gaps to fill.
    """
    # tomorrow_focus (明日关注)
    if perf.observe_state == "TRIGGERED":
        if perf.hit:
            tomorrow_focus = "已触发且命中，关注次日是否站稳触发价"
        else:
            tomorrow_focus = "已触发但回落，关注是否假突破"
    elif perf.observe_state == "INVALIDATED":
        tomorrow_focus = "已失效，建议移出观察或降低权重"
    elif perf.observe_state == "EXPIRED":
        tomorrow_focus = "观察期已过未触发，建议移出观察"
    else:
        tomorrow_focus = "继续等待触发信号"

    # downgrade_reason (降级原因)
    downgrade_reason = ""
    if perf.invalidated:
        downgrade_reason = "价格跌破失效价"
    elif perf.hit is False and perf.next_day_return_pct is not None and perf.next_day_return_pct < 0:
        downgrade_reason = f"触发后下跌 {abs(perf.next_day_return_pct):.1f}%"
    elif perf.hit is False:
        downgrade_reason = "未达到触发距离，信号偏弱"
    elif perf.risk_flags:
        downgrade_reason = "存在风险标记: " + "、".join(perf.risk_flags[:3])

    # evidence_needed (需要补证据) — already set from candidate data
    evidence_needed = list(perf.evidence_needed)

    return tomorrow_focus, downgrade_reason, evidence_needed


def build_candidate_performance(
    candidate: Candidate,
    next_day_close: Optional[float] = None,
    day3_close: Optional[float] = None,
    day5_close: Optional[float] = None,
) -> CandidatePerformance:
    perf = CandidatePerformance(
        symbol=candidate.symbol,
        trade_date=candidate.trade_date,
        entry_price=float(candidate.trigger_price or 0),
        trigger_price=candidate.trigger_price,
        invalid_price=candidate.invalid_price,
        strategy_tags=list(candidate.strategy_tags),
        tier=candidate.tier,
        need_deep_ta=candidate.need_deep_ta,
        observe_state=candidate.observe_state,
        composite_score=candidate.composite_score,
        game_balance=candidate.game_balance,
        risk_flags=list(candidate.risk_flags),
        next_day_close=next_day_close,
        day3_close=day3_close,
        day5_close=day5_close,
        candidate_type=candidate.candidate_type,  # [TF-REVIEW-003]
        split_scores={
            "technical_score": getattr(candidate, "technical_score", 0.0) or 0.0,
            "policy_score": getattr(candidate, "policy_score", 0.0) or 0.0,
            "fund_flow_score": getattr(candidate, "fund_flow_score", 0.0) or 0.0,
        },  # [TF-REVIEW-003]
        evidence_needed=list(getattr(candidate, "missing_evidence", []) or []),  # [TF-REVIEW-003]
    )
    if perf.entry_price == 0 and candidate.score > 0:
        perf.entry_price = 0.0
    perf.compute_returns()
    perf.compute_attribution()  # [TF-REVIEW-003] strategy_attribution_review
    return perf


def build_candidate_performance_from_dict(
    entry: dict,
    next_day_close: Optional[float] = None,
    day3_close: Optional[float] = None,
    day5_close: Optional[float] = None,
) -> CandidatePerformance:
    trigger = entry.get("trigger_price")
    perf = CandidatePerformance(
        symbol=entry.get("symbol", ""),
        trade_date=entry.get("trade_date", ""),
        entry_price=float(trigger) if trigger else 0.0,
        trigger_price=trigger,
        invalid_price=entry.get("invalid_price"),
        strategy_tags=entry.get("strategies", entry.get("strategy_tags", [])),
        tier=entry.get("tier", ""),
        need_deep_ta=entry.get("need_deep_ta", False),
        observe_state=entry.get("observe_state", "WAITING"),
        composite_score=entry.get("composite_score", 0.0),
        game_balance=entry.get("game_balance", ""),
        risk_flags=entry.get("risk_flags", []),
        next_day_close=next_day_close,
        day3_close=day3_close,
        day5_close=day5_close,
        candidate_type=entry.get("candidate_type", ""),  # [TF-REVIEW-003]
        split_scores={
            "technical_score": entry.get("technical_score", 0.0) or 0.0,
            "policy_score": entry.get("policy_score", 0.0) or 0.0,
            "fund_flow_score": entry.get("fund_flow_score", 0.0) or 0.0,
        },  # [TF-REVIEW-003]
        evidence_needed=list(entry.get("missing_evidence", []) or []),  # [TF-REVIEW-003]
    )
    perf.compute_returns()
    perf.compute_attribution()  # [TF-REVIEW-003] strategy_attribution_review
    return perf


def compute_strategy_stats(
    performances: list[CandidatePerformance],
) -> dict[str, StrategyStats]:
    stats_map: dict[str, StrategyStats] = {}

    for perf in performances:
        tags = perf.strategy_tags if perf.strategy_tags else ["NO_STRATEGY"]
        for tag in tags:
            if tag not in stats_map:
                stats_map[tag] = StrategyStats(strategy_tag=tag)
            st = stats_map[tag]
            st.total_candidates += 1

            if perf.hit is None:
                st.no_data_count += 1
            elif perf.hit:
                st.hit_count += 1
            else:
                st.miss_count += 1

            if perf.invalidated:
                st.invalidated_count += 1

            # [TF-REVIEW-003] strategy_attribution_review
            ht = perf.hit_type or HitAttribution.DATA_ISSUE.value
            st.attributions[ht] = st.attributions.get(ht, 0) + 1

    all_returns_next = []
    all_returns_3 = []
    all_returns_5 = []
    for tag, st in stats_map.items():
        st.compute_rates()
        tag_perfs = [
            p for p in performances
            if tag in (p.strategy_tags or ["NO_STRATEGY"])
        ]
        next_returns = [p.next_day_return_pct for p in tag_perfs if p.next_day_return_pct is not None]
        d3_returns = [p.day3_return_pct for p in tag_perfs if p.day3_return_pct is not None]
        d5_returns = [p.day5_return_pct for p in tag_perfs if p.day5_return_pct is not None]
        if next_returns:
            st.avg_next_day_return = round(sum(next_returns) / len(next_returns), 2)
        if d3_returns:
            st.avg_day3_return = round(sum(d3_returns) / len(d3_returns), 2)
        if d5_returns:
            st.avg_day5_return = round(sum(d5_returns) / len(d5_returns), 2)
        all_returns_next.extend(next_returns)
        all_returns_3.extend(d3_returns)
        all_returns_5.extend(d5_returns)

    return stats_map


def compute_tier_stats(
    performances: list[CandidatePerformance],
) -> dict[str, dict]:
    tier_map: dict[str, dict] = {}
    for perf in performances:
        t = perf.tier or "unknown"
        if t not in tier_map:
            tier_map[t] = {"total": 0, "hit": 0, "miss": 0, "no_data": 0, "invalidated": 0}
        tier_map[t]["total"] += 1
        if perf.hit is None:
            tier_map[t]["no_data"] += 1
        elif perf.hit:
            tier_map[t]["hit"] += 1
        else:
            tier_map[t]["miss"] += 1
        if perf.invalidated:
            tier_map[t]["invalidated"] += 1
    return tier_map


# [TF-REVIEW-003] strategy_attribution_review
def compute_candidate_type_stats(
    performances: list[CandidatePerformance],
) -> dict[str, dict]:
    """Aggregate performance by candidate_type (POLICY_AMBUSH/TECH_TRADE/...)."""
    ct_map: dict[str, dict] = {}
    for perf in performances:
        ct = perf.candidate_type or "UNCLASSIFIED"
        if ct not in ct_map:
            ct_map[ct] = {"total": 0, "hit": 0, "miss": 0, "no_data": 0, "invalidated": 0}
        ct_map[ct]["total"] += 1
        if perf.hit is None:
            ct_map[ct]["no_data"] += 1
        elif perf.hit:
            ct_map[ct]["hit"] += 1
        else:
            ct_map[ct]["miss"] += 1
        if perf.invalidated:
            ct_map[ct]["invalidated"] += 1
    return ct_map


# [TF-REVIEW-003] strategy_attribution_review
def compute_attribution_stats(
    performances: list[CandidatePerformance],
) -> dict[str, dict]:
    """Aggregate performance by hit attribution type.

    Shares data format with TF-QUALITY-004 calibration_summary
    (dict of per-category counts + lists of affected symbols).
    """
    attr_map: dict[str, dict] = {
        a.value: {
            "label": a.label_cn,
            "total": 0,
            "hit": 0,
            "miss": 0,
            "no_data": 0,
            "invalidated": 0,
            "symbols": [],
        }
        for a in HitAttribution
    }
    for perf in performances:
        ht = perf.hit_type or HitAttribution.DATA_ISSUE.value
        if ht not in attr_map:
            attr_map[ht] = {
                "label": ht,
                "total": 0, "hit": 0, "miss": 0,
                "no_data": 0, "invalidated": 0, "symbols": [],
            }
        row = attr_map[ht]
        row["total"] += 1
        if perf.hit is None:
            row["no_data"] += 1
        elif perf.hit:
            row["hit"] += 1
        else:
            row["miss"] += 1
        if perf.invalidated:
            row["invalidated"] += 1
        row["symbols"].append(perf.symbol)
    return attr_map


def generate_suggestions(
    strategy_stats: dict[str, StrategyStats],
    tier_stats: dict[str, dict],
    total_candidates: int,
) -> list[str]:
    suggestions = []

    for tag, st in strategy_stats.items():
        if st.hit_rate is not None and st.hit_rate < 30 and st.total_candidates >= 3:
            suggestions.append(
                f"[{tag}] 命中率偏低({st.hit_rate:.0f}%)，建议检查策略阈值或信号质量"
            )
        if st.false_positive_rate is not None and st.false_positive_rate > 70 and st.total_candidates >= 3:
            suggestions.append(
                f"[{tag}] 误报率偏高({st.false_positive_rate:.0f}%)，建议收紧入池条件"
            )
        if st.invalidated_count >= 3:
            suggestions.append(
                f"[{tag}] 失效计数较高({st.invalidated_count})，建议检查失效价设置"
            )

    for t, ts in tier_stats.items():
        total = ts["total"]
        if total > 0:
            no_data_pct = ts["no_data"] / total * 100
            if no_data_pct > 50:
                suggestions.append(
                    f"[{t}层] 数据缺失率{no_data_pct:.0f}%，建议检查行情数据源"
                )

    if total_candidates == 0:
        suggestions.append("无候选数据，无法生成复盘建议")

    if not suggestions:
        suggestions.append("策略信号质量正常，暂无调参建议")

    return suggestions


def run_post_market_review(
    performances: list[CandidatePerformance],
    candidate_date: Optional[str] = None,
    review_date: Optional[str] = None,
    plan_date: Optional[str] = None,
    effective_trade_date: Optional[str] = None,
) -> ReviewSummary:
    if not review_date:
        review_date = datetime.now().strftime("%Y-%m-%d")
    if not candidate_date and performances:
        candidate_date = performances[0].trade_date
    elif not candidate_date:
        candidate_date = review_date

    # [M-007-fix] auto-compute returns before aggregation
    for p in performances:
        p.compute_returns()
        p.compute_attribution()  # [TF-REVIEW-003] strategy_attribution_review

    strategy_stats = compute_strategy_stats(performances)
    tier_stats = compute_tier_stats(performances)
    candidate_type_stats = compute_candidate_type_stats(performances)  # [TF-REVIEW-003]
    attribution_stats = compute_attribution_stats(performances)  # [TF-REVIEW-003]

    total = len(performances)
    hit_count = sum(1 for p in performances if p.hit is True)
    miss_count = sum(1 for p in performances if p.hit is False)
    no_data_count = sum(1 for p in performances if p.hit is None)
    invalidated_count = sum(1 for p in performances if p.invalidated is True)

    next_returns = [p.next_day_return_pct for p in performances if p.next_day_return_pct is not None]
    d3_returns = [p.day3_return_pct for p in performances if p.day3_return_pct is not None]
    d5_returns = [p.day5_return_pct for p in performances if p.day5_return_pct is not None]

    removal_reasons = []
    for p in performances:
        if p.invalidated:
            removal_reasons.append(f"{p.symbol}: 价格跌破失效价")
        if p.observe_state == "EXPIRED":
            removal_reasons.append(f"{p.symbol}: 观察到期未触发")

    suggestions = generate_suggestions(strategy_stats, tier_stats, total)

    # [TF-REVIEW-003] strategy_attribution_review — attribution-based suggestions
    for attr_val, row in attribution_stats.items():
        if row["total"] >= 3 and attr_val == HitAttribution.DATA_ISSUE.value:
            suggestions.append(
                f"[{row['label']}] {row['total']} 个候选数据不足，建议补齐行情/证据后再复盘"
            )
        if row["total"] >= 3 and attr_val == HitAttribution.RISK_HIT.value:
            suggestions.append(
                f"[{row['label']}] {row['total']} 个候选因风险失效，建议检查风险阈值设置"
            )

    # [TF-REVIEW-003] strategy_attribution_review — next-day feedback items
    next_day_feedback = [
        {
            "symbol": p.symbol,
            "candidate_type": p.candidate_type,
            "hit_type": p.hit_type,
            "observe_state": p.observe_state,
            "tomorrow_focus": p.tomorrow_focus,
            "downgrade_reason": p.downgrade_reason,
            "evidence_needed": list(p.evidence_needed),
        }
        for p in performances
    ]

    # [TF-REVIEW-002] review_date_mapping — compute data_status
    if total == 0:
        data_status = ReviewDataStatus.NO_CANDIDATES
    elif no_data_count == total:
        data_status = ReviewDataStatus.NO_MARKET_DATA
    elif len(next_returns) > 0 and len(d3_returns) == 0:
        data_status = ReviewDataStatus.NOT_ENOUGH_DAYS
    else:
        data_status = ReviewDataStatus.OK

    summary = ReviewSummary(
        review_date=review_date,
        candidate_date=candidate_date,
        total_candidates=total,
        overall_hit_count=hit_count,
        overall_miss_count=miss_count,
        no_data_candidates=no_data_count,
        overall_invalidated_count=invalidated_count,
        strategy_stats=strategy_stats,
        tier_stats=tier_stats,
        common_removal_reasons=removal_reasons,
        suggestions=suggestions,
        avg_next_day_return=round(sum(next_returns) / len(next_returns), 2) if next_returns else None,
        avg_day3_return=round(sum(d3_returns) / len(d3_returns), 2) if d3_returns else None,
        avg_day5_return=round(sum(d5_returns) / len(d5_returns), 2) if d5_returns else None,
        data_status=data_status.value,
        data_status_message=data_status.message_cn,
        plan_date=plan_date or candidate_date,
        effective_trade_date=effective_trade_date or review_date,
        candidate_type_stats=candidate_type_stats,  # [TF-REVIEW-003]
        attribution_stats=attribution_stats,  # [TF-REVIEW-003]
        next_day_feedback=next_day_feedback,  # [TF-REVIEW-003]
    )
    summary.compute_overall()
    return summary


def render_review_markdown(summary: ReviewSummary) -> str:
    lines = [
        f"# 盘后复盘 {summary.review_date}",
        "",
        f"## 候选日期: {summary.candidate_date}",
        "",
    ]

    # [TF-REVIEW-002] review_date_mapping — show cross-date mapping
    if summary.plan_date and summary.plan_date != summary.candidate_date:
        lines.append(f"> 该候选池由 {summary.plan_date} 生成，将在 {summary.candidate_date} 复盘")
        lines.append("")

    if summary.effective_trade_date and summary.effective_trade_date != summary.review_date:
        lines.append(f"> 生效交易日: {summary.effective_trade_date}")
        lines.append("")

    # [TF-REVIEW-002] review_date_mapping — show data_status
    if summary.data_status and summary.data_status != ReviewDataStatus.OK.value:
        lines.append(f"> **数据状态**: {summary.data_status} — {summary.data_status_message}")
        lines.append("")

    lines.extend([
        "### 总体概览",
        "",
        f"| 指标 | 值 |",
        f"|------|-----|",
        f"| 总候选数 | {summary.total_candidates} |",
        f"| 有评分数 | {summary.scored_candidates} |",
        f"| 无数据数 | {summary.no_data_candidates} |",
        f"| 命中数 | {summary.overall_hit_count} |",
        f"| 误报数 | {summary.overall_miss_count} |",
        f"| 失效数 | {summary.overall_invalidated_count} |",
        f"| 总命中率 | {summary.overall_hit_rate if summary.overall_hit_rate is not None else 'N/A'}% |",
        f"| 总误报率 | {summary.overall_false_positive_rate if summary.overall_false_positive_rate is not None else 'N/A'}% |",
        f"| 平均次日收益 | {summary.avg_next_day_return if summary.avg_next_day_return is not None else 'N/A'}% |",
        f"| 平均3日收益 | {summary.avg_day3_return if summary.avg_day3_return is not None else 'N/A'}% |",
        f"| 平均5日收益 | {summary.avg_day5_return if summary.avg_day5_return is not None else 'N/A'}% |",
        "",
    ])

    lines.append("### 策略命中率")
    lines.append("")
    lines.append("| 策略 | 总数 | 命中 | 误报 | 无数据 | 失效 | 命中率 | 误报率 | 平均次日 | 平均3日 | 平均5日 |")
    lines.append("|------|------|------|------|--------|------|--------|--------|----------|----------|----------|")
    for tag in sorted(summary.strategy_stats.keys()):
        st = summary.strategy_stats[tag]
        lines.append(
            f"| {tag} | {st.total_candidates} | {st.hit_count} | {st.miss_count} "
            f"| {st.no_data_count} | {st.invalidated_count} "
            f"| {st.hit_rate if st.hit_rate is not None else 'N/A'}% | {st.false_positive_rate if st.false_positive_rate is not None else 'N/A'}% "
            f"| {st.avg_next_day_return if st.avg_next_day_return is not None else 'N/A'}% | {st.avg_day3_return if st.avg_day3_return is not None else 'N/A'}% "
            f"| {st.avg_day5_return if st.avg_day5_return is not None else 'N/A'}% |"
        )
    lines.append("")

    lines.append("### 分层统计")
    lines.append("")
    lines.append("| 层级 | 总数 | 命中 | 误报 | 无数据 | 失效 |")
    lines.append("|------|------|------|------|--------|------|")
    for t in sorted(summary.tier_stats.keys()):
        ts = summary.tier_stats[t]
        lines.append(
            f"| {t}层 | {ts['total']} | {ts['hit']} | {ts['miss']} "
            f"| {ts['no_data']} | {ts['invalidated']} |"
        )
    lines.append("")

    # [TF-REVIEW-003] strategy_attribution_review — candidate type stats
    if summary.candidate_type_stats:
        lines.append("### 候选类型表现")
        lines.append("")
        lines.append("| 类型 | 总数 | 命中 | 误报 | 无数据 | 失效 |")
        lines.append("|------|------|------|------|--------|------|")
        for ct in sorted(summary.candidate_type_stats.keys()):
            cs = summary.candidate_type_stats[ct]
            lines.append(
                f"| {ct} | {cs['total']} | {cs['hit']} | {cs['miss']} "
                f"| {cs['no_data']} | {cs['invalidated']} |"
            )
        lines.append("")

    # [TF-REVIEW-003] strategy_attribution_review — attribution stats
    if summary.attribution_stats:
        lines.append("### 命中归因")
        lines.append("")
        lines.append("| 归因 | 总数 | 命中 | 误报 | 无数据 | 失效 |")
        lines.append("|------|------|------|------|--------|------|")
        for attr_val in HitAttribution.all_values():
            if attr_val not in summary.attribution_stats:
                continue
            ar = summary.attribution_stats[attr_val]
            if ar["total"] == 0:
                continue
            lines.append(
                f"| {ar['label']} | {ar['total']} | {ar['hit']} | {ar['miss']} "
                f"| {ar['no_data']} | {ar['invalidated']} |"
            )
        lines.append("")

    # [TF-REVIEW-003] strategy_attribution_review — next-day feedback
    if summary.next_day_feedback:
        lines.append("### 次日反馈")
        lines.append("")
        lines.append("| 代码 | 归因 | 明日关注 | 降级原因 | 需要补证据 |")
        lines.append("|------|------|----------|----------|------------|")
        for fb in summary.next_day_feedback:
            evidence_str = "、".join(fb.get("evidence_needed", [])) if fb.get("evidence_needed") else "-"
            lines.append(
                f"| {fb['symbol']} | {fb.get('hit_type', '')} "
                f"| {fb.get('tomorrow_focus', '-')} "
                f"| {fb.get('downgrade_reason', '-') or '-'} "
                f"| {evidence_str} |"
            )
        lines.append("")

    if summary.common_removal_reasons:
        lines.append("### 移除理由")
        lines.append("")
        for r in summary.common_removal_reasons[:20]:
            lines.append(f"- {r}")
        lines.append("")

    lines.append("### 调参建议")
    lines.append("")
    for s in summary.suggestions:
        lines.append(f"- {s}")
    lines.append("")

    text = "\n".join(lines)
    for word in FORBIDDEN_WORDS:
        if word in text:
            text = text.replace(word, "***")
    return text


def save_review_report(
    summary: ReviewSummary,
    output_dir: str = "docs/tradeflow_reviews",
) -> str:
    os.makedirs(output_dir, exist_ok=True)
    filename = f"{summary.review_date}.md"
    filepath = os.path.join(output_dir, filename)
    markdown = render_review_markdown(summary)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(markdown)
    return filepath
