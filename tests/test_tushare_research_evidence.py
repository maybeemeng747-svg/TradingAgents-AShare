"""TA-TUSHARE-2000-001C: Tushare research evidence pack tests."""

from __future__ import annotations

import importlib.util
import json
import math
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

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
from tradingagents.dataflows.tushare_research_evidence import (
    EVIDENCE_ENDPOINT_SPECS,
    EVIDENCE_SCHEMA_VERSION,
    TOKEN_STATUS_NO_KEY,
    _records_for_state,
    build_tushare_research_evidence,
    evidence_params,
    validate_evidence_pack,
)

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "export_tushare_research_evidence.py"
FIXTURE = ROOT / "tests" / "fixtures" / "tushare_research_evidence" / "example_sanitized.json"
AS_OF = "2026-08-16"

EXPECTED_ENDPOINTS = tuple(spec.endpoint for spec in EVIDENCE_ENDPOINT_SPECS)


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


def _income_rows() -> list[dict]:
    return [
        {
            "ts_code": "603629.SH",
            "ann_date": "20260420",
            "f_ann_date": "20260420",
            "end_date": "20251231",
            "report_type": "1",
            "total_revenue": 100.5,
        },
        {
            "ts_code": "603629.SH",
            "ann_date": "20270420",
            "f_ann_date": "20270420",
            "end_date": "20261231",
            "report_type": "1",
            "total_revenue": 999.0,
        },
    ]


class TestEvidencePack:
    def test_covers_all_nine_research_endpoints_with_full_audit_fields(self):
        pack = build_tushare_research_evidence(
            symbol="603629.SH",
            as_of=AS_OF,
            query_fn=_query_from({"income": _income_rows()}),
        )
        assert tuple(pack["endpoints"]) == EXPECTED_ENDPOINTS
        assert len(EXPECTED_ENDPOINTS) == 9
        for endpoint, record in pack["endpoints"].items():
            for key in (
                "params",
                "queried_at",
                "data_period",
                "response_sha256",
                "response_sha256_scope",
                "cache",
                "error",
                "row_count",
                "eligible_row_count",
                "records",
                "permission_status",
                "category",
            ):
                assert key in record, f"{endpoint} missing {key}"
        assert pack["evidence_schema_version"] == EVIDENCE_SCHEMA_VERSION
        assert pack["schema"] == "tushare_research_evidence"

    def test_statement_window_params_summary(self):
        params = evidence_params(EVIDENCE_ENDPOINT_SPECS[0], "603629.SH", AS_OF)
        assert params["ts_code"] == "603629.SH"
        assert params["start_date"] == "20230812"  # 1100 days before 2026-08-16
        assert params["end_date"] == "20260816"
        symbol_only = evidence_params(EVIDENCE_ENDPOINT_SPECS[6], "603629.SH", AS_OF)
        assert symbol_only == {"ts_code": "603629.SH"}

    def test_records_are_normalized_and_future_disclosures_filtered(self):
        pack = build_tushare_research_evidence(
            symbol="603629.SH",
            as_of=AS_OF,
            query_fn=_query_from({"income": _income_rows()}),
        )
        income = pack["endpoints"]["income"]
        assert income["state"] == STATE_HAS_DATA
        assert income["row_count"] == 2  # raw response rows
        assert income["eligible_row_count"] == 1  # as_of filtered
        assert len(income["records"]) == 1
        assert income["records"][0]["end_date"] == "20251231"
        assert all(
            record.get("ann_date", "0") <= "20260816" for record in income["records"]
        )

    def test_nan_values_become_null_in_records(self):
        rows = [
            {
                "ts_code": "603629.SH",
                "ann_date": "20260420",
                "end_date": "20251231",
                "total_revenue": float("nan"),
                "n_income": None,
            }
        ]
        pack = build_tushare_research_evidence(
            symbol="603629.SH", as_of=AS_OF, query_fn=_query_from({"income": rows})
        )
        record = pack["endpoints"]["income"]["records"][0]
        assert record["total_revenue"] is None
        assert record["n_income"] is None

    def test_normal_no_data_keeps_empty_records_with_zero_count(self):
        pack = build_tushare_research_evidence(
            symbol="603629.SH", as_of=AS_OF, query_fn=_query_from({})
        )
        express = pack["endpoints"]["express"]
        assert express["state"] == STATE_NORMAL_NO_DATA
        assert express["records"] == []
        assert express["row_count"] == 0
        assert express["error"] is None

    @pytest.mark.parametrize(
        "message,expected",
        [
            ("抱歉，您没有访问该接口的权限", STATE_PERMISSION_DENIED),
            ("抱歉，您每分钟最多访问该接口5次", STATE_RATE_LIMITED),
            ("remote end closed connection without response", STATE_QUERY_FAILED),
        ],
    )
    def test_failure_states_keep_records_null_and_distinct(self, message, expected):
        pack = build_tushare_research_evidence(
            symbol="603629.SH",
            as_of=AS_OF,
            query_fn=_query_from({"income": RuntimeError(message)}),
        )
        income = pack["endpoints"]["income"]
        assert income["state"] == expected
        assert income["records"] is None
        assert income["row_count"] is None
        assert income["eligible_row_count"] is None
        assert income["error"]

    def test_field_missing_endpoint_keeps_records_null(self):
        rows = [{"ts_code": "603629.SH", "ann_date": "20260420"}]  # no end_date
        pack = build_tushare_research_evidence(
            symbol="603629.SH", as_of=AS_OF, query_fn=_query_from({"income": rows})
        )
        income = pack["endpoints"]["income"]
        assert income["state"] == STATE_FIELD_MISSING
        assert "end_date" in income["missing_fields"]
        assert income["records"] is None

    def test_stale_state_never_carries_records(self):
        frame = pd.DataFrame(_income_rows())
        assert _records_for_state(STATE_STALE, frame) is None

    def test_no_key_fails_closed_without_calling_query_fn(self):
        def must_not_call(_endpoint, **_kwargs):
            raise AssertionError("NO_KEY must never trigger an upstream call")

        pack = build_tushare_research_evidence(
            symbol="603629.SH",
            as_of=AS_OF,
            query_fn=must_not_call,
            token_status=TOKEN_STATUS_NO_KEY,
        )
        assert pack["token_status"] == TOKEN_STATUS_NO_KEY
        assert pack["status"] == STATE_NOT_QUERIED
        for record in pack["endpoints"].values():
            assert record["state"] == STATE_NOT_QUERIED
            assert record["records"] is None
            assert record["cache"]["upstream_called"] is False

    def test_has_key_requires_query_fn(self):
        with pytest.raises(ValueError, match="query_fn is required"):
            build_tushare_research_evidence(
                symbol="603629.SH", as_of=AS_OF, token_status="HAS_KEY"
            )

    def test_invalid_collection_mode_rejected(self):
        with pytest.raises(ValueError, match="collection_mode"):
            build_tushare_research_evidence(
                symbol="603629.SH",
                as_of=AS_OF,
                query_fn=_query_from({}),
                collection_mode="offline",
            )

    def test_fixture_mode_with_no_key_still_collects_and_marks_mode(self):
        pack = build_tushare_research_evidence(
            symbol="603629.SH",
            as_of=AS_OF,
            query_fn=_query_from({"income": _income_rows()}),
            token_status=TOKEN_STATUS_NO_KEY,
            collection_mode="fixture",
        )
        assert pack["token_status"] == TOKEN_STATUS_NO_KEY
        assert pack["collection_mode"] == "fixture"
        assert pack["endpoints"]["income"]["state"] == STATE_HAS_DATA

    def test_permission_level_and_endpoint_annotation(self):
        pack = build_tushare_research_evidence(
            symbol="603629.SH",
            as_of=AS_OF,
            query_fn=_query_from({"income": _income_rows()}),
            permission_tier="2000_points",
            permission_matrix_ref="docs/task_runs/fake-matrix.json",
            endpoint_permissions={"income": "allowed", "fina_audit": "allowed"},
        )
        assert pack["permission_level"] == {
            "tier": "2000_points",
            "matrix_ref": "docs/task_runs/fake-matrix.json",
        }
        assert pack["endpoints"]["income"]["permission_status"] == "allowed"
        assert pack["endpoints"]["fina_audit"]["permission_status"] == "allowed"
        assert pack["endpoints"]["dividend"]["permission_status"] is None

    def test_summary_by_state_and_pack_status(self):
        pack = build_tushare_research_evidence(
            symbol="603629.SH",
            as_of=AS_OF,
            query_fn=_query_from(
                {
                    "income": _income_rows(),
                    "fina_audit": RuntimeError("抱歉，您没有访问该接口的权限"),
                }
            ),
        )
        assert pack["summary"]["by_state"][STATE_HAS_DATA] == 1
        assert pack["summary"]["by_state"][STATE_PERMISSION_DENIED] == 1
        assert pack["summary"]["endpoint_count"] == 9
        assert pack["status"] == STATE_HAS_DATA

    def test_pack_is_json_serializable_without_token(self):
        secret = "evidence-sensitive-token-0123456789abcdef"

        def leak(_endpoint, **_kwargs):
            raise RuntimeError(f"upstream rejected {secret}")

        pack = build_tushare_research_evidence(
            symbol="603629.SH", as_of=AS_OF, query_fn=leak, token=secret
        )
        dumped = json.dumps(pack, ensure_ascii=False)
        assert secret not in dumped
        assert "[REDACTED]" in dumped


class TestEvidencePackValidation:
    def _base_pack(self) -> dict:
        return build_tushare_research_evidence(
            symbol="603629.SH", as_of=AS_OF, query_fn=_query_from({})
        )

    def test_rejects_failure_state_with_empty_records(self):
        pack = self._base_pack()
        pack["endpoints"]["income"]["state"] = STATE_PERMISSION_DENIED
        pack["endpoints"]["income"]["records"] = []
        with pytest.raises(ValueError, match="may not masquerade"):
            validate_evidence_pack(pack)

    def test_rejects_has_data_without_records(self):
        pack = self._base_pack()
        pack["endpoints"]["income"]["state"] = STATE_HAS_DATA
        pack["endpoints"]["income"]["row_count"] = 3
        pack["endpoints"]["income"]["records"] = None
        with pytest.raises(ValueError, match="HAS_DATA requires non-empty records"):
            validate_evidence_pack(pack)

    def test_rejects_normal_no_data_with_rows(self):
        pack = self._base_pack()
        pack["endpoints"]["income"]["state"] = STATE_NORMAL_NO_DATA
        pack["endpoints"]["income"]["row_count"] = 2
        pack["endpoints"]["income"]["records"] = [{"ts_code": "603629.SH"}]
        with pytest.raises(ValueError, match="NORMAL_NO_DATA"):
            validate_evidence_pack(pack)

    def test_rejects_unknown_state(self):
        pack = self._base_pack()
        pack["endpoints"]["income"]["state"] = "OK"
        with pytest.raises(ValueError, match="unknown evidence state"):
            validate_evidence_pack(pack)

    def test_rejects_bad_token_status(self):
        pack = self._base_pack()
        pack["token_status"] = "token=abc123"
        with pytest.raises(ValueError, match="HAS_KEY/NO_KEY"):
            validate_evidence_pack(pack)

    def test_rejects_pack_without_endpoints(self):
        with pytest.raises(ValueError, match="endpoint records"):
            validate_evidence_pack({"endpoints": {}})


class TestSanitizedExampleFixture:
    def test_committed_fixture_loads_and_is_synthetic(self):
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        assert payload["fixture_kind"] == "sanitized_synthetic"
        assert set(payload["endpoints"]) >= {
            "income",
            "fina_audit",
            "forecast",
        }
        dumped = json.dumps(payload)
        for marker in ("authorization", "cookie", "TUSHARE_TOKEN="):
            assert marker not in dumped.lower()

    def test_fixture_example_pack_builds_and_validates(self):
        def fixture_query(endpoint: str, **_kwargs):
            payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
            return _query_from(payload["endpoints"])(endpoint)

        pack = build_tushare_research_evidence(
            symbol="603629.SH",
            as_of=AS_OF,
            query_fn=fixture_query,
            token_status="HAS_KEY",
        )
        validate_evidence_pack(pack)
        assert pack["endpoints"]["income"]["state"] == STATE_HAS_DATA
        assert pack["endpoints"]["express"]["state"] == STATE_NORMAL_NO_DATA
        assert pack["endpoints"]["fina_audit"]["state"] == STATE_PERMISSION_DENIED
        assert pack["endpoints"]["fina_mainbz"]["state"] == STATE_FIELD_MISSING
        forecast = pack["endpoints"]["forecast"]
        assert forecast["row_count"] == 2
        assert forecast["eligible_row_count"] == 1  # 2027 disclosure filtered


def _load_script():
    spec = importlib.util.spec_from_file_location("export_tushare_research_evidence", SCRIPT)
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
        assert module.scan_text_for_secret("Authorization: Bearer x", "")
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

    def test_symbol_is_required(self):
        result = self._run("--output", "/tmp/x.json", "--no-dotenv")
        assert result.returncode == 2

    def test_no_key_fails_closed_without_fixture(self):
        result = self._run(
            "603629.SH",
            "--as-of",
            AS_OF,
            "--output",
            "/tmp/should-not-exist.json",
            "--no-dotenv",
        )
        assert result.returncode == 2
        assert "NO_KEY" in result.stdout

    def test_fixture_mode_writes_pack_and_refuses_overwrite(self, tmp_path):
        output = tmp_path / "evidence.json"
        first = self._run(
            "603629.SH",
            "--as-of",
            AS_OF,
            "--output",
            str(output),
            "--fixture",
            str(FIXTURE),
            "--no-dotenv",
        )
        assert first.returncode == 0, first.stderr
        pack = json.loads(output.read_text(encoding="utf-8"))
        validate_evidence_pack(pack)
        assert pack["endpoints"]["income"]["state"] == STATE_HAS_DATA

        # Refuse to overwrite the existing explicit target.
        before = output.read_text(encoding="utf-8")
        second = self._run(
            "603629.SH",
            "--as-of",
            AS_OF,
            "--output",
            str(output),
            "--fixture",
            str(FIXTURE),
            "--no-dotenv",
        )
        assert second.returncode == 2
        assert "refusing to overwrite" in second.stderr
        assert output.read_text(encoding="utf-8") == before

    def test_summary_stdout_has_no_records_or_token(self, tmp_path):
        output = tmp_path / "evidence.json"
        result = self._run(
            "603629.SH",
            "--as-of",
            AS_OF,
            "--output",
            str(output),
            "--fixture",
            str(FIXTURE),
            "--no-dotenv",
        )
        assert result.returncode == 0
        json_start = result.stdout.index("{")
        summary = json.loads(result.stdout[json_start:])
        for record in summary["endpoints"].values():
            assert "records" not in record
        dumped = json.dumps(summary)
        assert "total_revenue" not in dumped
