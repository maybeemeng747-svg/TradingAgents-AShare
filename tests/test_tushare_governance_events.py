"""TA-TUSHARE-2000-001D: Tushare governance event pack tests."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from tradingagents.dataflows.tushare_governance_events import (
    GOVERNANCE_ENDPOINT_SPECS,
    GOVERNANCE_SCHEMA_VERSION,
    TOKEN_STATUS_HAS_KEY,
    TOKEN_STATUS_NO_KEY,
    build_governance_event_pack,
    governance_params,
    validate_governance_pack,
)
from tradingagents.dataflows.tushare_query_contract import (
    STATE_FIELD_MISSING,
    STATE_HAS_DATA,
    STATE_NORMAL_NO_DATA,
    STATE_NOT_QUERIED,
    STATE_PERMISSION_DENIED,
    STATE_QUERY_FAILED,
    STATE_RATE_LIMITED,
)

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "export_tushare_governance_events.py"
FIXTURE = (
    ROOT / "tests" / "fixtures" / "tushare_governance_events" / "example_sanitized.json"
)
AS_OF = "2026-08-16"


def _query_from(responses: dict[str, object]):
    def query(endpoint: str, **_kwargs):
        value = responses.get(endpoint)
        if isinstance(value, Exception):
            raise value
        if isinstance(value, dict) and "error" in value:
            raise RuntimeError(value["error"])
        if value is None:
            return pd.DataFrame()
        return pd.DataFrame(value).copy()

    return query


def _fixture_query(endpoint: str, **_kwargs):
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return _query_from(payload["endpoints"])(endpoint)


class TestGovernancePack:
    def test_same_symbol_pack_covers_all_eight_event_endpoints(self):
        pack = build_governance_event_pack(
            symbol="603629.SH", as_of=AS_OF, query_fn=_query_from({})
        )
        assert tuple(pack["endpoints"]) == tuple(
            spec.endpoint for spec in GOVERNANCE_ENDPOINT_SPECS
        )
        assert len(GOVERNANCE_ENDPOINT_SPECS) == 8
        for endpoint, record in pack["endpoints"].items():
            for key in (
                "params",
                "queried_at",
                "data_period",
                "response_sha256",
                "cache",
                "error",
                "row_count",
                "eligible_row_count",
                "records",
                "source_endpoint",
                "permission_status",
            ):
                assert key in record, f"{endpoint} missing {key}"
            assert record["source_endpoint"] == endpoint
        assert pack["governance_schema_version"] == GOVERNANCE_SCHEMA_VERSION
        assert pack["schema"] == "tushare_governance_events"

    def test_no_event_and_query_failure_are_strictly_distinguished(self):
        pack = build_governance_event_pack(
            symbol="603629.SH",
            as_of=AS_OF,
            query_fn=_query_from(
                {
                    "pledge_detail": RuntimeError("抱歉，您没有访问该接口的权限"),
                    "stk_holdernumber": [],
                }
            ),
        )
        no_event = pack["endpoints"]["stk_holdernumber"]
        failed = pack["endpoints"]["pledge_detail"]
        assert no_event["state"] == STATE_NORMAL_NO_DATA
        assert no_event["records"] == []
        assert no_event["error"] is None
        assert failed["state"] == STATE_PERMISSION_DENIED
        assert failed["records"] is None
        assert failed["error"]
        anomaly = {item["endpoint"]: item for item in pack["anomalies"]}
        assert "pledge_detail" in anomaly
        assert "stk_holdernumber" not in anomaly

    @pytest.mark.parametrize(
        "message,expected",
        [
            ("抱歉，您没有访问该接口的权限", STATE_PERMISSION_DENIED),
            ("抱歉，您每分钟最多访问该接口5次", STATE_RATE_LIMITED),
            ("remote end closed connection", STATE_QUERY_FAILED),
        ],
    )
    def test_failure_states_distinct_and_records_null(self, message, expected):
        pack = build_governance_event_pack(
            symbol="603629.SH",
            as_of=AS_OF,
            query_fn=_query_from({"top10_holders": RuntimeError(message)}),
        )
        record = pack["endpoints"]["top10_holders"]
        assert record["state"] == expected
        assert record["records"] is None
        assert record["row_count"] is None

    def test_events_keep_announcement_and_report_dates(self):
        pack = build_governance_event_pack(
            symbol="603629.SH",
            as_of=AS_OF,
            query_fn=_query_from(
                {
                    "stk_holdertrade": [
                        {
                            "ts_code": "603629.SH",
                            "ann_date": "20260715",
                            "holder_name": "合成示例",
                            "change_type": "减持",
                        }
                    ]
                }
            ),
        )
        record = pack["endpoints"]["stk_holdertrade"]
        assert record["state"] == STATE_HAS_DATA
        assert record["records"][0]["ann_date"] == "20260715"
        assert record["source_endpoint"] == "stk_holdertrade"

    def test_future_disclosures_are_filtered_from_records(self):
        pack = build_governance_event_pack(
            symbol="603629.SH",
            as_of=AS_OF,
            query_fn=_query_from(
                {
                    "share_float": [
                        {
                            "ts_code": "603629.SH",
                            "ann_date": "20260801",
                            "float_date": "20260901",
                            "float_share": 100.0,
                        },
                        {
                            "ts_code": "603629.SH",
                            "ann_date": "20270101",
                            "float_date": "20270201",
                            "float_share": 200.0,
                        },
                    ]
                }
            ),
        )
        record = pack["endpoints"]["share_float"]
        assert record["row_count"] == 2
        assert record["eligible_row_count"] == 1
        assert record["records"][0]["ann_date"] == "20260801"

    def test_repurchase_market_window_is_filtered_to_symbol(self):
        pack = build_governance_event_pack(
            symbol="603629.SH", as_of=AS_OF, query_fn=_fixture_query
        )
        record = pack["endpoints"]["repurchase"]
        assert record["response_sha256_scope"] == "frame_symbol_filtered"
        assert record["row_count"] == 1
        assert record["records"][0]["ts_code"] == "603629.SH"
        assert all(row["ts_code"] == "603629.SH" for row in record["records"])
        # The market-window query must not carry ts_code upstream.
        params = governance_params(
            next(s for s in GOVERNANCE_ENDPOINT_SPECS if s.endpoint == "repurchase"),
            "603629.SH",
            AS_OF,
        )
        assert "ts_code" not in params
        assert params["start_date"] and params["end_date"]

    def test_repurchase_window_without_symbol_matches_is_no_event(self):
        pack = build_governance_event_pack(
            symbol="603629.SH",
            as_of=AS_OF,
            query_fn=_query_from(
                {
                    "repurchase": [
                        {
                            "ts_code": "000001.SZ",
                            "ann_date": "20260610",
                            "end_date": "20260630",
                        }
                    ]
                }
            ),
        )
        record = pack["endpoints"]["repurchase"]
        assert record["state"] == STATE_NORMAL_NO_DATA
        assert record["records"] == []

    def test_freshness_reports_latest_event_dates(self):
        pack = build_governance_event_pack(
            symbol="603629.SH", as_of=AS_OF, query_fn=_fixture_query
        )
        freshness = pack["freshness"]["stk_holdernumber"]
        assert freshness["latest_event_date"] == "20260422"
        assert freshness["as_of"] == AS_OF
        assert freshness["days_behind_as_of"] > 0
        assert pack["freshness"]["pledge_detail"]["latest_event_date"] is None

    def test_no_key_fails_closed_without_any_network_call(self):
        def must_not_call(_endpoint, **_kwargs):
            raise AssertionError("NO_KEY must never trigger an upstream call")

        pack = build_governance_event_pack(
            symbol="603629.SH",
            as_of=AS_OF,
            query_fn=must_not_call,
            token_status=TOKEN_STATUS_NO_KEY,
        )
        assert pack["status"] == STATE_NOT_QUERIED
        for record in pack["endpoints"].values():
            assert record["state"] == STATE_NOT_QUERIED
            assert record["records"] is None
            assert record["cache"]["upstream_called"] is False

    def test_fixture_mode_with_no_key_still_collects(self):
        pack = build_governance_event_pack(
            symbol="603629.SH",
            as_of=AS_OF,
            query_fn=_fixture_query,
            token_status=TOKEN_STATUS_NO_KEY,
            collection_mode="fixture",
        )
        assert pack["collection_mode"] == "fixture"
        assert pack["endpoints"]["stk_holdernumber"]["state"] == STATE_HAS_DATA

    def test_field_missing_records_null(self):
        rows = [{"ts_code": "603629.SH", "ann_date": "20260715"}]  # no end_date
        pack = build_governance_event_pack(
            symbol="603629.SH",
            as_of=AS_OF,
            query_fn=_query_from({"pledge_stat": rows}),
        )
        record = pack["endpoints"]["pledge_stat"]
        assert record["state"] == STATE_FIELD_MISSING
        assert record["records"] is None

    def test_pack_json_has_no_token_and_no_action_words(self):
        secret = "governance-sensitive-token-0123456789abcdef"

        def leak(_endpoint, **_kwargs):
            raise RuntimeError(f"upstream rejected {secret}")

        pack = build_governance_event_pack(
            symbol="603629.SH", as_of=AS_OF, query_fn=leak, token=secret
        )
        dumped = json.dumps(pack, ensure_ascii=False)
        assert secret not in dumped
        assert "[REDACTED]" in dumped


class TestGovernanceValidation:
    def _base_pack(self) -> dict:
        return build_governance_event_pack(
            symbol="603629.SH", as_of=AS_OF, query_fn=_query_from({})
        )

    def test_rejects_failure_state_with_empty_records(self):
        pack = self._base_pack()
        pack["endpoints"]["share_float"]["state"] = STATE_PERMISSION_DENIED
        pack["endpoints"]["share_float"]["records"] = []
        with pytest.raises(ValueError, match="must not embed records"):
            validate_governance_pack(pack)

    def test_rejects_events_without_dates(self):
        pack = self._base_pack()
        pack["endpoints"]["share_float"]["state"] = STATE_HAS_DATA
        pack["endpoints"]["share_float"]["row_count"] = 1
        pack["endpoints"]["share_float"]["eligible_row_count"] = 1
        pack["endpoints"]["share_float"]["records"] = [{"ts_code": "603629.SH"}]
        with pytest.raises(ValueError, match="announcement/report dates"):
            validate_governance_pack(pack)

    def test_rejects_trading_action_words(self):
        pack = self._base_pack()
        pack["endpoints"]["share_float"]["state"] = STATE_HAS_DATA
        pack["endpoints"]["share_float"]["row_count"] = 1
        pack["endpoints"]["share_float"]["eligible_row_count"] = 1
        pack["endpoints"]["share_float"]["records"] = [
            {"ts_code": "603629.SH", "ann_date": "20260801", "note": "建议买入"}
        ]
        with pytest.raises(ValueError, match="trading action words"):
            validate_governance_pack(pack)

    def test_rejects_missing_source_endpoint(self):
        pack = self._base_pack()
        pack["endpoints"]["share_float"]["source_endpoint"] = ""
        with pytest.raises(ValueError, match="source endpoint"):
            validate_governance_pack(pack)

    def test_holder_increase_decrease_is_governance_fact_not_action_word(self):
        pack = self._base_pack()
        pack["endpoints"]["share_float"]["state"] = STATE_HAS_DATA
        pack["endpoints"]["share_float"]["row_count"] = 1
        pack["endpoints"]["share_float"]["eligible_row_count"] = 1
        pack["endpoints"]["share_float"]["records"] = [
            {
                "ts_code": "603629.SH",
                "ann_date": "20260715",
                "change_type": "减持",
                "holder_name": "合成示例股东",
            }
        ]
        validate_governance_pack(pack)


class TestSanitizedExampleFixture:
    def test_fixture_example_pack_builds_and_validates(self):
        pack = build_governance_event_pack(
            symbol="603629.SH",
            as_of=AS_OF,
            query_fn=_fixture_query,
            token_status=TOKEN_STATUS_HAS_KEY,
        )
        validate_governance_pack(pack)
        states = pack["summary"]["by_state"]
        assert states[STATE_HAS_DATA] == 7
        assert states[STATE_NORMAL_NO_DATA] == 1  # pledge_detail empty in fixture
        assert pack["status"] == STATE_HAS_DATA


def _load_script():
    spec = importlib.util.spec_from_file_location("export_tushare_governance_events", SCRIPT)
    assert spec and spec.loader, "unable to build spec for export script"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestExportScript:
    def test_default_permission_matrix_is_audited_rerun_truth_source(self):
        # 001A-R1A final review P2: default exports must consume the audited
        # R1 rerun matrix, not the superseded 041709 archive.
        default = _load_script().DEFAULT_PERMISSION_MATRIX
        assert default.is_file()
        assert default.parent.name == "TA-TUSHARE-2000-001A-R1-RERUN-20260816-202237"
        matrix = json.loads(default.read_text(encoding="utf-8"))
        assert len(matrix["endpoints"]) == 24

    def test_scan_text_for_secret_detects_serialized_headers(self):
        # 001A-R1A final review P1#2: quoted/dict header forms must trip the
        # credential self-check even with a short value and no token match.
        module = _load_script()
        assert module.scan_text_for_secret('{"Cookie": "short-secret"}', "")
        assert module.scan_text_for_secret("headers={'Authorization': 'Bearer x'}", "")
        assert module.scan_text_for_secret("Cookie: sid=xyz", "")
        assert not module.scan_text_for_secret("clean sanitized error text", "")

    def _run(self, *args: str, env: dict[str, str] | None = None):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin", **(env or {})},
            cwd=str(ROOT),
        )

    def test_output_is_required(self):
        result = self._run("603629.SH", "--as-of", AS_OF, "--no-dotenv")
        assert result.returncode == 2
        assert "--output is required" in result.stderr

    def test_no_key_fails_closed_without_fixture(self, tmp_path):
        result = self._run(
            "603629.SH",
            "--as-of",
            AS_OF,
            "--output",
            str(tmp_path / "g.json"),
            "--no-dotenv",
        )
        assert result.returncode == 2
        assert "NO_KEY" in result.stdout

    def test_refuses_existing_output(self, tmp_path):
        target = tmp_path / "governance.json"
        target.write_text("sentinel", encoding="utf-8")
        result = self._run(
            "603629.SH",
            "--as-of",
            AS_OF,
            "--output",
            str(target),
            "--fixture",
            str(FIXTURE),
            "--no-dotenv",
        )
        assert result.returncode == 2
        assert "refusing to overwrite" in result.stderr
        assert target.read_text(encoding="utf-8") == "sentinel"

    @pytest.mark.parametrize(
        "forbidden",
        ["knowledge_base", "tree_work", "haigui", "hairui"],
    )
    def test_refuses_knowledge_or_hairui_directories(self, tmp_path, forbidden):
        target = tmp_path / forbidden / "governance.json"
        result = self._run(
            "603629.SH",
            "--as-of",
            AS_OF,
            "--output",
            str(target),
            "--fixture",
            str(FIXTURE),
            "--no-dotenv",
        )
        assert result.returncode == 2
        assert "refused" in result.stderr or "knowledge-base" in result.stderr

    def test_fixture_mode_writes_valid_pack_with_sanitized_summary(self, tmp_path):
        target = tmp_path / "out" / "governance.json"
        result = self._run(
            "603629.SH",
            "--as-of",
            AS_OF,
            "--output",
            str(target),
            "--fixture",
            str(FIXTURE),
            "--sleep-seconds",
            "0",
            "--no-dotenv",
        )
        assert result.returncode == 0, result.stderr
        pack = json.loads(target.read_text(encoding="utf-8"))
        validate_governance_pack(pack)
        summary = json.loads(result.stdout[result.stdout.index("{"):])
        for record in summary["endpoints"].values():
            assert "records" not in record
        assert "holder_nums" not in json.dumps(summary)
