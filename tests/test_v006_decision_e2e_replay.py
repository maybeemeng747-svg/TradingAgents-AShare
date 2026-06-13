"""[V-006] decision_semantics_e2e — End-to-end action-semantics replay.

Traces the full chain from signal text → _extract_decision_semantics →
resolve_report_fields → create_report (DB write) → DB read-back →
notification payload (Bark / WeCom), verifying that the 3-layer semantics
(research_direction / execution_action / action_label) are consistent
across DB lightweight columns, result_data JSON, and push payloads.

Five canonical scenarios from the V-006 spec:
  1. 未持仓偏多无触发价 → 等待触发 / WAIT
  2. 未持仓看多有触发价 → 条件入场 / ENTER
  3. 未持仓偏空       → 回避 / WAIT
  4. 已持仓中性       → 持有 / HOLD
  5. 已持仓偏空       → 条件减仓 / REDUCE

Rules:
  - No LLM calls.
  - No prompt modifications.
  - Uses in-memory SQLite only — never touches production DB.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import sessionmaker

from tradingagents.graph.signal_processing import _extract_decision_semantics


# ─── Scenario fixtures ───────────────────────────────────────────────────────

SCENARIOS = [
    {
        "id": "S1_no_pos_bull_no_trigger",
        "label": "未持仓偏多无触发价",
        "verdict_direction": "偏多",
        "signal_text": '<!-- VERDICT: {"direction": "偏多"} -->\n基本面改善，技术面偏多',
        "has_position": False,
        "trigger_price": None,
        "expected_direction": "偏多",
        "expected_action": "WAIT",
        "expected_label": "等待触发",
        "expected_decision": "BUY",
    },
    {
        "id": "S2_no_pos_bull_with_trigger",
        "label": "未持仓看多有触发价",
        "verdict_direction": "看多",
        "signal_text": '<!-- VERDICT: {"direction": "看多"} -->\n放量突破，趋势确认',
        "has_position": False,
        "trigger_price": 25.50,
        "expected_direction": "看多",
        "expected_action": "ENTER",
        "expected_label": "条件入场",
        "expected_decision": "BUY",
    },
    {
        "id": "S3_no_pos_bearish",
        "label": "未持仓偏空",
        "verdict_direction": "偏空",
        "signal_text": '<!-- VERDICT: {"direction": "偏空"} -->\n盈利恶化，估值偏高',
        "has_position": False,
        "trigger_price": None,
        "expected_direction": "偏空",
        "expected_action": "WAIT",
        "expected_label": "回避",
        "expected_decision": "SELL",
    },
    {
        "id": "S4_has_pos_neutral",
        "label": "已持仓中性",
        "verdict_direction": "中性",
        "signal_text": '<!-- VERDICT: {"direction": "中性"} -->\n估值合理，业绩平稳',
        "has_position": True,
        "trigger_price": None,
        "expected_direction": "中性",
        "expected_action": "HOLD",
        "expected_label": "持有",
        "expected_decision": "HOLD",
    },
    {
        "id": "S5_has_pos_bearish",
        "label": "已持仓偏空",
        "verdict_direction": "偏空",
        "signal_text": '<!-- VERDICT: {"direction": "偏空"} -->\n行业景气下行，风险升高',
        "has_position": True,
        "trigger_price": None,
        "expected_direction": "偏空",
        "expected_action": "REDUCE",
        "expected_label": "条件减仓",
        "expected_decision": "SELL",
    },
]


# ─── DB fixture ──────────────────────────────────────────────────────────────

@pytest.fixture()
def db_session():
    """In-memory SQLite session for E2E report persistence."""
    from sqlalchemy import create_engine
    from api.database import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# ─── Helper: simulate the real graph → save pipeline ─────────────────────────

def _simulate_pipeline(db_session, scenario: dict, index: int) -> "ReportDB":
    """Simulate api/main.py lines 2167-2203: resolve → merge → create_report."""
    from api.services import report_service

    # Step 1: Build initial result_data with just the final_trade_decision
    result_data = {
        "final_trade_decision": scenario["signal_text"],
    }

    # Step 2: resolve_report_fields with has_position (as api/main.py does)
    resolved = report_service.resolve_report_fields(
        result_data=result_data,
        has_position=scenario["has_position"],
        target_price_override=scenario["trigger_price"],
    )

    # Step 3: Merge resolved semantics back into result_data (api/main.py 2175-2183)
    result_data.update({
        "research_direction": resolved["research_direction"],
        "execution_action": resolved["execution_action"],
        "action_label": resolved["action_label"],
    })

    # Step 4: create_report — simulates the DB save
    report = report_service.create_report(
        db=db_session,
        symbol=f"60100{index}.SH",
        trade_date="2026-06-14",
        decision=scenario["expected_decision"],
        result_data=result_data,
        user_id="v006-test",
        target_price_override=scenario["trigger_price"],
    )
    return report


# ─── Tests ───────────────────────────────────────────────────────────────────

class TestDecisionSemanticsLayer:
    """Layer 1: _extract_decision_semantics produces correct 3-layer output."""

    @pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
    def test_semantics_extraction(self, scenario):
        result = _extract_decision_semantics(
            scenario["signal_text"],
            has_position=scenario["has_position"],
            trigger_price=scenario["trigger_price"],
        )
        assert result.research_direction == scenario["expected_direction"], (
            f"{scenario['id']}: direction mismatch"
        )
        assert result.execution_action == scenario["expected_action"], (
            f"{scenario['id']}: action mismatch"
        )
        assert result.action_label == scenario["expected_label"], (
            f"{scenario['id']}: label mismatch"
        )


class TestReportPersistenceE2E:
    """Layer 2: DB columns and result_data JSON are consistent after save/read."""

    @pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
    def test_db_columns_match_expected(self, db_session, scenario):
        report = _simulate_pipeline(db_session, scenario, SCENARIOS.index(scenario))

        from api.services.report_service import get_report
        loaded = get_report(db_session, report.id)

        assert loaded.research_direction == scenario["expected_direction"]
        assert loaded.execution_action == scenario["expected_action"]
        assert loaded.action_label == scenario["expected_label"]

    @pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
    def test_result_data_preserves_semantics(self, db_session, scenario):
        """DECISION-004 regression: pre-computed semantics survive create_report."""
        report = _simulate_pipeline(db_session, scenario, SCENARIOS.index(scenario))

        from api.services.report_service import get_report
        loaded = get_report(db_session, report.id)

        rd = loaded.result_data
        assert isinstance(rd, dict)
        assert rd["research_direction"] == scenario["expected_direction"]
        assert rd["execution_action"] == scenario["expected_action"]
        assert rd["action_label"] == scenario["expected_label"]

    @pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
    def test_db_column_matches_result_data(self, db_session, scenario):
        """The lightweight column and result_data JSON must agree."""
        report = _simulate_pipeline(db_session, scenario, SCENARIOS.index(scenario))

        from api.services.report_service import get_report
        loaded = get_report(db_session, report.id)
        rd = loaded.result_data

        assert loaded.research_direction == rd["research_direction"]
        assert loaded.execution_action == rd["execution_action"]
        assert loaded.action_label == rd["action_label"]


class TestNotificationPayloadE2E:
    """Layer 3: Bark and WeCom payloads use action_label, not just decision."""

    @pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
    def test_bark_payload_uses_action_label(self, db_session, scenario):
        report = _simulate_pipeline(db_session, scenario, SCENARIOS.index(scenario))

        from api.services.bark_notification_service import build_report_payload
        payload = build_report_payload(report)

        title = payload["title"]
        assert scenario["expected_label"] in title, (
            f"Bark title should contain action_label='{scenario['expected_label']}', got: {title}"
        )
        # Body includes execution_action code
        assert scenario["expected_action"] in payload["body"]

    @pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
    def test_wecom_message_uses_action_label(self, db_session, scenario):
        report = _simulate_pipeline(db_session, scenario, SCENARIOS.index(scenario))

        from api.services.wecom_notification_service import build_report_message
        msg = build_report_message(report)

        assert f"动作：{scenario['expected_label']}" in msg, (
            f"WeCom msg should contain 动作：{scenario['expected_label']}, got: {msg}"
        )
        assert f"方向：{scenario['expected_direction']}" in msg
        assert f"动作码：{scenario['expected_action']}" in msg


class TestNotAllHold:
    """V-006 acceptance: at least 5 samples, action labels not all '持有'."""

    def test_action_labels_are_diverse(self):
        labels = set()
        for scenario in SCENARIOS:
            result = _extract_decision_semantics(
                scenario["signal_text"],
                has_position=scenario["has_position"],
                trigger_price=scenario["trigger_price"],
            )
            labels.add(result.action_label)

        assert len(labels) >= 4, f"Expected >=4 distinct labels, got {labels}"
        assert "持有" in labels, "HOLD scenario should produce 持有"
        # Not all 持有
        assert labels != {"持有"}, f"All labels are 持有 — semantics layering failed: {labels}"

    def test_execution_actions_are_diverse(self):
        actions = set()
        for scenario in SCENARIOS:
            result = _extract_decision_semantics(
                scenario["signal_text"],
                has_position=scenario["has_position"],
                trigger_price=scenario["trigger_price"],
            )
            actions.add(result.execution_action)

        assert "WAIT" in actions
        assert "ENTER" in actions
        assert "HOLD" in actions
        assert "REDUCE" in actions
        assert len(actions) >= 4


class TestFullChainConsistency:
    """Single-test trace: semantics → resolve → DB → read-back → notifications."""

    def test_all_five_scenarios_e2e(self, db_session):
        from api.services.report_service import get_report
        from api.services.bark_notification_service import build_report_payload
        from api.services.wecom_notification_service import build_report_message

        results = []

        for i, scenario in enumerate(SCENARIOS):
            # Persist
            report = _simulate_pipeline(db_session, scenario, i)
            loaded = get_report(db_session, report.id)

            # Bark
            bark = build_report_payload(loaded)
            # WeCom
            wecom = build_report_message(loaded)

            results.append({
                "scenario": scenario["id"],
                "db_direction": loaded.research_direction,
                "db_action": loaded.execution_action,
                "db_label": loaded.action_label,
                "rd_direction": loaded.result_data.get("research_direction"),
                "rd_action": loaded.result_data.get("execution_action"),
                "rd_label": loaded.result_data.get("action_label"),
                "bark_title_has_label": scenario["expected_label"] in bark["title"],
                "wecom_has_label": f"动作：{scenario['expected_label']}" in wecom,
            })

        # Verify every scenario
        for i, (scenario, r) in enumerate(zip(SCENARIOS, results)):
            sid = scenario["id"]
            assert r["db_direction"] == scenario["expected_direction"], f"{sid}: DB direction"
            assert r["db_action"] == scenario["expected_action"], f"{sid}: DB action"
            assert r["db_label"] == scenario["expected_label"], f"{sid}: DB label"
            assert r["rd_direction"] == scenario["expected_direction"], f"{sid}: result_data direction"
            assert r["rd_action"] == scenario["expected_action"], f"{sid}: result_data action"
            assert r["rd_label"] == scenario["expected_label"], f"{sid}: result_data label"
            assert r["bark_title_has_label"], f"{sid}: Bark title missing action_label"
            assert r["wecom_has_label"], f"{sid}: WeCom missing action_label"

        # Verify diversity
        labels = {r["db_label"] for r in results}
        assert len(labels) >= 4
        assert labels != {"持有"}
