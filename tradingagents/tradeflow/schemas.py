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
ALL_STRATEGIES = {STRATEGY_VCP, STRATEGY_PULLBACK, STRATEGY_EVENT, STRATEGY_POLICY_VERSION}

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
            lines.append("")

        return "\n".join(lines)
