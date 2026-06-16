# TradeFlow schema field coverage
"""Verify TradeFlowCandidateItem exposes all 8 sub-scores used by frontend."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.tradeflow_schemas import TradeFlowCandidateItem


def test_candidate_item_has_all_sub_scores():
    """Frontend expects these 8 numeric/list fields; missing any → 0.0 fallback."""
    fields = TradeFlowCandidateItem.model_fields
    required = {
        "technical_score": float,
        "policy_score": float,
        "fund_flow_score": float,
        "event_score": float,
        "risk_penalty_score": float,
        "data_quality_score": float,
        "ranking_reasons": list,
        "weakness_reasons": list,
    }
    for name, expected_type in required.items():
        assert name in fields, f"Missing field: {name}"
    # All 8 present
    assert len(required) == 8
