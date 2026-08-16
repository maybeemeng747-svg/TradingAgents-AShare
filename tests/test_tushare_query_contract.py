"""TA-TUSHARE-2000-001B: unified eight-state Tushare query contract tests."""

from __future__ import annotations

import json
import time

import pandas as pd
import pytest

from tradingagents.dataflows.providers.cn_tushare_provider import CnTushareProvider
from tradingagents.dataflows.tushare_query_contract import (
    ALL_QUERY_STATES,
    CONTRACT_SCHEMA_VERSION,
    QUERY_STATES,
    STATE_FIELD_MISSING,
    STATE_HAS_DATA,
    STATE_NORMAL_NO_DATA,
    STATE_NOT_QUERIED,
    STATE_PERMISSION_DENIED,
    STATE_QUERY_FAILED,
    STATE_RATE_LIMITED,
    STATE_STALE,
    TushareQueryResult,
    classify_frame,
    error_result,
    execute_structured_query,
    frame_result,
    not_queried_result,
    stale_result,
)

TOKEN = "T" + "a1b2c3d4e5f6" * 4

REQUIRED_AUDIT_KEYS = (
    "contract_schema_version",
    "endpoint",
    "params",
    "state",
    "queried_at",
    "row_count",
    "data_period",
    "response_sha256",
    "response_sha256_scope",
    "cache",
    "error",
    "error_type",
    "missing_fields",
)


def _income_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ts_code": "603629.SH",
                "end_date": "20260630",
                "ann_date": "20260826",
                "revenue": 100.5,
            },
            {
                "ts_code": "603629.SH",
                "end_date": "20250331",
                "ann_date": "20250428",
                "revenue": 80.0,
            },
        ]
    )


class TestContractStates:
    def test_exactly_eight_states_in_contract_order(self):
        assert QUERY_STATES == (
            "HAS_DATA",
            "NORMAL_NO_DATA",
            "QUERY_FAILED",
            "PERMISSION_DENIED",
            "RATE_LIMITED",
            "FIELD_MISSING",
            "NOT_QUERIED",
            "STALE",
        )
        assert len(ALL_QUERY_STATES) == 8

    def test_has_data_result_carries_full_audit_payload(self):
        result = frame_result("income", {"ts_code": "603629.SH"}, _income_frame())
        assert result.state == STATE_HAS_DATA
        assert result.row_count == 2
        assert result.data_period == {"field": "ann_date", "min": "20250428", "max": "20260826"}
        assert result.response_sha256_scope == "frame"
        assert result.error is None
        record = result.to_dict()
        for key in REQUIRED_AUDIT_KEYS:
            assert key in record
        json.dumps(record)

    def test_business_zero_stays_data_and_is_never_dropped(self):
        frame = pd.DataFrame([{"ts_code": "603629.SH", "end_date": "20260630", "revenue": 0}])
        result = frame_result("income", {"ts_code": "603629.SH"}, frame)
        assert result.state == STATE_HAS_DATA
        assert result.row_count == 1
        payload = result.to_dict()
        assert payload["row_count"] == 1
        # The zero value survives in the hashed canonical payload.
        from tradingagents.dataflows.tushare_capability import (
            canonical_frame_payload,
            payload_sha256,
        )

        assert payload["response_sha256"] == payload_sha256(canonical_frame_payload(frame))
        assert '"revenue":0' in canonical_frame_payload(frame)

    def test_normal_no_data_is_empty_frame_without_error(self):
        result = frame_result("forecast", {"ts_code": "603629.SH"}, pd.DataFrame())
        assert result.state == STATE_NORMAL_NO_DATA
        assert result.row_count == 0
        assert result.data_period is None
        assert result.error is None

    def test_field_missing_lists_missing_columns(self):
        frame = pd.DataFrame([{"ts_code": "603629.SH", "trade_date": "20260810", "net_mf_amount": 1}])
        state, missing = classify_frame(frame, ("buy_lg_amount", "sell_lg_amount", "buy_elg_amount"))
        assert state == STATE_FIELD_MISSING
        assert missing == ("buy_lg_amount", "sell_lg_amount", "buy_elg_amount")
        result = frame_result("moneyflow", {}, frame, required_fields=("buy_lg_amount",))
        assert result.state == STATE_FIELD_MISSING
        assert result.row_count == 1  # real rows as evidence, never 0
        assert result.to_dict()["missing_fields"] == ["buy_lg_amount"]

    def test_empty_frame_with_required_fields_is_normal_no_data_not_field_missing(self):
        state, missing = classify_frame(pd.DataFrame(), ("buy_lg_amount",))
        assert state == STATE_NORMAL_NO_DATA
        assert missing == ()

    def test_frame_with_all_required_fields_is_has_data(self):
        frame = pd.DataFrame([{"trade_date": "20260810", "buy_lg_amount": 0}])
        state, missing = classify_frame(frame, ("buy_lg_amount",))
        assert state == STATE_HAS_DATA
        assert missing == ()

    @pytest.mark.parametrize(
        "message,expected",
        [
            ("抱歉，您没有访问该接口的权限", STATE_PERMISSION_DENIED),
            ("抱歉，您积分不足", STATE_PERMISSION_DENIED),
            ("抱歉，您每分钟最多访问该接口5次", STATE_RATE_LIMITED),
            ("too many requests", STATE_RATE_LIMITED),
            ("remote end closed connection without response", STATE_QUERY_FAILED),
        ],
    )
    def test_error_states_are_distinguishable(self, message, expected):
        result = error_result("income", {}, RuntimeError(message))
        assert result.state == expected
        assert result.row_count is None
        assert result.data_period is None
        assert result.response_sha256_scope == "sanitized_error"
        assert result.error_type == "RuntimeError"

    def test_rate_limit_takes_precedence_over_permission_wording(self):
        result = error_result(
            "income", {}, RuntimeError("访问频率过高且没有权限访问该接口")
        )
        assert result.state == STATE_RATE_LIMITED

    def test_not_queried_result_never_carries_numeric_counts(self):
        result = not_queried_result("top_list", {"ts_code": "603629.SH"}, "force=False")
        assert result.state == STATE_NOT_QUERIED
        assert result.row_count is None
        assert result.data_period is None
        record = result.to_dict()
        assert record["cache"]["upstream_called"] is False

    def test_stale_result_keeps_stale_frame_evidence_and_never_mimics_fresh(self):
        stale_frame = _income_frame()
        result = stale_result(
            "income",
            {"ts_code": "603629.SH"},
            stale_frame,
            age_seconds=9999.0,
            ttl_seconds=300.0,
            error="upstream query failed",
        )
        assert result.state == STATE_STALE
        assert result.row_count == 2
        assert result.response_sha256_scope == "stale_frame"
        assert result.to_dict()["cache"]["expired"] is True
        assert result.to_dict()["cache"]["stale_served"] is False

    def test_fail_closed_validation_rejects_zero_masquerading_failures(self):
        with pytest.raises(ValueError, match="must not carry numeric"):
            TushareQueryResult(
                endpoint="income",
                params={},
                state=STATE_PERMISSION_DENIED,
                queried_at="2026-08-16T00:00:00+08:00",
                row_count=0,
                data_period=None,
                response_sha256=None,
                response_sha256_scope=None,
            )
        with pytest.raises(ValueError, match="requires missing_fields"):
            TushareQueryResult(
                endpoint="moneyflow",
                params={},
                state=STATE_FIELD_MISSING,
                queried_at="2026-08-16T00:00:00+08:00",
                row_count=1,
                data_period=None,
                response_sha256="x",
                response_sha256_scope="frame",
            )
        with pytest.raises(ValueError, match="unknown Tushare query state"):
            TushareQueryResult(
                endpoint="income",
                params={},
                state="OK",
                queried_at="2026-08-16T00:00:00+08:00",
                row_count=None,
                data_period=None,
                response_sha256=None,
                response_sha256_scope=None,
            )
        with pytest.raises(ValueError, match="row_count must reflect real rows"):
            TushareQueryResult(
                endpoint="moneyflow",
                params={},
                state=STATE_FIELD_MISSING,
                queried_at="2026-08-16T00:00:00+08:00",
                row_count=0,
                data_period=None,
                response_sha256="x",
                response_sha256_scope="frame",
                missing_fields=("buy_lg_amount",),
            )

    def test_row_count_must_match_attached_frame(self):
        with pytest.raises(ValueError, match="row_count must match"):
            TushareQueryResult(
                endpoint="income",
                params={},
                state=STATE_HAS_DATA,
                queried_at="2026-08-16T00:00:00+08:00",
                row_count=5,
                data_period=None,
                response_sha256="x",
                response_sha256_scope="frame",
                frame=pd.DataFrame([{"a": 1}]),
            )


class TestExecuteStructuredQuery:
    def test_success_returns_has_data_with_params_summary(self):
        result = execute_structured_query(
            lambda endpoint, **kw: _income_frame(),
            "income",
            {"ts_code": "603629.SH", "start_date": "20250101"},
        )
        assert result.state == STATE_HAS_DATA
        record = result.to_dict()
        assert record["params"] == {
            "ts_code": "603629.SH",
            "start_date": "20250101",
        }
        assert record["cache"]["upstream_called"] is True
        assert record["cache"]["hit"] is False

    def test_fresh_cache_entry_short_circuits_upstream_call(self):
        def must_not_be_called(_endpoint, **_kwargs):
            raise AssertionError("fresh cache must not trigger an upstream call")

        result = execute_structured_query(
            must_not_be_called,
            "income",
            {"ts_code": "603629.SH"},
            cache_entry=(_income_frame(), 10.0, 300.0),
        )
        assert result.state == STATE_HAS_DATA
        record = result.to_dict()
        assert record["cache"] == {
            "hit": True,
            "expired": False,
            "stale_served": False,
            "upstream_called": False,
            "age_seconds": 10.0,
            "ttl_seconds": 300.0,
        }

    def test_expired_cache_plus_upstream_failure_yields_stale_not_failure(self):
        def fail(_endpoint, **_kwargs):
            raise RuntimeError("抱歉，您每分钟最多访问该接口5次")

        result = execute_structured_query(
            fail,
            "income",
            {"ts_code": "603629.SH"},
            cache_entry=(_income_frame(), 5000.0, 300.0),
        )
        assert result.state == STATE_STALE
        record = result.to_dict()
        assert record["row_count"] == 2
        assert record["cache"]["expired"] is True
        assert record["error_type"] == "RuntimeError"

    def test_upstream_failure_without_cache_is_classified_not_stale(self):
        def fail(_endpoint, **_kwargs):
            raise RuntimeError("抱歉，您没有访问该接口的权限")

        result = execute_structured_query(
            fail, "income", {"ts_code": "603629.SH"}, token=TOKEN
        )
        assert result.state == STATE_PERMISSION_DENIED
        assert result.row_count is None

    def test_token_is_redacted_from_every_error_artifact(self):
        def fail(_endpoint, **_kwargs):
            raise RuntimeError(f"request rejected: {TOKEN}")

        result = execute_structured_query(
            fail, "income", {"ts_code": "603629.SH"}, token=TOKEN
        )
        record = result.to_dict()
        dumped = json.dumps(record)
        assert TOKEN not in dumped
        assert "[REDACTED]" in record["error"]
        assert TOKEN not in result.response_sha256  # hash never embeds plaintext


class TestProviderIntegration:
    def test_optional_query_collapse_is_eliminated_in_audit_trail(self):
        basic = pd.DataFrame(
            [{"ts_code": "603629.SH", "exchange": "SSE", "list_date": "20181224"}]
        )
        company = pd.DataFrame([{"ts_code": "603629.SH", "exchange": "SSE"}])

        def query(endpoint: str, **_kwargs):
            if endpoint == "stock_basic":
                return basic
            if endpoint == "stock_company":
                return company
            if endpoint == "daily_basic":
                raise RuntimeError("抱歉，您没有访问该接口的权限")
            return pd.DataFrame()

        provider = CnTushareProvider(query_fn=query)
        payload = provider.get_fundamentals(
            "603629.SH", curr_date=pd.Timestamp.now().strftime("%Y-%m-%d")
        )

        # Compatibility text contract is unchanged.
        assert "data_quality=PARTIAL" in payload
        assert "optional_endpoints=daily_basic" in payload
        assert "没有访问该接口的权限" not in payload
        # Audit truth now distinguishes the true state.
        audit = provider.last_structured_result("daily_basic")
        assert audit is not None
        assert audit["state"] == STATE_PERMISSION_DENIED
        assert audit["row_count"] is None
        assert "没有访问该接口的权限" in audit["error"]
        assert audit["cache"]["upstream_called"] is True

    def test_rate_limited_query_records_state_and_keeps_raise_behavior(self):
        def fail(_endpoint, **_kwargs):
            raise RuntimeError("抱歉，您每分钟最多访问该接口5次")

        provider = CnTushareProvider(query_fn=fail)
        with pytest.raises(RuntimeError, match="每分钟最多访问"):
            provider.get_individual_fund_flow("603629.SH")
        audit = provider.last_structured_result("moneyflow")
        assert audit["state"] == STATE_RATE_LIMITED
        assert audit["row_count"] is None

    def test_lhb_not_triggered_records_not_queried(self):
        provider = CnTushareProvider(query_fn=lambda *_a, **_k: pd.DataFrame())
        payload = provider.get_lhb_detail("603629.SH", "2026-08-10", force=False)
        assert "LHB_NOT_QUERIED" in payload
        audit = provider.last_structured_result("top_list")
        assert audit["state"] == STATE_NOT_QUERIED
        assert audit["cache"]["upstream_called"] is False

    def test_unconfigured_provider_fails_closed_without_token_leak(
        self, monkeypatch
    ):
        monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
        provider = CnTushareProvider(token="")
        assert provider.configured is False

        with pytest.raises(RuntimeError, match="TUSHARE_TOKEN"):
            provider._query("daily", ts_code="603629.SH", start_date="20260801", end_date="20260810")

        audit = provider.last_structured_result("daily")
        assert audit["state"] == STATE_NOT_QUERIED
        assert audit["cache"]["upstream_called"] is False
        dumped = json.dumps(provider.structured_results())
        assert "TUSHARE_TOKEN" not in dumped.replace("requires TUSHARE_TOKEN", "")

    def test_stale_cache_audit_when_upstream_fails(self):
        frame = pd.DataFrame(
            [{"trade_date": "20260810", "close": 10.0, "pe": 12.0}]
        )

        def succeed_then_fail():
            calls = {"count": 0}

            def query(endpoint: str, **_kwargs):
                calls["count"] += 1
                if calls["count"] > 1:
                    raise RuntimeError("remote end closed connection without response")
                return frame.copy()

            return query

        provider = CnTushareProvider(query_fn=succeed_then_fail())
        first = provider._query("daily_basic", ts_code="603629.SH")
        assert not first.empty
        # Force the cached entry to be expired.
        for key, (_ts, cached_frame) in list(provider._cache.items()):
            provider._cache[key] = (time.monotonic() - 1000.0, cached_frame)

        unavailable: list[str] = []
        result = provider._optional_query(
            "daily_basic", unavailable=unavailable, ts_code="603629.SH"
        )
        assert result.empty
        assert unavailable == ["daily_basic"]

        audit = provider.last_structured_result("daily_basic")
        assert audit["state"] == STATE_STALE
        assert audit["row_count"] == 1
        assert audit["cache"]["expired"] is True
        assert audit["cache"]["age_seconds"] > 900

        # Required-path behavior unchanged: the failure still raises.
        with pytest.raises(RuntimeError, match="remote end closed"):
            provider._query("daily_basic", ts_code="603629.SH")

    def test_fresh_cache_hit_is_recorded_with_cache_metadata(self):
        frame = pd.DataFrame([{"trade_date": "20260810", "close": 10.0}])
        provider = CnTushareProvider(
            query_fn=lambda _endpoint, **_k: frame.copy()
        )
        provider._query("daily", ts_code="603629.SH")
        provider._query("daily", ts_code="603629.SH")

        audit = provider.last_structured_result("daily")
        assert audit["state"] == STATE_HAS_DATA
        assert audit["cache"]["hit"] is True
        assert audit["cache"]["upstream_called"] is False
        assert audit["cache"]["age_seconds"] >= 0.0

    def test_field_missing_moneyflow_records_state_and_kept_compat_raise(self):
        frame = pd.DataFrame([{"trade_date": "20260810", "net_mf_amount": 1}])
        provider = CnTushareProvider(query_fn=lambda _e, **_k: frame.copy())

        with pytest.raises(NotImplementedError, match="large/extra-large"):
            provider.get_individual_fund_flow("603629.SH")

        audit = provider.last_structured_result("moneyflow")
        assert audit["state"] == STATE_FIELD_MISSING
        assert set(audit["missing_fields"]) == {
            "buy_lg_amount",
            "sell_lg_amount",
            "buy_elg_amount",
            "sell_elg_amount",
        }
        assert audit["row_count"] == 1

    def test_structured_audit_is_bounded_and_json_serializable(self):
        frame = pd.DataFrame([{"trade_date": "20260810"}])
        provider = CnTushareProvider(query_fn=lambda _e, **_k: frame.copy())
        for _ in range(200):
            provider._query("daily", ts_code="603629.SH")
        assert len(provider.structured_results()) == 128
        records = provider.structured_results(endpoint="daily")
        assert records and all(r["endpoint"] == "daily" for r in records)
        dumped = json.dumps(records)
        assert "frame" not in records[0]
        assert isinstance(dumped, str)

    def test_provider_audit_never_contains_token(self, monkeypatch):
        secret = "provider-audit-sensitive-token"
        monkeypatch.delenv("TUSHARE_TOKEN", raising=False)

        def fail(_endpoint, **_kwargs):
            raise RuntimeError(f"upstream rejected {secret}")

        provider = CnTushareProvider(token=secret, query_fn=fail)
        payload = provider.get_margin_trading("603629.SH")
        assert "MARGIN_FAILED" in payload
        assert secret not in payload

        dumped = json.dumps(provider.structured_results())
        assert secret not in dumped
        assert "[REDACTED]" in provider.last_structured_result("margin_detail")["error"]

    def test_query_fn_not_implemented_is_not_treated_as_not_queried(self):
        def fail(_endpoint, **_kwargs):
            raise NotImplementedError("upstream bug")

        provider = CnTushareProvider(query_fn=fail)
        with pytest.raises(RuntimeError, match="upstream bug"):
            provider._query("daily", ts_code="603629.SH")
        audit = provider.last_structured_result("daily")
        # The upstream WAS called; only local setup failures are NOT_QUERIED.
        assert audit["state"] == STATE_QUERY_FAILED
        assert audit["cache"]["upstream_called"] is True

    def test_contract_schema_version_stamp(self):
        result = not_queried_result("top_list", {}, "skip")
        assert result.to_dict()["contract_schema_version"] == CONTRACT_SCHEMA_VERSION
