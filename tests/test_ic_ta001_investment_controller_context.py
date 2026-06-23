# [IC-TA-001] investment_controller_context
"""Tests for IC-TA-001: investment-controller 只读上下文包与数据契约.

Covers:
- Stable empty structure when no data (holdings/observation/candidates/reports).
- Aggregation across all six buckets with seeded fixtures.
- Every bucket + item carries ``source`` and ``as_of``.
- ``data_status`` values limited to fresh/stale/missing/failed/skipped.
- READ-ONLY contract: runtime tier = FAST_RADAR, no LLM.
- No synthesised strong action verb (立即买入 / 立即卖出 / 满仓 / 清仓).
- No sensitive fields (api_key / token / secret / password) leaked.
- Holdings isolation: observation warehouse never pollutes real holdings.
- Bucket failure degrades gracefully (data_status=failed, empty items).
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
    CONTEXT_SCHEMA_VERSION,
    CONTEXT_SOURCE,
    DATA_STATUS_FAILED,
    DATA_STATUS_FRESH,
    DATA_STATUS_MISSING,
    DATA_STATUS_SKIPPED,
    DATA_STATUS_STALE,
    _empty_bucket,
    _union_symbols,
    assert_no_strong_action_verbs,
    get_investment_controller_context,
)
from api.runtime_tier import RuntimeTier, tradeflow_endpoint_tier


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────

TODAY = datetime.now().strftime("%Y-%m-%d")


@pytest.fixture
def tmp_tf_db(tmp_path):
    """Empty tradeflow SQLite DB with all tables created."""
    db_path = str(tmp_path / "test_ic_ta001_tradeflow.db")
    init_db(db_path)
    return db_path


@pytest.fixture
def tmp_sqla_session():
    """In-memory SQLAlchemy session with holdings + reports tables."""
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


def _make_position(symbol, name, current_position, average_cost, market_value):
    """Helper to construct an ImportedPortfolioPositionDB row."""
    from api.database import ImportedPortfolioPositionDB
    return ImportedPortfolioPositionDB(
        id=uuid.uuid4().hex,
        user_id="ic_user",
        source="test",
        symbol=symbol,
        security_name=name,
        current_position=current_position,
        available_position=current_position,
        average_cost=average_cost,
        market_value=market_value,
        current_position_pct=0.0,
        trade_points_json="[]",
        trade_points_count=0,
        latest_trade_at=None,
        latest_trade_action=None,
        last_imported_at=datetime.now(),
    )


def _make_report(symbol, decision, action_label, trade_date):
    """Helper to construct a completed ReportDB row."""
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
        action_label=action_label,
        target_price=None,
        stop_loss_price=None,
        confidence="",
        risk_items="[]",
        key_metrics="{}",
        analyst_traces="{}",
        trader_investment_plan="",
        final_trade_decision="",
    )


# ──────────────────────────────────────────────────────────────────────────────
# Pure unit tests on helpers
# ──────────────────────────────────────────────────────────────────────────────

class TestEmptyBucketHelper:

    def test_returns_stable_shape(self):
        bucket = _empty_bucket("foo", "2026-06-23 10:00:00", DATA_STATUS_MISSING)
        assert set(bucket.keys()) == {"source", "as_of", "data_status", "count", "items"}
        assert bucket["source"] == "foo"
        assert bucket["count"] == 0
        assert bucket["items"] == []

    def test_all_allowed_statuses(self):
        for status in ALLOWED_DATA_STATUSES:
            bucket = _empty_bucket("s", "as_of", status)
            assert bucket["data_status"] == status

    def test_unknown_status_falls_back_to_missing(self):
        bucket = _empty_bucket("s", "as_of", "bogus")
        assert bucket["data_status"] == DATA_STATUS_MISSING


class TestUnionSymbols:

    def test_empty_inputs(self):
        assert _union_symbols([], []) == []

    def test_dedupes_preserving_order(self):
        a = [{"symbol": "A.SH"}, {"symbol": "B.SH"}]
        b = [{"symbol": "B.SH"}, {"symbol": "C.SH"}]
        assert _union_symbols(a, b) == ["A.SH", "B.SH", "C.SH"]

    def test_ignores_missing_symbol(self):
        a = [{"symbol": "A.SH"}, {"name": "no_symbol"}]
        assert _union_symbols(a) == ["A.SH"]


# ──────────────────────────────────────────────────────────────────────────────
# Stable empty structure
# ──────────────────────────────────────────────────────────────────────────────

class TestEmptyStateContract:
    """No holdings, no observation items, no candidates, no reports."""

    def test_returns_all_six_buckets(self, tmp_sqla_session, tmp_tf_db):
        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        required_buckets = {
            "holdings", "observation_warehouse", "tradeflow_candidates",
            "latest_ta_reports", "data_health", "pending_ta_required",
        }
        assert required_buckets.issubset(result.keys())

    def test_top_level_metadata(self, tmp_sqla_session, tmp_tf_db):
        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        assert result["schema_version"] == CONTEXT_SCHEMA_VERSION
        assert result["generated_by"] == CONTEXT_SOURCE
        assert result["read_only"] is True
        assert "as_of" in result
        assert "previous_trade_date" in result
        assert "is_trading_day" in result
        assert isinstance(result["notes"], list)

    def test_empty_holdings_bucket(self, tmp_sqla_session, tmp_tf_db):
        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        holdings = result["holdings"]
        assert holdings["count"] == 0
        assert holdings["items"] == []
        # No positions → nothing to quote → missing data.
        assert holdings["data_status"] == DATA_STATUS_MISSING

    def test_empty_observation_bucket(self, tmp_sqla_session, tmp_tf_db):
        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        obs = result["observation_warehouse"]
        assert obs["count"] == 0
        assert obs["items"] == []
        assert obs["data_status"] == DATA_STATUS_MISSING

    def test_empty_candidates_bucket(self, tmp_sqla_session, tmp_tf_db):
        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        cand = result["tradeflow_candidates"]
        assert cand["count"] == 0
        assert cand["items"] == []
        assert cand["data_status"] == DATA_STATUS_MISSING

    def test_empty_reports_bucket(self, tmp_sqla_session, tmp_tf_db):
        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        reports = result["latest_ta_reports"]
        assert reports["count"] == 0
        assert reports["data_status"] == DATA_STATUS_MISSING

    def test_empty_pending_ta_bucket(self, tmp_sqla_session, tmp_tf_db):
        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        pending = result["pending_ta_required"]
        assert pending["count"] == 0


# ──────────────────────────────────────────────────────────────────────────────
# Source / as_of contract
# ──────────────────────────────────────────────────────────────────────────────

class TestSourceAndAsOfContract:
    """Every bucket and every item must carry ``source`` and ``as_of``."""

    def test_bucket_level_source_and_as_of(self, tmp_sqla_session, tmp_tf_db):
        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        for bucket_name in (
            "holdings", "observation_warehouse", "tradeflow_candidates",
            "latest_ta_reports", "data_health", "pending_ta_required",
        ):
            bucket = result[bucket_name]
            assert bucket.get("source"), f"{bucket_name} missing source"
            assert bucket.get("as_of"), f"{bucket_name} missing as_of"

    def test_holdings_items_carry_source_and_as_of(
        self, tmp_sqla_session, tmp_tf_db, monkeypatch
    ):
        tmp_sqla_session.add(_make_position("600000.SH", "浦发银行", 100, 10.0, 1000.0))
        tmp_sqla_session.commit()
        # Stub live quotes so we don't hit the network.
        from api.services import investment_controller_context as ic_ctx
        monkeypatch.setattr(
            ic_ctx, "_fetch_live_quotes",
            lambda symbols: {"600000.SH": {"price": 10.5, "change_pct": 0.5, "quote_time": "10:00", "source": "stub"}}
        )
        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        for item in result["holdings"]["items"]:
            assert item["source"] == "imported_portfolio"
            assert item["as_of"]


# ──────────────────────────────────────────────────────────────────────────────
# data_status contract
# ──────────────────────────────────────────────────────────────────────────────

class TestDataStatusContract:

    def test_all_statuses_in_allowed_set(self, tmp_sqla_session, tmp_tf_db):
        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        for bucket_name in (
            "holdings", "observation_warehouse", "tradeflow_candidates",
            "latest_ta_reports", "data_health", "pending_ta_required",
        ):
            status = result[bucket_name]["data_status"]
            assert status in ALLOWED_DATA_STATUSES, (
                f"{bucket_name} has bogus data_status={status!r}"
            )

    def test_holdings_without_quotes_on_trading_day_is_stale_or_skipped(
        self, tmp_sqla_session, tmp_tf_db, monkeypatch
    ):
        tmp_sqla_session.add(_make_position("600000.SH", "浦发银行", 100, 10.0, 1000.0))
        tmp_sqla_session.commit()
        from api.services import investment_controller_context as ic_ctx
        # Force trading day + empty quotes.
        monkeypatch.setattr(ic_ctx, "_fetch_live_quotes", lambda symbols: {})
        monkeypatch.setattr(ic_ctx, "is_cn_trading_day", lambda d: True)
        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        assert result["holdings"]["data_status"] == DATA_STATUS_STALE

    def test_holdings_without_quotes_on_non_trading_day_is_skipped(
        self, tmp_sqla_session, tmp_tf_db, monkeypatch
    ):
        tmp_sqla_session.add(_make_position("600000.SH", "浦发银行", 100, 10.0, 1000.0))
        tmp_sqla_session.commit()
        from api.services import investment_controller_context as ic_ctx
        monkeypatch.setattr(ic_ctx, "_fetch_live_quotes", lambda symbols: {})
        monkeypatch.setattr(ic_ctx, "is_cn_trading_day", lambda d: False)
        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        assert result["holdings"]["data_status"] == DATA_STATUS_SKIPPED

    def test_holdings_with_quotes_is_fresh(
        self, tmp_sqla_session, tmp_tf_db, monkeypatch
    ):
        tmp_sqla_session.add(_make_position("600000.SH", "浦发银行", 100, 10.0, 1000.0))
        tmp_sqla_session.commit()
        from api.services import investment_controller_context as ic_ctx
        monkeypatch.setattr(
            ic_ctx, "_fetch_live_quotes",
            lambda symbols: {"600000.SH": {"price": 10.5}}
        )
        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        assert result["holdings"]["data_status"] == DATA_STATUS_FRESH
        assert result["holdings"]["items"][0]["live_price"] == 10.5


# ──────────────────────────────────────────────────────────────────────────────
# Aggregation across buckets
# ──────────────────────────────────────────────────────────────────────────────

class TestAggregation:

    def test_observation_items_surface_in_context(
        self, tmp_sqla_session, tmp_tf_db
    ):
        from api.services.tradeflow_service import create_observation_item
        create_observation_item(
            symbol="601689.SH", name="拓普集团",
            entry_low=29.0, entry_high=30.5, trigger_price=30.0,
            invalid_price=28.0, horizon="short", source="manual",
            reason="接近买点", priority=5,
            tf_db_path=tmp_tf_db,
        )
        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        obs = result["observation_warehouse"]
        assert obs["count"] == 1
        assert obs["items"][0]["symbol"] == "601689.SH"
        assert obs["data_status"] == DATA_STATUS_FRESH
        # Boundary prices preserved (TRACK-001 contract).
        assert obs["items"][0]["entry_low"] == 29.0

    def test_ta_reports_attached_to_holdings(
        self, tmp_sqla_session, tmp_tf_db, monkeypatch
    ):
        tmp_sqla_session.add(_make_position("600000.SH", "浦发银行", 100, 10.0, 1000.0))
        tmp_sqla_session.add(_make_report("600000.SH", "HOLD", "持有", TODAY))
        tmp_sqla_session.commit()
        from api.services import investment_controller_context as ic_ctx
        monkeypatch.setattr(ic_ctx, "_fetch_live_quotes", lambda symbols: {})
        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        # latest_ta_reports bucket surfaces the structured-fact summary.
        reports = result["latest_ta_reports"]
        assert reports["count"] == 1
        assert reports["items"][0]["symbol"] == "600000.SH"
        assert reports["items"][0]["decision"] == "HOLD"
        # Holdings item also carries the embedded report.
        assert result["holdings"]["items"][0]["latest_report"] is not None

    def test_pending_ta_required_includes_observation_items(
        self, tmp_sqla_session, tmp_tf_db
    ):
        from api.services.tradeflow_service import (
            create_observation_item,
            mark_observation_item_status,
        )
        create_observation_item(
            symbol="002353.SZ", name="杰瑞股份",
            entry_low=34.0, entry_high=35.5, trigger_price=35.0,
            invalid_price=33.0, horizon="mid", source="tradeflow",
            reason="政策利好", priority=3,
            tf_db_path=tmp_tf_db,
        )
        # Mark it ta_required.
        from api.services.tradeflow_service import get_observation_items
        created = get_observation_items(tf_db_path=tmp_tf_db)
        item_id = created["items"][0]["id"]
        mark_observation_item_status(item_id=item_id, status="ta_required", tf_db_path=tmp_tf_db)

        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        pending = result["pending_ta_required"]
        syms = [p["symbol"] for p in pending["items"]]
        assert "002353.SZ" in syms
        assert pending["data_status"] == DATA_STATUS_FRESH

    def test_runtime_tier_is_fast_radar(self, tmp_sqla_session, tmp_tf_db):
        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        meta = result["runtime_tier_meta"]
        assert meta["runtime_tier"] == RuntimeTier.FAST_RADAR.value
        assert meta["llm_allowed"] is False
        assert meta["requires_confirmation"] is False

    def test_endpoint_registered_as_fast(self):
        tier = tradeflow_endpoint_tier("investment_controller_context")
        assert tier == RuntimeTier.FAST_RADAR


# ──────────────────────────────────────────────────────────────────────────────
# Graceful degradation
# ──────────────────────────────────────────────────────────────────────────────

class TestGracefulDegradation:

    def test_tradeflow_db_missing_does_not_raise(self, tmp_sqla_session, tmp_path):
        # Point at a non-existent DB path.
        bogus = str(tmp_path / "does_not_exist.db")
        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=bogus
        )
        # Buckets that depend on tradeflow DB degrade to missing/failed, never raise.
        assert result["observation_warehouse"]["data_status"] in ALLOWED_DATA_STATUSES
        assert result["tradeflow_candidates"]["data_status"] in ALLOWED_DATA_STATUSES
        assert result["data_health"]["data_status"] in ALLOWED_DATA_STATUSES

    def test_failed_quotes_do_not_break_holdings(
        self, tmp_sqla_session, tmp_tf_db, monkeypatch
    ):
        tmp_sqla_session.add(_make_position("600000.SH", "浦发银行", 100, 10.0, 1000.0))
        tmp_sqla_session.commit()

        def _explode(_symbols):
            raise RuntimeError("network exploded")

        from api.services import investment_controller_context as ic_ctx
        monkeypatch.setattr(ic_ctx, "_fetch_live_quotes", _explode)
        monkeypatch.setattr(ic_ctx, "is_cn_trading_day", lambda d: True)

        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        holdings = result["holdings"]
        # Holdings themselves still return; quotes are just absent.
        assert holdings["count"] == 1
        assert holdings["items"][0]["live_price"] is None
        # Failed quotes on trading day → stale.
        assert holdings["data_status"] == DATA_STATUS_STALE

    def test_serializable_to_json(self, tmp_sqla_session, tmp_tf_db):
        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        # Stable structure must JSON-serialise for the controller transport.
        json.dumps(result)


# ──────────────────────────────────────────────────────────────────────────────
# READ-ONLY + safety: no strong action, no sensitive fields
# ──────────────────────────────────────────────────────────────────────────────

class TestReadOnlySafetyContract:
    """The controller context surfaces structured facts only — it must never
    synthesise a strong buy/sell recommendation or leak credentials."""

    @pytest.mark.parametrize("strong", ["立即买入", "立即卖出", "满仓", "清仓", "全仓"])
    def test_no_strong_action_verb_synthesised(
        self, tmp_sqla_session, tmp_tf_db, monkeypatch, strong
    ):
        from api.services import investment_controller_context as ic_ctx
        monkeypatch.setattr(ic_ctx, "_fetch_live_quotes", lambda symbols: {})
        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        # The controller-generated fields (notes, generated_by) must stay clean.
        # TA report.decision is structured fact and may carry HOLD etc., but
        # the controller itself adds no new strong verb.
        synthesised_text = json.dumps({
            "notes": result["notes"],
            "generated_by": result["generated_by"],
            "data_status_values": [b["data_status"] for b in [
                result["holdings"], result["observation_warehouse"],
                result["tradeflow_candidates"], result["latest_ta_reports"],
                result["data_health"], result["pending_ta_required"],
            ]],
        }, ensure_ascii=False)
        assert strong not in synthesised_text
        # Helper guard also passes.
        assert_no_strong_action_verbs(result)

    def test_no_sensitive_field_keys(self, tmp_sqla_session, tmp_tf_db, monkeypatch):
        from api.services import investment_controller_context as ic_ctx
        monkeypatch.setattr(ic_ctx, "_fetch_live_quotes", lambda symbols: {})
        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        # Recursively walk the payload, check no key looks like a credential.
        forbidden_key_fragments = ("api_key", "apikey", "token", "secret", "password", "apikey", "cookie")

        def _walk(obj, path=""):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    low = str(k).lower()
                    for bad in forbidden_key_fragments:
                        assert bad not in low, f"sensitive key fragment {bad!r} in {path}.{k}"
                    _walk(v, f"{path}.{k}")
            elif isinstance(obj, list):
                for i, v in enumerate(obj):
                    _walk(v, f"{path}[{i}]")

        _walk(result)

    def test_no_credential_string_in_values(self, tmp_sqla_session, tmp_tf_db, monkeypatch):
        from api.services import investment_controller_context as ic_ctx
        monkeypatch.setattr(ic_ctx, "_fetch_live_quotes", lambda symbols: {})
        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        # Source labels should be stable identifiers, not contain raw keys.
        blob = json.dumps(result, ensure_ascii=False)
        for needle in ("sk-", "Bearer ", "AKID", "-----BEGIN"):
            assert needle not in blob, f"credential marker {needle!r} leaked"


# ──────────────────────────────────────────────────────────────────────────────
# Holdings isolation (TRACK-001 inheritance)
# ──────────────────────────────────────────────────────────────────────────────

class TestHoldingsIsolation:
    """Observation warehouse entries must never pollute the holdings bucket."""

    def test_observation_items_not_in_holdings(self, tmp_sqla_session, tmp_tf_db):
        from api.services.tradeflow_service import create_observation_item
        create_observation_item(
            symbol="601689.SH", name="拓普集团",
            entry_low=29.0, entry_high=30.5,
            tf_db_path=tmp_tf_db,
        )
        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        # Holdings bucket is independent of observation warehouse.
        holding_symbols = {h["symbol"] for h in result["holdings"]["items"]}
        assert "601689.SH" not in holding_symbols
        # Observation bucket carries it.
        obs_symbols = {o["symbol"] for o in result["observation_warehouse"]["items"]}
        assert "601689.SH" in obs_symbols
