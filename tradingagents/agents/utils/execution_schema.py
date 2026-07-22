"""Execution Schema — structured output for report execution decisions.  # [N-005] execution_schema

Provides machine-readable alternative to format_execution_block() text output.
Does NOT replace text output; produces structured sidecar data.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional

# [C-004] action_enum — single canonical enum; no local duplicate.
from tradingagents.agents.utils.trade_actions import TradeAction

# Backward-compatible alias so existing imports of ``Action`` keep working.
Action = TradeAction


class BuyLevel(Enum):
    LEVEL_0 = 0
    LEVEL_1 = 1
    LEVEL_2 = 2
    LEVEL_3 = 3
    LEVEL_4 = 4


class RiskLevel(Enum):
    LEVEL_0 = 0
    LEVEL_1 = 1
    LEVEL_2 = 2
    LEVEL_3 = 3
    LEVEL_4 = 4


@dataclass
class ExecutionSchema:
    action: str
    buy_level: int = 0
    risk_level: int = 0
    trigger_price: Optional[float] = None
    invalid_price: Optional[float] = None
    confidence: str = "中"
    opportunity_score: int = 0
    source_coverage: int = 0
    evidence_coverage: int = 0
    strong_action_gate_passed: bool = True
    gate_failures: list[str] = field(default_factory=list)
    position_status: str = "unknown"
    analysis_intent: str = "watch"
    horizon: str = "short"
    data_quality_flags: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    valuation_mismatch: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    def validate(self) -> list[str]:
        issues: list[str] = []
        valid_actions = {a.value for a in Action}
        if self.action not in valid_actions:
            issues.append(f"Invalid action: {self.action}")
        if self.position_status == "no_position" and self.action != "WAIT":
            issues.append(f"no_position but action={self.action}, should be WAIT")
        if self.action == "ENTER" and self.buy_level < 3:
            issues.append(f"ENTER but buy_level={self.buy_level}, need >= 3")
        if self.action in ("REDUCE", "EXIT") and self.position_status != "has_position":
            issues.append(f"{self.action} but position_status={self.position_status}")
        if self.evidence_coverage < 50:
            self.data_quality_flags.append("low_evidence_coverage")
        if self.source_coverage < 50:
            self.data_quality_flags.append("low_source_coverage")
        return issues


def build_execution_schema(
    source_coverage: int,
    evidence_coverage: int,
    confidence: str,
    opportunity_score: int,
    risk_level: int,
    buy_level: int,
    position_status: str = "unknown",
    analysis_intent: str = "watch",
    horizon: str = "short",
    strong_action_gate: dict = None,
    valuation_mismatch: bool = False,
) -> ExecutionSchema:
    if position_status == "no_position":
        action = "WAIT"
    elif analysis_intent == "stop_loss":
        action = "EXIT"
    elif analysis_intent == "reduce":
        action = "REDUCE"
    elif analysis_intent in ("add", "entry") and buy_level >= 3:
        action = "ENTER"
    else:
        action = "HOLD"

    gate = strong_action_gate or {"passed": True, "failures": []}

    return ExecutionSchema(
        action=action,
        buy_level=buy_level,
        risk_level=risk_level,
        confidence=confidence,
        opportunity_score=opportunity_score,
        source_coverage=source_coverage,
        evidence_coverage=evidence_coverage,
        strong_action_gate_passed=gate["passed"],
        gate_failures=gate.get("failures", []),
        position_status=position_status,
        analysis_intent=analysis_intent,
        horizon=horizon,
        valuation_mismatch=valuation_mismatch,
    )
