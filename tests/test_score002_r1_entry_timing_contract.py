# [SCORE-002-R1] entry_timing_api_contract
"""Tests for SCORE-002-R1: entry_timing API contract & risk deduction persistence.

Covers:
  1. Structured entry_timing model in API schema (no longer Dict[str, Any]).
  2. Risk deductions persisted as structured records
     (source_field / raw_score / applied_deduction).
  3. entry_timing_card round-trips through EntryTimingCard pydantic model
     and the full TradeFlowCandidateItem.
  4. risk_deductions attributable to the composite entry_timing.
"""

from __future__ import annotations

import json

import pytest

from tradingagents.tradeflow.entry_timing_score import (
    EntryTimingResult,
    RiskDeductionRecord,
    compute_entry_timing,
    entry_timing_to_dict,
)


# ── Risk deduction persistence ─────────────────────────────────────

class TestRiskDeductionPersistence:
    """[SCORE-002-R1] risk_deductions structured records survive the card."""

    def test_no_deductions_when_clean(self) -> None:
        """A clean high-score candidate records no risk deductions."""
        result = compute_entry_timing(
            pricing_gap_score=25.0, current_price=15.0, support_price=14.5,
            game_balance="favorable", resonance_count=3, positive_category_count=3,
            fund_flow_anomaly_score=12.0, fund_flow_unit_verified=True,
            technical_score=80.0, event_score=75.0, ambush_score=60.0,
            narrative_score=70.0, contradiction_level="none",
            risk_penalty=-3.0, invalid_price=13.0,
        )
        # small risk_penalty (-3) produces a tiny deduction; contradiction none
        records = result.risk_deductions
        # contradiction_level=none should not appear
        assert all(r.source_field != "contradiction_level" for r in records)

    def test_timing_risk_flag_deduction_recorded(self) -> None:
        """A timing risk flag produces a structured deduction record."""
        result = compute_entry_timing(
            pricing_gap_score=15.0, current_price=10.0, support_price=9.5,
            game_balance="favorable", resonance_count=2, positive_category_count=2,
            fund_flow_anomaly_score=6.0, fund_flow_unit_verified=True,
            technical_score=50.0, event_score=40.0, ambush_score=30.0,
            narrative_score=35.0, contradiction_level="",
            risk_penalty=-15.0, invalid_price=8.5,
            risk_flags=["第一次大跌风险"],
        )
        flag_records = [r for r in result.risk_deductions if r.source_field == "timing_risk_flags"]
        assert len(flag_records) == 1
        rec = flag_records[0]
        assert rec.raw_score == 1.0  # one flag
        assert rec.applied_deduction > 0.0
        # entry_timing_pre_flags must be set when flags applied
        assert result.entry_timing_pre_flags is not None
        assert result.entry_timing_pre_flags > result.entry_timing
        assert round(result.entry_timing_pre_flags - result.entry_timing, 1) == rec.applied_deduction

    def test_multiple_flag_deduction_capped(self) -> None:
        """Multiple flags produce a single aggregated record (cap 30)."""
        result = compute_entry_timing(
            pricing_gap_score=20.0, current_price=8.0, support_price=7.5,
            game_balance="fragile", resonance_count=1, positive_category_count=1,
            overheat_penalty=-10.0, fund_flow_anomaly_score=3.0,
            fund_flow_unit_verified=False, technical_score=30.0,
            event_score=20.0, ambush_score=10.0, narrative_score=15.0,
            contradiction_level="high", risk_penalty=-25.0, invalid_price=6.0,
            risk_flags=["跌停未打开"], overheat_flags=["板块退潮"],
        )
        flag_records = [r for r in result.risk_deductions if r.source_field == "timing_risk_flags"]
        assert len(flag_records) == 1
        rec = flag_records[0]
        assert rec.raw_score == 2.0  # two flags
        # cap is 30 points
        assert rec.applied_deduction <= 30.0

    def test_contradiction_deduction_recorded(self) -> None:
        """contradiction_level deduction is persisted with its penalty value."""
        result = compute_entry_timing(
            pricing_gap_score=15.0, current_price=10.0, support_price=9.5,
            game_balance="favorable", resonance_count=2, positive_category_count=2,
            fund_flow_anomaly_score=6.0, fund_flow_unit_verified=True,
            technical_score=50.0, event_score=75.0, ambush_score=60.0,
            narrative_score=70.0, contradiction_level="high",
            risk_penalty=-3.0, invalid_price=8.5,
        )
        contra = [r for r in result.risk_deductions if r.source_field == "contradiction_level"]
        assert len(contra) == 1
        assert contra[0].raw_score == 50.0  # high penalty
        assert contra[0].applied_deduction > 0.0

    def test_contradiction_deduction_uses_clipped_actual_loss(self) -> None:
        result = compute_entry_timing(
            event_score=20.0,
            contradiction_level="high",
        )
        contradiction = next(
            row for row in result.risk_deductions
            if row.source_field == "contradiction_level"
        )
        assert contradiction.raw_score == 50.0
        assert contradiction.applied_deduction == 20.0

    def test_risk_penalty_deduction_recorded(self) -> None:
        """risk_penalty deduction is persisted with sign preserved."""
        result = compute_entry_timing(
            pricing_gap_score=15.0, current_price=10.0, support_price=9.5,
            game_balance="favorable", resonance_count=2, positive_category_count=2,
            fund_flow_anomaly_score=6.0, fund_flow_unit_verified=True,
            technical_score=50.0, event_score=40.0, ambush_score=30.0,
            narrative_score=35.0, contradiction_level="",
            risk_penalty=-25.0, invalid_price=8.5,
        )
        rp = [r for r in result.risk_deductions if r.source_field == "risk_penalty"]
        assert len(rp) == 1
        assert rp[0].raw_score == -25.0  # sign preserved
        assert rp[0].applied_deduction > 0.0

    def test_risk_penalty_not_recorded_when_it_does_not_lower_score(self) -> None:
        """Audit deductions must reflect the actual composite score change."""
        result = compute_entry_timing(
            risk_penalty=-10.0,
            invalid_price=9.8,
            current_price=10.0,
        )
        assert not any(
            row.source_field == "risk_penalty"
            for row in result.risk_deductions
        )

    def test_record_fields_complete(self) -> None:
        """Each RiskDeductionRecord has all three required fields."""
        result = compute_entry_timing(
            risk_penalty=-25.0, invalid_price=8.5, current_price=10.0,
            risk_flags=["跌停未打开"], contradiction_level="high",
            event_score=50.0,
        )
        for rec in result.risk_deductions:
            assert isinstance(rec, RiskDeductionRecord)
            assert rec.source_field  # non-empty
            assert isinstance(rec.raw_score, float)
            assert isinstance(rec.applied_deduction, float)
            assert rec.applied_deduction >= 0.0


# ── Serialization ────────────────────────────────────────────────────

class TestSerializationR1:
    """[SCORE-002-R1] risk_deductions survive JSON round-trip."""

    def test_risk_deductions_in_dict(self) -> None:
        result = compute_entry_timing(
            risk_penalty=-25.0, invalid_price=8.5, current_price=10.0,
            risk_flags=["跌停未打开"], contradiction_level="high",
            event_score=50.0,
        )
        d = entry_timing_to_dict(result)
        assert "risk_deductions" in d
        assert "entry_timing_pre_flags" in d
        assert isinstance(d["risk_deductions"], list)
        for rec in d["risk_deductions"]:
            assert set(rec.keys()) == {"source_field", "raw_score", "applied_deduction"}

    def test_json_serializable(self) -> None:
        result = compute_entry_timing(
            risk_penalty=-25.0, invalid_price=8.5, current_price=10.0,
            risk_flags=["跌停未打开"], event_score=50.0,
        )
        d = entry_timing_to_dict(result)
        s = json.dumps(d, ensure_ascii=False)
        back = json.loads(s)
        assert len(back["risk_deductions"]) == len(result.risk_deductions)

    def test_no_flags_pre_flags_is_none(self) -> None:
        """entry_timing_pre_flags is None when no timing risk flags present."""
        result = compute_entry_timing(pricing_gap_score=20.0)
        assert result.entry_timing_pre_flags is None


# ── API schema contract ─────────────────────────────────────────────

class TestEntryTimingSchemaContract:
    """[SCORE-002-R1] entry_timing_card is a structured model, not Dict[str, Any]."""

    def test_entry_timing_card_validates_as_model(self) -> None:
        from api.tradeflow_schemas import EntryTimingCard
        result = compute_entry_timing(
            pricing_gap_score=25.0, current_price=15.0, support_price=14.5,
            game_balance="favorable", resonance_count=3, positive_category_count=3,
            fund_flow_anomaly_score=12.0, fund_flow_unit_verified=True,
            technical_score=80.0, event_score=75.0, ambush_score=60.0,
            narrative_score=70.0, contradiction_level="none",
            risk_penalty=-3.0, invalid_price=13.0,
        )
        d = entry_timing_to_dict(result)
        card = EntryTimingCard.model_validate(d)
        assert card.entry_timing == result.entry_timing
        assert card.data_status == result.data_status
        assert len(card.risk_deductions) == len(result.risk_deductions)

    def test_candidate_item_has_typed_card(self) -> None:
        """TradeFlowCandidateItem exposes entry_timing_card as EntryTimingCard."""
        from api.tradeflow_schemas import EntryTimingCard, TradeFlowCandidateItem
        item = {
            "symbol": "600519.SH",
            "pricing_gap_score": 20.0,
            "current_price": 15.0,
            "support_price": 14.0,
            "game_balance": "favorable",
            "resonance_count": 3,
            "positive_category_count": 3,
            "fund_flow_anomaly_score": 10.0,
            "fund_flow_unit_verified": True,
            "technical_score": 70.0,
            "event_score": 60.0,
            "ambush_score": 50.0,
            "narrative_score": 55.0,
            "risk_penalty": -5.0,
            "invalid_price": 13.0,
        }
        from api.services.tradeflow_service import _enrich_candidate_with_entry_timing
        enriched = _enrich_candidate_with_entry_timing(item)
        ci = TradeFlowCandidateItem.model_validate(enriched)
        assert isinstance(ci.entry_timing_card, EntryTimingCard)
        assert ci.entry_timing_card.entry_timing > 0.0

    def test_service_enrichment_carries_risk_deductions(self) -> None:
        """Service enrichment injects risk_deductions into the card dict."""
        from api.services.tradeflow_service import _enrich_candidate_with_entry_timing
        item = {
            "symbol": "600519.SH",
            "current_price": 8.0,
            "support_price": 7.5,
            "game_balance": "fragile",
            "resonance_count": 1,
            "positive_category_count": 1,
            "overheat_penalty": -10.0,
            "fund_flow_anomaly_score": 3.0,
            "fund_flow_unit_verified": False,
            "technical_score": 30.0,
            "event_score": 20.0,
            "ambush_score": 10.0,
            "narrative_score": 15.0,
            "contradiction_level": "high",
            "risk_penalty": -25.0,
            "invalid_price": 6.0,
            "risk_flags": ["跌停未打开"],
            "overheat_flags": ["板块退潮"],
        }
        result = _enrich_candidate_with_entry_timing(item)
        card = result["entry_timing_card"]
        assert "risk_deductions" in card
        assert len(card["risk_deductions"]) >= 1
        sources = {r["source_field"] for r in card["risk_deductions"]}
        assert "timing_risk_flags" in sources
        assert "contradiction_level" in sources
        assert "risk_penalty" in sources

    def test_service_enrichment_reads_persisted_risk_penalty_score(self) -> None:
        """Persisted TradeFlow rows use risk_penalty_score."""
        from api.services.tradeflow_service import _enrich_candidate_with_entry_timing

        item = {
            "symbol": "600519.SH",
            "current_price": 10.0,
            "invalid_price": 8.0,
            "risk_penalty_score": 25.0,
        }
        card = _enrich_candidate_with_entry_timing(item)["entry_timing_card"]
        deductions = {
            row["source_field"]: row for row in card["risk_deductions"]
        }
        assert deductions["risk_penalty"]["raw_score"] == -25.0
        assert deductions["risk_penalty"]["applied_deduction"] > 0
