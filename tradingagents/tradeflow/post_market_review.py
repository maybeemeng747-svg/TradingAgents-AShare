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

    def compute_overall(self) -> None:
        self.scored_candidates = self.overall_hit_count + self.overall_miss_count
        if self.scored_candidates > 0:
            self.overall_hit_rate = round(
                self.overall_hit_count / self.scored_candidates * 100, 1
            )
            self.overall_false_positive_rate = round(
                self.overall_miss_count / self.scored_candidates * 100, 1
            )


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
    )
    if perf.entry_price == 0 and candidate.score > 0:
        perf.entry_price = 0.0
    perf.compute_returns()
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
    )
    perf.compute_returns()
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

    strategy_stats = compute_strategy_stats(performances)
    tier_stats = compute_tier_stats(performances)

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
