from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from api.database import Base, ReportDB
from api.services import report_service


def _make_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    return TestingSessionLocal()


def _add_report(
    db,
    *,
    status: str = "running",
    decision=None,
    final_trade_decision=None,
    result_data=None,
):
    now = datetime.now(timezone.utc)
    report = ReportDB(
        id=uuid4().hex,
        user_id=uuid4().hex,
        symbol="600519.SH",
        trade_date="2026-04-01",
        status=status,
        decision=decision,
        final_trade_decision=final_trade_decision,
        result_data=result_data,
        created_at=now,
        updated_at=now,
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return report


def test_recover_stale_active_reports_marks_empty_running_report_failed():
    db = _make_session()
    try:
        report = _add_report(db, status="running")

        result = report_service.recover_stale_active_reports(db)

        refreshed = db.query(ReportDB).filter(ReportDB.id == report.id).first()
        assert result == {"total": 1, "completed": 0, "failed": 1}
        assert refreshed is not None
        assert refreshed.status == "failed"
        assert refreshed.error == report_service.STALE_REPORT_ERROR_MESSAGE
    finally:
        db.close()


def test_legacy_avoid_without_execution_action_hides_persisted_target():
    db = _make_session()
    try:
        report = _add_report(db, status="completed")
        report.research_direction = "偏空"
        report.execution_action = None
        report.action_label = "回避"
        report.target_price = 186.0
        report.result_data = {
            "research_direction": "偏空",
            "action_label": "回避",
            "target_price": 186.0,
        }
        db.commit()
        report_id = report.id

        normalized = report_service.get_report(db, report_id)

        assert normalized.target_price is None
        assert normalized.result_data["target_price"] is None
        persisted_target = db.execute(
            text("SELECT target_price FROM reports WHERE id = :report_id"),
            {"report_id": report_id},
        ).scalar_one()
        assert persisted_target == 186.0
    finally:
        db.close()


def test_legacy_reduce_without_execution_action_keeps_trigger_target():
    report = type(
        "LegacyReport",
        (),
        {
            "research_direction": "偏空",
            "execution_action": None,
            "action_label": "条件减仓",
            "decision": "SELL",
            "target_price": 150.0,
            "result_data": None,
        },
    )()

    normalized = report_service.normalize_report_action_label(report)

    assert normalized.target_price == 150.0


def test_legacy_exit_keeps_target_even_when_label_says_avoid():
    report = type(
        "LegacyReport",
        (),
        {
            "research_direction": "偏空",
            "execution_action": None,
            "action_label": "回避",
            "decision": "SELL",
            "target_price": 145.0,
            "result_data": None,
        },
    )()

    normalized = report_service.normalize_report_action_label(report)

    assert normalized.target_price == 145.0


def test_recover_stale_active_reports_marks_partial_running_report_failed():
    db = _make_session()
    try:
        report = _add_report(
            db,
            status="running",
            final_trade_decision="结论：持有\n目标价：1750\n止损价：1650",
            result_data={"final_trade_decision": "结论：持有"},
        )

        result = report_service.recover_stale_active_reports(db)

        refreshed = db.query(ReportDB).filter(ReportDB.id == report.id).first()
        assert result == {"total": 1, "completed": 0, "failed": 1}
        assert refreshed is not None
        assert refreshed.status == "failed"
        assert refreshed.error == report_service.STALE_REPORT_ERROR_MESSAGE
    finally:
        db.close()


def test_finalize_orphan_report_marks_pending_report_failed():
    db = _make_session()
    try:
        report = _add_report(db, status="pending")

        refreshed = report_service.finalize_orphan_report(db, report)

        assert refreshed.status == "failed"
        assert refreshed.error == report_service.STALE_REPORT_ERROR_MESSAGE
    finally:
        db.close()


def test_bearish_legacy_wait_does_not_expose_manager_target_as_executable():
    resolved = report_service.resolve_report_fields(
        result_data={
            "final_trade_decision": (
                "最终审核意见：维持空仓观望。\n"
                "目标价：—\n"
                "止损价：—"
            ),
            "trader_investment_plan": "当前建议观望，等待验证。",
            "investment_plan": (
                "可执行交易方案：\n"
                "入场区间（针对做空/减仓）：130.00元 - 132.00元。\n"
                "止损位：133.50元。\n"
                "止盈/减仓条件：第一目标：股价回落至 126.78元 附近，可考虑部分减仓。"
            ),
        }
    )

    assert resolved["research_direction"] == "偏空"
    assert resolved["execution_action"] == "WAIT"
    assert resolved["target_price"] is None
    assert resolved["stop_loss_price"] == 133.5


def test_resolve_report_fields_prefers_explicit_final_prices():
    resolved = report_service.resolve_report_fields(
        result_data={
            "final_trade_decision": "结论：买入\n目标价：23.50\n止损价：20.48",
            "investment_plan": "第一目标：22.00元；止损位：19.50元。",
        }
    )

    assert resolved["target_price"] == 23.5
    assert resolved["stop_loss_price"] == 20.48


def test_resolve_report_fields_final_stop_invalidation_clears_upstream_price():
    resolved = report_service.resolve_report_fields(
        result_data={
            "final_trade_decision": "最终交易决策：条件买入。触发价：待定；止损价：待定。",
            "trader_investment_plan": "若站稳10元则买入。止损价：9元。",
        },
        has_position=False,
    )

    assert resolved["stop_loss_price"] is None


@pytest.mark.parametrize(
    "final_decision",
    (
        "最终止损价暂未给出。",
        "最终止损价目前无法确定。",
        "最终止损价尚未设置。",
        "最终止损价仍待定。",
        "最终止损价暂时不适用。",
        "最终止损价取消执行。",
    ),
)
def test_resolve_report_fields_qualified_stop_invalidation(final_decision):
    resolved = report_service.resolve_report_fields(
        result_data={
            "final_trade_decision": final_decision,
            "trader_investment_plan": "止损价：9元。",
        },
        has_position=True,
    )

    assert resolved["stop_loss_price"] is None


@pytest.mark.parametrize(
    "final_decision",
    (
        "Final decision: HOLD. Stop-loss: not applicable.",
        "Final decision: HOLD. Stop loss cancelled.",
        "Final decision: HOLD. Stop-loss price: withdrawn.",
    ),
)
def test_resolve_report_fields_english_stop_invalidation(final_decision):
    resolved = report_service.resolve_report_fields(
        result_data={
            "final_trade_decision": final_decision,
            "trader_investment_plan": "Stop-loss: 9.0.",
        },
        has_position=True,
    )

    assert resolved["stop_loss_price"] is None


def test_resolve_report_fields_later_conditional_stop_supersedes_pending_value():
    resolved = report_service.resolve_report_fields(
        result_data={
            "final_trade_decision": "初稿止损价：待定。最终建议：若跌破8元则止损。",
        },
        has_position=True,
    )

    assert resolved["stop_loss_price"] == 8.0


@pytest.mark.parametrize(
    "final_decision",
    (
        "当前不设置止损。",
        "止损暂不设置。",
        "最终决定不设止损。",
        "止损价不再采用。",
    ),
)
def test_resolve_report_fields_explicit_no_stop_wording(final_decision):
    resolved = report_service.resolve_report_fields(
        result_data={
            "final_trade_decision": final_decision,
            "trader_investment_plan": "止损价：9元。",
        },
        has_position=True,
    )

    assert resolved["stop_loss_price"] is None


def test_resolve_report_fields_same_clause_stop_replacement_wins():
    resolved = report_service.resolve_report_fields(
        result_data={
            "final_trade_decision": "止损价取消，改为9元。",
            "trader_investment_plan": "止损价：8元。",
        },
        has_position=True,
    )

    assert resolved["stop_loss_price"] == 9.0


@pytest.mark.parametrize(
    "final_decision",
    (
        "最终止损仍维持为2%。",
        "最终止损价暂无调整，仍为2%。",
        "最终止损维持在2个百分点。",
    ),
)
def test_resolve_report_fields_rejects_qualified_percentage_stop(final_decision):
    resolved = report_service.resolve_report_fields(
        result_data={"final_trade_decision": final_decision},
        has_position=True,
    )

    assert resolved["stop_loss_price"] is None


def test_resolve_report_fields_rejects_points_as_absolute_stop():
    resolved = report_service.resolve_report_fields(
        result_data={"final_trade_decision": "最终建议：观望。止损价：2个点。"},
        has_position=True,
    )

    assert resolved["stop_loss_price"] is None


def test_resolve_report_fields_uses_latest_qualified_stop_revision():
    resolved = report_service.resolve_report_fields(
        result_data={
            "final_trade_decision": "止损价仍为9元；复核后最终止损价调整为8元。",
        },
        has_position=True,
    )

    assert resolved["stop_loss_price"] == 8.0


def test_resolve_report_fields_later_valid_stop_overrides_stale_invalidation():
    resolved = report_service.resolve_report_fields(
        result_data={
            "final_trade_decision": (
                "初稿止损价：待定。最终交易建议：若站稳10元则买入；"
                "最终止损价：9元。"
            ),
        },
        has_position=False,
    )

    assert resolved["stop_loss_price"] == 9.0


def test_resolve_report_fields_qualified_stop_overrides_stale_invalidation():
    resolved = report_service.resolve_report_fields(
        result_data={
            "final_trade_decision": (
                "初步止损价：待定。最终止损价调整为9元。最终建议：持有。"
            ),
        },
        has_position=True,
    )

    assert resolved["stop_loss_price"] == 9.0


def test_resolve_report_fields_ignores_generated_quality_check_and_numbered_risk_list():
    resolved = report_service.resolve_report_fields(
        result_data={
            "final_trade_decision": (
                "最终审核意见：当前禁止建仓。\n"
                "出现以下任一情况，应立即放弃任何入场计划，并对已持仓部分执行止损：\n"
                "1. RISK-1恶化：后续财报显示盈利增速进一步下滑。\n"
                "2. 技术面破位：股价放量跌破492.65元且无法在3个交易日内收回。\n"
                "目标价：—\n"
                "止损价：—\n\n"
                "### 执行质检\n"
                "- 触发价：554.96\n"
                "- 止损价：1.0\n"
            ),
            "trader_investment_plan": "不建议入场。空头观点只有放量站稳554.96元才失效。",
            "investment_plan": "不建议当前价位入场。",
        }
    )

    assert resolved["target_price"] is None
    assert resolved["stop_loss_price"] is None


def test_model_authored_quality_heading_cannot_hide_later_stop_cancellation():
    resolved = report_service.resolve_report_fields(
        result_data={
            "final_trade_decision": (
                "初始止损价：9元。\n"
                "### 执行质检\n"
                "模型自检后最终决定：止损价：取消。最终建议：持有。"
            ),
        },
        has_position=True,
    )

    assert resolved["stop_loss_price"] is None


def test_trusted_diagnostics_offset_ignores_copied_historical_marker():
    model_body = "最终建议：持有。止损价：9元。"
    diagnostics = (
        "<!-- TA_SYSTEM_DIAGNOSTICS_START -->\n"
        "### 执行质检\n"
        "- 止损价：9元\n\n"
        "### C-005 历史报告\n"
        "旧报告正文\n"
        "<!-- TA_SYSTEM_DIAGNOSTICS_START -->\n"
        "### 执行质检\n"
        "旧报告曾写止损价：取消。\n\n"
        "### 系统执行结论\n"
        "- 系统动作：WAIT\n\n"
        "### 系统执行结论\n"
        "- 系统动作：HOLD"
    )
    final_decision = f"{model_body}\n\n{diagnostics}"
    resolved = report_service.resolve_report_fields(
        result_data={
            "final_trade_decision": final_decision,
            "metadata": {"system_diagnostics_offset": len(model_body) + 2},
        },
        has_position=True,
    )

    assert resolved["stop_loss_price"] == 9.0


def test_bearish_wait_suppresses_upstream_bullish_target_price():
    resolved = report_service.resolve_report_fields(
        result_data={
            "final_trade_decision": "最终结论：偏空，等待观察。目标价：186.00",
            "research_direction": "偏空",
            "execution_action": "WAIT",
            "action_label": "回避",
        },
        has_position=False,
    )

    assert resolved["execution_action"] == "WAIT"
    assert resolved["action_label"] == "回避"
    assert resolved["target_price"] is None


def test_bearish_wait_derived_from_legacy_text_suppresses_target_price():
    resolved = report_service.resolve_report_fields(
        result_data={
            "final_trade_decision": "最终裁决：偏空，建议回避。目标价：186.00",
        },
        has_position=False,
    )

    assert resolved["research_direction"] == "偏空"
    assert resolved["execution_action"] == "WAIT"
    assert resolved["action_label"] == "回避"
    assert resolved["target_price"] is None


def test_legacy_bearish_wait_hides_persisted_target_without_database_mutation():
    db = _make_session()
    try:
        report = _add_report(db, status="completed")
        report.research_direction = "偏空"
        report.execution_action = "WAIT"
        report.action_label = "回避"
        report.target_price = 186.0
        report.result_data = {
            "research_direction": "偏空",
            "execution_action": "WAIT",
            "action_label": "回避",
            "target_price": 186.0,
        }
        db.commit()
        report_id = report.id

        normalized = report_service.get_report(db, report_id)

        assert normalized.target_price is None
        assert normalized.result_data["target_price"] is None
        persisted_target = db.execute(
            text("SELECT target_price FROM reports WHERE id = :report_id"),
            {"report_id": report_id},
        ).scalar_one()
        assert persisted_target == 186.0
    finally:
        db.close()


def test_explicit_wait_overrides_stale_legacy_sell_when_hiding_target():
    report = type(
        "MigratedReport",
        (),
        {
            "research_direction": "偏空",
            "execution_action": "WAIT",
            "action_label": "回避",
            "decision": "SELL",
            "target_price": 145.0,
            "result_data": None,
        },
    )()

    normalized = report_service.normalize_report_action_label(report)

    assert normalized.target_price is None


@pytest.mark.parametrize(
    "final_decision",
    (
        "止损价：9元，现已撤销。",
        "最终决定：止损价9元已取消。",
        "最终止损为9元，但该止损已取消。",
    ),
)
def test_resolve_report_fields_clears_stop_cancelled_after_value(final_decision):
    resolved = report_service.resolve_report_fields(
        result_data={
            "final_trade_decision": final_decision,
            "trader_investment_plan": "止损价：8元。",
        },
        has_position=True,
    )

    assert resolved["stop_loss_price"] is None
