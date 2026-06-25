from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.database import Base
from api.services import report_service
from tradingagents.agents.utils.readiness_score import (
    build_data_blockers,
    summarize_data_blockers,
)


def _blocker_by_key(blockers):
    return {item["key"]: item for item in blockers}


def test_build_data_blockers_distinguishes_failed_from_normal_no_data():
    blockers = build_data_blockers(
        reports={},
        raw_evidence={
            "stock_data": "2026-06-24,open,high,low,close,volume\n" + "1" * 60,
            "fund_flow_individual": "主力资金获取失败：AKShare timeout",
            "lhb": "龙虎榜：未上榜，非异动日无数据",
            "news": "公告获取失败：接口错误",
            "ratings": {"status": "NORMAL_NO_DATA", "raw": "RATINGS_NORMAL_NO_DATA: 无分析师评级"},
            "buybacks": {"status": "FAILED", "raw": "BUYBACK_FAILED: 回购接口失败"},
            "research_report": "REPORT_NORMAL_NO_DATA: 无券商研报",
        },
    )
    by_key = _blocker_by_key(blockers)

    assert by_key["individual_fund_flow"]["status"] == "query_failed"
    assert by_key["lhb_status"]["status"] == "normal_no_data"
    assert "正常无数据" in by_key["lhb_status"]["reason"]
    assert by_key["announcements"]["status"] == "query_failed"
    assert by_key["ratings"]["status"] == "normal_no_data"
    assert by_key["buybacks"]["status"] == "query_failed"
    assert by_key["research_report"]["status"] == "normal_no_data"
    assert "ohlcv_5d" not in by_key


def test_data_blocker_summary_keeps_normal_no_data_separate():
    blockers = [
        {"status": "query_failed"},
        {"status": "field_missing"},
        {"status": "not_queried"},
        {"status": "normal_no_data"},
        {"status": "skipped"},
    ]

    summary = summarize_data_blockers(blockers)

    assert summary["level"] == "warning"
    assert summary["counts"]["query_failed"] == 1
    assert summary["counts"]["field_missing"] == 1
    assert summary["counts"]["normal_no_data"] == 1
    assert summary["counts"]["skipped"] == 1
    assert "强结论需降级" in summary["message"]


def test_build_data_blockers_preserves_structured_skipped_status():
    blockers = build_data_blockers(
        reports={},
        raw_evidence={
            "fund_flow_individual": {"status": "SKIPPED", "raw": ""},
            "news": {"status": "SKIPPED", "raw": ""},
        },
    )
    by_key = _blocker_by_key(blockers)

    assert by_key["individual_fund_flow"]["status"] == "skipped"
    assert by_key["announcements"]["status"] == "skipped"
    assert by_key["individual_fund_flow"]["status_label"] == "已跳过"


def test_create_report_attaches_data_blockers_to_result_data():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        report = report_service.create_report(
            db=db,
            symbol="603629.SH",
            trade_date="2026-06-24",
            decision="HOLD",
            result_data={
                "market_report": "行情报告包含基础数据",
                "smart_money_report": "主力资金报告：数据源返回异常，未给出逐日净流入金额",
                "news_report": "公告获取失败：接口不可用",
                "final_trade_decision": "结论：数据不足，等待触发。",
                "metadata": {
                    "raw_evidence": {
                        "fund_flow_individual": "主力资金获取失败：AKShare timeout",
                        "lhb": "龙虎榜：未上榜，非异动日无数据",
                        "news": "公告获取失败：接口不可用",
                        "ratings": {"status": "NORMAL_NO_DATA", "raw": "RATINGS_NORMAL_NO_DATA: 无评级"},
                    }
                },
            },
        )

        result_data = report.result_data
        assert isinstance(result_data.get("data_blockers"), list)
        by_key = _blocker_by_key(result_data["data_blockers"])
        assert by_key["individual_fund_flow"]["status"] == "query_failed"
        assert by_key["lhb_status"]["status"] == "normal_no_data"
        assert by_key["announcements"]["status"] == "query_failed"
        assert result_data["data_blocker_summary"]["counts"]["query_failed"] >= 2
        assert report.decision == "HOLD"
    finally:
        db.close()
