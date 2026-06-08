"""DECISION-002: Historical report replay tests.

Replays 5 real delta_log samples through _extract_decision_semantics and
asserts the expected research_direction / execution_action / action_label.

Rules:
  - No LLM calls
  - No prompt modifications
  - No production DB writes
"""

import json
import re
from pathlib import Path

import pytest

from tradingagents.graph.signal_processing import _extract_decision_semantics

DELTA_LOG = Path(__file__).resolve().parent.parent / (
    "tradingagents/portfolio/analysis/delta_log"
)

SAMPLE_FILES = [
    "002709.SZ.json",
    "300750.SZ.json",
    "603256.SH.json",
    "002138.SZ.json",
    "600584.SH.json",
]


def _load_sample(filename: str) -> dict:
    path = DELTA_LOG / filename
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _extract_trigger_price(conclusion: str) -> float | None:
    m = re.search(r"触发价[：:]\s*([\d.]+)", conclusion)
    if m:
        val = m.group(1)
        return float(val)
    return None


def _detect_has_position(conclusion: str) -> bool | None:
    if "[C-001]" in conclusion and "未持仓" in conclusion:
        return False
    if "未持仓" in conclusion:
        return False
    if "已持仓" in conclusion or "当前持仓" in conclusion:
        return True
    return None


class TestReplay002709:
    """002709.SZ - 偏多方向，短线超跌反弹，未持仓，C-001 等待触发。"""

    def test_semantics(self):
        sample = _load_sample("002709.SZ.json")
        conclusion = sample["conclusion"]
        has_position = _detect_has_position(conclusion)
        trigger_price = _extract_trigger_price(conclusion)

        assert has_position is False
        assert trigger_price == pytest.approx(60.0)

        result = _extract_decision_semantics(
            conclusion,
            has_position=has_position,
            trigger_price=trigger_price,
        )

        assert result.research_direction == "偏多"
        assert result.execution_action == "WAIT"
        assert result.action_label == "等待触发"
        assert result.decision == "HOLD"


class TestReplay300750:
    """300750.SZ - 偏多方向，有条件买入，未持仓，C-001 等待触发。"""

    def test_semantics(self):
        sample = _load_sample("300750.SZ.json")
        conclusion = sample["conclusion"]
        has_position = _detect_has_position(conclusion)
        trigger_price = _extract_trigger_price(conclusion)

        assert has_position is False
        assert trigger_price == pytest.approx(430.0)

        result = _extract_decision_semantics(
            conclusion,
            has_position=has_position,
            trigger_price=trigger_price,
        )

        assert result.research_direction == "偏多"
        assert result.execution_action == "WAIT"
        assert result.action_label == "等待触发"
        assert result.decision == "HOLD"


class TestReplay603256:
    """603256.SH - 偏空方向，绝对禁止建仓，未持仓，C-001 等待触发。"""

    def test_semantics(self):
        sample = _load_sample("603256.SH.json")
        conclusion = sample["conclusion"]
        has_position = _detect_has_position(conclusion)
        trigger_price = _extract_trigger_price(conclusion)

        assert has_position is False
        assert trigger_price == pytest.approx(180.0)

        result = _extract_decision_semantics(
            conclusion,
            has_position=has_position,
            trigger_price=trigger_price,
        )

        assert result.research_direction == "偏空"
        assert result.execution_action == "WAIT"
        assert result.action_label == "回避"
        assert result.decision == "HOLD"


class TestReplay002138:
    """002138.SZ - 偏空方向，盈利恶化回避，未持仓，C-001 等待触发。"""

    def test_semantics(self):
        sample = _load_sample("002138.SZ.json")
        conclusion = sample["conclusion"]
        has_position = _detect_has_position(conclusion)
        trigger_price = _extract_trigger_price(conclusion)

        assert has_position is False
        assert trigger_price == pytest.approx(41.0)

        result = _extract_decision_semantics(
            conclusion,
            has_position=has_position,
            trigger_price=trigger_price,
        )

        assert result.research_direction == "偏空"
        assert result.execution_action == "WAIT"
        assert result.action_label == "回避"
        assert result.decision == "HOLD"


class TestReplay600584:
    """600584.SH - 无 VERDICT 标签，建议持有，未持仓，C-001 人工复核。"""

    def test_semantics(self):
        sample = _load_sample("600584.SH.json")
        conclusion = sample["conclusion"]
        has_position = _detect_has_position(conclusion)
        trigger_price = _extract_trigger_price(conclusion)

        assert has_position is False
        assert trigger_price is None

        result = _extract_decision_semantics(
            conclusion,
            has_position=has_position,
            trigger_price=trigger_price,
        )

        assert result.research_direction == "中性"
        assert result.execution_action == "WAIT"
        assert result.action_label == "观望"
        assert result.decision == "HOLD"
