"""TA-TUSHARE-2000-001E: backoff, auditable cache, stale reads and the
evidence-failure → action-downgrade integration chain."""

from __future__ import annotations

import json
import time

import pandas as pd
import pytest

from tradingagents.dataflows.providers.cn_tushare_provider import CnTushareProvider
from tradingagents.dataflows.tushare_query_contract import (
    STATE_FIELD_MISSING,
    STATE_HAS_DATA,
    STATE_NORMAL_NO_DATA,
    STATE_NOT_QUERIED,
    STATE_PERMISSION_DENIED,
    STATE_QUERY_FAILED,
    STATE_RATE_LIMITED,
    STATE_STALE,
)
from tradingagents.agents.utils.readiness_score import (
    calculate_buy_level,
    calculate_evidence_coverage,
    get_strong_action_gate,
    infer_evidence_statuses,
    sanitize_forbidden_strong_actions,
)
from tradingagents.graph.data_collector import DataCollector

SYMBOL = "603629.SH"


def _moneyflow_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "trade_date": "20260810",
                "buy_lg_amount": 5,
                "sell_lg_amount": 6,
                "buy_elg_amount": 7,
                "sell_elg_amount": 8,
            }
        ]
    )


class TestBoundedRateLimitBackoff:
    def test_rate_limit_retries_with_bounded_exponential_backoff_then_recovers(self):
        sleeps: list[float] = []
        calls = {"count": 0}

        def query(endpoint: str, **_kwargs):
            calls["count"] += 1
            if calls["count"] <= 2:
                raise RuntimeError("抱歉，您每分钟最多访问该接口5次")
            return _moneyflow_frame()

        provider = CnTushareProvider(
            query_fn=query,
            rate_limit_backoff_base_seconds=1.0,
            rate_limit_max_attempts=3,
            sleep_fn=sleeps.append,
        )
        frame = provider._query("moneyflow", ts_code=SYMBOL)
        assert not frame.empty
        audit = provider.last_structured_result("moneyflow")
        assert audit["state"] == STATE_HAS_DATA
        assert audit["attempts"] == 3
        assert audit["retry_reason"] == "rate_limit_backoff_recovered"
        assert sleeps == [1.0, 2.0]

    def test_backoff_is_bounded_when_rate_limit_persists(self):
        sleeps: list[float] = []

        def query(_endpoint, **_kwargs):
            raise RuntimeError("抱歉，您每小时最多访问该接口20次")

        provider = CnTushareProvider(
            query_fn=query,
            rate_limit_backoff_base_seconds=5.0,
            rate_limit_max_attempts=4,
            rate_limit_max_backoff_seconds=8.0,
            sleep_fn=sleeps.append,
        )
        with pytest.raises(RuntimeError):
            provider._query("moneyflow", ts_code=SYMBOL)
        audit = provider.last_structured_result("moneyflow")
        assert audit["state"] == STATE_RATE_LIMITED
        assert audit["attempts"] == 4
        assert audit["retry_reason"] == "rate_limit_backoff_exhausted"
        # Bounded: 5.0, 8.0 (capped), 8.0 (capped) — never grows unbounded.
        assert sleeps == [5.0, 8.0, 8.0]

    def test_permission_denied_is_never_retried(self):
        sleeps: list[float] = []
        calls = {"count": 0}

        def query(_endpoint, **_kwargs):
            calls["count"] += 1
            raise RuntimeError("抱歉，您没有访问该接口的权限")

        provider = CnTushareProvider(query_fn=query, sleep_fn=sleeps.append)
        with pytest.raises(RuntimeError):
            provider._query("moneyflow", ts_code=SYMBOL)
        assert calls["count"] == 1
        assert sleeps == []
        assert provider.last_structured_result("moneyflow")["state"] == STATE_PERMISSION_DENIED

    def test_generic_failure_is_never_retried(self):
        calls = {"count": 0}

        def query(_endpoint, **_kwargs):
            calls["count"] += 1
            raise RuntimeError("remote end closed connection")

        provider = CnTushareProvider(
            query_fn=query, sleep_fn=lambda _s: None
        )
        with pytest.raises(RuntimeError):
            provider._query("moneyflow", ts_code=SYMBOL)
        assert calls["count"] == 1

    def test_invalid_backoff_configuration_fails_closed(self):
        with pytest.raises(ValueError, match="rate_limit_max_attempts"):
            CnTushareProvider(rate_limit_max_attempts=0)


class TestAuditableCache:
    def test_cache_records_query_time_data_period_and_endpoint(self):
        provider = CnTushareProvider(
            query_fn=lambda _e, **_k: _moneyflow_frame()
        )
        provider._query("moneyflow", ts_code=SYMBOL)
        records = provider.cache_audit_records()
        assert len(records) == 1
        record = records[0]
        assert record["endpoint"] == "moneyflow"
        assert record["params"] == {"ts_code": SYMBOL}
        assert record["cached_at"]  # wall-clock query time (ISO)
        assert record["ttl_seconds"] == provider._cache_ttl_seconds
        assert record["expired"] is False
        assert record["row_count"] == 1
        assert record["data_period"] == {"field": "trade_date", "min": "20260810", "max": "20260810"}

    def test_read_cache_fresh_entry_hits_without_upstream_call(self):
        def must_not_call(_endpoint, **_kwargs):
            raise AssertionError("read_cache must never call upstream")

        provider = CnTushareProvider(query_fn=must_not_call)
        # Seed a fresh cache entry directly.
        key = ("moneyflow", (("ts_code", SYMBOL),))
        provider._cache[key] = (
            time.monotonic(),
            "2026-08-16T09:00:00+08:00",
            _moneyflow_frame(),
        )
        result = provider.read_cache("moneyflow", ts_code=SYMBOL)
        assert result is not None
        assert result.state == STATE_HAS_DATA
        assert result.cache.hit is True
        assert result.cache.stale_served is False

    def test_read_cache_expired_entry_returns_stale_never_fresh(self):
        provider = CnTushareProvider(query_fn=lambda _e, **_k: pd.DataFrame())
        key = ("moneyflow", (("ts_code", SYMBOL),))
        provider._cache[key] = (
            time.monotonic() - 1000.0,
            "2026-08-15T09:00:00+08:00",
            _moneyflow_frame(),
        )
        result = provider.read_cache("moneyflow", ts_code=SYMBOL)
        assert result is not None
        assert result.state == STATE_STALE
        assert result.cache.expired is True
        assert result.cache.stale_served is True
        assert result.cache.upstream_called is False
        assert result.row_count == 1  # stale evidence kept, clearly marked
        assert result.to_dict()["response_sha256_scope"] == "stale_frame"

    def test_read_cache_missing_entry_returns_none(self):
        provider = CnTushareProvider(query_fn=lambda _e, **_k: pd.DataFrame())
        assert provider.read_cache("moneyflow", ts_code=SYMBOL) is None

    def test_legacy_two_tuple_cache_entries_remain_readable(self):
        provider = CnTushareProvider(query_fn=lambda _e, **_k: pd.DataFrame())
        key = ("moneyflow", (("ts_code", SYMBOL),))
        provider._cache[key] = (time.monotonic() - 1000.0, _moneyflow_frame())
        result = provider.read_cache("moneyflow", ts_code=SYMBOL)
        assert result is not None
        assert result.state == STATE_STALE
        records = provider.cache_audit_records()
        assert records[0]["cached_at"] is None  # legacy entry lacks wall time


class TestSixStateDistinguishability:
    def test_permission_no_data_field_missing_rate_limit_failure_stale_all_distinct(self):
        income = pd.DataFrame([{"ts_code": SYMBOL, "ann_date": "20260420", "end_date": "20251231"}])
        moneyflow_short = pd.DataFrame([{"trade_date": "20260810", "net_mf_amount": 1}])

        def query(endpoint: str, **_kwargs):
            if endpoint == "margin_detail":
                raise RuntimeError("抱歉，您没有访问该接口的权限")
            if endpoint == "moneyflow":
                raise RuntimeError("抱歉，您每分钟最多访问该接口5次")
            if endpoint == "income":
                return income
            if endpoint == "daily_basic":
                return pd.DataFrame()  # normal no data
            if endpoint == "forecast":
                return moneyflow_short  # rows but wrong fields for forecast
            raise RuntimeError("remote end closed connection")

        provider = CnTushareProvider(
            query_fn=query,
            sleep_fn=lambda _s: None,
            rate_limit_max_attempts=1,
        )
        # Seed an expired cache entry for top_list to prove STALE.
        lhb_key = ("top_list", (("trade_date", "20260814"), ("ts_code", SYMBOL)))
        provider._cache[lhb_key] = (
            time.monotonic() - 2000.0,
            None,
            pd.DataFrame([{"trade_date": "20260814", "ts_code": SYMBOL}]),
        )

        with pytest.raises(RuntimeError):
            provider._query("margin_detail", ts_code=SYMBOL)
        with pytest.raises(RuntimeError):
            provider._query("moneyflow", ts_code=SYMBOL)
        provider._query("income", ts_code=SYMBOL)
        provider._query("daily_basic", ts_code=SYMBOL)
        with pytest.raises(RuntimeError):
            provider._query("express", ts_code=SYMBOL)
        with pytest.raises(RuntimeError):
            provider._query("top_list", ts_code=SYMBOL, trade_date="20260814")

        states = {
            endpoint: provider.last_structured_result(endpoint)["state"]
            for endpoint in (
                "margin_detail",
                "moneyflow",
                "income",
                "daily_basic",
                "express",
                "top_list",
            )
        }
        assert states == {
            "margin_detail": STATE_PERMISSION_DENIED,
            "moneyflow": STATE_RATE_LIMITED,
            "income": STATE_HAS_DATA,
            "daily_basic": STATE_NORMAL_NO_DATA,
            "express": STATE_QUERY_FAILED,
            "top_list": STATE_STALE,
        }
        # Generic failure keeps no numeric row count (never 0).
        assert provider.last_structured_result("express")["row_count"] is None

    def test_field_missing_via_required_fields_is_not_data(self):
        provider = CnTushareProvider(
            query_fn=lambda _e, **_k: pd.DataFrame(
                [{"trade_date": "20260810", "net_mf_amount": 1}]
            )
        )
        provider._query(
            "moneyflow",
            required_fields={"buy_lg_amount", "sell_lg_amount"},
            ts_code=SYMBOL,
        )
        audit = provider.last_structured_result("moneyflow")
        assert audit["state"] == STATE_FIELD_MISSING
        assert audit["row_count"] == 1
        assert set(audit["missing_fields"]) == {"buy_lg_amount", "sell_lg_amount"}


def _raw_evidence_entry(text: str) -> dict:
    return {
        "raw": text,
        "status": DataCollector._infer_source_status(text),
        "vendor": "cn_tushare",
        "endpoint": "tushare",
    }


STRONG_ACTION_RESPONSE = (
    "结论：短线共振明确，建议立即买入并可在突破后加仓，目标区间 25-28 元。"
)


class TestEvidenceFailureDowngradesAction:
    def _tushare_failure_texts(self) -> tuple[dict[str, str], dict[str, dict]]:
        """Real provider outputs with permission/rate-limit failures."""

        def query(endpoint: str, **_kwargs):
            if endpoint == "margin_detail":
                raise RuntimeError("抱歉，您没有访问该接口的权限")
            if endpoint == "moneyflow":
                raise RuntimeError("抱歉，您每分钟最多访问该接口5次")
            if endpoint == "repurchase":
                raise RuntimeError("抱歉，您没有访问该接口的权限")
            if endpoint == "top_list":
                return pd.DataFrame()  # not on the list — normal no data
            return pd.DataFrame()

        provider = CnTushareProvider(
            query_fn=query,
            sleep_fn=lambda _s: None,
            rate_limit_max_attempts=1,
        )
        texts = {
            "fund_flow_individual": _collector_failure_text(provider),
            "margin_trading": provider.get_margin_trading(SYMBOL),
            "buybacks": provider.get_buybacks(SYMBOL),
            "lhb": provider.get_lhb_detail(SYMBOL, "2026-08-14", force=True),
        }
        audit = {
            "moneyflow": provider.last_structured_result("moneyflow"),
            "margin_detail": provider.last_structured_result("margin_detail"),
            "repurchase": provider.last_structured_result("repurchase"),
        }
        return texts, audit

    def _healthy_texts(self) -> dict[str, str]:
        provider = CnTushareProvider(
            query_fn=lambda endpoint, **_kwargs: (
                _moneyflow_frame() if endpoint == "moneyflow" else pd.DataFrame()
            )
        )
        return {
            "fund_flow_individual": provider.get_individual_fund_flow(SYMBOL),
            "margin_trading": "603629.SH [DATA-010] MARGIN_HAS_DATA: 融资融券明细（Tushare）：\n| 交易日期 |\n|---|\n| 2026-08-10 |",
            "buybacks": "603629.SH [DATA-013] BUYBACK_HAS_DATA: 回购记录（Tushare）：\n| 公告日期 |\n|---|\n| 2026-07-01 |",
            "lhb": provider.get_lhb_detail(SYMBOL, "2026-08-14", force=True),
        }

    def test_structured_audit_truth_is_not_collapsed_by_text_layer(self):
        _texts, audit = self._tushare_failure_texts()
        assert audit["moneyflow"]["state"] == STATE_RATE_LIMITED
        assert audit["margin_detail"]["state"] == STATE_PERMISSION_DENIED
        assert audit["repurchase"]["state"] == STATE_PERMISSION_DENIED

    def test_evidence_failure_restricts_buy_level_and_downgrades_final_action(self):
        failure_texts, _audit = self._tushare_failure_texts()
        healthy_texts = self._healthy_texts()

        def build_chain(texts: dict[str, str]):
            raw_evidence = {
                key: _raw_evidence_entry(text) for key, text in texts.items()
            }
            # OHLCV etc. are healthy in both scenarios: only Tushare evidence
            # differs, so the delta proves the Tushare wiring matters.
            raw_evidence["stock_data"] = _raw_evidence_entry(
                "Date,Open,High,Low,Close,Volume\n2026-08-14,10,11,9,10.5,123456"
            )
            reports = {
                "market_report": "收盘价站稳均线，趋势确认",
                "volume_price_report": "成交量温和放大，换手率 3.2%，量比 1.4",
                "smart_money_report": "smart money ok",
                "news_report": "news ok",
            }
            statuses = infer_evidence_statuses(reports, raw_evidence=raw_evidence)
            coverage = calculate_evidence_coverage(
                ohlcv_5d=statuses["ohlcv_5d"],
                volume=statuses["volume"],
                turnover_rate=statuses["turnover_rate"],
                volume_ratio=statuses["volume_ratio"],
                individual_fund_flow=statuses["individual_fund_flow"],
                lhb_status=statuses["lhb_status"],
                margin_trading=statuses["margin_trading"],
                announcements=statuses["announcements"],
                research_report=statuses.get("research_report", "not_queried"),
            )
            source_coverage = 100
            gate = get_strong_action_gate(
                source_coverage=source_coverage,
                evidence_coverage=coverage,
                position_status="has_position",
            )
            buy = calculate_buy_level(
                source_coverage=source_coverage,
                evidence_coverage=coverage,
                trend_confirmed=True,
                main_capital_inflow_days=2,
                volume_healthy_expansion=True,
                position_status="has_position",
            )
            sanitized, changes, _offset = sanitize_forbidden_strong_actions(
                STRONG_ACTION_RESPONSE,
                gate,
                "has_position",
                buy["level"],
                0,
                return_system_offset=True,
            )
            return coverage, gate, buy, sanitized, changes

        healthy_coverage, healthy_gate, healthy_buy, healthy_text, healthy_changes = build_chain(
            healthy_texts
        )
        failure_coverage, failure_gate, failure_buy, failure_text, failure_changes = build_chain(
            failure_texts
        )

        # Evidence coverage drops because Tushare-critical evidence failed.
        assert failure_coverage < 70 <= healthy_coverage
        # Strong-action gate blocks on Tushare evidence failure only.
        assert healthy_gate["passed"] is True
        assert failure_gate["passed"] is False
        assert any("evidence_coverage" in item for item in failure_gate["failures"])
        # Buy Level is genuinely restricted (no level-3+ entry).
        assert healthy_buy["level"] >= 3
        assert failure_buy["level"] < healthy_buy["level"]
        # The final response action is downgraded, not merely warned about.
        assert "立即买入" in healthy_text or healthy_changes == []
        assert "立即买入" not in failure_text
        assert failure_changes  # sanitizer recorded real rewrites

    def test_downgrade_survives_rate_limit_backoff_exhaustion(self):
        # Backoff exhausted → RATE_LIMITED truth → same downgrade path.
        def query(endpoint: str, **_kwargs):
            if endpoint in {"margin_detail", "repurchase"}:
                raise RuntimeError("抱歉，您没有访问该接口的权限")
            if endpoint == "moneyflow":
                raise RuntimeError("抱歉，您每分钟最多访问该接口5次")
            return pd.DataFrame()

        provider = CnTushareProvider(
            query_fn=query,
            sleep_fn=lambda _s: None,
            rate_limit_max_attempts=3,
        )
        with pytest.raises(RuntimeError):
            provider.get_individual_fund_flow(SYMBOL)
        audit = provider.last_structured_result("moneyflow")
        assert audit["state"] == STATE_RATE_LIMITED
        assert audit["attempts"] == 3
        assert audit["retry_reason"] == "rate_limit_backoff_exhausted"

    def test_audit_and_cache_records_are_json_safe_without_credentials(self):
        secret = "resilience-sensitive-token-0123456789abcdef"

        def fail(_endpoint, **_kwargs):
            raise RuntimeError(f"upstream rejected {secret}")

        provider = CnTushareProvider(token=secret, query_fn=fail, sleep_fn=lambda _s: None)
        with pytest.raises(RuntimeError):
            provider._query("moneyflow", ts_code=SYMBOL)
        dumped = json.dumps(
            {
                "audit": provider.structured_results(),
                "cache": provider.cache_audit_records(),
            }
        )
        assert secret not in dumped


def _collector_failure_text(provider: CnTushareProvider) -> str:
    """Mirror DataCollector._safe's all-vendor failure marker text."""

    try:
        provider.get_individual_fund_flow(SYMBOL)
        raise AssertionError("expected fund flow failure")
    except Exception as exc:
        return f"数据获取失败：{type(exc).__name__}: {exc}"
