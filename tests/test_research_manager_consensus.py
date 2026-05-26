"""Tests for _build_consensus_block — minority direction fix and edge cases.

Covers G-003: minority must exclude 0-vote directions, game_theory_report
must be counted, and the output summary must contain majority/minority info.
"""

import json
import sys
import os

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.agents.managers.research_manager import (
    _build_consensus_block,
    _ANALYST_MAP,
)


def _verdict(direction: str) -> str:
    return f'<!-- VERDICT: {{"direction": "{direction}"}} -->\nSome analysis text.'


def _state_with_directions(dir_map: dict[str, str], extra_text: dict | None = None) -> dict:
    """Build a state dict from {state_key: direction} mapping."""
    state = {}
    for key, direction in dir_map.items():
        state[key] = _verdict(direction)
    if extra_text:
        for key, text in extra_text.items():
            state[key] = state.get(key, "") + text
    return state


class TestAnalystMap:
    def test_analyst_map_has_seven_entries(self):
        assert len(_ANALYST_MAP) == 7

    def test_game_theory_in_map(self):
        keys = [k for k, _ in _ANALYST_MAP]
        assert "game_theory_report" in keys

    def test_all_seven_state_keys(self):
        expected = {
            "market_report", "sentiment_report", "news_report",
            "fundamentals_report", "smart_money_report",
            "volume_price_report", "game_theory_report",
        }
        keys = {k for k, _ in _ANALYST_MAP}
        assert keys == expected


class TestSixBearishOneBullish:
    """6 空 1 多 → 触发降权 + 基本面额外降权 (if fundamentals is minority)."""

    def test_triggers_downweight(self):
        state = _state_with_directions({
            "market_report": "看空",
            "sentiment_report": "看空",
            "news_report": "看空",
            "fundamentals_report": "看多",
            "smart_money_report": "看空",
            "volume_price_report": "看空",
            "game_theory_report": "看空",
        })
        block = _build_consensus_block(state)
        assert block is not None
        assert "多数方向：偏空" in block
        assert "少数方向：偏多" in block
        assert "fundamentals_analyst" in block
        assert "已降权" in block

    def test_extra_downweight_with_negative_cashflow(self):
        state = _state_with_directions(
            {
                "market_report": "看空",
                "sentiment_report": "看空",
                "news_report": "看空",
                "fundamentals_report": "看多",
                "smart_money_report": "看空",
                "volume_price_report": "看空",
                "game_theory_report": "看空",
            },
            extra_text={"fundamentals_report": "经营现金流为负且持续下降"},
        )
        block = _build_consensus_block(state)
        assert block is not None

        meta_match = block.split("CONSENSUS_WEIGHT:")[1].split("-->")[0].strip()
        meta = json.loads(meta_match)
        fa = [a for a in meta["minority_analysts"] if a["name"] == "fundamentals_analyst"]
        assert len(fa) == 1
        assert fa[0]["extra_downweight"] is True


class TestFiveBearishTwoBullish:
    """5 空 2 多 → 触发降权."""

    def test_triggers_downweight(self):
        state = _state_with_directions({
            "market_report": "看空",
            "sentiment_report": "看空",
            "news_report": "看空",
            "smart_money_report": "看空",
            "volume_price_report": "看空",
            "fundamentals_report": "看多",
            "game_theory_report": "看多",
        })
        block = _build_consensus_block(state)
        assert block is not None
        assert "多数方向：偏空" in block
        assert "少数方向：偏多" in block
        assert "已降权" in block

        meta_match = block.split("CONSENSUS_WEIGHT:")[1].split("-->")[0].strip()
        meta = json.loads(meta_match)
        assert meta["minority_count"] == 2


class TestFourThreeSplit:
    """4 空 3 多 → 不触发（无明显多数）."""

    def test_no_trigger(self):
        state = _state_with_directions({
            "market_report": "看空",
            "sentiment_report": "看空",
            "news_report": "看空",
            "smart_money_report": "看空",
            "fundamentals_report": "看多",
            "game_theory_report": "看多",
            "volume_price_report": "看多",
        })
        block = _build_consensus_block(state)
        assert block is None


class TestAllAgree:
    """全部一致 → 不触发."""

    def test_no_trigger_all_bullish(self):
        state = _state_with_directions({
            "market_report": "看多",
            "sentiment_report": "看多",
            "news_report": "看多",
            "fundamentals_report": "看多",
            "smart_money_report": "看多",
            "volume_price_report": "看多",
            "game_theory_report": "看多",
        })
        block = _build_consensus_block(state)
        assert block is None

    def test_no_trigger_all_bearish(self):
        state = _state_with_directions({
            "market_report": "看空",
            "sentiment_report": "看空",
            "news_report": "看空",
            "fundamentals_report": "看空",
            "smart_money_report": "看空",
            "volume_price_report": "看空",
            "game_theory_report": "看空",
        })
        block = _build_consensus_block(state)
        assert block is None


class TestNeutralVotes:
    """含中性票的场景."""

    def test_neutral_as_minority(self):
        state = _state_with_directions({
            "market_report": "看多",
            "sentiment_report": "看多",
            "news_report": "看多",
            "fundamentals_report": "看多",
            "smart_money_report": "看多",
            "volume_price_report": "中性",
            "game_theory_report": "看多",
        })
        block = _build_consensus_block(state)
        assert block is not None
        assert "少数方向：中性" in block

    def test_neutral_ignored_when_zero_votes(self):
        """5 空 1 多, 0 中性 → minority should be 偏多, not 中性."""
        state = _state_with_directions({
            "market_report": "看空",
            "sentiment_report": "看空",
            "news_report": "看空",
            "smart_money_report": "看空",
            "volume_price_report": "看空",
            "fundamentals_report": "看多",
        })
        block = _build_consensus_block(state)
        assert block is not None
        assert "少数方向：偏多" in block
        assert "少数方向：中性" not in block


class TestFiveOneSplit:
    """5 空 1 多 (only 6 reports, game_theory missing)."""

    def test_minority_is_bullish_not_neutral(self):
        state = _state_with_directions({
            "market_report": "看空",
            "sentiment_report": "看空",
            "news_report": "看空",
            "smart_money_report": "看空",
            "volume_price_report": "看空",
            "fundamentals_report": "看多",
        })
        block = _build_consensus_block(state)
        assert block is not None
        assert "多数方向：偏空" in block
        assert "少数方向：偏多" in block

        meta_match = block.split("CONSENSUS_WEIGHT:")[1].split("-->")[0].strip()
        meta = json.loads(meta_match)
        assert meta["minority_count"] == 1


class TestGameTheoryCounted:
    """game_theory_report is correctly counted as 7th analyst."""

    def test_game_theory_breaks_tie(self):
        """3 空 3 多 + game_theory=空 → 4 空 3 多 → no trigger (no clear majority)."""
        state = _state_with_directions({
            "market_report": "看空",
            "sentiment_report": "看空",
            "news_report": "看多",
            "fundamentals_report": "看多",
            "smart_money_report": "看多",
            "volume_price_report": "看空",
            "game_theory_report": "看空",
        })
        block = _build_consensus_block(state)
        assert block is None

    def test_game_theory_makes_majority(self):
        """4 空 2 多 + game_theory=空 → 5 空 2 多 → trigger."""
        state = _state_with_directions({
            "market_report": "看空",
            "sentiment_report": "看空",
            "news_report": "看空",
            "smart_money_report": "看空",
            "fundamentals_report": "看多",
            "game_theory_report": "看空",
            "volume_price_report": "看多",
        })
        block = _build_consensus_block(state)
        assert block is not None
        assert "多数方向：偏空" in block

        meta_match = block.split("CONSENSUS_WEIGHT:")[1].split("-->")[0].strip()
        meta = json.loads(meta_match)
        assert meta["minority_count"] == 2


class TestOutputSummary:
    """Output must contain majority, minority, analyst names, downweight reason."""

    def test_summary_contains_required_fields(self):
        state = _state_with_directions({
            "market_report": "看空",
            "sentiment_report": "看空",
            "news_report": "看空",
            "smart_money_report": "看空",
            "volume_price_report": "看空",
            "game_theory_report": "看空",
            "fundamentals_report": "看多",
        })
        block = _build_consensus_block(state)
        assert block is not None
        assert "多数方向" in block
        assert "少数方向" in block
        assert "fundamentals_analyst" in block
        assert "降权状态" in block
        assert "建议" in block


class TestTooFewReports:
    """Less than 3 reports → None."""

    def test_two_reports(self):
        state = _state_with_directions({
            "market_report": "看空",
            "sentiment_report": "看多",
        })
        block = _build_consensus_block(state)
        assert block is None

    def test_one_report(self):
        state = _state_with_directions({
            "market_report": "看空",
        })
        block = _build_consensus_block(state)
        assert block is None
