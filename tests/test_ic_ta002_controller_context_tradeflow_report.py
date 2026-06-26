# [IC-TA-002] controller_context_tradeflow_report
"""Tests for IC-TA-002: investment-controller context 接入昊天日报与报告数据缺口.

Covers the two new buckets + controller_hints added on top of IC-TA-001:
- ``mandate_daily_report``: H-015 昊天主题日报 digest (read-only, no network).
- ``recent_report_data_blockers``: DATA-021 query_failed / field_missing
  aggregation across the user's most recent TA reports.
- ``controller_hints``: soft routing hints (needs_ta / daily_report_only /
  suppress_push_data_insufficient).

Contract guarantees re-asserted for the new surface:
- Stable empty structure when no data.
- Every bucket carries ``source`` + ``as_of`` + allowed ``data_status``.
- READ-ONLY: no DB writes, runtime tier stays FAST_RADAR, no LLM.
- No synthesised strong action verb (立即买入 / 立即卖出 / 满仓 / 清仓).
- JSON-serialisable payload.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.tradeflow.candidate_engine import init_db

from api.services.investment_controller_context import (
    ALLOWED_DATA_STATUSES,
    DATA_STATUS_FAILED,
    DATA_STATUS_FRESH,
    DATA_STATUS_MISSING,
    assert_no_strong_action_verbs,
    get_investment_controller_context,
)
from api.runtime_tier import RuntimeTier


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────

TODAY = datetime.now().strftime("%Y-%m-%d")


@pytest.fixture
def tmp_tf_db(tmp_path):
    db_path = str(tmp_path / "test_ic_ta002_tradeflow.db")
    init_db(db_path)
    return db_path


@pytest.fixture
def tmp_sqla_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from api.database import Base, ImportedPortfolioPositionDB, ReportDB

    engine = create_engine("sqlite:///:memory:")
    ImportedPortfolioPositionDB.__table__.create(engine)
    ReportDB.__table__.create(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _make_report_with_blockers(
    symbol, trade_date, *, severe_blockers, action_label="数据不足观察"
):
    """Build a completed ReportDB row whose result_data ships data_blockers.

    ``severe_blockers`` is a list of (key, status) tuples restricted to the
    severe statuses (query_failed / field_missing) so the IC-TA-002 aggregator
    picks them up.
    """
    from api.database import ReportDB
    blockers = [
        {
            "key": key,
            "label": key,
            "status": status,
            "status_label": status,
            "severity": "high" if status == "query_failed" else "medium",
            "reason": f"{key} {status}",
            "impact": "demo impact",
        }
        for key, status in severe_blockers
    ]
    return ReportDB(
        id=uuid.uuid4().hex,
        user_id="ic_user",
        symbol=symbol,
        trade_date=trade_date,
        status="completed",
        decision="HOLD",
        direction="",
        research_direction="偏空",
        execution_action="WAIT",
        action_label=action_label,
        target_price=None,
        stop_loss_price=None,
        confidence="",
        risk_items="[]",
        key_metrics="{}",
        analyst_traces="{}",
        trader_investment_plan="",
        final_trade_decision="",
        result_data={
            "market_report": "demo",
            "data_blockers": blockers,
            "data_blocker_summary": {
                "level": "warning" if blockers else "ok",
                "message": "demo",
                "counts": {"query_failed": sum(1 for _, s in severe_blockers if s == "query_failed")},
                "total": len(blockers),
            },
        },
    )


def _make_legacy_report(symbol, trade_date, decision="HOLD"):
    """A pre-DATA-021 report whose result_data has no stored data_blockers.

    Used to verify the IC-TA-002 aggregator falls back to deriving blockers
    via ``attach_report_data_blockers``.
    """
    from api.database import ReportDB
    return ReportDB(
        id=uuid.uuid4().hex,
        user_id="ic_user",
        symbol=symbol,
        trade_date=trade_date,
        status="completed",
        decision=decision,
        direction="",
        research_direction="",
        execution_action="",
        action_label="持有",
        target_price=None,
        stop_loss_price=None,
        confidence="",
        risk_items="[]",
        key_metrics="{}",
        analyst_traces="{}",
        trader_investment_plan="",
        final_trade_decision="",
        result_data={
            "market_report": "行情报告包含基础数据",
            "smart_money_report": "主力资金报告：数据源返回异常",
            "news_report": "公告获取失败：接口不可用",
            "metadata": {
                "raw_evidence": {
                    "fund_flow_individual": "主力资金获取失败：AKShare timeout",
                    "lhb": "龙虎榜：未上榜，非异动日无数据",
                    "news": "公告获取失败：接口不可用",
                }
            },
        },
    )


# ──────────────────────────────────────────────────────────────────────────────
# Bucket presence + stable empty state
# ──────────────────────────────────────────────────────────────────────────────

class TestNewBucketsPresence:

    def test_new_buckets_always_present(self, tmp_sqla_session, tmp_tf_db):
        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        assert "mandate_daily_report" in result
        assert "recent_report_data_blockers" in result
        assert "controller_hints" in result

    def test_mandate_report_empty_state(self, tmp_sqla_session, tmp_tf_db, monkeypatch):
        # Force the mandate report to report no data so the empty-state path
        # is exercised in isolation (the repo may ship saved mandate reports).
        monkeypatch.setattr(
            "api.services.tradeflow_service.get_mandate_daily_report",
            lambda **kw: {"status": "no_data"},
        )
        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        mandate = result["mandate_daily_report"]
        assert mandate["source"] == "mandate_daily_report"
        assert mandate["as_of"]
        # No heatmap / saved report in a bare tradeflow DB → missing.
        assert mandate["data_status"] == DATA_STATUS_MISSING
        assert mandate["main_candidate_count"] == 0

    def test_report_blockers_empty_state(self, tmp_sqla_session, tmp_tf_db):
        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        blockers = result["recent_report_data_blockers"]
        assert blockers["source"] == "recent_report_data_blockers"
        assert blockers["as_of"]
        assert blockers["data_status"] in ALLOWED_DATA_STATUSES
        assert blockers["affected_symbols"] == []
        assert blockers["scanned_report_count"] == 0

    def test_controller_hints_empty_state(self, tmp_sqla_session, tmp_tf_db, monkeypatch):
        # Isolate from any saved mandate report shipped with the repo.
        monkeypatch.setattr(
            "api.services.tradeflow_service.get_mandate_daily_report",
            lambda **kw: {"status": "no_data"},
        )
        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        hints = result["controller_hints"]
        assert hints["source"] == "controller_hints"
        assert hints["as_of"]
        assert hints["needs_ta"] == []
        assert hints["daily_report_only"] == []
        assert hints["suppress_push_data_insufficient"] == []

    def test_new_buckets_carry_source_and_as_of(self, tmp_sqla_session, tmp_tf_db):
        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        for bucket_name in ("mandate_daily_report", "recent_report_data_blockers",
                            "controller_hints"):
            bucket = result[bucket_name]
            assert bucket.get("source"), f"{bucket_name} missing source"
            assert bucket.get("as_of"), f"{bucket_name} missing as_of"

    def test_new_buckets_data_status_in_allowed_set(self, tmp_sqla_session, tmp_tf_db):
        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        for bucket_name in ("mandate_daily_report", "recent_report_data_blockers",
                            "controller_hints"):
            status = result[bucket_name]["data_status"]
            assert status in ALLOWED_DATA_STATUSES, (
                f"{bucket_name} bogus data_status={status!r}"
            )


# ──────────────────────────────────────────────────────────────────────────────
# recent_report_data_blockers aggregation
# ──────────────────────────────────────────────────────────────────────────────

class TestRecentReportDataBlockers:

    def test_severe_blockers_aggregated_from_stored_data(
        self, tmp_sqla_session, tmp_tf_db, monkeypatch
    ):
        from api.services import investment_controller_context as ic_ctx
        monkeypatch.setattr(ic_ctx, "_fetch_live_quotes", lambda symbols: {})
        tmp_sqla_session.add(_make_report_with_blockers(
            "603629.SH", TODAY,
            severe_blockers=[
                ("individual_fund_flow", "query_failed"),
                ("announcements", "query_failed"),
                ("lhb_status", "normal_no_data"),  # informational, not severe
            ],
        ))
        tmp_sqla_session.commit()

        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        blockers = result["recent_report_data_blockers"]
        assert blockers["data_status"] == DATA_STATUS_FRESH
        assert blockers["scanned_report_count"] == 1
        assert blockers["affected_report_count"] == 1
        # Only the two query_failed entries count as severe.
        assert blockers["total_severe_blockers"] == 2
        assert blockers["summary_level"] == "warning"
        assert blockers["field_counts"]["individual_fund_flow"] == 1
        assert blockers["field_counts"]["announcements"] == 1
        affected = blockers["affected_symbols"][0]
        assert affected["symbol"] == "603629.SH"
        assert set(affected["fields"]) == {"individual_fund_flow", "announcements"}

    def test_field_missing_also_counts_as_severe(
        self, tmp_sqla_session, tmp_tf_db, monkeypatch
    ):
        from api.services import investment_controller_context as ic_ctx
        monkeypatch.setattr(ic_ctx, "_fetch_live_quotes", lambda symbols: {})
        tmp_sqla_session.add(_make_report_with_blockers(
            "600000.SH", TODAY,
            severe_blockers=[("volume_price", "field_missing")],
        ))
        tmp_sqla_session.commit()

        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        blockers = result["recent_report_data_blockers"]
        assert blockers["total_severe_blockers"] == 1
        assert blockers["field_counts"]["volume_price"] == 1

    def test_clean_reports_yield_no_affected_symbols(
        self, tmp_sqla_session, tmp_tf_db, monkeypatch
    ):
        from api.services import investment_controller_context as ic_ctx
        monkeypatch.setattr(ic_ctx, "_fetch_live_quotes", lambda symbols: {})
        # Report with only a normal_no_data blocker (not severe).
        tmp_sqla_session.add(_make_report_with_blockers(
            "600000.SH", TODAY,
            severe_blockers=[("lhb_status", "normal_no_data")],
        ))
        tmp_sqla_session.commit()

        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        blockers = result["recent_report_data_blockers"]
        # Scanned successfully but no severe gaps.
        assert blockers["scanned_report_count"] == 1
        assert blockers["affected_report_count"] == 0
        assert blockers["summary_level"] == "ok"

    def test_legacy_report_without_stored_blockers_is_derived(
        self, tmp_sqla_session, tmp_tf_db, monkeypatch
    ):
        """Reports created before DATA-021 have no stored data_blockers; the
        aggregator must derive them read-only via attach_report_data_blockers."""
        from api.services import investment_controller_context as ic_ctx
        monkeypatch.setattr(ic_ctx, "_fetch_live_quotes", lambda symbols: {})
        tmp_sqla_session.add(_make_legacy_report("603629.SH", TODAY))
        tmp_sqla_session.commit()

        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        blockers = result["recent_report_data_blockers"]
        # The legacy fixture has fund_flow + news query failures.
        assert blockers["affected_report_count"] == 1
        keys = set(blockers["affected_symbols"][0]["fields"])
        assert "individual_fund_flow" in keys
        assert "announcements" in keys

    def test_only_most_recent_reports_scanned(
        self, tmp_sqla_session, tmp_tf_db, monkeypatch
    ):
        from api.services import investment_controller_context as ic_ctx
        from api.services.investment_controller_context import _RECENT_REPORT_SCAN_LIMIT
        monkeypatch.setattr(ic_ctx, "_fetch_live_quotes", lambda symbols: {})

        # Insert more reports than the scan limit; all should be capped.
        for i in range(_RECENT_REPORT_SCAN_LIMIT + 3):
            tmp_sqla_session.add(_make_report_with_blockers(
                f"{600000 + i}.SH", TODAY,
                severe_blockers=[("individual_fund_flow", "query_failed")],
            ))
        tmp_sqla_session.commit()

        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        blockers = result["recent_report_data_blockers"]
        assert blockers["scanned_report_count"] == _RECENT_REPORT_SCAN_LIMIT


# ──────────────────────────────────────────────────────────────────────────────
# controller_hints routing
# ──────────────────────────────────────────────────────────────────────────────

class TestControllerHints:

    def test_ta_required_observation_appears_in_needs_ta(
        self, tmp_sqla_session, tmp_tf_db
    ):
        from api.services.tradeflow_service import (
            create_observation_item,
            mark_observation_item_status,
            get_observation_items,
        )
        create_observation_item(
            symbol="002353.SZ", name="杰瑞股份",
            entry_low=34.0, entry_high=35.5, trigger_price=35.0,
            invalid_price=33.0, horizon="mid", source="tradeflow",
            reason="政策利好", priority=3, tf_db_path=tmp_tf_db,
        )
        created = get_observation_items(tf_db_path=tmp_tf_db)
        item_id = created["items"][0]["id"]
        mark_observation_item_status(item_id=item_id, status="ta_required", tf_db_path=tmp_tf_db)

        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        needs_ta = result["controller_hints"]["needs_ta"]
        symbols = [h["symbol"] for h in needs_ta]
        assert "002353.SZ" in symbols
        hint = next(h for h in needs_ta if h["symbol"] == "002353.SZ")
        assert hint["suggested_next_step"] == "consider_light_or_full_ta"
        assert hint["as_of"]

    def test_watching_observation_goes_to_daily_report_only(
        self, tmp_sqla_session, tmp_tf_db
    ):
        from api.services.tradeflow_service import create_observation_item
        # Default status is "watching".
        create_observation_item(
            symbol="601689.SH", name="拓普集团",
            entry_low=29.0, entry_high=30.5, reason="远观", tf_db_path=tmp_tf_db,
        )
        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        hints = result["controller_hints"]
        daily_symbols = [h["symbol"] for h in hints["daily_report_only"]]
        assert "601689.SH" in daily_symbols
        # A purely watching item must not also be flagged needs_ta.
        assert "601689.SH" not in [h["symbol"] for h in hints["needs_ta"]]

    def test_severe_blockers_surface_in_suppress_push(
        self, tmp_sqla_session, tmp_tf_db, monkeypatch
    ):
        from api.services import investment_controller_context as ic_ctx
        monkeypatch.setattr(ic_ctx, "_fetch_live_quotes", lambda symbols: {})
        tmp_sqla_session.add(_make_report_with_blockers(
            "603629.SH", TODAY,
            severe_blockers=[("individual_fund_flow", "query_failed")],
        ))
        tmp_sqla_session.commit()

        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        suppress = result["controller_hints"]["suppress_push_data_insufficient"]
        symbols = [h["symbol"] for h in suppress]
        assert "603629.SH" in symbols
        hint = next(h for h in suppress if h["symbol"] == "603629.SH")
        assert hint["suggested_next_step"] == "suppress_push_data_insufficient"
        assert "individual_fund_flow" in hint["fields"]
        assert hint["as_of"]

    def test_hint_entries_carry_source_and_as_of(
        self, tmp_sqla_session, tmp_tf_db
    ):
        from api.services.tradeflow_service import create_observation_item
        create_observation_item(
            symbol="601689.SH", name="拓普集团", reason="远观", tf_db_path=tmp_tf_db,
        )
        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        for lane in ("needs_ta", "daily_report_only", "suppress_push_data_insufficient"):
            for item in result["controller_hints"][lane]:
                assert item["source"]
                assert item["as_of"]


# ──────────────────────────────────────────────────────────────────────────────
# READ-ONLY + safety contract (re-asserted for the new surface)
# ──────────────────────────────────────────────────────────────────────────────

class TestReadOnlyAndSafety:

    def test_runtime_tier_unchanged_fast_radar(self, tmp_sqla_session, tmp_tf_db):
        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        meta = result["runtime_tier_meta"]
        assert meta["runtime_tier"] == RuntimeTier.FAST_RADAR.value
        assert meta["llm_allowed"] is False

    def test_aggregation_does_not_write_db(
        self, tmp_sqla_session, tmp_tf_db, monkeypatch
    ):
        """Re-running the context must not mutate report rows or persist new
        data_blocker columns — derivation is in-memory only."""
        from api.services import investment_controller_context as ic_ctx
        monkeypatch.setattr(ic_ctx, "_fetch_live_quotes", lambda symbols: {})
        report = _make_report_with_blockers(
            "603629.SH", TODAY,
            severe_blockers=[("individual_fund_flow", "query_failed")],
        )
        original_summary = json.dumps(
            report.result_data["data_blocker_summary"], sort_keys=True
        )
        tmp_sqla_session.add(report)
        tmp_sqla_session.commit()
        report_id = report.id

        # Run the context twice.
        for _ in range(2):
            get_investment_controller_context(
                tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
            )

        from api.database import ReportDB
        refreshed = tmp_sqla_session.query(ReportDB).filter_by(id=report_id).first()
        after_summary = json.dumps(
            refreshed.result_data["data_blocker_summary"], sort_keys=True
        )
        assert after_summary == original_summary

    @pytest.mark.parametrize("strong", ["立即买入", "立即卖出", "满仓", "清仓", "全仓"])
    def test_no_strong_action_verb_in_synthesised_fields(
        self, tmp_sqla_session, tmp_tf_db, monkeypatch, strong
    ):
        from api.services import investment_controller_context as ic_ctx
        monkeypatch.setattr(ic_ctx, "_fetch_live_quotes", lambda symbols: {})
        tmp_sqla_session.add(_make_report_with_blockers(
            "603629.SH", TODAY,
            severe_blockers=[("individual_fund_flow", "query_failed")],
            action_label="数据不足观察",
        ))
        tmp_sqla_session.add(_make_legacy_report("600000.SH", TODAY))
        tmp_sqla_session.commit()

        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        # Controller-synthesised text only (notes + hints reasons).
        synthesised = json.dumps({
            "notes": result["notes"],
            "generated_by": result["generated_by"],
            "controller_hints": result["controller_hints"],
            "mandate_daily_report": {
                k: v for k, v in result["mandate_daily_report"].items()
                if k not in ("evidence_gaps",)
            },
        }, ensure_ascii=False)
        assert strong not in synthesised
        assert_no_strong_action_verbs(result)

    def test_no_sensitive_field_keys(self, tmp_sqla_session, tmp_tf_db):
        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        forbidden = ("api_key", "apikey", "token", "secret", "password", "cookie")

        def _walk(obj, path=""):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    low = str(k).lower()
                    for bad in forbidden:
                        assert bad not in low, f"sensitive {bad!r} in {path}.{k}"
                    _walk(v, f"{path}.{k}")
            elif isinstance(obj, list):
                for i, v in enumerate(obj):
                    _walk(v, f"{path}[{i}]")

        _walk(result)

    def test_payload_is_json_serializable(
        self, tmp_sqla_session, tmp_tf_db, monkeypatch
    ):
        from api.services import investment_controller_context as ic_ctx
        monkeypatch.setattr(ic_ctx, "_fetch_live_quotes", lambda symbols: {})
        tmp_sqla_session.add(_make_report_with_blockers(
            "603629.SH", TODAY,
            severe_blockers=[("individual_fund_flow", "query_failed")],
        ))
        tmp_sqla_session.commit()
        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        json.dumps(result)


# ──────────────────────────────────────────────────────────────────────────────
# Graceful degradation
# ──────────────────────────────────────────────────────────────────────────────

class TestGracefulDegradation:

    def test_tradeflow_db_missing_does_not_raise(self, tmp_sqla_session, tmp_path):
        bogus = str(tmp_path / "does_not_exist.db")
        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=bogus
        )
        # New buckets degrade to allowed statuses, never raise.
        assert result["mandate_daily_report"]["data_status"] in ALLOWED_DATA_STATUSES
        assert result["recent_report_data_blockers"]["data_status"] in ALLOWED_DATA_STATUSES
        assert result["controller_hints"]["data_status"] in ALLOWED_DATA_STATUSES

    def test_mandate_report_failure_degrades_to_failed(self, tmp_sqla_session, tmp_tf_db, monkeypatch):
        """If the mandate report service blows up, the bucket must degrade."""
        from api.services import investment_controller_context as ic_ctx

        def _boom(*args, **kwargs):
            raise RuntimeError("heatmap exploded")

        monkeypatch.setattr(
            "api.services.tradeflow_service.get_mandate_daily_report", _boom
        )
        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        mandate = result["mandate_daily_report"]
        assert mandate["data_status"] == DATA_STATUS_FAILED
        assert mandate["main_candidates"] == []
        assert mandate["main_candidate_count"] == 0
