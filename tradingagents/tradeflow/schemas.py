"""TradeFlow schemas — data models for candidates, signals, and daily plans."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional


# ── Allowed actions ──
ALLOWED_ACTIONS = {"OBSERVE", "WAIT_TRIGGER", "NEED_DEEP_TA", "REMOVE_FROM_WATCH"}
FORBIDDEN_WORDS = {"立即买入", "重仓买入", "立即清仓", "满仓", "梭哈"}

STRATEGY_VCP = "VCP"
STRATEGY_PULLBACK = "PULLBACK_SUPPORT"
STRATEGY_EVENT = "EVENT_CATALYST"
STRATEGY_POLICY_VERSION = "POLICY_VERSION"  # [S-001] policy_version_signal
STRATEGY_NARRATIVE = "NARRATIVE_QUALITY"  # [S-002] narrative_quality_score
STRATEGY_FUND_FLOW = "FUND_FLOW_ANOMALY"  # [T-003] fund_flow_anomaly_pool
ALL_STRATEGIES = {STRATEGY_VCP, STRATEGY_PULLBACK, STRATEGY_EVENT, STRATEGY_POLICY_VERSION, STRATEGY_NARRATIVE, STRATEGY_FUND_FLOW}

# [N-004] astock_signal_tags
SIGNAL_TAG_POLICY_CATALYST = "POLICY_CATALYST"
SIGNAL_TAG_HOT_MONEY_LHB = "HOT_MONEY_LHB"
SIGNAL_TAG_LOCKUP_RISK = "LOCKUP_RISK"
SIGNAL_TAG_BUYBACK_EVENT = "BUYBACK_EVENT"
SIGNAL_TAG_RATING_CHANGE = "RATING_CHANGE"


@dataclass
class CandidateSignal:
    """One strategy hit on a symbol."""
    strategy_tag: str
    trigger_price: Optional[float] = None
    support_price: Optional[float] = None
    invalid_price: Optional[float] = None
    score: float = 0.0
    reason: str = ""
    evidence: dict = field(default_factory=dict)
    risk_flags: list[str] = field(default_factory=list)
    need_deep_ta: bool = False


@dataclass
class Candidate:
    """A tradeflow candidate — one row in tradeflow_candidates."""
    symbol: str
    name: str = ""
    source: str = "manual"
    strategy_tags: list[str] = field(default_factory=list)
    score: float = 0.0
    status: str = "active"
    trigger_price: Optional[float] = None
    support_price: Optional[float] = None
    invalid_price: Optional[float] = None
    need_deep_ta: bool = False
    evidence: dict = field(default_factory=dict)
    risk_flags: list[str] = field(default_factory=list)
    trade_date: str = ""
    created_at: str = ""
    updated_at: str = ""

    primary_strategy: str = ""
    signals: list[CandidateSignal] = field(default_factory=list, repr=False)
    policy_tags: list[str] = field(default_factory=list)  # [S-001] policy_version_signal
    version_score: float = 0.0  # [S-001] policy_version_signal
    policy_evidence_refs: list[dict] = field(default_factory=list)  # [S-001] policy_version_signal
    narrative_score: float = 0.0  # [S-002] narrative_quality_score
    narrative_reasons: list[str] = field(default_factory=list)  # [S-002] narrative_quality_score
    narrative_evidence_refs: list[dict] = field(default_factory=list)  # [S-002] narrative_quality_score
    risk_penalty: float = 0.0  # [S-003] underwater_risk_flags
    risk_evidence_refs: list[dict] = field(default_factory=list)  # [S-003] underwater_risk_flags
    risk_reasons: list[str] = field(default_factory=list)  # [S-003] underwater_risk_flags
    game_balance: str = ""  # [S-004] candidate_game_balance
    bull_case: str = ""  # [S-004] candidate_game_balance
    bear_case: str = ""  # [S-004] candidate_game_balance
    policy_case: str = ""  # [S-004] candidate_game_balance
    fund_flow_case: str = ""  # [S-004] candidate_game_balance
    resonance_count: int = 0  # [S-004] candidate_game_balance
    game_balance_refs: list[dict] = field(default_factory=list)  # [S-004] candidate_game_balance
    fund_flow_anomaly_score: float = 0.0  # [T-003] fund_flow_anomaly_pool
    fund_flow_anomaly_tags: list[str] = field(default_factory=list)  # [T-003]
    fund_flow_anomaly_refs: list[dict] = field(default_factory=list)  # [T-003]
    fund_flow_unit_verified: bool = False  # [T-003]
    fund_flow_individual_summary: str = ""  # [T-003]
    fund_flow_board_summary: str = ""  # [T-003]
    composite_score: float = 0.0  # [S-005] selection_priority_gate
    signal_category_hits: list[str] = field(default_factory=list)  # [S-005]
    positive_category_count: int = 0  # [S-005]
    data_completeness: float = 0.0  # [S-005]
    missing_evidence: list[str] = field(default_factory=list)  # [S-005]
    why_deep_ta: str = ""  # [S-005]
    why_not_deep_ta: str = ""  # [S-005]
    priority_rank: str = ""  # [S-005]
    tier: str = ""  # [S-007] candidate_tier_budget
    ta_budget_priority: int = 0  # [S-007] candidate_tier_budget
    tier_reason: str = ""  # [S-007] candidate_tier_budget
    missing_evidence_for_upgrade: list[str] = field(default_factory=list)  # [S-007]
    tradeflow_data_completeness: float = 0.0  # [S-008] tradeflow_evidence_gate
    missing_data_fields: list[str] = field(default_factory=list)  # [S-008]
    what_to_upgrade: list[str] = field(default_factory=list)  # [S-008]
    evidence_gate_applied: bool = False  # [S-008]
    universe_sources: list[str] = field(default_factory=list)  # [M-003] tradeflow_universe_manager
    observe_state: str = "WAITING"  # [M-005] intraday_observe_state
    observe_trigger_count: int = 0  # [M-005] intraday_observe_state
    observe_first_trigger_time: str = ""  # [M-005] intraday_observe_state
    deep_ta_status: str = ""  # [M-006] gated_deep_ta_dispatch
    deep_ta_dispatch_reason: str = ""  # [M-006] gated_deep_ta_dispatch
    deep_ta_model: str = ""  # [M-006] gated_deep_ta_dispatch
    deep_ta_report_path: str = ""  # [M-006] gated_deep_ta_dispatch
    deep_ta_dispatch_time: str = ""  # [M-006] gated_deep_ta_dispatch
    deep_ta_position_context: str = ""  # [M-006] gated_deep_ta_dispatch
    plan_date: str = ""  # [TF-DATE-001] tradeflow_date_semantics
    effective_trade_date: str = ""  # [TF-DATE-001] tradeflow_date_semantics
    observe_date: str = ""  # [TF-DATE-001] tradeflow_date_semantics
    beneficiary_path: list[str] = field(default_factory=list)  # [H-003] mandate_beneficiary_map
    company_role: str = ""  # [H-003] mandate_beneficiary_map
    mandate_topic: str = ""  # [H-003] mandate_beneficiary_map
    mandate_evidence_refs: list[dict] = field(default_factory=list)  # [H-003] mandate_beneficiary_map
    candidate_type: str = ""  # [H-004] mandate_ambush_score
    ambush_score: float = 0.0  # [H-004] mandate_ambush_score
    mandate_score_component: float = 0.0  # [H-004] mandate_ambush_score
    beneficiary_score_component: float = 0.0  # [H-004] mandate_ambush_score
    pricing_gap_score: float = 0.0  # [H-004] mandate_ambush_score
    overheat_penalty: float = 0.0  # [H-004] mandate_ambush_score
    candidate_type_reason: str = ""  # [H-004] mandate_ambush_score
    deep_ta_route: str = ""  # [H-004] mandate_ambush_score
    deep_ta_route_reason: str = ""  # [H-004] mandate_ambush_score
    ambush_reasons: list[str] = field(default_factory=list)  # [H-004] mandate_ambush_score
    ambush_evidence_refs: list[dict] = field(default_factory=list)  # [H-004] mandate_ambush_score
    research_queue: str = ""  # [H-007] mandate_ta_queue_router
    research_intent: str = ""  # [H-007] mandate_ta_queue_router
    research_route_reason: str = ""  # [H-007] mandate_ta_queue_router
    watchlist_note: str = ""  # [H-008] mandate_watchlist_note
    watchlist_note_suggested: str = ""  # [H-008] mandate_watchlist_note
    watchlist_topic: str = ""  # [H-008] mandate_watchlist_note
    watchlist_benefit_score: float = 0.0  # [H-008] mandate_watchlist_note
    watchlist_consensus_score: float = 0.0  # [H-008] mandate_watchlist_note
    watchlist_evidence_gap: list[str] = field(default_factory=list)  # [H-008] mandate_watchlist_note
    counter_evidence: list[dict] = field(default_factory=list)  # [H-009] mandate_counter_evidence_calibration
    overheat_flags: list[str] = field(default_factory=list)  # [H-009]
    downgrade_reasons: list[str] = field(default_factory=list)  # [H-009]
    what_would_change_mind: list[str] = field(default_factory=list)  # [H-009]
    topic_lifecycle_state: str = ""  # [H-010] mandate_topic_lifecycle
    topic_lifecycle_reason: str = ""  # [H-010] mandate_topic_lifecycle
    topic_last_signal_date: str = ""  # [H-010] mandate_topic_lifecycle
    topic_signal_count: int = 0  # [H-010] mandate_topic_lifecycle
    contradiction_level: str = ""  # [H-011] candidate_contradiction_explainer
    contradiction_items: list[dict] = field(default_factory=list)  # [H-011]
    blocking_evidence_gaps: list[str] = field(default_factory=list)  # [H-011]
    next_verification_steps: list[str] = field(default_factory=list)  # [H-011]
    technical_score: float = 0.0  # [TF-QUALITY-002] score_separation
    policy_score: float = 0.0  # [TF-QUALITY-002]
    fund_flow_score: float = 0.0  # [TF-QUALITY-002]
    event_score: float = 0.0  # [TF-QUALITY-002]
    risk_penalty_score: float = 0.0  # [TF-QUALITY-002]
    data_quality_score: float = 0.0  # [TF-QUALITY-002]
    ranking_reasons: list[str] = field(default_factory=list)  # [TF-QUALITY-002]
    weakness_reasons: list[str] = field(default_factory=list)  # [TF-QUALITY-002]

    def __post_init__(self):
        if not self.trade_date:
            self.trade_date = datetime.now().strftime("%Y-%m-%d")
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.updated_at:
            self.updated_at = self.created_at

    def merge_signals(self):
        """Merge individual signals into aggregate candidate fields.

        The signal with the highest score becomes the primary_signal.
        trigger/support/invalid prices come ONLY from the primary signal,
        preventing later strategies from silently overwriting them.
        """
        if not self.signals:
            return

        primary = max(self.signals, key=lambda s: s.score)
        self.primary_strategy = primary.strategy_tag

        tags = set()
        best_score = 0.0
        all_evidence = {}
        all_risks = []
        deep_ta = False

        for sig in self.signals:
            tags.add(sig.strategy_tag)
            if sig.score > best_score:
                best_score = sig.score
            all_evidence[sig.strategy_tag] = sig.evidence
            all_risks.extend(sig.risk_flags)
            if sig.need_deep_ta:
                deep_ta = True

        self.strategy_tags = sorted(tags)
        self.score = round(best_score, 2)
        self.evidence = all_evidence
        self.risk_flags = sorted(set(all_risks))
        self.trigger_price = primary.trigger_price
        self.support_price = primary.support_price
        self.invalid_price = primary.invalid_price
        self.need_deep_ta = deep_ta

    def to_db_row(self) -> dict:
        return {
            "trade_date": self.trade_date,
            "symbol": self.symbol,
            "name": self.name,
            "source": self.source,
            "strategy_tags_json": json.dumps(self.strategy_tags, ensure_ascii=False),
            "primary_strategy": self.primary_strategy,
            "score": self.score,
            "status": self.status,
            "trigger_price": self.trigger_price,
            "support_price": self.support_price,
            "invalid_price": self.invalid_price,
            "need_deep_ta": 1 if self.need_deep_ta else 0,
            "evidence_json": json.dumps(self.evidence, ensure_ascii=False),
            "risk_flags_json": json.dumps(self.risk_flags, ensure_ascii=False),
            "policy_tags_json": json.dumps(self.policy_tags, ensure_ascii=False),  # [S-001]
            "version_score": self.version_score,  # [S-001]
            "policy_evidence_refs_json": json.dumps(self.policy_evidence_refs, ensure_ascii=False),  # [S-001]
            "narrative_score": self.narrative_score,  # [S-002] narrative_quality_score
            "narrative_reasons_json": json.dumps(self.narrative_reasons, ensure_ascii=False),  # [S-002]
            "narrative_evidence_refs_json": json.dumps(self.narrative_evidence_refs, ensure_ascii=False),  # [S-002]
            "risk_penalty": self.risk_penalty,  # [S-003] underwater_risk_flags
            "risk_evidence_refs_json": json.dumps(self.risk_evidence_refs, ensure_ascii=False),  # [S-003]
            "risk_reasons_json": json.dumps(self.risk_reasons, ensure_ascii=False),  # [S-003]
            "game_balance": self.game_balance,  # [S-004] candidate_game_balance
            "bull_case": self.bull_case,  # [S-004]
            "bear_case": self.bear_case,  # [S-004]
            "policy_case": self.policy_case,  # [S-004]
            "fund_flow_case": self.fund_flow_case,  # [S-004]
            "resonance_count": self.resonance_count,  # [S-004]
            "game_balance_refs_json": json.dumps(self.game_balance_refs, ensure_ascii=False),  # [S-004]
            "fund_flow_anomaly_score": self.fund_flow_anomaly_score,  # [T-003] fund_flow_anomaly_pool
            "fund_flow_anomaly_tags_json": json.dumps(self.fund_flow_anomaly_tags, ensure_ascii=False),  # [T-003]
            "fund_flow_anomaly_refs_json": json.dumps(self.fund_flow_anomaly_refs, ensure_ascii=False),  # [T-003]
            "fund_flow_unit_verified": 1 if self.fund_flow_unit_verified else 0,  # [T-003]
            "fund_flow_individual_summary": self.fund_flow_individual_summary,  # [T-003]
            "fund_flow_board_summary": self.fund_flow_board_summary,  # [T-003]
            "composite_score": self.composite_score,  # [S-005] selection_priority_gate
            "signal_category_hits_json": json.dumps(self.signal_category_hits, ensure_ascii=False),  # [S-005]
            "positive_category_count": self.positive_category_count,  # [S-005]
            "data_completeness": self.data_completeness,  # [S-005]
            "missing_evidence_json": json.dumps(self.missing_evidence, ensure_ascii=False),  # [S-005]
            "why_deep_ta": self.why_deep_ta,  # [S-005]
            "why_not_deep_ta": self.why_not_deep_ta,  # [S-005]
            "priority_rank": self.priority_rank,  # [S-005]
            "tier": self.tier,  # [S-007] candidate_tier_budget
            "ta_budget_priority": self.ta_budget_priority,  # [S-007]
            "tier_reason": self.tier_reason,  # [S-007]
            "missing_evidence_for_upgrade_json": json.dumps(self.missing_evidence_for_upgrade, ensure_ascii=False),  # [S-007]
            "tradeflow_data_completeness": self.tradeflow_data_completeness,  # [S-008] tradeflow_evidence_gate
            "missing_data_fields_json": json.dumps(self.missing_data_fields, ensure_ascii=False),  # [S-008]
            "what_to_upgrade_json": json.dumps(self.what_to_upgrade, ensure_ascii=False),  # [S-008]
            "evidence_gate_applied": 1 if self.evidence_gate_applied else 0,  # [S-008]
            "universe_sources_json": json.dumps(self.universe_sources, ensure_ascii=False),  # [M-003]
            "observe_state": self.observe_state,  # [M-005] intraday_observe_state
            "observe_trigger_count": self.observe_trigger_count,  # [M-005]
            "observe_first_trigger_time": self.observe_first_trigger_time,  # [M-005]
            "deep_ta_status": self.deep_ta_status,  # [M-006] gated_deep_ta_dispatch
            "deep_ta_dispatch_reason": self.deep_ta_dispatch_reason,  # [M-006]
            "deep_ta_model": self.deep_ta_model,  # [M-006]
            "deep_ta_report_path": self.deep_ta_report_path,  # [M-006]
            "deep_ta_dispatch_time": self.deep_ta_dispatch_time,  # [M-006]
            "deep_ta_position_context": self.deep_ta_position_context,  # [M-006]
            "plan_date": self.plan_date,  # [TF-DATE-001] tradeflow_date_semantics
            "effective_trade_date": self.effective_trade_date,  # [TF-DATE-001]
            "observe_date": self.observe_date,  # [TF-DATE-001]
            "beneficiary_path_json": json.dumps(self.beneficiary_path, ensure_ascii=False),  # [H-003]
            "company_role": self.company_role,  # [H-003]
            "mandate_topic": self.mandate_topic,  # [H-003]
            "mandate_evidence_refs_json": json.dumps(self.mandate_evidence_refs, ensure_ascii=False),  # [H-003]
            "candidate_type": self.candidate_type,  # [H-004] mandate_ambush_score
            "ambush_score": self.ambush_score,  # [H-004]
            "mandate_score_component": self.mandate_score_component,  # [H-004]
            "beneficiary_score_component": self.beneficiary_score_component,  # [H-004]
            "pricing_gap_score": self.pricing_gap_score,  # [H-004]
            "overheat_penalty": self.overheat_penalty,  # [H-004]
            "candidate_type_reason": self.candidate_type_reason,  # [H-004]
            "deep_ta_route": self.deep_ta_route,  # [H-004]
            "deep_ta_route_reason": self.deep_ta_route_reason,  # [H-004]
            "ambush_reasons_json": json.dumps(self.ambush_reasons, ensure_ascii=False),  # [H-004]
            "ambush_evidence_refs_json": json.dumps(self.ambush_evidence_refs, ensure_ascii=False),  # [H-004]
            "research_queue": self.research_queue,  # [H-007] mandate_ta_queue_router
            "research_intent": self.research_intent,  # [H-007] mandate_ta_queue_router
            "research_route_reason": self.research_route_reason,  # [H-007] mandate_ta_queue_router
            "watchlist_note": self.watchlist_note,  # [H-008] mandate_watchlist_note
            "watchlist_note_suggested": self.watchlist_note_suggested,  # [H-008]
            "watchlist_topic": self.watchlist_topic,  # [H-008]
            "watchlist_benefit_score": self.watchlist_benefit_score,  # [H-008]
            "watchlist_consensus_score": self.watchlist_consensus_score,  # [H-008]
            "watchlist_evidence_gap_json": json.dumps(self.watchlist_evidence_gap, ensure_ascii=False),  # [H-008]
            "counter_evidence_json": json.dumps(self.counter_evidence, ensure_ascii=False),  # [H-009] mandate_counter_evidence_calibration
            "overheat_flags_json": json.dumps(self.overheat_flags, ensure_ascii=False),  # [H-009]
            "downgrade_reasons_json": json.dumps(self.downgrade_reasons, ensure_ascii=False),  # [H-009]
            "what_would_change_mind_json": json.dumps(self.what_would_change_mind, ensure_ascii=False),  # [H-009]
            "topic_lifecycle_state": self.topic_lifecycle_state,  # [H-010] mandate_topic_lifecycle
            "topic_lifecycle_reason": self.topic_lifecycle_reason,  # [H-010]
            "topic_last_signal_date": self.topic_last_signal_date,  # [H-010]
            "topic_signal_count": self.topic_signal_count,  # [H-010]
            "contradiction_level": self.contradiction_level,  # [H-011] candidate_contradiction_explainer
            "contradiction_items_json": json.dumps(self.contradiction_items, ensure_ascii=False),  # [H-011]
            "blocking_evidence_gaps_json": json.dumps(self.blocking_evidence_gaps, ensure_ascii=False),  # [H-011]
            "next_verification_steps_json": json.dumps(self.next_verification_steps, ensure_ascii=False),  # [H-011]
            "technical_score": self.technical_score,  # [TF-QUALITY-002] score_separation
            "policy_score": self.policy_score,  # [TF-QUALITY-002]
            "fund_flow_score": self.fund_flow_score,  # [TF-QUALITY-002]
            "event_score": self.event_score,  # [TF-QUALITY-002]
            "risk_penalty_score": self.risk_penalty_score,  # [TF-QUALITY-002]
            "data_quality_score": self.data_quality_score,  # [TF-QUALITY-002]
            "ranking_reasons_json": json.dumps(self.ranking_reasons, ensure_ascii=False),  # [TF-QUALITY-002]
            "weakness_reasons_json": json.dumps(self.weakness_reasons, ensure_ascii=False),  # [TF-QUALITY-002]
            "updated_at": datetime.now().isoformat(),
        }

    @classmethod
    def from_db_row(cls, row: dict) -> "Candidate":
        return cls(
            symbol=row["symbol"],
            name=row.get("name", ""),
            source=row.get("source", "manual"),
            strategy_tags=json.loads(row.get("strategy_tags_json", "[]")),
            primary_strategy=row.get("primary_strategy", ""),
            score=row.get("score", 0.0),
            status=row.get("status", "active"),
            trigger_price=row.get("trigger_price"),
            support_price=row.get("support_price"),
            invalid_price=row.get("invalid_price"),
            need_deep_ta=bool(row.get("need_deep_ta", 0)),
            evidence=json.loads(row.get("evidence_json", "{}")),
            risk_flags=json.loads(row.get("risk_flags_json", "[]")),
            trade_date=row.get("trade_date", ""),
            created_at=row.get("created_at", ""),
            updated_at=row.get("updated_at", ""),
            policy_tags=json.loads(row.get("policy_tags_json", "[]")),  # [S-001]
            version_score=row.get("version_score", 0.0),  # [S-001]
            policy_evidence_refs=json.loads(row.get("policy_evidence_refs_json", "[]")),  # [S-001]
            narrative_score=row.get("narrative_score", 0.0),  # [S-002] narrative_quality_score
            narrative_reasons=json.loads(row.get("narrative_reasons_json", "[]")),  # [S-002]
            narrative_evidence_refs=json.loads(row.get("narrative_evidence_refs_json", "[]")),  # [S-002]
            risk_penalty=row.get("risk_penalty", 0.0),  # [S-003] underwater_risk_flags
            risk_evidence_refs=json.loads(row.get("risk_evidence_refs_json", "[]")),  # [S-003]
            risk_reasons=json.loads(row.get("risk_reasons_json", "[]")),  # [S-003]
            game_balance=row.get("game_balance", ""),  # [S-004] candidate_game_balance
            bull_case=row.get("bull_case", ""),  # [S-004]
            bear_case=row.get("bear_case", ""),  # [S-004]
            policy_case=row.get("policy_case", ""),  # [S-004]
            fund_flow_case=row.get("fund_flow_case", ""),  # [S-004]
            resonance_count=row.get("resonance_count", 0),  # [S-004]
            game_balance_refs=json.loads(row.get("game_balance_refs_json", "[]")),  # [S-004]
            fund_flow_anomaly_score=row.get("fund_flow_anomaly_score", 0.0),  # [T-003] fund_flow_anomaly_pool
            fund_flow_anomaly_tags=json.loads(row.get("fund_flow_anomaly_tags_json", "[]")),  # [T-003]
            fund_flow_anomaly_refs=json.loads(row.get("fund_flow_anomaly_refs_json", "[]")),  # [T-003]
            fund_flow_unit_verified=bool(row.get("fund_flow_unit_verified", 0)),  # [T-003]
            fund_flow_individual_summary=row.get("fund_flow_individual_summary", ""),  # [T-003]
            fund_flow_board_summary=row.get("fund_flow_board_summary", ""),  # [T-003]
            composite_score=row.get("composite_score", 0.0),  # [S-005] selection_priority_gate
            signal_category_hits=json.loads(row.get("signal_category_hits_json", "[]")),  # [S-005]
            positive_category_count=row.get("positive_category_count", 0),  # [S-005]
            data_completeness=row.get("data_completeness", 0.0),  # [S-005]
            missing_evidence=json.loads(row.get("missing_evidence_json", "[]")),  # [S-005]
            why_deep_ta=row.get("why_deep_ta", ""),  # [S-005]
            why_not_deep_ta=row.get("why_not_deep_ta", ""),  # [S-005]
            priority_rank=row.get("priority_rank", ""),  # [S-005]
            tier=row.get("tier", ""),  # [S-007] candidate_tier_budget
            ta_budget_priority=row.get("ta_budget_priority", 0),  # [S-007]
            tier_reason=row.get("tier_reason", ""),  # [S-007]
            missing_evidence_for_upgrade=json.loads(row.get("missing_evidence_for_upgrade_json", "[]")),  # [S-007]
            tradeflow_data_completeness=row.get("tradeflow_data_completeness", 0.0),  # [S-008] tradeflow_evidence_gate
            missing_data_fields=json.loads(row.get("missing_data_fields_json", "[]")),  # [S-008]
            what_to_upgrade=json.loads(row.get("what_to_upgrade_json", "[]")),  # [S-008]
            evidence_gate_applied=bool(row.get("evidence_gate_applied", 0)),  # [S-008]
            universe_sources=json.loads(row.get("universe_sources_json", "[]")),  # [M-003]
            observe_state=row.get("observe_state", "WAITING"),  # [M-005] intraday_observe_state
            observe_trigger_count=row.get("observe_trigger_count", 0),  # [M-005]
            observe_first_trigger_time=row.get("observe_first_trigger_time", ""),  # [M-005]
            deep_ta_status=row.get("deep_ta_status", ""),  # [M-006] gated_deep_ta_dispatch
            deep_ta_dispatch_reason=row.get("deep_ta_dispatch_reason", ""),  # [M-006]
            deep_ta_model=row.get("deep_ta_model", ""),  # [M-006]
            deep_ta_report_path=row.get("deep_ta_report_path", ""),  # [M-006]
            deep_ta_dispatch_time=row.get("deep_ta_dispatch_time", ""),  # [M-006]
            deep_ta_position_context=row.get("deep_ta_position_context", ""),  # [M-006]
            plan_date=row.get("plan_date", ""),  # [TF-DATE-001] tradeflow_date_semantics
            effective_trade_date=row.get("effective_trade_date", ""),  # [TF-DATE-001]
            observe_date=row.get("observe_date", ""),  # [TF-DATE-001]
            beneficiary_path=json.loads(row.get("beneficiary_path_json", "[]")),  # [H-003]
            company_role=row.get("company_role", ""),  # [H-003]
            mandate_topic=row.get("mandate_topic", ""),  # [H-003]
            mandate_evidence_refs=json.loads(row.get("mandate_evidence_refs_json", "[]")),  # [H-003]
            candidate_type=row.get("candidate_type", ""),  # [H-004] mandate_ambush_score
            ambush_score=row.get("ambush_score", 0.0),  # [H-004]
            mandate_score_component=row.get("mandate_score_component", 0.0),  # [H-004]
            beneficiary_score_component=row.get("beneficiary_score_component", 0.0),  # [H-004]
            pricing_gap_score=row.get("pricing_gap_score", 0.0),  # [H-004]
            overheat_penalty=row.get("overheat_penalty", 0.0),  # [H-004]
            candidate_type_reason=row.get("candidate_type_reason", ""),  # [H-004]
            deep_ta_route=row.get("deep_ta_route", ""),  # [H-004]
            deep_ta_route_reason=row.get("deep_ta_route_reason", ""),  # [H-004]
            ambush_reasons=json.loads(row.get("ambush_reasons_json", "[]")),  # [H-004]
            ambush_evidence_refs=json.loads(row.get("ambush_evidence_refs_json", "[]")),  # [H-004]
            research_queue=row.get("research_queue", ""),  # [H-007] mandate_ta_queue_router
            research_intent=row.get("research_intent", ""),  # [H-007] mandate_ta_queue_router
            research_route_reason=row.get("research_route_reason", ""),  # [H-007] mandate_ta_queue_router
            watchlist_note=row.get("watchlist_note", ""),  # [H-008] mandate_watchlist_note
            watchlist_note_suggested=row.get("watchlist_note_suggested", ""),  # [H-008]
            watchlist_topic=row.get("watchlist_topic", ""),  # [H-008]
            watchlist_benefit_score=row.get("watchlist_benefit_score", 0.0),  # [H-008]
            watchlist_consensus_score=row.get("watchlist_consensus_score", 0.0),  # [H-008]
            watchlist_evidence_gap=json.loads(row.get("watchlist_evidence_gap_json", "[]")),  # [H-008]
            counter_evidence=json.loads(row.get("counter_evidence_json", "[]")),  # [H-009]
            overheat_flags=json.loads(row.get("overheat_flags_json", "[]")),  # [H-009]
            downgrade_reasons=json.loads(row.get("downgrade_reasons_json", "[]")),  # [H-009]
            what_would_change_mind=json.loads(row.get("what_would_change_mind_json", "[]")),  # [H-009]
            topic_lifecycle_state=row.get("topic_lifecycle_state", ""),  # [H-010] mandate_topic_lifecycle
            topic_lifecycle_reason=row.get("topic_lifecycle_reason", ""),  # [H-010]
            topic_last_signal_date=row.get("topic_last_signal_date", ""),  # [H-010]
            topic_signal_count=row.get("topic_signal_count", 0),  # [H-010]
            contradiction_level=row.get("contradiction_level", ""),  # [H-011] candidate_contradiction_explainer
            contradiction_items=json.loads(row.get("contradiction_items_json", "[]")),  # [H-011]
            blocking_evidence_gaps=json.loads(row.get("blocking_evidence_gaps_json", "[]")),  # [H-011]
            next_verification_steps=json.loads(row.get("next_verification_steps_json", "[]")),  # [H-011]
            # [TF-PERSIST-001] split_score_persistence — restore 8 split-score/
            # reason fields so DB round-trip preserves real values instead of
            # falling back to dataclass defaults.
            technical_score=row.get("technical_score", 0.0),  # [TF-QUALITY-002]
            policy_score=row.get("policy_score", 0.0),  # [TF-QUALITY-002]
            fund_flow_score=row.get("fund_flow_score", 0.0),  # [TF-QUALITY-002]
            event_score=row.get("event_score", 0.0),  # [TF-QUALITY-002]
            risk_penalty_score=row.get("risk_penalty_score", 0.0),  # [TF-QUALITY-002]
            data_quality_score=row.get("data_quality_score", 0.0),  # [TF-QUALITY-002]
            ranking_reasons=json.loads(row.get("ranking_reasons_json", "[]")),  # [TF-QUALITY-002]
            weakness_reasons=json.loads(row.get("weakness_reasons_json", "[]")),  # [TF-QUALITY-002]
        )


@dataclass
class Signal:
    """One signal event — row in tradeflow_signals."""
    symbol: str
    signal_type: str
    signal_level: str = "info"
    source: str = "tradeflow"
    evidence: dict = field(default_factory=dict)
    action_hint: str = "OBSERVE"
    status: str = "new"
    signal_time: str = ""
    created_at: str = ""

    def __post_init__(self):
        if not self.signal_time:
            self.signal_time = datetime.now().isoformat()
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        # validate action_hint
        if self.action_hint not in ALLOWED_ACTIONS:
            self.action_hint = "OBSERVE"

    def to_db_row(self) -> dict:
        return {
            "signal_time": self.signal_time,
            "symbol": self.symbol,
            "signal_type": self.signal_type,
            "signal_level": self.signal_level,
            "source": self.source,
            "evidence_json": json.dumps(self.evidence, ensure_ascii=False),
            "action_hint": self.action_hint,
            "status": self.status,
        }


@dataclass
class DailyPlan:
    """Daily pre-market plan — row in tradeflow_daily_plans."""
    trade_date: str
    mode: str = "pre_market"
    summary: str = ""
    candidates: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    created_at: str = ""
    plan_date: str = ""  # [TF-DATE-001] tradeflow_date_semantics
    effective_trade_date: str = ""  # [TF-DATE-001] tradeflow_date_semantics
    observe_date: str = ""  # [TF-DATE-001] tradeflow_date_semantics

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()

    def validate(self) -> list[str]:
        """Validate plan — return list of issues (empty = OK)."""
        issues = []
        for word in FORBIDDEN_WORDS:
            if word in self.summary:
                issues.append(f"Forbidden word in summary: {word}")
        for c in self.candidates:
            action = c.get("action", "")
            if action and action not in ALLOWED_ACTIONS:
                issues.append(f"Invalid action '{action}' for {c.get('symbol', '?')}")
        return issues

    def to_db_row(self) -> dict:
        return {
            "trade_date": self.trade_date,
            "mode": self.mode,
            "summary": self.summary,
            "candidates_json": json.dumps(self.candidates, ensure_ascii=False),
            "metadata_json": json.dumps(self.metadata, ensure_ascii=False),
            "created_at": self.created_at,
            "plan_date": self.plan_date,  # [TF-DATE-001] tradeflow_date_semantics
            "effective_trade_date": self.effective_trade_date,  # [TF-DATE-001]
            "observe_date": self.observe_date,  # [TF-DATE-001]
        }

    def render_text(self) -> str:
        """Render a human-readable plan text."""
        lines = [
            f"📊 盘前计划 {self.trade_date}",
            "=" * 50,
            "",
            f"📋 摘要: {self.summary}",
            "",
        ]
        if not self.candidates:
            lines.append("（无候选）")
        for i, c in enumerate(self.candidates, 1):
            sym = c.get("symbol", "?")
            name = c.get("name", "")
            action = c.get("action", "OBSERVE")
            strategies = c.get("strategies", [])
            reason = c.get("reason", "")
            trigger = c.get("trigger_price")
            invalid = c.get("invalid_price")
            deep_ta = c.get("need_deep_ta", False)

            lines.append(f"--- 候选 {i}: {sym} {name} ---")
            lines.append(f"  动作: {action}")
            if strategies:
                lines.append(f"  策略: {', '.join(strategies)}")
            if reason:
                lines.append(f"  原因: {reason}")
            if trigger:
                lines.append(f"  触发价: {trigger}")
            if invalid:
                lines.append(f"  失效价: {invalid}")
            if deep_ta:
                lines.append(f"  ⚠️ 需要深度 TA 分析")
            # [S-001] policy_version_signal — display policy tags and evidence
            policy_tags = c.get("policy_tags", [])
            version_score = c.get("version_score", 0)
            policy_refs = c.get("policy_evidence_refs", [])
            if policy_tags:
                lines.append(f"  政策版本: {', '.join(policy_tags)} (+{version_score}分)")
                if policy_refs:
                    for ref in policy_refs[:3]:
                        snippet = ref.get("matched_text", "")[:60]
                        lines.append(f"    - {snippet}")
            # [S-002] narrative_quality_score — display narrative quality
            narrative_score = c.get("narrative_score", 0)
            narrative_reasons = c.get("narrative_reasons", [])
            if narrative_score > 0:
                lines.append(f"  叙事质量: +{narrative_score}分")
                if narrative_reasons:
                    lines.append(f"    {'; '.join(narrative_reasons)}")
            # [S-003] underwater_risk_flags — display risk penalty
            risk_flags = c.get("risk_flags", [])
            risk_penalty = c.get("risk_penalty", 0)
            risk_reasons = c.get("risk_reasons", [])
            if risk_flags:
                lines.append(f"  ⚠️ 水下风险: {', '.join(risk_flags)} ({risk_penalty}分)")
                if risk_reasons:
                    lines.append(f"    {'; '.join(risk_reasons)}")
            # [S-004] candidate_game_balance — display game balance perspective
            game_balance = c.get("game_balance", "")
            bull_case = c.get("bull_case", "")
            bear_case = c.get("bear_case", "")
            policy_case = c.get("policy_case", "")
            fund_flow_case = c.get("fund_flow_case", "")
            if game_balance:
                balance_emoji = {"favorable": "🟢", "neutral": "🟡", "crowded": "🟠", "fragile": "🔴"}
                lines.append(f"  博弈平衡: {balance_emoji.get(game_balance, '')} {game_balance}")
                if bull_case:
                    lines.append(f"    多头视角: {bull_case}")
                if bear_case:
                    lines.append(f"    空头视角: {bear_case}")
                if policy_case:
                    lines.append(f"    政策/监管: {policy_case}")
                if fund_flow_case:
                    lines.append(f"    资金结构: {fund_flow_case}")
            # [T-003] fund_flow_anomaly_pool — display fund flow anomaly
            ff_tags = c.get("fund_flow_anomaly_tags", [])
            ff_score = c.get("fund_flow_anomaly_score", 0)
            ff_ind = c.get("fund_flow_individual_summary", "")
            ff_board = c.get("fund_flow_board_summary", "")
            ff_verified = c.get("fund_flow_unit_verified", False)
            if ff_tags:
                unit_mark = "✓" if ff_verified else "⚠未校验"
                lines.append(f"  资金异动: {', '.join(ff_tags)} (+{ff_score}分) [{unit_mark}]")
                if ff_ind:
                    lines.append(f"    个股: {ff_ind}")
                if ff_board:
                    lines.append(f"    板块: {ff_board}")
            # [S-005] selection_priority_gate — display priority gate results
            priority_rank = c.get("priority_rank", "")
            composite_score_val = c.get("composite_score", 0)
            why_deep = c.get("why_deep_ta", "")
            why_not_deep = c.get("why_not_deep_ta", "")
            missing_ev = c.get("missing_evidence", [])
            data_comp = c.get("data_completeness", 0)
            if priority_rank:
                rank_emoji = {"A": "🟢", "B": "🟡", "C": "🔴"}
                lines.append(f"  优先级: {rank_emoji.get(priority_rank, '')} {priority_rank}层 | 综合分: {composite_score_val:.1f} | 完整度: {data_comp:.0%}")
                if why_deep:
                    lines.append(f"    值得深挖: {why_deep}")
                if why_not_deep:
                    lines.append(f"    暂不深挖: {why_not_deep}")
                if missing_ev:
                    lines.append(f"    缺少证据: {', '.join(missing_ev[:5])}")
            # [S-007] candidate_tier_budget — display tier and budget allocation
            tier = c.get("tier", "")
            ta_budget = c.get("ta_budget_priority", 0)
            tier_reason_text = c.get("tier_reason", "")
            missing_upgrade = c.get("missing_evidence_for_upgrade", [])
            if tier:
                tier_label = {"A": "优先深挖", "B": "观察等待", "C": "暂不关注"}.get(tier, "")
                lines.append(f"  分层: {tier}层({tier_label}) | TA预算: {ta_budget} tokens")
                if tier_reason_text:
                    lines.append(f"    分层原因: {tier_reason_text}")
                if missing_upgrade:
                    lines.append(f"    升级所需: {'; '.join(missing_upgrade[:4])}")
            # [S-008] tradeflow_evidence_gate — display evidence completeness gate
            tf_comp = c.get("tradeflow_data_completeness", 0)
            missing_df = c.get("missing_data_fields", [])
            what_up = c.get("what_to_upgrade", [])
            gate_applied = c.get("evidence_gate_applied", False)
            if tf_comp > 0 or missing_df or gate_applied:
                gate_mark = "⚠门禁降级" if gate_applied else ""
                lines.append(f"  证据完整度: {tf_comp:.0%} {gate_mark}")
                if missing_df:
                    lines.append(f"    缺失字段: {', '.join(missing_df[:5])}")
                if what_up:
                    lines.append(f"    升级所需: {'; '.join(what_up[:4])}")
            # [M-005] intraday_observe_state — display observe state
            obs_state = c.get("observe_state", "")
            obs_count = c.get("observe_trigger_count", 0)
            obs_time = c.get("observe_first_trigger_time", "")
            if obs_state and obs_state != "WAITING":
                state_label = {"TRIGGERED": "已触发", "INVALIDATED": "已失效", "EXPIRED": "已到期"}.get(obs_state, obs_state)
                lines.append(f"  盘中观察: {state_label} (触发{obs_count}次)")
                if obs_time:
                    lines.append(f"    首次触发: {obs_time}")
            # [M-006] gated_deep_ta_dispatch — display deep TA dispatch status
            dta_status = c.get("deep_ta_status", "")
            dta_reason = c.get("deep_ta_dispatch_reason", "")
            dta_model = c.get("deep_ta_model", "")
            dta_pos = c.get("deep_ta_position_context", "")
            if dta_status:
                status_label = {
                    "PENDING": "待调度",
                    "BLOCKED": "已拦截",
                    "DISPATCHED": "已调度",
                    "SUCCESS": "已完成",
                    "FAILED": "失败",
                }.get(dta_status, dta_status)
                lines.append(f"  深度TA: {status_label}")
                if dta_reason:
                    lines.append(f"    原因: {dta_reason}")
                if dta_model:
                    lines.append(f"    模型: {dta_model}")
                if dta_pos:
                    lines.append(f"    持仓: {dta_pos}")
            # [H-004] mandate_ambush_score — display candidate type and ambush score
            cand_type = c.get("candidate_type", "")
            ambush_sc = c.get("ambush_score", 0)
            mandate_sc = c.get("mandate_score_component", 0)
            benef_sc = c.get("beneficiary_score_component", 0)
            pricing_sc = c.get("pricing_gap_score", 0)
            overheat_pen = c.get("overheat_penalty", 0)
            type_reason = c.get("candidate_type_reason", "")
            ta_route = c.get("deep_ta_route", "")
            ta_route_reason = c.get("deep_ta_route_reason", "")
            if cand_type:
                type_label = {
                    "POLICY_AMBUSH": "🟢政策左侧埋伏",
                    "POLICY_CONFIRM": "🔵政策右侧确认",
                    "TECH_TRADE": "⚪技术交易",
                    "EVENT_WATCH": "🟡事件观察",
                    "PSEUDO_POLICY": "🔴伪政策题材",
                    "OVERHEATED_AVOID": "⛔过热规避",
                }.get(cand_type, cand_type)
                lines.append(f"  昊天分类: {type_label} | 埋伏分: {ambush_sc:.0f}")
                if type_reason:
                    lines.append(f"    分类原因: {type_reason}")
                lines.append(f"    政策={mandate_sc:.0f} 受益={benef_sc:.0f} 价差={pricing_sc:.0f} 过热={overheat_pen:.0f}")
                if ta_route:
                    route_label = {
                        "policy_verify": "政策验证",
                        "deep_ta": "深度TA",
                        "short_term": "短线/做T",
                        "observe": "观察等待",
                        "skip": "跳过",
                    }.get(ta_route, ta_route)
                    lines.append(f"    TA路由: {route_label} — {ta_route_reason}")
            # [H-007] mandate_ta_queue_router — display research queue
            r_queue = c.get("research_queue", "")
            r_intent = c.get("research_intent", "")
            r_reason = c.get("research_route_reason", "")
            if r_queue:
                queue_label = {
                    "MIDLINE_POLICY": "中线政策研究",
                    "TA_CONFIRM": "TA深度确认",
                    "SHORT_TERM_TRADE": "短线交易",
                    "WATCH_ONLY": "仅观察",
                    "REJECTED": "已拒绝",
                }.get(r_queue, r_queue)
                intent_label = {
                    "policy_validation": "政策验证",
                    "trend_confirmation": "趋势确认",
                    "risk_review": "风控审查",
                }.get(r_intent, r_intent)
                lines.append(f"  研究队列: {queue_label} ({intent_label})")
                if r_reason:
                    lines.append(f"    分流原因: {r_reason}")
            # [H-008] mandate_watchlist_note — display watchlist note summary
            wl_topic = c.get("watchlist_topic", "")
            wl_note = c.get("watchlist_note_suggested", "")
            wl_benefit = c.get("watchlist_benefit_score", 0)
            wl_consensus = c.get("watchlist_consensus_score", 0)
            wl_gap = c.get("watchlist_evidence_gap", [])
            wl_existing = c.get("watchlist_note", "")
            if wl_note:
                lines.append(f"  自选备注: {wl_note}")
                if wl_existing and wl_existing != wl_note:
                    lines.append(f"    用户备注: {wl_existing}")
                if wl_topic:
                    lines.append(f"    主题: {wl_topic} | 利好: {wl_benefit:.1f} | 共识: {wl_consensus:.0f}")
                if wl_gap:
                    lines.append(f"    缺口: {', '.join(wl_gap[:4])}")
            lines.append("")

        return "\n".join(lines)
